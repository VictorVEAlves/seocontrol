import app as dashboard


def test_shopify_page_renders_frontend(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(dashboard, "_shopify_default_provider", lambda: "groq")

    response = dashboard.app.test_client().get("/shopify")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Shopify SEO" in html
    assert "/shopify/generate/start" in html
    assert "shopify-action-form" in html
    assert "shopify-top-grid" in html
    assert "shopify-process-grid" in html
    assert "shopify-process-audit" in html
    assert "shopify-process-generate" in html
    assert "shopify-process-publish" in html
    assert "Auditar loja" in html
    assert "Gerar sugestoes com IA" in html
    assert "Revisar e publicar" in html
    assert "/shopify/credentials" in html
    assert "shopify-workflow" in html
    assert "shopify-tab-audit" in html
    assert "shopify-tab-queue" in html
    assert "shopify-tab-publish" in html
    assert "shopify-tab-log" in html
    assert "Descrição Shopify sugerida" in html
    assert "setWorkflowState" in html
    assert "setShopifyTab" in html
    assert '<option value="groq" selected>' in html
    assert "async function runShopifyAudit" in html
    assert "/shopify/queue/delete" in html
    assert "deleteSelectedShopify" in html
    assert "shopify-settings-form" not in html
    assert "shopify-kpis" not in html
    assert "split(String.fromCharCode(10))" in html
    assert "split('\\n')" not in html
    assert "split('\n')" not in html


def test_shopify_credentials_page_renders_form(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")

    response = dashboard.app.test_client().get("/shopify/credentials")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Credenciais Shopify" in html
    assert "shopify-settings-form" in html
    assert 'name="shopify_client_id"' in html
    assert 'name="shopify_client_secret"' in html
    assert 'name="shopify_site_name"' in html
    assert 'name="shopify_business_context"' in html
    assert 'name="shopify_content_guidelines"' in html
    assert 'autocomplete="new-password"' in html
    assert "/shopify/settings" in html


def test_shopify_queue_endpoint_returns_payload(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(
        "modules.shopify_seo.load_queue",
        lambda: [
            {
                "id": "gid://shopify/Product/1",
                "resource_type": "product",
                "path": "/products/produto",
                "title": "Produto",
                "status": "pending_review",
                "current": {"seo_title": ""},
                "proposal": {"seo_title": "Produto Premium Secret Shop"},
            }
        ],
    )

    response = dashboard.app.test_client().get("/shopify/queue")
    data = response.get_json()

    assert response.status_code == 200
    assert data["counts"]["pending_review"] == 1
    assert data["rows"][0]["path"] == "/products/produto"
    assert data["rows"][0]["resource_label"] == "Produto"
    assert data["rows"][0]["status_label"] == "Em revisão"


def test_shopify_queue_delete_endpoint_removes_selected_keys(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    import modules.shopify_seo as shopify_seo

    calls = {}

    def fake_delete_queue_items(keys=None, urls_filter=None):
        calls["keys"] = set(keys or [])
        calls["urls_filter"] = urls_filter
        return 1, []

    monkeypatch.setattr(shopify_seo, "delete_queue_items", fake_delete_queue_items)
    monkeypatch.setattr(
        dashboard,
        "_shopify_queue_payload",
        lambda limit=120: {"counts": {"pending_review": 0}, "total": 0, "rows": [], "queue_file": "queue.json"},
    )

    response = dashboard.app.test_client().post(
        "/shopify/queue/delete",
        json={"keys": ["product:gid://shopify/Product/1"]},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["deleted"] == 1
    assert calls["keys"] == {"product:gid://shopify/Product/1"}
    assert calls["urls_filter"] is None


def test_shopify_queue_delete_requires_selection(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")

    response = dashboard.app.test_client().post("/shopify/queue/delete", json={"keys": []})

    assert response.status_code == 400
    assert "Selecione" in response.get_json()["error"]


def test_shopify_audit_uses_url_targeted_fetch(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    import modules.shopify_seo as shopify_seo

    calls = {}
    monkeypatch.setattr(shopify_seo.ShopifyCredentials, "from_env", staticmethod(lambda: object()))
    monkeypatch.setattr(shopify_seo, "ShopifyGraphQLClient", lambda _credentials: object())

    def fake_fetch(_client, resource, urls, limit, query):
        calls.update({"resource": resource, "urls": urls, "limit": limit, "query": query})
        return [
            {
                "resource_type": "collection",
                "id": "gid://shopify/Collection/1",
                "handle": "lacoste",
                "title": "Lacoste",
                "description_text": "",
                "seo_title": "",
                "seo_description": "",
            }
        ]

    monkeypatch.setattr(shopify_seo, "fetch_resources_for_urls", fake_fetch)

    response = dashboard.app.test_client().post(
        "/shopify/audit",
        json={
            "resource": "collections",
            "limit": 20,
            "query": "status:active",
            "urls": "/collections/lacoste",
        },
    )
    data = response.get_json()

    assert response.status_code == 200
    assert calls == {
        "resource": "collections",
        "urls": ["/collections/lacoste"],
        "limit": 20,
        "query": "status:active",
    }
    assert data["total"] == 1
    assert data["rows"][0]["path"] == "/collections/lacoste"


def test_shopify_problem_translation_is_portuguese():
    detail = dashboard._shopify_problem_detail("SEO description missing in Shopify")

    assert detail["title"] == "Meta description ausente"
    assert "Shopify" in detail["detail"]
    assert detail["severity"] == "high"


def test_shopify_settings_rejects_browser_autofill(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    updates = []
    monkeypatch.setattr(dashboard, "_update_env_file", lambda key, value: updates.append((key, value)))

    response = dashboard.app.test_client().post(
        "/shopify/settings",
        json={
            "store_domain": "wiyvq0-4w.myshopify.com",
            "api_version": "2026-04",
            "public_base_url": "https://www.secretshop.com.br",
            "client_id": "victoralves.secret@example.com",
            "client_secret": "senha-salva",
        },
    )

    assert response.status_code == 400
    assert not any(key == "SHOPIFY_CLIENT_ID" for key, _ in updates)
    assert not any(key == "SHOPIFY_CLIENT_SECRET" for key, _ in updates)


def test_shopify_settings_saves_manual_credential_fields(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    updates = []
    monkeypatch.setattr(dashboard, "_update_env_file", lambda key, value: updates.append((key, value)))

    response = dashboard.app.test_client().post(
        "/shopify/settings",
        json={
            "store_domain": "wiyvq0-4w.myshopify.com",
            "api_version": "2026-04",
            "public_base_url": "https://www.secretshop.com.br",
            "shopify_client_id": "88e6c3ec6172db9de0194320d486d69c",
            "shopify_client_secret": "shopify-client-secret-test",
            "shopify_site_name": "Nova Loja",
            "shopify_business_context": "Moda feminina\npremium",
            "shopify_content_guidelines": "Nao citar Secret Outlet.",
        },
    )

    assert response.status_code == 200
    assert ("SHOPIFY_CLIENT_ID", "88e6c3ec6172db9de0194320d486d69c") in updates
    assert ("SHOPIFY_CLIENT_SECRET", "shopify-client-secret-test") in updates
    assert ("SHOPIFY_SITE_NAME", "Nova Loja") in updates
    assert ("SHOPIFY_BUSINESS_CONTEXT", "Moda feminina premium") in updates
    assert ("SHOPIFY_CONTENT_GUIDELINES", "Nao citar Secret Outlet.") in updates


def test_shopify_settings_authenticated_saves_to_active_site_settings(monkeypatch):
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    cfg = {"site_id": "site-1", "SHOPIFY_CLIENT_ID": "old-client"}
    monkeypatch.setattr(dashboard, "_load_active_site_config", lambda: cfg)
    updates = {}
    monkeypatch.setattr(dashboard, "_update_active_user_site_config", lambda **kwargs: (updates.update(kwargs), cfg.update(kwargs)))

    def _fail_env_write(_key, _value):
        raise AssertionError("production save must not write .env")

    monkeypatch.setattr(dashboard, "_update_env_file", _fail_env_write)

    response = dashboard.app.test_client().post(
        "/shopify/settings",
        json={
            "store_domain": "novaloja.myshopify.com",
            "api_version": "2026-04",
            "public_base_url": "https://www.novaloja.com.br",
            "shopify_client_id": "client-id",
            "shopify_client_secret": "client-secret",
            "shopify_site_name": "Nova Loja",
            "shopify_business_context": "Moda premium",
            "shopify_content_guidelines": "Usar PT-BR.",
        },
    )

    assert response.status_code == 200
    assert updates["SHOPIFY_STORE_DOMAIN"] == "novaloja.myshopify.com"
    assert updates["SHOPIFY_CLIENT_ID"] == "client-id"
    assert updates["SHOPIFY_CLIENT_SECRET"] == "client-secret"
    assert response.get_json()["config"]["ready"] is True


def test_shopify_runtime_context_uses_shopify_fields(monkeypatch):
    monkeypatch.setenv("SHOPIFY_PUBLIC_BASE_URL", "https://www.novaloja.com.br")
    monkeypatch.setenv("SHOPIFY_STORE_DOMAIN", "novaloja.myshopify.com")
    monkeypatch.setenv("SHOPIFY_SITE_NAME", "Nova Loja")
    monkeypatch.setenv("SHOPIFY_BUSINESS_CONTEXT", "Loja de moda feminina premium.")
    monkeypatch.setenv("SHOPIFY_CONTENT_GUIDELINES", "Nao citar Secret Outlet.")
    monkeypatch.setenv("BUSINESS_CONTEXT", "Secret Outlet antiga.")

    cfg = dashboard._shopify_runtime_context_config()

    assert cfg["site_url"] == "https://www.novaloja.com.br"
    assert cfg["site_name"] == "Nova Loja"
    assert cfg["business_context"] == "Loja de moda feminina premium."
    assert cfg["content_guidelines"] == "Nao citar Secret Outlet."


def test_shopify_job_append_works_outside_request_context(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "1")
    job_id = "shopify-background-test"
    dashboard._register_job(job_id, {
        "user_id": "user-1",
        "status": "running",
        "stdout": "",
    })

    dashboard._shopify_job_append(job_id, "linha do worker")

    job = dashboard._get_job(job_id)
    assert job["stdout"] == "linha do worker\n"
