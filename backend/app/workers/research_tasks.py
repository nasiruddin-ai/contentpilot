import uuid

from app.core.database import sync_session
from app.services import research_service
from app.workers.celery_app import celery


# Longer limits than the default: a feed run opens up to 20 articles.
@celery.task(name="research.fetch_source", soft_time_limit=600, time_limit=660)
def fetch_source(run_id: str) -> dict:
    return research_service.execute_run(uuid.UUID(run_id))


@celery.task(name="research.refresh_due_sources")
def refresh_due_sources() -> dict[str, int]:
    with sync_session() as db:
        run_ids = research_service.schedule_due_runs(db)
    # Enqueued after the commit above, so workers can see the runs.
    for run_id in run_ids:
        fetch_source.delay(str(run_id))
    return {"queued": len(run_ids)}
