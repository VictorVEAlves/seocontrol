"""
Google Search Console API — trend detection & live performance data.

Uses requests + google-auth directly (no httplib2) to avoid Windows proxy issues.

Web setup:
  Users connect in /settings with the server-level Google OAuth app.
  Tokens are saved per user/site and reused by the GSC modules.

CLI/local fallback:
  A local gsc_credentials.json can still be used for developer-only scripts.
"""

from __future__ import annotations

import json
import hashlib
import os
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

from config import (BASE_DIR, GEMINI_API_KEY,
                    disable_broken_local_proxy, get_site_url, get_gsc_property, get_site_name,
                    get_brand_clusters, get_gsc_credentials_file, get_gsc_token_file,
                    get_site_id, get_site_owner_user_id)


def _nuke_proxies() -> None:
    for key in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy", "FTP_PROXY", "ftp_proxy"]:
        os.environ.pop(key, None)


def _make_stdout_safe() -> None:
    """Force UTF-8 on stdout/stderr so prints with → á é ç never raise on Windows."""
    import sys
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


disable_broken_local_proxy()
_nuke_proxies()
_make_stdout_safe()

SCOPES           = ["https://www.googleapis.com/auth/webmasters.readonly"]


def _credentials_file() -> Path:
    return get_gsc_credentials_file()


def _token_file() -> Path:
    return get_gsc_token_file()


def _dashboard_cache_file(kind: str, period_days: int) -> Path:
    raw_key = "|".join([
        get_site_owner_user_id() or "local",
        get_site_id() or "",
        get_gsc_property() or get_site_url() or "default",
    ])
    site_key = hashlib.sha1(raw_key.encode("utf-8")).hexdigest()
    folder = BASE_DIR / ".runtime"
    return folder / f"dashboard_{kind}_{site_key}_{period_days}d.json"


def _gsc_query_url() -> str:
    """Build the GSC searchAnalytics query endpoint for the current property."""
    return (
        "https://searchconsole.googleapis.com/webmasters/v3/sites/"
        + quote(get_gsc_property(), safe="")
        + "/searchAnalytics/query"
    )

DROP_CRITICAL = -0.25
DROP_WARNING  = -0.15

def _build_brand_page_index() -> dict[str, dict]:
    index: dict[str, dict] = {}
    for _brand, _cluster in get_brand_clusters().items():
        _tier = _cluster.get("tier", "")
        for _p in [_cluster["pillar"]] + _cluster.get("pages", []) + _cluster.get("blog", []):
            if _p:
                index[_p.rstrip("/")] = {"brand": _brand, "tier": _tier}
    return index


# ── Auth ──────────────────────────────────────────────────────────────────────

class GSCAuthRequired(Exception):
    """GSC needs interactive (browser) auth — not safe to do inside a web request."""
    pass


