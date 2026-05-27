import json
from modules.crawler import get_page, extract_links, is_internal
from config import get_site_url as _get_site_url

HTML_SIZE_WARN_KB = 500

TITLE_MIN = 30
TITLE_MAX = 65
DESC_MIN = 100
DESC_MAX = 165


def audit_page(url: str) -> dict:
    status, soup, headers, final_url = get_page(url)

    result = {
        "url": url,
        "final_url": final_url,
        "status": status,
        "redirect": url.rstrip("/") != final_url.rstrip("/"),
        "title": "",
        "title_length": 0,
        "description": "",
        "description_length": 0,
        "h1_count": 0,
        "h1_texts": [],
        "h2_count": 0,
        "h2_texts": [],
        "images_total": 0,
        "images_no_alt": 0,
        "canonical": "",
        "schemas": [],
        "has_faq_schema": False,
        "has_product_schema": False,
        "meta_keywords": "",
        "word_count": 0,
        "html_size_kb": 0,
        "redirect_status": "",
        "mixed_content_count": 0,
        "outgoing_internal_links": [],
        "issues": [],
        "warnings": [],
        "ok": [],
        "score": 0,
        "grade": "F",
    }

    if not soup:
        result["issues"].append(f"Página inacessível (status {status})")
        return result

    # --- Meta Title ---
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else ""
    result["title"] = title
    result["title_length"] = len(title)

    if not title:
        result["issues"].append("Meta title ausente")
    elif len(title) < TITLE_MIN:
        result["warnings"].append(f"Meta title curto ({len(title)} chars — mín {TITLE_MIN})")
    elif len(title) > TITLE_MAX:
        result["warnings"].append(f"Meta title longo ({len(title)} chars — cortado no Google)")
    else:
        result["ok"].append(f"Meta title OK ({len(title)} chars)")

    # --- Meta Description ---
    desc_tag = soup.find("meta", attrs={"name": "description"})
    desc = desc_tag.get("content", "").strip() if desc_tag else ""
    result["description"] = desc
    result["description_length"] = len(desc)

    if not desc:
        result["issues"].append("Meta description ausente")
    elif len(desc) < DESC_MIN:
        result["warnings"].append(f"Meta description curta ({len(desc)} chars — mín {DESC_MIN})")
    elif len(desc) > DESC_MAX:
        result["warnings"].append(f"Meta description longa ({len(desc)} chars — cortada no Google)")
    else:
        result["ok"].append(f"Meta description OK ({len(desc)} chars)")

    # --- H1 ---
    h1_tags = soup.find_all("h1")
    h1_texts = [h.get_text(strip=True) for h in h1_tags]
    result["h1_count"] = len(h1_tags)
    result["h1_texts"] = h1_texts

    if len(h1_tags) == 0:
        result["issues"].append("H1 ausente")
    elif len(h1_tags) > 1:
        result["issues"].append(f"Múltiplos H1 ({len(h1_tags)}): {h1_texts[:2]}")
    else:
        result["ok"].append(f"H1 OK: \"{h1_texts[0][:55]}\"")

    # --- H2s ---
    h2_tags = soup.find_all("h2")
    result["h2_count"] = len(h2_tags)
    result["h2_texts"] = [h.get_text(strip=True) for h in h2_tags[:8]]

    if len(h2_tags) == 0:
        result["warnings"].append("Nenhum H2 — estrutura de conteúdo fraca")

    # --- Images without alt ---
    images = soup.find_all("img")
    no_alt = [img.get("src", "")[:80] for img in images if not img.get("alt", "").strip()]
    result["images_total"] = len(images)
    result["images_no_alt"] = len(no_alt)

    if no_alt:
        sev = "issues" if len(no_alt) > 5 else "warnings"
        result[sev].append(f"{len(no_alt)} imagens sem alt text")

    # --- Canonical ---
    canonical = soup.find("link", rel="canonical")
    result["canonical"] = canonical.get("href", "").strip() if canonical else ""
    if not result["canonical"]:
        result["warnings"].append("Tag canonical ausente")
    else:
        result["ok"].append("Canonical presente")

    # --- Schema JSON-LD ---
    schemas = []
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "{}")
            schema_type = data.get("@type", "")
            if isinstance(schema_type, list):
                schema_type = ", ".join(schema_type)
            if schema_type:
                schemas.append(schema_type)
        except Exception:
            pass
    result["schemas"] = schemas
    result["has_faq_schema"] = "FAQPage" in schemas
    result["has_product_schema"] = "Product" in schemas or "ItemList" in schemas

    if not schemas:
        result["warnings"].append("Nenhum Schema markup (JSON-LD) encontrado")
    else:
        result["ok"].append(f"Schema: {', '.join(schemas)}")

    # --- Meta Keywords (Bagy legacy field; collected, not scored) ---
    kw_tag = soup.find("meta", attrs={"name": "keywords"})
    result["meta_keywords"] = kw_tag.get("content", "").strip() if kw_tag else ""

    # --- Word count ---
    body = soup.find("body")
    if body:
        text = body.get_text(separator=" ", strip=True)
        result["word_count"] = len([w for w in text.split() if len(w) > 2])
        if result["word_count"] < 150:
            result["warnings"].append(f"Conteúdo escasso ({result['word_count']} palavras)")

    # --- HTML size ---
    size_bytes = int(headers.get("_content_size_bytes", 0))
    result["html_size_kb"] = round(size_bytes / 1024, 1)
    if size_bytes > HTML_SIZE_WARN_KB * 1024:
        result["warnings"].append(
            f"HTML muito grande ({result['html_size_kb']} KB) — impacta velocidade de carregamento"
        )

    # --- Redirect type ---
    redir_status = headers.get("_redirect_status", "")
    result["redirect_status"] = redir_status
    if redir_status == "302":
        result["warnings"].append("Redirect 302 (temporário) — altere para 301 permanente para preservar PageRank")

    # --- Mixed content (HTTP recursos em página HTTPS) ---
    if url.startswith("https://"):
        http_count = 0
        for tag, attr in [("a", "href"), ("img", "src"), ("script", "src"),
                          ("link", "href"), ("iframe", "src"), ("source", "src")]:
            for el in soup.find_all(tag, {attr: True}):
                if str(el.get(attr, "")).startswith("http://"):
                    http_count += 1
        result["mixed_content_count"] = http_count
        if http_count:
            result["warnings"].append(
                f"{http_count} recurso(s) HTTP em página HTTPS — conteúdo misto"
            )

    # --- Outgoing internal links (used by audit_pages for orphan check) ---
    result["outgoing_internal_links"] = [
        lnk["url"] for lnk in extract_links(soup, url) if lnk["is_internal"]
    ]

    # --- Score ---
    score = 100 - (len(result["issues"]) * 20) - (len(result["warnings"]) * 5)
    result["score"] = max(0, min(100, score))
    result["grade"] = (
        "A" if result["score"] >= 85 else
        "B" if result["score"] >= 70 else
        "C" if result["score"] >= 50 else
        "D"
    )

    return result


