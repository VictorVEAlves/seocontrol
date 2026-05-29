"""
Keyword Position Tracker

Uses the GSC API to track average position of top keywords per configured cluster.
Compares the last 7 days vs the previous 7 days and flags keywords that:
  - Dropped below position 10 (left first page)
  - Worsened by ≥3 positions
  - Are in top/good brand pages (high commercial impact)

Reuses the auth layer from gsc_api.py.
"""

from __future__ import annotations

import os
import json
from datetime import date, timedelta
from urllib.parse import quote

from config import (disable_broken_local_proxy, get_brand_clusters, get_gsc_property,
                    get_site_url, get_gsc_credentials_file, get_gsc_token_file,
                    get_gsc_token_json)

disable_broken_local_proxy()

_SCOPES           = ["https://www.googleapis.com/auth/webmasters.readonly"]

_GSC_QUERY_URL = (
    "https://searchconsole.googleapis.com/webmasters/v3/sites/"
    + quote(get_gsc_property(), safe="")
    + "/searchAnalytics/query"
)

# Top/good pages worth tracking closely
_TRACKED_PAGES: dict[str, dict] = {}
for _brand, _cluster in get_brand_clusters().items():
    _tier = _cluster.get("tier", "")
    for _p in [_cluster["pillar"]] + _cluster.get("pages", []):
        if _p:
            _TRACKED_PAGES[_p.rstrip("/")] = {"brand": _brand, "tier": _tier}


def _build_session():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import google.auth.transport.requests
    import requests as _req

    for k in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        os.environ.pop(k, None)

    credentials_file = get_gsc_credentials_file()
    token_file = get_gsc_token_file()
    token_json = get_gsc_token_json()
    creds = None
    if token_json:
        try:
            creds = Credentials.from_authorized_user_info(json.loads(token_json), _SCOPES)
        except Exception:
            pass
    elif token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), _SCOPES)
        except Exception:
            pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            session = _req.Session()
            session.trust_env = False
            creds.refresh(google.auth.transport.requests.Request(session=session))
        else:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    "GSC não conectado. Acesse Configurações e clique em Conectar com Google."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), _SCOPES)
            creds = flow.run_local_server(port=0)
        if not token_json:
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json(), encoding="utf-8")

    authed = google.auth.transport.requests.AuthorizedSession(creds)
    authed.trust_env = False
    return authed


def _fetch_rows(session, start_date: str, end_date: str, dimensions: list,
                row_limit: int = 1000, filters: list = None) -> list:
    body: dict = {
        "startDate": start_date, "endDate": end_date,
        "dimensions": dimensions, "rowLimit": row_limit,
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]
    resp = session.post(_GSC_QUERY_URL, json=body, timeout=30)
    resp.raise_for_status()
    return resp.json().get("rows", [])


def _date_range(offset_start: int, offset_end: int) -> tuple[str, str]:
    today = date.today()
    return (
        str(today - timedelta(days=offset_start)),
        str(today - timedelta(days=offset_end)),
    )


def run(scope_urls: list[str] | None = None) -> dict:
    session = _build_session()

    cur_start, cur_end   = _date_range(7, 1)
    prev_start, prev_end = _date_range(14, 8)

    pages_to_check = scope_urls or list(_TRACKED_PAGES.keys())

    results: list[dict] = []

    for page_path in pages_to_check:
        full_url = page_path if page_path.startswith("http") else get_site_url() + page_path
        page_filter = [{"dimension": "page", "operator": "equals", "expression": full_url}]
        meta = _TRACKED_PAGES.get(page_path.rstrip("/"), {})

        try:
            cur_rows  = _fetch_rows(session, cur_start, cur_end,   ["query"], filters=page_filter)
            prev_rows = _fetch_rows(session, prev_start, prev_end, ["query"], filters=page_filter)
        except Exception as exc:
            results.append({"page": page_path, "error": str(exc), **meta})
            continue

        # Build position maps: query → position
        cur_map  = {r["keys"][0]: r["position"] for r in cur_rows}
        prev_map = {r["keys"][0]: r["position"] for r in prev_rows}

        keywords: list[dict] = []
        for query, cur_pos in sorted(cur_map.items(), key=lambda x: x[1]):
            prev_pos = prev_map.get(query)
            delta    = round(cur_pos - prev_pos, 1) if prev_pos else None

            status = "stable"
            if cur_pos > 10:
                status = "off_page1"
            elif delta and delta >= 3:
                status = "dropped"
            elif delta and delta <= -3:
                status = "gained"

            keywords.append({
                "query":    query,
                "position": round(cur_pos, 1),
                "prev_pos": round(prev_pos, 1) if prev_pos else None,
                "delta":    delta,
                "clicks":   next((r["clicks"] for r in cur_rows if r["keys"][0] == query), 0),
                "impressions": next((r["impressions"] for r in cur_rows if r["keys"][0] == query), 0),
                "status":   status,
            })

        dropped  = [k for k in keywords if k["status"] == "dropped"]
        off_page = [k for k in keywords if k["status"] == "off_page1"]
        gained   = [k for k in keywords if k["status"] == "gained"]

        results.append({
            "page":        page_path,
            "brand":       meta.get("brand", ""),
            "tier":        meta.get("tier", ""),
            "keywords":    keywords[:50],
            "total_kw":    len(keywords),
            "dropped":     len(dropped),
            "off_page1":   len(off_page),
            "gained":      len(gained),
            "top_drops":   dropped[:5],
            "period_current":  f"{cur_start} → {cur_end}",
            "period_previous": f"{prev_start} → {prev_end}",
        })

    # Summary
    total_dropped  = sum(p.get("dropped", 0) for p in results)
    total_off_page = sum(p.get("off_page1", 0) for p in results)
    pages_critical = [p for p in results if p.get("dropped", 0) > 0 or p.get("off_page1", 0) > 0]

    return {
        "results":        results,
        "pages_checked":  len(results),
        "total_dropped":  total_dropped,
        "total_off_page": total_off_page,
        "pages_critical": len(pages_critical),
        "period_current":  f"{cur_start} → {cur_end}",
        "period_previous": f"{prev_start} → {prev_end}",
    }
