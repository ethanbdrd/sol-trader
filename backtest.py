#!/usr/bin/env python3
"""
Backtest simple — rejoue les verdicts du système sur les N derniers jours.

Aucune exécution simulée : à chaque pas de temps on reconstruit les signaux
avec les données disponibles À CE MOMENT (pas de lookahead), on calcule le
verdict, puis on mesure le mouvement du prix 4h et 24h plus tard.

Limites v1 :
- fenêtre max ~13 jours (contract_stats et klines 15m ne remontent pas plus loin)
- M1 (calendrier macro) non rejouable → item non répondu (completion max 95%)
- F4 utilise les liquidations horaires de contract_stats (pas /liq_orders)

Usage:
    python backtest.py                 # 12 jours, pas de 4h
    python backtest.py --days 8 --step 2
"""

import argparse
import pandas as pd
from datetime import timedelta

from soltrader.config import (SYMBOL, BTC_SYMBOL, SWING_N, SWING_N_FAST,
                              LIQ_MIN_THRESHOLD)
from soltrader.api import fetch_ohlcv, fetch_contract_stats, fetch_funding_rate
from soltrader.signals import (calc_mas, assess_structure, detect_bos_choch,
                               detect_fvg, detect_candle_confirmation,
                               detect_absorption, calc_volume_profile,
                               check_session, assess_funding, assess_oi,
                               assess_ls_ratio, assess_taker_flow)
from soltrader.scoring import SignalBoard


def load_data():
    """Fetch une seule fois toutes les séries nécessaires."""
    print("  Chargement des données Gate.io...")
    data = {
        "sol_1d":  fetch_ohlcv(SYMBOL, "1d", limit=300),
        "sol_4h":  fetch_ohlcv(SYMBOL, "4h", limit=500),
        "sol_15m": fetch_ohlcv(SYMBOL, "15m", limit=2000),
        "btc_1d":  fetch_ohlcv(BTC_SYMBOL, "1d", limit=300),
        "stats_1h": fetch_contract_stats(SYMBOL, interval="1h", limit=336),
        "funding": fetch_funding_rate(SYMBOL, limit=100),
    }
    missing = [k for k, v in data.items() if v is None]
    if missing:
        raise SystemExit(f"  Données manquantes: {missing} — abandon.")
    # Normalise les colonnes numériques de contract_stats
    st = data["stats_1h"]
    for col in ("long_taker_size", "short_taker_size", "mark_price",
                "open_interest_usd", "lsr_account",
                "long_liq_size", "short_liq_size"):
        if col in st.columns:
            st[col] = pd.to_numeric(st[col], errors="coerce")
    st["sumOpenInterestValue"] = st.get("open_interest_usd")
    st = st.set_index("timestamp")
    data["stats_1h"] = st
    return data


def closed_before(df, t, interval):
    """Bougies entièrement clôturées avant t (pas de lookahead)."""
    return df[df.index <= t - pd.Timedelta(interval)]


