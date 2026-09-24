"""
tests/test_db_migrations.py — Tests for database schema creation.

Verifies the SQLAlchemy models produce the correct table structure
and constraints using an in-memory SQLite engine (no PostgreSQL needed).
"""

import pytest
from sqlalchemy.exc import IntegrityError

from db.models import User, Product, StockAuditLog, ChatHistory


def test_schema_creates_all_tables(db_engine):
    """All expected tables must be created by Base.metadata.create_all."""
    from sqlalchemy import inspect
    inspector = inspect(db_engine)
    tables = set(inspector.get_table_names())
    assert "users" in tables
    assert "products" in tables
    assert "stock_audit_log" in tables
    assert "chat_history" in tables


def test_products_requires_user_id(db_session):
    """Inserting a product without user_id must fail (FK or NOT NULL)."""
    product = Product(
        name="Apple",
        category="Fruit",
        stock=10,
        price=1.5,
        supplier="FarmCo",
        # user_id intentionally omitted
    )
    db_session.add(product)
    with pytest.raises(Exception):  # IntegrityError on commit
        db_session.commit()
    db_session.rollback()


def test_user_product_relationship(db_session):
    """A product created with a valid user_id should be retrievable."""
    user = User(
        full_name="Test User",
        email="test@example.com",
        hashed_password="hashed",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    product = Product(
        user_id=user.id,
        name="Widget",
        category="Tools",
        stock=5,
        price=9.99,
        supplier="SupplierA",
    )
    db_session.add(product)
    db_session.commit()

    fetched = db_session.query(Product).filter(
        Product.user_id == user.id,
        Product.name == "Widget",
    ).first()
    assert fetched is not None
    assert fetched.stock == 5


def test_user_id_tenant_isolation(db_session):
    """Products from user A must not be visible when filtering for user B."""
    user_a = User(full_name="Alice", email="alice@test.com", hashed_password="h", is_active=True)
    user_b = User(full_name="Bob", email="bob@test.com", hashed_password="h", is_active=True)
    db_session.add_all([user_a, user_b])
    db_session.commit()

    db_session.add(Product(user_id=user_a.id, name="Alice Product", category="X", stock=1, price=1.0, supplier="S"))
    db_session.commit()

    # user_b should see 0 products
    count = db_session.query(Product).filter(Product.user_id == user_b.id).count()
    assert count == 0

    # user_a should see 1
    count = db_session.query(Product).filter(Product.user_id == user_a.id).count()
    assert count == 1


def test_stock_audit_log_created(db_session):
    """StockAuditLog should record stock changes correctly."""
    user = User(full_name="Auditor", email="audit@test.com", hashed_password="h", is_active=True)
    db_session.add(user)
    db_session.commit()

    product = Product(user_id=user.id, name="Tracked Item", category="Y", stock=100, price=5.0, supplier="S")
    db_session.add(product)
    db_session.commit()

    log = StockAuditLog(
        user_id=user.id,
        product_id=product.id,
        old_stock=100,
        new_stock=75,
        action="ai_update",
    )
    db_session.add(log)
    db_session.commit()

    fetched = db_session.query(StockAuditLog).filter(StockAuditLog.product_id == product.id).first()
    assert fetched.old_stock == 100
    assert fetched.new_stock == 75
    assert fetched.action == "ai_update"
