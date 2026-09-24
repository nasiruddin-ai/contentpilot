import uuid

from app.services import content_service, editor_service
from app.workers.celery_app import celery


# Four AI calls; the quality model may fall back or wait out free-tier limits.
@celery.task(name="content.generate", soft_time_limit=600, time_limit=660)
def generate_content(generation_id: str) -> dict:
    return content_service.execute_generation(uuid.UUID(generation_id))


@celery.task(name="content.revise", soft_time_limit=300, time_limit=360)
def revise_post(revision_id: str) -> dict:
    return editor_service.execute_revision(uuid.UUID(revision_id))
