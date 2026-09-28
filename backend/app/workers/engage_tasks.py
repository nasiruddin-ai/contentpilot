import uuid

from app.services import engage_service
from app.workers.celery_app import celery


# One poll fetches comments/messages and drafts several AI replies.
@celery.task(name="engage.poll_brand", soft_time_limit=540, time_limit=600)
def poll_brand(brand_id: str) -> dict:
    return engage_service.poll_brand(uuid.UUID(brand_id))


@celery.task(name="engage.tick")
def tick() -> dict:
    return engage_service.tick()
