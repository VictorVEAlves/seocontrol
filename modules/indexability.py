from urllib.parse import urlparse

from config import get_site_url
from modules.crawler import get_page, normalize_url


def audit_url(url: str) -> dict:
    base = get_site_url()
    if not base and not url.startswith("http"):
        raise RuntimeError("Configure a URL do site antes de auditar indexabilidade.")
    full_url = url if url.startswith("http") else base + url
    status, soup, headers, final_url = get_page(full_url)
    normalized_final = normalize_url(final_url)

    result = {
        "url": full_url,
        "final_url": final_url,
        "status": status,
        "redirect": normalize_url(full_url) != normalized_final,
        "noindex": False,
        "nofollow": False,
        "canonical": "",
        "canonical_cross": False,
        "has_params": bool(urlparse(full_url).query),
        "indexable": False,
        "issues": [],
    }

    if status != 200:
        result["issues"].append(f"Status nao indexavel: {status}")
    if result["redirect"]:
        result["issues"].append("URL redireciona")
    if result["has_params"]:
        result["issues"].append("URL com parametros")

    if soup:
        robots = soup.find("meta", attrs={"name": "robots"})
        directives = (robots.get("content", "") if robots else "").lower()
        result["noindex"] = "noindex" in directives
        result["nofollow"] = "nofollow" in directives
        if result["noindex"]:
            result["issues"].append("Meta robots noindex")

        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            result["canonical"] = normalize_url(canonical.get("href"), full_url)
            result["canonical_cross"] = result["canonical"] != normalized_final
            if result["canonical_cross"]:
                result["issues"].append("Canonical aponta para outra URL")
        else:
            result["issues"].append("Canonical ausente")

    result["indexable"] = status == 200 and not result["noindex"] and not result["canonical_cross"]
    return result


def run(urls: list) -> dict:
    rows = [audit_url(url) for url in urls]
    return {
        "total": len(rows),
        "indexable": len([r for r in rows if r["indexable"]]),
        "non_indexable": len([r for r in rows if not r["indexable"]]),
        "issues": [r for r in rows if r["issues"]],
        "pages": rows,
    }
