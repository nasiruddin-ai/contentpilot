from app.workers.celery_app import celery


@celery.task(name="system.ping")
def ping() -> dict[str, str]:
    """Smoke test that a worker is consuming from the broker."""
    return {"status": "ok"}
