"""
tests/conftest.py — Shared pytest fixtures for all test modules.

Uses SQLite in-memory via SQLAlchemy for fast, isolated unit tests.
No real PostgreSQL connection required to run the test suite.
"""

import os
import sys
import pytest
from pathlib import Path

# Ensure backend/ is on the path before importing app modules
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

# Override DATABASE_URL with SQLite in-memory BEFORE any app import
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-32chars!")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from db.models import Base


@pytest.fixture(scope="function")
def db_engine():
    """SQLite in-memory engine with SQLAlchemy — fresh per test function."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Enable FK enforcement for SQLite
    @event.listens_for(engine, "connect")
    def _set_fk_pragma(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)


@pytest.fixture(scope="function")
def db_session(db_engine):
    """Return a SQLAlchemy Session bound to the in-memory engine."""
    Session = sessionmaker(bind=db_engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def registered_user_credentials():
    return {"full_name": "Test User", "email": "test@example.com", "password": "Password123"}
