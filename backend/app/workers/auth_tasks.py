from app.core.database import sync_session
from app.services import auth_service
from app.workers.celery_app import celery


@celery.task(name="auth.purge_stale_refresh_tokens")
def purge_stale_refresh_tokens() -> dict[str, int]:
    with sync_session() as db:
        deleted = auth_service.purge_stale_refresh_tokens(db)
    return {"deleted": deleted}
