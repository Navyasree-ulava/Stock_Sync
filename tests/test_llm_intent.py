"""
tests/test_llm_intent.py — LLM structured-intent pipeline regression tests.

The model is stubbed (no network in CI); everything after extraction is real:
deterministic validation, the clarification store, and the actual MCP tool
functions against a database.
"""

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
    Clarification,
    IntentPlan,
    Rejection,
    build_intent,
    normalise_product_query,
    user_stated_ids,
    validate_intent,
)
from db.models import Product, StockAuditLog, User

RICE_1KG = {"name": "Basmati Rice 1kg", "category": "Grains", "stock": 20, "price": 120.0, "supplier": "AgroSupply"}
RICE_5KG = {"name": "Basmati Rice 5kg", "category": "Grains", "stock": 5, "price": 550.0, "supplier": "AgroSupply"}
HUB = {"name": "USB-C Hub 7-Port", "category": "Electronics", "stock": 10, "price": 1499.0, "supplier": "TechMart"}
MILK = {"name": "Full Cream Milk 1L", "category": "Dairy", "stock": 40, "price": 68.5, "supplier": "DairyFresh"}


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


def _fake_llm(payloads):
    """
    Replace the model call with a scripted sequence of submit_intent payloads.

    Each payload is what the model "returned". A payload of None simulates the
    model failing to produce a tool call at all.
    """
    queue = list(payloads)
    calls = []

    def _completions_create(**kwargs):
        calls.append(kwargs)
        payload = queue.pop(0) if queue else {}
        message = SimpleNamespace(content=None, tool_calls=None)
        if payload is not None:
            message.tool_calls = [
                SimpleNamespace(
                    id="call_1",
                    type="function",
                    function=SimpleNamespace(
                        name="submit_intent",
                        arguments=_dumps(payload),
                    ),
                )
            ]
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=_completions_create)
        )
    )
    return client, calls


def _dumps(payload):
    import json
    return json.dumps(payload)


@pytest.fixture
def scripted(monkeypatch):
    """Install a scripted model and an MCP bridge that hits the real tools."""

    def _install(payloads):
        client, calls = _fake_llm(payloads)
        monkeypatch.setattr(agent, "_get_openai_client", lambda: client)
        monkeypatch.setattr(agent, "clarification_store", clarification_store)

        async def _call_tool(name, arguments):
            arguments = dict(arguments)
            func = getattr(mcp_server, name)
            return func(**arguments)

        monkeypatch.setattr(agent.mcp_manager, "call_tool", _call_tool)
        return calls

    return _install


def _tools():
    return [{"type": "function", "function": {"name": n}} for n in (
        "query_inventory_db", "get_product_details", "create_product", "search_inventory",
        "get_low_stock_items", "get_all_categories", "get_products_by_category",
        "get_products_by_names", "get_inventory_analytics", "get_category_analytics",
        "update_stock", "update_product", "delete_product",
    )]


async def _run(question, user_id):
    return await agent._run_llm_intent(question, user_id, _tools())


def _rows(user_id, sessions, name):
    db = sessions()
    rows = [p for p in db.query(Product).filter(Product.user_id == user_id).all() if p.name == name]
    db.close()
    return rows


# ── The example from the specification ───────────────────────────────────────

@pytest.mark.asyncio
async def test_spelled_out_number_is_understood_and_price_applied(inventory, scripted):
    """
    'Update the price of the USBC Hub 7 Port Electronics Item two 1500'
    must set price 1500, not be rejected and not be read as a stock change.
    """
    sessions, user_id = inventory
    scripted([{
        "status": "ready",
        "intent": "update_product",
        "product_queries": ["USBC Hub 7 Port Electronics Item two 1500"],
        "changes": {"price": 1500},
    }])

    response = await _run(
        "Update the price of the USBC Hub 7 Port Electronics Item two 1500", user_id
    )

    assert response.tool_used == "update_product"
    assert response.data[0]["success"] is True
    assert "₹1,500.00" in response.answer
    # The number must not leak into the stock field.
    assert response.data[0]["stock"] == 10
    rows = _rows(user_id, sessions, "USB-C Hub 7-Port")
    assert len(rows) == 1 and rows[0].price == 1500.0


