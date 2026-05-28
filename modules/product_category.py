from config import get_site_url
from modules.crawler import get_page


def audit_category(url: str) -> dict:
    base = get_site_url()
    if not base and not url.startswith("http"):
        raise RuntimeError("Configure a URL do site antes de auditar categorias/produtos.")
    full_url = url if url.startswith("http") else base + url
    status, soup, _, final_url = get_page(full_url)
    result = {
        "url": full_url,
        "status": status,
        "final_url": final_url,
        "product_count_hint": 0,
        "has_breadcrumbs": False,
        "filter_links": 0,
        "filter_indexable_risk": 0,
        "issues": [],
    }
    if not soup:
        result["issues"].append("Pagina inacessivel")
        return result

    text = soup.get_text(" ", strip=True).lower()
    result["has_breadcrumbs"] = "breadcrumb" in str(soup).lower() or "inicio" in text and ">" in text
    product_like = soup.select("[class*=product], [class*=produto], [data-product], [itemtype*=Product]")
    result["product_count_hint"] = len(product_like)
    filter_links = [
        a.get("href", "") for a in soup.find_all("a", href=True)
        if any(token in a.get("href", "").lower() for token in ["?filter", "filter=", "variant=", "tamanho=", "cor="])
    ]
    result["filter_links"] = len(filter_links)
    result["filter_indexable_risk"] = len([href for href in filter_links if "noindex" not in href.lower()])

    if result["product_count_hint"] == 0:
        result["issues"].append("Nenhum produto detectado por heuristica")
    if not result["has_breadcrumbs"]:
        result["issues"].append("Breadcrumb nao detectado")
    if result["filter_indexable_risk"] > 10:
        result["issues"].append("Muitos filtros potencialmente indexaveis")
    return result


def run(urls: list) -> dict:
    pages = [audit_category(url) for url in urls]
    return {"total": len(pages), "issues": [p for p in pages if p["issues"]], "pages": pages}
