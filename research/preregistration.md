# Pré-enregistrement — sol-trader, règles v2

Rédigé le **2026-11-09**, avant tout calcul de rendement avec les règles v2.
Le commit qui introduit ce fichier fait foi : toute modification ultérieure
des règles ci-dessous exige une **nouvelle version** de ce document (v3…),
et remet le compteur de paper trading à zéro.

## Ce qu'on teste

**H1 — Les « go » ont une espérance nette positive.** Les verdicts `go`
effectifs (statut après bloqueurs) du moteur v2, exécutés mécaniquement avec
les règles d'exécution ci-dessous, ont un résultat net moyen > 0 R par trade,
après frais, glissement et funding.

Le bot sert de filtre à des trades discrétionnaires. H1 ne mesure donc pas
ton résultat réel, mais la valeur du signal brut. C'est la question
préalable : si le signal mécanique perd, le filtre n'apporte rien de mesurable.

## Règles du signal (v2)

Code de référence : `soltrader/engine.py`, `soltrader/scoring.py`,
`soltrader/config.py` au commit de ce fichier.

- 20 items automatiques, poids 2 pour S1 et S2, 1 pour les autres.
  M2 n'est plus directionnel (BTC compté une fois via M3).
- Direction : ratio de points ≥ 0,65 (long) ou ≤ 0,35 (short).
- `go` : complétion ≥ 80 %, ≥ 6 pts dans le sens, ≤ 1 pt dans le sens
  opposé, ≥ 2 pts de confirmation hors S1/S2/M3.
- Statut effectif : bloqueur global → `blocked` ; bloqueur visant le sens
  proposé → `directional_blocked` ; sinon le statut du score.
- Uniquement des périodes clôturées ; panne de données = item non répondu.

## Règles d'exécution

Code de référence : `soltrader/execution.py`.

| Paramètre | Valeur |
|---|---|
| Entrée | prix au moment du verdict, taker |
| Stop | pivot 4H de R1 le plus **proche** du prix (creux le plus haut sous le prix pour un long, sommet le plus bas au-dessus pour un short) ± 0,3 % — voir amendement v2.1 |
| Refus | stop < 1 % ou > 6 % du prix, ou pas de pivot |
| Objectif | 2R |
| Sortie forcée | 72 h |
| Stop et objectif dans la même bougie 15 min | stop |
| Frais | 0,075 % par côté (taker Gate.io) |
| Glissement | 0,02 % par côté |
| Funding | taux réellement versés aux échéances 00/08/16 UTC |
| Positions | une seule à la fois ; un `go` pendant une position ouverte est ignoré |
| Levier | x10 (sans effet sur le résultat en R ; liquidation ~ −9,5 %, jamais atteinte avec un stop ≤ 6 %) |

## Deux jeux de données, deux statuts

1. **Backtest rétrospectif** (`backtest.py`, archive `data/`, ~2026-07-29 →
   2026-11-09, un verdict par heure à HH:15). **Exploratoire**, pour deux
   raisons : la règle `go` v2 s'inspire de l'expérience accumulée en tradant
   pendant cette période, et le calendrier macro n'est pas rejouable (M1
   absent, donc plus de trades qu'en live). Il sert à vérifier que le
   système fonctionne et à mesurer les coûts, pas à conclure.
2. **Paper trading en avant** (à partir du déploiement de ce commit), même
   moteur, mêmes règles, données jamais vues. **Confirmatoire.**

## Critères de décision (paper trading)

Métrique principale : **R net moyen par trade**. Secondaires : win rate,
profit factor, drawdown max en R, coût moyen par trade.

Évaluation à **30 trades clôturés** :

- R net moyen ≥ +0,15 **et** profit factor ≥ 1,2 → feu vert pour suivre les
  `go` en réel, risque limité à 0,5 % du capital par trade (taille de
  position = 0,5 % du capital ÷ distance au stop).
- R net moyen ≤ 0 → H1 rejetée : les `go` v2 n'ont pas d'avantage. Toute
  nouvelle règle passe par un nouveau pré-enregistrement.
- Entre les deux → on continue jusqu'à 60 trades, puis on applique les mêmes
  seuils.

Hors critères (exploratoire, rapporté mais sans décision) : les trades
simulés sur les statuts `possible`, et toute ventilation par signal.

## Ce qui est interdit

- Changer un seuil, un poids ou une règle d'exécution en regardant les
  résultats du paper trading sans créer une v3 et repartir de zéro.
- Relancer le backtest avec des variantes et ne garder que la meilleure.

## Amendement v2.1 — 2026-11-09, avant le premier trade papier

**Changement** : le stop utilise le pivot 4H le plus *proche* du prix au lieu
du plus *récent*. Ce correctif figurait dans l'audit (« SL suggéré parfois
incohérent ») **avant** le backtest v2, mais il a été appliqué **après**
avoir vu ce backtest. Pour ne pas choisir la variante a posteriori :

- les deux backtests sont conservés tels quels (`research/backtest_v2.*`
  avec l'ancienne règle, `research/backtest_v2.1.*` avec la nouvelle) ;
- la règle v2.1 est retenue **quel que soit** son résultat ;
- le paper trading démarre avec v2.1 ; son compteur part de zéro.

## Erratum données — 2026-11-09

Le backfill initial de `data/stats_1h.csv` contenait **1 703 heures en double**
(18/08 → 28/10 : pages Gate.io qui se chevauchent, non dédoublonnées quand
l'archive était vide). Les fichiers `backtest_v2.*` et `backtest_v2.1.*` ont
été calculés sur ces données faussées et sont **invalides** (conservés pour
traçabilité). Bug corrigé (`archive.merge_series`), archive dédoublonnée,
les deux règles relancées une fois sur données propres, sans autre changement :

| Fichier | Règle de stop | `go` (H1) | `possible` (exploratoire) |
|---|---|---|---|
| `backtest_v2_corrige.*` | pivot le plus récent | 13 trades, win 31 %, −0,25 R, PF 0,58 | 36 trades, win 53 %, +0,12 R, PF 1,27 |
| `backtest_v2.1_corrige.*` | pivot le plus proche (retenue) | 15 trades, win 20 %, −0,49 R, PF 0,36 | 39 trades, win 46 %, +0,06 R, PF 1,11 |

Lecture : sur ces ~3 mois, rien n'indique que les `go` aient un avantage.
L'échantillon reste trop petit pour conclure ; la décision revient au
paper trading, selon les critères ci-dessus.
