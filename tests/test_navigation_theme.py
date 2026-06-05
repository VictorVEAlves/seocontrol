import app as dashboard


def test_sidebar_prioritizes_dashboard(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_name", lambda: "Cliente Premium")
    monkeypatch.setattr(dashboard, "_current_user_email", lambda: "")

    with dashboard.app.test_request_context("/"):
        html = dashboard.page_shell("Teste", "<div>Conteudo</div>")

    assert '<div class="nav-label">Principal</div>' in html
    assert 'href="/full-audit?new=1"' in html
    assert html.index("Dashboard") < html.index("Nova Auditoria")
    assert html.index("Dashboard") < html.index("Auditoria Completa")


def test_theme_uses_premium_palette():
    css = dashboard.styles()

    assert "--accent:" in css
    assert "#c8a15a" in css
    assert "--nav-bg:      #080d18" in css
    assert "linear-gradient(135deg, var(--brand), var(--brand-dark))" in css


def test_dashboard_frontend_has_error_timeout_helpers(monkeypatch):
    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {}))
    monkeypatch.setattr(dashboard, "_current_site_id", lambda: "")

    html = dashboard.app.test_client().get("/").get_data(as_text=True)

    assert "fetchJsonWithTimeout" in html
    assert 'id="chart-error"' in html
    assert "Tempo esgotado ao buscar dados do Google Search Console" in html


def test_dashboard_data_uses_error_status(monkeypatch):
    import modules.gsc_api as gsc_api

    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {}))
    monkeypatch.setattr(gsc_api, "get_dashboard_data", lambda period_days=28: {"error": "falha simulada"})

    response = dashboard.app.test_client().get("/dashboard/data?period=28")

    assert response.status_code == 503
    assert response.get_json()["error"] == "falha simulada"
