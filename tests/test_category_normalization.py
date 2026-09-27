"""
tests/test_category_normalization.py — category identity is case-insensitive.

Proves the rule for ARBITRARY categories, not just "Grains":
"Grains" == "grains" == "GRAINS" == " Grains ", and the same holds for
"Electronics", "Dairy", "home appliances", etc.
"""

import sys
from pathlib import Path

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

import server as mcp_server
from db.models import (
    Product,
    User,
    display_category,
    normalize_category,
)

# Two logically distinct categories, each written several ways.
CASINGS = ["Grains", "grains", "GRAINS", " Grains ", "\tGrains\n"]
ELECTRONICS_CASINGS = ["Electronics", "ELECTRONICS", "electronics", " electronics "]
DAIRY_CASINGS = ["Dairy", "dairy", "DAIRY", " Dairy "]


@pytest.fixture
def tenant(db_engine, monkeypatch):
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "_get_db", sessions)
    db = sessions()
    user = User(full_name="Cat User", email="cat@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return sessions, user_id


def _rows(user_id, sessions):
    db = sessions()
    rows = db.query(Product).filter(Product.user_id == user_id).all()
    data = [{"name": p.name, "category": p.category} for p in rows]
    db.close()
    return data


# ── The normalization primitive ───────────────────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Grains", "grains"),
        ("grains", "grains"),
        ("GRAINS", "grains"),
        (" Grains ", "grains"),
        ("\tGRAINS \n", "grains"),
        ("Electronics", "electronics"),
        ("  DAIRY  ", "dairy"),
        ("home appliances", "home appliances"),
        ("Home Appliances", "home appliances"),
        ("", "general"),
        (None, "general"),
        ("   ", "general"),
    ],
)
def test_normalize_category_is_trim_and_lowercase(raw, expected):
    assert normalize_category(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("grains", "Grains"),
        ("electronics", "Electronics"),
        ("home appliances", "Home Appliances"),
        ("general", "General"),
        ("", "General"),
    ],
)
def test_display_category_derives_a_sensible_label(raw, expected):
    assert display_category(raw) == expected


def test_all_casings_normalize_to_one_identity():
    normalized = {normalize_category(c) for c in CASINGS}
    assert normalized == {"grains"}


# ── Storage: writes are normalized ───────────────────────────────────────────

@pytest.mark.parametrize("variant", CASINGS)
def test_create_product_stores_the_normalized_value(tenant, variant):
    sessions, user_id = tenant
    result = mcp_server.create_product(
        user_id=user_id, name=f"Rice {variant!r}", category=variant,
        stock=1, price=10.0, supplier="S",
    )
    assert result["success"] is True
    stored = _rows(user_id, sessions)
    assert [r["category"] for r in stored] == ["grains"]
    # The API still shows a sensible display label.
    assert result["category"] == "Grains"


@pytest.mark.parametrize("variant", ELECTRONICS_CASINGS)
def test_create_normalizes_an_arbitrary_second_category(tenant, variant):
    sessions, user_id = tenant
    mcp_server.create_product(
        user_id=user_id, name="Hub", category=variant, stock=1, price=1.0, supplier="S")
    assert [r["category"] for r in _rows(user_id, sessions)] == ["electronics"]


def test_update_product_normalizes_the_new_category(tenant):
    sessions, user_id = tenant
    mcp_server.create_product(
        user_id=user_id, name="Hub", category="Electronics", stock=1,
        price=1.0, supplier="S")
    result = mcp_server.update_product(
        user_id=user_id, product_name="Hub", new_category="  ELECTRONICS  ")
    assert result["success"] is True
    # Same identity, so this is a no-op rather than a second category.
    assert result.get("updated_fields", []) == []
    assert [r["category"] for r in _rows(user_id, sessions)] == ["electronics"]


def test_update_product_can_move_to_a_genuinely_different_category(tenant):
    sessions, user_id = tenant
    mcp_server.create_product(
        user_id=user_id, name="Hub", category="Electronics", stock=1,
        price=1.0, supplier="S")
    mcp_server.update_product(
        user_id=user_id, product_name="Hub", new_category="GADGETS")
    assert [r["category"] for r in _rows(user_id, sessions)] == ["gadgets"]


# ── Listing / dedup: one entry per identity ───────────────────────────────────

def test_get_all_categories_deduplicates_every_casing(tenant):
    sessions, user_id = tenant
    for i, variant in enumerate(CASINGS):
        mcp_server.create_product(
            user_id=user_id, name=f"Rice {i}", category=variant,
            stock=1, price=1.0, supplier="S")
    for i, variant in enumerate(DAIRY_CASINGS):
        mcp_server.create_product(
            user_id=user_id, name=f"Milk {i}", category=variant,
            stock=1, price=1.0, supplier="S")
    assert mcp_server.get_all_categories(user_id=user_id) == ["Dairy", "Grains"]


def test_legacy_mixed_case_rows_are_collapsed_on_read(tenant):
    """Rows written before normalization must still appear as one category."""
    sessions, user_id = tenant
    db = sessions()
    for i, variant in enumerate(["Grains", "GRAINS", "dairy"]):
        db.add(Product(user_id=user_id, name=f"Legacy {i}",
                       category=variant, stock=1, price=1.0, supplier="S"))
    db.commit()
    db.close()
    assert mcp_server.get_all_categories(user_id=user_id) == ["Dairy", "Grains"]


