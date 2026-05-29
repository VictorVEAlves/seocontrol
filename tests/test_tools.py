import app as dashboard


def test_ai_insights_tool_is_removed_from_standalone_tools():
    html = dashboard.app.test_client().get("/tools").get_data(as_text=True)

    assert 'data-module="ai-insights"' not in html
    assert "/ai-insights" not in html


def test_ai_insights_page_redirects_to_full_audit():
    response = dashboard.app.test_client().get("/ai-insights", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["Location"].startswith("/full-audit")
