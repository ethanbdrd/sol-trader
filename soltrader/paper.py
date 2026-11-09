"""Paper trading — trades fictifs ouverts sur les verdicts live.

Deux carnets indépendants, une position au plus chacun :
- "go"       : test confirmatoire de H1 (research/preregistration.md)
- "possible" : exploratoire
Mêmes règles que le backtest (soltrader/execution.py). Aucun ordre réel.

État : .paper_state.json (positions ouvertes) ; historique :
paper_trades.jsonl (trades clôturés, une ligne chacun).
"""

import json

import pandas as pd

from .config import PAPER_STATE, PAPER_LOG
from .execution import Trade, plan_trade, simulate, summarize

BOOKS = ("go", "possible")


def load_state(path=PAPER_STATE):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        raw = {}
    return {b: (Trade.from_dict(raw[b]) if raw.get(b) else None) for b in BOOKS}


def save_state(state, path=PAPER_STATE):
    with open(path, "w", encoding="utf-8") as f:
        json.dump({b: (t.to_dict() if t else None) for b, t in state.items()}, f, indent=1)


def read_trades(path=PAPER_LOG):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return out


def _append_trade(book, trade, path):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"book": book, **trade.to_dict()}, ensure_ascii=False) + "\n")


def update(md, ev, status, direction, candles_15m, funding,
           state_path=PAPER_STATE, log_path=PAPER_LOG):
    """Fait avancer les positions ouvertes puis ouvre si le verdict le permet.

    candles_15m : bougies 15 min CLÔTURÉES couvrant au moins la détention max.
    Retourne la liste des événements [(type, book, Trade)] (type = open|close).
    """
    state = load_state(state_path)
    events = []
    for book in BOOKS:
        tr = state[book]
        if tr is not None:
            tr = simulate(tr, candles_15m, funding)
            if tr.status == "closed":
                _append_trade(book, tr, log_path)
                events.append(("close", book, tr))
                tr = None
            state[book] = tr
        if state[book] is None and status == book and direction:
            level = ev.sl_long if direction == "long" else ev.sl_short
            lp, sp = ev.board.score()[2:4]
            new, why = plan_trade(direction, md.price, md.now, level,
                                  meta={"long_pts": lp, "short_pts": sp})
            if new is not None:
                state[book] = new
                events.append(("open", book, new))
            else:
                events.append(("skip", book, why))
    save_state(state, state_path)
    return events


def format_event(kind, book, payload):
    """Message Telegram court pour un événement paper."""
    tag = "GO" if book == "go" else "POSSIBLE"
    if kind == "skip":
        return None
    t = payload
    arrow = "▲ LONG" if t.direction == "long" else "▼ SHORT"
    if kind == "open":
        return (f"[PAPER {tag}] Ouverture {arrow} SOL/USDT\n"
                f"Entree {t.entry:.3f}  |  Stop {t.stop:.3f} (-{t.risk_pct:.2f}%)  |  "
                f"Objectif {t.target:.3f} (2R)\n"
                f"Trade fictif, aucun ordre passe.")
    hold = (pd.Timestamp(t.exit_time) - pd.Timestamp(t.entry_time)).total_seconds() / 3600
    return (f"[PAPER {tag}] Cloture {arrow} -- {t.exit_reason.upper()}\n"
            f"Entree {t.entry:.3f} -> sortie {t.exit_price:.3f} en {hold:.0f} h\n"
            f"Net {t.net_pct:+.2f}% du notionnel ({t.r_net:+.2f} R, {t.margin_pct:+.1f}% de marge a x10)\n"
            f"Frais+gliss. {t.costs_pct:.2f}%  |  funding {t.funding_pct:+.3f}%")


def stats_by_book(path=PAPER_LOG):
    rows = read_trades(path)
    out = {}
    for book in BOOKS:
        trades = [Trade.from_dict({k: v for k, v in r.items() if k != "book"})
                  for r in rows if r.get("book") == book]
        out[book] = summarize(trades)
    return out
