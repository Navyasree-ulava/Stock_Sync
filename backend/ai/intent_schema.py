"""
ai/intent_schema.py — Typed structured intent, deterministic validation, and MCP planning.

The LLM's only job is to turn free-form language into `InventoryIntent`.
Everything after that is deterministic Python:

  * unknown / malformed intents are rejected, never forwarded to a tool
  * required fields are checked and turned into a *question*, not an error
  * numeric ranges are enforced here, before any database call
  * the LLM never supplies `user_id` and never supplies a product id that it
    did not read out of the user's own message

The plan produced here names existing MCP tools and their arguments; no new
tool, table, or column is introduced.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# products.stock is a 4-byte integer column; keep in step with mcp_server.
MAX_STOCK = 2_147_483_647
MAX_LIMIT = 10_000
MAX_TARGETS = 20

# The 13 MCP capabilities this layer may target.
INTENT_QUERY_INVENTORY = "query_inventory_db"
INTENT_GET_DETAILS = "get_product_details"
INTENT_CREATE = "create_product"
INTENT_SEARCH = "search_inventory"
INTENT_LOW_STOCK = "get_low_stock_items"
INTENT_ALL_CATEGORIES = "get_all_categories"
INTENT_BY_CATEGORY = "get_products_by_category"
INTENT_BY_NAMES = "get_products_by_names"
INTENT_ANALYTICS = "get_inventory_analytics"
INTENT_CATEGORY_ANALYTICS = "get_category_analytics"
INTENT_UPDATE_STOCK = "update_stock"
INTENT_UPDATE_PRODUCT = "update_product"
INTENT_DELETE = "delete_product"

KNOWN_INTENTS = frozenset({
    INTENT_QUERY_INVENTORY,
    INTENT_GET_DETAILS,
    INTENT_CREATE,
    INTENT_SEARCH,
    INTENT_LOW_STOCK,
    INTENT_ALL_CATEGORIES,
    INTENT_BY_CATEGORY,
    INTENT_BY_NAMES,
    INTENT_ANALYTICS,
    INTENT_CATEGORY_ANALYTICS,
    INTENT_UPDATE_STOCK,
    INTENT_UPDATE_PRODUCT,
    INTENT_DELETE,
})

# Intents that must resolve to exactly one existing product before acting.
SINGLE_PRODUCT_INTENTS = frozenset({
    INTENT_GET_DETAILS,
    INTENT_UPDATE_STOCK,
    INTENT_UPDATE_PRODUCT,
    INTENT_DELETE,
})

MUTATING_INTENTS = frozenset({
    INTENT_CREATE,
    INTENT_UPDATE_STOCK,
    INTENT_UPDATE_PRODUCT,
    INTENT_DELETE,
})

DESTRUCTIVE_INTENTS = frozenset({INTENT_DELETE})

SORT_OPTIONS = ("price_asc", "price_desc", "stock_asc", "stock_desc")

# Common phrasings the model may emit for the same capability. Anything not
# listed here is rejected rather than guessed at.
INTENT_ALIASES = {
    "get_product": INTENT_GET_DETAILS,
    "product_details": INTENT_GET_DETAILS,
    "update": INTENT_UPDATE_PRODUCT,
    "add_product": INTENT_CREATE,
    "list_products": INTENT_SEARCH,
    "list_all_products": INTENT_SEARCH,
    "low_stock": INTENT_LOW_STOCK,
    "categories": INTENT_ALL_CATEGORIES,
    "analytics": INTENT_ANALYTICS,
    "delete_product": INTENT_DELETE,
    "restock": INTENT_UPDATE_STOCK,
}


# ── Typed payload the LLM must produce ────────────────────────────────────────

class ProductChanges(BaseModel):
    """Field-level changes. Only what the MCP update tools can actually apply."""

    stock: Optional[int] = None
    price: Optional[float] = None
    category: Optional[str] = None
    # True when the user asked for a relative change ("add 5 more"). The
    # backend refuses to guess the resulting absolute level.
    stock_is_relative: bool = False


class InventoryIntent(BaseModel):
    """Strict structured intent. `product_queries` is human-readable text only."""

    status: Literal["ready", "needs_clarification"] = "ready"
    intent: str = ""
    product_queries: list[str] = Field(default_factory=list)
    changes: ProductChanges = Field(default_factory=ProductChanges)
    category: Optional[str] = None
    names: list[str] = Field(default_factory=list)
    supplier: Optional[str] = None
    threshold: Optional[int] = None
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    stock_threshold: Optional[int] = None
    sort_by: Optional[str] = None
    limit: Optional[int] = None
    offset: Optional[int] = None
    # Only honoured when the same digits appear in the user's own message.
    product_id: Optional[int] = None
    # When candidate products were supplied and the user's answer picks one of
    # them, copy that product's name here exactly as given.
    select_candidate: Optional[str] = None
    missing: list[str] = Field(default_factory=list)
    clarification_question: Optional[str] = None


# The single function the model is allowed to call.
INTENT_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_intent",
        "description": (
            "Submit the structured inventory intent for the user's request, or ask a "
            "single concise follow-up question when required information is missing."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["ready", "needs_clarification"],
                    "description": "ready when the intent can be executed now.",
                },
                "intent": {
                    "type": "string",
                    "enum": sorted(KNOWN_INTENTS),
                    "description": "The single MCP capability that satisfies the request.",
                },
                "product_queries": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Human-readable product names exactly as the user described "
                        "them, with any numbers that belong to a change removed. "
                        "Never a product id. Use several entries only when the user "
                        "clearly asked for several products."
                    ),
                },
                "changes": {
                    "type": "object",
                    "properties": {
                        "stock": {"type": "integer"},
                        "price": {"type": "number"},
                        "category": {"type": "string"},
                        "stock_is_relative": {
                            "type": "boolean",
                            "description": "True for 'add 5 more' style relative changes.",
                        },
                    },
                    "required": [],
                },
                "category": {"type": "string", "description": "Category filter or value."},
                "names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "For get_products_by_names only.",
                },
                "supplier": {"type": "string", "description": "Optional supplier for a new product."},
                "threshold": {"type": "integer", "description": "Low-stock threshold."},
                "min_price": {"type": "number"},
                "max_price": {"type": "number"},
                "stock_threshold": {"type": "integer"},
                "sort_by": {"type": "string", "enum": list(SORT_OPTIONS)},
                "limit": {"type": "integer"},
                "offset": {"type": "integer"},
                "product_id": {
                    "type": "integer",
                    "description": "Only if the user themselves quoted this id.",
                },
                "select_candidate": {
                    "type": "string",
                    "description": (
                        "If candidate products were listed for you and the user's "
                        "answer picks exactly one of them, copy that product's name "
                        "here exactly as it was given. Leave empty otherwise."
                    ),
                },
                "missing": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field names still required when status is needs_clarification.",
                },
                "clarification_question": {
                    "type": "string",
                    "description": "One short question, only when status is needs_clarification.",
                },
            },
            "required": ["status", "intent"],
        },
    },
}


SYSTEM_PROMPT_INTENT = """You are the intent layer of StockSync, an inventory assistant.

