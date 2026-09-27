"""
db/migrations.py — Schema bootstrap and custom column migrations using SQLAlchemy.
"""

import logging
from sqlalchemy import Engine, inspect, text
from db.models import Base, normalize_category

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
                
                # 3. Preserve a legacy username, then drop the obsolete column.
                if "username" in columns:
                    conn.execute(text(
                        "UPDATE users SET full_name = COALESCE(NULLIF(full_name, ''), username)"
                    ))
                    log.info("[DB] Migrating: Dropping obsolete 'username' column from 'users' table")
                    conn.execute(text("ALTER TABLE users DROP COLUMN username"))

    except Exception as e:
        log.error(f"[DB] Migration helper failed: {e}", exc_info=True)
        raise

    _normalize_existing_categories(engine)

    log.info("[DB] Schema migrations complete.")


def _normalize_existing_categories(engine: Engine) -> None:
    """
    Bring pre-existing rows onto the normalized category identity.

    Rows are rewritten to trim + lowercase. Products are independent rows, so
    merging equivalent categories never loses or duplicates a product; the
    display label is derived at read time from the normalized value.
    """
    try:
        inspector = inspect(engine)
        if "products" not in inspector.get_table_names():
            return
        with engine.begin() as conn:
            rows = conn.execute(text("SELECT id, category FROM products")).fetchall()
            changed = 0
            for product_id, raw_category in rows:
                normalized = normalize_category(raw_category)
                if str(raw_category or "") != normalized:
                    conn.execute(
                        text("UPDATE products SET category = :c WHERE id = :i"),
                        {"c": normalized, "i": product_id},
                    )
                    changed += 1
            if changed:
                log.info(
                    f"[DB] Normalized category casing on {changed} existing product row(s)"
                )
    except Exception as exc:
        # Never block startup on a data cleanup; writes are normalized going forward.
        log.error(f"[DB] Category normalization backfill failed: {exc}", exc_info=True)
