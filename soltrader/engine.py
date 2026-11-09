"""Moteur unique : données de marché → SignalBoard + lignes d'affichage.

Utilisé à l'identique par l'analyse live (analysis.py), le backtest
(backtest.py) et le paper trading. Aucune entrée/sortie réseau ici :
tout ce dont le moteur a besoin arrive dans `MarketData`.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from .config import SWING_N, SWING_N_FAST, LIQ_MIN_THRESHOLD
from .scoring import SignalBoard
from .signals import (calc_mas, assess_structure, detect_bos_choch,
                      detect_fvg, detect_candle_confirmation, detect_absorption,
                      calc_volume_profile, check_session, assess_funding,
                      assess_oi, assess_ls_ratio, assess_taker_flow)


def closed_only(df, interval, now):
    """Garde uniquement les périodes entièrement terminées à l'instant `now`.

    Gate.io renvoie la bougie (ou la période contract_stats) EN COURS en
    dernière position : au cron de :17, la bougie 15 min n'a que ~2 minutes
    et la ligne contract_stats de l'heure est quasi vide. Les index sont
    des débuts de période ; une période [t, t+interval[ est close si
    t + interval <= now.
    """
    if df is None:
        return None
    return df[df.index + pd.Timedelta(interval) <= pd.Timestamp(now)]


@dataclass
class MarketData:
    """Instantané des données de marché à l'instant `now`.

    sol_1d / sol_4h / sol_15m / btc_1d : OHLCV indexés par open_time (UTC).
    stats_1h : contract_stats Gate.io indexé par timestamp, colonnes numériques.
    funding  : [{fundingRate, fundingTime}] trié ascendant.
    macro    : liste d'events HIGH dans les 4h, ou None si calendrier indisponible.
    replay   : True en backtest (le calendrier macro n'est pas rejouable).
    """
    now: datetime
    price: Optional[float]
    btc_price: Optional[float]
    sol_1d: Optional[pd.DataFrame]
    sol_4h: Optional[pd.DataFrame]
    sol_15m: Optional[pd.DataFrame]
    btc_1d: Optional[pd.DataFrame]
    stats_1h: Optional[pd.DataFrame]
    funding: Optional[list]
    macro: Optional[list] = None
    replay: bool = False


@dataclass
class Row:
    section: str
    label: str
    value: str
    color: str = "info"          # good | bad | warn | dim | info
    hint: str = ""
    direction: Optional[str] = None
    signal: bool = False         # True = affiché avec la flèche de direction


@dataclass
class Evaluation:
    board: SignalBoard
    rows: list = field(default_factory=list)
    # Niveaux structurels utiles au paper trading (None si absents)
    sl_long: Optional[float] = None
    sl_short: Optional[float] = None


SECTIONS = [
    "01 · STRUCTURE DE MARCHÉ  [Gate.io OHLCV]",
    "02 · SESSION & MACRO",
    "03 · SENTIMENT & DÉRIVÉS  [Gate.io]",
    "04 · CVD — PRESSION D'ACHAT  [Gate.io taker flow]",
    "05 · LIQUIDATION HEATMAP  [Manuel requis]",
    "06 · ZONE D'ENTRÉE & TIMING  [OHLCV + Gate.io]",
]
S_STRUCT, S_MACRO, S_DERIV, S_CVD, S_HEAT, S_ENTRY = SECTIONS


def _dir_color(d, neutral="warn"):
    return "good" if d == "long" else ("bad" if d == "short" else neutral)


def evaluate(md: MarketData) -> Evaluation:
    board = SignalBoard()
    ev = Evaluation(board=board)
    rows = ev.rows
    price = md.price

    def row(section, label, value, color="info", hint=""):
        rows.append(Row(section, label, value, color, hint))

    def sig(section, label, value, color, direction, hint=""):
        rows.append(Row(section, label, value, color, hint, direction, signal=True))

    d1  = calc_mas(md.sol_1d)  if md.sol_1d  is not None else None
    h4  = calc_mas(md.sol_4h)  if md.sol_4h  is not None else None
    m15 = calc_mas(md.sol_15m) if md.sol_15m is not None else None

    # ── 1. STRUCTURE ─────────────────────────
    # S1 — structure daily
    if d1 is not None and len(d1):
        struct_d, sh_d, _ = assess_structure(d1, n=SWING_N)
        dir_d = "long" if struct_d == "bullish" else ("short" if struct_d == "bearish" else None)
        sig(S_STRUCT, "[S1] Tendance Daily (HH/HL vs LH/LL)", struct_d.upper(),
            _dir_color(dir_d), dir_d, f"derniers hauts: {[f'{v:.2f}' for v in sh_d[-3:]]}")
        board.add("S1_daily_structure", dir_d if dir_d else struct_d, weight=2)
    else:
        row(S_STRUCT, "[S1] Tendance Daily", "ERREUR API", "bad")

    # S2 — MA200 daily
    if d1 is not None and len(d1) and pd.notna(d1["ma200"].iloc[-1]) and price:
        ma200 = d1["ma200"].iloc[-1]
        dir_ma200 = "long" if price > ma200 else "short"
        sig(S_STRUCT, "[S2] MA200 Daily",
            f"{'AU-DESSUS' if dir_ma200 == 'long' else 'EN-DESSOUS'} ({ma200:.2f})",
            _dir_color(dir_ma200), dir_ma200,
            f"distance: {(price - ma200) / ma200 * 100:+.2f}%")
        board.add("S2_ma200", dir_ma200, weight=2)
    else:
        row(S_STRUCT, "[S2] MA200 Daily", "DONNÉES INSUFFISANTES", "warn")

    # S3 — structure 4H
    struct_4h = None
    if h4 is not None and len(h4):
        struct_4h, _, _ = assess_structure(h4, n=SWING_N_FAST)
        dir_4h = "long" if struct_4h == "bullish" else ("short" if struct_4h == "bearish" else None)
        sig(S_STRUCT, "[S3] Structure 4H", struct_4h.upper(), _dir_color(dir_4h), dir_4h)
        board.add("S3_4h_structure", dir_4h if dir_4h else struct_4h, weight=1)
    else:
        row(S_STRUCT, "[S3] Structure 4H", "ERREUR API", "bad")

    # S5 — alignement MAs 15min
    if m15 is not None and len(m15) >= 35:
        last = m15.iloc[-1]
        ma5, ma15, ma30 = last["ma5"], last["ma15"], last["ma30"]
        ma30_slope = m15["ma30"].iloc[-1] - m15["ma30"].iloc[-5]
        if ma5 > ma15 > ma30 and ma30_slope > 0:
            dir_ma, ma_label = "long", f"MA5({ma5:.2f}) > MA15({ma15:.2f}) > MA30({ma30:.2f}) ↑"
        elif ma5 < ma15 < ma30 and ma30_slope < 0:
            dir_ma, ma_label = "short", f"MA5({ma5:.2f}) < MA15({ma15:.2f}) < MA30({ma30:.2f}) ↓"
        else:
            dir_ma, ma_label = None, f"MA5({ma5:.2f}) MA15({ma15:.2f}) MA30({ma30:.2f}) — ENCHEVÊTRÉES"
        sig(S_STRUCT, "[S5] MAs alignées 15min", dir_ma.upper() if dir_ma else "RANGE",
            _dir_color(dir_ma), dir_ma, ma_label)
        board.add("S5_ma_alignment", dir_ma if dir_ma else "range", weight=1)
    else:
        row(S_STRUCT, "[S5] MAs 15min", "ERREUR API", "bad")

    # S4 — BOS / CHoCH 4H
    if h4 is not None and len(h4):
        bos_signal, bos_label = detect_bos_choch(h4, struct_4h, n=SWING_N_FAST)
        bos_dir = ("long" if bos_signal == "bullish_bos"
                   else "short" if bos_signal == "bearish_bos" else None)
        color = _dir_color(bos_dir, "warn" if bos_signal == "choch_warning" else "dim")
        sig(S_STRUCT, "[S4] BOS / CHoCH 4H", bos_label, color, bos_dir)
        board.add("S4_bos_choch",
                  bos_dir if bos_dir else ("choch" if bos_signal == "choch_warning" else "neutral"),
                  weight=1)
        if bos_signal == "choch_warning":
            board.block("choch_4h", "CHoCH detecte sur 4H — potentiel retournement")
    else:
        row(S_STRUCT, "[S4] BOS/CHoCH 4H", "ERREUR API", "bad")

    # ── 2. SESSION & MACRO ───────────────────
    session_id, session_name, session_color = check_session(md.now)
    row(S_MACRO, "[E2] Session de trading", session_name,
        {"us": "good", "asian": "bad"}.get(session_id, "warn"))
    board.add("E2_session", session_id, weight=1)
    if session_id == "asian":
        board.block("session", "Session asiatique — fakeouts fréquents")

    # M1 — calendrier macro
    if md.replay:
        pass  # non rejouable en backtest → item non répondu
    elif md.macro is None:
        # Fail-safe : sans calendrier on ne peut pas exclure un CPI/FOMC imminent
        row(S_MACRO, "[M1] Calendrier macro", "API indisponible — verifier manuellement", "warn",
            "→ fr.investing.com/economic-calendar")
        board.block("macro_inconnu",
                    "Calendrier macro indisponible — verifier manuellement avant de trader")
    elif len(md.macro) == 0:
        row(S_MACRO, "[M1] Calendrier macro", "Aucun event HIGH dans les 4h", "good")
        board.add("M1_macro", "ok_no_event", weight=1)
    else:
        names = ", ".join(e["title"] for e in md.macro[:3])
        row(S_MACRO, "[M1] Calendrier macro",
            f"⚠ {len(md.macro)} EVENT(S) HIGH dans les 4h", "bad", names)
        board.add("M1_macro", "event_near", weight=1)
        board.block("macro_event", f"Event(s) macro imminents: {names}")

    # M3 / M2 — biais BTC
    b1d = calc_mas(md.btc_1d) if md.btc_1d is not None else None
    btc_price = md.btc_price
    if b1d is not None and len(b1d) and btc_price and pd.notna(b1d["ma200"].iloc[-1]):
        struct_btc, _, _ = assess_structure(b1d, n=SWING_N)
        ma200_btc = b1d["ma200"].iloc[-1]
        dir_btc = ("long" if (struct_btc == "bullish" and btc_price > ma200_btc)
                   else "short" if (struct_btc == "bearish" and btc_price < ma200_btc)
                   else None)
        sig(S_MACRO, "[M3] Biais BTC",
            f"{struct_btc.upper()} / MA200 {'OK ▲' if btc_price > ma200_btc else 'KO ▼'}",
            _dir_color(dir_btc), dir_btc, f"MA200={ma200_btc:,.2f}")
        board.add("M3_btc_bias", dir_btc if dir_btc else "neutral", weight=1)
        # M2 : simple marqueur tendance / range (non directionnel).
        # Avant le 2026-11-09, M2 reprenait la direction de M3 → BTC comptait double.
        btc_trending = struct_btc in ("bullish", "bearish")
        m2_dir_str = (f" {'▲' if dir_btc == 'long' else '▼'}" if dir_btc else "")
        row(S_MACRO, "[M2] BTC en tendance",
            f"TENDANCE CLAIRE{m2_dir_str}" if btc_trending else "EN RANGE — signal non exploitable",
            _dir_color(dir_btc),
            "" if btc_trending else "attends une tendance BTC directionnelle")
        board.add("M2_btc_trending", "trending" if btc_trending else "ranging", weight=1)
    else:
        row(S_MACRO, "[M3] Biais BTC", "ERREUR API", "bad")

    # ── 3. DÉRIVÉS ───────────────────────────
    # F1 — funding
    if md.funding:
        fr_val, fr_signal, fr_dir = assess_funding(md.funding)
        fr_pct = fr_val * 100 if fr_val else 0
        recent_fr = [float(r["fundingRate"]) * 100 for r in md.funding[-3:]]
        color = ("good" if fr_signal in ("favorable_long", "neutral")
                 else "warn" if "caution" in fr_signal else "bad")
        sig(S_DERIV, "[F1] Funding Rate", f"{fr_pct:+.4f}%  ({fr_signal})", color, fr_dir,
            f"annualisé: {fr_pct * 3 * 365:+.1f}%  |  3 derniers: {[f'{v:+.4f}%' for v in recent_fr]}")
        board.add("F1_funding", fr_dir if fr_dir else "neutral", weight=1)
        if abs(fr_pct) > 0.07:
            board.block("funding_extreme", f"Funding extrême ({fr_pct:+.4f}%) — flush probable")
    else:
        row(S_DERIV, "[F1] Funding Rate", "ERREUR API", "bad")

    st = md.stats_1h
    has_stats = st is not None and len(st) > 0

    # F2 — open interest (percentile 14 jours)
    if has_stats and "sumOpenInterestValue" in st.columns:
        oi_df = st.tail(336)
        oi_change, oi_current, _, oi_percentile = assess_oi(oi_df)
        is_extreme = oi_percentile >= 90
        is_very_low = oi_percentile <= 10
        row(S_DERIV, "[F2] Open Interest",
            f"${oi_current / 1e6:.1f}M  (6h: {oi_change:+.2f}%  |  percentile 14j: {oi_percentile:.0f}%)",
            "bad" if is_extreme else "warn" if oi_percentile >= 75 or is_very_low else "good",
            "⚠ EXTREME 14j — flush probable" if is_extreme
            else "⚠ TRES BAS 14j — volatilite imminente possible" if is_very_low else "")
        board.add("F2_oi", "extreme" if is_extreme else "neutral", weight=1)
        if is_extreme:
            board.block("oi_extreme",
                        f"OI au {oi_percentile:.0f}e percentile sur 14j — marche surexpose")
    else:
        row(S_DERIV, "[F2] Open Interest", "ERREUR API", "bad")

    # F4 — liquidations 8h (sommes horaires contract_stats, en contrats = SOL)
    liq_cols = ["long_liq_size", "short_liq_size"]
    if (has_stats and set(liq_cols) <= set(st.columns)
            and st[liq_cols].tail(8).notna().any().any()):
        liq8 = st.tail(8)
        long_liqs = float(liq8["long_liq_size"].fillna(0).sum())
        short_liqs = float(liq8["short_liq_size"].fillna(0).sum())
        dominant = max(long_liqs, short_liqs)
        block_reason = blocked_dir = None
        if dominant < LIQ_MIN_THRESHOLD:
            hint, color, liq_dir = (f"aucune liquidation significative (8h, total: "
                                    f"{long_liqs + short_liqs:.0f} contracts)"), "good", "safe"
        elif long_liqs > short_liqs * 2:
            hint, color, liq_dir = (f"pic LIQS LONGS ({long_liqs:.0f} contracts) — "
                                    f"eviter SHORT ici (bottom possible)"), "warn", "warning"
            block_reason = (f"Longs viennent d'etre liquides ({long_liqs:.0f} contracts) — "
                            f"ne pas shorter un potentiel bottom")
            blocked_dir = "short"
        elif short_liqs > long_liqs * 2:
            hint, color, liq_dir = (f"pic LIQS SHORTS ({short_liqs:.0f} contracts) — "
                                    f"eviter LONG ici (top possible)"), "warn", "warning"
            block_reason = (f"Shorts viennent d'etre liquides ({short_liqs:.0f} contracts) — "
                            f"ne pas longer un potentiel top")
            blocked_dir = "long"
        else:
            hint, color, liq_dir = (f"liqs equilibrees (L:{long_liqs:.0f} / "
                                    f"S:{short_liqs:.0f} contracts)"), "dim", "safe"
        row(S_DERIV, "[F4] Liquidations récentes (8h)", hint, color)
        board.add("F4_liquidations", liq_dir, weight=1)
        if block_reason:
            board.block("recent_liquidation", block_reason, direction=blocked_dir)
    else:
        row(S_DERIV, "[F4] Liquidations récentes", "ERREUR API", "bad")

    # F3 — ratio long/short retail (contrarian)
    if has_stats and "lsr_account" in st.columns and pd.notna(st["lsr_account"].iloc[-1]):
        lsr = st["lsr_account"].iloc[-1]
        long_acc = lsr / (1 + lsr)
        ls_df = pd.DataFrame({"longAccount": [long_acc], "shortAccount": [1 - long_acc]})
        _, ls_signal, ls_dir = assess_ls_ratio(ls_df)
        sig(S_DERIV, "[F3] Ratio Long/Short (contrarian)", ls_signal.upper(),
            _dir_color(ls_dir, "dim"), ls_dir,
            f"Retail: {long_acc * 100:.1f}% L / {(1 - long_acc) * 100:.1f}% S")
        board.add("F3_ls_ratio", ls_dir if ls_dir else "neutral", weight=1)
    else:
        row(S_DERIV, "[F3] Ratio Long/Short", "ERREUR API", "bad")

    # ── 4. CVD ───────────────────────────────
    flow_cols = ["long_taker_size", "short_taker_size", "mark_price"]
    flow = st[flow_cols] if has_stats and set(flow_cols) <= set(st.columns) else None

    # C1 — flux taker 6h
    c1_status, c1_dir, c1_cvd, c1_share, c1_chg = assess_taker_flow(
        flow.tail(6) if flow is not None else None)
    if c1_status != "unclear":
        color = ("good" if "bullish" in c1_status or c1_dir == "long"
                 else "bad" if "bearish" in c1_status or c1_dir == "short" else "warn")
        sig(S_CVD, "[C1] CVD taker 6h", c1_status.upper().replace("_", " "), color, c1_dir,
            f"CVD={c1_cvd:+,.0f} contrats  |  buy={c1_share * 100:.1f}%  |  prix {c1_chg:+.2f}%")
        board.add("C1_cvd", c1_dir if c1_dir else c1_status, weight=1)
        if "divergence" in c1_status:
            blocked = "long" if c1_status == "divergence_bearish" else "short"
            reason = (f"Prix +{c1_chg:.2f}% sur 6h mais flux taker vendeur (buy {c1_share * 100:.0f}%) — long non confirme"
                      if blocked == "long" else
                      f"Prix {c1_chg:.2f}% sur 6h mais flux taker acheteur (buy {c1_share * 100:.0f}%) — short risque")
            board.block("cvd_divergence", reason, direction=blocked)
    else:
        row(S_CVD, "[C1] CVD taker 6h", "ERREUR API", "bad")

    # C2 — momentum flux taker 40h
    c2_status, c2_dir, c2_cvd, c2_share, c2_chg = assess_taker_flow(
        flow.tail(40) if flow is not None else None, min_buy_share_dev=0.05)
    if c2_status != "unclear":
        sig(S_CVD, "[C2] Momentum taker 40h",
            "BULLISH" if c2_dir == "long" else "BEARISH" if c2_dir == "short" else "NEUTRE",
            _dir_color(c2_dir), c2_dir,
            f"CVD={c2_cvd:+,.0f} contrats  |  buy={c2_share * 100:.1f}%  |  prix {c2_chg:+.2f}%")
        board.add("C2_cvd_4h", c2_dir if c2_dir else "neutral", weight=1)
    elif h4 is not None and len(h4) >= 10:
        recent = h4.iloc[-10:]
        bull = int((recent["close"] > recent["open"]).sum())
        bear = int((recent["close"] < recent["open"]).sum())
        ratio = bull / len(recent)
        c2_dir = "long" if ratio > 0.60 else "short" if ratio < 0.40 else None
        row(S_CVD, "[C2] Momentum 4H (fallback bougies)",
            f"{bull} haussières / {bear} baissières  ->  "
            f"{'BULLISH' if c2_dir == 'long' else 'BEARISH' if c2_dir == 'short' else 'NEUTRE'}",
            _dir_color(c2_dir))
        board.add("C2_cvd_4h", c2_dir if c2_dir else "neutral", weight=1)

    # ── 5. HEATMAP (manuel) ──────────────────
    row(S_HEAT, "[L1] Liquidation Heatmap", "⚠ VÉRIFIER MANUELLEMENT", "warn",
        "→ https://coinank.com/chart/derivatives/liq-heat-map/solusdt/1w")
    row(S_HEAT, "", "(aucune API publique disponible)", "dim")

    # ── 6. ENTRÉE ────────────────────────────
    # C3 — absorption 15min
    absorbed, vol_ratio, body_pct = detect_absorption(m15)
    if m15 is None or len(m15) < 10:
        # Fail-safe : pas de données ≠ "pas d'absorption"
        row(S_ENTRY, "[C3] Absorption 15min", "DONNÉES INSUFFISANTES", "warn")
        board.add("C3_absorption", None, weight=1)
    elif absorbed:
        row(S_ENTRY, "[C3] Absorption 15min",
            f"ABSORPTION DETECTEE (vol x{vol_ratio:.1f} avg, body {body_pct:.2f}%)", "bad",
            "volume fort sans mouvement = vendeurs/acheteurs cachés")
        board.add("C3_absorption", "absorption", weight=1)
        board.block("absorption", "Absorption detectee sur 15min — attendre resolution")
    else:
        row(S_ENTRY, "[C3] Absorption 15min", "Aucune absorption detectee", "good")
        board.add("C3_absorption", "no_absorption", weight=1)

    # E1 — FVG le plus proche
    e1_dir = None
    if m15 is not None and len(m15) and price:
        fvgs = detect_fvg(m15, n_candles=80)
        if fvgs:
            ftype, flo, fhi, _ = min(fvgs, key=lambda f: abs(price - (f[1] + f[2]) / 2))
            dist = abs(price - (flo + fhi) / 2) / price * 100
            strictly_in = flo <= price <= fhi
            filled = (ftype == "bullish" and price > fhi) or (ftype == "bearish" and price < flo)
            approaching = not strictly_in and dist < 0.8
            if strictly_in:
                zone_label, in_zone, color = "DANS LA ZONE", True, "good"
            elif approaching and not filled:
                zone_label, in_zone, color = f"EN APPROCHE ({dist:.2f}%)", True, "warn"
            elif filled:
                zone_label = f"COMBLÉ — prix {'au-dessus' if ftype == 'bullish' else 'en dessous'}"
                in_zone, color = False, "dim"
            else:
                zone_label, in_zone, color = f"hors zone ({dist:.2f}%)", False, "dim"
            e1_dir = ("long" if ftype == "bullish" else "short") if in_zone else None
            row(S_ENTRY, "[E1] FVG le plus proche (15min)",
                f"{ftype.upper()} FVG [{flo:.2f} - {fhi:.2f}]  dist: {dist:.2f}%", color, zone_label)
            board.add("E1_fvg_ob", e1_dir if e1_dir else "neutral", weight=1)
        else:
            row(S_ENTRY, "[E1] FVG / Order Block", "Aucun FVG recent detectable", "dim")
            board.add("E1_fvg_ob", "none_detected", weight=1)
    else:
        row(S_ENTRY, "[E1] FVG / Order Block", "DONNÉES INSUFFISANTES", "dim")

    # E3 — bougie de confirmation 15min
    pattern, pattern_dir = detect_candle_confirmation(m15)
    if pattern:
        sig(S_ENTRY, "[E3] Confirmation bougie 15min", pattern.upper().replace("_", " "),
            _dir_color(pattern_dir), pattern_dir)
        board.add("E3_candle_confirm", pattern_dir, weight=1)
    else:
        row(S_ENTRY, "[E3] Confirmation bougie 15min", "Pas de pattern clair", "dim")
        board.add("E3_candle_confirm", "no_pattern" if m15 is not None else None, weight=1)

    if e1_dir and pattern_dir and e1_dir != pattern_dir:
        row(S_ENTRY, "[!] Contradiction E1/E3",
            f"FVG {e1_dir.upper()} vs bougie {pattern_dir.upper()} — signaux opposés", "warn",
            "prendre le signal E3 (bougie) comme confirmation prioritaire")

    # E4 — volume profile
    poc_price, lvns = calc_volume_profile(m15, n_candles=100, bins=40)
    if poc_price and price:
        dist_poc = abs(price - poc_price) / price * 100
        min_dist = price * 0.005
        above = [l for l in lvns if l > price + min_dist]
        below = [l for l in lvns if l < price - min_dist]
        lvn_str = (f"  LVN↑ {min(above):.2f}" if above else "") + \
                  (f"  LVN↓ {max(below):.2f}" if below else "")
        row(S_ENTRY, "[E4] Volume Profile (15min)",
            f"POC: {poc_price:.2f}  (dist: {dist_poc:.2f}%){lvn_str}",
            "good" if dist_poc < 1.0 else "dim")
        board.add("E4_vp_poc", "neutral", weight=1)
    else:
        row(S_ENTRY, "[E4] Volume Profile", "DONNÉES INSUFFISANTES", "dim")

    # R1 — SL structurel (pivots 4H)
    if h4 is not None and len(h4) and price:
        _r1_stop_levels(ev, h4, price, row)

    return ev


def _sl_warning(dist_pct, sign):
    lev_impact = dist_pct * 10
    if dist_pct < 1.0:
        return f" !! TROP SERRÉ ({dist_pct:.1f}%) — bruit de marché suffisant pour déclencher"
    if lev_impact >= 90:
        return f" !! TROP LARGE — liquidation x10 à {sign}10% (SL jamais atteint)"
    if lev_impact >= 70:
        return f" ! proche liquidation ({lev_impact:.0f}% de la position)"
    return ""


def _r1_stop_levels(ev, h4, price, row):
    board = ev.board
    _, sh4, sl4 = assess_structure(h4, n=SWING_N_FAST)
    sl_below = [v for v in sl4 if v < price]
    sh_above = [v for v in sh4 if v > price]
    parts, warns = [], []
    if sl_below:
        lvl = sl_below[-1]
        dist = (price - lvl) / price * 100
        w = _sl_warning(dist, "-")
        warns.append(w)
        parts.append(f"LONG: sous {lvl:.2f} (-{dist:.1f}%){w}")
        ev.sl_long = float(lvl)
        if dist < 1.0:
            board.block("sl_trop_serre_long", f"SL long à {dist:.1f}% — trop serré pour x10",
                        direction="long")
    else:
        parts.append("LONG: pas de pivot bas visible")
    if sh_above:
        lvl = sh_above[-1]
        dist = (lvl - price) / price * 100
        w = _sl_warning(dist, "+")
        warns.append(w)
        parts.append(f"SHORT: dessus {lvl:.2f} (+{dist:.1f}%){w}")
        ev.sl_short = float(lvl)
        if dist < 1.0:
            board.block("sl_trop_serre_short", f"SL short à {dist:.1f}% — trop serré pour x10",
                        direction="short")
    else:
        parts.append("SHORT: pas de pivot haut visible")

    if sl_below or sh_above:
        color = "bad" if any("TROP" in w for w in warns) else "dim"
        row(S_ENTRY, "[R1] SL suggéré (auto)", "  |  ".join(parts), color,
            "a valider — placer 0.3% au-dela du niveau")
        board.add("R1_sl_suggested", "computed", weight=1)
    else:
        row(S_ENTRY, "[R1] SL suggéré (auto)",
            "Pas de pivot structurel identifiable — SL manuel requis", "warn")
