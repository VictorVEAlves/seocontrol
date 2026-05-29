"""
Prioritized SEO backlog.

Turns findings from multiple audit modules into a single action list ordered by
expected impact, confidence and effort. This is intentionally lightweight so it
can evolve into a persistent task system later.
"""

from __future__ import annotations

import math
from config import get_site_url


def _priority(impact: float, confidence: float, effort: float) -> float:
    effort = max(effort, 1.0)
    return round((impact * (confidence / 100)) / effort, 1)


def _item(
    *,
    source: str,
    action: str,
    target: str,
    reason: str,
    impact: float,
    confidence: float,
    effort: float,
    owner: str = "SEO",
    evidence: dict | None = None,
) -> dict:
    return {
        "source": source,
        "action": action,
        "target": target,
        "reason": reason,
        "impact": impact,
        "confidence": confidence,
        "effort": effort,
        "priority": _priority(impact, confidence, effort),
        "owner": owner,
        "evidence": evidence or {},
    }


def _impact_from_impressions(impressions: int, base: float = 15.0) -> float:
    """Log-scaled impact: 10k impr→35, 50k→55, 150k→70, 300k→80, 500k+→88."""
    if impressions <= 0:
        return base
    return min(88, base + math.log(impressions + 1, 2) * 3.8)


def _confidence_from_volume(impressions: int, base: float = 60.0) -> float:
    """More data → higher confidence, capped at 90."""
    return min(90, base + min(impressions / 8000, 28))


def _from_gsc(gsc: dict) -> list:
    items = []

    for row in gsc.get("content_opps", [])[:30]:
        query       = row.get("query", "")
        impressions = int(row.get("impressions", 0) or 0)
        position    = float(row.get("position", 0) or 0)
        score       = float(row.get("opportunity_score", 0) or 0)
        # Impact scales with both volume and opportunity score
        impact      = min(88, _impact_from_impressions(impressions) * 0.6 + score * 0.4)
        confidence  = _confidence_from_volume(impressions)
        items.append(_item(
            source="gsc",
            action=row.get("content_action", "Avaliar oportunidade de conteudo para query"),
            target=query,
            reason=(
                f"Query com {impressions:,} impressoes, posicao {position:.1f} "
                f"e tipo {row.get('query_type', 'query')}."
            ),
            impact=impact,
            confidence=confidence,
            effort=4,
            evidence=row,
        ))

    for row in gsc.get("quick_wins", [])[:25]:
        query       = row.get("query", "")
        impressions = int(row.get("impressions", 0) or 0)
        position    = float(row.get("position", 0) or 0)
        # Position boost: pos 3 = +27 pts, pos 9 = +9 pts, pos 12+ = 0
        pos_factor  = max(0, (12 - position)) * 3
        impact      = min(88, _impact_from_impressions(impressions, base=10) + pos_factor)
        confidence  = _confidence_from_volume(impressions)
        # Harder to improve pages already ranking below pos 8
        effort      = 3 if position <= 7 else 4 if position <= 9 else 5
        items.append(_item(
            source="gsc",
            action=row.get("action", "Otimizar pagina para ganhar posicoes"),
            target=query,
            reason=f"Query em posicao {position:.1f} com {impressions:,} impressoes.",
            impact=impact,
            confidence=confidence,
            effort=effort,
            evidence=row,
        ))

    for row in gsc.get("low_ctr_pages", [])[:20]:
        page        = row.get("page", "")
        potential   = int(row.get("potential_clicks", 0) or 0)
        impressions = int(row.get("impressions", 0) or 0)
        # Non-linear: +4000 clicks ≠ same impact as +500 clicks
        if potential >= 3000:
            impact = 88
        elif potential >= 1000:
            impact = 62 + (potential - 1000) / 100
        elif potential >= 300:
            impact = 38 + (potential - 300) / 23
        else:
            impact = 20 + potential / 21
        impact     = min(88, impact)
        confidence = _confidence_from_volume(impressions, base=65)
        items.append(_item(
            source="gsc",
            action="Reescrever title e meta description para aumentar CTR",
            target=page,
            reason=f"Potencial de +{potential:,} cliques com CTR de benchmark. {impressions:,} impressoes.",
            impact=impact,
            confidence=confidence,
            effort=2,
            evidence=row,
        ))

    return items


def _from_onpage(onpage: list) -> list:
    items = []
    for page in onpage or []:
        issues = page.get("issues", [])
        warnings = page.get("warnings", [])
        if not issues and not warnings:
            continue

        severity = len(issues) * 15 + len(warnings) * 5
        impact = min(90, 20 + severity)
        effort = 2 + min(4, len(issues) + len(warnings) / 2)
        target = page.get("url", "")
        reason = "; ".join((issues + warnings)[:3])
        items.append(_item(
            source="onpage",
            action="Corrigir problemas on-page da pagina",
            target=target,
            reason=reason,
            impact=impact,
            confidence=80,
            effort=effort,
            evidence={
                "grade": page.get("grade"),
                "score": page.get("score"),
                "issues": issues,
                "warnings": warnings,
            },
        ))
    return items


