from collections import defaultdict
from urllib.parse import urlparse
from modules.crawler import (
    extract_canonical,
    extract_links,
    get_page,
    is_internal,
    normalize_url,
    shared_session,
    should_skip,
)
from config import MAX_CRAWL_PAGES, get_priority_pages, get_site_url


def crawl(start_url: str = None, max_pages: int = MAX_CRAWL_PAGES) -> dict:
    """
    BFS crawl of the site. Returns pages dict and incoming_links map.
    """
    start_url = start_url or get_site_url()
    if not start_url:
        raise RuntimeError("Configure a URL do site antes de rastrear links.")
    visited = {}
    queue = [normalize_url(start_url)]
    incoming = defaultdict(list)

    try:
        from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, MofNCompleteColumn
        ctx = Progress(SpinnerColumn(), TextColumn("{task.description}"),
                       BarColumn(), MofNCompleteColumn())
    except ImportError:
        ctx = None

    def process():
        while queue and len(visited) < max_pages:
            url = queue.pop(0)
            if url in visited or should_skip(url) or not is_internal(url):
                continue

            path = urlparse(url).path
            if ctx:
                task_desc = f"[cyan]{path[:70]}[/cyan]"
                progress.update(task, description=task_desc, completed=len(visited))

            status, soup, headers, final_url = get_page(url)

            visited[url] = {
                "status": status,
                "final_url": final_url,
                "redirect": normalize_url(url) != normalize_url(final_url),
                "links": [],
                "canonical": extract_canonical(soup, url),
                "html_size_bytes": int(headers.get("_content_size_bytes", 0) or 0),
            }

            if soup:
                links = extract_links(soup, url)
                visited[url]["links"] = links
                for lk in links:
                    if lk["is_internal"]:
                        target = normalize_url(lk["url"])
                        incoming[target].append({
                            "source": url,
                            "anchor": lk["anchor"],
                            "nofollow": lk["nofollow"],
                        })
                        if target not in visited:
                            queue.append(target)

    with shared_session(cache=True):
        if ctx:
            with ctx as progress:
                task = progress.add_task("Rastreando...", total=max_pages)
                process()
        else:
            process()

    return {"pages": visited, "incoming_links": dict(incoming)}


# ── Finders ───────────────────────────────────────────────────────────────────

def find_broken(crawl_data: dict) -> list:
    pages    = crawl_data["pages"]
    incoming = crawl_data["incoming_links"]
    broken   = []
    base     = get_site_url()

    for url, data in pages.items():
        if data["status"] == 0 or data["status"] >= 400:
            srcs = incoming.get(url, [])
            broken.append({
                "url":          url.replace(base, ""),
                "full_url":     url,
                "status":       data["status"],
                "linked_from":  [s["source"].replace(base, "") for s in srcs[:5]],
                "anchors":      [s["anchor"] for s in srcs[:5]],
                "total_sources": len(srcs),
            })

    return sorted(broken, key=lambda x: x["total_sources"], reverse=True)


def find_redirect_chains(crawl_data: dict) -> list:
    pages  = crawl_data["pages"]
    chains = []
    base   = get_site_url()

    for url, data in pages.items():
        if data["redirect"]:
            dest = normalize_url(data["final_url"])
            if dest in pages and pages[dest].get("redirect"):
                chains.append({
                    "start":        url.replace(base, ""),
                    "intermediate": dest.replace(base, ""),
                    "final":        pages[dest]["final_url"].replace(base, ""),
                })

    return chains


def find_orphans(crawl_data: dict, priority_pages: list) -> list:
    incoming = crawl_data["incoming_links"]
    orphans  = []
    base     = get_site_url()

    for page in priority_pages:
        full = normalize_url(page, base)
        srcs = incoming.get(full, [])
        if len(srcs) <= 2:
            orphans.append({
                "url":            page,
                "incoming_count": len(srcs),
                "sources":        [s["source"].replace(base, "") for s in srcs],
            })

    return sorted(orphans, key=lambda x: x["incoming_count"])


def find_nofollow_internal(crawl_data: dict) -> list:
    pages  = crawl_data["pages"]
    result = []
    base   = get_site_url()

    for source, data in pages.items():
        for lk in data.get("links", []):
            if lk.get("nofollow") and lk["is_internal"]:
                result.append({
                    "source": source.replace(base, ""),
                    "target": lk["url"].replace(base, ""),
                    "anchor": lk["anchor"],
                })

    return result


def run(max_pages: int = MAX_CRAWL_PAGES) -> dict:
    crawl_data = crawl(max_pages=max_pages)
    priority_pages = get_priority_pages()

    return {
        "pages_crawled":    len(crawl_data["pages"]),
        "broken":           find_broken(crawl_data),
        "redirect_chains":  find_redirect_chains(crawl_data),
        "orphans":          find_orphans(crawl_data, priority_pages),
        "nofollow_internal": find_nofollow_internal(crawl_data),
        "crawl_data":       crawl_data,
    }
