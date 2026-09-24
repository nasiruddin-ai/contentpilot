import uuid

from app.services import opportunity_service
from app.workers.celery_app import celery


# Analysis batches plus generation can take several minutes on the free tier.
@celery.task(name="opportunities.generate", soft_time_limit=900, time_limit=960)
def generate_opportunities(run_id: str) -> dict:
    return opportunity_service.execute_run(uuid.UUID(run_id))
