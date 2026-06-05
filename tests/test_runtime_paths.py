import importlib

import config


def test_serverless_runtime_uses_tmp_for_scoped_files(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SEO_RUNTIME_DIR", raising=False)
    config.set_runtime_site_config({
        "user_id": "user-1",
        "site_id": "site-1",
        "site_url": "https://example.com",
    })

    try:
        path = config.get_scoped_runtime_file("shopify_admin_token.json", "shopify")
    finally:
        config.clear_runtime_site_config()

    assert path.as_posix().startswith("/tmp/seo-audit-runtime/shopify/")
    assert "/var/task" not in path.as_posix()


def test_shopify_module_runtime_files_are_writable_in_serverless(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.delenv("SEO_RUNTIME_DIR", raising=False)
    config.set_runtime_site_config({
        "user_id": "user-1",
        "site_id": "site-1",
        "site_url": "https://example.com",
    })

    from modules import shopify_seo
    try:
        reloaded = importlib.reload(shopify_seo)
        token_path = reloaded.TOKEN_CACHE_FILE.as_posix()
        queue_path = reloaded.QUEUE_FILE.as_posix()
    finally:
        config.clear_runtime_site_config()
        monkeypatch.delenv("VERCEL", raising=False)
        importlib.reload(shopify_seo)

    assert token_path.startswith("/tmp/seo-audit-runtime/shopify/")
    assert queue_path.startswith("/tmp/seo-audit-runtime/shopify/")
    assert "/var/task" not in token_path
    assert "/var/task" not in queue_path
