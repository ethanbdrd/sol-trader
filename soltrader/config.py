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

# Cache du calendrier Forex Factory (évite le rate limit HTTP 429)
MACRO_CACHE = ".macro_calendar.json"

# Paper trading : positions ouvertes + historique des trades clôturés
PAPER_STATE = ".paper_state.json"
PAPER_LOG   = "paper_trades.jsonl"

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

# Règle "go" durcie (2026-11-09, fixée AVANT tout calcul de rendement) :
# un go exige un consensus net, pas seulement une majorité.
# - MAX_OPP_PTS_GO : points maximum tolérés dans le sens opposé
# - MIN_CONFIRM_GO : points minimum venant de signaux HORS tendance de fond
#   (REGIME_SIGNALS = tendance daily SOL + biais BTC, lents et corrélés entre eux)
MAX_OPP_PTS_GO = 1
MIN_CONFIRM_GO = 2
REGIME_SIGNALS = frozenset({"S1_daily_structure", "S2_ma200", "M3_btc_bias"})

# Pivot detection: N candles each side to qualify as swing high/low
# 2 sur Daily (délai ~4 jours), 3 sur 4H/15min (délai ~12-45 bougies)
SWING_N      = 2
SWING_N_FAST = 3   # pour 4H et 15min

# Seuil minimum de liquidations (contracts) pour déclencher le bloqueur F4
# (~40 000$ à 80$/SOL) — évite les faux positifs sur des liqs négligeables
LIQ_MIN_THRESHOLD = 500

# ─────────────────────────────────────────────
# Exécution simulée (backtest + paper trading) — pré-enregistré le 2026-11-09
# ─────────────────────────────────────────────
LEVERAGE     = 10
FEE_TAKER    = 0.00075   # 0,075 % par côté (taker Gate.io SOL_USDT, vérifié via /contracts)
SLIPPAGE     = 0.0002    # 0,02 % par côté (hypothèse prudente, SOL perp très liquide)
SL_BUFFER    = 0.003     # stop placé 0,3 % au-delà du pivot (consigne R1)
RR_TARGET    = 2.0       # objectif à 2R (R:R minimum 1:2 de la checklist)
MIN_STOP_PCT = 1.0       # stop plus serré = bruit (cf. bloqueur sl_trop_serre)
MAX_STOP_PCT = 6.0       # au-delà, trop proche de la liquidation x10 (~ -9,5 %)
MAX_HOLD_H   = 72        # sortie forcée après 72 h
