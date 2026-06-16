"""Google Analytics 4 API helpers.

The app already stores a Google OAuth token per user/site for Search Console.
This module reuses that token when it also has Analytics scope and calls GA4
through REST, avoiding extra client dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - Python without zoneinfo
    ZoneInfo = None

import requests

from config import (
    get_ga4_property,
    get_gsc_property,
    get_gsc_token_file,
    get_gsc_token_json,
    get_runtime_dir,
    get_site_id,
    get_site_owner_user_id,
    get_site_url,
    update_runtime_site_config,
)


GSC_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"
GOOGLE_SCOPES = [GSC_SCOPE, GA4_SCOPE, "https://www.googleapis.com/auth/userinfo.email", "openid"]
TOKEN_URL = "https://oauth2.googleapis.com/token"
ADMIN_ACCOUNT_SUMMARIES_URL = "https://analyticsadmin.googleapis.com/v1beta/accountSummaries"
DATA_API_BASE = "https://analyticsdata.googleapis.com/v1beta"
FUNNEL_EVENTS = ["session_start", "view_item", "add_to_cart", "begin_checkout", "purchase"]
ORGANIC_SEO_CHANNELS = ["Organic Search", "Organic Shopping"]
CHANNEL_PRESETS = {
    "all": ("Todos os canais", []),
    "organic": ("SEO organico", ORGANIC_SEO_CHANNELS),
    "seo": ("SEO organico", ORGANIC_SEO_CHANNELS),
    "organic_search": ("Organic Search", ["Organic Search"]),
    "organic_shopping": ("Organic Shopping", ["Organic Shopping"]),
    "direct": ("Direct", ["Direct"]),
    "paid_search": ("Paid Search", ["Paid Search"]),
    "cross_network": ("Cross-network", ["Cross-network"]),
    "paid_social": ("Paid Social", ["Paid Social"]),
    "email": ("Email", ["Email"]),
    "referral": ("Referral", ["Referral"]),
    "display": ("Display", ["Display"]),
    "unassigned": ("Unassigned", ["Unassigned"]),
}


class GA4AuthRequired(Exception):
    """Google Analytics access needs a connected OAuth token."""


def normalize_property(value: str) -> str:
    prop = str(value or "").strip()
    if not prop:
        return ""
    if prop.isdigit():
        return f"properties/{prop}"
    if prop.startswith("properties/"):
        return prop
    return prop


def _nuke_proxies() -> None:
    for key in [
        "HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
        "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy",
    ]:
        os.environ.pop(key, None)


def _token_file() -> Path:
    return get_gsc_token_file()


def _load_token_info() -> dict:
    token_json = get_gsc_token_json()
    if token_json:
        try:
            info = json.loads(token_json)
            return info if isinstance(info, dict) else {}
        except Exception:
            return {}
    token_file = _token_file()
    if token_file.exists():
        try:
            info = json.loads(token_file.read_text(encoding="utf-8"))
            return info if isinstance(info, dict) else {}
        except Exception:
            return {}
    return {}


def _store_token_info(info: dict) -> None:
    token_json = json.dumps(info, ensure_ascii=False)
    if get_gsc_token_json():
        update_runtime_site_config(gsc_token_json=token_json)
        return
    token_file = _token_file()
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(token_json, encoding="utf-8")


def _client_id_secret(info: dict) -> tuple[str, str]:
    client_id = (
        str(info.get("client_id") or "")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
        or os.environ.get("GOOGLE_CLIENT_ID", "")
        or os.environ.get("GSC_OAUTH_CLIENT_ID", "")
    ).strip()
    client_secret = (
        str(info.get("client_secret") or "")
        or os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")
        or os.environ.get("GOOGLE_CLIENT_SECRET", "")
        or os.environ.get("GSC_OAUTH_CLIENT_SECRET", "")
    ).strip()
    return client_id, client_secret


def _token_expired(info: dict) -> bool:
    expiry = str(info.get("expiry") or "")
    if not expiry:
        return not bool(info.get("token"))
    try:
        expiry_dt = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
        return expiry_dt.timestamp() <= time.time() + 90
    except Exception:
        return True


def _refresh_token(info: dict, timeout: int = 10) -> dict:
    refresh_token = str(info.get("refresh_token") or "").strip()
    if not refresh_token:
        raise GA4AuthRequired(
            "Token Google sem refresh_token. Reconecte o Google em Configuracoes."
        )
    client_id, client_secret = _client_id_secret(info)
    if not client_id or not client_secret:
        raise GA4AuthRequired(
            "OAuth Google sem client_id/client_secret. Configure as variaveis do servidor."
        )

    session = requests.Session()
    session.trust_env = False
    resp = session.post(
        str(info.get("token_uri") or TOKEN_URL),
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=timeout,
    )
    try:
        payload = resp.json()
    except Exception:
        payload = {}
    if not resp.ok:
        detail = payload.get("error_description") or payload.get("error") or resp.text[:200]
        raise GA4AuthRequired(
            "Google Analytics nao autorizado para este token. "
            f"Reconecte o Google nas Configuracoes. Detalhe: {detail}"
        )
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GA4AuthRequired("Refresh do Google nao retornou access_token.")

    info = dict(info)
    info["token"] = access_token
    info["token_uri"] = str(info.get("token_uri") or TOKEN_URL)
    if payload.get("refresh_token"):
        info["refresh_token"] = str(payload["refresh_token"])
    try:
        expires_in = int(payload.get("expires_in") or 3600)
    except Exception:
        expires_in = 3600
    info["expiry"] = (datetime.utcnow() + timedelta(seconds=max(60, expires_in - 60))).isoformat() + "Z"
    payload_scopes = payload.get("scope")
    if payload_scopes:
        info["scopes"] = str(payload_scopes).split()
    _store_token_info(info)
    return info


def _get_bearer(timeout: int = 10) -> str:
    _nuke_proxies()
    info = _load_token_info()
    if not info:
        raise GA4AuthRequired("Google Analytics nao conectado.")
    token = str(info.get("token") or "").strip()
    if token and not _token_expired(info):
        return token
    info = _refresh_token(info, timeout=timeout)
    return str(info.get("token") or "")


def _headers(timeout: int = 10) -> dict:
    token = _get_bearer(timeout=timeout)
    if not token:
        raise GA4AuthRequired("Token Google vazio para Analytics.")
    return {"Authorization": f"Bearer {token}"}


def list_ga4_properties(timeout: int = 12) -> dict:
    """Return GA4 properties available to the connected Google account."""
    try:
        session = requests.Session()
        session.trust_env = False
        resp = session.get(ADMIN_ACCOUNT_SUMMARIES_URL, headers=_headers(timeout=timeout), timeout=timeout)
        if not resp.ok:
            try:
                payload = resp.json()
                detail = payload.get("error", {}).get("message") or resp.text[:200]
            except Exception:
                detail = resp.text[:200]
            return {"error": detail, "properties": []}
        properties: list[dict] = []
        for account in resp.json().get("accountSummaries", []) or []:
            account_name = account.get("displayName") or account.get("name") or ""
            for prop in account.get("propertySummaries", []) or []:
                name = str(prop.get("property") or "").strip()
                if not name:
                    continue
                properties.append({
                    "property": name,
                    "display_name": str(prop.get("displayName") or name),
                    "account": str(account_name),
                })
        return {"properties": properties}
    except Exception as exc:
        return {"error": str(exc), "properties": []}


def _channel_key(channel: str | None) -> str:
    key = str(channel or "all").strip()
    if not key:
        return "all"
    return key.lower().replace(" ", "_").replace("-", "_")


def _channel_filter_values(channel: str | None) -> list[str]:
    raw = str(channel or "all").strip()
    key = _channel_key(raw)
    if key in {"organic", "seo"}:
        return ["sessionSourceMedium contains / organic"]
    if key in CHANNEL_PRESETS:
        return list(CHANNEL_PRESETS[key][1])
    if raw.lower() in {"all", "todos", "todos_os_canais"}:
        return []
    return [raw]


def _channel_label(channel: str | None) -> str:
    raw = str(channel or "all").strip()
    key = _channel_key(raw)
    if key in CHANNEL_PRESETS:
        return CHANNEL_PRESETS[key][0]
    return raw or CHANNEL_PRESETS["all"][0]


def _dimension_in_filter(field_name: str, values: list[str]) -> dict | None:
    filtered = [str(value).strip() for value in values if str(value or "").strip()]
    if not filtered:
        return None
    return {
        "filter": {
            "fieldName": field_name,
            "inListFilter": {"values": filtered, "caseSensitive": False},
        }
    }


def _dimension_contains_filter(field_name: str, value: str) -> dict | None:
    text = str(value or "").strip()
    if not text:
        return None
    return {
        "filter": {
            "fieldName": field_name,
            "stringFilter": {
                "matchType": "CONTAINS",
                "value": text,
                "caseSensitive": False,
            },
        }
    }


def _is_seo_organic_channel(channel: str | None) -> bool:
    return _channel_key(channel) in {"organic", "seo"}


def _channel_dimension_filter(channel: str | None) -> dict | None:
    if _is_seo_organic_channel(channel):
        return _dimension_contains_filter("sessionSourceMedium", " / organic")
    return _dimension_in_filter("sessionDefaultChannelGroup", _channel_filter_values(channel))


def _seo_organic_dimension_filter() -> dict:
    return _dimension_contains_filter("sessionSourceMedium", " / organic") or {}


def _combine_filters(*filters: dict | None) -> dict | None:
    expressions = [item for item in filters if item]
    if not expressions:
        return None
    if len(expressions) == 1:
        return expressions[0]
    return {"andGroup": {"expressions": expressions}}


def _same_values(left: list[str], right: list[str]) -> bool:
    return sorted(left) == sorted(right)


def _cache_file(kind: str, period_days: int | str, property_id: str, channel: str | None = None) -> Path:
    raw_key = "|".join([
        "ga4-filter-v2",
        get_site_owner_user_id() or "local",
        get_site_id() or "",
        property_id or get_site_url() or "default",
        _channel_key(channel),
    ])
    site_key = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()
    return get_runtime_dir() / f"ga4_{kind}_{site_key}_{period_days}d.json"


def _run_report(
    property_id: str,
    start_date: str,
    end_date: str,
    metrics: list[str],
    dimensions: list[str] | None = None,
    limit: int = 100,
    dimension_filter: dict | None = None,
    timeout: int = 18,
) -> dict:
    prop = normalize_property(property_id)
    if not prop:
        raise ValueError("Propriedade GA4 nao configurada.")
    body: dict = {
        "dateRanges": [{"startDate": start_date, "endDate": end_date}],
        "metrics": [{"name": metric} for metric in metrics],
        "limit": str(limit),
        "keepEmptyRows": True,
    }
    if dimensions:
        body["dimensions"] = [{"name": dim} for dim in dimensions]
    if dimension_filter:
        body["dimensionFilter"] = dimension_filter

    session = requests.Session()
    session.trust_env = False
    resp = session.post(
        f"{DATA_API_BASE}/{prop}:runReport",
        headers={**_headers(timeout=timeout), "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
    )
    if not resp.ok:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text[:250]
        except Exception:
            detail = resp.text[:250]
        raise RuntimeError(detail)
    return resp.json()


def _row_metrics(row: dict, metric_names: list[str]) -> dict:
    values = row.get("metricValues") or []
    out: dict[str, float] = {}
    for idx, name in enumerate(metric_names):
        raw = values[idx].get("value") if idx < len(values) else 0
        try:
            out[name] = float(raw or 0)
        except Exception:
            out[name] = 0.0
    return out


def _row_dimensions(row: dict, dim_names: list[str]) -> dict:
    values = row.get("dimensionValues") or []
    out: dict[str, str] = {}
    for idx, name in enumerate(dim_names):
        out[name] = str(values[idx].get("value") if idx < len(values) else "")
    return out


def _summary_from_report(payload: dict, metrics: list[str]) -> dict:
    rows = payload.get("rows") or []
    if not rows:
        return {metric: 0.0 for metric in metrics}
    return _row_metrics(rows[0], metrics)


def _pct_delta(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _money(value: float) -> float:
    return round(float(value or 0), 2)


def _date_label(value: str) -> str:
    raw = str(value or "")
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:]}"
    return raw


def _today_for_reports() -> date:
    timezone_name = os.environ.get("APP_TIMEZONE", "America/Sao_Paulo")
    if ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(timezone_name)).date()
        except Exception:
            pass
    return date.today()


def _periods(period_days: int) -> tuple[date, date, date, date]:
    today = _today_for_reports()
    cur_end = today - timedelta(days=1)
    cur_start = cur_end - timedelta(days=period_days - 1)
    prev_end = cur_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=period_days - 1)
    return cur_start, cur_end, prev_start, prev_end


def _resolved_periods(
    period_days: int,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[date, date, date, date, int]:
    if start_date and end_date:
        cur_start = date.fromisoformat(str(start_date))
        cur_end = date.fromisoformat(str(end_date))
        if cur_start > cur_end:
            raise ValueError("A data inicial deve ser anterior a data final.")
        days = (cur_end - cur_start).days + 1
        if days > 500:
            raise ValueError("O intervalo do dashboard deve ter no maximo 500 dias.")
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=days - 1)
        return cur_start, cur_end, prev_start, prev_end, days
    days = max(1, min(500, int(period_days or 28)))
    cur_start, cur_end, prev_start, prev_end = _periods(days)
    return cur_start, cur_end, prev_start, prev_end, days


def _event_filter(events: list[str]) -> dict:
    return {
        "filter": {
            "fieldName": "eventName",
            "inListFilter": {"values": events},
        }
    }


def _build_funnel(payload: dict) -> list[dict]:
    counts = {event: 0 for event in FUNNEL_EVENTS}
    for row in payload.get("rows") or []:
        dims = _row_dimensions(row, ["eventName"])
        metrics = _row_metrics(row, ["eventCount"])
        event = dims.get("eventName")
        if event in counts:
            counts[event] += int(metrics.get("eventCount") or 0)

    labels = {
        "session_start": "Sessao iniciada",
        "view_item": "Visualizou produto",
        "add_to_cart": "Adicionou ao carrinho",
        "begin_checkout": "Iniciou checkout",
        "purchase": "Compra",
    }
    first = counts.get("session_start") or max(counts.values() or [0]) or 0
    previous = None
    funnel = []
    for event in FUNNEL_EVENTS:
        count = int(counts.get(event) or 0)
        funnel.append({
            "event": event,
            "label": labels[event],
            "count": count,
            "from_start_rate": round(count / first * 100, 2) if first else 0.0,
            "step_rate": round(count / previous * 100, 2) if previous else None,
        })
        previous = count or previous
    return funnel


def _gsc_query_url() -> str:
    prop = str(get_gsc_property() or get_site_url() or "").strip()
    if not prop:
        return ""
    return (
        "https://searchconsole.googleapis.com/webmasters/v3/sites/"
        + quote(prop, safe="")
        + "/searchAnalytics/query"
    )


def _run_gsc_search_analytics(
    start_date: date,
    end_date: date,
    dimensions: list[str],
    row_limit: int = 500,
    timeout: int = 15,
) -> list[dict]:
    url = _gsc_query_url()
    if not url:
        return []
    session = requests.Session()
    session.trust_env = False
    resp = session.post(
        url,
        headers={**_headers(timeout=timeout), "Content-Type": "application/json"},
        json={
            "startDate": str(start_date),
            "endDate": str(end_date),
            "dimensions": dimensions,
            "rowLimit": row_limit,
            "startRow": 0,
        },
        timeout=timeout,
    )
    if not resp.ok:
        try:
            detail = resp.json().get("error", {}).get("message") or resp.text[:250]
        except Exception:
            detail = resp.text[:250]
        raise RuntimeError(detail)
    return resp.json().get("rows") or []


def _fetch_gsc_seo_data(start_date: date, end_date: date) -> dict:
    gsc_end = min(end_date, _today_for_reports() - timedelta(days=2))
    if gsc_end < start_date:
        gsc_end = end_date
    try:
        daily_rows = _run_gsc_search_analytics(start_date, gsc_end, ["date"], row_limit=500)
        page_rows = _run_gsc_search_analytics(start_date, gsc_end, ["page"], row_limit=100)
    except Exception as exc:
        return {
            "error": str(exc),
            "period": f"{start_date} -> {gsc_end}",
            "clicks": 0,
            "impressions": 0,
            "ctr": 0.0,
            "position": 0.0,
            "pages": [],
        }

    clicks = sum(int(row.get("clicks") or 0) for row in daily_rows)
    impressions = sum(int(row.get("impressions") or 0) for row in daily_rows)
    weighted_pos = sum(
        float(row.get("position") or 0) * int(row.get("impressions") or 0)
        for row in daily_rows
    )
    pages = []
    for row in page_rows:
        keys = row.get("keys") or []
        page = str(keys[0] if keys else "")
        row_impressions = int(row.get("impressions") or 0)
        row_clicks = int(row.get("clicks") or 0)
        pages.append({
            "page": page,
            "impressions": row_impressions,
            "clicks": row_clicks,
            "ctr": round(row_clicks / row_impressions * 100, 2) if row_impressions else 0.0,
            "position": round(float(row.get("position") or 0), 1),
        })

    return {
        "period": f"{start_date} -> {gsc_end}",
        "clicks": clicks,
        "impressions": impressions,
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
        "position": round(weighted_pos / impressions, 1) if impressions else 0.0,
        "pages": pages,
    }


def _funnel_count(funnel: list[dict], event: str) -> int:
    for step in funnel:
        if step.get("event") == event:
            return int(step.get("count") or 0)
    return 0


def _rate(value: float, base: float) -> float | None:
    if not base:
        return None
    return round(float(value or 0) / float(base) * 100, 2)


def _build_seo_funnel(gsc_data: dict, organic_summary: dict, organic_funnel: list[dict]) -> dict:
    impressions = int(gsc_data.get("impressions") or 0)
    clicks = int(gsc_data.get("clicks") or 0)
    sessions = int(organic_summary.get("sessions") or 0)
    view_item = _funnel_count(organic_funnel, "view_item")
    add_to_cart = _funnel_count(organic_funnel, "add_to_cart")
    checkout = _funnel_count(organic_funnel, "begin_checkout")
    purchases = int(organic_summary.get("ecommercePurchases") or 0)
    revenue = _money(organic_summary.get("totalRevenue") or 0)

    steps = [
        {"key": "impressions", "label": "Impressoes GSC", "value": impressions, "kind": "count", "from_start_rate": 100.0 if impressions else 0.0, "step_rate": None},
        {"key": "clicks", "label": "Cliques GSC", "value": clicks, "kind": "count", "from_start_rate": _rate(clicks, impressions), "step_rate": _rate(clicks, impressions)},
        {"key": "sessions", "label": "Sessoes organicas GA4", "value": sessions, "kind": "count", "from_start_rate": _rate(sessions, impressions), "step_rate": _rate(sessions, clicks)},
        {"key": "view_item", "label": "Visualizou produto", "value": view_item, "kind": "count", "from_start_rate": _rate(view_item, impressions), "step_rate": _rate(view_item, sessions)},
        {"key": "add_to_cart", "label": "Adicionou ao carrinho", "value": add_to_cart, "kind": "count", "from_start_rate": _rate(add_to_cart, impressions), "step_rate": _rate(add_to_cart, view_item)},
        {"key": "checkout", "label": "Iniciou checkout", "value": checkout, "kind": "count", "from_start_rate": _rate(checkout, impressions), "step_rate": _rate(checkout, add_to_cart)},
        {"key": "purchases", "label": "Compras organicas", "value": purchases, "kind": "count", "from_start_rate": _rate(purchases, impressions), "step_rate": _rate(purchases, checkout)},
        {"key": "revenue", "label": "Receita organica", "value": revenue, "kind": "money", "from_start_rate": None, "step_rate": None},
    ]
    return {
        "period": gsc_data.get("period") or "",
        "ga4_channels": ORGANIC_SEO_CHANNELS,
        "error": gsc_data.get("error") or "",
        "steps": steps,
        "metrics": {
            "ctr": _rate(clicks, impressions),
            "click_to_session_rate": _rate(sessions, clicks),
            "session_to_purchase_rate": _rate(purchases, sessions),
            "revenue_per_click": _money(revenue / clicks) if clicks else 0.0,
            "revenue_per_1000_impressions": _money(revenue / impressions * 1000) if impressions else 0.0,
        },
    }


def _path_key(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    path = parsed.path if parsed.scheme or parsed.netloc else raw.split("?", 1)[0]
    if not path.startswith("/"):
        path = "/" + path
    return path.rstrip("/") or "/"


def _build_seo_landing_pages(gsc_pages: list[dict], organic_landings: list[dict]) -> list[dict]:
    gsc_map: dict[str, dict] = {}
    for row in gsc_pages or []:
        key = _path_key(str(row.get("page") or ""))
        if not key:
            continue
        current = gsc_map.setdefault(key, {"impressions": 0, "clicks": 0, "position_sum": 0.0})
        impressions = int(row.get("impressions") or 0)
        clicks = int(row.get("clicks") or 0)
        current["impressions"] += impressions
        current["clicks"] += clicks
        current["position_sum"] += float(row.get("position") or 0) * impressions

    ga4_map: dict[str, dict] = {}
    for row in organic_landings or []:
        key = _path_key(str(row.get("landing_page") or ""))
        if not key:
            continue
        current = ga4_map.setdefault(key, {"revenue": 0.0, "purchases": 0, "sessions": 0})
        current["revenue"] += float(row.get("revenue") or 0)
        current["purchases"] += int(row.get("purchases") or 0)
        current["sessions"] += int(row.get("sessions") or 0)

    rows = []
    for key in sorted(set(gsc_map) | set(ga4_map)):
        gsc = gsc_map.get(key, {})
        ga4 = ga4_map.get(key, {})
        impressions = int(gsc.get("impressions") or 0)
        clicks = int(gsc.get("clicks") or 0)
        sessions = int(ga4.get("sessions") or 0)
        purchases = int(ga4.get("purchases") or 0)
        revenue = _money(ga4.get("revenue") or 0)
        rows.append({
            "landing_page": key,
            "impressions": impressions,
            "clicks": clicks,
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
            "sessions": sessions,
            "purchases": purchases,
            "revenue": revenue,
            "conversion_rate": round(purchases / sessions * 100, 2) if sessions else 0.0,
            "revenue_per_click": _money(revenue / clicks) if clicks else 0.0,
        })
    rows.sort(key=lambda item: (float(item.get("revenue") or 0), int(item.get("clicks") or 0)), reverse=True)
    return rows[:50]


def get_revenue_summary(
    period_days: int = 28,
    property_id: str | None = None,
    force: bool = False,
    channel: str | None = "organic",
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict:
    """Fetch only the GA4 revenue KPIs needed by the main dashboard."""
    prop = normalize_property(property_id or get_ga4_property())
    if not prop:
        return {"error": "Selecione a propriedade do Google Analytics 4 nas Configuracoes."}

    try:
        cur_start, cur_end, prev_start, prev_end, period_days = _resolved_periods(
            period_days,
            start_date,
            end_date,
        )
    except Exception as exc:
        return {"error": str(exc)}

    channel = channel or "organic"
    cache_key = f"{cur_start}_{cur_end}" if start_date and end_date else period_days
    cache_file = _cache_file("revenue_summary", cache_key, prop, channel)
    if not force:
        try:
            if cache_file.exists():
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if time.time() - float(cached.get("_cached_at", 0)) < 3600:
                    return cached
        except Exception:
            pass

    metrics = ["totalRevenue", "ecommercePurchases", "sessions"]
    channel_filter = _channel_dimension_filter(channel)
    try:
        current = _summary_from_report(
            _run_report(
                prop,
                str(cur_start),
                str(cur_end),
                metrics,
                limit=1,
                dimension_filter=channel_filter,
            ),
            metrics,
        )
        previous = _summary_from_report(
            _run_report(
                prop,
                str(prev_start),
                str(prev_end),
                metrics,
                limit=1,
                dimension_filter=channel_filter,
            ),
            metrics,
        )
    except Exception as exc:
        return {"error": str(exc)}

    revenue = float(current.get("totalRevenue") or 0)
    purchases = int(current.get("ecommercePurchases") or 0)
    sessions = int(current.get("sessions") or 0)
    prev_revenue = float(previous.get("totalRevenue") or 0)
    prev_purchases = int(previous.get("ecommercePurchases") or 0)
    prev_sessions = int(previous.get("sessions") or 0)
    avg_purchase = revenue / purchases if purchases else 0
    prev_avg_purchase = prev_revenue / prev_purchases if prev_purchases else 0

    def kpi(value: float, prev: float, money: bool = False) -> dict:
        return {
            "value": _money(value) if money else round(float(value or 0), 2),
            "prev": _money(prev) if money else round(float(prev or 0), 2),
            "delta": _pct_delta(value, prev),
        }

    out = {
        "property": prop,
        "period_days": period_days,
        "period": f"{cur_start} -> {cur_end}",
        "previous_period": f"{prev_start} -> {prev_end}",
        "channel": _channel_key(channel),
        "channel_label": _channel_label(channel),
        "kpis": {
            "revenue": kpi(revenue, prev_revenue, money=True),
            "purchases": kpi(purchases, prev_purchases),
            "avg_purchase_revenue": kpi(avg_purchase, prev_avg_purchase, money=True),
            "sessions": kpi(sessions, prev_sessions),
        },
        "_cached_at": time.time(),
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def get_analytics_data(
    period_days: int = 28,
    property_id: str | None = None,
    force: bool = False,
    channel: str | None = None,
) -> dict:
    """Fetch GA4 ecommerce and funnel data for the active site."""
    period_days = max(7, min(365, int(period_days or 28)))
    prop = normalize_property(property_id or get_ga4_property())
    if not prop:
        return {"error": "Selecione a propriedade do Google Analytics 4 nas Configuracoes."}

    channel_values = _channel_filter_values(channel)
    channel_label = _channel_label(channel)
    channel_filter = _channel_dimension_filter(channel)
    organic_filter = _seo_organic_dimension_filter()
    cache_file = _cache_file("dashboard", period_days, prop, channel or "all")
    if not force:
        try:
            if cache_file.exists():
                cached = json.loads(cache_file.read_text(encoding="utf-8"))
                if time.time() - float(cached.get("_cached_at", 0)) < 3600:
                    return cached
        except Exception:
            pass

    cur_start, cur_end, prev_start, prev_end = _periods(period_days)
    metrics = ["totalRevenue", "ecommercePurchases", "sessions", "activeUsers"]

    try:
        current_summary = _summary_from_report(
            _run_report(prop, str(cur_start), str(cur_end), metrics, limit=1, dimension_filter=channel_filter),
            metrics,
        )
        previous_summary = _summary_from_report(
            _run_report(prop, str(prev_start), str(prev_end), metrics, limit=1, dimension_filter=channel_filter),
            metrics,
        )
        current_daily = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            metrics,
            dimensions=["date"],
            limit=period_days + 2,
            dimension_filter=channel_filter,
        )
        previous_daily = _run_report(
            prop,
            str(prev_start),
            str(prev_end),
            metrics,
            dimensions=["date"],
            limit=period_days + 2,
            dimension_filter=channel_filter,
        )
        channel_report = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            metrics,
            dimensions=["sessionDefaultChannelGroup"],
            limit=12,
            dimension_filter=channel_filter,
        )
        source_report = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            metrics,
            dimensions=["sessionSourceMedium"],
            limit=12,
            dimension_filter=channel_filter,
        )
        landing_report = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            metrics,
            dimensions=["landingPagePlusQueryString"],
            limit=25,
            dimension_filter=channel_filter,
        )
        funnel_report = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            ["eventCount"],
            dimensions=["eventName"],
            limit=20,
            dimension_filter=_combine_filters(_event_filter(FUNNEL_EVENTS), channel_filter),
        )
    except Exception as exc:
        return {"error": str(exc)}

    product_rows: list[dict] = []
    try:
        product_report = _run_report(
            prop,
            str(cur_start),
            str(cur_end),
            ["itemRevenue", "itemsPurchased"],
            dimensions=["itemName"],
            limit=15,
            dimension_filter=channel_filter,
        )
        for row in product_report.get("rows") or []:
            dims = _row_dimensions(row, ["itemName"])
            vals = _row_metrics(row, ["itemRevenue", "itemsPurchased"])
            product_rows.append({
                "item": dims.get("itemName") or "(not set)",
                "revenue": _money(vals.get("itemRevenue")),
                "items": int(vals.get("itemsPurchased") or 0),
            })
    except Exception:
        product_rows = []

    revenue = float(current_summary.get("totalRevenue") or 0)
    purchases = int(current_summary.get("ecommercePurchases") or 0)
    sessions = int(current_summary.get("sessions") or 0)
    users = int(current_summary.get("activeUsers") or 0)

    prev_revenue = float(previous_summary.get("totalRevenue") or 0)
    prev_purchases = int(previous_summary.get("ecommercePurchases") or 0)
    prev_sessions = int(previous_summary.get("sessions") or 0)
    prev_users = int(previous_summary.get("activeUsers") or 0)

    avg_purchase = revenue / purchases if purchases else 0
    prev_avg_purchase = prev_revenue / prev_purchases if prev_purchases else 0
    conversion_rate = purchases / sessions * 100 if sessions else 0
    prev_conversion_rate = prev_purchases / prev_sessions * 100 if prev_sessions else 0

    def kpi(value: float, prev: float, money: bool = False) -> dict:
        return {
            "value": _money(value) if money else round(float(value or 0), 2),
            "prev": _money(prev) if money else round(float(prev or 0), 2),
            "delta": _pct_delta(value, prev),
        }

    kpis = {
        "revenue": kpi(revenue, prev_revenue, money=True),
        "purchases": kpi(purchases, prev_purchases),
        "avg_purchase_revenue": kpi(avg_purchase, prev_avg_purchase, money=True),
        "conversion_rate": kpi(conversion_rate, prev_conversion_rate),
        "sessions": kpi(sessions, prev_sessions),
        "active_users": kpi(users, prev_users),
    }

    def daily_rows(payload: dict) -> list[dict]:
        rows = []
        for row in sorted(payload.get("rows") or [], key=lambda r: (_row_dimensions(r, ["date"]).get("date") or "")):
            dims = _row_dimensions(row, ["date"])
            vals = _row_metrics(row, metrics)
            rows.append({
                "date": _date_label(dims.get("date") or ""),
                "revenue": _money(vals.get("totalRevenue")),
                "purchases": int(vals.get("ecommercePurchases") or 0),
                "sessions": int(vals.get("sessions") or 0),
                "active_users": int(vals.get("activeUsers") or 0),
            })
        return rows

    def dimension_rows(payload: dict, dimension: str, label: str) -> list[dict]:
        rows = []
        for row in payload.get("rows") or []:
            dims = _row_dimensions(row, [dimension])
            vals = _row_metrics(row, metrics)
            row_sessions = int(vals.get("sessions") or 0)
            row_purchases = int(vals.get("ecommercePurchases") or 0)
            rows.append({
                label: dims.get(dimension) or "(not set)",
                "revenue": _money(vals.get("totalRevenue")),
                "purchases": row_purchases,
                "sessions": row_sessions,
                "active_users": int(vals.get("activeUsers") or 0),
                "conversion_rate": round(row_purchases / row_sessions * 100, 2) if row_sessions else 0.0,
            })
        rows.sort(key=lambda r: (float(r.get("revenue") or 0), int(r.get("purchases") or 0)), reverse=True)
        return rows

    is_seo_organic = _is_seo_organic_channel(channel)
    organic_summary = current_summary if is_seo_organic else {}
    organic_funnel = _build_funnel(funnel_report) if is_seo_organic else []
    organic_landings: list[dict] = dimension_rows(landing_report, "landingPagePlusQueryString", "landing_page") if is_seo_organic else []
    try:
        if not organic_summary:
            organic_summary = _summary_from_report(
                _run_report(prop, str(cur_start), str(cur_end), metrics, limit=1, dimension_filter=organic_filter),
                metrics,
            )
        if not organic_funnel:
            organic_funnel = _build_funnel(_run_report(
                prop,
                str(cur_start),
                str(cur_end),
                ["eventCount"],
                dimensions=["eventName"],
                limit=20,
                dimension_filter=_combine_filters(_event_filter(FUNNEL_EVENTS), organic_filter),
            ))
        if not organic_landings:
            organic_landing_report = _run_report(
                prop,
                str(cur_start),
                str(cur_end),
                metrics,
                dimensions=["landingPagePlusQueryString"],
                limit=50,
                dimension_filter=organic_filter,
            )
            organic_landings = dimension_rows(organic_landing_report, "landingPagePlusQueryString", "landing_page")
        gsc_seo_data = _fetch_gsc_seo_data(cur_start, cur_end)
        seo_funnel = _build_seo_funnel(gsc_seo_data, organic_summary, organic_funnel)
        seo_landing_pages = _build_seo_landing_pages(gsc_seo_data.get("pages") or [], organic_landings)
    except Exception as exc:
        seo_funnel = {
            "period": "",
            "ga4_channels": ORGANIC_SEO_CHANNELS,
            "error": str(exc),
            "steps": [],
            "metrics": {},
        }
        seo_landing_pages = []

    out = {
        "property": prop,
        "period_days": period_days,
        "period": f"{cur_start} -> {cur_end}",
        "previous_period": f"{prev_start} -> {prev_end}",
        "period_start": str(cur_start),
        "period_end": str(cur_end),
        "previous_period_start": str(prev_start),
        "previous_period_end": str(prev_end),
        "channel": _channel_key(channel),
        "channel_label": channel_label,
        "channel_values": channel_values,
        "kpis": kpis,
        "time_series": daily_rows(current_daily),
        "previous_time_series": daily_rows(previous_daily),
        "channels": dimension_rows(channel_report, "sessionDefaultChannelGroup", "channel"),
        "sources": dimension_rows(source_report, "sessionSourceMedium", "source_medium"),
        "landing_pages": dimension_rows(landing_report, "landingPagePlusQueryString", "landing_page"),
        "products": product_rows,
        "funnel": _build_funnel(funnel_report),
        "seo_funnel": seo_funnel,
        "seo_landing_pages": seo_landing_pages,
        "_cached_at": time.time(),
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out
