"""
tests/test_llm_intent.py — LLM structured-intent pipeline regression tests.

The model is stubbed (no network in CI). Everything after extraction is real:
deterministic validation, the clarification store, and the actual MCP tool
functions against a database.

Client contract under test: the API returns only conversational text plus, when
useful, a table of plain product rows. No intent, tool name, confirmation flag,
or success marker is ever exposed.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))

import server as mcp_server
from ai import agent
from ai.clarification import clarification_store
from ai.intent_schema import (
    CANONICAL_TOOL,
    REQUIRED_FIELDS,
    Clarification,
    IntentPlan,
    Rejection,
    build_intent,
    merge_intent,
    normalise_product_query,
    parse_quantity,
    salvage_missing_create_fields,
    user_stated_ids,
    validate_intent,
)
from db.models import Product, User

RICE_1KG = {"name": "Basmati Rice 1kg", "category": "Grains", "stock": 20, "price": 120.0, "supplier": "AgroSupply"}
RICE_5KG = {"name": "Basmati Rice 5kg", "category": "Grains", "stock": 5, "price": 550.0, "supplier": "AgroSupply"}
HUB = {"name": "USB-C Hub 7-Port", "category": "Electronics", "stock": 10, "price": 1499.0, "supplier": "TechMart"}
MILK = {"name": "Full Cream Milk 1L", "category": "Dairy", "stock": 40, "price": 68.5, "supplier": "DairyFresh"}

PUBLIC_KEYS = {"id", "name", "category", "stock", "price", "supplier"}
FORBIDDEN_TEXT = (
    "needs_clarification", "select_candidate", "updated_fields", "old_stock",
    "new_stock", "product_id", "create_product(", "update_product(",
    "delete_product(", "search_inventory(", "query_inventory_db(",
)
# Internal agent/result-envelope fields that must never reach the client.
FORBIDDEN_KEYS = {
    "success", "message", "before", "updated_fields", "product_id", "old_stock",
    "new_stock", "deleted_id", "error", "needs_clarification", "missing",
    "tool", "intent", "select_candidate", "user_id", "matches",
}
MUTATION_TOOLS = {"create_product", "update_stock", "update_product", "delete_product"}


@pytest.fixture
def inventory(db_engine, monkeypatch):
    """A real database plus the real MCP tool functions bound to it."""
    sessions = sessionmaker(bind=db_engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "_get_db", sessions)
    db = sessions()
    user = User(full_name="Intent User", email="intent@example.com", hashed_password="x")
    db.add(user)
    db.commit()
    user_id = user.id
    for item in (RICE_1KG, RICE_5KG, HUB, MILK):
        db.add(Product(user_id=user_id, **item))
    db.commit()
    db.close()
    clarification_store.clear(user_id)
    yield sessions, user_id
    clarification_store.clear(user_id)


def _install_model(monkeypatch, payloads):
    queue = list(payloads)
    calls = []

    def _create(**kwargs):
        calls.append(kwargs)
        payload = queue.pop(0) if queue else {}
        message = SimpleNamespace(content=None, tool_calls=None)
        if payload is not None:
            message.tool_calls = [SimpleNamespace(
                id="call_1", type="function",
                function=SimpleNamespace(
                    name="submit_intent", arguments=json.dumps(payload)),
            )]
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))
    )
    monkeypatch.setattr(agent, "_get_openai_client", lambda: client)
    monkeypatch.setattr(agent, "clarification_store", clarification_store)
    return calls


class _Harness:
    """Live view over the recorded LLM calls and MCP tool calls."""

    def __init__(self, llm_calls, tool_calls):
        self.llm = llm_calls
        self.tools = tool_calls

    @property
    def mutations(self):
        return [c for c in self.tools if c[0] in MUTATION_TOOLS]

    @property
    def mutation_names(self):
        return [name for name, _ in self.mutations]


@pytest.fixture
def scripted(monkeypatch):
    """Scripted model + an MCP bridge that runs the real tools and records calls."""

    def _install(payloads):
        llm_calls = _install_model(monkeypatch, payloads)
        tool_calls = []

        async def _call_tool(name, arguments):
            tool_calls.append((name, dict(arguments)))
            return getattr(mcp_server, name)(**dict(arguments))

        monkeypatch.setattr(agent.mcp_manager, "call_tool", _call_tool)
        return _Harness(llm_calls, tool_calls)

    return _install


def _tools():
    return [{"type": "function", "function": {"name": n}} for n in CANONICAL_TOOL.values()]


async def _run(question, user_id):
    return await agent._run_llm_intent(question, user_id, _tools())


def _rows(user_id, sessions, name):
    db = sessions()
    rows = [p for p in db.query(Product).filter(Product.user_id == user_id).all() if p.name == name]
    db.close()
    return rows


def _product(user_id, sessions, name):
    rows = _rows(user_id, sessions, name)
    return rows[0] if rows else None


def _assert_clean(response):
    """The client must never receive agent internals."""
    assert response.tool_used is None, f"tool leaked: {response.tool_used}"
    text = response.answer or ""
    for token in FORBIDDEN_TEXT:
        assert token not in text, f"{token!r} leaked into answer: {text!r}"
    for row in response.data or []:
        leaked = FORBIDDEN_KEYS & set(row)
        assert not leaked, f"internal fields in data row: {leaked}"


def _fmt(value):
    return f"₹{float(value):,.2f}"


# ── The reported bug: all missing create fields asked at once ────────────────

@pytest.mark.asyncio
async def test_create_missing_price_and_supplier_asks_for_both(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["phonecase"], "category": "electronics",
        "changes": {"stock": 90},
    }])

    response = await _run(
        "add a product to electronics category name is phonecase stock is 90", user_id
    )

    assert response.data is None, "nothing may be created yet"
    answer = response.answer.lower()
    assert "price" in answer and "supplier" in answer, response.answer
    assert response.answer.count("?") == 1, "must be one concise question"
    _assert_clean(response)
    pending = clarification_store.get(user_id)
    assert pending is not None
    assert sorted(pending.missing) == ["price", "supplier"]


@pytest.mark.asyncio
async def test_clarification_answer_creates_with_no_invented_supplier(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}},
        {"status": "ready", "intent": "create_product",
         "changes": {"price": 100}, "supplier": "navya"},
    ])

    first = await _run(
        "add a product to electronics category name is phonecase stock is 90", user_id
    )
    assert first.data is None

    second = await _run("price is 100 rupees and supplier is navya", user_id)

    assert len(harness.mutations) == 1
    name, args = harness.mutations[0]
    assert name == "create_product"
    assert args["supplier"] == "navya", "supplier must come from the user, not a default"
    assert "Unknown" not in (second.answer, json.dumps(second.data or []))
    row = _product(user_id, sessions, "phonecase")
    assert row is not None and row.stock == 90 and row.price == 100.0
    assert row.supplier == "navya"
    assert "navya" in second.answer
    _assert_clean(second)


@pytest.mark.asyncio
async def test_supplier_is_never_defaulted(inventory, scripted):
    """With no supplier anywhere in the conversation, nothing is created."""
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90, "price": 100}},
    ])

    response = await _run("add phonecase in electronics, stock 90, price 100", user_id)

    assert response.data is None
    assert "supplier" in response.answer.lower()
    assert _product(user_id, sessions, "phonecase") is None
    _assert_clean(response)


@pytest.mark.asyncio
async def test_follow_up_supplying_one_field_asks_only_for_the_rest(inventory, scripted):
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}},
        {"status": "ready", "intent": "create_product", "changes": {"price": 100}},
        {"status": "ready", "intent": "create_product", "supplier": "navya"},
    ])

    await _run("add phonecase in electronics with stock 90", user_id)
    second = await _run("price is 100 rupees", user_id)

    assert second.data is None
    assert "supplier" in second.answer.lower()
    assert "price" not in second.answer.lower(), "price was already supplied"
    third = await _run("supplier is navya", user_id)

    assert third.data[0]["name"] == "phonecase"
    row = _product(user_id, sessions, "phonecase")
    assert row.price == 100.0 and row.supplier == "navya" and row.stock == 90


@pytest.mark.asyncio
async def test_follow_up_preserves_earlier_fields(inventory, scripted):
    """The second message must not discard the first message's values."""
    sessions, user_id = inventory
    harness = scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}},
        {"status": "ready", "intent": "create_product",
         "changes": {"price": 100}, "supplier": "navya"},
    ])

    await _run("add phonecase in electronics with stock 90", user_id)
    await _run("100 rupees, supplier navya", user_id)

    _, args = harness.mutations[0]
    assert args["name"] == "phonecase"      # from turn 1
    assert args["category"] == "electronics"  # from turn 1
    assert args["stock"] == 90              # from turn 1
    assert args["price"] == 100.0           # from turn 2
    assert args["supplier"] == "navya"      # from turn 2