def _from_broken_links(links: dict) -> list:
    items = []
    for row in links.get("broken", [])[:30]:
        sources = int(row.get("total_sources", 0) or 0)
        impact = min(95, 35 + sources * 8)
        items.append(_item(
            source="links",
            action="Corrigir link quebrado ou criar redirect 301",
            target=row.get("url", ""),
            reason=f"Status {row.get('status')} encontrado em {sources} origem(ns).",
            impact=impact,
            confidence=95,
            effort=2,
            owner="Dev/SEO",
            evidence=row,
        ))
    return items


def _from_clusters(internal_links: dict) -> list:
    items = []
    for cluster in internal_links.get("cluster_analysis", []):
        health = int(cluster.get("health_score", 100) or 100)
        if health >= 80:
            continue

        missing = (
            cluster.get("missing_from_pillar", [])
            + cluster.get("missing_to_pillar", [])
        )
        impact = min(85, 30 + (100 - health) * 0.8 + len(missing) * 3)
        items.append(_item(
            source="internal_links",
            action="Reforcar links internos bidirecionais do cluster",
            target=cluster.get("brand", ""),
            reason=f"Saude do cluster em {health}/100; {len(missing)} link(s) ausente(s).",
            impact=impact,
            confidence=75,
            effort=max(2, min(8, len(missing))),
            evidence=cluster,
        ))
    return items


def _from_pagespeed(pagespeed: list) -> list:
    items = []
    for row in pagespeed or []:
        if row.get("strategy") != "mobile" or row.get("error"):
            continue
        perf = row.get("performance_score")
        if perf is None or perf >= 75:
            continue
        issues = row.get("issues", [])
        impact = min(90, 30 + (75 - perf))
        items.append(_item(
            source="pagespeed",
            action="Melhorar Core Web Vitals mobile",
            target=row.get("url", ""),
            reason="; ".join(issues) or f"Performance mobile em {perf}/100.",
            impact=impact,
            confidence=70,
            effort=8,
            owner="Dev",
            evidence=row,
        ))
    return items


def _from_sitemap(sitemap: dict) -> list:
    items = []
    for url in sitemap.get("missing_priority", []):
        items.append(_item(
            source="sitemap",
            action="Adicionar pagina prioritaria ao sitemap",
            target=url,
            reason="Pagina prioritaria nao encontrada no sitemap.",
            impact=55,
            confidence=85,
            effort=2,
            owner="SEO/Dev",
            evidence={"url": url},
        ))
    for url in sitemap.get("blocked_priority", []):
        items.append(_item(
            source="robots",
            action="Revisar bloqueio no robots.txt",
            target=url,
            reason="Pagina prioritaria parece bloqueada por regra Disallow.",
            impact=85,
            confidence=90,
            effort=2,
            owner="Dev",
            evidence={"url": url},
        ))
    for row in sitemap.get("canonical_issues", []):
        items.append(_item(
            source="sitemap",
            action="Corrigir canonical inconsistente",
            target=row.get("url", ""),
            reason=f"Canonical aponta para {row.get('canonical')}.",
            impact=70,
            confidence=85,
            effort=3,
            owner="SEO/Dev",
            evidence=row,
        ))
    return items


def _from_indexability(indexability: dict) -> list:
    items = []
    for row in indexability.get("issues", []):
        items.append(_item(
            source="indexability",
            action="Corrigir risco de indexabilidade",
            target=row.get("url", ""),
            reason="; ".join(row.get("issues", [])[:3]),
            impact=80 if not row.get("indexable") else 45,
            confidence=90,
            effort=3,
            owner="SEO/Dev",
            evidence=row,
        ))
    return items


def _from_snippets(snippets: dict) -> list:
    items = []
    for row in snippets.get("issues", []):
        items.append(_item(
            source="snippets",
            action="Ajustar title/description publicado",
            target=row.get("url", ""),
            reason=f"Title {row.get('title_status')} | Description {row.get('description_status')}.",
            impact=50,
            confidence=80,
            effort=2,
            evidence=row,
        ))
    return items


def _from_content_gap(content_gap: dict) -> list:
    items = []
    for row in content_gap.get("gaps", [])[:20]:
        items.append(_item(
            source="content_gap",
            action=row.get("suggested_action", "Criar ou otimizar conteudo"),
            target=row.get("entity", ""),
            reason=f"{row.get('impressions', 0)} impressoes em intent {row.get('intent')}.",
            impact=min(90, 30 + int(row.get("impressions", 0) or 0) / 500),
            confidence=70,
            effort=5,
            evidence=row,
        ))
    return items