def _get_credentials(silent: bool = False):
    """Load GSC credentials. With silent=True, never opens a browser — raises instead."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    import google.auth.transport.requests
    import requests as _req

    creds = None
    token_file = _token_file()
    credentials_file = _credentials_file()
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception:
            pass

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # Refresh using a proxy-free requests session
            session = _req.Session()
            session.trust_env = False
            auth_req = google.auth.transport.requests.Request(session=session)
            creds.refresh(auth_req)
        else:
            if silent:
                raise GSCAuthRequired(
                    "GSC não autenticado. Acesse Configurações → Google Search Console → Conectar com Google."
                )
            if not credentials_file.exists():
                raise FileNotFoundError(
                    "GSC não conectado. Acesse Configurações e clique em Conectar com Google."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)

        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    return creds


def _build_session(silent: bool = False):
    """
    Return a proxy-free AuthorizedSession for the GSC REST API.
    Uses requests with trust_env=False so Windows system proxies are ignored.
    silent=True never triggers the interactive browser flow (web-safe).
    """
    import requests as _req
    import google.auth.transport.requests

    _nuke_proxies()
    creds = _get_credentials(silent=silent)

    # Proxy-free base session — trust_env=False blocks ALL proxy sources
    # (env vars, .netrc, Windows registry, IE settings)
    base = _req.Session()
    base.trust_env = False

    # Refresh token if needed using the same proxy-free session
    if not creds.valid:
        auth_req = google.auth.transport.requests.Request(session=base)
        creds.refresh(auth_req)
        token_file = _token_file()
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(creds.to_json(), encoding="utf-8")

    authed = google.auth.transport.requests.AuthorizedSession(creds)
    authed.trust_env = False
    return authed


def _get_gsc_bearer(timeout: int = 20) -> str:
    """
    Get a GSC API bearer token WITHOUT interactive auth, bounded by `timeout`.
    Runs the (potentially blocking) auth in a worker thread so a hung network
    refresh can never freeze the caller. Raises on failure or timeout.
    """
    import concurrent.futures as _cf

    def _work() -> str:
        session = _build_session(silent=True)
        creds = session.credentials
        if not creds.token:
            raise RuntimeError("token GSC vazio após autenticação")
        return creds.token

    _ex = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        return _ex.submit(_work).result(timeout=timeout)
    except _cf.TimeoutError:
        raise TimeoutError(f"autenticação GSC excedeu {timeout}s")
    finally:
        _ex.shutdown(wait=False)


# ── API calls ─────────────────────────────────────────────────────────────────


def _fetch_rows(session, start_date: str, end_date: str, dimensions: list,
                row_limit: int = 500, filters: list = None, timeout: int = 30) -> list:
    body: dict = {
        "startDate":  start_date,
        "endDate":    end_date,
        "dimensions": dimensions,
        "rowLimit":   row_limit,
        "startRow":   0,
    }
    if filters:
        body["dimensionFilterGroups"] = [{"filters": filters}]

    for attempt in range(3):
        try:
            resp = session.post(_gsc_query_url(), json=body, timeout=timeout)
            resp.raise_for_status()
            return resp.json().get("rows", [])
        except Exception as exc:
            err = str(exc)
            if attempt < 2 and ("10060" in err or "10061" in err or "timed out" in err.lower()
                                 or "503" in err or "connection" in err.lower()):
                _nuke_proxies()
                time.sleep(3 * (attempt + 1))
                continue
            print(f"    ! GSC API erro: {exc}")
            return []
    return []


def _date_range(days_back_start: int, days_back_end: int) -> tuple[str, str]:
    today = date.today()
    end   = today - timedelta(days=days_back_end)
    start = today - timedelta(days=days_back_start)
    return str(start), str(end)


def _rows_to_dict(rows: list) -> dict:
    result = {}
    for row in rows:
        keys = row.get("keys", [])
        key  = keys[0] if keys else ""
        result[key] = {
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         round(float(row.get("ctr", 0)) * 100, 3),
            "position":    round(float(row.get("position", 0)), 1),
        }
    return result


# ── Brand classification ───────────────────────────────────────────────────────

def _classify_page(page_url: str) -> dict:
    index = _build_brand_page_index()
    if not index:
        return {"brand": None, "tier": None}
    path = page_url.replace(get_site_url(), "").rstrip("/")
    if path in index:
        return index[path]
    for bp, info in index.items():
        if path.startswith(bp + "/") or path.startswith(bp + "-"):
            return info
    return {"brand": None, "tier": None}


def _priority_score(drop: dict) -> float:
    score = 0.0
    tier  = drop.get("tier") or ""
    sev   = drop.get("severity", "warning")
    impr  = int(drop.get("impressions") or 0)

    if tier == "top":    score += 60
    elif tier == "good": score += 35

    if sev == "critical":
        score += 25
        # Use impression loss magnitude as signal (impressions actually fell)
        score += abs(drop.get("impressions_delta") or 0) * 30
    elif sev == "warning":
        score += abs(drop.get("impressions_delta") or 0) * 30
    elif sev == "ctr_issue":
        # Use click-drop magnitude instead; impression gain doesn't indicate priority
        score += abs(drop.get("clicks_delta") or 0) * 15

    if impr >= 5000:   score += 30
    elif impr >= 2000: score += 20
    elif impr >= 1000: score += 10
    elif impr >= 500:  score += 5

    return score


# ── Analysis ──────────────────────────────────────────────────────────────────

def _pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous, 4)


def _detect_page_drops(current: dict, previous: dict, min_impressions: int = 300) -> list:
    """
    Classify each page into one of four buckets:
      critical / warning  — impressions dropped (the main risk signal)
      ctr_issue           — impressions stable/up but clicks dropped significantly
                            (CTR/snippet problem, not a visibility loss)
    Pages where impressions increased are excluded from the drops list entirely,
    even if clicks dropped — those belong in a separate CTR report.
    """
    drops = []
    for page, cur in current.items():
        prev = previous.get(page)
        if not prev:
            continue
        if cur["impressions"] < min_impressions and prev["impressions"] < min_impressions:
            continue

        impr_delta  = _pct(cur["impressions"], prev["impressions"])
        click_delta = _pct(cur["clicks"], prev["clicks"])
        pos_delta   = _pct(cur["position"], prev["position"])

        severity = None

        if impr_delta is not None and impr_delta <= DROP_CRITICAL:
            severity = "critical"
        elif impr_delta is not None and impr_delta <= DROP_WARNING:
            severity = "warning"
        elif (impr_delta is None or impr_delta > DROP_WARNING):
            # Impressions are stable or growing — only flag as ctr_issue if clicks
            # dropped AND impressions didn't compensate (i.e. traffic still fell)
            if (click_delta is not None and click_delta <= DROP_CRITICAL
                    and cur["clicks"] < prev["clicks"]):
                severity = "ctr_issue"

        if severity:
            brand_info = _classify_page(page)
            drop = {
                "page":              page,
                "severity":          severity,
                "brand":             brand_info["brand"],
                "tier":              brand_info["tier"],
                "impressions":       cur["impressions"],
                "impressions_prev":  prev["impressions"],
                "impressions_delta": impr_delta,
                "clicks":            cur["clicks"],
                "clicks_prev":       prev["clicks"],
                "clicks_delta":      click_delta,
                "ctr":               cur["ctr"],
                "position":          cur["position"],
                "position_prev":     prev["position"],
                "position_delta":    pos_delta,
            }
            drop["_score"] = _priority_score(drop)
            drops.append(drop)

    drops.sort(key=lambda x: x.get("_score", 0), reverse=True)
    return drops


def _build_brand_summary(drops: list,
                         cur_pages: dict | None = None,
                         prev_pages: dict | None = None) -> dict:
    summary: dict[str, dict] = {}

    # First pass: losses from drop list
    for d in drops:
        brand = d.get("brand")
        if not brand:
            continue
        if brand not in summary:
            summary[brand] = {
                "tier": d.get("tier"), "n_critical": 0, "n_warning": 0,
                "n_ctr_issue": 0, "impressions_lost": 0, "impressions_gained": 0, "pages": [],
            }
        entry = summary[brand]
        sev = d.get("severity", "warning")
        if sev == "critical":
            entry["n_critical"] += 1
        elif sev == "ctr_issue":
            entry["n_ctr_issue"] += 1
        else:
            entry["n_warning"] += 1
        entry["impressions_lost"] += max(0, int(d.get("impressions_prev") or 0) - int(d.get("impressions") or 0))
        path = d["page"].replace(get_site_url(), "")
        if path not in entry["pages"]:
            entry["pages"].append(path)

    # Second pass: gains from all brand pages (pages that aren't in drops)
    if cur_pages and prev_pages:
        base = get_site_url().rstrip("/")
        for bp, info in _build_brand_page_index().items():
            brand = info.get("brand")
            if not brand:
                continue
            full_url = base + bp
            cur  = cur_pages.get(full_url) or cur_pages.get(full_url + "/")
            prev = prev_pages.get(full_url) or prev_pages.get(full_url + "/")
            if not cur or not prev:
                continue
            gain = int(cur.get("impressions", 0)) - int(prev.get("impressions", 0))
            if gain <= 0:
                continue
            if brand not in summary:
                summary[brand] = {
                    "tier": info.get("tier"), "n_critical": 0, "n_warning": 0,
                    "n_ctr_issue": 0, "impressions_lost": 0, "impressions_gained": 0, "pages": [],
                }
            summary[brand]["impressions_gained"] += gain

    # Compute net_change for each brand
    for entry in summary.values():
        entry["net_change"] = entry.get("impressions_gained", 0) - entry.get("impressions_lost", 0)

    return dict(sorted(
        summary.items(),
        key=lambda kv: (0 if kv[1]["tier"] == "top" else 1 if kv[1]["tier"] == "good" else 2,
                        kv[1]["net_change"])  # most negative (worst) first within each tier
    ))


# ── Gemini analysis ───────────────────────────────────────────────────────────

def _analyze_with_gemini(drops: list, brand_summary: dict,
                         period_cur: str, period_prev: str, api_key: str) -> dict:
    try:
        from modules.gemini_insights import _call_gemini_raw, _parse_json_safe
    except Exception as exc:
        return {"_error": f"Import falhou: {exc}", "_ai_ok": False}

    brand_drops = [d for d in drops if d.get("brand")]
    other_drops = sorted([d for d in drops if not d.get("brand")],
                         key=lambda d: int(d.get("impressions") or 0), reverse=True)[:15]

    def _fmt(d: dict) -> str:
        path  = d["page"].replace(get_site_url(), "")
        brand = d.get("brand") or "—"
        tier  = d.get("tier") or "—"
        impr  = int(d.get("impressions") or 0)
        impr_d = d.get("impressions_delta") or 0
        clk_d  = d.get("clicks_delta") or 0
        sev    = d.get("severity", "warning")
        sev_label = "CRITICA" if sev == "critical" else ("CTR_QUEDA" if sev == "ctr_issue" else "AVISO")
        return (f"  [{sev_label}] {path} | marca:{brand}({tier}) | "
                f"impr:{impr:,} | Δimpr:{impr_d:+.0%} | Δcliques:{clk_d:+.0%}")

    brand_sum_lines = "\n".join(
        f"  {b}: tier={v['tier']} | críticas={v['n_critical']} | avisos={v['n_warning']} | "
        f"perdidas≈{v['impressions_lost']:,} | ganhas≈{v.get('impressions_gained',0):,} | "
        f"variação_neta={v.get('net_change', 0):+,}"
        for b, v in brand_summary.items()
    ) or "  (nenhuma marca identificada)"

    # Separate brands that are net-positive vs net-negative for balanced framing
    growing_brands  = [b for b, v in brand_summary.items() if v.get("net_change", 0) > 0]
    declining_brands = [b for b, v in brand_summary.items() if v.get("net_change", 0) < 0]

    prompt = f"""Você é especialista em SEO. Analise as mudanças detectadas no Google Search Console de {get_site_url()}.

