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
