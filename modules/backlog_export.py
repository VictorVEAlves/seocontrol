import csv
import html
from datetime import datetime
from pathlib import Path

from config import REPORTS_FOLDER


FIELDS = [
    "priority",
    "source",
    "owner",
    "action",
    "target",
    "reason",
    "impact",
    "confidence",
    "effort",
]


def export_csv(items: list, output_path: str | None = None) -> str:
    path = Path(output_path) if output_path else _default_path("csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for item in items:
            writer.writerow({field: item.get(field, "") for field in FIELDS})
    return str(path)


def export_html(items: list, output_path: str | None = None) -> str:
    path = Path(output_path) if output_path else _default_path("html")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = "\n".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('priority', '')))}</td>"
        f"<td>{html.escape(str(item.get('source', '')))}</td>"
        f"<td>{html.escape(str(item.get('owner', '')))}</td>"
        f"<td>{html.escape(str(item.get('action', '')))}</td>"
        f"<td>{html.escape(str(item.get('target', '')))}</td>"
        f"<td>{html.escape(str(item.get('reason', '')))}</td>"
        f"<td>{html.escape(str(item.get('impact', '')))}</td>"
        f"<td>{html.escape(str(item.get('confidence', '')))}</td>"
        f"<td>{html.escape(str(item.get('effort', '')))}</td>"
        "</tr>"
        for item in items
    )
    path.write_text(f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Backlog SEO Priorizado</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #1f2933; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
    th, td {{ border-bottom: 1px solid #e5e7eb; padding: 9px; text-align: left; vertical-align: top; }}
    th {{ background: #f8fafc; position: sticky; top: 0; }}
  </style>
</head>
<body>
  <h1>Backlog SEO Priorizado</h1>
  <p>Gerado em {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
  <table>
    <thead><tr>{''.join(f'<th>{html.escape(field)}</th>' for field in FIELDS)}</tr></thead>
    <tbody>{rows or '<tr><td colspan="9">Nenhum item encontrado.</td></tr>'}</tbody>
  </table>
</body>
</html>
""", encoding="utf-8")
    return str(path)


def export_all(items: list) -> dict:
    return {
        "csv": export_csv(items),
        "html": export_html(items),
    }


def _default_path(ext: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M")
    return Path(REPORTS_FOLDER) / f"seo_backlog_{timestamp}.{ext}"