PERÍODO ATUAL: {period_cur}
PERÍODO ANTERIOR: {period_prev}

NOTA: Quedas de CTR (CTR_QUEDA) indicam que as impressões se mantiveram ou cresceram mas os cliques caíram — são problemas de snippet/título, NÃO de visibilidade. Só críticas e avisos indicam perda real de impressões.

MARCAS EM CRESCIMENTO: {', '.join(growing_brands) or 'nenhuma'}
MARCAS EM DECLÍNIO: {', '.join(declining_brands) or 'nenhuma'}

VARIAÇÃO POR MARCA (perdidas vs ganhas vs variação neta):
{brand_sum_lines}

PÁGINAS DE MARCA COM QUEDA REAL (impressões caíram):
{chr(10).join(_fmt(d) for d in brand_drops if d.get("severity") in ("critical","warning")) or "  (nenhuma)"}

PÁGINAS COM QUEDA DE CTR APENAS (impressões estáveis/crescendo, só cliques caíram):
{chr(10).join(_fmt(d) for d in brand_drops if d.get("severity") == "ctr_issue") or "  (nenhuma)"}

OUTRAS PÁGINAS DE ALTO IMPACTO:
{chr(10).join(_fmt(d) for d in other_drops) or "  (nenhuma)"}

Responda SOMENTE com JSON válido. Sem markdown. Sem texto fora do JSON.

