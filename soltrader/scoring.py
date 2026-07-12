"""Moteur de scoring directionnel — SignalBoard."""

from .config import AUTO_ITEMS, MIN_PTS_GO, MIN_PTS_DIRECTION


class SignalBoard:
    def __init__(self):
        self.signals  = []  # (name, direction, weight, detail)
        self.blockers = []  # (name, reason, blocks_direction)
        # blocks_direction: None = bloque tout, 'long' = bloque seulement long,
        #                   'short' = bloque seulement short

    def add(self, name, direction, weight=1, detail=""):
        """
        direction: 'long' / 'short' (signal directionnel scoré),
        un marqueur neutre ('neutral', 'ranging', 'safe', ...) = évalué sans direction,
        ou None = données manquantes (item non répondu).
        """
        self.signals.append((name, direction, weight, detail))

    def block(self, name, reason="", direction=None):
        """direction=None bloque tout. 'long' ou 'short' bloque seulement ce sens."""
        self.blockers.append((name, reason, direction))

    @property
    def is_blocked(self):
        """True si au moins un bloqueur global (direction=None)."""
        return any(d is None for _, _, d in self.blockers)

    def is_blocked_for(self, trade_dir):
        """True si ce sens de trade est bloqué (bloqueur global ou directionnel)."""
        for _, _, d in self.blockers:
            if d is None or d == trade_dir:
                return True
        return False

    def directional_blockers(self):
        """Retourne les bloqueurs directionnels (long ou short uniquement)."""
        return [(n, r, d) for n, r, d in self.blockers if d is not None]

    def global_blockers(self):
        """Retourne les bloqueurs globaux (bloquent tout)."""
        return [(n, r) for n, r, d in self.blockers if d is None]

    def score(self):
        long_pts  = sum(w for _, d, w, _ in self.signals if d == "long")
        short_pts = sum(w for _, d, w, _ in self.signals if d == "short")
        total     = long_pts + short_pts
        # answered = item évalué avec données disponibles : direction long/short
        # OU marqueur neutre explicite ("ranging", "neutral", "safe", ...).
        # None = données manquantes (API down, calcul impossible).
        answered = sum(1 for _, d, _, _ in self.signals if d is not None)
        # completion = disponibilité des données sur les items automatisés
        completion = answered / AUTO_ITEMS if AUTO_ITEMS else 0

        if total == 0:
            return "wait", None, 0, 0, completion
        ratio = long_pts / total
        if ratio >= 0.65:
            direction = "long"
        elif ratio <= 0.35:
            direction = "short"
        else:
            direction = None

        if completion < 0.60:
            return "incomplete", direction, long_pts, short_pts, completion
        if direction is None:
            return "mixed", None, long_pts, short_pts, completion
        win_pts = long_pts if direction == "long" else short_pts
        if win_pts < MIN_PTS_DIRECTION:
            # Direction issue de trop peu de signaux actifs → pas exploitable
            return "mixed", None, long_pts, short_pts, completion
        if completion >= 0.80 and win_pts >= MIN_PTS_GO:
            return "go", direction, long_pts, short_pts, completion
        return "possible", direction, long_pts, short_pts, completion

    def effective_status(self, status, completion):
        """Statut effectif pour la notif : blocked > directional_blocked > score."""
        if self.is_blocked:
            return "blocked"
        if self.directional_blockers() and completion >= 0.55:
            return "directional_blocked"
        return status