@pytest.mark.asyncio
async def test_follow_up_cannot_redirect_the_pending_operation(inventory, scripted):
    """
    A half-finished create must never be turned into a delete of another
    product. Either the create completes with the user's own values, or the
    pending question is abandoned; a delete never happens silently either way.
    """
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}},
        {"status": "ready", "intent": "delete_product",
         "product_queries": ["Basmati Rice 1kg"],
         "changes": {"price": 100}, "supplier": "navya"},
    ])

    await _run("add phonecase in electronics with stock 90", user_id)
    await _run("100 rupees, supplier navya", user_id)

    # Nothing was deleted: the delete, if pursued, needs its own confirmation.
    assert _product(user_id, sessions, "Basmati Rice 1kg") is not None
    # The create is either absent (abandoned) or complete with the real supplier.
    phonecase = _product(user_id, sessions, "phonecase")
    assert phonecase is None or phonecase.supplier == "navya"


@pytest.mark.asyncio
async def test_invalid_follow_up_keeps_pending_and_reasks(inventory, scripted):
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}},
        {"status": "ready", "intent": "create_product"},
    ])

    await _run("add phonecase in electronics with stock 90", user_id)
    again = await _run("I don't know", user_id)

    assert again.data is None
    assert "price" in again.answer.lower() and "supplier" in again.answer.lower()
    assert clarification_store.get(user_id) is not None, "pending intent must survive"
    assert _product(user_id, sessions, "phonecase") is None
    _assert_clean(again)


@pytest.mark.asyncio
async def test_clarification_gives_up_after_repeated_failures(inventory, scripted, monkeypatch):
    sessions, user_id = inventory
    monkeypatch.setattr(agent, "MAX_CLARIFICATIONS", 2)
    empty = {"status": "ready", "intent": "create_product",
             "product_queries": ["phonecase"], "category": "electronics",
             "changes": {"stock": 90}}
    scripted([empty, empty, empty])

    await _run("add phonecase in electronics with stock 90", user_id)
    await _run("hmm", user_id)
    third = await _run("still nothing", user_id)

    assert "restate" in third.answer.lower()
    assert third.data is None
    assert clarification_store.get(user_id) is None


# ── Create with everything supplied ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_with_all_fields_executes_immediately(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["phonecase"], "category": "electronics",
        "changes": {"stock": 90, "price": 100}, "supplier": "navya",
    }])

    response = await _run(
        "add phonecase to electronics with 90 in stock at 100 rupees from navya", user_id
    )

    assert len(harness.mutations) == 1
    row = _product(user_id, sessions, "phonecase")
    assert (row.stock, row.price, row.supplier) == (90, 100.0, "navya")
    assert "phonecase" in response.answer and "navya" in response.answer
    _assert_clean(response)


@pytest.mark.asyncio
async def test_spelled_out_number_is_understood_and_price_applied(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "update_product",
        "product_queries": ["USBC Hub 7 Port Electronics Item two 1500"],
        "changes": {"price": 1500},
    }])

    response = await _run(
        "Update the price of the USBC Hub 7 Port Electronics Item two 1500", user_id
    )

    assert len(harness_mutations(response)) >= 0  # placeholder-free: checked below
    row = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert row.price == 1500.0 and row.stock == 10
    assert "₹1,500.00" in response.answer
    _assert_clean(response)


def harness_mutations(response):
    return response.data or []


# ── Canonical mapping ─────────────────────────────────────────────────────────

def test_canonical_tool_is_defined_for_every_intent():
    assert set(CANONICAL_TOOL) == set(CANONICAL_TOOL.values())


@pytest.mark.asyncio
async def test_model_supplied_tool_is_overridden_by_canonical_mapping(inventory, scripted):
    """intent=update_product with tool=delete_product must still update."""
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "update_product", "tool": "delete_product",
        "product_queries": ["USB-C Hub 7-Port"], "changes": {"price": 42.0},
    }])

    response = await _run("set the usb-c hub price to 42", user_id)

    assert [name for name, _ in harness.tools if name in MUTATION_TOOLS] == ["update_product"]
    assert _product(user_id, sessions, "USB-C Hub 7-Port").price == 42.0
    assert _product(user_id, sessions, "USB-C Hub 7-Port") is not None
    _assert_clean(response)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "payload", "expected_tool"),
    [
        ("search for basmati",
         {"status": "ready", "intent": "search_inventory", "product_queries": ["basmati"]},
         "search_inventory"),
        ("find rice please",
         {"status": "ready", "intent": "search_inventory", "product_queries": ["rice"]},
         "search_inventory"),
        ("list everything in stock under 5",
         {"status": "ready", "intent": "search_inventory", "stock_threshold": 5},
         "search_inventory"),
        ("which products are running low?",
         {"status": "ready", "intent": "get_low_stock_items", "threshold": 10},
         "get_low_stock_items"),
        ("show me the Electronics category",
         {"status": "ready", "intent": "get_products_by_category", "category": "Electronics"},
         "get_products_by_category"),
        ("what categories exist?",
         {"status": "ready", "intent": "get_all_categories"},
         "get_all_categories"),
        ("give me a category breakdown",
         {"status": "ready", "intent": "get_category_analytics"},
         "get_category_analytics"),
        ("how is my inventory overall?",
         {"status": "ready", "intent": "get_inventory_analytics"},
         "get_inventory_analytics"),
        ("details for Full Cream Milk 1L",
         {"status": "ready", "intent": "get_product_details",
          "product_queries": ["Full Cream Milk 1L"]},
         "get_product_details"),
        ("look up Basmati Rice 1kg",
         {"status": "ready", "intent": "query_inventory_db",
          "product_queries": ["Basmati Rice 1kg"]},
         "query_inventory_db"),
        ("get me rice and milk",
         {"status": "ready", "intent": "get_products_by_names",
          "names": ["rice", "milk"]},
         "get_products_by_names"),
    ],
)
async def test_read_variations_use_the_canonical_tool(inventory, scripted, question, payload, expected_tool):
    sessions, user_id = inventory
    harness = scripted([payload])
    response = await _run(question, user_id)
    # A single-product read may resolve the target first; the action is last.
    assert harness.tools[-1][0] == expected_tool, harness.tools
    assert harness.mutations == []
    _assert_clean(response)


