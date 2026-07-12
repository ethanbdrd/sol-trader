"""Analyse principale — orchestre fetch → signaux → board → affichage."""

import pandas as pd
from datetime import datetime, timezone
from colorama import Fore, Back, Style

from .config import (SYMBOL, BTC_SYMBOL, SWING_N, SWING_N_FAST,
                     AUTO_ITEMS, MANUAL_ITEMS, LIQ_MIN_THRESHOLD)
from .display import (W, DIM, G, R, Y, RST,
                      header, section, row, signal_row, verdict_box)
from .api import (fetch_ohlcv, fetch_current_price, fetch_funding_rate,
                  fetch_open_interest_history, fetch_long_short_ratio,
                  fetch_liquidations, fetch_macro_calendar, fetch_taker_flow)
from .signals import (calc_mas, assess_structure, detect_swings, detect_bos_choch,
                      detect_fvg, detect_candle_confirmation, detect_absorption,
                      calc_volume_profile, check_session, assess_funding,
                      assess_oi, assess_ls_ratio, assess_taker_flow)
from .scoring import SignalBoard


def run_analysis(symbol=SYMBOL, verbose=False):
    board = SignalBoard()
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    header(f"SOL/USDT × 10  —  {now_utc}")

    # ── 0. Current price ─────────────────────
    price = fetch_current_price(symbol)
    btc_price = fetch_current_price(BTC_SYMBOL)
    if price:
        btc_str = f"   {DIM}BTC  {W}{btc_price:,.2f} USDT" if btc_price else ""
        print(f"\n  {DIM}Prix SOL  {W}{price:.4f} USDT{RST}{btc_str}")

    # ── 1. STRUCTURE DE MARCHÉ ─────────────────
    section("01 · STRUCTURE DE MARCHÉ  [Gate.io OHLCV]")

    # Daily data → MA200 + structure
    df_daily = fetch_ohlcv(symbol, "1d", limit=250)
    df_daily = calc_mas(df_daily) if df_daily is not None else None

    # 4H data → structure
    df_4h = fetch_ohlcv(symbol, "4h", limit=200)
    df_4h = calc_mas(df_4h) if df_4h is not None else None

    # 15min data → MAs alignment
    df_15m = fetch_ohlcv(symbol, "15m", limit=100)
    df_15m = calc_mas(df_15m) if df_15m is not None else None

    # S1 — Daily structure
    if df_daily is not None:
        struct_d, sh_d, sl_d = assess_structure(df_daily, n=SWING_N)
        color = G if struct_d == "bullish" else (R if struct_d == "bearish" else Y)
        dir_d = "long" if struct_d == "bullish" else ("short" if struct_d == "bearish" else None)
        signal_row("[S1] Tendance Daily (HH/HL vs LH/LL)",
                   struct_d.upper(), color, dir_d,
                   f"derniers hauts: {[f'{v:.2f}' for v in sh_d[-3:]]}")
        board.add("S1_daily_structure", dir_d if dir_d else struct_d, weight=2)
    else:
        row("[S1] Tendance Daily", "ERREUR API", R)

    # S2 — MA200 Daily
    if df_daily is not None and pd.notna(df_daily["ma200"].iloc[-1]) and price:
        ma200 = df_daily["ma200"].iloc[-1]
        dir_ma200 = "long" if price > ma200 else "short"
        dist_pct = (price - ma200) / ma200 * 100
        color = G if dir_ma200 == "long" else R
        signal_row("[S2] MA200 Daily",
                   f"{'AU-DESSUS' if dir_ma200=='long' else 'EN-DESSOUS'} ({ma200:.2f})",
                   color, dir_ma200,
                   f"distance: {dist_pct:+.2f}%")
        board.add("S2_ma200", dir_ma200, weight=2)
    else:
        row("[S2] MA200 Daily", "DONNÉES INSUFFISANTES", Y)

    # S3 — 4H structure
    struct_4h = None
    if df_4h is not None:
        struct_4h, sh_4h, sl_4h = assess_structure(df_4h, n=SWING_N_FAST)
        color = G if struct_4h == "bullish" else (R if struct_4h == "bearish" else Y)
        dir_4h = "long" if struct_4h == "bullish" else ("short" if struct_4h == "bearish" else None)
        signal_row("[S3] Structure 4H",
                   struct_4h.upper(), color, dir_4h)
        board.add("S3_4h_structure", dir_4h if dir_4h else struct_4h, weight=1)
    else:
        row("[S3] Structure 4H", "ERREUR API", R)

    # S5 — MAs alignment on 15min
    if df_15m is not None:
        last = df_15m.iloc[-1]
        ma5, ma15, ma30 = last["ma5"], last["ma15"], last["ma30"]
        ma30_slope = df_15m["ma30"].iloc[-1] - df_15m["ma30"].iloc[-5]
        if ma5 > ma15 > ma30 and ma30_slope > 0:
            dir_ma = "long"
            ma_label = f"MA5({ma5:.2f}) > MA15({ma15:.2f}) > MA30({ma30:.2f}) ↑"
            color = G
        elif ma5 < ma15 < ma30 and ma30_slope < 0:
            dir_ma = "short"
            ma_label = f"MA5({ma5:.2f}) < MA15({ma15:.2f}) < MA30({ma30:.2f}) ↓"
            color = R
        else:
            dir_ma = None
            ma_label = f"MA5({ma5:.2f}) MA15({ma15:.2f}) MA30({ma30:.2f}) — ENCHEVÊTRÉES"
            color = Y
        signal_row("[S5] MAs alignées 15min",
                   dir_ma.upper() if dir_ma else "RANGE", color, dir_ma, ma_label)
        board.add("S5_ma_alignment", dir_ma if dir_ma else "range", weight=1)
    else:
        row("[S5] MAs 15min", "ERREUR API", R)

    # S4 — BOS / CHoCH sur 4H
    if df_4h is not None:
        bos_signal, bos_label = detect_bos_choch(df_4h, struct_4h, n=SWING_N_FAST)
        bos_dir = (
            "long"  if bos_signal == "bullish_bos"
            else "short" if bos_signal == "bearish_bos"
            else None
        )
        color = G if bos_dir == "long" else (R if bos_dir == "short" else (Y if bos_signal == "choch_warning" else DIM))
        signal_row("[S4] BOS / CHoCH 4H", bos_label, color, bos_dir)
        board.add("S4_bos_choch",
                  bos_dir if bos_dir else ("choch" if bos_signal == "choch_warning" else "neutral"),
                  weight=1)
        if bos_signal == "choch_warning":
            board.block("choch_4h", "CHoCH detecte sur 4H — potentiel retournement")
    else:
        row("[S4] BOS/CHoCH 4H", "ERREUR API", R)

    # ── 2. MACRO & SESSION ────────────────────
    section("02 · SESSION & MACRO")

    session_id, session_name, session_color = check_session()
    row("[E2] Session de trading", session_name, session_color)
    board.add("E2_session", session_id, weight=1)
    if session_id == "asian":
        board.block("session", "Session asiatique — fakeouts fréquents")

    # M1 — Macro calendar (Forex Factory JSON)
    macro_events = fetch_macro_calendar()
    if macro_events is None:
        row("[M1] Calendrier macro", "API indisponible — verifier manuellement", Y,
            "→ fr.investing.com/economic-calendar")
    elif len(macro_events) == 0:
        row("[M1] Calendrier macro", "Aucun event HIGH dans les 4h", G)
        board.add("M1_macro", "ok_no_event", weight=1)
    else:
        names = ", ".join(e["title"] for e in macro_events[:3])
        row("[M1] Calendrier macro", f"⚠ {len(macro_events)} EVENT(S) HIGH dans les 4h", R,
            names)
        board.add("M1_macro", "event_near", weight=1)
        board.block("macro_event", f"Event(s) macro imminents: {names}")

    # BTC structure (même analyse que SOL)
    df_btc_d = fetch_ohlcv(BTC_SYMBOL, "1d", limit=250)
    if df_btc_d is not None:
        df_btc_d = calc_mas(df_btc_d)
    if df_btc_d is not None and btc_price and pd.notna(df_btc_d["ma200"].iloc[-1]):
        struct_btc, _, _ = assess_structure(df_btc_d, n=SWING_N)
        ma200_btc = df_btc_d["ma200"].iloc[-1]
        dir_btc = "long" if (struct_btc == "bullish" and btc_price > ma200_btc) \
                  else "short" if (struct_btc == "bearish" and btc_price < ma200_btc) \
                  else None
        color = G if dir_btc == "long" else (R if dir_btc == "short" else Y)
        signal_row("[M3] Biais BTC",
                   f"{struct_btc.upper()} / MA200 {'OK ▲' if btc_price > ma200_btc else 'KO ▼'}",
                   color, dir_btc,
                   f"MA200={ma200_btc:,.2f}")
        board.add("M3_btc_bias", dir_btc if dir_btc else "neutral", weight=1)
        # M2 : BTC en tendance claire (non-ranging)
        btc_trending = struct_btc in ("bullish", "bearish")
        m2_dir = dir_btc if btc_trending else None
        m2_dir_str = f" {'▲ LONG' if dir_btc == 'long' else '▼ SHORT'}" if btc_trending and dir_btc else ""
        m2_label   = f"TENDANCE CLAIRE{m2_dir_str}" if btc_trending else "EN RANGE — signal non exploitable"
        m2_color   = (G if dir_btc == "long" else R if dir_btc == "short" else Y)
        row("[M2] BTC en tendance", m2_label, m2_color,
            "" if btc_trending else "attends une tendance BTC directionnelle")
        board.add("M2_btc_trending", m2_dir if m2_dir else "ranging", weight=1)
    else:
        row("[M3] Biais BTC", "ERREUR API", R)

    # ── 3. SENTIMENT & DÉRIVÉS ────────────────
    section("03 · SENTIMENT & DÉRIVÉS  [Gate.io]")

    # F1 — Funding Rate
    funding_data = fetch_funding_rate(symbol)
    if funding_data:
        fr_val, fr_signal, fr_dir = assess_funding(funding_data)
        fr_pct = fr_val * 100 if fr_val else 0
        recent_fr = [float(r["fundingRate"]) * 100 for r in funding_data[-3:]]
        color = (G if fr_signal in ("favorable_long", "neutral")
                 else Y if "caution" in fr_signal
                 else R)
        annualized = fr_pct * 3 * 365  # 3 periods/day
        signal_row("[F1] Funding Rate",
                   f"{fr_pct:+.4f}%  ({fr_signal})",
                   color, fr_dir,
                   f"annualisé: {annualized:+.1f}%  |  3 derniers: {[f'{v:+.4f}%' for v in recent_fr]}")
        board.add("F1_funding", fr_dir if fr_dir else "neutral", weight=1)
        if abs(fr_pct) > 0.07:
            board.block("funding_extreme",
                        f"Funding extrême ({fr_pct:+.4f}%) — flush probable")
    else:
        row("[F1] Funding Rate", "ERREUR API", R)

    # F2 — Open Interest (fenêtre 14 jours pour un percentile stable)
    oi_df = fetch_open_interest_history(symbol, period="1h", limit=336)
    if oi_df is not None:
        oi_change, oi_current, oi_pct_max, oi_percentile = assess_oi(oi_df)
        oi_m      = oi_current / 1e6
        is_extreme = oi_percentile >= 90
        is_very_low = oi_percentile <= 10
        color = (R if is_extreme
                 else Y if oi_percentile >= 75 or is_very_low
                 else G)
        oi_note = ("⚠ EXTREME 14j — flush probable" if is_extreme
                   else "⚠ TRES BAS 14j — volatilite imminente possible" if is_very_low
                   else "")
        row("[F2] Open Interest",
            f"${oi_m:.1f}M  (6h: {oi_change:+.2f}%  |  percentile 14j: {oi_percentile:.0f}%)",
            color, oi_note)
        board.add("F2_oi", "extreme" if is_extreme else "neutral", weight=1)
        if is_extreme:
            board.block("oi_extreme",
                        f"OI au {oi_percentile:.0f}e percentile sur 14j — marche surexpose")
    else:
        row("[F2] Open Interest", "ERREUR API", R)

    # F4 — Liquidations récentes (Gate.io /liq_orders — données réelles)
    liq_data = fetch_liquidations(symbol, limit=100)
    if liq_data is None:
        row("[F4] Liquidations récentes", "ERREUR API", R)
    else:
        now_ts   = datetime.now(timezone.utc).timestamp()
        cutoff   = now_ts - 8 * 3600   # dernières 8h
        recent_liqs = [l for l in liq_data if int(l.get("time", 0)) >= cutoff]
        long_liqs  = sum(abs(float(l["size"])) for l in recent_liqs if float(l["size"]) > 0)
        short_liqs = sum(abs(float(l["size"])) for l in recent_liqs if float(l["size"]) < 0)
        total_liqs = long_liqs + short_liqs
        dominant = max(long_liqs, short_liqs)
        if total_liqs == 0 or dominant < LIQ_MIN_THRESHOLD:
            liq_hint  = f"aucune liquidation significative (8h, total: {total_liqs:.0f} contracts)"
            liq_color = G
            liq_dir   = "safe"
            liq_block_reason = None
        elif long_liqs > short_liqs * 2 and long_liqs >= LIQ_MIN_THRESHOLD:
            liq_hint  = f"pic LIQS LONGS ({long_liqs:.0f} contracts) — eviter SHORT ici (bottom possible)"
            liq_color = Y
            liq_dir   = "warning"
            liq_block_reason = f"Longs viennent d'etre liquides ({long_liqs:.0f} contracts) — ne pas shorter un potentiel bottom"
        elif short_liqs > long_liqs * 2 and short_liqs >= LIQ_MIN_THRESHOLD:
            liq_hint  = f"pic LIQS SHORTS ({short_liqs:.0f} contracts) — eviter LONG ici (top possible)"
            liq_color = Y
            liq_dir   = "warning"
            liq_block_reason = f"Shorts viennent d'etre liquides ({short_liqs:.0f} contracts) — ne pas longer un potentiel top"
        else:
            liq_hint  = f"liqs equilibrees (L:{long_liqs:.0f} / S:{short_liqs:.0f} contracts)"
            liq_color = DIM
            liq_dir   = "safe"
            liq_block_reason = None
        row("[F4] Liquidations récentes (8h)", liq_hint, liq_color)
        board.add("F4_liquidations", liq_dir, weight=1)
        if liq_block_reason:
            blocked_dir = "short" if long_liqs > short_liqs else "long"
            board.block("recent_liquidation", liq_block_reason, direction=blocked_dir)

    # F3 — Long/Short Ratio
    ls_df = fetch_long_short_ratio(symbol, period="1h", limit=12)
    ls_long, ls_signal, ls_dir = assess_ls_ratio(ls_df) if ls_df is not None else (None, "unknown", None)
    if ls_long is not None and pd.notna(ls_long):
        ls_short = 1 - ls_long
        color = (G if ls_dir == "long" else R if ls_dir == "short" else DIM)
        signal_row("[F3] Ratio Long/Short (contrarian)",
                   ls_signal.upper(), color, ls_dir,
                   f"Retail: {ls_long*100:.1f}% L / {ls_short*100:.1f}% S")
        board.add("F3_ls_ratio", ls_dir if ls_dir else "neutral", weight=1)
    else:
        row("[F3] Ratio Long/Short", "ERREUR API", R)

    # ── 4. CVD ────────────────────────────────
    section("04 · CVD — PRESSION D'ACHAT  [Gate.io taker flow]")

    # C1 — Flux taker 6h (données réelles par heure, fenêtre alignée avec le prix)
    flow_6h = fetch_taker_flow(symbol, interval="1h", limit=6)
    c1_status, c1_dir, c1_cvd, c1_share, c1_chg = assess_taker_flow(flow_6h)
    if c1_status != "unclear":
        color = (G if "bullish" in c1_status or c1_dir == "long"
                 else R if "bearish" in c1_status or c1_dir == "short"
                 else Y)
        signal_row("[C1] CVD taker 6h",
                   c1_status.upper().replace("_", " "),
                   color, c1_dir,
                   f"CVD={c1_cvd:+,.0f} contrats  |  buy={c1_share*100:.1f}%  |  prix {c1_chg:+.2f}%")
        board.add("C1_cvd", c1_dir if c1_dir else c1_status, weight=1)
        if "divergence" in c1_status:
            # Divergence bearish (prix monte + flux vendeur) → bloque le LONG
            # Divergence bullish (prix baisse + flux acheteur) → bloque le SHORT
            blocked_dir = "long" if c1_status == "divergence_bearish" else "short"
            reason_map  = {
                "divergence_bearish": f"Prix +{c1_chg:.2f}% sur 6h mais flux taker vendeur (buy {c1_share*100:.0f}%) — long non confirme",
                "divergence_bullish": f"Prix {c1_chg:.2f}% sur 6h mais flux taker acheteur (buy {c1_share*100:.0f}%) — short risque",
            }
            board.block("cvd_divergence", reason_map[c1_status], direction=blocked_dir)
    else:
        row("[C1] CVD taker 6h", "ERREUR API", R)

    # C2 — Momentum flux taker 40h (10 périodes 4h)
    flow_40h = fetch_taker_flow(symbol, interval="4h", limit=10)
    c2_status, c2_dir, c2_cvd, c2_share, c2_chg = assess_taker_flow(
        flow_40h, min_buy_share_dev=0.05)
    if c2_status != "unclear":
        color = G if c2_dir == "long" else (R if c2_dir == "short" else Y)
        label = ("BULLISH" if c2_dir == "long"
                 else "BEARISH" if c2_dir == "short" else "NEUTRE")
        signal_row("[C2] Momentum taker 40h", label, color, c2_dir,
                   f"CVD={c2_cvd:+,.0f} contrats  |  buy={c2_share*100:.1f}%  |  prix {c2_chg:+.2f}%")
        board.add("C2_cvd_4h", c2_dir if c2_dir else "neutral", weight=1)
    elif df_4h is not None and len(df_4h) >= 10:
        # Fallback si contract_stats indisponible : momentum bougies 4H
        recent = df_4h.iloc[-10:]
        bullish_candles = (recent["close"] > recent["open"]).sum()
        bearish_candles = (recent["close"] < recent["open"]).sum()
        bull_ratio = bullish_candles / len(recent)
        cvd4h_dir = "long" if bull_ratio > 0.60 else "short" if bull_ratio < 0.40 else None
        color = G if cvd4h_dir == "long" else (R if cvd4h_dir == "short" else Y)
        row("[C2] Momentum 4H (fallback bougies)",
            f"{bullish_candles} haussières / {bearish_candles} baissières"
            f"  ->  {'BULLISH' if cvd4h_dir=='long' else 'BEARISH' if cvd4h_dir=='short' else 'NEUTRE'}",
            color)
        board.add("C2_cvd_4h", cvd4h_dir if cvd4h_dir else "neutral", weight=1)

    # ── 5. HEATMAP ────────────────────────────
    section("05 · LIQUIDATION HEATMAP  [Manuel requis]")
    row("[L1] Liquidation Heatmap",
        "⚠ VÉRIFIER MANUELLEMENT", Y,
        "→ https://coinank.com/chart/derivatives/liq-heat-map/solusdt/1w")
    row("", "(aucune API publique disponible)", DIM)

    # ── 6. ENTRY ANALYSIS ────────────────────
    section("06 · ZONE D'ENTRÉE & TIMING  [OHLCV + Gate.io]")

    # C3 — Absorption (volume fort + body plat)
    absorbed, vol_ratio, body_pct = detect_absorption(df_15m)
    if absorbed:
        row("[C3] Absorption 15min",
            f"ABSORPTION DETECTEE (vol x{vol_ratio:.1f} avg, body {body_pct:.2f}%)", R,
            "volume fort sans mouvement = vendeurs/acheteurs cachés")
        board.add("C3_absorption", "absorption", weight=1)
        board.block("absorption", "Absorption detectee sur 15min — attendre resolution")
    else:
        row("[C3] Absorption 15min", "Aucune absorption detectee", G)
        board.add("C3_absorption", "no_absorption", weight=1)

    # E1 — FVG sur 15min
    if df_15m is not None and price:
        fvgs = detect_fvg(df_15m, n_candles=80)
        # Find closest FVG to current price
        nearest_fvg  = None
        nearest_dist = float("inf")
        for ftype, flo, fhi, _ in fvgs:
            mid  = (flo + fhi) / 2
            dist = abs(price - mid) / price * 100
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_fvg  = (ftype, flo, fhi, dist)
        if nearest_fvg:
            ftype, flo, fhi, dist = nearest_fvg
            # Prix réellement à l'intérieur du FVG
            strictly_in = flo <= price <= fhi
            # Prix proche mais pas encore dans le FVG (en approche)
            approaching = not strictly_in and dist < 0.8
            # FVG déjà comblé : bullish FVG comblé si prix > fhi, bearish si prix < flo
            filled = (ftype == "bullish" and price > fhi) or \
                     (ftype == "bearish" and price < flo)

            if strictly_in:
                zone_label = "DANS LA ZONE"
                in_zone = True
                color   = G
            elif approaching and not filled:
                zone_label = f"EN APPROCHE ({dist:.2f}%)"
                in_zone = True   # encore exploitable
                color   = Y
            elif filled:
                zone_label = f"COMBLÉ — prix {'au-dessus' if ftype=='bullish' else 'en dessous'}"
                in_zone = False
                color   = DIM
            else:
                zone_label = f"hors zone ({dist:.2f}%)"
                in_zone = False
                color   = DIM

            e1_dir = "long"  if (ftype == "bullish" and in_zone) \
                else "short" if (ftype == "bearish" and in_zone) \
                else None
            row("[E1] FVG le plus proche (15min)",
                f"{ftype.upper()} FVG [{flo:.2f} - {fhi:.2f}]  dist: {dist:.2f}%",
                color, zone_label)
            board.add("E1_fvg_ob", e1_dir if e1_dir else "neutral", weight=1)
        else:
            row("[E1] FVG / Order Block", "Aucun FVG recent detectable", DIM)
            board.add("E1_fvg_ob", "none_detected", weight=1)
    else:
        row("[E1] FVG / Order Block", "DONNÉES INSUFFISANTES", DIM)

    # E3 — Confirmation bougie (15min)
    pattern, pattern_dir = detect_candle_confirmation(df_15m)
    if pattern:
        color = G if pattern_dir == "long" else R
        signal_row("[E3] Confirmation bougie 15min",
                   pattern.upper().replace("_", " "), color, pattern_dir)
        board.add("E3_candle_confirm", pattern_dir, weight=1)
    else:
        row("[E3] Confirmation bougie 15min", "Pas de pattern clair", DIM)
        board.add("E3_candle_confirm", "no_pattern" if df_15m is not None else None, weight=1)

    # Détection contradiction E1 vs E3
    e1_dir_val = next((d for n, d, _, _ in board.signals if n == "E1_fvg_ob"), None)
    if (e1_dir_val in ("long", "short") and pattern_dir in ("long", "short")
            and e1_dir_val != pattern_dir):
        row("[!] Contradiction E1/E3",
            f"FVG {e1_dir_val.upper()} vs bougie {pattern_dir.upper()} — signaux opposés",
            Y, "prendre le signal E3 (bougie) comme confirmation prioritaire")

    # E4 — Volume Profile POC (15min, 100 dernières bougies)
    poc_price, lvns = calc_volume_profile(df_15m, n_candles=100, bins=40)
    if poc_price and price:
        dist_poc = abs(price - poc_price) / price * 100
        # Nearest LVN in direction — minimum 0.5% de distance pour être exploitable
        min_dist = price * 0.005
        lvns_above = [l for l in lvns if l > price + min_dist]
        lvns_below = [l for l in lvns if l < price - min_dist]
        lvn_above  = min(lvns_above) if lvns_above else None
        lvn_below  = max(lvns_below) if lvns_below else None
        color = G if dist_poc < 1.0 else DIM
        lvn_str = ""
        if lvn_above: lvn_str += f"  LVN↑ {lvn_above:.2f}"
        if lvn_below: lvn_str += f"  LVN↓ {lvn_below:.2f}"
        row("[E4] Volume Profile (15min)",
            f"POC: {poc_price:.2f}  (dist: {dist_poc:.2f}%){lvn_str}", color)
        # POC ne donne pas de direction mais confirme la zone
        board.add("E4_vp_poc", "neutral", weight=1)
    else:
        row("[E4] Volume Profile", "DONNÉES INSUFFISANTES", DIM)

    # R1 — suggestion SL basée sur les pivots structurels 4H
    if df_4h is not None and price:
        _, sh4, sl4 = assess_structure(df_4h, n=SWING_N_FAST)
        # SL long  = dernier swing low SOUS le prix actuel
        sl_below = [v for v in sl4 if v < price]
        # SL short = dernier swing high AU-DESSUS du prix actuel
        sh_above = [v for v in sh4 if v > price]

        def sl_warning(dist_pct, direction):
            """Retourne warning si SL trop proche de la liquidation ou trop serré."""
            lev_impact = dist_pct * 10
            if dist_pct < 1.0:
                return f" !! TROP SERRÉ ({dist_pct:.1f}%) — bruit de marché suffisant pour déclencher"
            elif lev_impact >= 90:
                return f" !! TROP LARGE — liquidation x10 à {direction}10% (SL jamais atteint)"
            elif lev_impact >= 70:
                return f" ! proche liquidation ({lev_impact:.0f}% de la position)"
            return ""

        if sl_below and sh_above:
            nearest_hl    = sl_below[-1]
            nearest_lh    = sh_above[-1]
            sl_long_dist  = (price - nearest_hl) / price * 100
            sl_short_dist = (nearest_lh - price) / price * 100
            warn_long  = sl_warning(sl_long_dist,  "-")
            warn_short = sl_warning(sl_short_dist, "+")
            col = R if (warn_long and "TROP" in warn_long) or (warn_short and "TROP" in warn_short) else DIM
            row("[R1] SL suggéré (auto)",
                f"LONG: sous {nearest_hl:.2f} (-{sl_long_dist:.1f}%){warn_long}"
                f"  |  SHORT: dessus {nearest_lh:.2f} (+{sl_short_dist:.1f}%){warn_short}",
                col, "a valider — placer 0.3% au-dela du niveau")
            # Bloquer directionnellement si SL trop serré (< 1%)
            if sl_long_dist < 1.0:
                board.block("sl_trop_serre_long",
                            f"SL long à {sl_long_dist:.1f}% — trop serré pour x10",
                            direction="long")
            if sl_short_dist < 1.0:
                board.block("sl_trop_serre_short",
                            f"SL short à {sl_short_dist:.1f}% — trop serré pour x10",
                            direction="short")
        elif sl_below:
            nearest_hl   = sl_below[-1]
            sl_long_dist = (price - nearest_hl) / price * 100
            warn_long    = sl_warning(sl_long_dist, "-")
            col = R if warn_long and "TROP" in warn_long else DIM
            row("[R1] SL suggéré (auto)",
                f"LONG: sous {nearest_hl:.2f} (-{sl_long_dist:.1f}%){warn_long}"
                f"  |  SHORT: pas de pivot haut visible",
                col, "a valider")
        elif sh_above:
            nearest_lh    = sh_above[-1]
            sl_short_dist = (nearest_lh - price) / price * 100
            warn_short    = sl_warning(sl_short_dist, "+")
            col = R if warn_short and "TROP" in warn_short else DIM
            row("[R1] SL suggéré (auto)",
                f"LONG: pas de pivot bas visible"
                f"  |  SHORT: dessus {nearest_lh:.2f} (+{sl_short_dist:.1f}%){warn_short}",
                col, "a valider")
        else:
            row("[R1] SL suggéré (auto)",
                "Pas de pivot structurel identifiable — SL manuel requis", Y)
        if sl_below or sh_above:
            board.add("R1_sl_suggested", "computed", weight=1)

    # ─────────────────────────────────────────
    # VERDICT FINAL
    # ─────────────────────────────────────────
    section("── VERDICT ──")

    status, direction, long_pts, short_pts, completion = board.score()
    total_pts     = long_pts + short_pts
    auto_answered = sum(1 for _, d, _, _ in board.signals if d is not None)
    directional   = sum(1 for _, d, _, _ in board.signals if d in ("long", "short"))

    print(f"\n  {DIM}Points LONG   {G}{long_pts:>4} pts")
    print(f"  {DIM}Points SHORT  {R}{short_pts:>4} pts")
    print(f"  {DIM}Données  {W}{auto_answered:>2}/{AUTO_ITEMS} items auto ({completion*100:.0f}%){DIM}  |  "
          f"Directionnels  {W}{directional}{DIM}  |  "
          f"Manuel  {W}{MANUAL_ITEMS} items restants")

    if board.is_blocked:
        # Bloqueurs globaux présents — bloquent tout trading
        print()
        for name, reason, bdir in board.blockers:
            if bdir is None:
                print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {RST}  {R}{name}{DIM}: {reason}")
            else:
                print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {bdir.upper():<6}{RST}  {R}{name}{DIM}: {reason}")
        verdict_box("🚫  BLOQUEUR ACTIF — NE PAS TRADER", Fore.RED, Back.RED)

    elif board.directional_blockers():
        # Bloqueurs directionnels — afficher d'abord les bloqueurs
        print()
        for name, reason, bdir in board.directional_blockers():
            print(f"  {Back.RED}{Style.BRIGHT} BLOQUEUR {bdir.upper():<6}{RST}  {R}{name}{DIM}: {reason}")

        # Ne montrer le verdict directionnel QUE si completion suffisante
        if completion < 0.55:
            verdict_box("—  ANALYSE INCOMPLÈTE — ATTENDRE", Fore.WHITE)
        elif direction and board.is_blocked_for(direction):
            opposite = "LONG" if direction == "short" else "SHORT"
            verdict_box(f"⚠  {direction.upper()} BLOQUÉ — {opposite} ENVISAGEABLE", Fore.YELLOW)
        elif direction:
            pts = long_pts if direction == "long" else short_pts
            col = Fore.GREEN if direction == "long" else Fore.RED
            arrow = "▲" if direction == "long" else "▼"
            label = "TRADE OK" if status == "go" else "POSSIBLE — COMPLÉTER"
            verdict_box(f"{arrow}  {direction.upper()} {label}  ({pts}/{total_pts} pts)", col)
        else:
            verdict_box("⚡  SIGNAUX MIXTES — NE PAS TRADER", Fore.MAGENTA)

    elif status == "go" and direction == "long":
        print()
        verdict_box(f"▲  LONG — TRADE OK  ({long_pts}/{total_pts} pts)", Fore.GREEN, Back.GREEN)

    elif status == "go" and direction == "short":
        print()
        verdict_box(f"▼  SHORT — TRADE OK  ({short_pts}/{total_pts} pts)", Fore.RED, Back.RED)

    elif status == "possible" and direction:
        dir_str = "LONG POSSIBLE" if direction == "long" else "SHORT POSSIBLE"
        print()
        verdict_box(f"~  {dir_str}  — COMPLÉTER L'ANALYSE", Fore.YELLOW)

    elif status == "mixed":
        print()
        verdict_box("⚡  SIGNAUX MIXTES — NE PAS TRADER", Fore.MAGENTA)

    else:
        print()
        verdict_box("—  ANALYSE INCOMPLÈTE — ATTENDRE", Fore.WHITE)

    # Reminder for manual items
    print()
    print(DIM + f"  {MANUAL_ITEMS} items nécessitant tes données personnelles (non automatisables):")
    print(DIM + "  ├─ [L2] Cluster contre dir.  → heatmap CoinAnk (bloqueur si cluster proche)")
    print(DIM + "  ├─ [L3] Cluster dans dir.    → heatmap CoinAnk (aimant de prix ?)")
    print(DIM + "  ├─ [R2] R:R minimum 1:2      → calculatrice checklist HTML (besoin prix entrée)")
    print(DIM + "  ├─ [R3] Taille de position   → calculatrice checklist HTML (besoin capital)")
    print(DIM + "  ├─ [R4] TPs partiels définis → noter TP1/TP2 avant d'entrer")
    print(DIM + "  ├─ [R5] Pas de position cor. → vérifier tes positions sur XT.com")
    print(DIM + "  └─ [R1] SL structurel        → suggestion ci-dessus, à valider manuellement")
    print()

    return status, direction, board
