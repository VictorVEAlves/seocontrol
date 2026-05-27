from collections import defaultdict
from urllib.parse import urlparse
from config import SITE_URL, BRAND_CLUSTERS


def _normalize(url: str) -> str:
    from modules.crawler import normalize_url
    return normalize_url(url, SITE_URL)


def build_graph(crawl_data: dict) -> dict:
    """Build directed link graph from crawl data."""
    pages    = crawl_data["pages"]
    graph    = defaultdict(set)
    in_deg   = defaultdict(int)
    out_deg  = defaultdict(int)

    for source, data in pages.items():
        for lk in data.get("links", []):
            if lk["is_internal"]:
                target = _normalize(lk["url"])
                graph[source].add(target)
                in_deg[target]  += 1
                out_deg[source] += 1

    return {
        "graph":    {k: list(v) for k, v in graph.items()},
        "in_deg":   dict(in_deg),
        "out_deg":  dict(out_deg),
    }


def cluster_analysis(crawl_data: dict) -> list:
    """For each brand cluster, check bidirectional link health."""
    g = build_graph(crawl_data)
    graph  = g["graph"]
    in_deg = g["in_deg"]
    reports = []

    for brand, cluster in BRAND_CLUSTERS.items():
        pillar_full = _normalize(cluster["pillar"])
        sub_pages   = [_normalize(p) for p in cluster.get("pages", [])]
        blog_pages  = [_normalize(p) for p in cluster.get("blog", [])]
        all_pages   = sub_pages + blog_pages

        pillar_links_to = set(graph.get(pillar_full, []))

        missing_from_pillar = [p for p in all_pages if p not in pillar_links_to]
        missing_to_pillar   = [p for p in all_pages
                               if pillar_full not in set(graph.get(p, []))]

        incoming_pillar = in_deg.get(pillar_full, 0)

        health = 100
        if all_pages:
            health -= int(len(missing_from_pillar) / len(all_pages) * 50)
            health -= int(len(missing_to_pillar)   / len(all_pages) * 50)

        reports.append({
            "brand":               brand,
            "pillar":              cluster["pillar"],
            "pillar_incoming":     incoming_pillar,
            "cluster_size":        len(all_pages),
            "missing_from_pillar": [p.replace(SITE_URL, "") for p in missing_from_pillar],
            "missing_to_pillar":   [p.replace(SITE_URL, "") for p in missing_to_pillar],
            "health_score":        max(0, health),
        })

    return sorted(reports, key=lambda x: x["health_score"])


def top_linked_pages(crawl_data: dict, top_n: int = 20) -> list:
    g      = build_graph(crawl_data)
    in_deg = g["in_deg"]
    return [
        {"url": url.replace(SITE_URL, ""), "incoming": count}
        for url, count in sorted(in_deg.items(), key=lambda x: x[1], reverse=True)[:top_n]
    ]


def orphan_pages(crawl_data: dict, priority_pages: list) -> list:
    g      = build_graph(crawl_data)
    in_deg = g["in_deg"]
    result = []

    for page in priority_pages:
        full = _normalize(page)
        cnt  = in_deg.get(full, 0)
        if cnt <= 2:
            result.append({"url": page, "incoming_links": cnt})

    return sorted(result, key=lambda x: x["incoming_links"])


def d3_graph_data(crawl_data: dict) -> dict:
    """Generate JSON for a D3.js force-directed graph visualization."""
    g      = build_graph(crawl_data)
    in_deg = g["in_deg"]
    graph  = g["graph"]

    def classify(url: str) -> str:
        path = urlparse(url).path.strip("/")
        if not path:
            return "home"
        segs = path.split("/")
        if len(segs) > 2:
            return "product"
        keywords = ["melhores", "como", "guia", "outlet", "blog", "diferenca",
                    "estilos", "tipos", "historia"]
        if any(k in path for k in keywords):
            return "blog"
        # Brand pillars
        pillars = [c["pillar"].strip("/") for c in BRAND_CLUSTERS.values()]
        if path in pillars:
            return "pillar"
        return "category"

    # Keep only non-product pages for readability
    pages  = crawl_data["pages"]
    keep   = {url for url in pages if classify(url) != "product"}

    url_to_id = {url: i for i, url in enumerate(keep)}
    nodes = [
        {
            "id":    url_to_id[url],
            "label": urlparse(url).path.strip("/")[:35] or "home",
            "url":   url,
            "type":  classify(url),
            "size":  max(5, min(40, in_deg.get(url, 0) * 2 + 5)),
        }
        for url in keep
    ]

    links = []
    for source, targets in graph.items():
        if source not in url_to_id:
            continue
        for target in targets:
            if target in url_to_id:
                links.append({"source": url_to_id[source], "target": url_to_id[target]})

    return {"nodes": nodes, "links": links}


def run(crawl_data: dict = None) -> dict:
    """Run internal links analysis. Accepts existing crawl_data or crawls fresh."""
    if crawl_data is None:
        from modules import broken_links
        print("Rastreando site para análise de links internos...")
        crawl_data = broken_links.crawl()

    from config import PRIORITY_PAGES
    return {
        "cluster_analysis": cluster_analysis(crawl_data),
        "top_linked":       top_linked_pages(crawl_data),
        "orphans":          orphan_pages(crawl_data, PRIORITY_PAGES),
        "d3_data":          d3_graph_data(crawl_data),
    }
