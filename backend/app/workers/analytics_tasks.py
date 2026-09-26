import uuid

from app.services import analytics_service
from app.workers.celery_app import celery


@celery.task(name="analytics.sync_brand", soft_time_limit=600, time_limit=660)
def sync_brand(sync_id: str) -> dict:
    return analytics_service.execute_sync(uuid.UUID(sync_id))


@celery.task(name="analytics.sync_all")
def sync_all() -> dict:
    return analytics_service.queue_all_brands()
