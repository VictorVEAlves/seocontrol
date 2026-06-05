from modules import shopify_seo


class FakeResponse:
    def __init__(self, payload=None, status_code=200, text=""):
        self._payload = payload or {}
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError("bad response")

    def json(self):
        return self._payload


def _product(handle, title="", desc="", body=""):
    return {
        "resource_type": "product",
        "id": f"gid://shopify/Product/{handle}",
        "handle": handle,
        "title": handle.title(),
        "description_text": body,
        "seo_title": title,
        "seo_description": desc,
    }


def test_audit_resources_flags_missing_short_and_duplicates(monkeypatch):
    monkeypatch.setattr(shopify_seo, "_public_base_url", lambda: "https://www.secretshop.com.br")
    resources = [
        _product("polo-lacoste", title="", desc=""),
        _product("camiseta-premium", title="Title repetido para teste com tamanho ideal", desc="D" * 145),
        _product("camiseta-basica", title="Title repetido para teste com tamanho ideal", desc="D" * 145),
    ]

    audited = shopify_seo.audit_resources(resources)

    assert audited[0]["needs_optimization"] is True
    assert "SEO title missing in Shopify" in audited[0]["issues"]
    assert "SEO description missing in Shopify" in audited[0]["issues"]
    assert "SEO title duplicated in Shopify" in audited[1]["warnings"]
    assert "SEO title duplicated in Shopify" in audited[2]["warnings"]
    assert audited[0]["url"] == "https://www.secretshop.com.br/products/polo-lacoste"


def test_audit_resources_flags_truncated_meta_description():
    resource = _product(
        "calcas-de-sarja",
        title="Calças de Sarja | Compre com Descontos Exclusivos",
        desc="Encontre as melhores calças de sarja com descontos exclusivos e qualidade garantida, compre agora e aproveite os benefícios de nossa...",
        body=" ".join(["conteudo"] * 45),
    )

    audited = shopify_seo.audit_resources([resource])

    assert "SEO description ends with ellipsis in Shopify" in audited[0]["warnings"]
    assert audited[0]["needs_optimization"] is True


def test_upsert_queue_uses_shopify_specific_file(tmp_path):
    queue = tmp_path / "shopify_seo_changes.json"
    changes = [
        {
            "id": "gid://shopify/Product/1",
            "resource_type": "product",
            "status": "pending_review",
            "proposal": {"seo_title": "Novo title", "seo_description": "Nova description"},
        }
    ]

    result = shopify_seo.upsert_queue(changes, path=queue)

    assert result == changes
    assert queue.exists()
    assert "Novo title" in queue.read_text(encoding="utf-8")


def test_generate_changes_marks_thin_description_for_update(monkeypatch):
    def fake_generate_for_page(page, gsc_data=None, provider=None, api_key=None):
        return {
            "meta_title": "Calças de Sarja | Compre com Descontos Exclusivos",
            "meta_description": "Encontre calças de sarja masculinas e femininas com descontos exclusivos, entrega rápida e ofertas especiais para comprar online.",
            "h1": "Calças de Sarja com Descontos Exclusivos",
            "description_html": "<h2>Calças de Sarja</h2><p>Conteúdo completo para a coleção.</p>",
            "_provider": "groq",
        }

    monkeypatch.setattr(
        "modules.content_generator.generate_for_page",
        fake_generate_for_page,
    )
    resources = [_product("calcas-de-sarja", title="T" * 50, desc="D" * 145, body="")]

    changes = shopify_seo.generate_changes(
        resources,
        provider="groq",
        api_key="key",
        only_needs=True,
    )

    assert changes[0]["current"]["description_words"] == 0
    assert changes[0]["proposal"]["update_description_html"] is True
    assert "<h2>Calças de Sarja</h2>" in changes[0]["proposal"]["description_html"]


def test_generate_changes_does_not_overwrite_good_description(monkeypatch):
    def fake_generate_for_page(page, gsc_data=None, provider=None, api_key=None):
        return {
            "meta_title": "Novo Title SEO com Tamanho Ideal para Produto",
            "meta_description": "Meta description completa para produto com descrição atual já suficiente e sem necessidade de sobrescrever conteúdo.",
            "h1": "Produto com descrição atual suficiente",
            "description_html": "<p>Nova descrição gerada.</p>",
            "_provider": "groq",
        }

    monkeypatch.setattr(
        "modules.content_generator.generate_for_page",
        fake_generate_for_page,
    )
    good_body = " ".join(["palavra"] * 45)
    resources = [_product("produto", title="", desc="D" * 145, body=good_body)]

    changes = shopify_seo.generate_changes(
        resources,
        provider="groq",
        api_key="key",
        only_needs=True,
    )

    assert changes[0]["current"]["description_words"] == 45
    assert changes[0]["proposal"]["update_description_html"] is False