def _from_products(products: dict) -> list:
    items = []
    for row in products.get("issues", []):
        items.append(_item(
            source="products",
            action="Corrigir sinais de categoria/produtos",
            target=row.get("url", ""),
            reason="; ".join(row.get("issues", [])[:3]),
            impact=55,
            confidence=65,
            effort=4,
            owner="SEO/Dev",
            evidence=row,
        ))
    return items


def _from_ai_insights(insights: dict) -> list:
    """Convert Gemini-generated tasks into backlog items."""
    if not insights or not insights.get("_ai_enhanced"):
        return []
    items = []
    for task in insights.get("tasks", []):
        impact     = max(1, min(100, int(task.get("impact")     or 50)))
        confidence = max(1, min(100, int(task.get("confidence") or 70)))
        effort     = max(1, min(10,  int(task.get("effort")     or 3)))
        items.append({
            "source":     "ai_insights",
            "action":     task.get("action", ""),
            "target":     task.get("target", ""),
            "reason":     task.get("reason", ""),
            "impact":     impact,
            "confidence": confidence,
            "effort":     effort,
            "priority":   _priority(impact, confidence, effort),
            "owner":      task.get("owner", "SEO"),
            "brand_tier": task.get("brand_tier", "all"),
            "evidence":   {"ai_generated": True},
        })
    return items


def _from_gsc_drops(gsc_api: dict) -> list:
    """High-priority items for pages with significant performance drops."""
    items = []
    for drop in gsc_api.get("drops", [])[:20]:
        page      = drop.get("page", "")
        impr      = int(drop.get("impressions", 0))
        impr_d    = drop.get("impressions_delta") or 0
        click_d   = drop.get("clicks_delta") or 0
        severity  = drop.get("severity", "warning")

        # High-volume drops are more impactful
        impact = min(95, 50 + _impact_from_impressions(impr, base=0) * 0.5)
        impact = impact + 10 if severity == "critical" else impact
        impact = min(95, impact)
        effort = 5  # Needs investigation + fix — unknown cause

        reason = (
            f"Queda de {abs(impr_d):.0%} em impressões"
            + (f" e {abs(click_d):.0%} em cliques" if click_d < -0.05 else "")
            + f" vs semana anterior ({impr:,} impressões atuais)."
        )
        items.append(_item(
            source="gsc_api",
            action="Investigar e corrigir queda de performance",
            target=page,
            reason=reason,
            impact=round(impact, 1),
            confidence=90,
            effort=effort,
            evidence=drop,
        ))
    return items


def _from_link_suggestions(link_suggestions: dict) -> list:
    items = []
    for row in link_suggestions.get("suggestions", [])[:50]:
        items.append(_item(
            source="link_suggestions",
            action="Adicionar link interno sugerido",
            target=row.get("target", ""),
            reason=f"{row.get('source')} -> {row.get('target')} | anchor: {row.get('anchor')}",
            impact=45,
            confidence=75,
            effort=1,
            evidence=row,
        ))
    return items


def run(results: dict, limit: int = 20) -> list:
    items = []
    items.extend(_from_gsc(results.get("gsc", {})))
    items.extend(_from_onpage(results.get("onpage", [])))
    items.extend(_from_broken_links(results.get("broken_links", {})))
    items.extend(_from_clusters(results.get("internal_links", {})))
    items.extend(_from_pagespeed(results.get("pagespeed", [])))
    items.extend(_from_sitemap(results.get("sitemap", {})))
    items.extend(_from_indexability(results.get("indexability", {})))
    items.extend(_from_snippets(results.get("snippets", {})))
    items.extend(_from_content_gap(results.get("content_gap", {})))
    items.extend(_from_products(results.get("products", {})))
    items.extend(_from_link_suggestions(results.get("link_suggestions", {})))
    items.extend(_from_gsc_drops(results.get("gsc_api", {})))
    # AI insights go last — they enrich the list without overriding data-driven items
    items.extend(_from_ai_insights(results.get("ai_insights", {})))
    sorted_items = sorted(items, key=lambda item: item["priority"], reverse=True)

    try:
        from modules import change_memory

        filtered, suppressed = change_memory.filter_backlog_items(sorted_items)
        results["change_memory"] = {
            "suppressed_count": len(suppressed),
            "suppressed": suppressed[:50],
            **change_memory.summary(),
        }
        return filtered[:limit]
    except Exception as exc:
        results["change_memory"] = {"error": str(exc), "suppressed_count": 0}
        return sorted_items[:limit]


def print_backlog(items: list) -> None:
    if not items:
        print("   Nenhuma acao prioritaria encontrada.")
        return

    print(f"   Top {len(items)} acoes priorizadas:")
    base = get_site_url()
    for idx, item in enumerate(items, start=1):
        target = str(item["target"]).replace(base, "")
        print(
            f"   {idx:02d}. [{item['priority']:>5}] {item['action']} "
            f"({item['source']})"
        )
        print(f"       alvo: {target[:90]}")
        print(f"       motivo: {item['reason'][:110]}")
