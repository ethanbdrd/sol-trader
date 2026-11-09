"""Analyse live — récupère les données, appelle le moteur, affiche le résultat."""

from colorama import Fore, Back, Style

from .config import SYMBOL, AUTO_ITEMS, MANUAL_ITEMS
from .display import (W, DIM, G, R, Y, RST,
                      header, section, row, signal_row, verdict_box)
from .engine import evaluate, SECTIONS
from .market import fetch_market_data

COLORS = {"good": G, "bad": R, "warn": Y, "dim": DIM, "info": W}


def render_rows(rows):
    """Affiche les lignes du moteur, regroupées par section dans l'ordre."""
    for sec in SECTIONS:
        sec_rows = [r for r in rows if r.section == sec]
        if not sec_rows:
            continue
        section(sec)
        for r in sec_rows:
            color = COLORS.get(r.color, W)
            if r.signal:
                signal_row(r.label, r.value, color, r.direction, r.hint)
            else:
                row(r.label, r.value, color, r.hint)


def run_analysis(symbol=SYMBOL, verbose=False, md=None):
    """Analyse complète. Retourne (status, direction, board, evaluation, market_data)."""
    md = md or fetch_market_data(symbol)
    ev = evaluate(md)
    board = ev.board

    header(f"SOL/USDT × 10  —  {md.now.strftime('%Y-%m-%d %H:%M UTC')}")
    if md.price:
        btc_str = f"   {DIM}BTC  {W}{md.btc_price:,.2f} USDT" if md.btc_price else ""
        print(f"\n  {DIM}Prix SOL  {W}{md.price:.4f} USDT{RST}{btc_str}")
    render_rows(ev.rows)

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

    return status, direction, board, ev, md
