#!/usr/bin/env python3
"""
Backtest — rejoue le moteur live heure par heure sur l'archive data/ et
simule l'exécution des `go` (frais, glissement, funding, SL/TP).

Pas de lookahead : à chaque instant t, le moteur ne voit que les périodes
clôturées avant t (engine.closed_only), exactement comme en live.
Règles et statut exploratoire : research/preregistration.md.

Usage:
    python backtest.py                    # toute l'archive disponible
    python backtest.py --days 30          # 30 derniers jours
    python backtest.py --refresh          # met d'abord l'archive à jour
    python backtest.py --out research/backtest_v2.json
"""

import argparse
import json
from datetime import timedelta

import pandas as pd

from soltrader import archive
from soltrader.engine import MarketData, evaluate, closed_only
from soltrader.execution import plan_trade, simulate, summarize



def closed_before(df, t, interval):
    """Périodes clôturées avant t — même définition que le live (pas de lookahead)."""
    return closed_only(df, interval, t)

# Tailles d'historique identiques aux requêtes live (soltrader/market.py)
LIVE_TAIL = {"sol_1d": 250, "sol_4h": 200, "sol_15m": 100, "btc_1d": 250, "stats_1h": 336}


def load_data(data_dir=archive.DATA_DIR):
    names = ["sol_1d", "sol_4h", "sol_15m", "btc_1d", "btc_15m", "stats_1h"]
    data = {n: archive.read_series(n, data_dir) for n in names}
    missing = [n for n, v in data.items() if v is None]
    if missing:
        raise SystemExit(f"  Archive incomplète: {missing} — lance "
                         f"`python -m soltrader.archive --backfill`.")
    st = data["stats_1h"]
    st["sumOpenInterestValue"] = st["open_interest_usd"]
    data["funding"] = archive.funding_from_stats(st)
    return data


def market_at(t, data):
    """Instantané MarketData vu à l'instant t, ou None si trop peu d'historique."""
    d1  = closed_before(data["sol_1d"], t, "1D").tail(LIVE_TAIL["sol_1d"])
    h4  = closed_before(data["sol_4h"], t, "4h").tail(LIVE_TAIL["sol_4h"])
    m15 = closed_before(data["sol_15m"], t, "15min").tail(LIVE_TAIL["sol_15m"])
    b1d = closed_before(data["btc_1d"], t, "1D").tail(LIVE_TAIL["btc_1d"])
    b15 = closed_before(data["btc_15m"], t, "15min")
    st  = closed_before(data["stats_1h"], t, "1h").tail(LIVE_TAIL["stats_1h"])
    if len(m15) < 100 or len(h4) < 30 or len(d1) < 210 or len(st) < 48 or b15.empty:
        return None
    ts = t.timestamp()
    return MarketData(
        now=t.to_pydatetime(),
        price=float(m15["close"].iloc[-1]),
        btc_price=float(b15["close"].iloc[-1]),
        sol_1d=d1, sol_4h=h4, sol_15m=m15, btc_1d=b1d, stats_1h=st,
        funding=[r for r in data["funding"] if r["fundingTime"] <= ts][-10:],
        macro=None, replay=True,
    )


def run(data, start, end, step_h=1):
    """Rejoue [start, end]. Retourne (verdicts, trades_go, trades_possible, refus)."""
    m15_all = data["sol_15m"]
    verdicts, trades = [], {"go": [], "possible": []}
    busy_until = {"go": start, "possible": start}
    refusals = {}
    t = start
    while t <= end:
        md = market_at(t, data)
        if md is not None:
            ev = evaluate(md)
            status, direction, lp, sp, completion = ev.board.score()
            eff = ev.board.effective_status(status, completion, direction)
            verdicts.append({"ts": t.isoformat(), "price": md.price, "status": eff,
                             "direction": direction, "long_pts": lp, "short_pts": sp})
            if eff in trades and direction and t >= busy_until[eff]:
                level = ev.sl_long if direction == "long" else ev.sl_short
                tr, why = plan_trade(direction, md.price, t, level,
                                     meta={"status": eff, "long_pts": lp, "short_pts": sp})
                if tr is None:
                    refusals[why.split(" (")[0]] = refusals.get(why.split(" (")[0], 0) + 1
                else:
                    tr = simulate(tr, m15_all, data["funding"])
                    if tr.status == "closed":
                        trades[eff].append(tr)
                        busy_until[eff] = pd.Timestamp(tr.exit_time)
                    else:
                        busy_until[eff] = end + timedelta(days=1)   # toujours ouvert en fin de données
        t += timedelta(hours=step_h)
    return verdicts, trades["go"], trades["possible"], refusals


