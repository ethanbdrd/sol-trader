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

from soltrader.config import SYMBOL, BTC_SYMBOL
from soltrader.api import fetch_ohlcv, fetch_contract_stats, fetch_funding_rate
from soltrader.engine import MarketData, evaluate, closed_only


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
    return data


closed_before = closed_only   # même définition que le live (pas de lookahead)


def market_at(t, data):
    """Reconstruit l'instantané MarketData vu à l'instant t, ou None si trop peu de données."""
    d1  = closed_before(data["sol_1d"], t, "1D")
    h4  = closed_before(data["sol_4h"], t, "4h")
    m15 = closed_before(data["sol_15m"], t, "15min")
    b1d = closed_before(data["btc_1d"], t, "1D")
    st  = closed_before(data["stats_1h"], t, "1h")
    if len(m15) < 100 or len(h4) < 30 or len(d1) < 210 or len(st) < 48:
        return None
    return MarketData(
        now=t.to_pydatetime(),
        price=float(m15["close"].iloc[-1]),
        btc_price=float(b1d["close"].iloc[-1]),
        sol_1d=d1, sol_4h=h4, sol_15m=m15.tail(100), btc_1d=b1d,
        stats_1h=st.tail(336),
        funding=[r for r in data["funding"] if int(r["fundingTime"]) <= t.timestamp()],
        macro=None, replay=True,
    )


def evaluate_at(t, data):
    """Board du moteur à l'instant t. Retourne (board, price) ou None."""
    md = market_at(t, data)
    if md is None:
        return None
    return evaluate(md).board, md.price


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