You convert the user's message into ONE structured inventory intent, or ask ONE short
follow-up question when information required to act is missing.

ABSOLUTE RULES
- You never write SQL. You only choose an intent and supply its arguments.
- You never invent a product id, a database value, a row count, or a user id.
  product_queries must contain the user's own words for the product, with any number
  that belongs to a change removed.
- product_id may be set only when the user themselves wrote that id.
- If the user asks to change a field the update tools cannot change (product name or
  supplier), choose update_product and leave changes empty; the backend will say so.
- If the user asks for a relative change ("add 5 more", "increase by 5"), set
  changes.stock_is_relative=true and put the amount in changes.stock. The backend
  will ask for the absolute level instead of guessing.
- Never invent values. If a value needed to act is absent, set
  status="needs_clarification", list it in "missing", and ask one short question.

INTENT SELECTION
- create_product: a brand new product is being added.
- update_stock: only the stock level changes to an absolute number.
- update_product: price and/or category change, or stock together with either.
- delete_product: a product is being removed. Never use it for a whole category
  or for "everything".
- query_inventory_db: a single lookup of one product the user names, when they
  are checking whether it exists or what its record says.
- search_inventory: the user is searching, finding, listing or filtering and may
  get several rows ("search for basmati", "find rice", "show me all products",
  "list products under 100"). Use this whenever the request is a search or a
  listing, not a single-product lookup.
- get_product_details: full record for one named product.
- get_low_stock_items: products at or below a stock level.
- get_all_categories: the list of category names.
- get_products_by_category: the user wants every product in ONE named category.
  Prefer this over search_inventory for "what is in the X category",
  "show products in X", "list the X category".
