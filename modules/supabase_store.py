"""
Supabase persistence layer for the SEO audit foundation.

Run `supabase/schema.sql` in the Supabase SQL Editor before saving results.
"""

from __future__ import annotations

import math
import os
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from supabase import Client, create_client

from config import SITE_URL

load_dotenv()


def _client() -> Client:
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY")
    if not url or not key:
        raise RuntimeError("Configure SUPABASE_URL e SUPABASE_KEY no .env.")
    return create_client(url, key)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, tuple):
        return [_clean(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if hasattr(value, "item"):
        try:
            return _clean(value.item())
        except Exception:
            pass
    return value


def _path(url: str) -> str:
    full = url if url.startswith("http") else SITE_URL + url
    return urlparse(full).path or "/"


def _full_url(url: str) -> str:
    return url if url.startswith("http") else SITE_URL + url


def _url_type(path: str) -> str:
    clean = path.strip("/")
    if not clean:
        return "home"
    if any(token in clean for token in ["melhores", "como", "guia", "diferenca", "estilos"]):
        return "blog"
    if "/" in clean:
        return "product"
    return "category"


def _upsert_site(sb: Client, name: str, base_url: str) -> str:
    payload = {"name": name, "base_url": base_url, "updated_at": _now()}
    res = sb.table("sites").upsert(payload, on_conflict="base_url").execute()
    if res.data:
        return res.data[0]["id"]
    found = sb.table("sites").select("id").eq("base_url", base_url).single().execute()
    return found.data["id"]


def _upsert_url(sb: Client, site_id: str, url: str, is_priority: bool = False) -> str | None:
    full = _full_url(url)
    path = _path(full)
    payload = {
        "site_id": site_id,
        "url": full,
        "path": path,
        "url_type": _url_type(path),
        "is_priority": is_priority,
        "last_seen_at": _now(),
    }
    res = sb.table("urls").upsert(payload, on_conflict="site_id,url").execute()
    return res.data[0]["id"] if res.data else None


def _create_run(sb: Client, site_id: str, run_type: str, scope: list, summary: dict) -> str:
    payload = {
        "site_id": site_id,
        "run_type": run_type,
        "scope": _clean(scope),
        "status": "completed",
        "finished_at": _now(),
        "summary": _clean(summary),
    }
    res = sb.table("crawl_runs").insert(payload).execute()
    return res.data[0]["id"]


def _summary(results: dict) -> dict:
    return {
        "gsc_queries": len(results.get("gsc", {}).get("top_queries", [])),
        "gsc_page_ctr_opportunities": len(results.get("gsc", {}).get("low_ctr_pages", [])),
        "gsc_content_opportunities": len(results.get("gsc", {}).get("content_opps", [])),
        "onpage_pages": len(results.get("onpage", [])),
        "duplicate_issues": results.get("duplicates", {}).get("total_issues", 0),
        "broken_links": len(results.get("broken_links", {}).get("broken", [])),
        "cluster_issues": len([
            c for c in results.get("internal_links", {}).get("cluster_analysis", [])
            if c.get("health_score", 100) < 80
        ]),
        "pagespeed_results": len(results.get("pagespeed", [])),
        "sitemap_issues": len(results.get("sitemap", {}).get("issues", [])),
        "indexability_issues": len(results.get("indexability", {}).get("issues", [])),
        "snippet_issues": len(results.get("snippets", {}).get("issues", [])),
        "content_gaps": len(results.get("content_gap", {}).get("gaps", [])),
        "product_issues": len(results.get("products", {}).get("issues", [])),
        "link_suggestions": len(results.get("link_suggestions", {}).get("suggestions", [])),
        "recommendations": len(results.get("backlog", [])),
    }


def _save_gsc(sb: Client, site_id: str, run_id: str, gsc: dict) -> None:
    query_rows = []
    for kind in ["top_queries", "ctr_opps", "content_opps", "pos_opps", "quick_wins"]:
        for row in gsc.get(kind, []):
            query_rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "query": row.get("query", ""),
                "clicks": int(row.get("clicks", 0) or 0),
                "impressions": int(row.get("impressions", 0) or 0),
                "ctr": row.get("ctr"),
                "position": row.get("position"),
                "opportunity_type": kind,
                "potential_clicks": row.get("potential_clicks"),
                "raw": _clean(row),
            })
    if query_rows:
        sb.table("gsc_queries").insert(query_rows).execute()

    page_rows = []
    for kind in ["top_pages", "low_ctr_pages", "cannibalization"]:
        for row in gsc.get(kind, []):
            page = row.get("page", "")
            url_id = _upsert_url(sb, site_id, page) if page else None
            page_rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "page": page,
                "clicks": int(row.get("clicks", 0) or 0),
                "impressions": int(row.get("impressions", 0) or 0),
                "ctr": row.get("ctr"),
                "position": row.get("position"),
                "opportunity_type": kind,
                "potential_clicks": row.get("potential_clicks"),
                "raw": _clean(row),
            })
    if page_rows:
        sb.table("gsc_pages").insert(page_rows).execute()


