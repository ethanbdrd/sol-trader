"""Log JSONL de chaque analyse — pour analyse a posteriori des signaux."""

import json

from .config import LOG_FILE
from .display import Y, RST


def build_record(now_str, price, status, effective_status, direction,
                 long_pts, short_pts, completion, board, notified=False):
    """Construit l'enregistrement d'une analyse (une ligne du JSONL)."""
    return {
        "ts":               now_str,
        "price":            price,
        "status":           effective_status,
        "score_status":     status,
        "direction":        direction,
        "long_pts":         long_pts,
        "short_pts":        short_pts,
        "completion":       round(completion, 3),
        "blockers":         [{"name": n, "direction": d} for n, _, d in board.blockers],
        "signals":          {n: d for n, d, _, _ in board.signals},
        "notified":         bool(notified),
    }


def append_log(record, path=LOG_FILE):
    """Ajoute une ligne au log JSONL (append-only)."""
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        print(Y + f"  [LOG] Impossible d'écrire {path}: {e}" + RST)


def read_log(path=LOG_FILE, limit=None):
    """Lit le log JSONL (lignes invalides ignorées). Renvoie les `limit` derniers."""
    records = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except FileNotFoundError:
        return []
    return records[-limit:] if limit else records
