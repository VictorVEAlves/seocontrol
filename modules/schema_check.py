"""
Schema Markup Auditor — secretoutlet.com.br

Crawls priority pages and checks for JSON-LD structured data.
For an e-commerce, the critical schemas are:
  - Product          → enables rich snippets (price, rating, availability)
  - BreadcrumbList   → enables breadcrumb trail in SERPs
  - Organization     → brand identity
  - WebSite + sitelinks searchbox

Each missing or malformed schema is a missed rich-snippet opportunity.
"""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup

from config import PRIORITY_PAGES, SITE_URL, REQUEST_TIMEOUT

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
}

# Schema types most valuable for this site
_PRIORITY_SCHEMAS = {
    "Product":       ("Product schema", "Habilita rich snippet de preço e disponibilidade"),
    "BreadcrumbList": ("BreadcrumbList", "Mostra trilha de navegação no SERP"),
    "Organization":  ("Organization", "Identidade da marca no Knowledge Graph"),
    "WebSite":       ("WebSite", "Habilita sitelinks searchbox"),
    "ItemList":      ("ItemList", "Lista de produtos estruturada"),
    "FAQPage":       ("FAQPage", "Habilita FAQ expandido no SERP"),
}

_warmed: list[requests.Session | None] = [None]


def _get_session() -> requests.Session:
    if _warmed[0] is not None:
        return _warmed[0]
    s = requests.Session()
    s.headers.update(_BROWSER_HEADERS)
    try:
        s.get(SITE_URL + "/", timeout=12)
    except Exception:
        pass
    _warmed[0] = s
    return s


def _extract_schemas(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    schemas = []
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
            if isinstance(data, list):
                schemas.extend(data)
            elif isinstance(data, dict):
                schemas.append(data)
        except (json.JSONDecodeError, TypeError):
            pass
    return schemas


def _audit_url(path: str) -> dict:
    url = path if path.startswith("http") else SITE_URL + path
    session = _get_session()

    result: dict = {
        "url":      url,
        "path":     path,
        "schemas":  [],
        "missing":  [],
        "issues":   [],
        "score":    0,
        "blocked":  False,
        "error":    "",
    }

    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if resp.status_code in (403, 429, 503):
            result["blocked"] = True
            return result
        resp.raise_for_status()
    except Exception as exc:
        result["error"] = str(exc)
        return result

    schemas = _extract_schemas(resp.text)
    schema_types = {s.get("@type", "") for s in schemas if isinstance(s, dict)}

    # Flatten nested @graph blocks
    for s in list(schemas):
        if isinstance(s, dict) and "@graph" in s:
            for item in s["@graph"]:
                if isinstance(item, dict):
                    schema_types.add(item.get("@type", ""))

    result["schemas"] = sorted(schema_types)

    # Determine page type from path to know which schemas to expect
    is_category = any(kw in path for kw in [
        "/tenis", "/polos", "/camisetas", "/camisas", "/calcas",
        "/bermudas", "/blusas", "/bones", "/carteiras", "/chinelos",
        "/jaquetas", "/moletons",
    ])
    is_brand_pillar = not is_category and path.count("/") == 1 and len(path) > 2
    is_homepage = path in ("/", "")

    # Expected schemas by page type
    expected: list[str] = ["BreadcrumbList"]
    if is_category or is_brand_pillar:
        expected += ["ItemList"]
    if is_homepage:
        expected += ["Organization", "WebSite"]

    for schema_type in expected:
        if schema_type not in schema_types:
            label, detail = _PRIORITY_SCHEMAS.get(schema_type, (schema_type, ""))
            result["missing"].append({"type": schema_type, "label": label, "detail": detail})
            result["issues"].append(f"Schema ausente: {label}")

    # Validate Product schemas if present (has required fields?)
    if "Product" in schema_types:
        for s in schemas:
            if isinstance(s, dict) and s.get("@type") == "Product":
                for field in ("name", "image", "offers"):
                    if not s.get(field):
                        result["issues"].append(f"Product schema sem campo '{field}'")

    # Score: 100 minus 15 per missing critical schema, minus 10 per validation issue
    deductions = len(result["missing"]) * 15 + len([i for i in result["issues"] if "sem campo" in i]) * 10
    result["score"] = max(0, 100 - deductions)

    return result


def run(urls: list[str] | None = None, max_workers: int = 5) -> dict:
    pages = urls or PRIORITY_PAGES[:40]

    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_audit_url, p): p for p in pages}
        for fut in as_completed(futures):
            try:
                rows.append(fut.result())
            except Exception as exc:
                rows.append({"path": futures[fut], "error": str(exc), "score": 0, "missing": [], "issues": []})

    rows.sort(key=lambda r: r.get("score", 100))

    pages_with_issues = [r for r in rows if r.get("missing") or r.get("issues")]
    missing_breadcrumb = [r for r in rows if any(m["type"] == "BreadcrumbList" for m in r.get("missing", []))]
    missing_itemlist   = [r for r in rows if any(m["type"] == "ItemList"      for m in r.get("missing", []))]
    blocked            = [r for r in rows if r.get("blocked")]

    return {
        "pages":               rows,
        "total":               len(rows),
        "pages_with_issues":   len(pages_with_issues),
        "missing_breadcrumb":  len(missing_breadcrumb),
        "missing_itemlist":    len(missing_itemlist),
        "blocked":             len(blocked),
        "avg_score":           round(sum(r.get("score", 0) for r in rows) / len(rows), 1) if rows else 0,
    }