- get_products_by_names: products matching an explicit list of names.
- get_inventory_analytics: totals, average price, most/least expensive.
- get_category_analytics: a per-category breakdown.

ANSWERING A CLARIFICATION
- If candidate products were listed for you and the user's reply clearly picks one
  of them (for example "the 5kg one", "the second one", "the cheaper one"), set
  select_candidate to that candidate's name copied exactly, and keep the rest of
  the original intent. Do not reword the product name.
- Otherwise combine the reply with the original request yourself.

CLARIFICATION
- Ask only for what is strictly required, one short question, and keep every other
  value you already understood.
- Example: "Update the price of the USB Hub" -> status="needs_clarification",
  intent="update_product", product_queries=["USB Hub"], changes={},
  missing=["price"], clarification_question="What price should I set for the USB Hub?"
- Example: "Delete the hub" with several hubs -> status="needs_clarification",
  intent="delete_product", product_queries=["hub"],
  clarification_question="Which hub do you want to delete?"
- If the user is in the middle of answering an earlier question, combine that answer
  with the earlier context and submit the completed intent.
"""


# ── Outcomes ──────────────────────────────────────────────────────────────────

@dataclass
class Clarification:
    """A question to ask the user. Nothing was executed."""

    question: str
    missing: list[str] = field(default_factory=list)


@dataclass
class Rejection:
    """Deterministic refusal. Nothing was executed."""

    message: str


@dataclass
class IntentPlan:
    """A validated, executable intent. No database values are present yet."""

    intent: str
    kind: str                      # read | create | update | delete | analytics
    tool_name: str                 # primary MCP tool
    arguments: dict                # tool args WITHOUT user_id and WITHOUT product_id
    targets: list[str]             # product_queries, possibly empty
    needs_product: bool = False    # resolve product_query -> real id first
    is_destructive: bool = False


IntentOutcome = IntentPlan | Clarification | Rejection


# ── Helpers ──────────────────────────────────────────────────────────────────

_USER_STATED_ID = re.compile(
    r"(?:\bproduct|\bid)\s*(?:id\s*|#\s*)?(\d+)\b", flags=re.IGNORECASE
)


def user_stated_ids(text: str) -> set[int]:
    """Product ids the user themselves wrote. Nothing else may set product_id."""
    return {int(m) for m in _USER_STATED_ID.findall(text or "")}


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n,.;:!?\"'")
    return cleaned or None


def _clean_question(value: Optional[str]) -> Optional[str]:
    """A follow-up question keeps its question mark."""
    if value is None:
        return None
    cleaned = re.sub(r"\s+", " ", str(value)).strip(" \t\r\n\"'")
    return cleaned or None


def _quote(name: str) -> str:
    return f"'{name}'"


def _is_number_word(token: str) -> bool:
    """True for a number spelled out as a word, e.g. the 'two' in 'two 1500'."""
    return bool(re.fullmatch(
        r"(?i)(zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
        r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
        r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|lakh|crore)",
        token.strip(),
    ))


def normalise_product_query(raw: str) -> Optional[str]:
    """
    Remove a trailing change phrase the model may have left in the name.

    'USBC Hub 7 Port Electronics Item two 1500' -> 'USBC Hub 7 Port Electronics Item'

    Only a spelled-out number plus its value is stripped, so a size that is
    genuinely part of a name ('Basmati Rice 1kg', '7 Up') is preserved. A bare
    trailing number is left alone because it may be part of the name.
    """
    text = _clean(raw)
    if not text:
        return None
    tokens = text.split()
    # "<number-word> <number>" tail, e.g. "two 1500".
    if (
        len(tokens) >= 2
        and _is_number_word(tokens[-2])
        and re.fullmatch(r"[\d.,]+", tokens[-1])
    ):
        tokens = tokens[:-2]
    # A lone trailing number word, e.g. "milk two".
    elif len(tokens) >= 2 and _is_number_word(tokens[-1]):
        tokens = tokens[:-1]
    return " ".join(tokens).strip() or None


# ── Validation ────────────────────────────────────────────────────────────────

def build_intent(raw: dict, user_text: str) -> InventoryIntent:
    """Parse the model's function-call arguments into a typed intent."""
    if not isinstance(raw, dict):
        return InventoryIntent(status="needs_clarification", intent="",
                               clarification_question="Could you rephrase that request?")
    payload = dict(raw)
    payload.pop("user_id", None)
    if isinstance(payload.get("changes"), dict):
        payload["changes"] = {k: v for k, v in payload["changes"].items() if k != "user_id"}
    try:
        return InventoryIntent(**payload)
    except Exception:
        return InventoryIntent(
            status="needs_clarification",
            intent="",
            clarification_question="Could you rephrase that request?",
        )


