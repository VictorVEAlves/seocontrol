"""
Operational memory for SEO changes already made on the site.

Reads the manual control CSV and uses it to avoid regenerating backlog items for
URLs/queries that were already implemented.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from config import BASE_DIR


DEFAULT_CHANGELOG_CANDIDATES = [
    BASE_DIR / "controle_seo_dashboard.xlsx - Controle SEO.csv",
    Path.home() / "Downloads" / "controle_seo_dashboard.xlsx - Controle SEO.csv",
]

DONE_STATUSES = {
    "feito",
    "implementado",
    "publicado",
    "concluido",
    "concluido",
    "done",
    "completed",
    "aplicado",
}

SOFT_STATUSES = {
    "em andamento",
    "andamento",
    "planejado",
    "pendente",
    "pending",
    "todo",
}

SUPPRESSIBLE_SOURCES = {
    "gsc",
    "onpage",
    "snippets",
    "content_gap",
    "products",
    "link_suggestions",
}


@dataclass(frozen=True)
class ChangeRecord:
    date: str
    url: str
    path: str
    change_type: str
    element: str
    before: str
    after: str
    status: str
    notes: str
    done: bool
    soft: bool


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    value = _strip_accents(value).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_path(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("http"):
        parsed = urlparse(raw)
        path = parsed.path or "/"
    else:
        path = raw
    path = "/" + path.strip("/")
    return "/" if path == "/" else path.rstrip("/")


def slug_from_query(value: str) -> str:
    text = normalize_text(value)
    return "/" + "-".join(text.split()) if text else ""


def _term_set(value: str) -> set[str]:
    terms = set()
    for term in normalize_text(value).split():
        terms.add(term)
        if len(term) > 4 and term.endswith("s"):
            terms.add(term[:-1])
    return terms


def _row_get(row: dict, *names: str) -> str:
    normalized = {normalize_text(k): v for k, v in row.items()}
    for name in names:
        value = normalized.get(normalize_text(name))
        if value is not None:
            return str(value or "").strip()
    return ""


def _parse_date(value: str) -> datetime:
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(str(value or "").strip(), fmt)
        except ValueError:
            continue
    return datetime.min


def find_changelog_path(path: str | os.PathLike | None = None) -> Path | None:
    if path:
        candidate = Path(path)
        return candidate if candidate.exists() else None

    env_path = os.environ.get("SEO_CHANGELOG_CSV", "").strip()
    if env_path:
        candidate = Path(env_path)
        if candidate.exists():
            return candidate

    for candidate in DEFAULT_CHANGELOG_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def load_records(path: str | os.PathLike | None = None) -> list[ChangeRecord]:
    csv_path = find_changelog_path(path)
    if not csv_path:
        return []

    rows = []
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            with csv_path.open("r", encoding=encoding, newline="") as fh:
                rows = list(csv.DictReader(fh))
            break
        except UnicodeDecodeError:
            continue

    records = []
    for row in rows:
        status = _row_get(row, "Status")
        status_norm = normalize_text(status)
        url = _row_get(row, "URL")
        records.append(ChangeRecord(
            date=_row_get(row, "Data"),
            url=url,
            path=normalize_path(url),
            change_type=_row_get(row, "Tipo de Mudanca", "Tipo de Mudança"),
            element=_row_get(row, "Elemento Alterado"),
            before=_row_get(row, "Antes"),
            after=_row_get(row, "Depois"),
            status=status,
            notes=_row_get(row, "Observacoes", "Observações"),
            done=status_norm in DONE_STATUSES,
            soft=status_norm in SOFT_STATUSES,
        ))

    return sorted(records, key=lambda item: _parse_date(item.date), reverse=True)


def _target_candidates(target: str) -> set[str]:
    target = str(target or "").strip()
    candidates = {normalize_path(target)}
    if not target.startswith(("http", "/")):
        candidates.add(slug_from_query(target))
    return {c for c in candidates if c}


def _record_matches_target(record: ChangeRecord, target: str) -> bool:
    candidates = _target_candidates(target)
    if record.path in candidates:
        return True

    query_slug = slug_from_query(target)
    if query_slug and record.path.endswith(query_slug):
        return True

    target_terms = _term_set(target)
    path_terms = _term_set(record.path)
    if len(target_terms) >= 2 and target_terms.issubset(path_terms):
        return True
    if len(path_terms) >= 2 and path_terms.issubset(target_terms):
        return True
    return False


def match_record(target: str, records: list[ChangeRecord] | None = None) -> ChangeRecord | None:
    records = records if records is not None else load_records()
    for record in records:
        if record.done and _record_matches_target(record, target):
            return record
    return None


def should_suppress_item(item: dict, records: list[ChangeRecord] | None = None) -> tuple[bool, ChangeRecord | None]:
    source = str(item.get("source") or "").lower()
    if source not in SUPPRESSIBLE_SOURCES:
        return False, None
    record = match_record(str(item.get("target") or ""), records)
    return bool(record), record


def filter_backlog_items(items: list[dict], records: list[ChangeRecord] | None = None) -> tuple[list[dict], list[dict]]:
    records = records if records is not None else load_records()
    kept = []
    suppressed = []
    for item in items:
        suppress, record = should_suppress_item(item, records)
        if suppress and record:
            skipped = dict(item)
            skipped["suppressed_by_change"] = {
                "date": record.date,
                "url": record.url,
                "status": record.status,
                "element": record.element,
                "notes": record.notes,
            }
            suppressed.append(skipped)
            continue
        kept.append(item)
    return kept, suppressed


def summary(path: str | os.PathLike | None = None) -> dict:
    records = load_records(path)
    done = [record for record in records if record.done]
    return {
        "path": str(find_changelog_path(path) or ""),
        "total_records": len(records),
        "done_records": len(done),
        "latest_done": [
            {
                "date": record.date,
                "path": record.path,
                "change_type": record.change_type,
                "element": record.element,
                "status": record.status,
            }
            for record in done[:10]
        ],
    }
