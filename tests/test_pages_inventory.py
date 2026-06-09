import app as dashboard


def _fake_pages_payload(*_args, **_kwargs):
    return {
        "period_days": 28,
        "period": "2026-05-10 → 2026-06-06",
        "total": 2,
        "rows": [
            {
                "source": "GSC",
                "url": "https://example.com/lacoste",
                "traffic": 5600,
                "previous_traffic": 1300,
                "traffic_delta": 4300,
                "traffic_share": 5.63,
                "keywords": 353,
                "top_keyword": "camiseta lacoste",
                "status": "active",
            },
            {
                "source": "GSC",
                "url": "https://example.com/reserva",
                "traffic": 6800,
                "previous_traffic": 600,
                "traffic_delta": 6200,
                "traffic_share": 6.88,
                "keywords": 162,
                "top_keyword": "reserva",
                "status": "new",
            },
        ],
    }


def test_pages_screen_renders_inventory_shell(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")

    response = dashboard.app.test_client().get("/pages")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Todas as páginas" in html
    assert "Novidades" in html
    assert "Perdidas" in html
    assert "Prompts de LLM" not in html
    assert "Domínios de ref." not in html
    assert "Intenção" not in html
    assert "/pages/data" in html
    assert "/pages/export.csv" in html


def test_pages_data_returns_gsc_inventory(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    import modules.gsc_api as gsc_api

    monkeypatch.setattr(gsc_api, "get_pages_inventory", _fake_pages_payload)

    response = dashboard.app.test_client().get("/pages/data?period=28&limit=2500")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["total"] == 2
    assert payload["rows"][0]["url"] == "https://example.com/lacoste"
    assert payload["rows"][0]["keywords"] == 353


def test_pages_export_csv_uses_inventory(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "false")
    import modules.gsc_api as gsc_api

    monkeypatch.setattr(gsc_api, "get_pages_inventory", _fake_pages_payload)

    response = dashboard.app.test_client().get("/pages/export.csv?period=28&limit=2500")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    assert "Fonte,URL,Trafego" in text
    assert "https://example.com/reserva" in text
    assert "camiseta lacoste" in text
