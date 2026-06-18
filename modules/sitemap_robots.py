import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests

from config import REQUEST_TIMEOUT, USER_AGENT, get_priority_pages, get_site_url
from modules.crawler import SKIP_EXTENSIONS, get_page, normalize_url, shared_session


HEADERS = {"User-Agent": USER_AGENT}


def fetch_robots(session: requests.Session | None = None) -> dict:
    url = urljoin(get_site_url(), "/robots.txt")
    http = session or requests
    try:
        resp = http.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        text = resp.text if resp.ok else ""
    except requests.RequestException as exc:
        return {"url": url, "status": 0, "text": "", "sitemaps": [], "disallows": [], "error": str(exc)}

    sitemaps = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", text)
    disallows = re.findall(r"(?im)^\s*Disallow:\s*(\S+)", text)
    return {"url": url, "status": resp.status_code, "text": text, "sitemaps": sitemaps, "disallows": disallows}


def _local_name(tag: str) -> str:
    return str(tag or "").rsplit("}", 1)[-1].lower()


def _direct_child_text(node, name: str) -> str:
    for child in list(node):
        if _local_name(child.tag) == name:
            return (child.text or "").strip()
    return ""


def _is_auditable_page_url(url: str) -> bool:
    site_url = get_site_url()
    if not site_url or not url:
        return False
    normalized = normalize_url(url, site_url)
    parsed = urlparse(normalized)
    site = urlparse(site_url)
    if parsed.netloc.lower() != site.netloc.lower():
        return False
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return False
    return True


def _parse_sitemap_xml(xml_text: str) -> tuple[list, list]:
    root = ET.fromstring(xml_text)
    urls = []
    indexes = []
    for node in list(root):
        name = _local_name(node.tag)
        value = _direct_child_text(node, "loc")
        if not value:
            continue
        if name == "sitemap":
            indexes.append(value)
        elif name == "url" and _is_auditable_page_url(value):
            urls.append(normalize_url(value, get_site_url()))
    return urls, indexes


def fetch_sitemap_urls(sitemap_urls: list | None = None, max_sitemaps: int = 20) -> dict:
    session = requests.Session()
    session.headers.update(HEADERS)
    robots = fetch_robots(session=session)
    queue = list(sitemap_urls or robots.get("sitemaps") or [urljoin(get_site_url(), "/sitemap.xml")])
    seen = set()
    urls = set()
    errors = []

    try:
        while queue and len(seen) < max_sitemaps:
            sitemap_url = queue.pop(0)
            if sitemap_url in seen:
                continue
            seen.add(sitemap_url)
            try:
                resp = session.get(sitemap_url, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
                parsed_urls, indexes = _parse_sitemap_xml(resp.text)
                urls.update(parsed_urls)
                queue.extend([idx for idx in indexes if idx not in seen])
            except Exception as exc:
                errors.append({"url": sitemap_url, "error": str(exc)})
    finally:
        session.close()

    return {"sitemaps_checked": list(seen), "urls": sorted(urls), "errors": errors, "robots": robots}


def _is_blocked_by_disallow(path: str, disallows: list) -> bool:
    for rule in disallows:
        if not rule or rule == "/":
            return rule == "/"
        prefix = rule.rstrip("*")
        if path.startswith(prefix):
            return True
    return False


def analyze(crawl_data: dict | None = None, priority_pages: list | None = None) -> dict:
    sitemap = fetch_sitemap_urls()
    sitemap_urls = set(sitemap["urls"])
    robots = sitemap["robots"]
    disallows = robots.get("disallows", [])
    priority = priority_pages if priority_pages is not None else get_priority_pages()

    missing_priority = []
    blocked_priority = []
    canonical_issues = []
    site_url = get_site_url()

    for page in priority:
        full = normalize_url(page, site_url)
        path = urlparse(full).path
        if full not in sitemap_urls:
            missing_priority.append(page)
        if _is_blocked_by_disallow(path, disallows):
            blocked_priority.append(page)

    pages = (crawl_data or {}).get("pages", {})
    with shared_session(cache=True):
        for url, data in pages.items():
            final_url = str(data.get("final_url") or url)
            if "canonical" in data:
                canonical_url = str(data.get("canonical") or "")
            else:
                status, soup, _, final_url = get_page(url)
                if not soup:
                    continue
                canonical = soup.find("link", rel="canonical")
                canonical_url = normalize_url(canonical.get("href"), url) if canonical and canonical.get("href") else ""
            if canonical_url and canonical_url != normalize_url(final_url):
                canonical_issues.append({
                    "url": url.replace(site_url, ""),
                    "canonical": canonical_url.replace(site_url, ""),
                    "final_url": normalize_url(final_url).replace(site_url, ""),
                })

    issues = []
    issues.extend({"type": "missing_from_sitemap", "url": url} for url in missing_priority)
    issues.extend({"type": "blocked_by_robots", "url": url} for url in blocked_priority)
    issues.extend({"type": "canonical_mismatch", **row} for row in canonical_issues)

    return {
        "robots": robots,
        "sitemap_urls_count": len(sitemap_urls),
        "sitemaps_checked": sitemap["sitemaps_checked"],
        "sitemap_errors": sitemap["errors"],
        "missing_priority": missing_priority,
        "blocked_priority": blocked_priority,
        "canonical_issues": canonical_issues,
        "issues": issues,
    }


def run(crawl_data: dict | None = None, priority_pages: list | None = None) -> dict:
    return analyze(crawl_data=crawl_data, priority_pages=priority_pages)
