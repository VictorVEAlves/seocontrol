import app as dashboard


def _stub_settings_state(monkeypatch):
    monkeypatch.setattr(dashboard, "_load_user_sites", lambda: [])
    monkeypatch.setattr(
        dashboard,
        "_load_active_site_config",
        lambda: {
            "site_id": "site-1",
            "site_url": "https://example.com",
            "site_name": "Example",
            "gsc_property": "",
        },
    )
    monkeypatch.setattr(dashboard, "_google_oauth_ready", lambda: False)


def _login_test_client():
    client = dashboard.app.test_client()
    with client.session_transaction() as sess:
        sess["user_id"] = "user-1"
        sess["user_email"] = "old@example.com"
        sess["user_metadata"] = {"display_name": "Nome Antigo"}
        sess["access_token"] = "access-token"
        sess["refresh_token"] = "refresh-token"
        sess["auth_project_url"] = dashboard.os.environ.get("SUPABASE_URL", "")
    return client


def test_settings_page_shows_user_account_form(monkeypatch):
    _stub_settings_state(monkeypatch)

    response = _login_test_client().get("/settings")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Conta do usuário" in html
    assert 'name="display_name"' in html
    assert 'name="user_email"' in html
    assert 'name="user_password"' in html


def test_settings_user_form_updates_supabase_auth(monkeypatch):
    _stub_settings_state(monkeypatch)
    calls = []

    class User:
        id = "user-1"
        email = "novo@example.com"
        user_metadata = {"display_name": "Victor", "full_name": "Victor"}

    class UpdateResponse:
        user = User()
        session = None

    class FakeAuth:
        def set_session(self, access_token, refresh_token):
            calls.append(("set_session", access_token, refresh_token))

        def update_user(self, payload):
            calls.append(("update_user", payload))
            return UpdateResponse()

    class FakeClient:
        auth = FakeAuth()

    monkeypatch.setattr(dashboard, "create_client", lambda _url, _key: FakeClient())

    client = _login_test_client()
    response = client.post(
        "/settings",
        data={
            "action": "user",
            "display_name": "Victor",
            "user_email": "novo@example.com",
            "user_password": "nova-senha",
            "user_password_confirm": "nova-senha",
        },
    )

    assert response.status_code == 200
    assert calls == [
        ("set_session", "access-token", "refresh-token"),
        (
            "update_user",
            {
                "data": {"display_name": "Victor", "full_name": "Victor"},
                "email": "novo@example.com",
                "password": "nova-senha",
            },
        ),
    ]
    assert "Dados do usuário salvos" in response.get_data(as_text=True)

    with client.session_transaction() as sess:
        assert sess["user_email"] == "novo@example.com"
        assert sess["user_metadata"]["display_name"] == "Victor"


def test_settings_user_form_rejects_mismatched_passwords(monkeypatch):
    _stub_settings_state(monkeypatch)
    calls = []
    monkeypatch.setattr(dashboard, "create_client", lambda _url, _key: calls.append("create_client"))

    response = _login_test_client().post(
        "/settings",
        data={
            "action": "user",
            "display_name": "Victor",
            "user_email": "old@example.com",
            "user_password": "senha-um",
            "user_password_confirm": "senha-dois",
        },
    )

    assert response.status_code == 200
    assert calls == []
    assert "As senhas não conferem" in response.get_data(as_text=True)
