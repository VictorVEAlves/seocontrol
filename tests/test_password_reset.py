import app as dashboard


def test_login_exposes_forgot_password_and_recovery_hash_redirect():
    html = dashboard.app.test_client().get("/login").get_data(as_text=True)

    assert "/forgot-password" in html
    assert "Esqueci minha senha" in html
    assert 'window.location.replace("/reset-password" + hash)' in html


def test_forgot_password_sends_recovery_email_with_reset_redirect(monkeypatch):
    calls = []

    class FakeAuth:
        def reset_password_for_email(self, email, options=None):
            calls.append({"email": email, "options": options or {}})

    class FakeSupabase:
        auth = FakeAuth()

    monkeypatch.setattr(dashboard, "get_supabase_public", lambda: FakeSupabase())

    response = dashboard.app.test_client().post(
        "/forgot-password",
        data={"email": "cliente@example.com"},
    )

    assert response.status_code == 200
    assert calls[0]["email"] == "cliente@example.com"
    assert calls[0]["options"]["redirect_to"].endswith("/reset-password")
    assert "enviaremos um link" in response.get_data(as_text=True)


def test_reset_password_uses_recovery_session_to_update_password(monkeypatch):
    calls = []

    class FakeAuth:
        def set_session(self, access_token, refresh_token):
            calls.append(("set_session", access_token, refresh_token))

        def update_user(self, attributes):
            calls.append(("update_user", attributes))

    class FakeSupabase:
        auth = FakeAuth()

    monkeypatch.setattr(dashboard, "create_client", lambda url, key: FakeSupabase())

    client = dashboard.app.test_client()
    response = client.post(
        "/reset-password",
        data={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "password": "nova-senha",
            "confirm_password": "nova-senha",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/login"
    assert calls == [
        ("set_session", "access-token", "refresh-token"),
        ("update_user", {"password": "nova-senha"}),
    ]

    with client.session_transaction() as sess:
        assert sess["auth_notice"] == "Senha alterada com sucesso. Faça login novamente."


def test_reset_password_rejects_mismatched_passwords():
    response = dashboard.app.test_client().post(
        "/reset-password",
        data={
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "password": "senha-um",
            "confirm_password": "senha-dois",
        },
    )

    assert response.status_code == 200
    assert "As senhas não conferem" in response.get_data(as_text=True)