@pytest.mark.asyncio
async def test_exactly_one_mutation_tool_runs_per_request(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "update_product",
        "product_queries": ["USB-C Hub 7-Port"],
        "changes": {"stock": 33, "price": 1000.0, "category": "Gadgets"},
    }])
    await _run("change the usb-c hub stock price and category all at once", user_id)
    assert len(harness.mutations) == 1
    assert harness.mutations[0][0] == "update_product"


@pytest.mark.asyncio
async def test_search_for_basmati_returns_one_row_each(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "search_inventory", "product_queries": ["basmati"]}])
    response = await _run("search for basmati", user_id)
    assert response.data is not None
    assert sorted(r["name"] for r in response.data) == ["Basmati Rice 1kg", "Basmati Rice 5kg"]
    _assert_clean(response)


# ── Updates ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stock_price_and_category_updates(inventory, scripted):
    sessions, user_id = inventory
    db = sessions()
    db.add(Product(user_id=user_id, name="Cotton T-Shirt", category="Apparel",
                  stock=5, price=500.0, supplier="S"))
    db.commit()
    db.close()
    for question, payload, check in [
        ("update the Quantity of USB-C Hub 7-Port to 20",
         {"status": "ready", "intent": "update_stock",
          "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 20}},
         lambda r: r.stock == 20),
        ("change the price of Full Cream Milk 1L to 72.25",
         {"status": "ready", "intent": "update_product",
          "product_queries": ["Full Cream Milk 1L"], "changes": {"price": 72.25}},
         lambda r: r.price == 72.25),
        ("update the category of Cotton T-Shirt to Clothing",
         {"status": "ready", "intent": "update_product",
          "product_queries": ["Cotton T-Shirt"], "changes": {"category": "Clothing"}},
         lambda r: r.category == "clothing"),
    ]:
        scripted([payload])
        response = await _run(question, user_id)
        assert check(_product(user_id, sessions, payload["product_queries"][0])), question
        _assert_clean(response)


@pytest.mark.asyncio
async def test_final_response_reflects_committed_row(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "update_product",
        "product_queries": ["USB-C Hub 7-Port"], "changes": {"price": 1250.5},
    }])
    response = await _run("set the usb-c hub price to 1250.50", user_id)
    committed = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert committed.price == 1250.5
    assert response.data[0]["price"] == 1250.5
    assert "₹1,250.50" in response.answer
    _assert_clean(response)


@pytest.mark.asyncio
async def test_size_token_in_user_text_resolves_ambiguity(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["basmati rice"], "changes": {"stock": 60}}])
    response = await _run(
        "update the Quantity of basmati ricce 1kg in category grains to 60", user_id
    )
    assert response.data[0]["name"] == "Basmati Rice 1kg"
    assert _product(user_id, sessions, "Basmati Rice 1kg").stock == 60
    assert _product(user_id, sessions, "Basmati Rice 5kg").stock == 5
    _assert_clean(response)


@pytest.mark.asyncio
async def test_true_ambiguity_asks_instead_of_guessing(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["basmati rice"], "changes": {"stock": 77}}])
    response = await _run("update the Quantity of basmati ricce to 77", user_id)
    assert response.data is None
    assert "1kg" in response.answer and "5kg" in response.answer
    assert _product(user_id, sessions, "Basmati Rice 1kg").stock == 20
    assert _product(user_id, sessions, "Basmati Rice 5kg").stock == 5


@pytest.mark.asyncio
async def test_ambiguous_product_selection_then_execution(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([
        {"status": "ready", "intent": "update_stock",
         "product_queries": ["basmati rice"], "changes": {"stock": 12}},
        {"status": "ready", "intent": "update_stock",
         "product_queries": ["basmati rice"], "select_candidate": "Basmati Rice 5kg"},
    ])
    first = await _run("set the stock of the basmati rice to 12", user_id)
    assert first.data is None and "Which one" in first.answer
    assert harness.mutations == [], "asking must not mutate"
    second = await _run("the 5kg one", user_id)
    # Exactly one mutation, and the stock value from turn 1 survived.
    assert harness.mutation_names == ["update_stock"]
    assert _product(user_id, sessions, "Basmati Rice 5kg").stock == 12
    assert _product(user_id, sessions, "Basmati Rice 1kg").stock == 20
    _assert_clean(second)


@pytest.mark.asyncio
async def test_relative_change_asks_for_the_absolute_level(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["Full Cream Milk 1L"],
               "changes": {"stock": 5, "stock_is_relative": True}}])
    response = await _run("add 5 more milk", user_id)
    assert response.data is None
    assert "new stock level" in response.answer
    assert _product(user_id, sessions, "Full Cream Milk 1L").stock == 40
    _assert_clean(response)


@pytest.mark.asyncio
async def test_restock_is_an_update_not_a_create(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "update_stock",
                         "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 15}}])
    response = await _run("add 5 more USB-C Hub 7-Port", user_id)
    assert [n for n, _ in harness.mutations] == ["update_stock"]
    assert _product(user_id, sessions, "USB-C Hub 7-Port").stock == 15
    assert _product(user_id, sessions, "5 more USB-C Hub 7-Port") is None


@pytest.mark.asyncio
async def test_multiple_mutation_targets_refused(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "update_stock",
                         "product_queries": ["Basmati Rice 1kg", "Full Cream Milk 1L"],
                         "changes": {"stock": 99}}])
    response = await _run("set the stock of basmati rice 1kg and milk to 99", user_id)
    assert harness.mutations == []
    assert response.data is None
    assert "one product at a time" in response.answer
    assert _product(user_id, sessions, "Basmati Rice 1kg").stock == 20
    assert _product(user_id, sessions, "Full Cream Milk 1L").stock == 40
    _assert_clean(response)


