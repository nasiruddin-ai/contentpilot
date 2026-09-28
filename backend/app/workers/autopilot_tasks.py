import uuid

from app.services import autopilot_service
from app.workers.celery_app import celery


# A cycle chains research, idea generation and several post generations.
@celery.task(name="autopilot.run", soft_time_limit=1500, time_limit=1560)
def run_autopilot(run_id: str) -> dict:
    return autopilot_service.execute_cycle(uuid.UUID(run_id))


@celery.task(name="autopilot.tick")
def tick() -> dict:
    return autopilot_service.tick()
