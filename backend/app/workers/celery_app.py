"""Celery foundation. Run with:

    celery -A app.workers.celery_app worker --loglevel=info
    celery -A app.workers.celery_app beat --loglevel=info
"""

import logging
import time

from celery import Celery
from celery.schedules import crontab
from celery.signals import setup_logging, task_failure, task_postrun, task_prerun, task_retry

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
logger = logging.getLogger("app.worker")

celery = Celery(
    "contentpilot",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.system_tasks", "app.workers.auth_tasks", "app.workers.research_tasks"],
)

celery.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Re-deliver a task if the worker dies mid-run, and hand out one task at a time.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_soft_time_limit=300,
    task_time_limit=360,
    result_expires=60 * 60 * 24,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "refresh-due-sources": {
            "task": "research.refresh_due_sources",
            "schedule": crontab(minute="*/5"),
        },
        "purge-stale-refresh-tokens": {
            "task": "auth.purge_stale_refresh_tokens",
            "schedule": crontab(hour=3, minute=0),
        },
    },
)


@setup_logging.connect
def _configure_worker_logging(**_: object) -> None:
    configure_logging(settings.log_level)


# Every task gets start/finish/failure log lines (spec section 73: observable jobs).
_started_at: dict[str, float] = {}


@task_prerun.connect
def _on_task_start(task_id: str, task, **_: object) -> None:
    _started_at[task_id] = time.perf_counter()
    logger.info("task_started", extra={"job_id": task_id, "task_name": task.name})


@task_postrun.connect
def _on_task_end(task_id: str, task, state: str | None = None, **_: object) -> None:
    started = _started_at.pop(task_id, None)
    duration_ms = round((time.perf_counter() - started) * 1000, 1) if started else None
    logger.info(
        "task_finished",
        extra={
            "job_id": task_id,
            "task_name": task.name,
            "result": state,
            "retry_count": task.request.retries,
            "duration_ms": duration_ms,
        },
    )


@task_retry.connect
def _on_task_retry(request, reason, **_: object) -> None:
    logger.warning(
        "task_retry",
        extra={"job_id": request.id, "task_name": request.task, "retry_count": request.retries, "error": str(reason)},
    )


@task_failure.connect
def _on_task_failure(task_id: str, exception: BaseException, sender, **_: object) -> None:
    logger.error(
        "task_failed",
        exc_info=exception,
        extra={"job_id": task_id, "task_name": sender.name},
    )