def _save_onpage(sb: Client, site_id: str, run_id: str, pages: list, scope: list) -> None:
    priority = {_full_url(u) for u in scope}
    snapshots = []
    issue_rows = []
    for page in pages or []:
        url = page.get("url", "")
        url_id = _upsert_url(sb, site_id, url, is_priority=_full_url(url) in priority)
        snapshots.append({
            "run_id": run_id,
            "site_id": site_id,
            "url_id": url_id,
            "url": url,
            "final_url": page.get("final_url"),
            "status_code": page.get("status"),
            "redirected": bool(page.get("redirect")),
            "title": page.get("title"),
            "title_length": page.get("title_length"),
            "meta_description": page.get("description"),
            "description_length": page.get("description_length"),
            "h1_count": page.get("h1_count"),
            "h1_texts": _clean(page.get("h1_texts", [])),
            "h2_count": page.get("h2_count"),
            "word_count": page.get("word_count"),
            "canonical": page.get("canonical"),
            "schemas": _clean(page.get("schemas", [])),
            "images_total": page.get("images_total"),
            "images_no_alt": page.get("images_no_alt"),
            "score": page.get("score"),
            "grade": page.get("grade"),
            "issues": _clean(page.get("issues", [])),
            "warnings": _clean(page.get("warnings", [])),
            "raw": _clean(page),
        })
        for message in page.get("issues", []):
            issue_rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "source": "onpage",
                "severity": "high",
                "issue_type": "onpage_issue",
                "title": message,
                "target": url,
                "evidence": _clean(page),
            })
        for message in page.get("warnings", []):
            issue_rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "source": "onpage",
                "severity": "medium",
                "issue_type": "onpage_warning",
                "title": message,
                "target": url,
                "evidence": _clean(page),
            })
    if snapshots:
        sb.table("page_snapshots").insert(snapshots).execute()
    if issue_rows:
        sb.table("issues").insert(issue_rows).execute()


def _save_link_issues(sb: Client, site_id: str, run_id: str, links: dict) -> None:
    rows = []
    for row in links.get("broken", []):
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "broken_links",
            "severity": "high",
            "issue_type": "broken_link",
            "title": f"Link quebrado: {row.get('url')}",
            "description": f"Status {row.get('status')}",
            "target": row.get("url"),
            "evidence": _clean(row),
        })
    for row in links.get("orphans", []):
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "internal_links",
            "severity": "medium",
            "issue_type": "orphan_page",
            "title": f"Pagina com poucos links internos: {row.get('url')}",
            "target": row.get("url"),
            "evidence": _clean(row),
        })
    if rows:
        sb.table("issues").insert(rows).execute()


def _save_duplicate_issues(sb: Client, site_id: str, run_id: str, duplicates: dict) -> None:
    rows = []
    mappings = [
        ("duplicate_titles", "duplicate_title", "Title duplicado", "medium"),
        ("duplicate_descs", "duplicate_description", "Description duplicada", "medium"),
        ("duplicate_h1s", "duplicate_h1", "H1 duplicado", "medium"),
        ("keyword_issues", "suspicious_keywords", "Meta keywords suspeitas", "low"),
    ]
    for key, issue_type, title, severity in mappings:
        for row in duplicates.get(key, []):
            target = row.get("url") or row.get("url_a") or ""
            rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "source": "duplicates",
                "severity": severity,
                "issue_type": issue_type,
                "title": title,
                "target": target,
                "evidence": _clean(row),
            })

    for key, issue_type, title in [
        ("missing_titles", "missing_title", "Meta title ausente"),
        ("missing_descs", "missing_description", "Meta description ausente"),
        ("missing_h1s", "missing_h1", "H1 ausente"),
    ]:
        for url in duplicates.get(key, []):
            url_id = _upsert_url(sb, site_id, url)
            rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "source": "duplicates",
                "severity": "high",
                "issue_type": issue_type,
                "title": title,
                "target": url,
                "evidence": {"url": url},
            })
    if rows:
        sb.table("issues").insert(rows).execute()


