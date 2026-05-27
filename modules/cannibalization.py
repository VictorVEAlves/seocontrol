"""
Keyword Cannibalization Detector — secretoutlet.com.br

Uses the GSC API to find keywords where multiple pages compete for the same
query. This is common in brand stores: /lacoste, /tenis-lacoste and
/polos-lacoste may all rank for "lacoste", splitting impressions and clicks.

Strategy:
  1. Fetch top queries (by impressions) with page breakdown
  2. For each query, count how many pages appear
  3. Flag queries where 2+ pages share the same query (cannibalization)
  4. Rank by total impressions (highest impact first)
"""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, timedelta
from urllib.parse import quote, urlparse

from config import BASE_DIR, BRAND_CLUSTERS, SITE_URL, disable_broken_local_proxy

disable_broken_local_proxy()

_GSC_PROPERTY     = os.environ.get("GSC_PROPERTY_URL", SITE_URL + "/")
_CREDENTIALS_FILE = BASE_DIR / "gsc_credentials.json"
_TOKEN_FILE       = BASE_DIR / ".gsc_token.json"
_SCOPES           = ["https://www.googleapis.com/auth/webmasters.readonly"]

_GSC_QUERY_URL = (
    "https://searchconsole.googleapis.com/webmasters/v3/sites/"
    + quote(_GSC_PROPERTY, safe="")
    + "/searchAnalytics/query"
)

# Brand page index for classification
_BRAND_PAGE_INDEX: dict[str, dict] = {}
for _brand, _cluster in BRAND_CLUSTERS.items():
    _tier = _cluster.get("tier", "")
    for _p in [_cluster["pillar"]] + _cluster.get("pages", []) + _cluster.get("blog", []):
        if _p:
            _BRAND_PAGE_INDEX[_p.rstrip("/")] = {"brand": _brand, "tier": _tier}


def _build_session():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import google.auth.transport.requests
    import requests as _req

    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)

    creds = None
    if _TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(_TOKEN_FILE), _SCOPES)
        except Exception:
            pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            session = _req.Session()
            session.trust_env = False
            creds.refresh(google.auth.transport.requests.Request(session=session))
        else:
            if not _CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    f"Arquivo {_CREDENTIALS_FILE.name} não encontrado.\n"
                    "Download em: console.cloud.google.com → APIs → Credenciais"
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(_CREDENTIALS_FILE), _SCOPES)
            creds = flow.run_local_server(port=0)
        _TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    authed = google.auth.transport.requests.AuthorizedSession(creds)
    authed.trust_env = False
    return authed


def _fetch_rows(session, start_date: str, end_date: str, dimensions: list,
                row_limit: int = 5000) -> list:
    body = {
        "startDate": start_date, "endDate": end_date,
        "dimensions": dimensions, "rowLimit": row_limit,
    }
    resp = session.post(_GSC_QUERY_URL, json=body, timeout=45)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _path(url: str) -> str:
    p = urlparse(url).path
    return p.rstrip("/") or "/"


def run(scope_urls: list[str] | None = None, lookback_days: int = 28) -> dict:
    session = _build_session()

    today      = date.today()
    end_date   = str(today - timedelta(days=1))
    start_date = str(today - timedelta(days=lookback_days))

    # Fetch query+page breakdown in one shot (GSC supports both dimensions together)
    rows = _fetch_rows(session, start_date, end_date, ["query", "page"])

    # Group by query → list of pages
    query_pages: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        query = row["keys"][0]
        page  = row["keys"][1]
        path  = _path(page)

        # If scope_urls provided, only include rows where the page matches
        if scope_urls and path not in [u.rstrip("/") for u in scope_urls]:
            continue

        query_pages[query].append({
            "page":        path,
            "clicks":      row.get("clicks", 0),
            "impressions": row.get("impressions", 0),
            "position":    round(row.get("position", 0), 1),
            "ctr":         round(row.get("ctr", 0) * 100, 2),
            "brand":       _BRAND_PAGE_INDEX.get(path, {}).get("brand", ""),
            "tier":        _BRAND_PAGE_INDEX.get(path, {}).get("tier", ""),
        })

    # Find cannibalized queries (2+ pages for the same query)
    cannibalized: list[dict] = []
    for query, pages in query_pages.items():
        if len(pages) < 2:
            continue

        total_impressions = sum(p["impressions"] for p in pages)
        total_clicks      = sum(p["clicks"] for p in pages)

        # Sort pages by impressions descending — dominant page first
        pages_sorted = sorted(pages, key=lambda p: p["impressions"], reverse=True)
        dominant     = pages_sorted[0]
        competing    = pages_sorted[1:]

        # Severity: based on impressions split (higher = more fragmented)
        max_share = dominant["impressions"] / total_impressions if total_impressions else 1
        severity  = "high" if max_share < 0.6 else "medium" if max_share < 0.8 else "low"

        cannibalized.append({
            "query":            query,
            "pages":            pages_sorted,
            "dominant_page":    dominant["page"],
            "competing_pages":  [p["page"] for p in competing],
            "total_impressions": total_impressions,
            "total_clicks":     total_clicks,
            "page_count":       len(pages),
            "dominant_share":   round(max_share * 100, 1),
            "severity":         severity,
        })

    # Sort by impact: high severity first, then by total impressions
    severity_order = {"high": 0, "medium": 1, "low": 2}
    cannibalized.sort(key=lambda x: (severity_order[x["severity"]], -x["total_impressions"]))

    high   = [c for c in cannibalized if c["severity"] == "high"]
    medium = [c for c in cannibalized if c["severity"] == "medium"]
    low    = [c for c in cannibalized if c["severity"] == "low"]

    # Group by brand for summary
    brand_counts: dict[str, int] = defaultdict(int)
    for item in cannibalized:
        brands = {p["brand"] for p in item["pages"] if p["brand"]}
        for b in brands:
            brand_counts[b] += 1

    brand_summary = [
        {"brand": b, "cannibalized_queries": n}
        for b, n in sorted(brand_counts.items(), key=lambda x: -x[1])
    ]

    return {
        "cannibalized":    cannibalized[:200],
        "total":           len(cannibalized),
        "high":            len(high),
        "medium":          len(medium),
        "low":             len(low),
        "brand_summary":   brand_summary,
        "period":          f"{start_date} → {end_date}",
        "lookback_days":   lookback_days,
    }
