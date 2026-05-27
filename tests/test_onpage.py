from bs4 import BeautifulSoup

from analyzers import onpage


def test_onpage_audit_uses_mocked_html_without_network(monkeypatch):
    html = """
    <html>
      <head>
        <title>Lacoste Outlet | Roupas e Tenis com Desconto</title>
        <meta name="description" content="Compre Lacoste original com desconto, nota fiscal, parcelamento e envio para todo o Brasil no outlet autorizado.">
        <link rel="canonical" href="https://www.secretoutlet.com.br/lacoste">
        <script type="application/ld+json">{"@type":"ItemList"}</script>
      </head>
      <body>
        <h1>Lacoste Original no Outlet Autorizado</h1>
        <h2>Produtos Lacoste em oferta</h2>
        <p>Moda masculina premium com camisetas, polos, tenis e acessorios para diferentes ocasioes.</p>
        <p>Escolha pecas originais com curadoria, desconto e condicoes de pagamento.</p>
      </body>
    </html>
    """

    def fake_get_page(url):
        return 200, BeautifulSoup(html, "lxml"), {}, url

    monkeypatch.setattr("modules.onpage.get_page", fake_get_page)

    result = onpage.audit_page("https://www.secretoutlet.com.br/lacoste")

    assert result["status"] == 200
    assert result["h1_count"] == 1
    assert result["has_product_schema"]
    assert "Meta title ausente" not in result["issues"]
    assert "Meta keywords ausente" not in result["warnings"]
