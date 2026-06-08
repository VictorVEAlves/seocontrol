import app as dashboard
import config
from modules import sitemap_robots


def test_deep_audit_scope_options_support_large_limits():
    key, option = dashboard._deep_audit_scope_config("100000")

    assert key == "100000"
    assert option["limit"] == 100000
    assert dashboard._deep_audit_scope_config("invalid")[0] == "10000"
    assert dashboard._deep_audit_max_sitemaps(100000) > 20


def test_select_deep_audit_pages_prefers_priority_clusters_then_sitemap(monkeypatch):
    captured = {}
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(config, "get_priority_pages", lambda: ["/priority", "/priority"])
    monkeypatch.setattr(config, "get_brand_clusters", lambda: {
        "brand": {
            "pillar": "/priority",
            "pages": ["/cluster"],
            "blog": ["/blog"],
        }
    })

    def fake_fetch_sitemap_urls(max_sitemaps=20):
        captured["max_sitemaps"] = max_sitemaps
        return {
            "urls": [f"https://example.com/page-{index}" for index in range(12000)],
            "errors": [],
            "sitemaps_checked": ["https://example.com/sitemap.xml"],
        }

    monkeypatch.setattr(sitemap_robots, "fetch_sitemap_urls", fake_fetch_sitemap_urls)

    pages, scope = dashboard._select_deep_audit_pages("10000")

    assert len(pages) == 10000
    assert pages[:4] == [
        "https://example.com/priority",
        "https://example.com/cluster",
        "https://example.com/blog",
        "https://example.com/page-0",
    ]
    assert captured["max_sitemaps"] > 20
    assert scope["requested_pages"] == 10000
    assert scope["sitemap_total"] == 12000
    assert scope["priority_pages"] == 1
    assert scope["cluster_pages"] == 2


def test_deep_audit_summary_aggregates_onpage_findings(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    summary = dashboard._deep_audit_empty_summary()

    dashboard._deep_audit_update_summary(summary, [
        {
            "url": "https://example.com/a",
            "score": 60,
            "grade": "D",
            "issues": ["Meta title ausente"],
            "warnings": ["HTML muito grande (600 KB)"],
        },
        {
            "url": "https://example.com/b",
            "score": 95,
            "grade": "A",
            "issues": [],
            "warnings": ["Meta keywords ausente", "Tag canonical ausente"],
        },
    ])

    assert summary["audited_pages"] == 2
    assert summary["critical_pages"] == 1
    assert summary["issues"] == 1
    assert summary["warnings"] == 2
    assert summary["findings"] == 3
    assert summary["health"] == 78
    assert summary["categories"]["Meta title"]["severity"] == "issue"
    assert summary["categories"]["HTML muito grande"]["pages"] == 1
    assert summary["categories"]["Tag canonical"]["pages"] == 1
    assert len(summary["sample_findings"]) == 3


def test_full_audit_screen_exposes_deep_crawler(monkeypatch):
    monkeypatch.setattr(dashboard, "_auth_required", lambda: False)
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(config, "get_priority_pages", lambda: ["/priority"])

    html = dashboard.app.test_client().get("/full-audit?new=1").get_data(as_text=True)

    assert "Crawler Profundo" in html
    assert "100.000 paginas do site" in html
    assert "/deep-audit/start" in html
    assert "function cancelDeepAudit()" in html
