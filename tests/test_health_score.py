import app as dashboard
from app import _health_score, _scoreable_onpage_warnings
import config
from modules import sitemap_robots


def _page(issues=None, warnings=None):
    return {
        "url": "https://www.secretoutlet.com.br/lacoste",
        "issues": issues or [],
        "warnings": warnings or [],
    }


def test_health_score_ignores_traffic_drops_and_performance_context():
    baseline = {"onpage": [_page()]}
    context_only = {
        "onpage": [_page()],
        "gsc": {
            "top_pages": [{"clicks": 0, "impressions": 100000}],
            "top_queries": [{"position": 80}],
            "low_ctr_pages": [{} for _ in range(20)],
            "cannibalization": [{} for _ in range(500)],
        },
        "gsc_api": {"drops": [{"severity": "critical"} for _ in range(123)]},
    }

    assert _health_score(baseline) == 100
    assert _health_score(context_only) == _health_score(baseline)


def test_health_score_uses_onpage_findings_and_ignores_legacy_meta_keywords():
    healthy = {"onpage": [_page(warnings=["Meta keywords ausente"])]}
    with_seo_finding = {"onpage": [_page(warnings=["Tag canonical ausente"])]}

    assert _health_score(healthy) == 100
    assert _health_score(with_seo_finding) == 95
    assert _scoreable_onpage_warnings(healthy["onpage"][0]) == []


def test_full_audit_report_counts_warnings_as_onpage_findings(monkeypatch):
    report = {
        "onpage": [_page(warnings=["HTML muito grande (600 KB)"])],
        "gsc": {},
        "gsc_api": {},
        "content_gap": {},
        "backlog": [],
        "ai_analysis": {},
        "_audit_scope": {
            "source": "Páginas prioritárias + sitemap",
            "sitemap_total": 25296,
            "duration": "50 a 65 minutos",
        },
    }
    monkeypatch.setattr(dashboard, "_load_last_audit", lambda: report)

    html = dashboard.app.test_client().get("/full-audit/report/last").get_data(as_text=True)

    assert "Achados on-page (1)" in html
    assert "Achados on-page (1 URLs)" in html
    assert "URLs auditadas on-page" in html
    assert "1 URLs analisadas de 25.296 localizadas no sitemap" in html


def test_full_audit_report_lists_all_pages_and_reason_details(monkeypatch):
    pages = [
        {
            "url": f"https://example.com/page-{index}",
            "title": "Curto",
            "issues": [],
            "warnings": ["Meta title curto (5 chars - min 30)"],
            "grade": "B",
        }
        for index in range(55)
    ]
    report = {
        "onpage": pages,
        "gsc": {},
        "gsc_api": {},
        "content_gap": {},
        "backlog": [],
        "ai_analysis": {},
        "_audit_scope": {"source": "Sitemap"},
    }
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(dashboard, "_load_last_audit", lambda: report)

    html = dashboard.app.test_client().get("/full-audit/report/last").get_data(as_text=True)

    assert "...e mais" not in html
    assert "/page-54" in html
    assert "Meta title curto (5 chars - min 30)" in html
    assert "atual: Curto" in html


def test_last_full_audit_is_saved_to_active_user_site_settings(monkeypatch):
    calls = {}

    class FakeResult:
        def __init__(self, data):
            self.data = data

    class FakeQuery:
        def __init__(self, operation):
            self.operation = operation
            self.filters = {}

        def select(self, *_args):
            return self

        def update(self, payload):
            calls["update_payload"] = payload
            return self

        def eq(self, key, value):
            self.filters[key] = value
            return self

        def limit(self, *_args):
            return self

        def execute(self):
            if self.operation == "select":
                return FakeResult([{"settings": {"existing": "keep"}}])
            calls["update_filters"] = dict(self.filters)
            return FakeResult([])

    class FakeSupabase:
        def table(self, name):
            calls.setdefault("tables", []).append(name)
            return FakeQuery("select" if len(calls["tables"]) == 1 else "update")

    monkeypatch.setattr(dashboard, "get_supabase", lambda: FakeSupabase())

    dashboard._save_last_audit(
        {"_completed_at": "29/05/2026 18:10", "onpage": [{"url": "https://example.com"}]},
        "ctx",
        site_config={"user_id": "user-1", "site_id": "site-1"},
    )

    settings = calls["update_payload"]["settings"]
    assert settings["existing"] == "keep"
    assert settings["last_full_audit"]["onpage"][0]["url"] == "https://example.com"
    assert settings["last_full_audit_saved_at"]
    assert calls["update_filters"] == {"user_id": "user-1", "site_id": "site-1"}


def test_last_full_audit_loads_from_active_site_settings(monkeypatch):
    report = {"_completed_at": "29/05/2026 18:10", "onpage": []}
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_load_active_site_config", lambda: {"last_full_audit": report})

    assert dashboard._has_last_audit() is True
    assert dashboard._load_last_audit() == report


def test_full_audit_screen_exposes_page_scope_and_time_estimates(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(config, "get_priority_pages", lambda: ["/marca", "/produto"])

    html = dashboard.app.test_client().get("/full-audit?new=1").get_data(as_text=True)

    assert "Quantidade de páginas da auditoria on-page" in html
    assert "Rápida - 2 páginas prioritárias (3 a 5 minutos)" in html
    assert "1.000 páginas do site (50 a 65 minutos)" in html
    assert "2.000 páginas do site (1h40 a 2h10)" in html


def test_select_full_audit_pages_prefers_priority_pages_then_sitemap(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(config, "get_priority_pages", lambda: ["/priority", "/priority"])
    monkeypatch.setattr(config, "get_brand_clusters", lambda: {
        "brand": {
            "pillar": "/priority",
            "pages": ["/priority/subcategory", "/cluster-page"],
            "blog": ["/blog/cluster-topic"],
        }
    })
    sitemap_urls = [f"https://example.com/page-{index}" for index in range(150)]
    monkeypatch.setattr(
        sitemap_robots,
        "fetch_sitemap_urls",
        lambda: {"urls": sitemap_urls, "errors": []},
    )

    pages, scope = dashboard._select_full_audit_pages("100")

    assert len(pages) == 100
    assert pages[0] == "https://example.com/priority"
    assert pages[1] == "https://example.com/priority/subcategory"
    assert pages[2] == "https://example.com/cluster-page"
    assert pages[3] == "https://example.com/blog/cluster-topic"
    assert pages[4] == "https://example.com/page-0"
    assert scope["requested_pages"] == 100
    assert scope["sitemap_total"] == 150
    assert scope["priority_pages"] == 1
    assert scope["cluster_pages"] == 3


def test_full_audit_start_passes_selected_scope_to_worker(monkeypatch):
    created = []
    monkeypatch.setattr(dashboard, "get_site_url", lambda: "https://example.com")

    class FakeThread:
        def __init__(self, target, args, daemon):
            created.append({"target": target, "args": args, "daemon": daemon})

        def start(self):
            return None

    monkeypatch.setattr(dashboard.threading, "Thread", FakeThread)

    response = dashboard.app.test_client().post(
        "/full-audit/start", json={"page_scope": "1000"}
    )

    assert response.get_json()["page_scope"] == "1000"
    assert created[0]["args"][2] == "1000"
