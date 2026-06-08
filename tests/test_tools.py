import app as dashboard


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
