"""Génère docs/index.html (GitHub Pages) depuis signal_log.jsonl.

Usage: python -m soltrader.dashboard
Aucune dépendance — HTML statique auto-contenu.
"""

import html
import os
from datetime import datetime, timezone

from .logger import read_log
from . import paper

DECISION_N = 30   # évaluation pré-enregistrée (research/preregistration.md)


def _num(v, fmt="{:+.2f}"):
    return fmt.format(v) if isinstance(v, (int, float)) else "—"


def _paper_section():
    stats = paper.stats_by_book()
    state = paper.load_state()
    cards = []
    for book, title in (("go", "Carnet GO — test H1"), ("possible", "Carnet POSSIBLE — exploratoire")):
        st = stats.get(book, {"n": 0})
        n = st.get("n", 0)
        pos = state.get(book)
        pos_html = (f"{'▲' if pos.direction == 'long' else '▼'} {pos.direction.upper()} "
                    f"depuis {html.escape(pos.entry_time[:16].replace('T', ' '))} · "
                    f"entrée {pos.entry:.3f} · stop {pos.stop:.3f} · objectif {pos.target:.3f}"
                    if pos else "aucune position ouverte")
        progress = f" · {n}/{DECISION_N} trades avant évaluation" if book == "go" else ""
        cards.append(
            f'<div class="pcard"><div class="ptitle">{title}</div>'
            f'<div class="pline">{n} trades clôturés{progress}</div>'
            f'<div class="pline">R net moyen {_num(st.get("avg_r"))} · win '
            f'{_num(st.get("win_rate", None) * 100 if n else None, "{:.0f}%")} · PF '
            f'{_num(st.get("profit_factor"), "{:.2f}")} · total {_num(st.get("sum_r"), "{:+.1f} R")} · '
            f'DD max {_num(st.get("max_drawdown_r"), "{:.1f} R")}</div>'
            f'<div class="pline dim">{pos_html}</div></div>')
    rows = []
    for t in reversed(paper.read_trades()[-30:]):
        rows.append(
            "<tr>"
            f"<td>{html.escape(t.get('book', '?'))}</td>"
            f"<td class='ts'>{html.escape(str(t.get('entry_time', ''))[:16].replace('T', ' '))}</td>"
            f"<td>{_dir_cell(t.get('direction'))}</td>"
            f"<td>{t.get('entry', 0):.3f} → {t.get('exit_price', 0):.3f}</td>"
            f"<td>{html.escape(str(t.get('exit_reason', '')))}</td>"
            f"<td>{_num(t.get('net_pct'), '{:+.2f}%')}</td>"
            f"<td>{_num(t.get('r_net'), '{:+.2f} R')}</td>"
            "</tr>")
    table = (f"<div class='tablewrap'><table><thead><tr><th>Carnet</th><th>Entrée</th><th>Dir.</th>"
             f"<th>Prix</th><th>Sortie</th><th>Net</th><th>R</th></tr></thead>"
             f"<tbody>{''.join(rows)}</tbody></table></div>") if rows else ""
    return (f"<h2>🧪 Paper trading</h2><div class='sub'>Trades fictifs, frais et funding inclus — "
            f"règles : research/preregistration.md</div><div class='cards'>{''.join(cards)}</div>{table}")

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
  h2 {{ font-size: 16px; margin: 28px 0 4px; }}
  .pcard {{ background: #111a2e; border: 1px solid #1e293b; border-radius: 8px; padding: 12px 16px; min-width: 300px; flex: 1; }}
  .ptitle {{ font-weight: bold; margin-bottom: 6px; }}
  .pline {{ font-size: 12px; }}
  .dim {{ color: #64748b; }}
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
{_paper_section()}
</body>
</html>
"""
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"  [DASHBOARD] {OUT_FILE} genere ({len(records)} analyses)")


if __name__ == "__main__":
    generate()
