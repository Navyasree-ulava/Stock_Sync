"""Focused MCP CRUD and deterministic-routing regression tests."""

import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

import server as mcp_server
from ai import agent
from db.models import Product, StockAuditLog, User

TOOL_NAMES = (
    "create_product",
    "get_products_by_category",
    "update_stock",
    "update_product",
    "delete_product",
    "search_inventory",
)


@pytest.fixture
def mcp_sessions(db_engine, monkeypatch):
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "_get_db", sessions)
    db = sessions()
    user = User(
        full_name="MCP Test User",
        email="mcp-crud@example.com",
        hashed_password="not-used",
    )
    db.add(user)
    db.commit()
    user_id = user.id
    db.close()
    return sessions, user_id


def test_exact_create_read_update_delete_and_numeric_preservation(mcp_sessions):
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(
        user_id=user_id,
        name="USB-C Hub 7-Port",
        category="Electronics",
        stock=10,
        price=1499.0,
        supplier="SRM",
    ))
    db.commit()
    db.close()

    created = mcp_server.create_product(
        user_id=user_id,
        name="Buttermilk",
        category="Dairy",
        stock=1,
        price=250.0,
        supplier="SRM",
    )
    assert created["success"] is True
    assert created["name"] == "Buttermilk"
    assert created["category"] == "Dairy"
    assert created["stock"] == 1
    assert type(created["stock"]) is int
    assert created["price"] == 250.0
    assert type(created["price"]) is float
    assert created["supplier"] == "SRM"

    dairy = mcp_server.get_products_by_category(user_id=user_id, category="Dairy")
    assert [product["name"] for product in dairy] == ["Buttermilk"]
    assert dairy[0]["price"] == 250.0

    updated = mcp_server.update_stock(
        user_id=user_id,
        product_name="USB-C Hub 7-Port",
        new_quantity=20,
    )
    assert updated["success"] is True
    assert updated["id"] is not None
    assert updated["stock"] == 20
    assert updated["price"] == 1499.0

    db = sessions()
    usb_rows = db.query(Product).filter(
        Product.user_id == user_id,
        Product.name == "USB-C Hub 7-Port",
    ).all()
    assert len(usb_rows) == 1
    assert usb_rows[0].stock == 20
    assert usb_rows[0].price == 1499.0
    audit = db.query(StockAuditLog).filter(
        StockAuditLog.user_id == user_id,
        StockAuditLog.product_id == usb_rows[0].id,
        StockAuditLog.action == "ai_update",
    ).all()
    assert len(audit) == 1
    assert audit[0].old_stock == 10
    assert audit[0].new_stock == 20
    db.close()

    electronics = mcp_server.get_products_by_category(
        user_id=user_id,
        category="Electronics",
    )
    assert [product["name"] for product in electronics] == ["USB-C Hub 7-Port"]
    assert electronics[0]["stock"] == 20
    assert electronics[0]["price"] == 1499.0

    deleted = mcp_server.delete_product(
        user_id=user_id,
        product_name="Buttermilk",
    )
    assert deleted["success"] is True
    assert deleted["id"] == created["id"]
    assert mcp_server.get_products_by_category(user_id=user_id, category="Dairy") == []

    second_delete = mcp_server.delete_product(
        user_id=user_id,
        product_name="Buttermilk",
    )
    assert "error" in second_delete


def test_update_product_combines_stock_price_and_fuzzy_name(mcp_sessions):
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(
        user_id=user_id,
        name="Basmati Rice 1kg",
        category="Grains",
        stock=10,
        price=120.0,
        supplier="AgroSupply",
    ))
    db.commit()
    db.close()

    result = mcp_server.update_product(
        user_id=user_id,
        product_name="basmati ricce 1kg",
        new_stock=60,
        new_price=130.0,
    )
    assert result["success"] is True
    assert result["id"]
    assert result["name"] == "Basmati Rice 1kg"
    assert result["stock"] == 60
    assert result["price"] == 130.0
    assert result["category"] == "Grains"
    assert set(result["updated_fields"]) == {"stock", "price"}

    db = sessions()
    rows = db.query(Product).filter(Product.user_id == user_id).all()
    assert len(rows) == 1
    assert rows[0].stock == 60
    assert rows[0].price == 130.0
    db.close()