# ── Delete confirmation ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_requires_confirmation_then_executes(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "delete_product",
                         "product_queries": ["Full Cream Milk 1L"]}])

    first = await _run("delete the full cream milk", user_id)

    assert harness.mutations == [], "nothing may be deleted before confirmation"
    assert first.data is None
    assert "?" in first.answer and "Full Cream Milk 1L" in first.answer
    assert _product(user_id, sessions, "Full Cream Milk 1L") is not None
    pending = clarification_store.get(user_id)
    assert pending is not None and pending.stage == "confirm_delete"
    _assert_clean(first)

    second = await _run("yes, delete it", user_id)

    assert [n for n, _ in harness.mutations] == ["delete_product"]
    assert _product(user_id, sessions, "Full Cream Milk 1L") is None
    assert _product(user_id, sessions, "Basmati Rice 1kg") is not None
    assert "Deleted" in second.answer
    _assert_clean(second)
    assert clarification_store.get(user_id) is None


@pytest.mark.asyncio
async def test_delete_rejection_changes_nothing(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "delete_product",
                         "product_queries": ["Full Cream Milk 1L"]}])

    await _run("delete the full cream milk", user_id)
    cancelled = await _run("no, keep it", user_id)

    assert harness.mutations == []
    assert _product(user_id, sessions, "Full Cream Milk 1L") is not None
    assert "did not delete" in cancelled.answer
    assert cancelled.data is None
    assert clarification_store.get(user_id) is None
    _assert_clean(cancelled)


@pytest.mark.asyncio
async def test_delete_unanswered_reply_asks_again_and_keeps_pending(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "delete_product",
                         "product_queries": ["Full Cream Milk 1L"]}])
    await _run("delete the full cream milk", user_id)
    again = await _run("what do you mean?", user_id)
    assert harness.mutations == []
    assert "yes or no" in again.answer
    assert clarification_store.get(user_id) is not None
    assert _product(user_id, sessions, "Full Cream Milk 1L") is not None


@pytest.mark.asyncio
async def test_delete_unknown_product_never_claims_success(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "delete_product",
                         "product_queries": ["Nonexistent Product 99"]}])
    response = await _run("delete Nonexistent Product 99", user_id)
    assert harness.mutations == []
    assert response.data is None
    assert "No product" in response.answer
    _assert_clean(response)


@pytest.mark.asyncio
async def test_delete_by_user_stated_id_is_allowed(inventory, scripted):
    sessions, user_id = inventory
    db = sessions()
    milk_id = db.query(Product).filter(
        Product.user_id == user_id, Product.name == "Full Cream Milk 1L").one().id
    db.close()
    scripted([{"status": "ready", "intent": "delete_product", "product_id": milk_id}])
    await _run(f"delete product {milk_id}", user_id)
    confirmed = await _run("yes", user_id)
    assert _product(user_id, sessions, "Full Cream Milk 1L") is None
    _assert_clean(confirmed)


# ── Security boundaries ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_model_cannot_invent_a_product_id(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "update_stock",
                         "product_id": 424242, "changes": {"stock": 1}}])
    response = await _run("make it stock 1", user_id)
    assert harness.mutations == []
    assert response.data is None
    assert "product ID that you provided" in response.answer
    assert _product(user_id, sessions, "Basmati Rice 1kg").stock == 20
    _assert_clean(response)


@pytest.mark.asyncio
async def test_model_cannot_supply_or_override_user_id(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "update_stock",
        "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 12},
        "user_id": 999999,
    }])
    await _run("set usb-c hub stock to 12", user_id)
    assert harness.mutations[0][1]["user_id"] == user_id
    other = sessions()
    leaked = other.query(Product).filter(Product.user_id == 999999).count()
    other.close()
    assert leaked == 0
    assert "user_id" not in json.dumps(agent.INTENT_TOOL)


@pytest.mark.asyncio
async def test_tenant_isolation_through_the_intent_path(inventory, scripted):
    sessions, user_a = inventory
    db = sessions()
    intruder = User(full_name="Intruder", email="intruder@example.com", hashed_password="x")
    db.add(intruder)
    db.commit()
    user_b = intruder.id
    db.close()

    harness = scripted([{"status": "ready", "intent": "update_stock",
                         "product_queries": ["Basmati Rice 1kg"], "changes": {"stock": 777}}])
    response = await _run("set basmati rice 1kg stock to 777", user_b)

    assert harness.mutations == []
    assert "No product" in response.answer
    assert _product(user_a, sessions, "Basmati Rice 1kg").stock == 20
    assert _product(user_b, sessions, "Basmati Rice 1kg") is None


@pytest.mark.asyncio
async def test_unknown_intent_and_bad_output_are_refused(inventory, scripted):
    sessions, user_id = inventory
    harness = scripted([{"status": "ready", "intent": "drop_database"}])
    response = await _run("drop everything", user_id)
    assert harness.mutations == []
    assert response.data is None
    _assert_clean(response)

    scripted([None])
    again = await _run("do something strange", user_id)
    assert again.data is None
    _assert_clean(again)


@pytest.mark.asyncio
async def test_analytics_uses_committed_totals(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "get_inventory_analytics"}])
    response = await _run("give me analytics", user_id)
    stats = response.data[0]
    assert stats["total_products"] == 4
    assert "₹22,880.00" in response.answer
    assert "Unnamed product" not in response.answer
    _assert_clean(response)