def test_generate_changes_uses_shopify_context(monkeypatch):
    from config import get_business_context, get_content_guidelines, get_site_name

    captured = {}

    def fake_generate_for_page(page, gsc_data=None, provider=None, api_key=None):
        captured["site_name"] = get_site_name()
        captured["business_context"] = get_business_context()
        captured["content_guidelines"] = get_content_guidelines()
        return {
            "meta_title": "Nova Loja | Moda Feminina Premium Online",
            "meta_description": "Conheca moda feminina premium na Nova Loja, com selecao atual para compor looks elegantes em diferentes ocasioes.",
            "h1": "Moda feminina premium na Nova Loja",
            "description_html": "<p>Conteudo gerado.</p>",
            "_provider": "groq",
        }

    monkeypatch.setenv("SHOPIFY_PUBLIC_BASE_URL", "https://www.novaloja.com.br")
    monkeypatch.setenv("SHOPIFY_SITE_NAME", "Nova Loja")
    monkeypatch.setenv("SHOPIFY_BUSINESS_CONTEXT", "Loja de moda feminina premium.")
    monkeypatch.setenv("SHOPIFY_CONTENT_GUIDELINES", "Nao citar Secret Outlet.")
    monkeypatch.setenv("BUSINESS_CONTEXT", "Secret Outlet antiga.")
    monkeypatch.setattr("modules.content_generator.generate_for_page", fake_generate_for_page)

    shopify_seo.generate_changes(
        [_product("vestido", title="", desc="", body="")],
        provider="groq",
        api_key="key",
        only_needs=True,
    )

    assert captured["site_name"] == "Nova Loja"
    assert captured["business_context"] == "Loja de moda feminina premium."
    assert captured["content_guidelines"] == "Nao citar Secret Outlet."


def test_approve_queue_requires_selection(tmp_path):
    queue = tmp_path / "shopify_seo_changes.json"
    shopify_seo.save_queue(
        [
            {
                "id": "gid://shopify/Product/1",
                "resource_type": "product",
                "path": "/products/polo-lacoste",
                "status": "pending_review",
            }
        ],
        path=queue,
    )

    count, changes = shopify_seo.approve_queue(
        urls_filter=["/products/polo-lacoste"],
        approve_all=False,
        path=queue,
    )

    assert count == 1
    assert changes[0]["status"] == "approved"
    assert changes[0]["approved_at"]


def test_apply_approved_changes_skips_pending_review_items():
    class FakeClient:
        def __init__(self):
            self.calls = []

        def update_seo(self, change):
            self.calls.append(change)
            return {"id": change["id"]}

    changes = [
        {
            "id": "gid://shopify/Product/1",
            "resource_type": "product",
            "handle": "polo-lacoste",
            "status": "pending_review",
            "proposal": {"seo_title": "Pendente", "seo_description": "D" * 145},
        },
        {
            "id": "gid://shopify/Product/2",
            "resource_type": "product",
            "handle": "camiseta-premium",
            "status": "approved",
            "proposal": {"seo_title": "Aprovado", "seo_description": "D" * 145},
        },
    ]
    client = FakeClient()

    updated, log = shopify_seo.apply_approved_changes(client, changes, apply=True)

    assert len(client.calls) == 1
    assert client.calls[0]["handle"] == "camiseta-premium"
    assert updated[0]["status"] == "pending_review"
    assert updated[1]["status"] == "published"
    assert log[0]["status"] == "published"


def test_apply_without_apply_flag_is_dry_run():
    class FakeClient:
        def update_seo(self, change):
            raise AssertionError("dry run must not call Shopify")

    changes = [
        {
            "id": "gid://shopify/Collection/1",
            "resource_type": "collection",
            "handle": "camisetas",
            "status": "approved",
            "proposal": {"seo_title": "Camisetas Premium Secret Shop", "seo_description": "D" * 145},
        }
    ]

    updated, log = shopify_seo.apply_approved_changes(FakeClient(), changes, apply=False)

    assert updated == changes
    assert log[0]["status"] == "dry_run"
    assert log[0]["dry_run"] is True


def test_update_product_seo_includes_description_html_when_requested():
    client = object.__new__(shopify_seo.ShopifyGraphQLClient)
    calls = []

    def fake_graphql(mutation, variables):
        calls.append({"mutation": mutation, "variables": variables})
        return {"productUpdate": {"product": {"id": "gid://shopify/Product/1"}, "userErrors": []}}

    client.graphql = fake_graphql

    result = client.update_product_seo(
        "gid://shopify/Product/1",
        "Title SEO",
        "Meta description",
        "<h2>Descrição</h2><p>Texto.</p>",
    )

    assert result["id"] == "gid://shopify/Product/1"
    assert calls[0]["variables"]["product"]["descriptionHtml"].startswith("<h2>")