def test_fuzzy_name_resolution_prefers_the_size_token(mcp_sessions):
    """'basmati ricce 1kg' must pick the 1kg row, not the near-identical 5kg row."""
    sessions, user_id = mcp_sessions
    db = sessions()
    for name, stock in (("Basmati Rice 1kg", 20), ("Basmati Rice 5kg", 5)):
        db.add(Product(
            user_id=user_id,
            name=name,
            category="Grains",
            stock=stock,
            price=120.0,
            supplier="AgroSupply",
        ))
    db.commit()
    db.close()

    result = mcp_server.update_stock(
        user_id=user_id,
        product_name="basmati ricce 1kg",
        new_quantity=60,
    )
    assert result["success"] is True
    assert result["name"] == "Basmati Rice 1kg"
    assert result["stock"] == 60

    db = sessions()
    rows = {p.name: p.stock for p in db.query(Product).filter(Product.user_id == user_id).all()}
    db.close()
    assert rows == {"Basmati Rice 1kg": 60, "Basmati Rice 5kg": 5}


def test_ambiguous_fuzzy_name_reports_candidates_instead_of_guessing(mcp_sessions):
    """A bare 'basmati rice' with no size must not silently pick a product."""
    sessions, user_id = mcp_sessions
    db = sessions()
    for name in ("Basmati Rice 1kg", "Basmati Rice 5kg"):
        db.add(Product(
            user_id=user_id,
            name=name,
            category="Grains",
            stock=10,
            price=120.0,
            supplier="AgroSupply",
        ))
    db.commit()
    db.close()

    result = mcp_server.update_stock(
        user_id=user_id,
        product_name="basmati rice",
        new_quantity=60,
    )
    assert "error" in result
    assert len(result["matches"]) == 2

    db = sessions()
    stocks = sorted(p.stock for p in db.query(Product).filter(Product.user_id == user_id).all())
    db.close()
    assert stocks == [10, 10]


# ── Production-readiness regressions ────────────────────────────────────────────


def test_update_rejects_two_products_in_one_request():
    """Regression: 'price of Milk and stock of Bread' silently wrote Bread's price."""
    route = agent.route_inventory_request("set the price of Milk and the stock of Bread to 10")
    assert route.validation_error
    assert "more than one product" in route.validation_error
    assert route.kind == "product_update"


def test_update_of_one_product_with_two_fields_is_still_allowed():
    """Two field references for the SAME product must not be treated as ambiguous."""
    route = agent.route_inventory_request("update the price of Rice and stock of Rice to 10")
    assert route.validation_error is None
    assert route.arguments["product_name"] == "Rice"


def test_trailing_field_noun_is_stripped_from_update_name():
    """Regression: 'update milk price to 50' resolved a product named 'milk price'."""
    route = agent.route_inventory_request("update milk price to 50")
    assert route.tool_name == "update_product"
    assert route.validation_error is None
    assert route.arguments["product_name"] == "milk"
    assert route.arguments["new_price"] == 50.0


def test_restock_phrasing_never_creates_a_product():
    """Regression: 'add 5 more Widgets ...' created a product named '5 more Widgets'."""
    for question in (
        "add 5 more Widgets in category Tools with stock 50 and price 99",
        "add 5 milk products",
    ):
        route = agent.route_inventory_request(question)
        assert route.tool_name == "update_stock", question
        assert route.validation_error, question
        assert route.arguments.get("name") is None, question


def test_mixed_mutation_intents_are_rejected():
    """Regression: 'update X and delete X' applied only the update and claimed success."""
    route = agent.route_inventory_request("update Wheat Bread stock to 5 and delete Wheat Bread")
    assert route.tool_name is None
    assert route.validation_error
    assert "mixes" in route.validation_error


@pytest.mark.parametrize(
    "question",
    [
        "delete all products",
        "delete every product",
        "remove the Dairy category",
        "delete all milk",
    ],
)
def test_bulk_and_category_deletion_are_rejected(question):
    """Bulk/category deletes must never be routed into the single-row delete tool."""
    route = agent.route_inventory_request(question)
    assert route.validation_error
    assert "not supported" in route.validation_error


