import app as dashboard


def test_ai_insights_command_accepts_selected_comparison():
    cmd = dashboard.build_tool_command({
        "module": "ai-insights",
        "comparison": "month",
    })

    assert "--comparison" in cmd
    assert cmd[cmd.index("--comparison") + 1] == "month"


def test_ai_insights_tool_exposes_comparison_field():
    html = dashboard.app.test_client().get("/tools?module=ai-insights").get_data(as_text=True)

    assert 'data-module="ai-insights" data-tags="comparison"' in html
    assert "AI Insights" in html