# ── Filtering is case-insensitive for every variant ──────────────────────────

@pytest.mark.parametrize("query", CASINGS + ["gRaInS", " GrAiNs "])
def test_get_products_by_category_matches_any_casing(tenant, query):
    sessions, user_id = tenant
    for i, variant in enumerate(CASINGS):
        mcp_server.create_product(
            user_id=user_id, name=f"Rice {i}", category=variant,
            stock=1, price=1.0, supplier="S")
    result = mcp_server.get_products_by_category(user_id=user_id, category=query)
    assert len(result) == len(CASINGS)
    assert {r["category"] for r in result} == {"Grains"}


def test_search_inventory_category_filter_is_case_insensitive(tenant):
    sessions, user_id = tenant
    for i, variant in enumerate(ELECTRONICS_CASINGS):
        mcp_server.create_product(
            user_id=user_id, name=f"Hub {i}", category=variant,
            stock=1, price=1.0, supplier="S")
    for query in ELECTRONICS_CASINGS:
        assert len(mcp_server.search_inventory(user_id=user_id, category=query)) == 4
    # A different category must not match.
    assert mcp_server.search_inventory(user_id=user_id, category="grains") == []


# ── Analytics group by identity ──────────────────────────────────────────────

def test_category_analytics_groups_every_casing_into_one_row(tenant):
    sessions, user_id = tenant
    for i, variant in enumerate(CASINGS):
        mcp_server.create_product(
            user_id=user_id, name=f"Rice {i}", category=variant,
            stock=10, price=100.0, supplier="S")
    mcp_server.create_product(
        user_id=user_id, name="Milk", category="DaIrY", stock=4, price=50.0, supplier="S")

    rows = mcp_server.get_category_analytics(user_id=user_id)
    by_label = {r["category"]: r for r in rows}
    assert set(by_label) == {"Grains", "Dairy"}
    assert by_label["Grains"]["product_count"] == len(CASINGS)
    assert by_label["Grains"]["total_stock"] == 10 * len(CASINGS)
    assert by_label["Dairy"]["product_count"] == 1


def test_category_analytics_aggregates_across_casings(tenant):
    sessions, user_id = tenant
    db = sessions()
    for i, variant in enumerate(["Grains", "grains", "GRAINS"]):
        db.add(Product(user_id=user_id, name=f"R {i}", category=variant,
                       stock=10, price=10.0, supplier="S"))
    db.commit()
    db.close()
    rows = mcp_server.get_category_analytics(user_id=user_id)
    assert len(rows) == 1
    assert rows[0]["product_count"] == 3
    assert rows[0]["total_stock"] == 30


# ── Tenant isolation is unaffected ────────────────────────────────────────────

def test_category_normalization_does_not_breach_tenants(tenant, db_engine, monkeypatch):
    sessions, user_a = tenant
    db = sessions()
    other = User(full_name="Other", email="cat-other@example.com", hashed_password="x")
    db.add(other)
    db.commit()
    user_b = other.id
    db.close()

    mcp_server.create_product(
        user_id=user_a, name="Mine", category="Grains", stock=1, price=1.0, supplier="S")
    mcp_server.create_product(
        user_id=user_b, name="Theirs", category="grains", stock=1, price=1.0, supplier="S")

    # Same normalized identity, but each tenant still sees only its own row.
    assert [r["name"] for r in mcp_server.get_products_by_category(
        user_id=user_a, category="grains")] == ["Mine"]
    assert [r["name"] for r in mcp_server.get_products_by_category(
        user_id=user_b, category="Grains")] == ["Theirs"]
    assert mcp_server.get_all_categories(user_id=user_a) == ["Grains"]


# ── AI intent: the model's casing is not trusted ─────────────────────────────

def test_llm_extracted_category_is_normalized_not_trusted():
    from ai.intent_schema import build_intent, validate_intent, IntentPlan

    for raw_category in ("Electronics", "ELECTRONICS", " electronics ", "eLeCtRoNiCs"):
        outcome = validate_intent(
            build_intent({
                "intent": "create_product", "product_queries": ["Hub"],
                "category": raw_category,
                "changes": {"stock": 1, "price": 2.0}, "supplier": "S",
            }, "add hub"),
            "add hub",
        )
        assert isinstance(outcome, IntentPlan), raw_category
        assert outcome.arguments["category"] == "electronics", raw_category


def test_llm_category_filter_is_normalized_before_querying():
    from ai.intent_schema import build_intent, validate_intent, IntentPlan

    outcome = validate_intent(
        build_intent({"intent": "get_products_by_category", "category": "  GRAINS "},
                     "show grains"),
        "show grains",
    )
    assert isinstance(outcome, IntentPlan)
    assert outcome.arguments["category"] == "grains"


def test_category_change_is_normalized():
    from ai.intent_schema import build_intent, validate_intent, IntentPlan

    outcome = validate_intent(
        build_intent({"intent": "update_product", "product_queries": ["Hub"],
                      "changes": {"category": " GADGETS "}}, "move hub"),
        "move hub",
    )
    assert isinstance(outcome, IntentPlan)
    assert outcome.arguments["new_category"] == "gadgets"