def test_update_stock_rejects_non_integer_and_out_of_range(mcp_sessions):
    """Regression: 5.5 was stored as 6 while the tool reported success with 5.5."""
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(user_id=user_id, name="Widget", category="Tools", stock=10, price=100.0))
    db.commit()
    db.close()

    fractional = mcp_server.update_stock(
        user_id=user_id, product_name="Widget", new_quantity=5.5
    )
    assert "error" in fractional
    assert "whole number" in fractional["error"]

    overflow = mcp_server.update_stock(
        user_id=user_id, product_name="Widget", new_quantity=2**40
    )
    assert "error" in overflow
    assert "2147483647" in overflow["error"]

    negative = mcp_server.update_stock(
        user_id=user_id, product_name="Widget", new_quantity=-1
    )
    assert "error" in negative

    db = sessions()
    stock = db.query(Product).filter(Product.user_id == user_id).one().stock
    db.close()
    assert stock == 10, "rejected updates must not modify stock"


def test_update_stock_integral_float_is_accepted_and_reported_exactly(mcp_sessions):
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(user_id=user_id, name="Widget", category="Tools", stock=10, price=100.0))
    db.commit()
    db.close()

    result = mcp_server.update_stock(
        user_id=user_id, product_name="Widget", new_quantity=7.0
    )
    assert result["success"] is True
    assert result["stock"] == 7
    assert result["new_stock"] == 7
    assert isinstance(result["new_stock"], int)

    db = sessions()
    product = db.query(Product).filter(Product.user_id == user_id).one()
    assert product.stock == 7
    assert isinstance(product.stock, int)
    db.close()


def test_create_and_update_price_rounding_matches_rest_layer(mcp_sessions):
    """REST rounds prices to 2dp; the MCP tools must store the same value."""
    sessions, user_id = mcp_sessions
    created = mcp_server.create_product(
        user_id=user_id, name="Rounded", category="C", stock=1, price=19.999
    )
    assert created["success"] is True
    assert created["price"] == 20.0

    updated = mcp_server.update_product(
        user_id=user_id, product_name="Rounded", new_price=1.005
    )
    assert updated["success"] is True
    assert updated["price"] == round(1.005, 2)


def test_search_inventory_rejects_invalid_pagination(mcp_sessions):
    """
    Regression: a negative limit raised a raw psycopg2 error through MCP,
    which leaked the SQL statement and bound parameters.
    """
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(user_id=user_id, name="Widget", category="Tools", stock=1, price=1.0))
    db.commit()
    db.close()

    # Declared -> list[dict], so FastMCP validates the return type: an invalid
    # argument must raise rather than return a dict envelope.
    for kwargs in ({"limit": -5}, {"limit": 0}, {"offset": -1}, {"limit": 10**9}):
        with pytest.raises(ValueError) as excinfo:
            mcp_server.search_inventory(user_id=user_id, **kwargs)
        message = str(excinfo.value)
        assert "SQL" not in message
        assert "psycopg2" not in message

    for kwargs in ({"min_price": float("nan")}, {"max_price": float("inf")}):
        with pytest.raises(ValueError):
            mcp_server.search_inventory(user_id=user_id, **kwargs)


def test_get_products_by_names_ignores_blank_entries(mcp_sessions):
    """A blank name must not match every product."""
    sessions, user_id = mcp_sessions
    db = sessions()
    for name in ("Alpha", "Beta"):
        db.add(Product(user_id=user_id, name=name, category="C", stock=1, price=1.0))
    db.commit()
    db.close()

    assert mcp_server.get_products_by_names(user_id=user_id, names=[""]) == []
    assert mcp_server.get_products_by_names(user_id=user_id, names=["  "]) == []
    assert mcp_server.get_products_by_names(user_id=user_id, names=[]) == []
    only_alpha = mcp_server.get_products_by_names(user_id=user_id, names=["", "Alpha"])
    assert [p["name"] for p in only_alpha] == ["Alpha"]


def test_mcp_create_rejects_out_of_range_stock(mcp_sessions):
    sessions, user_id = mcp_sessions
    result = mcp_server.create_product(
        user_id=user_id, name="Huge", category="C", stock=10**12, price=1.0
    )
    assert "error" in result
    assert "2147483647" in result["error"]

    db = sessions()
    assert db.query(Product).filter(Product.user_id == user_id).count() == 0
    db.close()


