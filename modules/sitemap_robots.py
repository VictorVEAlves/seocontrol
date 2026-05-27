import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests

from config import PRIORITY_PAGES, REQUEST_TIMEOUT, SITE_URL, USER_AGENT
from modules.crawler import get_page, normalize_url


HEADERS = {"User-Agent": USER_AGENT}


def fetch_robots() -> dict:
    url = urljoin(SITE_URL, "/robots.txt")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        text = resp.text if resp.ok else ""
    except requests.RequestException as exc:
        return {"url": url, "status": 0, "text": "", "sitemaps": [], "disallows": [], "error": str(exc)}

    sitemaps = re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", text)
    disallows = re.findall(r"(?im)^\s*Disallow:\s*(\S+)", text)
    return {"url": url, "status": resp.status_code, "text": text, "sitemaps": sitemaps, "disallows": disallows}


def _parse_sitemap_xml(xml_text: str) -> tuple[list, list]:
    root = ET.fromstring(xml_text)
    urls = []
    indexes = []
    for loc in root.findall(".//{*}loc"):
        value = (loc.text or "").strip()
        if value.endswith(".xml") or "sitemap" in value.lower():
            indexes.append(value)
        else:
            urls.append(normalize_url(value))
    return urls, indexes


def fetch_sitemap_urls(sitemap_urls: list | None = None, max_sitemaps: int = 20) -> dict:
    robots = fetch_robots()
    queue = list(sitemap_urls or robots.get("sitemaps") or [urljoin(SITE_URL, "/sitemap.xml")])
    seen = set()
    urls = set()
    errors = []

    while queue and len(seen) < max_sitemaps:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            parsed_urls, indexes = _parse_sitemap_xml(resp.text)
            urls.update(parsed_urls)
            queue.extend([idx for idx in indexes if idx not in seen])
        except Exception as exc:
            errors.append({"url": sitemap_url, "error": str(exc)})

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
    priority = priority_pages or PRIORITY_PAGES

    missing_priority = []
    blocked_priority = []
    canonical_issues = []

    for page in priority:
        full = normalize_url(page, SITE_URL)
        path = urlparse(full).path
        if full not in sitemap_urls:
            missing_priority.append(page)
        if _is_blocked_by_disallow(path, disallows):
            blocked_priority.append(page)

    pages = (crawl_data or {}).get("pages", {})
    for url, data in pages.items():
        status, soup, _, final_url = get_page(url)
        if not soup:
            continue
        canonical = soup.find("link", rel="canonical")
        canonical_url = normalize_url(canonical.get("href"), url) if canonical and canonical.get("href") else ""
        if canonical_url and canonical_url != normalize_url(final_url):
            canonical_issues.append({
                "url": url.replace(SITE_URL, ""),
                "canonical": canonical_url.replace(SITE_URL, ""),
                "final_url": normalize_url(final_url).replace(SITE_URL, ""),
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
