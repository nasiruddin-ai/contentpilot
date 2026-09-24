from app.services import health_service


def test_liveness(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_ok(client, monkeypatch):
    async def all_ok():
        return {"database": "ok", "redis": "ok"}

    monkeypatch.setattr(health_service, "readiness", all_ok)
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readiness_degraded_returns_503(client, monkeypatch):
    async def db_down():
        return {"database": "error", "redis": "ok"}

    monkeypatch.setattr(health_service, "readiness", db_down)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "checks": {"database": "error", "redis": "ok"}}


async def test_failed_check_reports_false_without_raising():
    async def boom():
        raise ConnectionError("refused")

    assert await health_service._run_check("database", boom) is False