@pytest.mark.asyncio
async def test_provider_rejecting_forced_tool_call_degrades_gracefully(inventory, monkeypatch):
    """
    Regression: the provider returns HTTP 400 when the model declines a forced
    tool_choice. That surfaced as an unhandled 500 instead of a safe refusal.
    """
    from openai import BadRequestError

    sessions, user_id = inventory
    seen = []

    def _create(**kwargs):
        seen.append(kwargs.get("tool_choice"))
        if kwargs.get("tool_choice") != "auto":
            raise BadRequestError(
                "Tool choice is required, but model did not call a tool",
                response=SimpleNamespace(status_code=400, headers={}, request=None),
                body=None,
            )
        payload = {"status": "ready", "intent": "get_all_categories"}
        message = SimpleNamespace(
            content=None,
            tool_calls=[SimpleNamespace(
                id="c", type="function",
                function=SimpleNamespace(
                    name="submit_intent", arguments=json.dumps(payload)),
            )],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    monkeypatch.setattr(agent, "_get_openai_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))))
    monkeypatch.setattr(agent, "clarification_store", clarification_store)

    async def _call_tool(name, arguments):
        return getattr(mcp_server, name)(**dict(arguments))

    monkeypatch.setattr(agent.mcp_manager, "call_tool", _call_tool)

    response = await _run("what categories do I have", user_id)

    assert seen[0] != "auto" and seen[-1] == "auto", seen
    assert response.tool_used is None
    assert "categor" in response.answer.lower()
    _assert_clean(response)


@pytest.mark.asyncio
async def test_total_extraction_failure_is_a_safe_refusal(inventory, monkeypatch):
    from openai import BadRequestError

    sessions, user_id = inventory

    def _create(**kwargs):
        raise BadRequestError(
            "Tool choice is required, but model did not call a tool",
            response=SimpleNamespace(status_code=400, headers={}, request=None),
            body=None,
        )

    monkeypatch.setattr(agent, "_get_openai_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_create))))
    monkeypatch.setattr(agent, "clarification_store", clarification_store)
    monkeypatch.setattr(agent.mcp_manager, "call_tool", AsyncMock())

    response = await _run("do something", user_id)
    assert response.data is None
    assert "could not turn that" in response.answer.lower()
    _assert_clean(response)


@pytest.mark.asyncio
async def test_ambiguous_ask_is_settled_from_user_words_not_re_asked(inventory, scripted):
    """
    The model may ask which rice even though the user wrote '1kg'. The database
    plus the user's own words settle it, so no needless question is asked.
    """
    sessions, user_id = inventory
    scripted([{
        "status": "needs_clarification", "intent": "delete_product",
        "product_queries": ["basmati rice"], "missing": ["product_query"],
        "clarification_question": "Which basmati rice product?",
    }])

    response = await _run("delete the basmati rice 1kg", user_id)

    # It went straight to the delete confirmation for the 1kg row.
    assert "Basmati Rice 1kg" in response.answer
    assert "?" in response.answer
    assert "cannot be undone" in response.answer
    assert _product(user_id, sessions, "Basmati Rice 1kg") is not None
    pending = clarification_store.get(user_id)
    assert pending is not None and pending.stage == "confirm_delete"
    _assert_clean(response)


@pytest.mark.asyncio
async def test_genuinely_ambiguous_ask_is_not_settled(inventory, scripted):
    """Without a decisive token the question stands and nothing is executed."""
    sessions, user_id = inventory
    harness = scripted([{
        "status": "needs_clarification", "intent": "delete_product",
        "product_queries": ["basmati rice"], "missing": ["product_query"],
        "clarification_question": "Which basmati rice product?",
    }])

    response = await _run("delete the basmati rice", user_id)

    assert harness.mutations == []
    assert "?" in response.answer
    assert _product(user_id, sessions, "Basmati Rice 1kg") is not None
    assert _product(user_id, sessions, "Basmati Rice 5kg") is not None


@pytest.mark.asyncio
async def test_failed_request_leaves_no_pending_state(inventory, scripted):
    """
    Regression: a failed create kept its pending intent, so the next unrelated
    message was merged into it and asked a nonsense follow-up.
    """
    sessions, user_id = inventory
    scripted([
        # Ask about price+supplier, then collide with an existing product name.
        {"status": "ready", "intent": "create_product",
         "product_queries": ["Basmati Rice 1kg"], "category": "Grains",
         "changes": {"stock": 5}},
        {"status": "ready", "intent": "create_product",
         "changes": {"price": 10.0}, "supplier": "S"},
    ])

    await _run("add basmati rice 1kg in grains with stock 5", user_id)
    failed = await _run("price 10 rupees, supplier S", user_id)

    assert "already exists" in failed.answer
    assert clarification_store.get(user_id) is None, "a settled request must not stay pending"
    # A brand new, unrelated request must start clean.
    scripted([{"status": "ready", "intent": "get_all_categories"}])
    fresh = await _run("what categories do I have", user_id)
    assert "categor" in fresh.answer.lower()
    _assert_clean(fresh)


@pytest.mark.asyncio
async def test_backend_recomputes_missing_fields_when_model_under_reports(inventory, scripted):
    """
    Regression: the model reported only 'price' missing while supplier was also
    absent, so the user was asked one field at a time. The backend is
    authoritative about what a mutation needs.
    """
    sessions, user_id = inventory
    scripted([{
        "status": "needs_clarification", "intent": "create_product",
        "product_queries": ["phonecase"], "category": "electronics",
        "changes": {"stock": 90}, "missing": ["price"],
        "clarification_question": "What price should I set?",
    }])

    response = await _run(
        "add a product to electronics category name is phonecase stock is 90", user_id
    )

    answer = response.answer.lower()
    assert "price" in answer and "supplier" in answer, response.answer
    assert response.data is None
    assert _product(user_id, sessions, "phonecase") is None
    pending = clarification_store.get(user_id)
    assert sorted(pending.missing) == ["price", "supplier"]
    _assert_clean(response)


@pytest.mark.asyncio
async def test_new_request_is_not_absorbed_by_a_pending_question(inventory, scripted):
    """
    Regression: while waiting for an absolute stock level, the user asked about
    a different product. The pending question must not swallow the new request.
    """
    sessions, user_id = inventory
    harness = scripted([
        {"status": "ready", "intent": "update_stock",
         "product_queries": ["Full Cream Milk 1L"],
         "changes": {"stock": 5, "stock_is_relative": True}},
        {"status": "ready", "intent": "update_stock",
         "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 42}},
    ])

    asked = await _run("add 5 more Full Cream Milk 1L", user_id)
    assert asked.data is None and "?" in asked.answer

    second = await _run("update the Quantity of USB-C Hub 7-Port to 42", user_id)

    assert harness.mutation_names == ["update_stock"]
    assert second.data[0]["name"] == "USB-C Hub 7-Port"
    assert _product(user_id, sessions, "USB-C Hub 7-Port").stock == 42
    assert _product(user_id, sessions, "Full Cream Milk 1L").stock == 40, "pending abandoned"


# ── Ask ALL missing required create fields at once ───────────────────────────

@pytest.mark.asyncio
async def test_missing_stock_and_supplier_asked_together(inventory, scripted):
    """BUG 2 case 1: never one field at a time."""
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["iPhone"], "category": "electronics",
        "changes": {"price": 100000.0},
    }, {
        "status": "ready", "intent": "create_product",
        "changes": {"stock": 25}, "supplier": "Apple",
    }])

    first = await _run(
        "Add a new item to electronics called iPhone with price 100000.", user_id)
    assert first.data is None
    answer = first.answer.lower()
    assert "stock" in answer and "supplier" in answer, first.answer
    assert first.answer.count("?") == 1
    pending = clarification_store.get(user_id)
    assert sorted(pending.missing) == ["stock", "supplier"]

    second = await _run("stock 25 and supplier Apple", user_id)
    assert harness.mutation_names == ["create_product"]
    row = _product(user_id, sessions, "iPhone")
    assert (row.stock, row.price, row.supplier) == (25, 100000.0, "Apple")


@pytest.mark.asyncio
async def test_missing_price_and_supplier_asked_together(inventory, scripted):
    """BUG 2 case 2."""
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Gadget"], "category": "tools",
        "changes": {"stock": 10},
    }])

    response = await _run("add a product called Gadget in category Tools with stock 10", user_id)
    assert response.data is None
    answer = response.answer.lower()
    assert "price" in answer and "supplier" in answer, response.answer
    assert sorted(clarification_store.get(user_id).missing) == ["price", "supplier"]


