from collectors.crawler import is_internal, normalize_url, should_skip
from modules import crawler as crawler_module
import requests


def test_normalize_url_removes_query_fragment_and_trailing_slash(monkeypatch):
    monkeypatch.setattr("modules.crawler.get_site_url", lambda: "https://www.secretoutlet.com.br")
    url = normalize_url("/lacoste/?utm_source=x#products")
    assert url == "https://www.secretoutlet.com.br/lacoste"


def test_internal_and_skip_rules(monkeypatch):
    monkeypatch.setattr("modules.crawler.get_site_url", lambda: "https://www.secretoutlet.com.br")
    assert is_internal("/lacoste")
    assert is_internal("https://www.secretoutlet.com.br/lacoste")
    assert not is_internal("https://example.com/lacoste")
    assert should_skip("https://www.secretoutlet.com.br/image.jpg")
    assert should_skip("mailto:contato@example.com")


def test_get_page_retries_remote_disconnect_with_browser_headers(monkeypatch):
    calls = []

    class FakeResponse:
        status_code = 200
        ok = True
        url = "https://example.com/lacoste"
        headers = {"content-type": "text/html"}
        content = b"<html><body>ok</body></html>"
        text = "<html><body>ok</body></html>"
        history = []

    class FakeSession:
        def __init__(self):
            self.headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get(self, url, timeout, allow_redirects):
            calls.append({"url": url, "headers": dict(self.headers)})
            if len(calls) == 1:
                raise requests.ConnectionError("Remote end closed connection without response")
            return FakeResponse()

    monkeypatch.setattr(crawler_module, "CRAWL_DELAY", 0)
    monkeypatch.setattr(crawler_module, "CRAWL_RETRIES", 1)
    monkeypatch.setattr(crawler_module.requests, "Session", FakeSession)

    status, soup, headers, final_url = crawler_module.get_page("https://example.com/lacoste")

    assert status == 200
    assert soup.get_text(strip=True) == "ok"
    assert final_url == "https://example.com/lacoste"
    assert headers["_fetch_attempts"] == 2
    assert calls[0]["headers"]["Connection"] == "close"
    assert calls[0]["headers"]["Referer"] == "https://example.com/"
