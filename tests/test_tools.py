import app as dashboard
import json


def test_ai_insights_tool_is_removed_from_standalone_tools():
    html = dashboard.app.test_client().get("/tools").get_data(as_text=True)

    assert 'data-module="ai-insights"' not in html
    assert "/ai-insights" not in html


def test_ai_insights_page_redirects_to_full_audit():
    response = dashboard.app.test_client().get("/ai-insights", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/full-audit")


def test_tool_output_hides_internal_command_details():
    output = dashboard.format_job_output({
        "status": "completed",
        "command": r"C:\Python\python.exe run.py --module blog-ideas",
        "stdout": "Resultado pronto.",
        "stderr": "trace tecnico",
    })

    assert output.startswith("Concluído")
    assert "Resultado pronto." in output
    assert "Comando:" not in output
    assert "Status:" not in output
    assert "STDERR" not in output
    assert "C:\\Python" not in output
    assert "Detalhes do erro" in output


def test_tool_runtime_config_drops_large_report_snapshots():
    compact = dashboard._compact_runtime_site_config({
        "site_id": "site-1",
        "user_id": "user-1",
        "site_url": "https://example.com",
        "gsc_token_json": '{"token":"ok"}',
        "priority_pages": ["/a", "/b"],
        "last_full_audit": {"huge": "x" * 10000},
        "available_gsc_sites": ["https://example.com/"],
    })

    payload = json.dumps(compact)

    assert compact["site_id"] == "site-1"
    assert compact["gsc_token_json"] == '{"token":"ok"}'
    assert "priority_pages" in compact
    assert "last_full_audit" not in compact
    assert "available_gsc_sites" not in compact
    assert len(payload) < 1000


def test_tool_subprocess_env_is_minimal(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "anon")
    monkeypatch.setenv("VERY_LARGE_UNUSED_ENV", "x" * 10000)

    env = dashboard._tool_subprocess_env('{"site_id":"site-1"}')

    assert env["SUPABASE_URL"] == "https://example.supabase.co"
    assert env["SEO_RUNTIME_SITE_CONFIG"] == '{"site_id":"site-1"}'
    assert "VERY_LARGE_UNUSED_ENV" not in env