# ── Varied natural language ───────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("payload", "expected_tool"),
    [
        ({"status": "ready", "intent": "update_stock", "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 20}}, "update_stock"),
        ({"status": "ready", "intent": "update_product", "product_queries": ["USB-C Hub 7-Port"], "changes": {"price": 250.5}}, "update_product"),
        ({"status": "ready", "intent": "update_product", "product_queries": ["USB-C Hub 7-Port"], "changes": {"category": "Gadgets"}}, "update_product"),
        ({"status": "ready", "intent": "update_product", "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 3, "price": 99.0}}, "update_product"),
        ({"status": "ready", "intent": "get_products_by_category", "category": "Grains"}, "get_products_by_category"),
        ({"status": "ready", "intent": "search_inventory", "product_queries": ["basmati"]}, "search_inventory"),
        ({"status": "ready", "intent": "get_low_stock_items", "threshold": 6}, "get_low_stock_items"),
        ({"status": "ready", "intent": "get_all_categories"}, "get_all_categories"),
        ({"status": "ready", "intent": "get_inventory_analytics"}, "get_inventory_analytics"),
        ({"status": "ready", "intent": "get_category_analytics"}, "get_category_analytics"),
        ({"status": "ready", "intent": "get_product_details", "product_queries": ["Full Cream Milk 1L"]}, "get_product_details"),
        ({"status": "ready", "intent": "query_inventory_db", "product_queries": ["Basmati Rice 1kg"]}, "query_inventory_db"),
        ({"status": "ready", "intent": "get_products_by_names", "names": ["Basmati Rice 1kg", "Full Cream Milk 1L"]}, "get_products_by_names"),
    ],
)
@pytest.mark.asyncio
async def test_varied_intents_map_to_the_right_tool(inventory, scripted, payload, expected_tool):
    sessions, user_id = inventory
    scripted([payload])
    response = await _run("please do the thing", user_id)
    assert response.tool_used == expected_tool
    assert not (response.data and response.data[0].get("error")), response.data


@pytest.mark.asyncio
async def test_analytics_answer_uses_committed_totals(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "get_inventory_analytics"}])
    response = await _run("give me analytics", user_id)
    stats = response.data[0]
    assert stats["total_products"] == 4
    assert stats["most_expensive"] == "USB-C Hub 7-Port"
    # Every figure in the prose must come from the committed tool result.
    assert str(stats["total_products"]) in response.answer
    assert _fmt(stats["total_inventory_value"]) in response.answer
    assert "₹22,880.00" in response.answer
    assert "Unnamed product" not in response.answer


def _fmt(value):
    return f"₹{float(value):,.2f}"


@pytest.mark.asyncio
async def test_category_analytics_renders_each_category(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "get_category_analytics"}])
    response = await _run("breakdown by category", user_id)
    assert "Grains" in response.answer and "Dairy" in response.answer
    assert "Unnamed product" not in response.answer


# ── Incomplete requests and clarification/resume ──────────────────────────────

@pytest.mark.asyncio
async def test_missing_price_asks_a_question_and_changes_nothing(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "needs_clarification",
        "intent": "update_product",
        "product_queries": ["USB-C Hub 7-Port"],
        "changes": {},
        "missing": ["price"],
        "clarification_question": "What price should I set for the USB Hub?",
    }])

    response = await _run("Update the price of the USB Hub", user_id)

    assert response.answer == "What price should I set for the USB Hub?"
    assert response.data[0]["needs_clarification"] is True
    assert response.tool_used is None, "nothing may be executed while waiting"
    rows = _rows(user_id, sessions, "USB-C Hub 7-Port")
    assert rows[0].price == 1499.0, "price must be untouched"