def _save_cluster_issues(sb: Client, site_id: str, run_id: str, internal_links: dict) -> None:
    rows = []
    for cluster in internal_links.get("cluster_analysis", []):
        health = int(cluster.get("health_score", 100) or 100)
        if health >= 80:
            continue
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "internal_links",
            "severity": "high" if health < 60 else "medium",
            "issue_type": "weak_cluster",
            "title": f"Cluster com saude baixa: {cluster.get('brand')}",
            "description": f"Health score {health}/100",
            "target": cluster.get("pillar"),
            "evidence": _clean(cluster),
        })
    for row in internal_links.get("orphans", []):
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "internal_links",
            "severity": "medium",
            "issue_type": "orphan_page",
            "title": f"Pagina orfa: {row.get('url')}",
            "target": row.get("url"),
            "evidence": _clean(row),
        })
    if rows:
        sb.table("issues").insert(rows).execute()


def _save_pagespeed_issues(sb: Client, site_id: str, run_id: str, pagespeed: list) -> None:
    rows = []
    for row in pagespeed or []:
        if row.get("error"):
            rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "source": "pagespeed",
                "severity": "medium",
                "issue_type": "pagespeed_error",
                "title": "Falha ao auditar PageSpeed",
                "description": row.get("error"),
                "target": row.get("url"),
                "evidence": _clean(row),
            })
            continue
        if row.get("strategy") != "mobile":
            continue
        perf = row.get("performance_score")
        if perf is not None and perf < 75:
            url_id = _upsert_url(sb, site_id, row.get("url", ""))
            rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "source": "pagespeed",
                "severity": "high" if perf < 50 else "medium",
                "issue_type": "low_mobile_performance",
                "title": f"Performance mobile baixa: {perf}/100",
                "description": "; ".join(row.get("issues", [])),
                "target": row.get("url"),
                "evidence": _clean(row),
            })
    if rows:
        sb.table("issues").insert(rows).execute()


def _save_generic_module_issues(sb: Client, site_id: str, run_id: str, results: dict) -> None:
    rows = []
    configs = [
        ("sitemap", "sitemap", "medium"),
        ("indexability", "indexability", "high"),
        ("snippets", "snippets", "medium"),
        ("products", "products", "medium"),
    ]
    for result_key, source, default_severity in configs:
        for row in results.get(result_key, {}).get("issues", []):
            target = row.get("url") or row.get("target") or row.get("page") or ""
            url_id = _upsert_url(sb, site_id, target) if str(target).startswith(("http", "/")) else None
            rows.append({
                "run_id": run_id,
                "site_id": site_id,
                "url_id": url_id,
                "source": source,
                "severity": default_severity,
                "issue_type": row.get("type") or source,
                "title": row.get("title") or row.get("type") or f"Issue em {source}",
                "description": row.get("reason") or "; ".join(row.get("issues", [])) if isinstance(row.get("issues"), list) else "",
                "target": str(target),
                "evidence": _clean(row),
            })

    for row in results.get("content_gap", {}).get("gaps", []):
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "content_gap",
            "severity": "medium",
            "issue_type": "content_gap",
            "title": f"Gap de conteudo: {row.get('entity')}",
            "description": row.get("suggested_action"),
            "target": row.get("entity"),
            "evidence": _clean(row),
        })

    for row in results.get("link_suggestions", {}).get("suggestions", []):
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "source": "link_suggestions",
            "severity": "low",
            "issue_type": "internal_link_suggestion",
            "title": "Sugestao de link interno",
            "description": row.get("reason"),
            "target": row.get("target"),
            "evidence": _clean(row),
        })

    if rows:
        sb.table("issues").insert(rows).execute()


def _save_recommendations(sb: Client, site_id: str, run_id: str, items: list) -> None:
    # Remove backlog items still in 'open' status — they are stale from the previous run.
    # Items already moved to todo/doing/done by the user are preserved.
    sb.table("recommendations").delete().eq("site_id", site_id).eq("status", "open").execute()

    rows = []
    for item in items or []:
        target = item.get("target", "")
        url_id = _upsert_url(sb, site_id, target) if str(target).startswith(("http", "/")) else None
        rows.append({
            "run_id": run_id,
            "site_id": site_id,
            "url_id": url_id,
            "source": item.get("source"),
            "action": item.get("action"),
            "target": str(target),
            "reason": item.get("reason"),
            "impact": item.get("impact"),
            "confidence": item.get("confidence"),
            "effort": item.get("effort"),
            "priority": item.get("priority"),
            "owner": item.get("owner"),
            "evidence": _clean(item.get("evidence", {})),
            "status": "open",
        })
    if rows:
        sb.table("recommendations").insert(rows).execute()


