from difflib import SequenceMatcher
from modules.crawler import get_page
from config import SITE_URL


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _fetch_seo(url: str) -> dict:
    status, soup, _, final_url = get_page(url)
    rec = {"url": url, "final_url": final_url, "status": status,
           "title": "", "description": "", "h1": "", "meta_keywords": ""}
    if not soup:
        return rec
    t = soup.find("title")
    d = soup.find("meta", attrs={"name": "description"})
    h = soup.find("h1")
    k = soup.find("meta", attrs={"name": "keywords"})
    rec["title"]         = t.get_text(strip=True) if t else ""
    rec["description"]   = d.get("content", "").strip() if d else ""
    rec["h1"]            = h.get_text(strip=True) if h else ""
    rec["meta_keywords"] = k.get("content", "").strip() if k else ""
    return rec


def run(urls: list, threshold: float = 0.80) -> dict:
    """
    Audit a list of URLs for duplicate/missing SEO fields.
    threshold: similarity ratio to flag as duplicate (0.0–1.0)
    """
    try:
        from rich.progress import track
        pages = [_fetch_seo(u if u.startswith("http") else SITE_URL + u)
                 for u in track(urls, description="Verificando duplicatas...")]
    except ImportError:
        pages = [_fetch_seo(u if u.startswith("http") else SITE_URL + u) for u in urls]

    dup_titles   = []
    dup_descs    = []
    dup_h1s      = []
    miss_titles  = []
    miss_descs   = []
    miss_h1s     = []
    kw_issues    = []

    n = len(pages)

    for i, pa in enumerate(pages):
        if not pa.get("title"):
            miss_titles.append(pa["url"])
        if not pa.get("description"):
            miss_descs.append(pa["url"])
        if not pa.get("h1"):
            miss_h1s.append(pa["url"])

        # Detect meta_keywords that look like they belong to another brand
        if pa.get("meta_keywords") and pa.get("url"):
            from urllib.parse import urlparse
            slug = urlparse(pa["url"]).path.strip("/").split("/")[0]
            kw_lower = pa["meta_keywords"].lower()
            for brand in ["lacoste", "tommy", "reserva", "aramis", "columbia",
                          "calvin", "levis", "dudalina", "crocs", "polo", "north face"]:
                if brand in kw_lower and brand not in slug.replace("-", " "):
                    kw_issues.append({
                        "url": pa["url"],
                        "slug": slug,
                        "keywords": pa["meta_keywords"][:100],
                        "suspicious_brand": brand,
                    })
                    break

        for pb in pages[i + 1:]:
            ts = _similarity(pa.get("title", ""), pb.get("title", ""))
            if ts >= threshold and pa.get("title"):
                dup_titles.append({
                    "url_a": pa["url"], "url_b": pb["url"],
                    "title_a": pa["title"][:80], "title_b": pb["title"][:80],
                    "similarity": f"{ts*100:.0f}%",
                })

            ds = _similarity(pa.get("description", ""), pb.get("description", ""))
            if ds >= threshold and pa.get("description"):
                dup_descs.append({
                    "url_a": pa["url"], "url_b": pb["url"],
                    "desc_a": pa["description"][:100], "desc_b": pb["description"][:100],
                    "similarity": f"{ds*100:.0f}%",
                })

            hs = _similarity(pa.get("h1", ""), pb.get("h1", ""))
            if hs >= threshold and pa.get("h1"):
                dup_h1s.append({
                    "url_a": pa["url"], "url_b": pb["url"],
                    "h1_a": pa["h1"][:80], "h1_b": pb["h1"][:80],
                    "similarity": f"{hs*100:.0f}%",
                })

    total_issues = (len(dup_titles) + len(dup_descs) + len(dup_h1s) +
                    len(miss_titles) + len(miss_descs) + len(miss_h1s) + len(kw_issues))

    return {
        "pages_audited":    n,
        "total_issues":     total_issues,
        "duplicate_titles": dup_titles,
        "duplicate_descs":  dup_descs,
        "duplicate_h1s":    dup_h1s,
        "missing_titles":   miss_titles,
        "missing_descs":    miss_descs,
        "missing_h1s":      miss_h1s,
        "keyword_issues":   kw_issues,
        "pages_data":       pages,
    }