@pytest.mark.asyncio
async def test_clarification_answer_completes_the_original_request(inventory, scripted):
    """Turn 1 asks, turn 2 answers with one word; the original intent completes."""
    sessions, user_id = inventory
    calls = scripted([
        {
            "status": "needs_clarification",
            "intent": "update_product",
            "product_queries": ["USB-C Hub 7-Port"],
            "changes": {},
            "missing": ["price"],
            "clarification_question": "What price should I set for the USB Hub?",
        },
        {
            "status": "ready",
            "intent": "update_product",
            "product_queries": ["USB-C Hub 7-Port"],
            "changes": {"price": 1750},
        },
    ])

    first = await _run("Update the price of the USB Hub", user_id)
    assert first.data[0]["needs_clarification"] is True
    assert clarification_store.get(user_id) is not None

    second = await _run("1750", user_id)

    assert second.tool_used == "update_product"
    assert "₹1,750.00" in second.answer
    rows = _rows(user_id, sessions, "USB-C Hub 7-Port")
    assert rows[0].price == 1750.0
    # The second call must carry the original request, not just "1750".
    second_messages = calls[1]["messages"]
    assert second_messages[-1]["content"] == "1750"
    conversation = " ".join(m["content"] for m in second_messages)
    assert "Update the price of the USB Hub" in conversation
    assert "What price should I set for the USB Hub?" in conversation
    assert "PENDING REQUEST CONTEXT" in second_messages[0]["content"]
    assert clarification_store.get(user_id) is None


@pytest.mark.asyncio
async def test_clarification_gives_up_after_repeated_failures(inventory, scripted, monkeypatch):
    """A user who never supplies the value must not loop forever."""
    sessions, user_id = inventory
    monkeypatch.setattr(agent, "MAX_CLARIFICATIONS", 2)
    scripted([
        {"status": "needs_clarification", "intent": "update_product",
         "product_queries": ["USB-C Hub 7-Port"], "changes": {}, "missing": ["price"],
         "clarification_question": "What price?"},
        {"status": "needs_clarification", "intent": "update_product",
         "product_queries": ["USB-C Hub 7-Port"], "changes": {}, "missing": ["price"],
         "clarification_question": "What price?"},
        {"status": "needs_clarification", "intent": "update_product",
         "product_queries": ["USB-C Hub 7-Port"], "changes": {}, "missing": ["price"],
         "clarification_question": "What price?"},
    ])

    await _run("set the price of the hub", user_id)
    await _run("hmm", user_id)
    third = await _run("still nothing", user_id)

    assert "restate" in third.answer.lower()
    assert third.data[0]["error"]
    assert clarification_store.get(user_id) is None


# ── Ambiguity ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ambiguous_delete_asks_which_product(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "delete_product",
               "product_queries": ["basmati rice"]}])

    response = await _run("Delete the basmati rice", user_id)

    assert "Which one" in response.answer
    assert "Basmati Rice 1kg" in response.answer and "Basmati Rice 5kg" in response.answer
    assert len(_rows(user_id, sessions, "Basmati Rice 1kg")) == 1
    assert len(_rows(user_id, sessions, "Basmati Rice 5kg")) == 1, "nothing may be deleted"


@pytest.mark.asyncio
async def test_ambiguity_resolved_by_the_next_answer(inventory, scripted):
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "delete_product", "product_queries": ["basmati rice"]},
        # The model must pick the offered candidate, not reword the name.
        {"status": "ready", "intent": "delete_product",
         "product_queries": ["basmati rice"], "select_candidate": "Basmati Rice 5kg"},
    ])

    first = await _run("delete the basmati rice", user_id)
    assert "Which one" in first.answer
    pending = clarification_store.get(user_id)
    assert pending is not None and len(pending.candidates) == 2

    second = await _run("the 5kg one", user_id)

    assert second.tool_used == "delete_product"
    assert "Deleted 'Basmati Rice 5kg'" in second.answer
    assert len(_rows(user_id, sessions, "Basmati Rice 5kg")) == 0
    assert len(_rows(user_id, sessions, "Basmati Rice 1kg")) == 1, "only the named product"