def test_mcp_client_does_not_leak_sql_on_tool_error():
    """
    Regression: a raising tool returned its traceback, which embedded the full
    SQL statement and bound parameters straight into the user-visible answer.
    """
    import asyncio

    from mcp_bridge import client_manager as cm

    leaked = (
        "Error executing tool search_inventory: "
        "(psycopg2.errors.InvalidRowCountInLimitClause) LIMIT must not be negative\n"
        "[SQL: SELECT products.id, products.name FROM products "
        "WHERE products.user_id = %(user_id_1)s]\n"
        "[parameters: {'user_id_1': 7, 'param_1': -5}]"
    )

    class _Content:
        """Mirrors mcp.types.TextContent, which exposes the payload as .text."""

        type = "text"

        def __init__(self, text):
            self.text = text

        def __str__(self):
            # Real TextContent stringifies to a single-line repr whose newlines
            # are escaped, which is why the client must read .text.
            return f"TextContent(type='text', text={self.text!r})"

    class _Result:
        isError = True
        content = [_Content(leaked)]

    class _Session:
        async def call_tool(self, name, args):
            return _Result()

    manager = cm.MCPManager()
    manager.session = _Session()
    try:
        result = asyncio.run(manager.call_tool("search_inventory", {"user_id": 7}))
    finally:
        manager.session = None

    message = result["error"]
    assert "[SQL:" not in message
    assert "[parameters:" not in message
    assert "products.user_id" not in message
    assert "user_id_1" not in message
    # The actionable first line is still preserved.
    assert "LIMIT must not be negative" in message


def test_mcp_mutations_are_tenant_scoped(mcp_sessions):
    sessions, owner_id = mcp_sessions
    db = sessions()
    other_user = User(
        full_name="Other User",
        email="mcp-other@example.com",
        hashed_password="not-used",
    )
    db.add(other_user)
    db.commit()
    other_id = other_user.id
    db.close()

    created = mcp_server.create_product(
        user_id=owner_id,
        name="Tenant Product",
        category="Private",
        stock=2,
        price=12.0,
        supplier="Owner",
    )
    assert created["success"] is True
    assert mcp_server.get_products_by_category(user_id=other_id, category="Private") == []
    denied = mcp_server.delete_product(user_id=other_id, product_id=created["id"])
    assert "error" in denied
    denied_update = mcp_server.update_stock(
        user_id=other_id,
        product_name=created["name"],
        new_quantity=99,
    )
    assert "error" in denied_update

    numeric_delete = mcp_server.delete_product(user_id=owner_id, product_name="42")
    assert "error" in numeric_delete

    db = sessions()
    assert db.query(Product).filter(Product.id == created["id"]).count() == 1
    assert db.query(Product).filter(Product.id == created["id"]).one().stock == 2
    db.close()


@pytest.fixture
def routing_mocks(monkeypatch):
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in TOOL_NAMES
    ]
    call_tool = AsyncMock()
    monkeypatch.setattr(agent.mcp_manager, "get_tools", lambda: tools)
    monkeypatch.setattr(agent.mcp_manager, "call_tool", call_tool)
    monkeypatch.setattr(
        agent,
        "_get_openai_client",
        lambda: pytest.fail("Deterministic routes must not call the LLM"),
    )
    return call_tool


@pytest.mark.asyncio
async def test_create_request_routes_once_with_typed_values(routing_mocks):
    routing_mocks.return_value = {
        "success": True,
        "id": 31,
        "name": "Buttermilk",
        "category": "Dairy",
        "stock": 1,
        "price": 250.0,
        "supplier": "SRM",
    }
    result = await agent.run_query(
        "insert the product Buttermilk with category dairy and stock 1 price 250 rupees and supplier SRM",
        {"id": 42},
    )
    assert routing_mocks.await_count == 1
    routing_mocks.assert_awaited_once_with(
        "create_product",
        {
            "name": "Buttermilk",
            "category": "Dairy",
            "stock": 1,
            "price": 250.0,
            "supplier": "SRM",
            "user_id": 42,
        },
    )
    assert result.tool_used == "create_product"
    assert result.data[0]["id"] == 31
    assert "₹250.00" in result.answer


@pytest.mark.asyncio
async def test_category_request_uses_one_tool_and_preserves_price(routing_mocks):
    routing_mocks.return_value = {
        "id": 12,
        "name": "USB-C Hub 7-Port",
        "category": "Electronics",
        "stock": 20,
        "price": 1499.0,
        "supplier": "SRM",
    }
    result = await agent.run_query(
        "Show me all products in the Electronics category",
        {"id": 42},
    )
    assert routing_mocks.await_count == 1
    routing_mocks.assert_awaited_once_with(
        "get_products_by_category",
        {"category": "Electronics", "user_id": 42},
    )
    assert result.tool_used == "get_products_by_category"
    assert len(result.data) == 1
    assert result.data[0]["price"] == 1499.0
    assert "₹1,499.00" in result.answer
    assert "14,990" not in result.answer


