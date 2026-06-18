import os

import app as dashboard


def test_gsc_property_normalization_keeps_domain_property_exact():
    assert dashboard.normalize_gsc_property("sc-domain:example.com/") == "sc-domain:example.com"
    assert dashboard.normalize_gsc_property("https://www.example.com") == "https://www.example.com/"


class _Result:
    def __init__(self, data=None):
        self.data = data


class _Query:
    def __init__(self, db, table):
        self.db = db
        self.table = table
        self.action = "select"
        self.payload = None
        self.filters = {}

    def select(self, *_args, **_kwargs):
        self.action = "select"
        return self

    def update(self, payload):
        self.action = "update"
        self.payload = payload
        return self

    def upsert(self, payload, **_kwargs):
        self.action = "upsert"
        self.payload = payload
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        rows = self.db[self.table]
        if self.action == "select":
            filtered = [
                row for row in rows
                if all(str(row.get(key)) == str(value) for key, value in self.filters.items())
            ]
            return _Result(filtered)
        if self.action == "update":
            for row in rows:
                if all(str(row.get(key)) == str(value) for key, value in self.filters.items()):
                    row.update(self.payload)
            return _Result([self.payload])
        if self.action == "upsert":
            self.db["upserts"].append(self.payload)
            if self.table == "user_site_settings":
                rows[:] = [
                    row for row in rows
                    if not (
                        str(row.get("user_id")) == str(self.payload.get("user_id"))
                        and str(row.get("site_id")) == str(self.payload.get("site_id"))
                    )
                ]
                rows.append(self.payload)
            return _Result([self.payload])
        return _Result([])


class _Supabase:
    def __init__(self):
        self.db = {
            "sites": [{"id": "site-1", "owner_user_id": "user-1", "base_url": "https://example.com"}],
            "user_site_settings": [
                {
                    "user_id": "user-1",
                    "site_id": "site-1",
                    "site_url": "https://example.com",
                    "site_name": "Example",
                    "settings": {
                        "site_id": "site-1",
                        "user_id": "user-1",
                        "site_url": "https://example.com",
                        "site_name": "Example",
                        "gsc_property": "https://old.example.com/",
                        "gsc_token_json": '{"token":"abc"}',
                        "available_gsc_sites": ["https://example.com/", "sc-domain:example.com"],
                        "available_ga4_properties": [{"property": "properties/123", "display_name": "GA4 Example"}],
                        "gsc_account_email": "user@example.com",
                        "ai_api_keys": {"gemini": "gemini-key"},
                    },
                }
            ],
            "upserts": [],
        }

    def table(self, name):
        return _Query(self.db, name)


def test_saving_site_form_preserves_gsc_auth_and_private_settings(monkeypatch):
    fake = _Supabase()
    monkeypatch.setattr(dashboard, "get_supabase", lambda: fake)

    with dashboard.app.test_request_context("/settings", method="POST"):
        dashboard.session["user_id"] = "user-1"
        dashboard.session["active_site_id"] = "site-1"
        dashboard.session["auth_project_url"] = os.environ.get("SUPABASE_URL", "")

        dashboard._save_user_site_config({
            "site_url": "https://example.com",
            "site_name": "Example",
            "gsc_property": "sc-domain:example.com",
            "ga4_property": "456",
            "business_context": "Contexto atualizado",
        })

    settings = fake.db["upserts"][-1]["settings"]
    assert settings["gsc_property"] == "sc-domain:example.com"
    assert settings["business_context"] == "Contexto atualizado"
    assert settings["gsc_token_json"] == '{"token":"abc"}'
    assert settings["available_gsc_sites"] == ["https://example.com/", "sc-domain:example.com"]
    assert settings["ga4_property"] == "properties/456"
    assert settings["available_ga4_properties"] == [{"property": "properties/123", "display_name": "GA4 Example"}]
    assert settings["gsc_account_email"] == "user@example.com"
    assert settings["ai_api_keys"] == {"gemini": "gemini-key"}
    assert "_new_site" not in settings


def test_saving_site_config_can_still_clear_gsc_token_explicitly(monkeypatch):
    fake = _Supabase()
    monkeypatch.setattr(dashboard, "get_supabase", lambda: fake)

    with dashboard.app.test_request_context("/settings/gsc/disconnect"):
        dashboard.session["user_id"] = "user-1"
        dashboard.session["active_site_id"] = "site-1"
        dashboard.session["auth_project_url"] = os.environ.get("SUPABASE_URL", "")

        dashboard._save_user_site_config({
            "site_url": "https://example.com",
            "site_name": "Example",
            "gsc_token_json": None,
        })

    settings = fake.db["upserts"][-1]["settings"]
    assert settings["gsc_token_json"] is None
    assert settings["available_gsc_sites"] == ["https://example.com/", "sc-domain:example.com"]


def test_settings_selects_sc_domain_property_without_trailing_slash(monkeypatch):
    cfg = {
        "site_id": "site-1",
        "user_id": "user-1",
        "site_url": "https://example.com",
        "site_name": "Example",
        "gsc_property": "sc-domain:example.com/",
        "gsc_token_json": '{"token":"abc"}',
        "available_gsc_sites": [
            "sc-domain:old.example.com",
            "sc-domain:example.com",
            "https://www.example.com/",
        ],
        "available_ga4_properties": [],
        "gsc_account_email": "user@example.com",
    }

    monkeypatch.setattr(dashboard, "_is_authenticated", lambda: True)
    monkeypatch.setattr(dashboard, "_current_site_id", lambda: "site-1")
    monkeypatch.setattr(dashboard, "_current_user_id", lambda: "user-1")
    monkeypatch.setattr(dashboard, "_current_user_email", lambda: "user@example.com")
    monkeypatch.setattr(dashboard, "_current_user_name", lambda: "User")
    monkeypatch.setattr(dashboard, "_load_user_sites", lambda *_args, **_kwargs: [{
        "site_id": "site-1",
        "site_url": "https://example.com",
        "site_name": "Example",
    }])
    monkeypatch.setattr(dashboard, "_load_active_site_config", lambda: dict(cfg))
    monkeypatch.setattr(dashboard, "_site_has_gsc_token", lambda *_args, **_kwargs: True)

    with dashboard.app.test_request_context("/settings"):
        html = dashboard.settings()

    assert '<option value="sc-domain:example.com" selected>' in html
    assert 'value="sc-domain:example.com/" selected' not in html
    assert 'id="site-url-input"' in html
    assert 'id="gsc-property-input"' in html
    assert "syncSiteUrlWithGscProperty" in html