@pytest.mark.asyncio
async def test_candidate_outside_the_offered_list_is_refused(inventory, scripted):
    """select_candidate may only name a row the backend actually offered."""
    sessions, user_id = inventory
    scripted([
        {"status": "ready", "intent": "delete_product", "product_queries": ["basmati rice"]},
        {"status": "ready", "intent": "delete_product",
         "product_queries": ["basmati rice"], "select_candidate": "Some Other Product"},
    ])

    await _run("delete the basmati rice", user_id)
    second = await _run("some other product", user_id)

    assert second.data[0]["error"]
    assert "could not tell which" in second.data[0]["error"]
    assert len(_rows(user_id, sessions, "Basmati Rice 1kg")) == 1
    assert len(_rows(user_id, sessions, "Basmati Rice 5kg")) == 1


@pytest.mark.asyncio
async def test_size_token_in_user_text_resolves_ambiguity(inventory, scripted):
    """
    The model may say 'basmati rice' for a request that said 'basmati ricce 1kg'.
    The user's own size token must pick the 1kg row without guessing.
    """
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["basmati rice"], "changes": {"stock": 60}}])

    response = await _run(
        "update the Quantity of basmati ricce 1kg in category grains to 60", user_id
    )

    assert response.tool_used == "update_stock"
    assert response.data[0]["name"] == "Basmati Rice 1kg"
    assert response.data[0]["stock"] == 60
    assert _rows(user_id, sessions, "Basmati Rice 5kg")[0].stock == 5, "other row untouched"


@pytest.mark.asyncio
async def test_true_ambiguity_still_asks_instead_of_guessing(inventory, scripted):
    """When the user's text does not separate the rows, a question is still asked."""
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["basmati rice"], "changes": {"stock": 77}}])

    response = await _run("update the Quantity of basmati ricce to 77", user_id)

    assert response.data[0]["needs_clarification"] is True
    assert _rows(user_id, sessions, "Basmati Rice 1kg")[0].stock == 20
    assert _rows(user_id, sessions, "Basmati Rice 5kg")[0].stock == 5


def test_narrow_by_user_text_requires_a_clear_winner():
    candidates = [
        {"id": 1, "name": "Basmati Rice 1kg"},
        {"id": 2, "name": "Basmati Rice 5kg"},
    ]
    picked = agent._narrow_by_user_text(candidates, "set basmati ricce 1kg stock to 60")
    assert picked is not None and picked["id"] == 1
    # Neither size mentioned -> no winner, must ask.
    assert not isinstance(agent._narrow_by_user_text(candidates, "set basmati rice to 60"), dict)


def test_narrow_by_user_text_ignores_non_candidates():
    assert agent._narrow_by_user_text([{"id": 1, "name": "Only One"}], "anything") is None


# ── Create vs restock ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_product_uses_committed_row(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Buttermilk 1L"], "category": "Dairy",
        "changes": {"stock": 25, "price": 45}, "supplier": "Amul",
    }])

    response = await _run("add Buttermilk 1L in Dairy, 25 in stock at 45 rupees from Amul", user_id)

    assert response.tool_used == "create_product"
    assert response.data[0]["success"] is True
    rows = _rows(user_id, sessions, "Buttermilk 1L")
    assert len(rows) == 1
    assert rows[0].stock == 25 and rows[0].price == 45.0 and rows[0].supplier == "Amul"


@pytest.mark.asyncio
async def test_restock_is_an_update_not_a_create(inventory, scripted):
    """'add 5 more Widgets' must not create a product named '5 more Widgets'."""
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "update_stock",
        "product_queries": ["USB-C Hub 7-Port"], "changes": {"stock": 15},
    }])

    response = await _run("add 5 more USB-C Hub 7-Port", user_id)

    assert response.tool_used == "update_stock"
    assert response.data[0]["stock"] == 15
    assert len(_rows(user_id, sessions, "5 more USB-C Hub 7-Port")) == 0


@pytest.mark.asyncio
async def test_relative_change_asks_for_the_absolute_level(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "update_stock",
        "product_queries": ["Full Cream Milk 1L"],
        "changes": {"stock": 5, "stock_is_relative": True},
    }])

    response = await _run("add 5 more milk", user_id)

    assert response.data[0]["needs_clarification"] is True
    assert "new stock level" in response.answer
    rows = _rows(user_id, sessions, "Full Cream Milk 1L")
    assert rows[0].stock == 40, "a relative change must never be applied as absolute"


