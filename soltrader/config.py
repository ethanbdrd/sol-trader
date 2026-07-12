"""Configuration centrale — constantes et seuils du système."""

import os

GATE_BASE   = "https://api.gateio.ws/api/v4"

SYMBOL      = "SOLUSDT"
BTC_SYMBOL  = "BTCUSDT"
TIMEOUT     = 10

# Telegram — lus depuis les variables d'environnement
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Fichier de state pour eviter les doublons de notif
STATE_FILE = ".sol_signal_state.json"

# Log JSONL de chaque analyse (commité par le workflow pour analyse a posteriori)
LOG_FILE = "signal_log.jsonl"

# Statuts qui declenchent une notification Telegram
NOTIFY_ON_STATUSES = {"go", "blocked", "directional_blocked", "possible"}

# Total items dans la checklist HTML (27)
# Le script en automatise 20 — les 7 restants sont manuels
TOTAL_CHECKLIST_ITEMS = 27
AUTO_ITEMS   = 20   # automatisés par ce script
MANUAL_ITEMS = 7    # nécessitent vraiment des données personnelles

# Gates du verdict :
# - completion = items auto évalués avec données dispo / AUTO_ITEMS
#   (mesure la disponibilité des données, pas la force du signal)
# - MIN_PTS_GO : points minimum côté gagnant pour un "go"
#   (S1+S2 = 4 pts max via la tendance daily seule → 6 exige d'autres confirmations)
# - MIN_PTS_DIRECTION : points minimum pour afficher une direction "possible"
MIN_PTS_GO        = 6
MIN_PTS_DIRECTION = 3

# Pivot detection: N candles each side to qualify as swing high/low
# 2 sur Daily (délai ~4 jours), 3 sur 4H/15min (délai ~12-45 bougies)
SWING_N      = 2
SWING_N_FAST = 3   # pour 4H et 15min

# Seuil minimum de liquidations (contracts) pour déclencher le bloqueur F4
# (~40 000$ à 80$/SOL) — évite les faux positifs sur des liqs négligeables
LIQ_MIN_THRESHOLD = 500
