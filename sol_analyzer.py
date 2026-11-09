#!/usr/bin/env python3
"""
SOL/USDT x10 — Automated Trade Signal Analyzer + Telegram Alerts
Gate.io Futures public API — no API key required

Dependencies:
    pip install requests pandas numpy colorama

Usage:
    python sol_analyzer.py                   # analyse one-shot, console only
    python sol_analyzer.py --notify          # one-shot + Telegram si signal actionnable
    python sol_analyzer.py --notify --force  # envoie la notif meme si signal inchange
    python sol_analyzer.py --watch 60        # refresh every 60s (local seulement)

Telegram setup:
    1. Cree un bot via @BotFather -> recupere TELEGRAM_TOKEN
    2. Recupere ton TELEGRAM_CHAT_ID via @userinfobot
    3. Exporte les vars d'env :
       export TELEGRAM_TOKEN="123456:ABC-xyz"
       export TELEGRAM_CHAT_ID="987654321"

GitHub Actions (execution sans PC) :
    Ajoute ces deux vars comme Repository Secrets dans Settings > Secrets.
    Le workflow .github/workflows/sol_signal.yml tourne selon le cron configure.

State & log :
    .sol_signal_state.json : etat du dernier signal notifie (anti-spam Telegram)
    signal_log.jsonl       : une ligne JSON par analyse (historique des verdicts)
    Les deux sont commites dans le repo par le workflow.

Architecture : le code vit dans le package soltrader/
    config.py   constantes et seuils
    api.py      appels Gate.io + Forex Factory
    signals.py  fonctions de calcul (pures, testees par pytest)
    scoring.py  SignalBoard (points, bloqueurs, verdict)
    analysis.py orchestration + affichage console
    notify.py   Telegram + state anti-spam
    logger.py   log JSONL des analyses
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

from soltrader.config import SYMBOL, NOTIFY_ON_STATUSES
from soltrader.display import C, DIM, RST
from soltrader.analysis import run_analysis
from soltrader.notify import (send_telegram, build_telegram_message,
                              load_state, save_state, signal_changed)
from soltrader.logger import build_record, append_log, read_log
from soltrader import paper
from soltrader.api import fetch_ohlcv
from soltrader.engine import closed_only


def main():
    parser = argparse.ArgumentParser(description="SOL/USDT x10 automated signal analyzer")
    parser.add_argument("--symbol",  default=SYMBOL,  help="Futures symbol (default: SOLUSDT)")
    parser.add_argument("--verbose", action="store_true", help="Extra debug info")
    parser.add_argument("--notify",  action="store_true",
                        help="Envoie une notification Telegram si signal actionnable")
    parser.add_argument("--force",   action="store_true",
                        help="Force l'envoi Telegram meme si le signal n'a pas change")
    parser.add_argument("--no-log",  action="store_true",
                        help="Ne pas ecrire l'analyse dans signal_log.jsonl")
    parser.add_argument("--paper",   action="store_true",
                        help="Met a jour le paper trading (positions fictives, cf. research/)")
    parser.add_argument("--min-interval", type=int, default=0,
                        help="Ignore le run si la derniere analyse loggee a moins de N minutes "
                             "(dedoublonne les crons redondants)")
    parser.add_argument("--watch",   type=int, default=0,
                        help="Refresh interval en secondes (0 = run once, local seulement)")
    args = parser.parse_args()

    if args.watch > 0:
        # Mode local en boucle — sans notif ni log pour eviter le spam
        print(C + f"\n  Mode watch: refresh toutes les {args.watch}s  (Ctrl+C pour quitter)" + RST)
        try:
            while True:
                print("\033[2J\033[H", end="")
                run_analysis(symbol=args.symbol, verbose=args.verbose)
                print(DIM + f"\n  Prochain refresh dans {args.watch}s...")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            print(C + "\n  Arret." + RST)
        return

    # Run unique (mode cron / GitHub Actions)
    if args.min_interval and recent_analysis(args.min_interval):
        print(DIM + f"  Derniere analyse il y a moins de {args.min_interval} min — run ignore." + RST)
        return

    status, direction, board, ev, md = run_analysis(symbol=args.symbol, verbose=args.verbose)
    price = md.price or 0.0
    now_str = md.now.strftime("%Y-%m-%d %H:%M UTC")
    _, _, long_pts, short_pts, completion = board.score()
    effective_status = board.effective_status(status, completion, direction)
    sent = False

    if args.notify:
        old_state = load_state()
        should_notify = (
            args.force
            or (effective_status in NOTIFY_ON_STATUSES
                and signal_changed(effective_status, direction, old_state, board))
        )

        if should_notify:
            msg = build_telegram_message(
                effective_status, direction, board, price,
                long_pts, short_pts, now_str, args.symbol
            )
            sent = send_telegram(msg)
            if sent:
                save_state({
                    "status":    effective_status,
                    "direction": direction,
                    "price":     price,
                    "timestamp": now_str,
                    "blockers":  [n for n, _, _ in board.blockers],
                })
        else:
            if effective_status not in NOTIFY_ON_STATUSES:
                print(DIM + f"\n  [NOTIFY] Signal '{effective_status}' non actionnable — pas de notif." + RST)
            else:
                print(DIM + f"\n  [NOTIFY] Signal inchange ({effective_status}/{direction}) — pas de notif." + RST)
                print(DIM + "           Utilise --force pour forcer l'envoi." + RST)

    if args.paper:
        run_paper(args, md, ev, effective_status, direction)

    if not args.no_log:
        append_log(build_record(now_str, price, status, effective_status,
                                direction, long_pts, short_pts, completion,
                                board, notified=sent))


def recent_analysis(minutes, now=None):
    """True si signal_log.jsonl contient une analyse de moins de `minutes` minutes."""
    last = read_log(limit=1)
    if not last:
        return False
    try:
        ts = datetime.strptime(last[-1]["ts"], "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return False
    now = now or datetime.now(timezone.utc)
    return timedelta(0) <= now - ts < timedelta(minutes=minutes)


def run_paper(args, md, ev, status, direction):
    """Paper trading : avance les positions fictives, en ouvre sur go/possible."""
    # 300 bougies 15 min = 75 h ≥ détention max (72 h)
    m15 = closed_only(fetch_ohlcv(args.symbol, "15m", limit=301), "15min", md.now)
    if m15 is None:
        print(DIM + "\n  [PAPER] Bougies 15 min indisponibles — mise à jour reportée." + RST)
        return
    events = paper.update(md, ev, status, direction, m15, md.funding)
    for kind, book, payload in events:
        msg = paper.format_event(kind, book, payload)
        if msg is None:
            print(DIM + f"\n  [PAPER {book}] signal non tradé : {payload}" + RST)
            continue
        print(C + "\n  " + msg.replace("\n", "\n  ") + RST)
        if args.notify:
            send_telegram(msg)


if __name__ == "__main__":
    main()