{{
  "resumo_executivo": "2-3 frases descrevendo padrões principais e impacto geral",
  "padroes": [
    {{"nome": "nome do padrão", "descricao": "...", "marcas_afetadas": ["marca1"], "impacto": "alto|medio|baixo"}}
  ],
  "acoes_prioritarias": [
    {{"prioridade": 1, "acao": "o que fazer exatamente", "paginas": ["/url"], "justificativa": "por que é urgente"}}
  ],
  "paginas_criticas": [
    {{"pagina": "/url", "marca": "nome|null", "motivo": "por que é critica", "acao_imediata": "o que fazer agora"}}
  ],
  "marcas_em_risco": [
    {{"marca": "nome", "tier": "top|good|outro", "resumo": "situação em 1 frase", "prioridade": "alta|media"}}
  ]
}}"""

    try:
        raw    = _call_gemini_raw(prompt, api_key)
        result = _parse_json_safe(raw)
        if result:
            result["_ai_ok"] = True
        return result or {"_error": "JSON vazio", "_ai_ok": False}
    except Exception as exc:
        return {"_error": str(exc), "_ai_ok": False}


# ── Page content fetch & tag suggestions ─────────────────────────────────────

_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control":   "no-cache",
    "Sec-Fetch-Dest":  "document",
    "Sec-Fetch-Mode":  "navigate",
    "Sec-Fetch-Site":  "none",
    "Sec-Fetch-User":  "?1",
    "Upgrade-Insecure-Requests": "1",
}
_warmed_session = None


def _get_warmed_session():
    """Requests session that looks like a real browser and has site cookies."""
    global _warmed_session
    if _warmed_session is not None:
        return _warmed_session
    import requests as _req
    s = _req.Session()
    s.headers.update(_BROWSER_HEADERS)
    # Load homepage to collect Azion/CDN cookies before hitting internal pages
    try:
        s.get(get_site_url() + "/", timeout=12, allow_redirects=True)
        time.sleep(0.8)
    except Exception:
        pass
    _warmed_session = s
    return s


_WAF_SIGNALS = ("azion", "forbidden", "access denied", "cloudflare", "bot protection", "just a moment")


def _fetch_page_content(url: str) -> dict:
    """Fetch current title, H1, meta description using a warmed browser-like session."""
    try:
        from bs4 import BeautifulSoup as _BS
        session = _get_warmed_session()
        r = session.get(url, timeout=15, allow_redirects=True)

        preview = r.text[:600].lower()
        if r.status_code in (403, 429) or any(sig in preview for sig in _WAF_SIGNALS):
            return {
                "url": url, "title": "", "h1": "", "description": "",
                "blocked": True, "status": r.status_code,
            }

        soup = _BS(r.text, "html.parser")
        title_tag = soup.find("title")
        h1_tag    = soup.find("h1")
        desc_meta = soup.find("meta", attrs={"name": "description"})
        return {
            "url":         url,
            "title":       title_tag.get_text(strip=True)[:120] if title_tag else "",
            "h1":          h1_tag.get_text(strip=True)[:120]    if h1_tag    else "",
            "description": (desc_meta.get("content", "") if desc_meta else "")[:200],
            "status":      r.status_code,
            "blocked":     False,
        }
    except Exception as exc:
        return {"url": url, "title": "", "h1": "", "description": "",
                "blocked": False, "error": str(exc)}


def _generate_tag_suggestions(drops_with_content: list, api_key: str) -> list:
    """Batch Gemini call: generate improved title/H1/description for each dropped page."""
    try:
        from modules.gemini_insights import _call_gemini_raw, _parse_json_safe
    except Exception:
        return []

    if not drops_with_content:
        return []

    pages_text = ""
    for p in drops_with_content:
        path    = p.get("url", "").replace(get_site_url(), "")
        brand   = p.get("brand") or "sem marca"
        tier    = p.get("tier") or "—"
        impr_d  = p.get("impressions_delta") or 0
        sev     = p.get("severity", "")
        blocked = p.get("blocked", False)
        title   = p.get("title") or ("(bloqueado pelo WAF — gere com base na URL)" if blocked else "(sem title)")
        h1      = p.get("h1")   or ("(bloqueado)" if blocked else "(sem H1)")
        desc    = (p.get("description") or ("(bloqueado)" if blocked else "(sem description)"))[:150]
        pages_text += (
            f"\nPágina: {path}\n"
            f"Marca: {brand} (tier:{tier}) | Queda: {impr_d:+.0%} | Severidade: {sev}\n"
            f"Title atual: {title}\n"
            f"H1 atual: {h1}\n"
            f"Description atual: {desc}\n"
        )

    prompt = (
        f"Você é especialista em SEO. Analise as páginas de {get_site_url()} que tiveram queda de tráfego orgânico "
        "e sugira versões otimizadas de title, H1 e meta description.\n\n"
        "Regras:\n"
        "- Title: 50-60 caracteres | palavra-chave principal + nome do site\n"
        "- H1: 30-55 caracteres | intenção de busca principal, natural\n"
        "- Description: 140-160 caracteres | benefício claro + CTA suave\n"
        "- Priorize termos com alta intenção de compra/visita\n\n"
        f"PÁGINAS:\n{pages_text}\n\n"
        'Responda com JSON. Sem markdown. Sem texto fora do JSON.\n\n'
        '{"suggestions": [{"page": "/url", "suggested_title": "...", '
        '"suggested_h1": "...", "suggested_description": "...", '
        '"main_issue": "causa provável da queda em 1 frase", "priority": "alta|media"}]}'
    )

    try:
        raw    = _call_gemini_raw(prompt, api_key)
        parsed = _parse_json_safe(raw)
        sug    = parsed.get("suggestions", [])
        return sug if isinstance(sug, list) else []
    except Exception:
        return []


# ── Detailed page enrichment ───────────────────────────────────────────────────

def _enrich_page(session, page_url: str) -> dict:
    cur_start,  cur_end  = _date_range(8, 2)
    prev_start, prev_end = _date_range(15, 9)

    page_filter = [{"dimension": "page", "operator": "equals", "expression": page_url}]

    cur_rows  = _fetch_rows(session, cur_start,  cur_end,  ["query"], filters=page_filter)
    prev_rows = _fetch_rows(session, prev_start, prev_end, ["query"], filters=page_filter)

    cur_queries  = _rows_to_dict(cur_rows)
    prev_queries = _rows_to_dict(prev_rows)

    def _sum(rows):
        return {
            "clicks":      sum(r.get("clicks", 0)      for r in rows.values()),
            "impressions": sum(r.get("impressions", 0)  for r in rows.values()),
        }

    cur_total  = _sum(cur_queries)
    prev_total = _sum(prev_queries)

    return {
        "page":              page_url,
        "period_current":    f"{cur_start} → {cur_end}",
        "period_previous":   f"{prev_start} → {prev_end}",
        "current":           cur_total,
        "previous":          prev_total,
        "delta_impressions": _pct(cur_total["impressions"], prev_total["impressions"]),
        "delta_clicks":      _pct(cur_total["clicks"],      prev_total["clicks"]),
        "top_queries_current":  sorted(cur_queries.items(),  key=lambda x: -x[1]["impressions"])[:10],
        "top_queries_previous": sorted(prev_queries.items(), key=lambda x: -x[1]["impressions"])[:10],
    }


# ── Main entry ────────────────────────────────────────────────────────────────

def _comparison_periods(comparison: str) -> tuple[str, str, str, str]:
    """
    Return (cur_start, cur_end, prev_start, prev_end) for the requested comparison mode.

    week  — last 7 days vs prior 7 days (default, catches recent changes)
    month — last 28 days vs prior 28 days (medium-term trends)
    year  — last 28 days vs same 28 days exactly 1 year ago (YoY / seasonality)
    """
    today = date.today()

    if comparison == "month":
        # last 28 days vs the 28 days before that
        cur_end   = today - timedelta(days=2)
        cur_start = cur_end - timedelta(days=27)
        prev_end  = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=27)

    elif comparison == "year":
        # last 28 days vs same window 365 days ago
        cur_end    = today - timedelta(days=2)
        cur_start  = cur_end - timedelta(days=27)
        prev_end   = cur_end   - timedelta(days=365)
        prev_start = cur_start - timedelta(days=365)

    else:
        # week (default): last 7 days vs prior 7 days
        cur_end    = today - timedelta(days=2)
        cur_start  = cur_end - timedelta(days=6)
        prev_end   = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=6)

    return str(cur_start), str(cur_end), str(prev_start), str(prev_end)


_COMPARISON_LABELS = {
    "week":  "Semana anterior",
    "month": "Mês anterior (28 dias)",
    "year":  "Ano anterior (YoY)",
}


def fetch_raw(limit: int = 500) -> dict:
    """Fetch queries, pages and time-series from GSC API for gsc_analyzer consumption.

    Returns {"queries": [...], "pages": [...], "time_series": [...]} or {"error": str}.
    CTR values are already converted to percentage (0-100 scale).
    """
    from datetime import date, timedelta
    today  = date.today()
    end    = today   - timedelta(days=2)
    start  = end     - timedelta(days=27)
    s, e   = str(start), str(end)

    try:
        session = _build_session(silent=True)
    except Exception as exc:
        return {"error": str(exc)}

    query_rows = _fetch_rows(session, s, e, ["query"], row_limit=limit)
    page_rows  = _fetch_rows(session, s, e, ["page"],  row_limit=limit)
    time_rows  = _fetch_rows(session, s, e, ["date"],  row_limit=500)

    def _pct(v):
        return round(float(v) * 100, 3)

    queries = []
    for row in query_rows:
        keys = row.get("keys", [])
        q = keys[0] if keys else ""
        if not q:
            continue
        queries.append({
            "query":       q,
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         _pct(row.get("ctr", 0)),
            "position":    round(float(row.get("position", 0)), 1),
        })

    pages = []
    for row in page_rows:
        keys = row.get("keys", [])
        url  = keys[0] if keys else ""
        _base = get_site_url()
        path = url.replace(_base, "").replace(_base.replace("://www.", "://"), "") or "/"
        pages.append({
            "page":        path,
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         _pct(row.get("ctr", 0)),
            "position":    round(float(row.get("position", 0)), 1),
        })

    time_series = []
    for row in sorted(time_rows, key=lambda r: (r.get("keys") or [""])[0]):
        keys = row.get("keys", [])
        time_series.append({
            "date":        keys[0] if keys else "",
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         _pct(row.get("ctr", 0)),
            "position":    round(float(row.get("position", 0)), 1),
        })

    return {"queries": queries, "pages": pages, "time_series": time_series}


def get_top_queries(comparison: str = "week", limit: int = 500) -> dict:
    """Fetch top queries from GSC API and return in blog_ideas-compatible format."""
    try:
        session = _build_session()
    except Exception as exc:
        return {"error": str(exc), "top_queries": []}

    cur_start, cur_end, _prev_start, _prev_end = _comparison_periods(comparison)

    rows = _fetch_rows(session, cur_start, cur_end, ["query"], row_limit=limit)
    top_queries = []
    for row in rows:
        keys = row.get("keys", [])
        query = keys[0] if keys else ""
        if not query:
            continue
        top_queries.append({
            "query":       query,
            "impressions": int(row.get("impressions", 0)),
            "clicks":      int(row.get("clicks", 0)),
            "ctr":         round(float(row.get("ctr", 0)) * 100, 3),
            "position":    round(float(row.get("position", 0)), 1),
        })

    return {"top_queries": top_queries}


def get_dashboard_data(period_days: int = 28) -> dict:
    """Fetch GSC performance data only (no Gemini). Fast — cached 1h."""
    import time as _time

    cache_file = _dashboard_cache_file("gsc", period_days)
    try:
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if _time.time() - float(cached.get("_cached_at", 0)) < 3600:
                return cached
    except Exception:
        pass

    # Auth: silent (never opens a browser) + hard 20s timeout so a hung
    # network refresh can never freeze the web request.
    try:
        _bearer = _get_gsc_bearer(timeout=20)
    except Exception as exc:
        return {"error": str(exc)}

    today      = date.today()
    cur_end    = today    - timedelta(days=2)
    cur_start  = cur_end  - timedelta(days=period_days - 1)
    prev_end   = cur_start - timedelta(days=1)
    prev_start = prev_end  - timedelta(days=period_days - 1)

    print(f"    Dashboard: {cur_start} → {cur_end} vs {prev_start} → {prev_end}")

    def _par_fetch(start: str, end: str, dims: list, limit: int) -> list:
        """Thread-safe GSC fetch — own session per thread, direct Bearer header."""
        import requests as _r
        _s = _r.Session()
        _s.trust_env = False
        body = {"startDate": start, "endDate": end,
                "dimensions": dims, "rowLimit": limit, "startRow": 0}
        try:
            resp = _s.post(
                _gsc_query_url(),
                json=body,
                headers={"Authorization": f"Bearer {_bearer}"},
                timeout=15,
            )
            resp.raise_for_status()
            return resp.json().get("rows", [])
        except Exception as _exc:
            print(f"    ! GSC fetch {dims} falhou: {_exc}")
            return []

    import concurrent.futures as _cf
    _ex = _cf.ThreadPoolExecutor(max_workers=4)
    _fs = {
        _ex.submit(_par_fetch, str(cur_start),  str(cur_end),   ["date"],  500): "daily",
        _ex.submit(_par_fetch, str(prev_start), str(prev_end),  ["date"],  500): "prev",
        _ex.submit(_par_fetch, str(cur_start),  str(cur_end),   ["query"],  10): "query",
        _ex.submit(_par_fetch, str(cur_start),  str(cur_end),   ["page"],   10): "pages",
    }
    _done, _pending = _cf.wait(_fs, timeout=25)
    _ex.shutdown(wait=False)  # Never block — hung threads die on their own

    _results = {"daily": [], "prev": [], "query": [], "pages": []}
    for _f in _done:
        try:
            _results[_fs[_f]] = _f.result()
        except Exception:
            pass
    if _pending:
        print(f"    ! {len(_pending)} chamadas GSC excederam 25s — retornando parcial")

    daily_rows = _results["daily"]
    prev_rows  = _results["prev"]
    query_rows = _results["query"]
    page_rows  = _results["pages"]
    print(f"    ok {len(daily_rows)} dias | {len(query_rows)} queries | {len(page_rows)} páginas")

    # ── Time series ───────────────────────────────────────────────────────────────
    time_series = []
    for row in sorted(daily_rows, key=lambda r: (r.get("keys") or [""])[0]):
        keys = row.get("keys", [])
        impr = int(row.get("impressions", 0))
        clks = int(row.get("clicks", 0))
        time_series.append({
            "date":        keys[0] if keys else "",
            "clicks":      clks,
            "impressions": impr,
            "ctr":         round(float(row.get("ctr", 0)) * 100, 2),
            "position":    round(float(row.get("position", 0)), 1),
        })

    cur_clicks = sum(r["clicks"]      for r in time_series)
    cur_impr   = sum(r["impressions"] for r in time_series)
    cur_ctr    = round(cur_clicks / cur_impr * 100, 2) if cur_impr else 0.0
    cur_pos    = round(
        sum(r["position"] * r["impressions"] for r in time_series) / cur_impr, 1
    ) if cur_impr else 0.0

    prev_clicks = sum(int(r.get("clicks", 0))      for r in prev_rows)
    prev_impr   = sum(int(r.get("impressions", 0)) for r in prev_rows)
    prev_ctr    = round(prev_clicks / prev_impr * 100, 2) if prev_impr else 0.0
    prev_pos    = round(
        sum(float(r.get("position", 0)) * int(r.get("impressions", 0)) for r in prev_rows) / prev_impr, 1
    ) if prev_impr else 0.0

    def dpct(cur, prev):
        if not prev:
            return None
        return round((cur - prev) / prev * 100, 1)

    kpis = {
        "clicks":      {"value": cur_clicks, "prev": prev_clicks, "delta": dpct(cur_clicks, prev_clicks)},
        "impressions": {"value": cur_impr,   "prev": prev_impr,   "delta": dpct(cur_impr,   prev_impr)},
        "ctr":         {"value": cur_ctr,    "prev": prev_ctr,    "delta": dpct(cur_ctr,     prev_ctr)},
        "position":    {"value": cur_pos,    "prev": prev_pos,    "delta": dpct(cur_pos,     prev_pos), "invert": True},
    }

    # ── Top queries & pages ────────────────────────────────────────────────────────
    top_queries = []
    for row in query_rows:
        keys = row.get("keys", [])
        top_queries.append({
            "query":       keys[0] if keys else "",
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         round(float(row.get("ctr", 0)) * 100, 1),
            "position":    round(float(row.get("position", 0)), 1),
        })

    top_pages = []
    _site_base = get_site_url()
    for row in page_rows:
        keys = row.get("keys", [])
        url  = keys[0] if keys else ""
        path = url.replace(_site_base, "").replace(
            _site_base.replace("://www.", "://"), "") or "/"
        top_pages.append({
            "page":        path,
            "clicks":      int(row.get("clicks", 0)),
            "impressions": int(row.get("impressions", 0)),
            "ctr":         round(float(row.get("ctr", 0)) * 100, 1),
            "position":    round(float(row.get("position", 0)), 1),
        })

    out = {
        "period_days":  period_days,
        "period":       f"{cur_start} → {cur_end}",
        "kpis":         kpis,
        "time_series":  time_series,
        "top_queries":  top_queries,
        "top_pages":    top_pages,
        "_cached_at":   _time.time(),
    }
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def get_dashboard_ai(period_days: int = 28) -> dict:
    """Generate Gemini analysis for the dashboard. Loads cached GSC data — runs independently."""
    import time as _time

    ai_cache = _dashboard_cache_file("ai", period_days)
    try:
        if ai_cache.exists():
            cached = json.loads(ai_cache.read_text(encoding="utf-8"))
            if _time.time() - float(cached.get("_cached_at", 0)) < 3600:
                return cached
    except Exception:
        pass

    # Load (or fetch) GSC data to build the prompt
    gsc = get_dashboard_data(period_days)
    if gsc.get("error"):
        return {"ai_summary": "", "ai_error": gsc["error"]}

    kpis        = gsc.get("kpis", {})
    top_queries = gsc.get("top_queries", [])
    top_pages   = gsc.get("top_pages", [])

    cur_clicks  = kpis.get("clicks",      {}).get("value", 0)
    cur_impr    = kpis.get("impressions", {}).get("value", 0)
    cur_ctr     = kpis.get("ctr",         {}).get("value", 0.0)
    cur_pos     = kpis.get("position",    {}).get("value", 0.0)
    prev_clicks = kpis.get("clicks",      {}).get("prev", 0)
    prev_impr   = kpis.get("impressions", {}).get("prev", 0)
    prev_ctr    = kpis.get("ctr",         {}).get("prev", 0.0)
    prev_pos    = kpis.get("position",    {}).get("prev", 0.0)

    from config import get_provider_api_key
    gemini_key = get_provider_api_key("gemini") or GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")

    if not gemini_key:
        return {"ai_summary": "", "ai_error": "GEMINI_API_KEY não configurada"}
    if not cur_impr:
        return {"ai_summary": "", "ai_error": "Sem dados de impressões no período"}

    def _d(k):
        d = kpis.get(k, {}).get("delta")
        return f"{d:+.1f}%" if d is not None else "N/A"

    def _fmt_rows(rows, key):
        lines = []
        for i, r in enumerate(rows[:10]):
            label = r.get(key, "")
            lines.append(
                f"  {i+1}. \"{label}\" — {r.get('clicks',0):,} cliques | "
                f"{r.get('impressions',0):,} impr | CTR {r.get('ctr',0):.1f}% | pos {r.get('position',0):.1f}"
            )
        return "\n".join(lines)

    prompt = f"""Você é um analista sênior de SEO. Analise os dados reais do Google Search Console de {get_site_url()} e gere um relatório executivo completo em PT-BR.

