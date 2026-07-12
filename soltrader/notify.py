"""Notification Telegram + state anti-spam."""

import json
import time
import requests

from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, STATE_FILE
from .display import G, R, Y, RST


def send_telegram(text: str, retries: int = 3) -> bool:
    """Envoie un message Telegram en texte brut. Retente jusqu'à `retries` fois.
    IMPORTANT : pas de parse_mode (HTML cause des 400 sur caractères spéciaux)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print(Y + "  [TELEGRAM] Token ou chat_id manquant — notif ignoree." + RST)
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    for attempt in range(1, retries + 1):
        try:
            r = requests.post(url, json=payload, timeout=15)
            if not r.ok:
                print(R + f"  [TELEGRAM] Erreur {r.status_code}: {r.text[:200]}" + RST)
                return False
            print(G + "  [TELEGRAM] Notification envoyee." + RST)
            return True
        except requests.exceptions.Timeout:
            print(Y + f"  [TELEGRAM] Timeout (tentative {attempt}/{retries})" + RST)
            if attempt < retries:
                time.sleep(3 * attempt)
        except requests.exceptions.RequestException as e:
            print(R + f"  [TELEGRAM] Erreur reseau : {e}" + RST)
            return False
    print(R + "  [TELEGRAM] Echec apres toutes les tentatives." + RST)
    return False


def build_telegram_message(status: str, direction, board, price: float,
                            long_pts: int, short_pts: int,
                            now_str: str, symbol: str) -> str:
    """Construit le message Telegram en texte brut."""
    dir_label = "LONG" if direction == "long" else ("SHORT" if direction == "short" else "?")

    SEP = "-" * 40

    if status == "go":
        header = f"[SIGNAL] {'▲' if direction=='long' else '▼'} {dir_label} -- SOL/USDT x10"
    elif status == "blocked":
        header = "[BLOQUEUR] NE PAS TRADER -- SOL/USDT x10"
    elif status == "directional_blocked":
        blocked_dirs = sorted({d.upper() for _, _, d in board.blockers if d})
        header = f"[BLOQUEUR {'/'.join(blocked_dirs)}] SOL/USDT x10 -- sens oppose envisageable"
    elif status == "possible":
        header = f"[POSSIBLE] {'▲' if direction=='long' else '▼'} {dir_label} -- SOL/USDT x10"
    else:
        header = "[INFO] Pas de signal -- SOL/USDT x10"

    lines = [
        SEP,
        header,
        SEP,
        f"Date  : {now_str}",
        f"Prix  : {price:.4f} USDT",
        f"Score : LONG {long_pts} pts / SHORT {short_pts} pts",
        "",
    ]

    all_blockers = board.blockers  # list of (name, reason, direction)
    if all_blockers:
        lines.append("== BLOQUEURS ==")
        for name, reason, bdir in all_blockers:
            r    = reason[:120] + "..." if len(reason) > 120 else reason
            dlbl = f"[{bdir.upper()}] " if bdir else ""
            lines.append(f"  ! {dlbl}{name}: {r}")
        lines.append("")

    actionable = [s for s in board.signals if s[1] in ("long", "short")]
    if actionable:
        lines.append("== SIGNAUX ==")
        for name, sig_dir, weight, detail in actionable[-8:]:
            arrow = "+" if sig_dir == "long" else "-"
            w_str = " [x2]" if weight == 2 else ""
            lines.append(f"  {arrow} {name}{w_str}")
        lines.append("")

    lines += [
        "== VERIFICATION MANUELLE ==",
        "  Heatmap : coinank.com/chart/derivatives/liq-heat-map/solusdt/1w",
        "  Macro   : fr.investing.com/economic-calendar",
        "  CVD     : velo.xyz/futures/SOL",
        SEP,
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────
# STATE — évite les doublons de notif
# ─────────────────────────────────────────────

def load_state() -> dict:
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state: dict):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except OSError as e:
        print(Y + f"  [STATE] Impossible d'écrire {STATE_FILE}: {e}" + RST)


def signal_changed(new_status: str, new_direction, old_state: dict,
                   board=None) -> bool:
    """
    Retourne True si le signal a changé depuis la dernière notification.
    Compare status, direction ET les bloqueurs actifs (pour éviter
    les doublons quand seul le status 'blocked' persiste mais que
    les bloqueurs changent — ex: oi_extreme remplacé par cvd_divergence).
    """
    if not old_state:
        return True
    if old_state.get("status") != new_status:
        return True
    if old_state.get("direction") != new_direction:
        return True
    # Compare le fingerprint des bloqueurs actifs
    if board is not None:
        new_blockers = frozenset(n for n, _, _ in board.blockers)
        old_blockers = frozenset(old_state.get("blockers", []))
        if new_blockers != old_blockers:
            return True
    return False