def validate_intent(intent: InventoryIntent, user_text: str,
                    allowed_ids: Optional[set[int]] = None) -> IntentOutcome:
    """
    Deterministic validation. Returns an IntentPlan, a Clarification, or a Rejection.

    Nothing here touches the database, and nothing here trusts the model.
    `allowed_ids` is the complete set of product ids this request may use: the
    digits the user wrote themselves, plus any row this backend already fetched
    from the database for this user while resolving an ambiguity.
    """
    stated_ids = set(user_stated_ids(user_text)) | set(allowed_ids or set())

    name = (intent.intent or "").strip()
    name = INTENT_ALIASES.get(name.lower(), name.lower())
    if name not in KNOWN_INTENTS:
        return Rejection(
            f"I cannot map that to an inventory action. Supported actions: "
            f"{', '.join(sorted(KNOWN_INTENTS))}."
        )

    targets: list[str] = []
    for raw in intent.product_queries or []:
        cleaned = normalise_product_query(raw)
        if cleaned and cleaned not in targets:
            targets.append(cleaned)
    if len(targets) > MAX_TARGETS:
        return Rejection(
            f"That request names {len(targets)} products. Please do at most {MAX_TARGETS} at a time."
        )

    changes = intent.changes
    stock_err = _validate_stock(changes.stock)
    if stock_err:
        return Rejection(stock_err)
    price_err = _validate_price(changes.price)
    if price_err:
        return Rejection(price_err)

    # A product id is only honoured when the user wrote those digits themselves.
    product_id = None
    if intent.product_id is not None:
        if intent.product_id in stated_ids:
            product_id = intent.product_id
        else:
            return Rejection(
                "I can only use a product ID that you provided. Please give the product name."
            )

    if name in MUTATING_INTENTS and intent.status == "needs_clarification":
        return _clarification_or_reject(intent, name, targets, is_mutation=True)

    if name == INTENT_CREATE:
        return _plan_create(intent, targets)
    if name == INTENT_UPDATE_STOCK:
        return _plan_update_stock(intent, targets, product_id)
    if name == INTENT_UPDATE_PRODUCT:
        return _plan_update_product(intent, targets, product_id)
    if name == INTENT_DELETE:
        return _plan_delete(intent, targets, product_id)
    if name == INTENT_GET_DETAILS:
        return _plan_details(targets, product_id)
    if name == INTENT_QUERY_INVENTORY:
        if not targets:
            return Clarification("Which product should I look up?",
                                 missing=["product_query"])
        return IntentPlan(
            intent=name, kind="read", tool_name=INTENT_QUERY_INVENTORY,
            arguments={"product_name": targets[0]}, targets=targets,
        )
    if name == INTENT_LOW_STOCK:
        threshold = intent.threshold if intent.threshold is not None else 10
        if threshold < 0:
            return Rejection("The low-stock threshold cannot be negative.")
        return IntentPlan(
            intent=name, kind="read", tool_name=INTENT_LOW_STOCK,
            arguments={"threshold": int(threshold)}, targets=[],
        )
    if name == INTENT_ALL_CATEGORIES:
        return IntentPlan(intent=name, kind="read", tool_name=INTENT_ALL_CATEGORIES,
                          arguments={}, targets=[])
    if name == INTENT_BY_CATEGORY:
        category = _clean(intent.category)
        if not category:
            return Clarification("Which category should I list?", missing=["category"])
        return IntentPlan(
            intent=name, kind="read", tool_name=INTENT_BY_CATEGORY,
            arguments={"category": category}, targets=[],
        )
    if name == INTENT_BY_NAMES:
        names = [_clean(n) for n in (intent.names or [])]
        names = [n for n in names if n]
        if not names:
            return Clarification("Which product names should I look up?",
                                 missing=["names"])
        return IntentPlan(
            intent=name, kind="read", tool_name=INTENT_BY_NAMES,
            arguments={"names": names}, targets=[],
        )
    if name == INTENT_SEARCH:
        return _plan_search(intent, targets)
    if name == INTENT_ANALYTICS:
        return IntentPlan(intent=name, kind="analytics", tool_name=INTENT_ANALYTICS,
                          arguments={}, targets=[])
    if name == INTENT_CATEGORY_ANALYTICS:
        return IntentPlan(intent=name, kind="analytics", tool_name=INTENT_CATEGORY_ANALYTICS,
                          arguments={}, targets=[])
    return Rejection("That action is not supported.")