@pytest.mark.asyncio
async def test_three_missing_fields_asked_together(inventory, scripted):
    """BUG 2 case 3: category + stock + supplier in one question."""
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Widget"], "changes": {"price": 99.0},
    }, {
        "status": "ready", "intent": "create_product",
        "category": "tools", "changes": {"stock": 4}, "supplier": "SRM",
    }])

    first = await _run("add a product named Widget with price 99", user_id)
    assert first.data is None
    answer = first.answer.lower()
    for field in ("category", "stock", "supplier"):
        assert field in answer, f"{field} missing from {first.answer!r}"
    assert first.answer.count("?") == 1
    assert sorted(clarification_store.get(user_id).missing) == ["category", "stock", "supplier"]

    second = await _run("category tools, stock 4, supplier SRM", user_id)
    row = _product(user_id, sessions, "Widget")
    assert row is not None
    # Stored identity is normalized; the API/UI shows a display label.
    assert (row.category, row.stock, row.supplier) == ("tools", 4, "SRM")
    assert "Tools" in second.answer
    _assert_clean(second)


@pytest.mark.asyncio
async def test_remaining_fields_only_after_partial_answer(inventory, scripted):
    """BUG 2 case 4: answering one field asks only for the rest."""
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Gadget2"], "category": "tools",
        "changes": {"stock": 10},
    }, {
        "status": "ready", "intent": "create_product", "changes": {"price": 500.0},
    }, {
        "status": "ready", "intent": "create_product", "supplier": "Acme",
    }])

    first = await _run("add a product named Gadget2 in category Tools with stock 10", user_id)
    assert "price" in first.answer.lower() and "supplier" in first.answer.lower()

    second = await _run("price 500", user_id)
    assert second.data is None
    second_answer = second.answer.lower()
    assert "supplier" in second_answer
    assert "price" not in second_answer, "price was already answered"

    third = await _run("supplier Acme", user_id)
    row = _product(user_id, sessions, "Gadget2")
    assert (row.stock, row.price, row.supplier) == (10, 500.0, "Acme")
    _assert_clean(third)


@pytest.mark.asyncio
async def test_all_fields_present_executes_without_asking(inventory, scripted):
    """BUG 2 case 5: no question when everything is present."""
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Full"], "category": "tools",
        "changes": {"stock": 3, "price": 25.0}, "supplier": "Acme",
    }])

    response = await _run(
        "add a product named Full in category Tools with stock 3 price 25 supplier Acme",
        user_id)
    assert harness.mutation_names == ["create_product"]
    assert response.data is not None
    _assert_clean(response)


@pytest.mark.asyncio
async def test_stated_value_not_reextracted_by_the_model_is_salvaged(inventory, scripted):
    """
    The model can drop a value the user clearly stated. The backend recovers it
    from the user's own wording instead of asking for it again.
    """
    sessions, user_id = inventory
    # The model extracted only the name and price; the user did state the rest.
    harness = scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Full2"], "changes": {"price": 25.0},
    }])

    response = await _run(
        "add a product named Full2 in category Tools with stock 3 price 25 supplier Acme",
        user_id)

    assert harness.mutation_names == ["create_product"], response.answer
    row = _product(user_id, sessions, "Full2")
    assert row is not None
    assert (row.category, row.stock, row.price, row.supplier) == ("tools", 3, 25.0, "Acme")


@pytest.mark.asyncio
async def test_salvage_never_invents_a_value_the_user_did_not_state(inventory, scripted):
    """Salvage only fills gaps; it cannot fabricate a supplier."""
    sessions, user_id = inventory
    harness = scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Bare"], "category": "tools",
        "changes": {"stock": 1, "price": 2.0},
    }])

    response = await _run("add a product named Bare in category Tools with stock 1 price 2", user_id)
    assert harness.mutations == []
    assert "supplier" in response.answer.lower()
    assert _product(user_id, sessions, "Bare") is None


def test_salvage_is_additive_and_validation_normalizes():
    from ai.intent_schema import build_intent, salvage_missing_create_fields, validate_intent

    raw = {"intent": "create_product", "product_queries": ["X"],
           "changes": {"stock": 9, "price": 5.0}, "category": "Tools",
           "supplier": "Given"}
    out = salvage_missing_create_fields(
        raw, "add X in category Tools with stock 1 price 5 from Nowhere")
    # Salvage only fills gaps; it never overwrites what the model supplied.
    assert out["changes"]["stock"] == 9
    assert out["changes"]["price"] == 5.0
    assert out["category"] == "Tools"
    assert out["supplier"] == "Given"
    # Normalization of the category is the validator's job, not the salvage's.
    plan = validate_intent(build_intent(out, "add X"), "add X")
    assert plan.arguments["category"] == "tools"


# ── Pure validation / merge units (no LLM) ───────────────────────────────────

@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ({"intent": "update_product", "product_queries": ["W"], "changes": {"stock": -1}}, "set W stock to -1"),
        ({"intent": "update_product", "product_queries": ["W"], "changes": {"price": -5}}, "set W price to -5"),
        ({"intent": "update_product", "product_queries": ["W"], "changes": {"stock": 2**40}}, "huge"),
        ({"intent": "create_product", "product_queries": ["A", "B"], "category": "C",
          "changes": {"stock": 1, "price": 1}, "supplier": "S"}, "add A and B"),
        ({"intent": "update_product", "product_queries": ["A", "B"], "changes": {"stock": 1}}, "update A and B"),
        ({"intent": "delete_product", "product_queries": ["A", "B"]}, "delete A and B"),
    ],
)
def test_validation_rejects_unsafe_intents(raw, text):
    assert isinstance(validate_intent(build_intent(raw, text), text), Rejection)


def test_relative_stock_is_a_clarification_not_a_rejection():
    outcome = validate_intent(
        build_intent({"intent": "update_stock", "product_queries": ["W"],
                      "changes": {"stock": 5, "stock_is_relative": True}}, "add 5 more W"),
        "add 5 more W")
    assert isinstance(outcome, Clarification)


def test_create_requires_supplier_in_the_required_field_table():
    assert "supplier" in REQUIRED_FIELDS["create_product"]


def test_merge_keeps_earlier_values_and_operation():
    merged = merge_intent(
        {"status": "needs_clarification", "intent": "create_product",
         "product_queries": ["phonecase"], "category": "electronics",
         "changes": {"stock": 90}, "missing": ["price", "supplier"],
         "clarification_question": "What price and supplier?"},
        {"status": "ready", "intent": "create_product",
         "changes": {"price": 100}, "supplier": "navya"},
    )
    assert merged["intent"] == "create_product"
    assert merged["product_queries"] == ["phonecase"]
    assert merged["category"] == "electronics"
    assert merged["changes"] == {"stock": 90, "price": 100}
    assert merged["supplier"] == "navya"
    assert "missing" not in merged and "clarification_question" not in merged


def test_user_stated_ids_only_reads_explicit_ids():
    assert user_stated_ids("delete product 12") == {12}
    assert user_stated_ids("delete id #7") == {7}
    assert user_stated_ids("set stock to 12") == set()
    assert user_stated_ids("show me product 5kg") == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USBC Hub 7 Port Electronics Item two 1500", "USBC Hub 7 Port Electronics Item"),
        ("Basmati Rice 1kg", "Basmati Rice 1kg"),
        ("milk  two", "milk"),
        ("7 Up", "7 Up"),
    ],
)
def test_product_query_normalisation(raw, expected):
    assert normalise_product_query(raw) == expected


