"""
Pytest fixtures: in-memory SQLite DB + FastAPI TestClient.
"""
import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from httpx import ASGITransport, AsyncClient
from fastapi.testclient import TestClient

# Use SQLite for tests so no MySQL required
os.environ.setdefault("TESTING", "1")

from app.database import Base, get_db
from app.main import app
from app.migrate import run_migrations


TEST_DB_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def test_engine():
    # StaticPool keeps one connection so :memory: is shared across all sessions
    engine = create_engine(
        TEST_DB_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    run_migrations(engine)
    return engine


@pytest.fixture(scope="session")
def SessionTest(test_engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db(SessionTest):
    session = SessionTest()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client(db):
    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as tc:
            yield tc
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
async def async_client(db):
    """AsyncClient for async endpoints (e.g. /stream)."""
    def override_get_db():
        try:
            yield db
        finally:
            pass
    app.dependency_overrides[get_db] = override_get_db
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac
    finally:
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def token(client, db):
    """Register a user and return (user_id, token)."""
    from app.models import User
    import secrets
    name = "test_user_" + secrets.token_hex(4)
    token_val = secrets.token_hex(16)
    u = User(name=name, token=token_val, status="open", last_seen_at=None)
    db.add(u)
    db.commit()
    db.refresh(u)
    return u.id, token_val


@pytest.fixture
def two_users(client, db):
    """Two users with tokens: (id_a, token_a, id_b, token_b)."""
    from app.models import User
    import secrets
    def add(name):
        u = User(name=name, token=secrets.token_hex(16), status="open", last_seen_at=None)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, u.token
    id_a, token_a = add("user_a_" + secrets.token_hex(4))
    id_b, token_b = add("user_b_" + secrets.token_hex(4))
    return id_a, token_a, id_b, token_b
