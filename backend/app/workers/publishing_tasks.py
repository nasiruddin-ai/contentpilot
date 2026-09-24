import uuid

from app.services import publishing_service
from app.workers.celery_app import celery


# acks_late is on globally, so a job is redelivered if a worker dies. A per-post lock and
# the external post ID check mean a post is never published twice.
@celery.task(name="publishing.publish_post", soft_time_limit=300, time_limit=360)
def publish_post(post_id: str) -> dict:
    return publishing_service.execute_publish(uuid.UUID(post_id))


@celery.task(name="publishing.queue_due_posts")
def queue_due_posts() -> dict:
    return publishing_service.queue_due_posts()
