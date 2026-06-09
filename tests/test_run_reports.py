from types import SimpleNamespace

import importlib

import config
import run


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