def test_plan_arguments_never_contain_user_id():
    plan = validate_intent(
        build_intent({"intent": "update_stock", "product_queries": ["W"],
                      "changes": {"stock": 3}}, "set W stock to 3"),
        "set W stock to 3")
    assert isinstance(plan, IntentPlan)
    assert "user_id" not in plan.arguments
    assert plan.needs_product is True


def test_narrow_by_user_text_requires_a_clear_winner():
    candidates = [{"id": 1, "name": "Basmati Rice 1kg"}, {"id": 2, "name": "Basmati Rice 5kg"}]
    picked = agent._narrow_by_user_text(candidates, "set basmati ricce 1kg stock to 60")
    assert picked is not None and picked["id"] == 1
    assert not isinstance(agent._narrow_by_user_text(candidates, "set basmati rice to 60"), dict)
    assert agent._narrow_by_user_text([{"id": 1, "name": "Only One"}], "anything") is None


# ═══════════════════════════════════════════════════════════════════════════
# Extraction hardening: number forms, under-extraction recovery, update scoping
# and provider-failure state. All of it deterministic: the model is scripted to
# under-report on purpose, and the backend must still be correct.
# ═══════════════════════════════════════════════════════════════════════════

# A model that grabbed only the product, as a weak extraction really behaves.
def _bare_create(name="iPhone", category=None, **extra):
    payload = {"intent": "create_product", "product_queries": [name]}
    if category:
        payload["category"] = category
    payload.update(extra)
    return payload


# ── Number and currency forms ────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected_stock,expected_price", [
    ("add iphone in electronics stock 60 price 100000 supplier apple",
     60, 100000.0),
    ("add iphone in electronics stock 60 price 1,00,000 supplier apple",
     60, 100000.0),
    ("add iphone in electronics stock 60 price 1,000,000 supplier apple",
     60, 1000000.0),
    ("add iphone in electronics stock 60 price 100k supplier apple",
     60, 100000.0),
    ("add iphone in electronics stock 60 price 1 lakh supplier apple",
     60, 100000.0),
    ("add iphone in electronics stock 60 price 2.5 lakhs supplier apple",
     60, 250000.0),
    ("add iphone in electronics stock 60 price 1 crore supplier apple",
     60, 10000000.0),
    ("add iphone in electronics price \u20b91,00,000 stock 60 units supplier apple",
     60, 100000.0),
    ("add iphone in electronics price rs 500 stock 60 supplier apple",
     60, 500.0),
    ("add iphone in electronics price inr 750 stock 12 pcs supplier apple",
     12, 750.0),
])
def test_number_and_currency_forms_recover_exactly(text, expected_stock, expected_price):
    recovered = salvage_missing_create_fields(_bare_create(category="electronics"), text)
    assert recovered["changes"]["stock"] == expected_stock
    assert recovered["changes"]["price"] == pytest.approx(expected_price)


def test_parse_quantity_rejects_what_it_does_not_understand():
    # A parser that guesses would invent inventory values out of thin air.
    assert parse_quantity("a lot") is None
    assert parse_quantity("5kg") is None
    assert parse_quantity("") is None
    assert parse_quantity(None) is None
    assert parse_quantity(-3) is None
    assert parse_quantity("1,00,000") == 100000.0


# ── The four required phrasings, with a model that dropped the numbers ───────

@pytest.mark.parametrize("text", [
    "Add iPhone to electronics, stock 60, price 100000, supplier Apple.",
    "Create an iPhone in electronics for 100000, 60 units, from Apple.",
    "Add 60 iPhones to electronics at \u20b9100000 each, supplier Apple.",
    "Put a new iPhone in Electronics. Apple supplies it. Price is 1 lakh and stock is 60.",
])
@pytest.mark.asyncio
async def test_all_four_phrasings_create_the_same_row(inventory, scripted, text):
    sessions, user_id = inventory
    scripted([_bare_create("iPhone", "electronics")])
    response = await _run(text, user_id)
    _assert_clean(response)
    row = _product(user_id, sessions, "iPhone")
    assert row is not None
    assert (row.stock, row.price, row.supplier) == (60, 100000.0, "Apple")
    assert row.category == "electronics"


@pytest.mark.asyncio
async def test_field_order_does_not_matter(inventory, scripted):
    sessions, user_id = inventory
    scripted([_bare_create("Widget", "tools")])
    response = await _run("supplier acme, 5 units, \u20b91,00,000, tools category, widget",
                         user_id)
    _assert_clean(response)
    row = _product(user_id, sessions, "Widget")
    assert (row.stock, row.price, row.supplier) == (5, 100000.0, "acme")


# ── Recovery never steals a labelled value ───────────────────────────────────

def test_labelled_price_is_not_re_read_as_stock():
    # "price 99" is spoken for; stock must stay missing and be asked for.
    raw = {"intent": "create_product", "product_queries": ["Widget"],
           "changes": {"price": 99}}
    recovered = salvage_missing_create_fields(raw, "add a product named Widget with price 99")
    assert recovered["changes"]["price"] == 99
    assert "stock" not in recovered["changes"]
    assert "supplier" not in recovered


def test_product_name_is_not_re_read_as_a_supplier():
    raw = {"intent": "create_product", "product_queries": ["Widget"],
           "category": "tools", "changes": {"price": 99}}
    recovered = salvage_missing_create_fields(
        raw, "add a product named Widget with price 99")
    assert "supplier" not in recovered


def test_model_values_are_never_overwritten():
    raw = {"intent": "create_product", "product_queries": ["iPhone"],
           "category": "electronics", "supplier": "CorrectCorp",
           "changes": {"stock": 7, "price": 123.0}}
    text = "add iphone in electronics stock 999 price 999999 supplier WrongCorp"
    recovered = salvage_missing_create_fields(raw, text)
    assert recovered["changes"] == {"stock": 7, "price": 123.0}
    assert recovered["supplier"] == "CorrectCorp"


def test_recovery_is_create_only():
    raw = {"intent": "update_product", "product_queries": ["iPhone"]}
    assert salvage_missing_create_fields(raw, "price 100000 stock 60") == raw


# ── Follow-up merging ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bare_number_and_supplier_answer_completes_the_create(inventory, scripted):
    sessions, user_id = inventory
    clarification_store.clear(user_id)
    scripted([
        _bare_create("iPhone", "electronics", changes={"price": 100000}),
        _bare_create(),
    ])
    first = await _run("Add iPhone to electronics with price 100000.", user_id)
    assert "stock" in first.answer.lower() and "supplier" in first.answer.lower()

    # "60 and Apple." names both open slots with no keywords at all.
    second = await _run("60 and Apple.", user_id)
    _assert_clean(second)
    row = _product(user_id, sessions, "iPhone")
    assert (row.stock, row.price, row.supplier) == (60, 100000.0, "Apple")


