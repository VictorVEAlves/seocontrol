import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_site_url, CRAWL_DELAY, REQUEST_TIMEOUT, USER_AGENT

_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
})

SKIP_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif",
                   ".bmp", ".pdf", ".css", ".js", ".ico", ".woff", ".woff2",
                   ".ttf", ".xml", ".mp4", ".webm", ".mov", ".zip"}


def get_page(url: str) -> tuple:
    """
    Fetch a URL. Returns (status_code, soup, headers, final_url).
    headers dict includes extra keys:
      _content_size_bytes : int — raw response body size
      _redirect_status    : str — HTTP status of first redirect (e.g. "301", "302"), or ""
    Returns (0, None, {}, url) on connection error.
    """
    try:
        time.sleep(CRAWL_DELAY)
        resp = _session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        final_url = resp.url
        content_type = resp.headers.get("content-type", "")
        soup = None
        if resp.ok and "text/html" in content_type:
            soup = BeautifulSoup(resp.text, "lxml")
        headers = dict(resp.headers)
        headers["_content_size_bytes"] = len(resp.content)
        headers["_redirect_status"] = str(resp.history[0].status_code) if resp.history else ""
        return resp.status_code, soup, headers, final_url
    except requests.RequestException as exc:
        headers = {
            "_fetch_error": str(exc),
            "_fetch_error_type": exc.__class__.__name__,
        }
        return 0, None, headers, url


def is_internal(url: str) -> bool:
    parsed = urlparse(url)
    site_parsed = urlparse(get_site_url())
    return parsed.netloc == "" or parsed.netloc == site_parsed.netloc


def normalize_url(url: str, base: str = "") -> str:
    if not base:
        base = get_site_url()
    url = urljoin(base, url).split("#")[0].split("?")[0]
    parsed = urlparse(url)
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def should_skip(url: str) -> bool:
    parsed = urlparse(url)
    path = parsed.path.lower()
    if any(path.endswith(ext) for ext in SKIP_EXTENSIONS):
        return True
    skip_patterns = ["mailto:", "tel:", "javascript:", "whatsapp:", "instagram.com",
                     "facebook.com", "twitter.com", "youtube.com", "linkedin.com"]
    return any(p in url.lower() for p in skip_patterns)


def extract_links(soup: BeautifulSoup, page_url: str) -> list:
    links = []
    if not soup:
        return links
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith("#"):
            continue
        abs_url = normalize_url(href, page_url)
        if should_skip(abs_url):
            continue
        rel = a.get("rel", [])
        if isinstance(rel, str):
            rel = rel.split()
        links.append({
            "url": abs_url,
            "anchor": a.get_text(strip=True)[:120],
            "nofollow": "nofollow" in rel,
            "is_internal": is_internal(abs_url),
        })
    return links