@pytest.mark.asyncio
async def test_create_missing_fields_asks_for_them(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "create_product",
        "product_queries": ["Buttermilk 1L"], "category": "Dairy",
        "changes": {"stock": 25},
    }])

    response = await _run("add Buttermilk 1L in Dairy with 25 in stock", user_id)

    assert response.data[0]["needs_clarification"] is True
    assert "price" in response.answer.lower()
    assert len(_rows(user_id, sessions, "Buttermilk 1L")) == 0


# ── Multiple targets ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multiple_mutation_targets_are_refused_not_partially_applied(inventory, scripted):
    sessions, user_id = inventory
    scripted([{
        "status": "ready", "intent": "update_stock",
        "product_queries": ["Basmati Rice 1kg", "Full Cream Milk 1L"],
        "changes": {"stock": 99},
    }])

    response = await _run("set the stock of basmati rice 1kg and milk to 99", user_id)

    assert response.data[0]["error"]
    assert "one product at a time" in response.data[0]["error"].lower()
    assert _rows(user_id, sessions, "Basmati Rice 1kg")[0].stock == 20
    assert _rows(user_id, sessions, "Full Cream Milk 1L")[0].stock == 40


# ── Destructive operations ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_removes_exactly_one_row_and_logs_it(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "delete_product",
               "product_queries": ["Full Cream Milk 1L"]}])

    response = await _run("delete the full cream milk", user_id)

    assert response.tool_used == "delete_product"
    assert response.data[0]["success"] is True
    assert len(_rows(user_id, sessions, "Full Cream Milk 1L")) == 0
    assert len(_rows(user_id, sessions, "Basmati Rice 1kg")) == 1


@pytest.mark.asyncio
async def test_delete_unknown_product_never_claims_success(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "delete_product",
               "product_queries": ["Nonexistent Product 99"]}])

    response = await _run("delete Nonexistent Product 99", user_id)

    assert response.tool_used is None
    assert "No product" in response.answer
    assert response.data[0]["error"]


@pytest.mark.asyncio
async def test_delete_by_user_stated_id_is_allowed(inventory, scripted):
    sessions, user_id = inventory
    db = sessions()
    milk = db.query(Product).filter(Product.user_id == user_id, Product.name == "Full Cream Milk 1L").one()
    milk_id = milk.id
    db.close()

    scripted([{"status": "ready", "intent": "delete_product", "product_id": milk_id}])

    response = await _run(f"delete product {milk_id}", user_id)

    assert response.tool_used == "delete_product"
    assert len(_rows(user_id, sessions, "Full Cream Milk 1L")) == 0


# ── Model-supplied values the backend must refuse ────────────────────────────

@pytest.mark.asyncio
async def test_model_cannot_invent_a_product_id(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "update_stock",
               "product_id": 424242, "changes": {"stock": 1}}])

    response = await _run("make it stock 1", user_id)

    assert response.data[0]["error"]
    assert "only use a product ID that you provided" in response.data[0]["error"]
    assert _rows(user_id, sessions, "Basmati Rice 1kg")[0].stock == 20


@pytest.mark.asyncio
async def test_unknown_intent_is_refused(inventory, scripted):
    sessions, user_id = inventory
    scripted([{"status": "ready", "intent": "drop_database", "product_queries": ["x"]}])
    response = await _run("drop everything", user_id)
    assert response.data[0]["error"]
    assert response.tool_used is None


@pytest.mark.asyncio
async def test_malformed_model_output_is_refused_safely(inventory, scripted):
    sessions, user_id = inventory
    scripted([None])
    response = await _run("do something strange", user_id)
    assert response.data[0]["error"]
    assert response.tool_used is None


