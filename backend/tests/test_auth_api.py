from sqlalchemy import text

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
REFRESH = "/api/v1/auth/refresh"
LOGOUT = "/api/v1/auth/logout"
ME = "/api/v1/users/me"

PASSWORD = "a-strong-password"


def register(client, email="founder@example.com", password=PASSWORD):
    return client.post(REGISTER, json={"name": "Founder", "email": email, "password": password})


def set_cookies(response) -> dict[str, str]:
    """Cookie name -> full Set-Cookie header."""
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


def test_register_sets_http_only_cookies_and_hides_tokens(api_client):
    response = register(api_client)
    assert response.status_code == 201

    body = response.json()
    assert body["user"]["email"] == "founder@example.com"
    assert "password_hash" not in body["user"]
    assert "token" not in response.text

    cookies = set_cookies(response)
    assert "HttpOnly" in cookies["cp_access"] and "SameSite=lax" in cookies["cp_access"]
    assert "HttpOnly" in cookies["cp_refresh"] and "Path=/api/v1/auth" in cookies["cp_refresh"]


def test_password_is_stored_hashed(api_client, db_engine):
    register(api_client)
    with db_engine.connect() as conn:
        stored = conn.scalar(text("SELECT password_hash FROM users"))
    assert stored.startswith("$argon2id$") and PASSWORD not in stored


def test_me_requires_login(api_client):
    response = api_client.get(ME)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"

    register(api_client)
    me = api_client.get(ME)
    assert me.status_code == 200
    assert me.json()["email"] == "founder@example.com"


def test_duplicate_email_is_rejected_case_insensitively(api_client):
    register(api_client, "founder@example.com")
    response = register(api_client, "FOUNDER@Example.com")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_TAKEN"


def test_register_validates_input(api_client):
    assert register(api_client, email="not-an-email").status_code == 422
    assert register(api_client, password="short").status_code == 422


def test_login_failures_look_identical(api_client):
    register(api_client)
    api_client.cookies.clear()

    wrong_password = api_client.post(LOGIN, json={"email": "founder@example.com", "password": "wrong-password"})
    unknown_email = api_client.post(LOGIN, json={"email": "nobody@example.com", "password": "wrong-password"})

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]
    assert api_client.get(ME).status_code == 401


def test_login_then_me(api_client):
    register(api_client)
    api_client.cookies.clear()

    response = api_client.post(LOGIN, json={"email": "Founder@Example.com", "password": PASSWORD})
    assert response.status_code == 200
    assert api_client.get(ME).status_code == 200


def test_refresh_rotates_and_detects_reuse(api_client):
    register(api_client)
    first_refresh = api_client.cookies["cp_refresh"]

    rotated = api_client.post(REFRESH)
    assert rotated.status_code == 200
    second_refresh = api_client.cookies["cp_refresh"]
    assert second_refresh != first_refresh

    # Replaying the old token (e.g. stolen) ends the whole session...
    api_client.cookies.clear()
    api_client.cookies.set("cp_refresh", first_refresh)
    replay = api_client.post(REFRESH)
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "SESSION_EXPIRED"

    # ...including the newest token.
    api_client.cookies.clear()
    api_client.cookies.set("cp_refresh", second_refresh)
    assert api_client.post(REFRESH).status_code == 401


def test_logout_ends_session(api_client):
    register(api_client)
    refresh_token = api_client.cookies["cp_refresh"]

    response = api_client.post(LOGOUT)
    assert response.status_code == 204
    assert 'cp_access=""' in response.headers.get("set-cookie", "")

    api_client.cookies.clear()
    api_client.cookies.set("cp_refresh", refresh_token)
    assert api_client.post(REFRESH).status_code == 401


def test_disabled_user_is_locked_out(api_client, db_engine):
    register(api_client)
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE users SET is_active = false"))

    # Existing access token stops working immediately...
    assert api_client.get(ME).status_code == 401
    # ...refresh fails...
    assert api_client.post(REFRESH).status_code == 401
    # ...and so does a fresh login.
    api_client.cookies.clear()
    response = api_client.post(LOGIN, json={"email": "founder@example.com", "password": PASSWORD})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACCOUNT_DISABLED"


def test_invalid_access_cookie_is_rejected(api_client):
    api_client.cookies.set("cp_access", "forged.token.value")
    assert api_client.get(ME).status_code == 401


def test_registration_is_rate_limited(api_client):
    statuses = [register(api_client, f"user{i}@example.com").status_code for i in range(6)]
    assert statuses[:5] == [201] * 5
    assert statuses[5] == 429


def test_login_is_rate_limited_per_email(api_client):
    register(api_client)
    api_client.cookies.clear()
    bad = {"email": "founder@example.com", "password": "wrong-password"}
    statuses = [api_client.post(LOGIN, json=bad).status_code for _ in range(6)]
    assert statuses[:5] == [401] * 5
    assert statuses[5] == 429

    # Even the right password is refused until the window passes.
    good = api_client.post(LOGIN, json={"email": "founder@example.com", "password": PASSWORD})
    assert good.status_code == 429


def test_cross_site_post_is_blocked(api_client):
    evil = api_client.post(LOGIN, json={"email": "a@b.com", "password": "x"}, headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert evil.json()["error"]["code"] == "FORBIDDEN_ORIGIN"

    own = api_client.post(
        LOGIN, json={"email": "a@b.com", "password": "x"}, headers={"Origin": "http://localhost:3000"}
    )
    assert own.status_code == 401