== PERÍODO: últimos {period_days} dias vs {period_days} anteriores ==
Cliques:    {cur_clicks:,}  ({_d('clicks')})
Impressões: {cur_impr:,}  ({_d('impressions')})
CTR médio:  {cur_ctr:.2f}%  ({_d('ctr')})
Posição:    {cur_pos:.1f}   ({_d('position')} — menor = melhor)
Período anterior: {prev_clicks:,} cliques | {prev_impr:,} impressões | CTR {prev_ctr:.2f}% | pos {prev_pos:.1f}

== TOP QUERIES ==
{_fmt_rows(top_queries, 'query')}

== TOP PÁGINAS ==
{_fmt_rows(top_pages, 'page')}

== RELATÓRIO ESPERADO ==
Use exatamente estes títulos em negrito (**título**). Cada seção deve citar números reais dos dados acima.

**Visão Geral**
Resume o período em 2-3 frases com os números absolutos e variações percentuais mais relevantes.

**Destaques Positivos**
2-3 bullets com o que está indo bem (métricas crescendo, CTR alto, posição melhorando). Cite números.

**Pontos de Atenção**
2-3 bullets com o que precisa de melhoria. Cite queries ou páginas específicas com seus números problemáticos.

**Oportunidades por Query**
Identifique: (a) queries com muitas impressões mas CTR < 2% — título/description provavelmente fraco; (b) queries na posição 5-15 com volume alto — candidatas a subir para top 3 com ajuste de conteúdo.