def save_content_changes(items: list) -> int:
    sb = _client()
    site_id = _upsert_site(sb, "Secret Outlet", SITE_URL)
    rows = []
    for item in items or []:
        url = item.get("_url") or item.get("url") or ""
        url_id = _upsert_url(sb, site_id, url) if url else None
        rows.append({
            "site_id": site_id,
            "url_id": url_id,
            "url": _full_url(url) if url else "",
            "provider": item.get("_provider"),
            "status": item.get("status", "pending"),
            "meta_title": item.get("meta_title"),
            "meta_description": item.get("meta_description"),
            "meta_keywords": item.get("meta_keywords"),
            "h1": item.get("h1"),
            "description_html": item.get("description_html"),
            "generated_at": item.get("_generated_at"),
            "published_at": item.get("_published_at"),
            "raw": _clean(item),
        })
    if rows:
        sb.table("content_changes").insert(rows).execute()
    return len(rows)


def save_tag_suggestions(items: list) -> int:
    """Save GSC tag-suggestion items to the recommendations table (Kanban)."""
    if not items:
        return 0
    sb      = _client()
    site_id = _upsert_site(sb, "Secret Outlet", SITE_URL)
    run_id  = _create_run(sb, site_id, "gsc-api", [], {"tag_suggestions": len(items)})

    # Remove stale open tag-suggestion tasks so we don't accumulate duplicates
    sb.table("recommendations").delete().eq("site_id", site_id).eq("source", "gsc_tags").eq("status", "open").execute()

    rows = []
    for item in items:
        target = str(item.get("target") or "")
        url_id = _upsert_url(sb, site_id, target) if target.startswith(("/", "http")) else None
        rows.append({
            "run_id":     run_id,
            "site_id":    site_id,
            "url_id":     url_id,
            "source":     "gsc_tags",
            "action":     item.get("action"),
            "target":     target,
            "reason":     item.get("reason"),
            "impact":     item.get("impact"),
            "confidence": item.get("confidence"),
            "effort":     item.get("effort"),
            "priority":   item.get("priority"),
            "owner":      item.get("owner", "SEO"),
            "evidence":   _clean(item.get("evidence", {})),
            "status":     "open",
        })
    if rows:
        sb.table("recommendations").insert(rows).execute()
    return len(rows)


def save_blog_ideas(ideas: list) -> int:
    sb = _client()
    site_id = _upsert_site(sb, "Secret Outlet", SITE_URL)
    rows = []
    for idea in ideas or []:
        url = idea.get("url") or f"/{idea.get('url_slug', '')}"
        url_id = _upsert_url(sb, site_id, url) if url else None
        rows.append({
            "site_id": site_id,
            "url_id": url_id,
            "url": _full_url(url) if url else "",
            "provider": idea.get("provider", "query_suggester"),
            "status": idea.get("status", "idea"),
            "meta_title": idea.get("meta_title"),
            "meta_description": idea.get("meta_description"),
            "h1": idea.get("h1"),
            "description_html": "\n".join(idea.get("sections", [])),
            "generated_at": idea.get("generated_at"),
            "raw": _clean(idea),
        })
    if rows:
        sb.table("content_changes").insert(rows).execute()
    return len(rows)


def save_audit_results(results: dict, run_type: str, scope: list) -> str:
    sb = _client()
    site_id = _upsert_site(sb, "Secret Outlet", SITE_URL)
    run_id = _create_run(sb, site_id, run_type, scope, _summary(results))

    # Clear stale open issues before inserting the fresh set from this run.
    sb.table("issues").delete().eq("site_id", site_id).eq("status", "open").execute()

    if results.get("gsc"):
        _save_gsc(sb, site_id, run_id, results["gsc"])
    if results.get("onpage"):
        _save_onpage(sb, site_id, run_id, results["onpage"], scope)
    if results.get("broken_links"):
        _save_link_issues(sb, site_id, run_id, results["broken_links"])
    if results.get("duplicates"):
        _save_duplicate_issues(sb, site_id, run_id, results["duplicates"])
    if results.get("internal_links"):
        _save_cluster_issues(sb, site_id, run_id, results["internal_links"])
    if results.get("pagespeed"):
        _save_pagespeed_issues(sb, site_id, run_id, results["pagespeed"])
    _save_generic_module_issues(sb, site_id, run_id, results)
    if results.get("backlog"):
        _save_recommendations(sb, site_id, run_id, results["backlog"])

    return run_id
