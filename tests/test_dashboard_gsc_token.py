import json

import app as dashboard
import config
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