def test_delete_name_does_not_treat_like_wildcards_as_data(mcp_sessions):
    sessions, user_id = mcp_sessions
    db = sessions()
    db.add(Product(
        user_id=user_id,
        name="AxBx Cable",
        category="Cables",
        stock=3,
        price=8.0,
        supplier="SRM",
    ))
    db.commit()
    db.close()

    result = mcp_server.delete_product(user_id=user_id, product_name="A_")
    assert "error" in result
    db = sessions()
    assert db.query(Product).filter(Product.name == "AxBx Cable").count() == 1
    db.close()


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (
            "add a product called Gadget in category Tools with stock 10 and price 500",
            {"tool": "create_product", "name": "Gadget", "category": "Tools", "stock": 10, "price": 500.0},
        ),
        (
            "insert product Lamp in the Home category with price 900 and stock 4",
            {"tool": "create_product", "name": "Lamp", "category": "Home", "stock": 4, "price": 900.0},
        ),
        (
            "add product Cable with category Electronics and stock 5 and price 1,499.50 and supplier SRM",
            {"tool": "create_product", "name": "Cable", "category": "Electronics", "stock": 5, "price": 1499.50},
        ),
    ],
)
def test_create_router_handles_prepositions_and_thousands_separators(question, expected):
    route = agent.route_inventory_request(question)
    assert route.tool_name == expected["tool"]
    assert route.validation_error is None
    for field in ("name", "category", "stock", "price"):
        assert route.arguments[field] == expected[field]


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        # Numbers written before the noun: "with 25 stock at 45 rupees from Amul".
        (
            "add a product Buttermilk 1L in category Dairy with 25 stock at 45 rupees from Amul",
            {"name": "Buttermilk 1L", "category": "Dairy", "stock": 25, "price": 45.0, "supplier": "Amul"},
        ),
        (
            "add Green Tea 250g in category Beverages with stock 30 at 199 rupees",
            {"name": "Green Tea 250g", "category": "Beverages", "stock": 30, "price": 199.0},
        ),
    ],
)
def test_create_router_accepts_number_before_noun_phrasing(question, expected):
    """A create phrased 'with 25 stock at 45 rupees' must not be read as a restock."""
    route = agent.route_inventory_request(question)
    assert route.tool_name == "create_product"
    assert route.validation_error is None
    for field, value in expected.items():
        assert route.arguments[field] == value


def test_category_only_change_routes_to_update_product():
    """'category of X to Y' is a category change, not a stock target."""
    route = agent.route_inventory_request("update the category of Cotton T-Shirt to Clothing")
    assert route.tool_name == "update_product"
    assert route.validation_error is None
    assert route.arguments["product_name"] == "Cotton T-Shirt"
    assert route.arguments["new_category"] == "Clothing"
    assert route.arguments["new_stock"] is None
    assert route.arguments["new_price"] is None


def test_category_locator_is_never_read_as_a_category_change():
    """'in category grains to 60' is a locator plus a stock target."""
    route = agent.route_inventory_request(
        "update the Quantity of basmati ricce 1kg in category grains to 60"
    )
    assert route.tool_name == "update_stock"
    assert route.arguments["product_name"] == "basmati ricce 1kg"
    assert route.arguments["new_quantity"] == 60

    combined = agent.route_inventory_request(
        "update the category of Widget to Electronics and price to 99"
    )
    assert combined.tool_name == "update_product"
    assert combined.arguments["new_category"] == "Electronics"
    assert combined.arguments["new_price"] == 99.0
    assert combined.arguments["new_stock"] is None


def test_restock_phrasing_is_not_mistaken_for_create():
    """'add 5 more units of milk' is a restock and must not create a product."""
    route = agent.route_inventory_request("add 5 more units of milk")
    assert route.tool_name == "update_stock"
    assert route.validation_error


def test_router_does_not_treat_stock_increase_or_reports_as_create():
    increase = agent.route_inventory_request("add 5 more units of milk")
    assert increase.tool_name == "update_stock"
    assert increase.validation_error

    price_update = agent.route_inventory_request("change the price of Widget to 99")
    assert price_update.tool_name == "update_product"
    assert price_update.validation_error is None
    assert price_update.arguments["new_price"] == 99.0

    assert agent.route_inventory_request("create a report of low stock items") is None
    assert agent.route_inventory_request("how many products are in each category?") is None


