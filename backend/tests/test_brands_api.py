import pytest

BRANDS = "/api/v1/brands"

SQUAREKO_KIT = {
    "name": "Squareko",
    "website": "https://squareko.com",
    "industry": "Squarespace Web Design",
    "audience": "Small business owners",
    "goals": ["Lead generation"],
    "tone": ["professional", "conversational", "clear"],
    "visual_style": ["minimal", "editorial"],
    "logo_url": "https://cdn.example.com/logo.png",
    "primary_color": "#07142f",
    "secondary_color": "#6E5BFF",
    "accent_color": "#4BA8FF",
    "heading_font": "Inter",
    "body_font": "Inter",
    "preferred_words": ["clarity", "conversion"],
    "banned_words": ["synergy"],
}


def sign_up(client, email="owner@example.com"):
    client.cookies.clear()
    response = client.post(
        "/api/v1/auth/register", json={"name": "Owner", "email": email, "password": "a-strong-password"}
    )
    assert response.status_code == 201


@pytest.fixture
def owner(api_client):
    sign_up(api_client)
    return api_client


def create(client, **overrides):
    return client.post(BRANDS, json={**SQUAREKO_KIT, **overrides})


def test_brands_require_login(api_client):
    assert api_client.get(BRANDS).status_code == 401
    assert api_client.post(BRANDS, json={"name": "X"}).status_code == 401


def test_create_full_brand_kit(owner):
    response = create(owner)
    assert response.status_code == 201
    brand = response.json()

    assert brand["name"] == "Squareko"
    assert brand["primary_color"] == "#07142F"  # normalized to uppercase
    assert brand["tone"] == ["professional", "conversational", "clear"]
    assert brand["banned_words"] == ["synergy"]
    assert brand["created_at"] and brand["updated_at"]


def test_new_brand_gets_default_pillar_mix(owner):
    pillars = create(owner).json()["content_pillars"]
    assert pillars == [
        {"pillar": "educational", "weight": 40},
        {"pillar": "opinion", "weight": 20},
        {"pillar": "case_study", "weight": 15},
        {"pillar": "story", "weight": 15},
        {"pillar": "promotion", "weight": 10},
    ]


def test_minimal_brand_needs_only_a_name(owner):
    response = owner.post(BRANDS, json={"name": "  Side Project  ", "content_pillars": []})
    assert response.status_code == 201
    assert response.json()["name"] == "Side Project"
    assert response.json()["content_pillars"] == []
    assert response.json()["tone"] == []


def test_lists_are_cleaned(owner):
    brand = create(owner, tone=["Clear", " clear ", "", "bold   voice"]).json()
    assert brand["tone"] == ["Clear", "bold voice"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"primary_color": "blue"},
        {"accent_color": "#FFF"},
        {"website": "not a url"},
        {"logo_url": "javascript:alert(1)"},
        {"tone": ["x" * 61]},
        {"tone": [f"t{i}" for i in range(21)]},
        {"content_pillars": [{"pillar": "educational", "weight": 50}]},
        {"content_pillars": [{"pillar": "educational", "weight": 50}, {"pillar": "educational", "weight": 50}]},
        {"content_pillars": [{"pillar": "memes", "weight": 100}]},
        {"content_pillars": [{"pillar": "educational", "weight": 101}]},
    ],
)
def test_invalid_brand_input_rejected(owner, overrides):
    response = create(owner, **overrides)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_word_cannot_be_preferred_and_banned(owner):
    response = create(owner, preferred_words=["Synergy"], banned_words=["synergy"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORD_LIST_CONFLICT"


def test_list_and_get_own_brands(owner):
    first = create(owner, name="First").json()
    create(owner, name="Second")

    names = [b["name"] for b in owner.get(BRANDS).json()]
    assert names == ["First", "Second"]
    assert owner.get(f"{BRANDS}/{first['id']}").json()["name"] == "First"


def test_other_users_brands_are_invisible(api_client):
    sign_up(api_client, "alice@example.com")
    alices = create(api_client, name="Alice Co").json()

    sign_up(api_client, "bob@example.com")
    assert api_client.get(BRANDS).json() == []

    url = f"{BRANDS}/{alices['id']}"
    for response in (
        api_client.get(url),
        api_client.patch(url, json={"name": "Hijacked"}),
        api_client.delete(url),
    ):
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "BRAND_NOT_FOUND"

    # Untouched from Alice's side.
    api_client.cookies.clear()
    api_client.post("/api/v1/auth/login", json={"email": "alice@example.com", "password": "a-strong-password"})
    assert api_client.get(url).json()["name"] == "Alice Co"


def test_patch_changes_only_sent_fields(owner):
    brand = create(owner).json()
    url = f"{BRANDS}/{brand['id']}"

    updated = owner.patch(url, json={"tone": ["bold"], "description": "We build sites."}).json()
    assert updated["tone"] == ["bold"]
    assert updated["description"] == "We build sites."
    assert updated["name"] == "Squareko"
    assert updated["visual_style"] == ["minimal", "editorial"]

    cleared = owner.patch(url, json={"description": None, "logo_url": None}).json()
    assert cleared["description"] is None and cleared["logo_url"] is None


def test_patch_rejects_null_for_required_fields(owner):
    brand = create(owner).json()
    response = owner.patch(f"{BRANDS}/{brand['id']}", json={"name": None})
    assert response.status_code == 422


def test_patch_word_conflict_checks_existing_values(owner):
    brand = create(owner).json()  # preferred: clarity, conversion
    response = owner.patch(f"{BRANDS}/{brand['id']}", json={"banned_words": ["Clarity"]})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "WORD_LIST_CONFLICT"
    assert owner.get(f"{BRANDS}/{brand['id']}").json()["banned_words"] == ["synergy"]


def test_patch_replaces_pillar_mix(owner):
    brand = create(owner).json()
    url = f"{BRANDS}/{brand['id']}"

    # Keeps some existing pillars (educational), drops others, adds new ones.
    mix = [
        {"pillar": "educational", "weight": 50},
        {"pillar": "how_to", "weight": 30},
        {"pillar": "community", "weight": 20},
    ]
    response = owner.patch(url, json={"content_pillars": mix})
    assert response.status_code == 200
    assert response.json()["content_pillars"] == mix

    # Leaving content_pillars out keeps the mix.
    assert owner.patch(url, json={"name": "Renamed"}).json()["content_pillars"] == mix


def test_delete_brand(owner):
    brand = create(owner).json()
    url = f"{BRANDS}/{brand['id']}"

    assert owner.delete(url).status_code == 204
    assert owner.get(url).status_code == 404
    assert owner.get(BRANDS).json() == []


def test_malformed_brand_id_is_422(owner):
    assert owner.get(f"{BRANDS}/not-a-uuid").status_code == 422