def _fmt(s):
    if not s.get("n"):
        return "    aucun trade clôturé"
    return (f"    trades {s['n']} ({s['long']} long / {s['short']} short)  |  "
            f"win {s['win_rate'] * 100:.0f}%  |  R net moyen {s['avg_r']:+.2f}  "
            f"(médiane {s['median_r']:+.2f})  |  total {s['sum_r']:+.1f} R\n"
            f"    profit factor {s['profit_factor']:.2f}  |  drawdown max {s['max_drawdown_r']:.1f} R  |  "
            f"net moyen {s['avg_net_pct']:+.2f}% du notionnel  |  coûts moyens {s['avg_costs_pct']:.2f}%\n"
            f"    durée moyenne {s['avg_hold_h']:.0f} h  |  sorties {s['exits']}")


def main():
    parser = argparse.ArgumentParser(description="Backtest des verdicts sol-trader")
    parser.add_argument("--days", type=int, default=0, help="Derniers N jours (0 = tout)")
    parser.add_argument("--step", type=int, default=1, help="Pas en heures")
    parser.add_argument("--refresh", action="store_true", help="Met l'archive à jour avant")
    parser.add_argument("--out", help="Écrit le résultat détaillé en JSON")
    args = parser.parse_args()

    if args.refresh:
        archive.update()
    data = load_data()
    end = data["sol_15m"].index[-1] + pd.Timedelta("15min")
    first = data["sol_15m"].index[0] + pd.Timedelta(hours=26)   # 100 bougies 15 min
    start = max(first, end - timedelta(days=args.days)) if args.days else first
    start = start.ceil("1h") + pd.Timedelta("15min")              # HH:15, comme le cron

    print(f"  Rejeu {start:%Y-%m-%d %H:%M} → {end:%Y-%m-%d %H:%M} UTC, pas {args.step} h…")
    verdicts, go, possible, refusals = run(data, start, end, args.step)
    counts = pd.Series([v["status"] for v in verdicts]).value_counts()

    print(f"\n  ── VERDICTS ({len(verdicts)}) " + "─" * 40)
    for s, n in counts.items():
        print(f"  {s:<22} {n:>5} ({n / len(verdicts) * 100:.0f}%)")
    print(f"\n  ── TRADES SUR `go` (règle pré-enregistrée H1) " + "─" * 16)
    s_go = summarize(go)
    print(_fmt(s_go))
    print(f"\n  ── TRADES SUR `possible` (exploratoire) " + "─" * 22)
    s_pos = summarize(possible)
    print(_fmt(s_pos))
    if refusals:
        print(f"\n  Signaux non tradés (stop invalide) : {refusals}")
    print("\n  Rappel : backtest EXPLORATOIRE (voir research/preregistration.md) ;"
          "\n  M1 macro non rejouable → plus de trades qu'en live.\n")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"start": start.isoformat(), "end": end.isoformat(),
                       "verdicts": counts.to_dict(), "refusals": refusals,
                       "go": {"summary": s_go, "trades": [t.to_dict() for t in go]},
                       "possible": {"summary": s_pos, "trades": [t.to_dict() for t in possible]}},
                      f, ensure_ascii=False, indent=1, default=str)
        print(f"  Détail écrit dans {args.out}")


if __name__ == "__main__":
    main()