def test_update_delete_and_ambiguous_phrasings():
    increase = agent.route_inventory_request("increase the stock of Widget by 5")
    assert increase.tool_name == "update_stock"
    assert increase.arguments["product_name"] == "Widget"
    assert increase.arguments["new_quantity"] is None
    assert increase.validation_error

    delete_id = agent.route_inventory_request("delete product 12")
    assert delete_id.tool_name == "delete_product"
    assert delete_id.arguments["product_id"] == 12
    assert delete_id.arguments["product_name"] is None

    remove_name = agent.route_inventory_request("remove Widget from my inventory")
    assert remove_name.tool_name == "delete_product"
    assert remove_name.arguments["product_name"] == "Widget"

    all_products = agent.route_inventory_request("show me all products")
    assert all_products.tool_name == "search_inventory"
    assert all_products.arguments["limit"] == 500

    stock_only = agent.route_inventory_request(
        "update the Quantity of basmati ricce 1kg in category grains to 60"
    )
    assert stock_only.tool_name == "update_stock"
    assert stock_only.validation_error is None
    assert stock_only.arguments["product_name"] == "basmati ricce 1kg"
    assert stock_only.arguments["new_quantity"] == 60

    stock_and_price = agent.route_inventory_request(
        "update the Quantity of basmati ricce 1kg in category grains to 60 and price to 130"
    )
    assert stock_and_price.tool_name == "update_product"
    assert stock_and_price.validation_error is None
    assert stock_and_price.arguments["product_name"] == "basmati ricce 1kg"
    assert stock_and_price.arguments["new_stock"] == 60
    assert stock_and_price.arguments["new_price"] == 130.0
    assert stock_and_price.arguments["new_category"] is None

    price_only = agent.route_inventory_request("update the price of Basmati Rice 1kg to 130")
    assert price_only.tool_name == "update_product"
    assert price_only.arguments["product_name"] == "Basmati Rice 1kg"
    assert price_only.arguments["new_price"] == 130.0
    assert price_only.arguments["new_stock"] is None


@pytest.mark.asyncio
async def test_mutation_failure_is_never_reported_as_success(routing_mocks):
    routing_mocks.return_value = {
        "error": "Multiple products match 'Widget'.",
        "matches": [{"id": 1, "name": "Widget"}, {"id": 2, "name": "Widget"}],
    }
    result = await agent.run_query("delete Widget", {"id": 42})
    assert "failed" in result.answer.lower()
    assert result.data[0]["error"]
    assert len(result.data[0]["matches"]) == 2


def test_general_loop_numeric_guard_preserves_only_tool_values():
    rows = [{"id": 12, "name": "USB-C Hub 7-Port", "stock": 20, "price": 1499.0}]
    assert agent._numeric_claims_preserved("It costs ₹1,499.00 and stock is 20.", rows)
    assert not agent._numeric_claims_preserved("It costs ₹14,990.00.", rows)


@pytest.mark.asyncio
async def test_update_and_delete_routes_each_execute_once(routing_mocks):
    routing_mocks.return_value = {
        "success": True,
        "id": 12,
        "name": "USB-C Hub 7-Port",
        "category": "Electronics",
        "stock": 20,
        "price": 1499.0,
        "supplier": "SRM",
        "old_stock": 10,
        "new_stock": 20,
    }
    updated = await agent.run_query(
        "update the Quantity of this USB-C Hub 7-Port to 20 in electronics table",
        {"id": 42},
    )
    assert routing_mocks.await_count == 1
    assert routing_mocks.await_args.args[0] == "update_stock"
    assert routing_mocks.await_args.args[1] == {
        "product_name": "USB-C Hub 7-Port",
        "new_quantity": 20,
        "user_id": 42,
    }
    assert updated.tool_used == "update_stock"
    assert updated.data[0]["price"] == 1499.0

    routing_mocks.reset_mock()
    routing_mocks.return_value = {
        "success": True,
        "id": 31,
        "name": "Buttermilk",
        "category": "Dairy",
        "stock": 1,
        "price": 250.0,
        "supplier": "SRM",
    }
    deleted = await agent.run_query("delete Buttermilk", {"id": 42})
    assert routing_mocks.await_count == 1
    assert routing_mocks.await_args.args[0] == "delete_product"
    assert deleted.tool_used == "delete_product"
    assert deleted.data[0]["id"] == 31
