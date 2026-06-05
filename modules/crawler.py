import time
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config as _config

get_site_url = _config.get_site_url


def _float_setting(name: str, env_key: str, default: str) -> float:
    try:
        return float(getattr(_config, name, os.environ.get(env_key, default)))
    except (TypeError, ValueError):
        return float(default)


def _int_setting(name: str, env_key: str, default: str) -> int:
    try:
        return int(getattr(_config, name, os.environ.get(env_key, default)))
    except (TypeError, ValueError):
        return int(default)


CRAWL_DELAY = _float_setting("CRAWL_DELAY", "SEO_CRAWL_DELAY", "1.0")
REQUEST_TIMEOUT = _float_setting("REQUEST_TIMEOUT", "SEO_REQUEST_TIMEOUT", "15")
CRAWL_RETRIES = _int_setting("CRAWL_RETRIES", "SEO_CRAWL_RETRIES", "2")
USER_AGENT = getattr(
    _config,
    "USER_AGENT",
    os.environ.get(
        "SEO_CRAWLER_USER_AGENT",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36",
    ),
)

BASE_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Connection": "close",
    "DNT": "1",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

SKIP_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".avif",
                   ".bmp", ".pdf", ".css", ".js", ".ico", ".woff", ".woff2",
                   ".ttf", ".xml", ".mp4", ".webm", ".mov", ".zip"}


def _request_headers(url: str) -> dict:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else get_site_url()
    headers = dict(BASE_HEADERS)
    if origin:
        headers["Referer"] = origin.rstrip("/") + "/"
    return headers


def get_page(url: str) -> tuple:
    """
    Fetch a URL. Returns (status_code, soup, headers, final_url).
    headers dict includes extra keys:
      _content_size_bytes : int — raw response body size
      _redirect_status    : str — HTTP status of first redirect (e.g. "301", "302"), or ""
    Returns (0, None, {}, url) on connection error.
    """
    attempts = max(1, int(CRAWL_RETRIES) + 1)
    errors = []
    for attempt in range(attempts):
        try:
            time.sleep(CRAWL_DELAY if attempt == 0 else min(2.0, CRAWL_DELAY + attempt * 0.5))
            with requests.Session() as session:
                session.headers.update(_request_headers(url))
                resp = session.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
            final_url = resp.url
            content_type = resp.headers.get("content-type", "")
            soup = None
            if resp.ok and "text/html" in content_type:
                soup = BeautifulSoup(resp.text, "lxml")
            headers = dict(resp.headers)
            headers["_content_size_bytes"] = len(resp.content)
            headers["_redirect_status"] = str(resp.history[0].status_code) if resp.history else ""
            headers["_fetch_attempts"] = attempt + 1
            return resp.status_code, soup, headers, final_url
        except requests.RequestException as exc:
            errors.append(f"{exc.__class__.__name__}: {exc}")
            if attempt < attempts - 1:
                continue
            headers = {
                "_fetch_error": str(exc),
                "_fetch_error_type": exc.__class__.__name__,
                "_fetch_errors": " | ".join(errors[-3:]),
                "_fetch_attempts": attempts,
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