def _rescore(result: dict) -> None:
    score = 100 - (len(result["issues"]) * 20) - (len(result["warnings"]) * 5)
    result["score"] = max(0, min(100, score))
    result["grade"] = (
        "A" if result["score"] >= 85 else
        "B" if result["score"] >= 70 else
        "C" if result["score"] >= 50 else
        "D"
    )


def audit_pages(urls: list, verbose: bool = True) -> list:
    full_urls = [u if u.startswith("http") else _get_site_url() + u for u in urls]
    try:
        from rich.progress import track as _track
        _iter = _track(full_urls, description="Auditando páginas...") if verbose else full_urls
    except ImportError:
        _iter = full_urls
    results = [audit_page(url) for url in _iter]

    # ── Cross-page: duplicate meta titles ────────────────────────────────────
    title_map: dict = {}
    for r in results:
        t = r.get("title", "").strip().lower()
        if t:
            title_map.setdefault(t, []).append(r["url"])
    for r in results:
        t = r.get("title", "").strip().lower()
        dupes = title_map.get(t, [])
        if t and len(dupes) > 1:
            others = [u for u in dupes if u != r["url"]]
            r["warnings"].append(
                f"Meta title duplicado em {len(others)} outra(s) página(s): {others[0]}"
            )

    # ── Cross-page: duplicate meta descriptions ───────────────────────────────
    desc_map: dict = {}
    for r in results:
        d = r.get("description", "").strip().lower()
        if d:
            desc_map.setdefault(d, []).append(r["url"])
    for r in results:
        d = r.get("description", "").strip().lower()
        dupes = desc_map.get(d, [])
        if d and len(dupes) > 1:
            others = [u for u in dupes if u != r["url"]]
            r["warnings"].append(
                f"Meta description duplicada em {len(others)} outra(s) página(s): {others[0]}"
            )

    # ── Cross-page: orphan pages (< 2 links internos recebidos) ─────────────
    incoming: dict = {}
    audited_urls = {r["url"] for r in results}
    for r in results:
        for link in r.get("outgoing_internal_links", []):
            if link in audited_urls:
                incoming[link] = incoming.get(link, 0) + 1
    for r in results:
        count = incoming.get(r["url"], 0)
        if count < 2:
            r["warnings"].append(
                f"Página com apenas {count} link(s) interno(s) recebido(s) — risco de página órfã"
            )

    # ── Recalculate scores after cross-page additions ────────────────────────
    for r in results:
        _rescore(r)

    return results
