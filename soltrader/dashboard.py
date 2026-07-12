"""Génère docs/index.html (GitHub Pages) depuis signal_log.jsonl.

Usage: python -m soltrader.dashboard
Aucune dépendance — HTML statique auto-contenu.
"""

import html
import os
from datetime import datetime, timezone

from .logger import read_log

OUT_DIR  = "docs"
OUT_FILE = os.path.join(OUT_DIR, "index.html")

STATUS_META = {
    "go":                  ("GO",          "#22c55e"),
    "possible":            ("POSSIBLE",    "#eab308"),
    "blocked":             ("BLOQUÉ",      "#ef4444"),
    "directional_blocked": ("BLOQ. DIR.",  "#f97316"),
    "mixed":               ("MIXTE",       "#a855f7"),
    "incomplete":          ("INCOMPLET",   "#64748b"),
    "wait":                ("ATTENTE",     "#64748b"),
}


def _badge(status):
    label, color = STATUS_META.get(status, (status or "?", "#64748b"))
    return (f'<span class="badge" style="background:{color}1a;color:{color};'
            f'border:1px solid {color}66">{html.escape(label)}</span>')


def _dir_cell(direction):
    if direction == "long":
        return '<span style="color:#22c55e">▲ LONG</span>'
    if direction == "short":
        return '<span style="color:#ef4444">▼ SHORT</span>'
    return '<span style="color:#64748b">—</span>'


def generate(limit=200):
    records = read_log(limit=limit)
    records = list(reversed(records))  # plus récent en premier
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    counts = {}
    for r in records:
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1

    stat_cards = "".join(
        f'<div class="card"><div class="num" style="color:{STATUS_META.get(s, ("", "#64748b"))[1]}">{n}</div>'
        f'<div class="lbl">{html.escape(STATUS_META.get(s, (s, ""))[0])}</div></div>'
        for s, n in sorted(counts.items(), key=lambda kv: -kv[1])
    )

    rows_html = []
    for r in records:
        blockers = ", ".join(
            f"{b.get('name', '?')}{' [' + b['direction'] + ']' if b.get('direction') else ''}"
            for b in r.get("blockers", [])
        ) or "—"
        notif = "📨" if r.get("notified") else ""
        rows_html.append(
            "<tr>"
            f"<td class='ts'>{html.escape(str(r.get('ts', '?')))}</td>"
            f"<td>{r.get('price', 0):.2f}</td>"
            f"<td>{_badge(r.get('status'))}</td>"
            f"<td>{_dir_cell(r.get('direction'))}</td>"
            f"<td>{r.get('long_pts', 0)} / {r.get('short_pts', 0)}</td>"
            f"<td>{r.get('completion', 0)*100:.0f}%</td>"
            f"<td class='blockers'>{html.escape(blockers)}</td>"
            f"<td>{notif}</td>"
            "</tr>"
        )

    page = f"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SOL Trader — Historique des signaux</title>
<style>
  :root {{ color-scheme: dark; }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: #0b1220; color: #e2e8f0; font: 14px/1.5 ui-monospace, "Cascadia Code", Consolas, monospace; padding: 24px; }}
  h1 {{ font-size: 20px; margin-bottom: 4px; }}
  .sub {{ color: #64748b; margin-bottom: 20px; }}
  .cards {{ display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: #111a2e; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 20px; text-align: center; }}
  .card .num {{ font-size: 24px; font-weight: bold; }}
  .card .lbl {{ font-size: 11px; color: #64748b; letter-spacing: 0.5px; }}
  .tablewrap {{ overflow-x: auto; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th {{ text-align: left; color: #64748b; font-size: 11px; letter-spacing: 0.5px; text-transform: uppercase;
       padding: 8px 12px; border-bottom: 1px solid #1e293b; }}
  td {{ padding: 8px 12px; border-bottom: 1px solid #16213a; white-space: nowrap; }}
  tr:hover td {{ background: #111a2e; }}
  .badge {{ padding: 2px 8px; border-radius: 99px; font-size: 11px; font-weight: bold; }}
  .ts {{ color: #94a3b8; }}
  .blockers {{ color: #94a3b8; font-size: 12px; max-width: 420px; overflow: hidden; text-overflow: ellipsis; }}
  .empty {{ color: #64748b; padding: 40px; text-align: center; }}
</style>
</head>
<body>
<h1>📡 SOL Trader — Historique des signaux</h1>
<div class="sub">Généré le {now} · {len(records)} analyses · SOL/USDT x10 · les 7 items manuels restent à valider avant tout trade</div>
<div class="cards">{stat_cards or ''}</div>
<div class="tablewrap">
<table>
<thead><tr><th>Date</th><th>Prix</th><th>Statut</th><th>Direction</th><th>L / S pts</th><th>Données</th><th>Bloqueurs</th><th>Notif</th></tr></thead>
<tbody>{''.join(rows_html) or '<tr><td colspan="8" class="empty">Aucune analyse loggée pour le moment.</td></tr>'}</tbody>
</table>
</div>
</body>
</html>
"""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"  [DASHBOARD] {OUT_FILE} genere ({len(records)} analyses)")


if __name__ == "__main__":
    generate()
