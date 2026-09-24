"""
db/migrations.py — Schema bootstrap and custom column migrations using SQLAlchemy.
"""

import logging
from sqlalchemy import Engine, inspect, text
from db.models import Base

log = logging.getLogger(__name__)


def run_migrations(engine: Engine) -> None:
    """Apply all schema migrations via SQLAlchemy."""
    log.info("[DB] Creating database tables if they do not exist...")
    Base.metadata.create_all(bind=engine)
    
    # Run dynamic column updates for existing tables
    try:
        inspector = inspect(engine)
        if "users" in inspector.get_table_names():
            columns = [col["name"] for col in inspector.get_columns("users")]
            
            with engine.begin() as conn:
                # 1. Add full_name if missing
                if "full_name" not in columns:
                    log.info("[DB] Migrating: Adding 'full_name' column to 'users' table")
                    if engine.dialect.name == "sqlite":
                        conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(255) DEFAULT 'Demo User'"))
                    else:
                        conn.execute(text("ALTER TABLE users ADD COLUMN full_name VARCHAR(255) NOT NULL DEFAULT 'Demo User'"))
                
                # 2. Add business_name if missing
                if "business_name" not in columns:
                    log.info("[DB] Migrating: Adding 'business_name' column to 'users' table")
                    conn.execute(text("ALTER TABLE users ADD COLUMN business_name VARCHAR(255)"))
                
                # 3. Drop username if present
                if "username" in columns:
                    log.info("[DB] Migrating: Dropping obsolete 'username' column from 'users' table")
                    try:
                        conn.execute(text("ALTER TABLE users DROP COLUMN username"))
                    except Exception as drop_err:
                        log.warning(f"[DB] Drop username column failed (may not be supported on this SQLite version): {drop_err}")
                        
    except Exception as e:
        log.error(f"[DB] Migration helper failed: {e}", exc_info=True)

    log.info("[DB] Schema migrations complete.")
