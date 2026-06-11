import app as dashboard


def test_sidebar_prioritizes_dashboard(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_name", lambda: "Cliente Premium")
    monkeypatch.setattr(dashboard, "_current_user_email", lambda: "")

    with dashboard.app.test_request_context("/"):
        html = dashboard.page_shell("Teste", "<div>Conteudo</div>")

    assert '<div class="nav-label">Principal</div>' in html
    assert 'href="/full-audit?new=1"' in html
    assert html.index("Dashboard") < html.index("Nova Auditoria")
    assert html.index("Analytics") < html.index("Nova Auditoria")
    assert html.index("Páginas") < html.index("Nova Auditoria")
    assert html.index("Dashboard") < html.index("Auditoria Completa")
    assert "Local" not in html
    assert "SEO Control Center" not in html


def test_sidebar_settings_footer_uses_nav_colors(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_name", lambda: "Cliente Premium")
    monkeypatch.setattr(dashboard, "_current_user_email", lambda: "cliente@example.com")

    with dashboard.app.test_request_context("/settings"):
        html = dashboard.page_shell("Teste", "<div>Conteudo</div>")

    assert '<div class="sidebar-footer">\n      <nav class="nav">' in html
    assert 'href="/settings" class="active"' in html
    assert "color:#c4c0b8" in html


def test_theme_uses_premium_palette():
    css = dashboard.styles()

    assert "--accent:" in css
    assert "#d6b25e" in css
    assert "--brand:       #080808" in css
    assert "--nav-bg:      #050505" in css
    assert ".nav a.active { background: #e7e2d7; color: #080808" in css
    assert "body.nav-open .sidebar { transform: translateX(0); }" in css
    assert ".mobile-topbar" in css
    assert ".mobile-card-table" in css
    assert "content: attr(data-label)" in css


def test_page_shell_has_mobile_navigation_controls(monkeypatch):
    monkeypatch.setattr(dashboard, "get_site_name", lambda: "Cliente Premium")
    monkeypatch.setattr(dashboard, "_current_user_email", lambda: "cliente@example.com")

    with dashboard.app.test_request_context("/full-audit/report/last"):
        html = dashboard.page_shell("Teste", "<div class='health-summary-card'>Conteudo</div>")

    assert 'class="mobile-topbar"' in html
    assert 'onclick="toggleMobileNav()"' in html
    assert 'onclick="closeMobileNav()"' in html
    assert "document.body.classList.toggle('nav-open')" in html
    assert 'href="/full-audit?new=1" aria-label="Nova auditoria"' in html


def test_dashboard_frontend_has_error_timeout_helpers(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {}))
    monkeypatch.setattr(dashboard, "_current_site_id", lambda: "")

    html = dashboard.app.test_client().get("/").get_data(as_text=True)

    assert "fetchJsonWithTimeout" in html
    assert 'id="chart-error"' in html
    assert "Tempo esgotado ao buscar dados do Google Search Console" in html


def test_dashboard_data_uses_error_status(monkeypatch):
    import modules.gsc_api as gsc_api

    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {}))
    monkeypatch.setattr(gsc_api, "get_dashboard_data", lambda period_days=28: {"error": "falha simulada"})

    response = dashboard.app.test_client().get("/dashboard/data?period=28")

    assert response.status_code == 503
    assert response.get_json()["error"] == "falha simulada"


def test_healthz_is_public_and_exposes_release_flags():
    response = dashboard.app.test_client().get("/healthz")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["deep_audit"] is True
    assert payload["theme"] == "black-premium"
