import importlib

from collectors.crawler import is_internal, normalize_url, should_skip
from modules import crawler as crawler_module
import requests
import config as config_module


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
    assert calls[0]["headers"].get("Connection") != "close"
    assert calls[0]["headers"]["Referer"] == "https://example.com/"


def test_shared_session_reuses_session_and_fetch_cache(monkeypatch):
    calls = []
    sessions = []

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
            self.closed = False
            sessions.append(self)

        def get(self, url, timeout, allow_redirects):
            calls.append({"url": url, "headers": dict(self.headers)})
            return FakeResponse()

        def close(self):
            self.closed = True

    monkeypatch.setattr(crawler_module, "CRAWL_DELAY", 0)
    monkeypatch.setattr(crawler_module, "CRAWL_RETRIES", 0)
    monkeypatch.setattr(crawler_module.requests, "Session", FakeSession)

    with crawler_module.shared_session(cache=True):
        first = crawler_module.get_page("https://example.com/lacoste")
        second = crawler_module.get_page("https://example.com/lacoste")

    assert len(sessions) == 1
    assert len(calls) == 1
    assert first[0] == 200
    assert second[1].get_text(strip=True) == "ok"
    assert sessions[0].closed is True


def test_crawler_imports_when_retry_setting_is_missing(monkeypatch):
    with monkeypatch.context() as m:
        m.delattr(config_module, "CRAWL_RETRIES", raising=False)
        m.setenv("SEO_CRAWL_RETRIES", "3")

        reloaded = importlib.reload(crawler_module)

        assert reloaded.CRAWL_RETRIES == 3

    importlib.reload(crawler_module)