def evaluate_at(t, data):
    """Reconstruit le board à l'instant t. Retourne (board, price) ou None."""
    d1  = closed_before(data["sol_1d"], t, "1d")
    h4  = closed_before(data["sol_4h"], t, "4h")
    m15 = closed_before(data["sol_15m"], t, "15min")
    b1d = closed_before(data["btc_1d"], t, "1d")
    st  = data["stats_1h"][data["stats_1h"].index <= t - pd.Timedelta("1h")]

    if len(m15) < 100 or len(h4) < 30 or len(d1) < 210 or len(st) < 48:
        return None

    price = m15["close"].iloc[-1]
    btc_price = b1d["close"].iloc[-1]
    d1, h4, m15, b1d = calc_mas(d1), calc_mas(h4), calc_mas(m15), calc_mas(b1d)
    board = SignalBoard()

    # S1 — structure daily
    struct_d, _, _ = assess_structure(d1, n=SWING_N)
    dir_d = "long" if struct_d == "bullish" else ("short" if struct_d == "bearish" else None)
    board.add("S1_daily_structure", dir_d if dir_d else struct_d, weight=2)

    # S2 — MA200 daily
    ma200 = d1["ma200"].iloc[-1]
    if pd.notna(ma200):
        board.add("S2_ma200", "long" if price > ma200 else "short", weight=2)
    else:
        board.add("S2_ma200", None)

    # S3 — structure 4H
    struct_4h, _, _ = assess_structure(h4, n=SWING_N_FAST)
    dir_4h = "long" if struct_4h == "bullish" else ("short" if struct_4h == "bearish" else None)
    board.add("S3_4h_structure", dir_4h if dir_4h else struct_4h, weight=1)

    # S5 — MAs 15min
    last = m15.iloc[-1]
    ma5, ma15_, ma30 = last["ma5"], last["ma15"], last["ma30"]
    ma30_slope = m15["ma30"].iloc[-1] - m15["ma30"].iloc[-5]
    if ma5 > ma15_ > ma30 and ma30_slope > 0:
        board.add("S5_ma_alignment", "long", weight=1)
    elif ma5 < ma15_ < ma30 and ma30_slope < 0:
        board.add("S5_ma_alignment", "short", weight=1)
    else:
        board.add("S5_ma_alignment", "range", weight=1)

    # S4 — BOS / CHoCH
    bos_signal, _ = detect_bos_choch(h4, struct_4h, n=SWING_N_FAST)
    bos_dir = ("long" if bos_signal == "bullish_bos"
               else "short" if bos_signal == "bearish_bos" else None)
    board.add("S4_bos_choch",
              bos_dir if bos_dir else ("choch" if bos_signal == "choch_warning" else "neutral"),
              weight=1)
    if bos_signal == "choch_warning":
        board.block("choch_4h", "CHoCH 4H")

    # E2 — session (depuis le timestamp rejoué)
    session_id, _, _ = check_session(t.to_pydatetime())
    board.add("E2_session", session_id, weight=1)
    if session_id == "asian":
        board.block("session", "Session asiatique")

    # M1 — calendrier macro : non rejouable → non répondu

    # M2/M3 — biais BTC
    struct_btc, _, _ = assess_structure(b1d, n=SWING_N)
    ma200_btc = b1d["ma200"].iloc[-1]
    if pd.notna(ma200_btc):
        dir_btc = "long" if (struct_btc == "bullish" and btc_price > ma200_btc) \
                  else "short" if (struct_btc == "bearish" and btc_price < ma200_btc) \
                  else None
        board.add("M3_btc_bias", dir_btc if dir_btc else "neutral", weight=1)
        btc_trending = struct_btc in ("bullish", "bearish")
        m2_dir = dir_btc if btc_trending else None
        board.add("M2_btc_trending", m2_dir if m2_dir else "ranging", weight=1)
    else:
        board.add("M3_btc_bias", None)
        board.add("M2_btc_trending", None)

    # F1 — funding au moment t
    rates_before = [r for r in data["funding"] if int(r["fundingTime"]) <= t.timestamp()]
    fr_val, fr_signal, fr_dir = assess_funding(rates_before)
    board.add("F1_funding", fr_dir if fr_dir else ("neutral" if fr_val is not None else None), weight=1)
    if fr_val is not None and abs(fr_val * 100) > 0.07:
        board.block("funding_extreme", f"Funding {fr_val*100:+.4f}%")

    # F2 — OI percentile sur la fenêtre disponible avant t
    oi_slice = st.tail(336)
    _, _, _, oi_percentile = assess_oi(oi_slice)
    is_extreme = oi_percentile >= 90
    board.add("F2_oi", "extreme" if is_extreme else "neutral", weight=1)
    if is_extreme:
        board.block("oi_extreme", f"OI percentile {oi_percentile:.0f}")

    # F4 — liquidations 8h (sommes horaires contract_stats)
    liq8 = st.tail(8)
    long_liqs  = float(liq8.get("long_liq_size", pd.Series(dtype=float)).sum() or 0)
    short_liqs = float(liq8.get("short_liq_size", pd.Series(dtype=float)).sum() or 0)
    dominant = max(long_liqs, short_liqs)
    if dominant < LIQ_MIN_THRESHOLD:
        board.add("F4_liquidations", "safe", weight=1)
    elif long_liqs > short_liqs * 2:
        board.add("F4_liquidations", "warning", weight=1)
        board.block("recent_liquidation", f"liqs longs {long_liqs:.0f}", direction="short")
    elif short_liqs > long_liqs * 2:
        board.add("F4_liquidations", "warning", weight=1)
        board.block("recent_liquidation", f"liqs shorts {short_liqs:.0f}", direction="long")
    else:
        board.add("F4_liquidations", "safe", weight=1)

    # F3 — ratio L/S retail
    if "lsr_account" in st.columns and len(st):
        lsr = st["lsr_account"].iloc[-1]
        ls_df = pd.DataFrame({"longAccount": [lsr / (1 + lsr)],
                              "shortAccount": [1 - lsr / (1 + lsr)]})
        _, _, ls_dir = assess_ls_ratio(ls_df)
        board.add("F3_ls_ratio", ls_dir if ls_dir else "neutral", weight=1)
    else:
        board.add("F3_ls_ratio", None)

    # C1 — flux taker 6h / C2 — 40h
    flow_cols = st[["long_taker_size", "short_taker_size", "mark_price"]]
    c1_status, c1_dir, *_ = assess_taker_flow(flow_cols.tail(6))
    board.add("C1_cvd", c1_dir if c1_dir else c1_status, weight=1)
    if "divergence" in c1_status:
        board.block("cvd_divergence", c1_status,
                    direction="long" if c1_status == "divergence_bearish" else "short")
    c2_status, c2_dir, *_ = assess_taker_flow(flow_cols.tail(40), min_buy_share_dev=0.05)
    board.add("C2_cvd_4h", c2_dir if c2_dir else "neutral", weight=1)

    # C3 — absorption 15min
    absorbed, _, _ = detect_absorption(m15)
    board.add("C3_absorption", "absorption" if absorbed else "no_absorption", weight=1)
    if absorbed:
        board.block("absorption", "Absorption 15min")

    # E1 — FVG le plus proche
    fvgs = detect_fvg(m15, n_candles=80)
    e1_val = "none_detected"
    if fvgs:
        nearest = min(fvgs, key=lambda f: abs(price - (f[1] + f[2]) / 2))
        ftype, flo, fhi, _ = nearest
        dist = abs(price - (flo + fhi) / 2) / price * 100
        strictly_in = flo <= price <= fhi
        filled = (ftype == "bullish" and price > fhi) or (ftype == "bearish" and price < flo)
        in_zone = strictly_in or (not filled and dist < 0.8)
        e1_val = ("long" if ftype == "bullish" else "short") if in_zone else "neutral"
    board.add("E1_fvg_ob", e1_val, weight=1)

    # E3 — pattern bougie
    _, pattern_dir = detect_candle_confirmation(m15)
    board.add("E3_candle_confirm", pattern_dir if pattern_dir else "no_pattern", weight=1)

    # E4 — volume profile
    poc, _ = calc_volume_profile(m15, n_candles=100, bins=40)
    board.add("E4_vp_poc", "neutral" if poc else None, weight=1)

    # R1 — SL structurel + bloqueurs SL trop serré
    _, sh4, sl4 = assess_structure(h4, n=SWING_N_FAST)
    sl_below = [v for v in sl4 if v < price]
    sh_above = [v for v in sh4 if v > price]
    if sl_below or sh_above:
        board.add("R1_sl_suggested", "computed", weight=1)
    if sl_below and (price - sl_below[-1]) / price * 100 < 1.0:
        board.block("sl_trop_serre_long", "", direction="long")
    if sh_above and (sh_above[-1] - price) / price * 100 < 1.0:
        board.block("sl_trop_serre_short", "", direction="short")

    return board, price


