import asyncio
import os
import sys

os.environ["APP_ENV"] = "test"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-signing"
# Redis database 15 is reserved for tests and flushed freely.
os.environ["REDIS_URL"] = os.environ.get("TEST_REDIS_URL", "redis://localhost:6380/15")

# psycopg's async mode can't run on Windows' default Proactor event loop.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import pytest
import redis as sync_redis
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import BACKEND_DIR, get_settings
from app.main import create_app

# A separate database so tests never touch development data.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+psycopg://contentpilot:contentpilot@localhost:5433/contentpilot_test",
)


@pytest.fixture
def app():
    get_settings.cache_clear()
    return create_app()


@pytest.fixture
def client(app):
    # Let unhandled exceptions become 500 responses instead of re-raising in the test.
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def alembic_config(connection: Connection) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def db_engine():
    """Fresh test database, built by running the real migrations."""
    url = make_url(TEST_DATABASE_URL)
    admin = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3}
    )
    try:
        with admin.connect() as conn:
            exists = conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": url.database})
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    except OperationalError:
        pytest.skip("Postgres not reachable. Start it with: docker compose up -d postgres")
    finally:
        admin.dispose()

    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
        command.upgrade(alembic_config(conn), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    """Each test runs in a transaction that is rolled back afterwards."""
    with db_engine.connect() as conn:
        outer = conn.begin()
        session = Session(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            session.close()
            outer.rollback()


@pytest.fixture
def api_client(db_engine, monkeypatch):
    """App wired to the test database and test Redis; all data wiped afterwards."""
    try:
        sync_redis.Redis.from_url(os.environ["REDIS_URL"], socket_connect_timeout=3).flushdb()
    except sync_redis.RedisError:
        pytest.skip("Redis not reachable. Start it with: docker compose up -d redis")

    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    with TestClient(create_app(), raise_server_exceptions=False) as test_client:
        yield test_client
    get_settings.cache_clear()
    with db_engine.begin() as conn:
        conn.execute(text("TRUNCATE users CASCADE"))


# --- API helpers shared by feature tests ------------------------------------

PASSWORD = "a-strong-password"


def sign_up(client, email: str) -> None:
    client.cookies.clear()
    response = client.post("/api/v1/auth/register", json={"name": "Owner", "email": email, "password": PASSWORD})
    assert response.status_code == 201


def add_source(client, brand_id, url="https://example.com/feed", source_type="rss", **extra):
    return client.post(
        "/api/v1/sources",
        json={"brand_id": brand_id, "name": "Example", "url": url, "source_type": source_type, **extra},
    )


@pytest.fixture
def net(api_client, monkeypatch):
    """Routes all DNS and HTTP (API and worker code) through a fake internet, and
    records research jobs instead of sending them to Celery."""
    from app.api.v1.sources import get_fetcher
    from app.research.fetcher import clear_robots_cache
    from app.services import research_service, source_service
    from app.utils import urls
    from tests.fakes import FakeInternet

    fake = FakeInternet(dns={"intranet.example": ["10.0.0.7"]})

    async def check(raw):
        return await urls.check_public_url(raw, resolver=fake.resolve)

    monkeypatch.setattr(source_service, "check_public_url", check)
    monkeypatch.setattr(research_service, "fetcher_factory", lambda: fake.fetcher())
    fake.enqueued = []
    monkeypatch.setattr(research_service, "enqueue_run", fake.enqueued.append)
    api_client.app.dependency_overrides[get_fetcher] = lambda: fake.fetcher()
    clear_robots_cache()
    yield fake
    clear_robots_cache()


@pytest.fixture
def brand_id(api_client, net):
    sign_up(api_client, "owner@example.com")
    return api_client.post("/api/v1/brands", json={"name": "Squareko"}).json()["id"]