def _clarification_or_reject(intent: InventoryIntent, name: str,
                             targets: list[str], is_mutation: bool) -> Clarification | Rejection:
    """The model asked to clarify. Accept a good question, reject a useless one."""
    question = _clean_question(intent.clarification_question)
    if not question:
        missing = ", ".join(_clean(m) or "" for m in (intent.missing or []))
        return Rejection(
            f"Not enough information to {name.replace('_', ' ')}"
            + (f". Missing: {missing}." if missing else ".")
        )
    if len(question) > 300:
        question = question[:297] + "..."
    return Clarification(
        question=question,
        missing=[m for m in (intent.missing or []) if _clean(m)],
    )


def _validate_stock(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Stock must be a whole number."
    if isinstance(value, float):
        if not float(value).is_integer():
            return "Stock must be a whole number."
        value = int(value)
    if value < 0:
        return "Stock cannot be negative."
    if value > MAX_STOCK:
        return f"Stock must be {MAX_STOCK} or less."
    return None


def _validate_price(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Price must be a number."
    if number != number or number in (float("inf"), float("-inf")):
        return "Price must be a finite number."
    if number < 0:
        return "Price cannot be negative."
    return None


def _plan_create(intent: InventoryIntent, targets: list[str]) -> IntentOutcome:
    if not targets:
        return Clarification("What should I call the new product?", missing=["name"])
    name = targets[0]
    if len(targets) > 1:
        return Rejection(
            "Please add one product at a time so each one is confirmed individually."
        )
    category = _clean(intent.category)
    missing = []
    if not category:
        missing.append("category")
    if intent.changes.stock is None:
        missing.append("stock")
    if intent.changes.price is None:
        missing.append("price")
    if missing:
        return Clarification(
            _create_question(name, missing),
            missing=missing,
        )
    return IntentPlan(
        intent=INTENT_CREATE,
        kind="create",
        tool_name=INTENT_CREATE,
        arguments={
            "name": name,
            "category": category,
            "stock": int(intent.changes.stock),
            "price": round(float(intent.changes.price), 2),
            "supplier": _clean(intent.supplier) or "Unknown",
        },
        targets=targets,
    )


def _create_question(name: str, missing: list[str]) -> str:
    if missing == ["category"]:
        return f"Which category should {_quote(name)} be in?"
    if missing == ["stock"]:
        return f"How many units of {_quote(name)} should be in stock?"
    if missing == ["price"]:
        return f"What price should I set for {_quote(name)}?"
    parts = []
    if "category" in missing:
        parts.append("the category")
    if "stock" in missing:
        parts.append("the stock quantity")
    if "price" in missing:
        parts.append("the price")
    return f"Please provide {', '.join(parts)} for {_quote(name)}."


def _plan_update_stock(intent: InventoryIntent, targets: list[str],
                       product_id: Optional[int]) -> IntentOutcome:
    if not targets and product_id is None:
        return Clarification("Which product's stock should I change?", missing=["product_query"])
    if len(targets) > 1:
        return Rejection(
            "Please update one product at a time so each change is confirmed individually."
        )
    if intent.changes.stock_is_relative:
        return Clarification(
            f"What should the new stock level be for {_quote(targets[0])}?"
            if targets else "What should the new stock level be?",
            missing=["stock"],
        )
    if intent.changes.stock is None:
        subject = _quote(targets[0]) if targets else "that product"
        return Clarification(f"What stock quantity should I set for {subject}?",
                             missing=["stock"])
    arguments = {"new_quantity": int(intent.changes.stock)}
    if product_id is not None:
        arguments["product_id"] = product_id
    return IntentPlan(
        intent=INTENT_UPDATE_STOCK, kind="update", tool_name=INTENT_UPDATE_STOCK,
        arguments=arguments, targets=targets, needs_product=product_id is None,
    )


def _plan_update_product(intent: InventoryIntent, targets: list[str],
                         product_id: Optional[int]) -> IntentOutcome:
    if not targets and product_id is None:
        return Clarification("Which product should I change?", missing=["product_query"])
    if len(targets) > 1:
        return Rejection(
            "Please update one product at a time so each change is confirmed individually."
        )
    changes = intent.changes
    if changes.stock_is_relative:
        subject = _quote(targets[0]) if targets else "that product"
        return Clarification(f"What should the new stock level be for {subject}?",
                             missing=["stock"])
    applied = {}
    if changes.stock is not None:
        applied["new_stock"] = int(changes.stock)
    if changes.price is not None:
        applied["new_price"] = round(float(changes.price), 2)
    category = _clean(changes.category)
    if category:
        applied["new_category"] = category[:100]
    if not applied:
        subject = _quote(targets[0]) if targets else "that product"
        return Clarification(
            f"What would you like to change about {subject} — its stock, price, or category?",
            missing=["changes"],
        )
    if product_id is not None:
        applied["product_id"] = product_id
    return IntentPlan(
        intent=INTENT_UPDATE_PRODUCT, kind="update", tool_name=INTENT_UPDATE_PRODUCT,
        arguments=applied, targets=targets, needs_product=product_id is None,
    )


def _plan_delete(intent: InventoryIntent, targets: list[str],
                 product_id: Optional[int]) -> IntentOutcome:
    if not targets and product_id is None:
        return Clarification("Which product should I delete?", missing=["product_query"])
    if len(targets) > 1:
        return Rejection(
            "Please delete one product at a time so each deletion is confirmed individually."
        )
    arguments = {}
    if product_id is not None:
        arguments["product_id"] = product_id
    return IntentPlan(
        intent=INTENT_DELETE, kind="delete", tool_name=INTENT_DELETE,
        arguments=arguments, targets=targets, needs_product=product_id is None,
        is_destructive=True,
    )


def _plan_details(targets: list[str], product_id: Optional[int]) -> IntentOutcome:
    if not targets and product_id is None:
        return Clarification("Which product's details should I show?", missing=["product_query"])
    if len(targets) > 1:
        return Rejection("Please ask about one product at a time.")
    arguments = {"product_id": product_id} if product_id is not None else {}
    return IntentPlan(
        intent=INTENT_GET_DETAILS, kind="read", tool_name=INTENT_GET_DETAILS,
        arguments=arguments, targets=targets, needs_product=product_id is None,
    )


def _plan_search(intent: InventoryIntent, targets: list[str]) -> IntentOutcome:
    arguments: dict[str, Any] = {}
    if targets:
        arguments["name"] = targets[0]
    category = _clean(intent.category)
    if category:
        arguments["category"] = category
    if intent.min_price is not None:
        if _validate_price(intent.min_price):
            return Rejection("The minimum price must be a finite, non-negative number.")
        arguments["min_price"] = round(float(intent.min_price), 2)
    if intent.max_price is not None:
        if _validate_price(intent.max_price):
            return Rejection("The maximum price must be a finite, non-negative number.")
        arguments["max_price"] = round(float(intent.max_price), 2)
    if intent.min_price is not None and intent.max_price is not None:
        if float(intent.min_price) > float(intent.max_price):
            return Rejection("The minimum price cannot be greater than the maximum price.")
    if intent.stock_threshold is not None:
        if _validate_stock(intent.stock_threshold):
            return Rejection("The stock filter must be a whole, non-negative number.")
        arguments["stock_threshold"] = int(intent.stock_threshold)
    if intent.sort_by:
        sort = str(intent.sort_by).strip().lower()
        if sort not in SORT_OPTIONS:
            return Rejection(
                f"Sort must be one of: {', '.join(SORT_OPTIONS)}."
            )
        arguments["sort_by"] = sort
    limit = intent.limit if intent.limit is not None else 50
    if limit < 1 or limit > MAX_LIMIT:
        return Rejection(f"The result limit must be between 1 and {MAX_LIMIT}.")
    arguments["limit"] = int(limit)
    offset = intent.offset if intent.offset is not None else 0
    if offset < 0:
        return Rejection("The result offset cannot be negative.")
    arguments["offset"] = int(offset)
    return IntentPlan(
        intent=INTENT_SEARCH, kind="read", tool_name=INTENT_SEARCH,
        arguments=arguments, targets=targets,
    )
