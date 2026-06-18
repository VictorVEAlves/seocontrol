import json
from datetime import date, timedelta

import app as dashboard
import config
import modules.ga4_api as ga4_api
import modules.gemini_insights as gemini_insights
import modules.gsc_api as gsc_api


class _FakeCreds:
    valid = False
    expired = True
    token = ""
    refresh_token = "refresh-token"
    token_uri = "https://oauth2.googleapis.com/token"
    client_id = "client-id"
    client_secret = "client-secret"

    def to_json(self):
        return json.dumps({
            "token": self.token,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })


class _FakeTokenResponse:
    ok = True
    text = ""

    def json(self):
        return {"access_token": "new-access-token", "expires_in": 3600}


class _FakeSession:
    trust_env = True

    def post(self, url, data=None, timeout=None):
        assert url == "https://oauth2.googleapis.com/token"
        assert data["grant_type"] == "refresh_token"
        assert timeout == 3
        return _FakeTokenResponse()


def test_gsc_bearer_refreshes_with_direct_timeout(monkeypatch, tmp_path):
    updates = {}
    config.set_runtime_site_config({"gsc_token_json": "old-token-json"})
    monkeypatch.setattr(gsc_api, "get_gsc_token_json", lambda: "old-token-json")
    monkeypatch.setattr(gsc_api, "update_runtime_site_config", lambda **kwargs: updates.update(kwargs))
    monkeypatch.setattr(gsc_api, "_load_authorized_credentials", lambda token_file: _FakeCreds())
    monkeypatch.setattr("requests.Session", lambda: _FakeSession())

    token = gsc_api._get_gsc_bearer(timeout=1)

    assert token == "new-access-token"
    assert "new-access-token" in updates["gsc_token_json"]

    config.clear_runtime_site_config()


def test_dashboard_persists_refreshed_gsc_token(monkeypatch):
    saved = {}
    old_token = "old-token-json"
    new_token = "new-token-json"

    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {"gsc_token_json": old_token}))
    monkeypatch.setattr(dashboard, "_auth_required", lambda: False)
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "get_gsc_token_json", lambda: new_token)
    monkeypatch.setattr(dashboard, "_update_active_user_site_config", lambda **kwargs: saved.update(kwargs))

    def fake_get_dashboard_data(period_days=28):
        return {"kpis": {}, "time_series": [], "top_queries": [], "top_pages": []}

    config.set_runtime_site_config({"gsc_token_json": old_token})
    monkeypatch.setattr(gsc_api, "get_dashboard_data", fake_get_dashboard_data)

    response = dashboard.app.test_client().get("/dashboard/data?period=28")

    assert response.status_code == 200
    assert saved["gsc_token_json"] == new_token

    config.clear_runtime_site_config()


def test_dashboard_data_passes_custom_date_range(monkeypatch):
    calls = {}
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(
        dashboard,
        "_dashboard_setup_status",
        lambda: (True, "", {"gsc_token_json": "old-token"}),
    )
    monkeypatch.setattr(dashboard, "_persist_dashboard_refreshed_gsc_token", lambda initial="": None)

    def fake_get_dashboard_data(period_days=28, start_date=None, end_date=None):
        calls.update(period_days=period_days, start_date=start_date, end_date=end_date)
        return {"kpis": {}, "time_series": [], "top_queries": [], "top_pages": []}

    monkeypatch.setattr(gsc_api, "get_dashboard_data", fake_get_dashboard_data)

    response = dashboard.app.test_client().get(
        "/dashboard/data?start_date=2026-06-01&end_date=2026-06-10"
    )

    assert response.status_code == 200
    assert calls == {
        "period_days": 10,
        "start_date": "2026-06-01",
        "end_date": "2026-06-10",
    }


