# sol-trader — contexte projet

Analyseur de signaux de trade **SOL/USDT futures x10**. Il **ne passe aucun ordre** : il calcule
un verdict à partir d'une checklist de signaux, envoie une alerte Telegram, et tient un **paper
trading** (trades fictifs) pour mesurer la valeur réelle des signaux. Usage : filtre pour des
trades discrétionnaires, exécutés à la main sur XT.com.

## Où ça tourne

- **Production = GitHub Actions**, indépendante de tout PC. `.github/workflows/sol_signal.yml`
  tourne `17 7-21 * * *` **et** `47 7-21 * * *` (GitHub saute souvent des crons ; le run de :47
  est ignoré si une analyse a moins de 40 min, option `--min-interval`). `concurrency` = un run à la fois.
  Étapes : `python sol_analyzer.py --notify --paper`, `python -m soltrader.archive`,
  `python -m soltrader.dashboard`, puis commit `chore: update signal state [skip ci]` (signé `sol-bot`)
  de `.sol_signal_state.json`, `.macro_calendar.json`, `.paper_state.json`, `paper_trades.jsonl`,
  `signal_log.jsonl`, `docs/`, `data/`. Le job échoue si le push échoue 3 fois.
- Lancement manuel : workflow_dispatch avec l'input `force` (renvoie la notif même si inchangée).
- `tests.yml` : pytest à chaque push sur main.
- Secrets du repo : `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`.
- Dashboard : https://ethanbdrd.github.io/sol-trader/ (Pages sur `main` `/docs`).
- Repo public : https://github.com/ethanbdrd/sol-trader (compte GitHub `ethanbdrd`).

## Données

- API publique **Gate.io futures** (sans clé). Binance et Bybit bloquent les IP des runners GitHub.
  Gate.io renvoie la **période en cours** en dernier : toujours passer par `engine.closed_only`.
  Une ligne contract_stats horodatée T couvre [T, T+1h[. 1 contrat SOL_USDT = 1 SOL. Funding 8h.
- Calendrier macro Forex Factory (M1), mis en cache 6 h dans `.macro_calendar.json` (rate limit 429).
  Calendrier indisponible en live → bloqueur global `macro_inconnu`.
- **Archive** `data/*.csv` (SOL 15m/4h/1d, BTC 15m/1d, contract_stats 1h) : Gate.io ne garde que
  10 000 bougies / 180 j de stats, donc on archive à chaque run. Backfill : `python -m soltrader.archive --backfill`.

## Architecture (`soltrader/`)

| Fichier | Rôle |
|---|---|
| `config.py` | constantes, seuils du verdict, paramètres d'exécution simulée |
| `api.py` | appels Gate.io et Forex Factory (avec cache) |
| `market.py` | récupère un `MarketData` live (périodes clôturées uniquement) |
| `engine.py` | **moteur unique** `evaluate(MarketData) → Evaluation` (board + lignes + niveaux SL), sans I/O ; utilisé par le live, le backtest et le paper trading |
| `signals.py` | fonctions pures de calcul des signaux |
| `scoring.py` | `SignalBoard` : points, bloqueurs, verdict, `effective_status(status, completion, direction)` |
| `analysis.py` | live : fetch → moteur → affichage console |
| `execution.py` | simulateur d'exécution (stop R1 ± 0,3 %, objectif 2R, 72 h max, frais, glissement, funding) |
| `paper.py` | paper trading, carnets `go` et `possible` |
| `archive.py` | archive `data/*.csv` |
| `notify.py` | Telegram (texte brut, **pas de parse_mode**), state anti-spam, masquage du token |
| `logger.py` | log JSONL d'une ligne par analyse |
| `dashboard.py` | `signal_log.jsonl` + paper → `docs/index.html` |

Points d'entrée : `sol_analyzer.py` (`--notify`, `--force`, `--paper`, `--min-interval N`, `--no-log`,
`--watch N`) et `backtest.py` (rejoue l'archive heure par heure, `--days`, `--refresh`, `--out`).

Signaux : S1-S5 (structure, MA200, BOS/CHoCH), M1-M3 (macro, BTC ; M2 = marqueur non scoré),
F1-F4 (funding, OI, L/S, liquidations), C1-C3 (CVD, absorption), E1-E4 (FVG, session, bougie, POC),
R1 (SL au pivot 4H le plus proche). Checklist HTML de 27 items, dont 20 automatisés.
Statuts : `go`, `possible`, `mixed`, `incomplete`, `blocked`, `directional_blocked`.
`go` = ≥ 6 pts, ≤ 1 pt opposé, ≥ 2 pts hors S1/S2/M3, complétion ≥ 80 %.

## Recherche — règles importantes

- `research/preregistration.md` : règles v2.1 figées, hypothèse H1 (les `go` ont une espérance
  nette > 0), **critère de décision à 30 trades papier** du carnet `go`.
- **Ne jamais modifier un seuil, un poids ou une règle d'exécution sans créer une v3 du
  pré-enregistrement et remettre le compteur paper à zéro.** Ne pas relancer le backtest en variantes.
- Backtests exploratoires conservés : `research/backtest_v2.*`, `research/backtest_v2.1.*`.

## Dev local

```bash
python3.11 -m venv .venv && source .venv/bin/activate   # ou : uv venv --python 3.11 .venv
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
python sol_analyzer.py --no-log   # analyse console, sans Telegram ni log ni paper
python backtest.py                # sur l'archive data/
```

- Python 3.11 (comme en CI). Dépendances figées : modifier `requirements.in` puis
  `uv pip compile requirements.in --python-version 3.11 -o requirements.txt --no-header --annotation-style line`.
- Ne pas lancer `--paper` en local (modifierait l'état paper de production).
- **Toujours `git pull --rebase` avant de committer** : le bot pousse un commit par heure.
- Messages de commit en français, style conventionnel (`feat:`, `fix:`, `refactor:`, `chore:`).
