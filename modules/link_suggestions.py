from config import BRAND_CLUSTERS, SITE_URL


def run(internal_links: dict) -> dict:
    suggestions = []
    for cluster in internal_links.get("cluster_analysis", []):
        brand = cluster.get("brand")
        config = BRAND_CLUSTERS.get(brand, {})
        pillar = config.get("pillar") or cluster.get("pillar")
        for target in cluster.get("missing_from_pillar", []):
            suggestions.append({
                "source": pillar,
                "target": target,
                "anchor": target.strip("/").replace("-", " ").title(),
                "reason": "Pillar nao linka para pagina do cluster",
            })
        for source in cluster.get("missing_to_pillar", []):
            suggestions.append({
                "source": source,
                "target": pillar,
                "anchor": brand.replace("_", " ").title() if brand else "Ver categoria",
                "reason": "Pagina do cluster nao linka de volta para a pillar",
            })
    return {"total": len(suggestions), "suggestions": suggestions}
