from types import SimpleNamespace

import importlib

import config
import run
import app as dashboard
from modules import supabase_store


class _FakeResult:
    def __init__(self, data=None):
        self.data = data


class _FakeQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        rows = [
            row for row in self.rows
            if all(str(row.get(key)) == str(value) for key, value in self.filters.items())
        ]
        return _FakeResult(rows)


class _FakeSupabase:
    def __init__(self, runs, issues=None):
        self.runs = runs
        self.issues = issues or []

    def table(self, name):
        assert name in {"crawl_runs", "issues"}
        return _FakeQuery(self.runs if name == "crawl_runs" else self.issues)


def test_no_report_flag_skips_local_snapshot():
    args = SimpleNamespace(no_report=True)

    assert run.should_save_report(args, run_all=False, mod="blog-ideas", results={"blog_ideas": [1]}) is False


def test_audit_module_saves_report_without_no_report_flag():
    args = SimpleNamespace(no_report=False)

    assert run.should_save_report(args, run_all=False, mod="blog-ideas", results={"blog_ideas": [1]}) is True


def test_reports_folder_uses_tmp_in_serverless(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SEO_RUNTIME_DIR", raising=False)

    reloaded = importlib.reload(config)
    try:
        reports_folder = reloaded.REPORTS_FOLDER.replace("\\", "/")
        assert reports_folder.startswith("/tmp/seo-audit-runtime/reports")
        assert "/var/task" not in reports_folder
    finally:
        monkeypatch.delenv("VERCEL", raising=False)
        importlib.reload(config)


def test_gsc_api_summary_counts_are_saved_in_run_summary():
    summary = supabase_store._summary({
        "gsc_api": {
            "drops": [
                {"severity": "critical"},
                {"severity": "warning"},
                {"severity": "critical"},
            ],
            "tag_suggestions": [{"page": "/a"}],
            "brand_summary": {"lacoste": {}, "reserva": {}},
            "total_pages_cur": 100,
            "total_pages_prev": 95,
            "period_current": "2026-06-01 -> 2026-06-07",
            "period_previous": "2026-05-25 -> 2026-05-31",
            "comparison_label": "Semana anterior",
        },
        "blog_ideas": [{"title": "A"}, {"title": "B"}],
    })

    assert summary["gsc_api_drops"] == 3
    assert summary["gsc_api_critical_drops"] == 2
    assert summary["gsc_api_tag_suggestions"] == 1
    assert summary["gsc_api_brands"] == 2
    assert summary["gsc_api_pages_current"] == 100
    assert summary["gsc_api_comparison"] == "Semana anterior"
    assert summary["blog_ideas"] == 2


def test_reports_list_includes_tool_runs_for_active_site(monkeypatch):
    runs = [
        {
            "id": "run-gsc",
            "site_id": "site-1",
            "run_type": "gsc-api",
            "status": "completed",
            "created_at": "2026-06-09T12:00:00+00:00",
            "summary": {"gsc_api_drops": 116, "gsc_api_critical_drops": 33},
            "scope": [],
        },
        {
            "id": "run-other-site",
            "site_id": "site-2",
            "run_type": "gsc-api",
            "status": "completed",
            "created_at": "2026-06-09T13:00:00+00:00",
            "summary": {"gsc_api_drops": 999},
            "scope": [],
        },
        {
            "id": "run-full-audit",
            "site_id": "site-1",
            "run_type": "full-audit",
            "status": "completed",
            "created_at": "2026-06-09T11:00:00+00:00",
            "summary": {},
            "scope": [],
        },
    ]
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_current_site_id", lambda: "site-1")
    monkeypatch.setattr(dashboard, "_load_last_audit", lambda: {"_completed_at": "09/06/2026 12:30"})
    monkeypatch.setattr(dashboard, "get_supabase", lambda: _FakeSupabase(runs))

    response = dashboard.app.test_client().get("/reports")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Auditoria Completa" in html
    assert "Tendências GSC (API ao vivo)" in html
    assert "Quedas detectadas" in html
    assert "/reports/run/run-gsc" in html
    assert "run-other-site" not in html
    assert "run-full-audit" not in html


def test_report_run_detail_is_scoped_to_active_site(monkeypatch):
    runs = [
        {
            "id": "run-gsc",
            "site_id": "site-1",
            "run_type": "gsc-api",
            "status": "completed",
            "created_at": "2026-06-09T12:00:00+00:00",
            "summary": {
                "gsc_api_drops": 116,
                "gsc_api_critical_drops": 33,
                "gsc_api_period_current": "2026-06-01 -> 2026-06-07",
            },
            "scope": ["/lacoste"],
        },
        {
            "id": "run-gsc",
            "site_id": "site-2",
            "run_type": "gsc-api",
            "status": "completed",
            "created_at": "2026-06-09T12:00:00+00:00",
            "summary": {"gsc_api_drops": 999},
            "scope": ["/other"],
        },
    ]
    issues = [
        {
            "run_id": "run-gsc",
            "site_id": "site-1",
            "severity": "high",
            "title": "Queda de tráfego detectada",
            "target": "/lacoste",
            "description": "Variação de impressões: -1200",
        },
        {
            "run_id": "run-gsc",
            "site_id": "site-2",
            "severity": "high",
            "title": "Achado de outro site",
            "target": "/other",
            "description": "não deve aparecer",
        },
    ]
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_current_site_id", lambda: "site-1")
    monkeypatch.setattr(dashboard, "get_supabase", lambda: _FakeSupabase(runs, issues))

    response = dashboard.app.test_client().get("/reports/run/run-gsc")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Tendências GSC (API ao vivo)" in html
    assert "Quedas detectadas" in html
    assert "116" in html
    assert "/lacoste" in html
    assert "Achados salvos (1)" in html
    assert "Queda de tráfego detectada" in html
    assert "Variação de impressões: -1200" in html
    assert "999</div>" not in html
    assert "Achado de outro site" not in html
    assert "/other" not in html