def forward_return(m15, t, hours):
    """Retour du prix entre t et t+hours (en %), None si hors données."""
    start = m15[m15.index <= t]
    end   = m15[m15.index <= t + pd.Timedelta(hours=hours)]
    if len(start) == 0 or len(end) == 0 or end.index[-1] < t + pd.Timedelta(hours=hours - 1):
        return None
    p0, p1 = start["close"].iloc[-1], end["close"].iloc[-1]
    return (p1 - p0) / p0 * 100


def main():
    parser = argparse.ArgumentParser(description="Backtest des verdicts sol-trader")
    parser.add_argument("--days", type=int, default=12, help="Jours à rejouer (max ~13)")
    parser.add_argument("--step", type=int, default=4,  help="Pas en heures")
    args = parser.parse_args()

    data = load_data()
    m15 = data["sol_15m"]
    end = m15.index[-1]
    start = end - timedelta(days=min(args.days, 13))

    results = []
    t = start.ceil("4h")
    while t <= end:
        out = evaluate_at(t, data)
        if out:
            board, price = out
            status, direction, lp, sp, completion = board.score()
            eff = board.effective_status(status, completion)
            results.append({
                "ts": t, "price": price, "status": eff, "direction": direction,
                "long_pts": lp, "short_pts": sp,
                "blocked_dir": direction and board.is_blocked_for(direction),
                "fwd4":  forward_return(m15, t, 4),
                "fwd24": forward_return(m15, t, 24),
            })
        t += timedelta(hours=args.step)

    if not results:
        raise SystemExit("  Pas assez de données pour rejouer la période demandée.")

    # ── Table des verdicts ──
    print(f"\n  {'DATE (UTC)':<18} {'PRIX':>8}  {'STATUT':<20} {'DIR':<6} {'PTS':>7}  {'+4h':>7}  {'+24h':>7}")
    print("  " + "-" * 84)
    for r in results:
        f4  = f"{r['fwd4']:+.2f}%" if r['fwd4']  is not None else "   —"
        f24 = f"{r['fwd24']:+.2f}%" if r['fwd24'] is not None else "   —"
        dir_s = (r["direction"] or "—").upper()
        print(f"  {r['ts'].strftime('%m-%d %H:%M'):<18} {r['price']:>8.2f}  "
              f"{r['status']:<20} {dir_s:<6} {r['long_pts']:>2}L/{r['short_pts']:<2}S  {f4:>7}  {f24:>7}")

    # ── Synthèse ──
    print("\n  ── SYNTHÈSE " + "─" * 50)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for s, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {s:<22} {n:>3} ({n/len(results)*100:.0f}%)")

    # Signaux "tradables" : possible/go avec direction non bloquée
    tradeable = [r for r in results
                 if r["status"] in ("possible", "go") and r["direction"]
                 and not r["blocked_dir"]]
    for horizon, key in (("4h", "fwd4"), ("24h", "fwd24")):
        rets = [(r[key] if r["direction"] == "long" else -r[key])
                for r in tradeable if r[key] is not None]
        if rets:
            wins = sum(1 for x in rets if x > 0)
            print(f"\n  Signaux possible/go non bloqués ({len(rets)}) — horizon {horizon}:")
            print(f"    retour signé moyen : {sum(rets)/len(rets):+.2f}%"
                  f"   |   win rate : {wins}/{len(rets)} ({wins/len(rets)*100:.0f}%)")
    if not tradeable:
        print("\n  Aucun signal possible/go non bloqué sur la période.")
    print()


if __name__ == "__main__":
    main()