**Oportunidades por Página**
Identifique páginas com impressões altas mas CTR abaixo da média do site ({cur_ctr:.2f}%). Cite URL e números.

**Plano de Ação**
3-5 ações concretas e priorizadas. Formato: "1. [Ação específica] — impacto esperado [métrica]". Ex: "Reescrever title da página prioritária para alinhar com a intenção da query — potencial de subir CTR de 1.2% para 3%+".

Responda apenas em PT-BR. Sem texto fora das seções definidas."""

    ai_summary = ""
    ai_error   = ""
    try:
        from modules.gemini_insights import _GEMINI_MODELS, _GEMINI_BASE, _extract_text
        import requests as _req
        _s = _req.Session()
        _s.trust_env = False
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 1200, "temperature": 0.4},
        }
        for _model in _GEMINI_MODELS:
            try:
                _r = _s.post(
                    f"{_GEMINI_BASE}/{_model}:generateContent?key={gemini_key}",
                    json=body, timeout=35,
                )
                if _r.status_code in (429, 503):
                    time.sleep(2)
                    continue
                _r.raise_for_status()
                _text = _extract_text(_r.json())
                if _text:
                    ai_summary = _text.strip()
                    break
                ai_error = f"Resposta vazia ({_model})"
            except Exception as _e:
                ai_error = str(_e)
                continue
    except Exception as exc:
        ai_error = str(exc)

    out = {"ai_summary": ai_summary, "ai_error": ai_error, "_cached_at": _time.time()}
    try:
        ai_cache.parent.mkdir(parents=True, exist_ok=True)
        ai_cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass
    return out


def run(results: dict, scope_urls: list | None = None, use_ai: bool = True,
        comparison: str = "week") -> dict:
    try:
        print("    Autenticando com Google Search Console API...")
        session = _build_session()
        print(f"    Propriedade: {get_gsc_property()}")
    except Exception as exc:
        return {"error": str(exc), "drops": [], "brand_summary": {}, "page_detail": {}}

    cur_start, cur_end, prev_start, prev_end = _comparison_periods(comparison)
    comp_label = _COMPARISON_LABELS.get(comparison, comparison)

    print(f"    Comparação:       {comp_label}")
    print(f"    Período atual:    {cur_start} → {cur_end}")
    print(f"    Período anterior: {prev_start} → {prev_end}")

    print("    Buscando métricas de página...")
    cur_pages  = _rows_to_dict(_fetch_rows(session, cur_start,  cur_end,  ["page"], row_limit=1000))
    prev_pages = _rows_to_dict(_fetch_rows(session, prev_start, prev_end, ["page"], row_limit=1000))

    drops  = _detect_page_drops(cur_pages, prev_pages)
    n_crit = sum(1 for d in drops if d["severity"] == "critical")
    n_brand = sum(1 for d in drops if d.get("brand"))
    print(f"    ok {len(drops)} quedas ({n_crit} críticas, {n_brand} em páginas de marca)")

    brand_summary = _build_brand_summary(drops, cur_pages, prev_pages)
    for brand, info in list(brand_summary.items())[:5]:
        net = info.get("net_change", 0)
        print(f"       {brand} [{info['tier']}]: {info['n_critical']} críticas, "
              f"perdidas≈{info['impressions_lost']:,} ganhas≈{info.get('impressions_gained',0):,} "
              f"neta={net:+,}")

    gemini_key = GEMINI_API_KEY or os.environ.get("GEMINI_API_KEY", "")

    # ── Fetch live page content for top affected pages ─────────────────────────
    page_content: list = []
    tag_suggestions: list = []
    if use_ai and drops and gemini_key:
        # Brand pages first, then high-impression non-brand pages
        to_audit = [d for d in drops if d.get("tier") in ("top", "good")]
        extras   = [d for d in drops if not d.get("brand") and int(d.get("impressions") or 0) >= 2000]
        to_audit = (to_audit + extras)[:15]

        if to_audit:
            print(f"    Auditando conteúdo de {len(to_audit)} páginas...")
            from concurrent.futures import ThreadPoolExecutor, as_completed
            with ThreadPoolExecutor(max_workers=5) as ex:
                futures = {ex.submit(_fetch_page_content, d["page"]): d for d in to_audit}
                for future in as_completed(futures):
                    drop    = futures[future]
                    content = future.result()
                    content.update({
                        "brand":             drop.get("brand"),
                        "tier":              drop.get("tier"),
                        "severity":          drop.get("severity"),
                        "impressions":       drop.get("impressions"),
                        "impressions_delta": drop.get("impressions_delta"),
                        "clicks_delta":      drop.get("clicks_delta"),
                    })
                    page_content.append(content)

            print("    Gerando sugestões de tags com Gemini...")
            tag_suggestions = _generate_tag_suggestions(page_content, gemini_key)
            print(f"    ok {len(tag_suggestions)} sugestões de tags geradas")

    # ── Strategic drop analysis via Gemini ─────────────────────────────────────
    ai_analysis: dict = {}
    if use_ai and drops and gemini_key:
        print("    Analisando padrões com Gemini...")
        ai_analysis = _analyze_with_gemini(
            drops, brand_summary,
            f"{cur_start} → {cur_end}", f"{prev_start} → {prev_end}",
            gemini_key,
        )
        if ai_analysis.get("_ai_ok"):
            print("    ok análise Gemini concluída")
        else:
            print(f"    ! Gemini indisponível: {ai_analysis.get('_error', '')}")
    elif use_ai and not gemini_key:
        print("    ! GEMINI_API_KEY não configurada — análise IA desativada")

    page_detail = {}
    if scope_urls:
        for url in scope_urls[:10]:
            full = url if url.startswith("http") else get_site_url() + url
            print(f"    Detalhando {full}...")
            try:
                page_detail[full] = _enrich_page(session, full)
            except Exception as exc:
                page_detail[full] = {"error": str(exc)}

    return {
        "drops":            drops,
        "brand_summary":    brand_summary,
        "ai_analysis":      ai_analysis,
        "page_content":     page_content,
        "tag_suggestions":  tag_suggestions,
        "page_detail":      page_detail,
        "period_current":   f"{cur_start} → {cur_end}",
        "period_previous":  f"{prev_start} → {prev_end}",
        "comparison":       comparison,
        "comparison_label": comp_label,
        "total_pages_cur":  len(cur_pages),
        "total_pages_prev": len(prev_pages),
    }
