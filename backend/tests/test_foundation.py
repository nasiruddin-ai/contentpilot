import json
import logging

from app.core.config import Settings
from app.core.logging import JsonFormatter, request_id_ctx


def test_database_url_gets_psycopg_driver():
    for raw in ("postgres://u:p@db:5432/x", "postgresql://u:p@db:5432/x"):
        assert Settings(database_url=raw).database_url == "postgresql+psycopg://u:p@db:5432/x"


def test_celery_urls_default_to_redis():
    settings = Settings(redis_url="redis://cache:6379/0", celery_broker_url="", celery_result_backend="")
    assert settings.celery_broker_url == "redis://cache:6379/0"
    assert settings.celery_result_backend == "redis://cache:6379/0"


def test_cors_origins_fall_back_to_app_url():
    assert Settings(cors_origins="", app_url="https://app.example.com").allowed_origins == [
        "https://app.example.com"
    ]
    assert Settings(cors_origins="https://a.com, https://b.com").allowed_origins == [
        "https://a.com",
        "https://b.com",
    ]


def test_secrets_are_masked():
    settings = Settings(openai_api_key="sk-live-123")
    assert "sk-live-123" not in repr(settings)


def test_json_log_includes_request_id_and_extras():
    token = request_id_ctx.set("req-1")
    try:
        record = logging.makeLogRecord({"msg": "hello", "levelname": "INFO", "user_id": "u1"})
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_ctx.reset(token)
    assert line["message"] == "hello"
    assert line["request_id"] == "req-1"
    assert line["user_id"] == "u1"


def test_celery_ping_task_runs_locally():
    from app.workers.system_tasks import ping

    assert ping.apply().get() == {"status": "ok"}
