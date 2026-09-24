import pytest
from fastapi import FastAPI

from app.core.errors import AppError


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    @app.get("/_test/crash")
    async def crash():
        raise RuntimeError("secret internal detail")

    @app.get("/_test/app-error")
    async def app_error():
        raise AppError("CONTENT_GENERATION_FAILED", "Unable to generate content.", 502)

    @app.get("/_test/typed")
    async def typed(limit: int):
        return {"limit": limit}

    return app


def test_generates_request_id(client):
    response = client.get("/health")
    assert len(response.headers["x-request-id"]) == 32


def test_echoes_safe_request_id(client):
    response = client.get("/health", headers={"X-Request-ID": "abc123-client-id"})
    assert response.headers["x-request-id"] == "abc123-client-id"


def test_replaces_unsafe_request_id(client):
    response = client.get("/health", headers={"X-Request-ID": "bad id\\nINJECTED"})
    assert response.headers["x-request-id"] != "bad id\\nINJECTED"


def test_not_found_uses_envelope(client):
    response = client.get("/does-not-exist")
    body = response.json()
    assert response.status_code == 404
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
    assert body["error"]["request_id"] == response.headers["x-request-id"]


def test_unhandled_error_hides_details(client):
    response = client.get("/_test/crash")
    body = response.json()
    assert response.status_code == 500
    assert body["error"]["code"] == "INTERNAL_ERROR"
    assert "secret" not in response.text
    assert body["error"]["request_id"] == response.headers["x-request-id"]


def test_app_error_maps_to_envelope(client):
    response = client.get("/_test/app-error")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "CONTENT_GENERATION_FAILED"
    assert response.json()["error"]["message"] == "Unable to generate content."


def test_validation_error_lists_fields(client):
    response = client.get("/_test/typed", params={"limit": "many"})
    body = response.json()
    assert response.status_code == 422
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["error"]["details"][0]["field"] == "query.limit"
