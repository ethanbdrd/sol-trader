# sol-trader — contexte projet

Analyseur de signaux de trade **SOL/USDT futures x10**. Il **ne passe aucun ordre** : il calcule
un verdict à partir d'une checklist de signaux et envoie une alerte Telegram.

## Où ça tourne

- **Production = GitHub Actions**, indépendante de tout PC. Le workflow `.github/workflows/sol_signal.yml`
  tourne `17 7-21 * * *` (toutes les heures de 07h à 21h UTC, à la minute 17). Il lance
  `python sol_analyzer.py --notify`, régénère le dashboard, puis commite `.sol_signal_state.json`,
  `signal_log.jsonl` et `docs/` (commits `chore: update signal state [skip ci]` signés `sol-bot`).
- Lancement manuel : workflow_dispatch avec l'input `force` (pour renvoyer la notif même si le signal n'a pas changé).
- `tests.yml` : pytest à chaque push sur main.
- Secrets du repo : `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- Dashboard : https://ethanbdrd.github.io/sol-trader/ (Pages sur `main` `/docs`).
- Repo : https://github.com/ethanbdrd/sol-trader (compte GitHub `ethanbdrd`).

## Données

- API publique **Gate.io futures** (sans clé). Binance et Bybit bloquent les IP des runners GitHub.
- Calendrier macro Forex Factory (signal M1).

## Architecture (`soltrader/`)

| Fichier | Rôle |
|---|---|
| `config.py` | constantes et seuils (`MIN_PTS_GO=6`, `MIN_PTS_DIRECTION=3`, `SWING_N`, `LIQ_MIN_THRESHOLD`…) |
| `api.py` | appels Gate.io et Forex Factory |
| `signals.py` | fonctions pures de calcul des signaux (testées) |
| `scoring.py` | `SignalBoard` : points long/short, bloqueurs (globaux ou directionnels), verdict |
| `analysis.py` | orchestration : fetch → signaux → board → affichage |
| `notify.py` | Telegram (texte brut, **pas de parse_mode**) et state anti-spam |
| `logger.py` | log JSONL d'une ligne par analyse |
| `dashboard.py` | `python -m soltrader.dashboard` : `signal_log.jsonl` → `docs/index.html` |

Points d'entrée : `sol_analyzer.py` (`--notify`, `--force`, `--watch N`, `--verbose`) et `backtest.py`
(`--days`, `--step` ; rejeu sans lookahead sur environ 13 jours max).

Signaux : S1-S5 (structure, MA200, BOS/CHoCH), M1-M3 (macro, BTC), F1-F4 (funding, OI, L/S,
liquidations), C1-C3 (CVD, absorption), E1-E4 (FVG/OB, session, bougie, POC), R1 (SL suggéré).
Checklist HTML de 27 items, dont 20 automatisés.
Statuts : `go`, `possible`, `mixed`, `blocked`, `directional_blocked`.

## Dev local

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python sol_analyzer.py            # analyse console, sans Telegram
```

- Python 3.11 (comme en CI).
- **Toujours `git pull --rebase` avant de committer** : le bot pousse un commit par heure.
- Messages de commit en français, style conventionnel (`feat:`, `fix:`, `refactor:`, `chore:`).