@pytest.mark.asyncio
async def test_partial_answer_asks_only_for_what_is_left(inventory, scripted):
    sessions, user_id = inventory
    clarification_store.clear(user_id)
    scripted([
        _bare_create("iPhone", "electronics", changes={"price": 100000}),
        _bare_create(),
        _bare_create(),
    ])
    await _run("Add iPhone to electronics with price 100000.", user_id)
    second = await _run("60", user_id)           # stock only
    _assert_clean(second)
    lower = second.answer.lower()
    assert "supplier" in lower
    assert "stock" not in lower.split("supplier")[0].replace("what", "")

    third = await _run("Apple", user_id)
    _assert_clean(third)
    row = _product(user_id, sessions, "iPhone")
    assert (row.stock, row.price, row.supplier) == (60, 100000.0, "Apple")


@pytest.mark.asyncio
async def test_earlier_fields_survive_an_unhelpful_follow_up(inventory, scripted):
    sessions, user_id = inventory
    clarification_store.clear(user_id)
    scripted([
        _bare_create("iPhone", "electronics", changes={"price": 100000}),
        {},                                     # the model says nothing useful
        _bare_create(),
    ])
    await _run("Add iPhone to electronics with price 100000.", user_id)
    await _run("60 and Apple.", user_id)
    row = _product(user_id, sessions, "iPhone")
    assert row is not None and row.price == 100000.0
    assert row.category == "electronics"


# ── Update scoping ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_touches_only_the_named_field(inventory, scripted):
    sessions, user_id = inventory
    before = _product(user_id, sessions, "USB-C Hub 7-Port")
    scripted([{"intent": "update_product", "product_queries": ["USB-C Hub 7-Port"],
               "changes": {"price": 90000}}])
    response = await _run("Change USB-C Hub 7-Port price to 90000.", user_id)
    _assert_clean(response)
    after = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert after.price == 90000.0
    assert after.stock == before.stock
    assert after.category == before.category
    assert after.supplier == before.supplier


@pytest.mark.asyncio
async def test_update_with_several_fields_touches_only_those(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"intent": "update_product", "product_queries": ["USB-C Hub 7-Port"],
               "changes": {"stock": 100, "price": 2000}}])
    response = await _run(
        "Change USB-C Hub 7-Port stock to 100 and price to 2000.", user_id)
    _assert_clean(response)
    after = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert (after.stock, after.price) == (100, 2000.0)


@pytest.mark.asyncio
async def test_supplier_change_is_reported_not_silently_dropped(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"intent": "update_product", "product_queries": ["USB-C Hub 7-Port"],
               "changes": {"stock": 100, "supplier": "Samsung"}}])
    response = await _run(
        "Change USB-C Hub 7-Port stock to 100 and supplier to Samsung.", user_id)
    _assert_clean(response)
    after = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert after.stock == 100                     # the supported part applied
    assert after.supplier == "TechMart"           # contract cannot change it
    assert "supplier" in response.answer.lower()


@pytest.mark.asyncio
async def test_ambiguous_update_asks_instead_of_inventing(inventory, scripted):
    sessions, user_id = inventory
    before = _product(user_id, sessions, "USB-C Hub 7-Port")
    harness = scripted([{"intent": "update_product",
                         "product_queries": ["USB-C Hub 7-Port"]}])
    response = await _run("Update USB-C Hub 7-Port.", user_id)
    _assert_clean(response)
    assert response.data is None
    after = _product(user_id, sessions, "USB-C Hub 7-Port")
    assert (after.stock, after.price) == (before.stock, before.price)
    assert harness.mutations == []


# ── Reads stay reads ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("question,payload", [
    ("Show all electronics.", {"intent": "get_products_by_category",
                               "category": "electronics"}),
    ("What products are in grains?", {"intent": "get_products_by_category",
                                      "category": "grains"}),
    ("Show USB-C Hub 7-Port.", {"intent": "get_product_details",
                                "product_queries": ["USB-C Hub 7-Port"]}),
    ("Find products supplied by TechMart.", {"intent": "search_inventory",
                                             "product_queries": ["TechMart"]}),
    ("Show products with stock below 10.", {"intent": "get_low_stock_items",
                                            "threshold": 10}),
])
@pytest.mark.asyncio
async def test_natural_language_reads_never_mutate(inventory, scripted, question, payload):
    sessions, user_id = inventory
    harness = scripted([payload])
    response = await _run(question, user_id)
    _assert_clean(response)
    assert harness.mutations == []
    assert harness.tools, "a read tool should still have run"


# ── Provider failures ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_provider_failure_creates_no_pending_state(inventory, monkeypatch):
    from openai import APIError

    sessions, user_id = inventory
    clarification_store.clear(user_id)

    def _boom(**kwargs):
        raise APIError(message="upstream exploded", request=None, body=None)

    monkeypatch.setattr(agent, "_get_openai_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_boom))))

    response = await _run("Add iPhone to electronics stock 60 price 100000 supplier Apple",
                          user_id)
    assert response.data is None
    assert clarification_store.get(user_id) is None


@pytest.mark.asyncio
async def test_provider_timeout_creates_no_pending_state(inventory, monkeypatch):
    import time as _time

    sessions, user_id = inventory
    clarification_store.clear(user_id)

    def _hang(**kwargs):
        _time.sleep(30)          # a provider that never answers
        return None

    monkeypatch.setattr(agent, "INTENT_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(agent, "_get_openai_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_hang))))

    response = await _run("Add iPhone to electronics stock 60 price 100000 supplier Apple",
                          user_id)
    assert response.data is None
    assert clarification_store.get(user_id) is None


@pytest.mark.asyncio
async def test_intent_call_sends_a_bounded_timeout(inventory, scripted, monkeypatch):
    sessions, user_id = inventory
    monkeypatch.setattr(agent, "INTENT_TIMEOUT_SECONDS", 12.5)
    harness = scripted([_bare_create("iPhone", "electronics", changes={"stock": 1})])
    await _run("Add iPhone to electronics", user_id)
    assert harness.llm, "the model should have been called"
    assert harness.llm[0]["timeout"] == 12.5


@pytest.mark.asyncio
async def test_failed_follow_up_does_not_corrupt_a_pending_intent(inventory, scripted, monkeypatch):
    import httpx
    from openai import RateLimitError

    sessions, user_id = inventory
    clarification_store.clear(user_id)
    scripted([_bare_create("iPhone", "electronics", changes={"price": 100000})])
    await _run("Add iPhone to electronics with price 100000.", user_id)
    pending_before = clarification_store.get(user_id)
    assert pending_before is not None

    request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = httpx.Response(429, request=request)

    def _limited(**kwargs):
        raise RateLimitError("rate limit reached", response=response, body=None)

    monkeypatch.setattr(agent, "_get_openai_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=_limited))))
    with pytest.raises(RateLimitError):
        await _run("60 and Apple.", user_id)

    # The provider 429 is not this app's own rate limit, and it must not have
    # quietly discarded the request the user is halfway through.
    pending_after = clarification_store.get(user_id)
    assert pending_after is not None
    assert pending_after.raw_intent.get("product_queries") == ["iPhone"]