def test_dashboard_data_reconnect_required_clears_stale_google_token(monkeypatch, tmp_path):
    saved = {}
    token_file = tmp_path / "stale-token.json"
    token_file.write_text("stale", encoding="utf-8")

    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(
        dashboard,
        "_dashboard_setup_status",
        lambda: (True, "", {"gsc_token_json": "old-token"}),
    )
    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_active_gsc_files", lambda: (tmp_path / "creds.json", token_file))
    monkeypatch.setattr(dashboard, "_update_active_user_site_config", lambda **kwargs: saved.update(kwargs))
    monkeypatch.setattr(
        gsc_api,
        "get_dashboard_data",
        lambda period_days=28: {
            "error": "refresh do GSC falhou: Token has been expired or revoked."
        },
    )

    response = dashboard.app.test_client().get("/dashboard/data?period=28")

    payload = response.get_json()
    assert response.status_code == 401
    assert payload["google_reconnect_required"] is True
    assert payload["reconnect_url"].endswith("/settings#gsc")
    assert saved["gsc_token_json"] is None
    assert saved["available_gsc_sites"] is None
    assert saved["available_ga4_properties"] is None
    assert not token_file.exists()


def test_dashboard_rejects_future_custom_end_date(monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRED", "0")
    monkeypatch.setattr(dashboard, "_dashboard_setup_status", lambda: (True, "", {}))
    start = date.today() - timedelta(days=2)
    future = date.today() + timedelta(days=1)

    response = dashboard.app.test_client().get(
        f"/dashboard/data?start_date={start}&end_date={future}"
    )

    assert response.status_code == 400
    assert "posterior a ontem" in response.get_json()["error"]


def test_dashboard_ai_prompt_includes_organic_revenue(monkeypatch, tmp_path):
    captured = {}

    monkeypatch.setattr(
        gsc_api,
        "_dashboard_cache_file",
        lambda kind, period: tmp_path / f"{kind}_{period}.json",
    )
    monkeypatch.setattr(
        gsc_api,
        "get_dashboard_data",
        lambda period_days=28, start_date=None, end_date=None: {
            "kpis": {
                "clicks": {"value": 1000, "prev": 800, "delta": 25.0},
                "impressions": {"value": 50000, "prev": 45000, "delta": 11.1},
                "ctr": {"value": 2.0, "prev": 1.78, "delta": 12.4},
                "position": {"value": 6.0, "prev": 6.5, "delta": -7.7},
            },
            "top_queries": [],
            "top_pages": [],
        },
    )
    monkeypatch.setattr(
        ga4_api,
        "get_revenue_summary",
        lambda **kwargs: {
            "kpis": {
                "revenue": {"value": 1500, "prev": 1000, "delta": 50.0},
                "purchases": {"value": 10, "prev": 8, "delta": 25.0},
                "avg_purchase_revenue": {"value": 150, "prev": 125, "delta": 20.0},
                "sessions": {"value": 900, "prev": 750, "delta": 20.0},
            }
        },
    )
    monkeypatch.setattr(config, "get_provider_api_key", lambda provider: "gemini-key")
    monkeypatch.setattr(gemini_insights, "_GEMINI_MODELS", ["test-model"])
    monkeypatch.setattr(gemini_insights, "_extract_text", lambda payload: "Analise pronta")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"ok": True}

    class FakeSession:
        trust_env = True

        def post(self, url, json=None, timeout=None):
            captured["prompt"] = json["contents"][0]["parts"][0]["text"]
            return FakeResponse()

    monkeypatch.setattr("requests.Session", lambda: FakeSession())

    result = gsc_api.get_dashboard_ai(
        period_days=10,
        start_date="2026-06-01",
        end_date="2026-06-10",
    )

    assert result["ai_summary"].startswith("**Impacto em Receita**")
    assert result["ai_summary"].endswith("Analise pronta")
    assert result["revenue_context_available"] is True
    assert result["revenue_highlights"]["revenue"] == 1500
    assert result["revenue_highlights"]["purchases"] == 10
    assert "Faturamento orgânico: R$ 1.500,00 (+50.0%)" in captured["prompt"]
    assert "Compras orgânicas: 10 (+25.0%)" in captured["prompt"]
    assert "Ticket médio orgânico: R$ 150,00 (+20.0%)" in captured["prompt"]
    assert "**Impacto em Receita**" in captured["prompt"]