@pytest.mark.asyncio
async def test_model_never_supplies_user_id(inventory, scripted):
    sessions, user_id = inventory
    calls = scripted([{
        "status": "ready", "intent": "update_stock",
        "product_queries": ["USB-C Hub 7-Port"],
        "changes": {"stock": 12},
        "user_id": 999999,
    }])

    response = await _run("set usb-c hub stock to 12", user_id)

    # The injected id must win and must be the authenticated user.
    rows = _rows(user_id, sessions, "USB-C Hub 7-Port")
    assert rows[0].stock == 12
    other = sessions()
    leaked = other.query(Product).filter(Product.user_id == 999999).count()
    other.close()
    assert leaked == 0
    # The model must not see a user_id field in the tool it is offered.
    assert "user_id" not in str(agent.INTENT_TOOL)


# ── Tenant isolation through the new path ─────────────────────────────────────

@pytest.mark.asyncio
async def test_intent_path_cannot_touch_another_tenant(inventory, db_engine, monkeypatch, scripted):
    sessions_a, user_a = inventory
    sessions_b = sessions_a
    db = sessions_b()
    intruder = User(full_name="Intruder", email="intruder@example.com", hashed_password="x")
    db.add(intruder)
    db.commit()
    user_b = intruder.id
    db.close()

    scripted([{"status": "ready", "intent": "update_stock",
               "product_queries": ["Basmati Rice 1kg"], "changes": {"stock": 777}}])

    response = await _run("set basmati rice 1kg stock to 777", user_b)

    assert "No product" in response.answer
    assert _rows(user_a, sessions_a, "Basmati Rice 1kg")[0].stock == 20, "tenant A untouched"
    assert _rows(user_b, sessions_b, "Basmati Rice 1kg") == []


# ── Deterministic validation unit tests (no LLM involved) ─────────────────────

@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ({"intent": "update_product", "product_queries": ["Widget"], "changes": {"stock": -1}}, "set widget stock to -1"),
        ({"intent": "update_product", "product_queries": ["Widget"], "changes": {"price": -5}}, "set widget price to -5"),
        ({"intent": "update_product", "product_queries": ["Widget"], "changes": {"stock": 2**40}}, "set widget stock huge"),
        ({"intent": "create_product", "product_queries": ["A", "B"], "category": "C", "changes": {"stock": 1, "price": 1}}, "add A and B"),
        ({"intent": "update_product", "product_queries": ["A", "B"], "changes": {"stock": 1}}, "update A and B"),
        ({"intent": "delete_product", "product_queries": ["A", "B"]}, "delete A and B"),
    ],
)
def test_validation_rejects_unsafe_intents(raw, text):
    outcome = validate_intent(build_intent(raw, text), text)
    assert isinstance(outcome, Rejection), outcome


def test_relative_stock_is_a_clarification_not_a_rejection():
    outcome = validate_intent(
        build_intent({"intent": "update_stock", "product_queries": ["W"],
                      "changes": {"stock": 5, "stock_is_relative": True}}, "add 5 more W"),
        "add 5 more W",
    )
    assert isinstance(outcome, Clarification)


def test_user_stated_ids_only_reads_explicit_ids():
    assert user_stated_ids("delete product 12") == {12}
    assert user_stated_ids("delete id #7") == {7}
    assert user_stated_ids("delete product id 5") == {5}
    assert user_stated_ids("set stock to 12") == set()
    # A size-like number inside a name is not an id.
    assert user_stated_ids("show me product 5kg") == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("USBC Hub 7 Port Electronics Item two 1500", "USBC Hub 7 Port Electronics Item"),
        ("Basmati Rice 1kg", "Basmati Rice 1kg"),
        ("milk  two", "milk"),
        ("7 Up", "7 Up"),
        ("Widget price to 500", "Widget price to 500"),
    ],
)
def test_product_query_normalisation(raw, expected):
    assert normalise_product_query(raw) == expected


def test_plan_arguments_never_contain_user_id():
    plan = validate_intent(
        build_intent({"intent": "update_stock", "product_queries": ["W"],
                      "changes": {"stock": 3}}, "set W stock to 3"),
        "set W stock to 3",
    )
    assert isinstance(plan, IntentPlan)
    assert "user_id" not in plan.arguments
    assert plan.needs_product is True
