"""
db/connection.py — Centralized SQLAlchemy connection factory.

Single source of truth for DB connection settings.
Supports both PostgreSQL (production) and SQLite in-memory (tests).
"""

import os
from typing import Generator
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://stockquery:stockpassword@localhost:5432/stockquery_db"
)

_is_sqlite = DATABASE_URL.startswith("sqlite")

if _is_sqlite:
    # SQLite (used in tests) — StaticPool, no pool_size/max_overflow
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    # PostgreSQL (production)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=20,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields an ORM Session and closes it after the request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