def test_update_collection_seo_includes_description_html_when_requested():
    client = object.__new__(shopify_seo.ShopifyGraphQLClient)
    calls = []

    def fake_graphql(mutation, variables):
        calls.append({"mutation": mutation, "variables": variables})
        return {"collectionUpdate": {"collection": {"id": "gid://shopify/Collection/1"}, "userErrors": []}}

    client.graphql = fake_graphql

    result = client.update_collection_seo(
        "gid://shopify/Collection/1",
        "Title SEO",
        "Meta description",
        "<h2>Coleção</h2><p>Texto.</p>",
    )

    assert result["id"] == "gid://shopify/Collection/1"
    assert calls[0]["variables"]["input"]["descriptionHtml"].startswith("<h2>")


def test_request_admin_token_uses_client_credentials_and_cache(monkeypatch, tmp_path):
    cache = tmp_path / "shopify_admin_token.json"
    monkeypatch.setattr(shopify_seo, "TOKEN_CACHE_FILE", cache)
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None, **_kwargs):
        calls.append({"url": url, "data": data, "headers": headers, "timeout": timeout})
        return FakeResponse(
            {
                "access_token": "generated-token",
                "expires_in": 3600,
                "scope": "read_products,write_products",
            }
        )

    monkeypatch.setattr(shopify_seo.requests, "post", fake_post)
    credentials = shopify_seo.ShopifyCredentials(
        store_domain="wiyvq0-4w.myshopify.com",
        admin_token="",
        client_id="client-id",
        client_secret="client-secret",
    )

    token = shopify_seo.request_admin_token(credentials)
    cached_token = shopify_seo.request_admin_token(credentials)

    assert token == "generated-token"
    assert cached_token == "generated-token"
    assert len(calls) == 1
    assert calls[0]["url"] == "https://wiyvq0-4w.myshopify.com/admin/oauth/access_token"
    assert calls[0]["data"]["grant_type"] == "client_credentials"
    assert calls[0]["data"]["client_id"] == "client-id"
    assert calls[0]["data"]["client_secret"] == "client-secret"


def test_shopify_credentials_read_runtime_site_config(monkeypatch):
    monkeypatch.delenv("SHOPIFY_STORE_DOMAIN", raising=False)
    monkeypatch.delenv("SHOPIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("SHOPIFY_CLIENT_SECRET", raising=False)
    shopify_seo.set_runtime_site_config({
        "SHOPIFY_STORE_DOMAIN": "runtime-store.myshopify.com",
        "SHOPIFY_CLIENT_ID": "runtime-client",
        "SHOPIFY_CLIENT_SECRET": "runtime-secret",
        "SHOPIFY_API_VERSION": "2026-04",
    })

    try:
        credentials = shopify_seo.ShopifyCredentials.from_env()
    finally:
        shopify_seo.clear_runtime_site_config()

    assert credentials.store_domain == "runtime-store.myshopify.com"
    assert credentials.client_id == "runtime-client"
    assert credentials.client_secret == "runtime-secret"


def test_fetch_resources_for_urls_uses_collection_handle_lookup(monkeypatch):
    monkeypatch.setattr(shopify_seo, "_public_base_url", lambda: "https://www.secretshop.com.br")

    class FakeClient:
        def fetch_collection_by_handle(self, handle):
            assert handle == "lacoste"
            return {
                "id": "gid://shopify/Collection/1",
                "title": "Lacoste",
                "handle": "lacoste",
                "descriptionHtml": "",
                "seo": {"title": "", "description": ""},
            }

        def fetch_product_by_handle(self, handle):
            raise AssertionError("product lookup should not run for collection URL")

        def fetch_collections(self, limit=None, query=None):
            raise AssertionError("direct URL lookup should avoid limited collection scan")

        def fetch_products(self, limit=None, query=None):
            raise AssertionError("product scan should not run")

    resources = shopify_seo.fetch_resources_for_urls(
        FakeClient(),
        "collections",
        ["/collections/lacoste"],
        limit=20,
        query="status:active",
    )

    assert len(resources) == 1
    assert resources[0]["handle"] == "lacoste"
    assert resources[0]["path"] == "/collections/lacoste"


def test_fetch_resources_for_urls_falls_back_to_full_collection_scan(monkeypatch):
    monkeypatch.setattr(shopify_seo, "_public_base_url", lambda: "https://www.secretshop.com.br")
    limits = []

    class FakeClient:
        def fetch_collection_by_handle(self, handle):
            raise RuntimeError("collectionByHandle unavailable")

        def fetch_collections(self, limit=None, query=None):
            limits.append(limit)
            if limit is None:
                return [
                    {
                        "id": "gid://shopify/Collection/1",
                        "title": "Lacoste",
                        "handle": "lacoste",
                        "descriptionHtml": "",
                        "seo": {"title": "", "description": ""},
                    }
                ]
            return []

        def fetch_products(self, limit=None, query=None):
            return []

    resources = shopify_seo.fetch_resources_for_urls(
        FakeClient(),
        "collections",
        ["/collections/lacoste"],
        limit=20,
        query="status:active",
    )

    assert limits == [None]
    assert len(resources) == 1
    assert resources[0]["handle"] == "lacoste"
