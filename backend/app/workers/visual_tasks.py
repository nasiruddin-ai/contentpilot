import uuid

from app.services import visual_service
from app.workers.celery_app import celery


@celery.task(name="visuals.generate", soft_time_limit=300, time_limit=360)
def generate_visual(visual_id: str) -> dict:
    return visual_service.execute_visual(uuid.UUID(visual_id))
