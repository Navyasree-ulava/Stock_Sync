"""
ai/agent.py — Deterministic inventory routing and Groq/MCP orchestration.

CRUD and canonical product-list requests are routed to exactly one MCP tool.
Their response text is rendered from the committed tool result so model prose
cannot alter authoritative IDs, prices, stock values, or row counts.
"""

import os
import re
import json
import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

from openai import (
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    RateLimitError,
)
from pydantic import BaseModel

from mcp_bridge.client_manager import mcp_manager
from db.models import normalize_category

from .clarification import clarification_store
from .intent_schema import (
    INTENT_TOOL,
    MUTATING_INTENTS,
    SYSTEM_PROMPT_INTENT,
    Clarification,
    IntentPlan,
    Rejection,
    build_intent,
    merge_intent,
    normalise_intent_name,
    normalise_product_query,
    salvage_missing_create_fields,
    validate_intent,
)

import time

log = logging.getLogger(__name__)

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
PROVIDER = "groq"
API_KEY = GROQ_API_KEY
BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
MODEL = os.environ.get("LLM_MODEL", DEFAULT_GROQ_MODEL)
MAX_TURNS: int = int(os.environ.get("LLM_MAX_TURNS", "10"))
# When true the LLM produces a typed intent first and Python validates and
# executes it. Set to "false" to fall back to the legacy regex router.
USE_LLM_INTENT: bool = os.environ.get("USE_LLM_INTENT", "true").strip().lower() not in (
    "false",
    "0",
    "no",
)
# Stop asking follow-up questions after this many rounds so a user who cannot
# fill the gap gets a clear instruction instead of an endless loop.
MAX_CLARIFICATIONS: int = int(os.environ.get("LLM_MAX_CLARIFICATIONS", "3"))

# Hard bound on one intent-extraction call. A provider that hangs must not hold
# the request open indefinitely, and it must not leave pending clarification
# state behind: the caller treats a timeout exactly like a failed extraction.
INTENT_TIMEOUT_SECONDS: float = float(os.environ.get("LLM_INTENT_TIMEOUT", "30"))

PRODUCT_READ_TOOLS = {
    "query_inventory_db",
    "get_product_details",
    "search_inventory",
    "get_low_stock_items",
    "get_products_by_category",
    "get_products_by_names",
}

SYSTEM_PROMPT = """You are StockSync, an inventory assistant with tool-based access to a real PostgreSQL database.

RULES:
1. Use only values returned by tools. Never change, rescale, infer, or recalculate numeric values.
2. Return prices using the exact numeric value, for example 1499 as ₹1,499.00.
3. Product category listings use get_products_by_category exactly once.
4. Product searches use search_inventory exactly once.
5. Create, update, and delete requests must use their matching mutation tool and report success only when the tool returns success=true.
6. Use update_stock for stock-only changes and update_product for stock+price/category or price/category-only changes.
7. Each product-list request executes one canonical read tool; do not call an overlapping read tool for the same data.
8. The authenticated user ID is injected by the backend. Never request or provide user_id.
"""


class QueryResponse(BaseModel):
    answer: str
    tool_used: Optional[str] = None
    data: Optional[list] = None


class AIConfigurationError(RuntimeError):
    """The AI provider is not configured correctly."""


class MCPUnavailableError(RuntimeError):
    """Inventory tools are unavailable or failed unexpectedly."""


@dataclass(frozen=True)
class RoutedRequest:
    tool_name: str
    arguments: dict
    kind: str
    label: str
    validation_error: Optional[str] = None


def _get_openai_client() -> OpenAI:
    if not API_KEY:
        raise AIConfigurationError("GROQ_API_KEY is not configured.")
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


def _normalize_client_rows(value) -> list[dict]:
    values = value if isinstance(value, list) else [value]
    rows = []
    for item in values:
        if isinstance(item, dict):
            if item:
                rows.append(item)
        elif item is not None and item != "":
            rows.append({"value": item})
    return rows


def _clean_name(value: str) -> str:
    value = re.sub(r"^(?:the|this|product)\s+", "", value.strip(), flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", value).strip(" ,.;:?")


def _clean_category(value: str) -> str:
    """
    Canonical category identity for LLM-extracted categories.

    The model's casing is never trusted: the value is normalized (trim +
    lowercase) so 'Grains', 'grains' and ' Grains ' are one category. The
    display label is derived separately.
    """
    value = re.sub(r"^(?:the|my)\s+", "", value.strip(), flags=re.IGNORECASE)
    value = re.sub(r"\s+category$", "", value, flags=re.IGNORECASE)
    value = value.strip(" ,.;:?")
    if not value:
        return ""
    return normalize_category(value)


# A field noun can end up glued to the product name when the user writes
# "update milk price to 50" instead of "update the price of milk to 50".
_TRAILING_FIELD_NOUN = re.compile(
    r"\s+(?:price|stock|quantity|category|cat)\s*$", flags=re.IGNORECASE
)


def _strip_trailing_field_noun(name: Optional[str]) -> Optional[str]:
    if not name:
        return name
    return _TRAILING_FIELD_NOUN.sub("", name).strip() or None


def _distinct_product_targets(text: str) -> set[str]:
    """
    Collect product names bound to a field with "<field> of/for <name>".

    "set the price of Milk and the stock of Bread to 10" names two different
    products, so it must be rejected rather than applied to whichever one the
    name regex happened to capture.
    """
    targets = set()
    for match in re.finditer(
        r"\b(?:price|stock|quantity|category|cat)\s+(?:of|for)\s+(?:the\s+|this\s+)?"
        r"(.+?)(?=\s+(?:and|to|with|from|in)\b|$)",
        text,
        flags=re.IGNORECASE,
    ):
        candidate = _clean_name(match.group(1)).lower()
        if candidate:
            targets.add(candidate)
    return targets


def _extract_category(question: str) -> Optional[str]:
    patterns = (
        r"\b(?:in|from)\s+(?:the\s+)?(.+?)\s+category\b",
        r"\bproducts?\s+(?:in|from)\s+(?:the\s+)?(.+?)(?:\s+category)?\s*(?:\?|$)",
        r"\b(?:show|list|display|get)\s+(?:me\s+)?(?:all\s+)?(?:the\s+)?(.+?)\s+products?\b",
    )
    for pattern in patterns:
        match = re.search(pattern, question, flags=re.IGNORECASE)
        if match:
            category = _clean_category(match.group(1))
            if category and category.lower() not in {"all", "inventory", "my inventory"}:
                return category
    return None


def _extract_create_request(question: str) -> RoutedRequest:
    # Preserve digit-grouping commas before normalizing separator commas.
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", question)
    text = re.sub(r"\s*,\s*", " and ", text)
    name = None
    for pattern in (
        r"\bproduct\s+(?:called\s+|named\s+)?(.+?)(?=\s+(?:with\s+)?(?:in|to|for|category|cat|stock|quantity|price|supplier)\b|$)",
        r"\b(?:add|insert|create)\s+(?:a\s+)?(?:new\s+)?(?:product\s+)?(.+?)(?=\s+(?:to|in|for)\s+(?:the\s+)?|\s+(?:with|category|cat|stock|quantity|price|supplier)\b|$)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = _clean_name(match.group(1))
            break

    category_match = None
    for pattern in (
        r"\b(?:in|under)\s+(?:the\s+)?(.+?)\s+category\b",
        r"\b(?:category|cat)\s*(?:is|of|=|:)?\s*(.+?)(?=\s+(?:and\s+)?(?:with|stock|quantity|price|supplier)\b|$)",
    ):
        category_match = re.search(pattern, text, flags=re.IGNORECASE)
        if category_match:
            break

    # Stock and price appear in both orders in real requests:
    # "stock 25" and "25 stock", "price 45" and "at 45 rupees".
    stock_match = None
    for pattern in (
        r"\b(?:stock|quantity)\s*(?:is|of|=|:)?\s*(\d+(?:\.\d+)?)",
        r"\bwith\s+(\d+)\s*(?:units?\s+)?(?:in\s+)?(?:stock|quantity)\b",
        r"\b(\d+)\s*(?:units?\s+)?(?:in\s+)?(?:stock|quantity)\b",
    ):
        stock_match = re.search(pattern, text, flags=re.IGNORECASE)
        if stock_match:
            break

    price_match = None
    for pattern in (
        r"\bprice\s*(?:is|of|=|:|at)?\s*(?:rs\.?|inr|₹\s*)?(\d+(?:\.\d+)?)",
        r"\b(?:at|for|to)\s*(?:rs\.?|inr|₹\s*)?(\d+(?:\.\d+)?)\s*(?:rupees?|rs\.?)\b",
        r"\b(\d+(?:\.\d+)?)\s*(?:rupees?|rs\.)\b",
    ):
        price_match = re.search(pattern, text, flags=re.IGNORECASE)
        if price_match:
            break

    supplier_match = None
    for pattern in (
        r"\bsupplier\s*(?:is|of|=|:)?\s*(.+?)(?=\s+(?:and\s+)?(?:category|stock|quantity|price)\b|$)",
        r"\bfrom\s+(.+?)$",
    ):
        supplier_match = re.search(pattern, text, flags=re.IGNORECASE)
        if supplier_match:
            break

    category = _clean_category(category_match.group(1)) if category_match else None
    supplier = _clean_name(supplier_match.group(1)) if supplier_match else "Unknown"
    stock = None
    stock_parse_error = None
    price = None
    if stock_match:
        parsed_stock = float(stock_match.group(1))
        if parsed_stock.is_integer():
            stock = int(parsed_stock)
        else:
            stock_parse_error = "Stock must be a whole number."
    if price_match:
        price = float(price_match.group(1))

    arguments = {
        "name": name,
        "category": category,
        "stock": stock,
        "price": price,
        "supplier": supplier or "Unknown",
    }
    missing = [field for field in ("name", "category", "stock", "price") if arguments[field] is None]
    if stock_parse_error:
        error = stock_parse_error
    elif missing:
        error = f"Missing required create field(s): {', '.join(missing)}."
    else:
        error = None
    return RoutedRequest("create_product", arguments, "create", "Product creation", error)


def _extract_update_request(question: str) -> RoutedRequest:
    text = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", question)
    text = re.sub(r"\s*,\s*", " and ", text)

    product_id = None
    id_match = re.search(r"\bproduct\s*(?:id|#)\s*(\d+)", text, flags=re.IGNORECASE)
    if id_match:
        product_id = int(id_match.group(1))

    name = None
    for pattern in (
        r"\b(?:quantity|stock)\s+of\s+(?:the\s+|this\s+)?(.+?)(?=\s+(?:to|from|by)\b|\s+in\s+|\s+and\s+price\b|$)",
        r"\b(?:update|set|change)\s+(?:the\s+)?price\s+(?:of|for)\s+(?:the\s+|this\s+)?(.+?)(?=\s+to\b|\s+in\s+|$)",
        r"\b(?:update|set|change)\s+(?:the\s+)?(?:category|cat)\s+(?:of|for)\s+(?:the\s+|this\s+)?(.+?)(?=\s+to\b|\s+in\s+|\s+and\s+|$)",
        r"\b(?:update|set|change)\s+(?:the\s+)?(?:quantity|stock)?\s*(?:of\s+|for\s+)?(?:this\s+|the\s+)?(.+?)(?=\s+(?:stock|quantity)\b|\s+to\b|\s+from\b|\s+in\s+|\s+and\s+price\b|$)",
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            name = _clean_name(match.group(1))
            # "update milk price to 50" must target "milk", not "milk price".
            name = _strip_trailing_field_noun(name)
            break

    price_match = re.search(
        r"\bprice\b.*?\bto\s+(?:rs\.?|inr|₹\s*)?(\d+(?:\.\d+)?)",
        text,
        flags=re.IGNORECASE,
    )
    # Two distinct change forms. "category to X" and "category of NAME to X".
    # A bare "in category X" locator must never match either, otherwise the
    # stock target that follows it would be read as a category value.
    category_change_match = re.search(
        r"\bcategory\s+(?:to|=|:)\s*(.+?)(?=\s+(?:and\s+)?(?:price|stock|quantity|supplier)\b|$)",
        text,
        flags=re.IGNORECASE,
    )
    if not category_change_match:
        category_change_match = re.search(
            r"\bcategory\s+(?:of|for)\s+(?:the\s+|this\s+)?.+?\s+to\s+(.+?)(?=\s+(?:and\s+)?(?:price|stock|quantity|supplier)\b|$)",
            text,
            flags=re.IGNORECASE,
        )
    supplier_change = re.search(r"\bsupplier\s+(?:to|=|:)\s*\S+", text, flags=re.IGNORECASE)

    # Remove a price clause before searching for the stock target so
    # "price to 130" is never interpreted as stock=130.
    stock_text = re.sub(
        r"\bprice\b.*?\bto\s+(?:rs\.?|inr|₹\s*)?\d+(?:\.\d+)?",
        "",
        text,
        flags=re.IGNORECASE,
    )
    quantity_match = re.search(r"\bto\s+(-?\d+)\b|\bby\s+(-?\d+)\b", stock_text, flags=re.IGNORECASE)
    new_quantity = None
    relative_change = False
    if quantity_match:
        if quantity_match.group(1) is not None:
            new_quantity = int(quantity_match.group(1))
        else:
            relative_change = True

    new_price = float(price_match.group(1)) if price_match else None
    new_category = (
        _clean_category(category_change_match.group(1))
        if category_change_match
        else None
    )

    supplier_match = re.search(
        r"\bsupplier\s+(?:is\s+)?([A-Za-z0-9&'(). -]+?)(?=\s+(?:and\s+)?(?:category|stock|quantity|price)\b|$)",
        text,
        flags=re.IGNORECASE,
    )

    multiple_targets = sorted(_distinct_product_targets(text))

    if len(multiple_targets) > 1:
        error = (
            "One product per request: this request names more than one product "
            f"({', '.join(multiple_targets)}). Update them one at a time."
        )
    elif supplier_change:
        error = "Supplier changes are not applied by the deterministic update route."
    elif relative_change:
        error = "Use an absolute target quantity, for example 'set the stock of Widget to 10'."
    elif product_id is None and not name:
        error = "A product name or product ID is required for an update."
    elif new_quantity is None and new_price is None and new_category is None:
        error = "Provide a new stock, price, or category value."
    elif new_quantity is not None and new_quantity < 0:
        error = "Stock cannot be negative."
    elif new_price is not None and new_price < 0:
        error = "Price cannot be negative."
    else:
        error = None

    if new_price is not None or new_category is not None:
        return RoutedRequest(
            "update_product",
            {
                "product_id": product_id,
                "product_name": name,
                "supplier": _clean_name(supplier_match.group(1)) if supplier_match else None,
                "new_stock": new_quantity,
                "new_price": new_price,
                "new_category": new_category,
            },
            "product_update",
            "Product update",
            error,
        )

    return RoutedRequest(
        "update_stock",
        {
            "product_id": product_id,
            "product_name": name,
            "new_quantity": new_quantity,
            "supplier": _clean_name(supplier_match.group(1)) if supplier_match else None,
        },
        "update",
        "Stock update",
        error,
    )


def _extract_delete_request(question: str) -> RoutedRequest:
    product_id = None
    for pattern in (
        r"\b(?:id|#)\s*(\d+)\b",
        r"\b(?:delete|remove)\s+(?:the\s+)?(?:product|item)\s+(\d+)\b",
    ):
        id_match = re.search(pattern, question, flags=re.IGNORECASE)
        if id_match:
            product_id = int(id_match.group(1))
            break

    name = None
    match = re.search(
        r"\b(?:delete|remove)\s+(?:the\s+)?(?:product\s+)?(?:called\s+|named\s+)?(.+?)(?:\s+(?:from|out of)\s+(?:(?:the|my)\s+)?inventory|\s+in\s+(?:the\s+)?inventory|\?|$)",
        question,
        flags=re.IGNORECASE,
    )
    if product_id is None and match:
        name = _clean_name(match.group(1))

    supplier_match = re.search(
        r"\bsupplier\s+(?:is\s+)?([A-Za-z0-9&'(). -]+?)(?:\?|$)",
        question,
        flags=re.IGNORECASE,
    )
    arguments = {
        "product_id": product_id,
        "product_name": name,
        "supplier": _clean_name(supplier_match.group(1)) if supplier_match else None,
    }
    # Bulk and category deletion are not supported. Routing them into the
    # delete tool would depend on a name that happens not to match anything.
    bulk_target = bool(
        name
        and re.search(
            r"^(?:all|every|each|everything|any|entire)\b|\bcategory\b",
            name,
            flags=re.IGNORECASE,
        )
    )
    if bulk_target:
        error = (
            "Bulk deletion and category deletion are not supported. "
            "Delete one product at a time by its exact name or product ID."
        )
    elif product_id is None and not name:
        error = "A product name or product ID is required for deletion."
    else:
        error = None
    return RoutedRequest("delete_product", arguments, "delete", "Product deletion", error)


def _extract_search_request(question: str) -> RoutedRequest:
    name = None
    quoted = re.search(r"[\"']([^\"']+)[\"']", question)
    if quoted:
        name = quoted.group(1).strip()
    else:
        for pattern in (
            r"\b(?:search|find)\s+(?:for\s+)?(?:the\s+)?(?:product\s+)?(.+?)(?:\?|$)",
            r"\b(?:price|stock)\s+of\s+(?:the\s+|this\s+)?(.+?)(?:\?|$)",
        ):
            match = re.search(pattern, question, flags=re.IGNORECASE)
            if match:
                name = _clean_name(match.group(1))
                break
    return RoutedRequest("search_inventory", {"name": name}, "search", "Product search")


def route_inventory_request(question: str) -> Optional[RoutedRequest]:
    """Route high-confidence CRUD and canonical read requests without an LLM turn."""
    lower = question.lower()

    # Reject requests that mix two different mutation intents. Applying only
    # the first one would report success while silently dropping the second.
    mutation_intents = {
        name: re.search(pattern, lower)
        for name, pattern in (
            ("create", r"\b(add|insert|create)\b"),
            ("update", r"\b(update|set|change|increase|decrease)\b"),
            ("delete", r"\b(delete|remove)\b"),
        )
    }
    present_intents = sorted(k for k, v in mutation_intents.items() if v)
    if len(present_intents) > 1:
        return RoutedRequest(
            None,
            {},
            "mixed",
            "Request",
            (
                f"This request mixes {present_intents[0]} and {present_intents[1]} operations. "
                "Send one change at a time so the result is unambiguous."
            ),
        )

    create_verb = mutation_intents["create"]
    # "add 5 more Widgets" is a restock, not a product named "5 more Widgets".
    quantity_first_create = re.search(
        r"\b(?:add|insert|create)\s+(?:an?\s+)?(?:new\s+)?(?:product\s+)?\d+\b", lower
    )
    # A create request names a product and supplies product fields. A stock
    # number alone ("add 5 more units of milk") is a restock, not a create.
    field_hints = sum(
        1
        for pattern in (
            r"\bcategory\b",
            r"\bprice\b|\brupees?\b|\brs\.?\b|₹",
            r"\bsupplier\b|\bfrom\b",
        )
        if re.search(pattern, lower)
    )
    if quantity_first_create:
        return RoutedRequest(
            "update_stock",
            {},
            "update",
            "Stock update",
            "Use an absolute target quantity, for example 'set the stock of Widget to 12'.",
        )
    if create_verb and (re.search(r"\bproduct\b", lower) or field_hints >= 1):
        return _extract_create_request(question)
    if (
        create_verb
        and re.search(r"\b(?:stock|quantity|units)\b", lower)
        and not re.search(r"\b(?:report|summary|analytics|analysis|list)\b", lower)
    ):
        return RoutedRequest(
            "update_stock",
            {},
            "update",
            "Stock update",
            "Use an absolute target quantity, for example 'set the stock of Milk to 12'.",
        )
    if create_verb and not re.search(
        r"\b(?:report|summary|analytics|analysis|list)\b", lower
    ):
        return _extract_create_request(question)
    if re.search(r"\b(update|set|change|increase|decrease)\b", lower) and re.search(
        r"\b(?:stock|quantity|price|category)\b",
        lower,
    ):
        return _extract_update_request(question)
    if re.search(r"\b(delete|remove)\b", lower):
        return _extract_delete_request(question)

    if re.search(
        r"\b(analytics|average|total|value|how\s+many|count|breakdown|most\s+expensive|cheapest|statistics|stats)\b",
        lower,
    ):
        return None

    if "category" in lower or re.search(r"\bproducts?\s+(?:in|from)\b", lower):
        category = _extract_category(question)
        if category:
            return RoutedRequest(
                "get_products_by_category",
                {"category": category},
                "category",
                f"category '{category}'",
            )

    if re.search(r"\b(search|find)\b", lower):
        return _extract_search_request(question)
    if re.search(r"\b(?:show|list|display|get)\b.*\b(?:all\s+)?products\b", lower):
        return RoutedRequest("search_inventory", {"limit": 500}, "search", "inventory")
    if re.search(r"\b(?:price|stock)\s+of\b", lower):
        return _extract_search_request(question)
    return None


def _format_price(value) -> str:
    try:
        return f"₹{float(value):,.2f}"
    except (TypeError, ValueError):
        return "₹0.00"


def _format_product_line(product: dict) -> str:
    supplier = product.get("supplier") or "Unknown"
    return (
        f"- {product.get('name', 'Unnamed product')} — "
        f"stock: {product.get('stock', 0)}, "
        f"price: {_format_price(product.get('price', 0))}, "
        f"supplier: {supplier}"
    )


def _format_product_read_response(rows: list[dict], label: str) -> str:
    if not rows:
        return f"No products found for {label}."
    heading = f"Found {len(rows)} product{'' if len(rows) == 1 else 's'} for {label}:"
    return heading + "\n" + "\n".join(_format_product_line(row) for row in rows)


def _numeric_claims_preserved(text: str, rows: list[dict]) -> bool:
    allowed = {float(len(rows))}

    def collect(value):
        if isinstance(value, list):
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for nested in value.values():
                collect(nested)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            allowed.add(float(value))
        elif isinstance(value, str):
            for token in re.findall(r"\d[\d,]*(?:\.\d+)?", value):
                allowed.add(float(token.replace(",", "")))

    collect(rows)
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text or ""):
        if float(token.replace(",", "")) not in allowed:
            return False
    return True


def _routed_failure(route: RoutedRequest, message: str, tool_result: Optional[dict] = None) -> QueryResponse:
    error_data = {"error": message}
    if tool_result and tool_result.get("matches"):
        error_data["matches"] = tool_result["matches"]
    return QueryResponse(
        answer=f"{route.label} failed: {message}",
        tool_used=route.tool_name if not route.validation_error else None,
        data=[error_data],
    )


async def _execute_routed_request(route: RoutedRequest, user_id: int, tools: list[dict]) -> QueryResponse:
    # A route with no tool is a pre-tool rejection (e.g. mixed intents):
    # nothing was called, so there is no tool availability to verify.
    if route.tool_name is None:
        return _routed_failure(route, route.validation_error or "Request could not be routed.")
    available = {tool.get("function", {}).get("name") for tool in tools}
    if route.tool_name not in available:
        return _routed_failure(route, f"Required MCP tool '{route.tool_name}' is unavailable.")
    if route.validation_error:
        return _routed_failure(route, route.validation_error)
    arguments = {key: value for key, value in route.arguments.items() if value is not None}
    arguments["user_id"] = user_id
    log.info(f"[AGENT] Canonical route '{route.tool_name}' args={arguments}")
    result = await mcp_manager.call_tool(route.tool_name, arguments)

    if isinstance(result, dict) and result.get("error"):
        error_text = str(result["error"])
        if error_text.startswith(("MCP session", "Tool call failed")):
            raise MCPUnavailableError(f"MCP tool '{route.tool_name}' failed: {error_text}")
        return _routed_failure(route, error_text, result)

    if route.kind == "create":
        if not isinstance(result, dict) or result.get("success") is not True or result.get("id") is None:
            return _routed_failure(route, "Database insert was not confirmed.")
        return QueryResponse(
            answer=(
                f"Created '{result['name']}' (ID {result['id']}) in {result['category']} with "
                f"stock {result['stock']}, price {_format_price(result['price'])}, "
                f"supplier {result.get('supplier') or 'Unknown'}."
            ),
            tool_used=route.tool_name,
            data=[result],
        )

    if route.kind == "product_update":
        if not isinstance(result, dict) or result.get("success") is not True or result.get("id") is None:
            return _routed_failure(route, "Database product update was not confirmed.", result)
        before = result.get("before") or {}
        changes = []
        if "stock" in result.get("updated_fields", []):
            changes.append(f"stock {before.get('stock')} → {result.get('stock')}")
        if "price" in result.get("updated_fields", []):
            changes.append(
                f"price {_format_price(before.get('price', 0))} → {_format_price(result.get('price', 0))}"
            )
        if "category" in result.get("updated_fields", []):
            changes.append(f"category {before.get('category')} → {result.get('category')}")
        summary = "; ".join(changes) if changes else "already had the requested values"
        return QueryResponse(
            answer=f"Updated '{result['name']}' (ID {result['id']}): {summary}.",
            tool_used=route.tool_name,
            data=[result],
        )

    if route.kind == "update":
        if not isinstance(result, dict) or result.get("success") is not True or result.get("id") is None:
            return _routed_failure(route, "Database update was not confirmed.")
        return QueryResponse(
            answer=(
                f"Updated '{result['name']}' (ID {result['id']}) stock from "
                f"{result.get('old_stock')} to {result.get('new_stock')}. "
                f"Price remains {_format_price(result.get('price', 0))}."
            ),
            tool_used=route.tool_name,
            data=[result],
        )

    if route.kind == "delete":
        if not isinstance(result, dict) or result.get("success") is not True or result.get("id") is None:
            return _routed_failure(route, "Database deletion was not confirmed.")
        return QueryResponse(
            answer=f"Deleted '{result['name']}' (ID {result['id']}) from your inventory.",
            tool_used=route.tool_name,
            data=[result],
        )

    rows = _normalize_client_rows(result)
    answer = _format_product_read_response(rows, route.label)
    limit = route.arguments.get("limit")
    if limit and len(rows) >= limit:
        answer += f"\nShowing the first {limit} rows; narrow the search for a complete view."
    return QueryResponse(
        answer=answer,
        tool_used=route.tool_name,
        data=rows,
    )


# ── LLM structured-intent pipeline ────────────────────────────────────────────
# Flow: user text -> LLM typed intent -> deterministic validation ->
#       product resolution (DB) -> one MCP action -> verified rendering.
# The model never produces SQL, product ids, or user_id.

_INTENT_MODEL_MAX_TOKENS = 700

# Internal bookkeeping that must never reach the browser.
_INTERNAL_ROW_KEYS = frozenset({
    "success", "message", "before", "updated_fields", "product_id",
    "old_stock", "new_stock", "deleted_id", "error", "needs_clarification",
    "missing", "tool", "intent", "select_candidate", "user_id",
})
_PUBLIC_ROW_KEYS = ("id", "name", "category", "stock", "price", "supplier")


def _public_row(row: dict) -> dict:
    """Keep the useful product columns, drop every internal agent field."""
    return {key: row[key] for key in _PUBLIC_ROW_KEYS if key in row}


def _public_rows(rows) -> list[dict]:
    return [_public_row(r) for r in rows if isinstance(r, dict) and r.get("id") is not None]


def _observe(stage: str, **fields) -> None:
    """Structured, secret-free observability for the intent pipeline."""
    parts = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    log.info(f"[INTENT:{stage}] {parts}")


def _intent_error(message: str, intent: str = None, stage: str = "rejected") -> QueryResponse:
    """A refusal that never touched the database. Conversation text only."""
    _observe(stage, intent=intent, outcome="error")
    return QueryResponse(answer=message, tool_used=None, data=None)


def _clarification_response(clarification: Clarification, intent: str = None) -> QueryResponse:
    """
    A question for the user.

    Deliberately carries no data payload: the browser must show the question,
    not the agent's internal state. The pending intent lives server-side.
    """
    _observe("clarification", intent=intent, missing=list(clarification.missing))
    return QueryResponse(answer=clarification.question, tool_used=None, data=None)


_AFFIRMATIVE = {
    "yes", "yeah", "yep", "yup", "sure", "ok", "okay", "confirm", "confirmed",
    "affirmative", "proceed", "delete", "remove", "go", "ahead",
}
_NEGATIVE = {
    "no", "nope", "nah", "cancel", "stop", "dont", "keep", "abort", "nevermind",
    "negative", "keepit", "dontdelete",
}
# A confirmation reply is expected to be short. Without this, a confused
# question such as "what do you mean?" would match a stray affirmative word.
_MAX_CONFIRM_WORDS = 5


def _is_affirmative(text: str) -> bool:
    tokens = re.findall(r"[a-z]+", (text or "").lower())
    if not tokens or len(tokens) > _MAX_CONFIRM_WORDS:
        return False
    return bool(set(tokens) & _AFFIRMATIVE)


def _is_negative(text: str) -> bool:
    tokens = set(re.findall(r"[a-z]+", (text or "").lower()))
    return bool(tokens & _NEGATIVE)


def _build_intent_messages(question: str, pending) -> list[dict]:
    """
    Assemble the extraction prompt.

    A pending clarification is folded into the system message together with the
    original request and the candidate rows, so the model can complete the
    original intent from a short answer.
    """
    system = SYSTEM_PROMPT_INTENT
    if pending is not None:
        system += (
            "\n\nPENDING REQUEST CONTEXT (the user is answering your question; "
            "combine it and submit the completed intent):\n"
            + json.dumps(pending.to_context(), default=str)
        )
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": pending.original_question},
        ]
        if pending.clarification_question:
            messages.append({"role": "assistant", "content": pending.clarification_question})
        messages.append({"role": "user", "content": question})
        return messages
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]


async def _extract_raw_intent(client: OpenAI, question: str, pending) -> Optional[dict]:
    """
    One LLM call, with a single relaxed retry.

    The provider can reject a forced tool_choice when the model declines to
    emit a tool call (HTTP 400). That must degrade to "no intent" rather than
    a 500, so it is retried once without the forcing and then given up on.
    """
    messages = _build_intent_messages(question, pending)

    def _call(forced: bool):
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=[INTENT_TOOL],
            tool_choice=(
                {"type": "function", "function": {"name": "submit_intent"}}
                if forced else "auto"
            ),
            parallel_tool_calls=False,
            max_tokens=_INTENT_MODEL_MAX_TOKENS,
            timeout=INTENT_TIMEOUT_SECONDS,
        )

    async def _attempt(forced: bool):
        loop = asyncio.get_running_loop()
        return await asyncio.wait_for(
            loop.run_in_executor(None, _call, forced),
            timeout=INTENT_TIMEOUT_SECONDS,
        )

    try:
        response = await _attempt(True)
    except asyncio.TimeoutError:
        log.error(f"[INTENT] intent extraction timed out after {INTENT_TIMEOUT_SECONDS}s")
        return None
    except BadRequestError as exc:
        log.warning(f"[INTENT] forced tool_choice rejected ({exc}); retrying unforced")
        try:
            response = await _attempt(False)
        except asyncio.TimeoutError:
            log.error(f"[INTENT] unforced intent extraction timed out "
                      f"after {INTENT_TIMEOUT_SECONDS}s")
            return None
        except Exception as retry_exc:  # noqa: BLE001
            log.error(f"[INTENT] intent extraction failed: {retry_exc}")
            return None
    except (AuthenticationError, NotFoundError, RateLimitError):
        # Distinct from this app's own 30/min per-IP limit; let /query map it.
        raise
    except Exception as exc:  # noqa: BLE001
        log.error(f"[INTENT] intent extraction failed: {exc}")
        return None

    msg = response.choices[0].message

    if msg.tool_calls:
        call = msg.tool_calls[0]
        if call.function.name != "submit_intent":
            log.warning(f"[INTENT] model called unexpected tool '{call.function.name}'")
            return None
        try:
            raw = json.loads(call.function.arguments)
        except (json.JSONDecodeError, TypeError):
            log.warning("[INTENT] submit_intent arguments were not valid JSON")
            return None
        return raw if isinstance(raw, dict) else None

    # The model answered in prose instead of calling the tool. Try to recover a
    # JSON object, otherwise treat it as "I could not work out the request".
    content = (msg.content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.lower().startswith("json"):
            content = content[4:]
    start, end = content.find("{"), content.rfind("}")
    if start != -1 and end > start:
        try:
            raw = json.loads(content[start:end + 1])
            if isinstance(raw, dict):
                return raw
        except json.JSONDecodeError:
            pass
    return None


async def _resolve_candidates(user_id: int, product_query: str) -> list[dict]:
    """
    Look the product up in PostgreSQL through the existing read tool.

    This is the only place a product id is obtained. The model never supplies
    one, so an invented id is impossible by construction.
    """
    result = await mcp_manager.call_tool(
        "query_inventory_db",
        {"user_id": user_id, "product_name": product_query},
    )
    if isinstance(result, dict) and result.get("error"):
        return []
    rows = _normalize_client_rows(result)
    return [
        row for row in rows
        if isinstance(row, dict) and row.get("id") is not None and not row.get("error")
    ]


async def _load_product(user_id: int, product_id) -> Optional[dict]:
    """Fetch one tenant-owned product row by id, or None."""
    if product_id is None:
        return None
    result = await mcp_manager.call_tool(
        "get_product_details", {"user_id": user_id, "product_id": int(product_id)}
    )
    if isinstance(result, dict) and result.get("id") is not None and not result.get("error"):
        return result
    return None


def _ambiguity_question(plan: IntentPlan, candidates: list[dict]) -> str:
    """Ask which product, using only names the database actually returned."""
    verb = {
        "delete_product": "delete",
        "update_stock": "change the stock of",
        "update_product": "change",
        "get_product_details": "show details for",
    }.get(plan.intent, "use")
    names = ", ".join(f"'{c.get('name')}'" for c in candidates[:5])
    if len(candidates) > 5:
        names += f", and {len(candidates) - 5} more"
    return (
        f"Several products match. Which one should I {verb}? "
        f"Matches: {names}."
    )


def _pending_raw(plan: IntentPlan) -> dict:
    """Raw intent persisted for a backend-raised question.

    The operation and every value already validated must be stored, otherwise
    merging a follow-up answer would lose what the user already said.
    """
    return {
        "status": "needs_clarification",
        "intent": plan.intent,
        "product_queries": list(plan.targets),
        "changes": dict(plan.changes),
    }


def _narrow_by_user_text(candidates: list[dict], user_text: str) -> Optional[dict]:
    """
    Break an ambiguity using only the user's own words.

    The model may under-specify the name ("basmati rice" for a request that
    actually said "basmati ricce 1kg"). Every candidate name is scored by how
    many of its own tokens appear in what the user typed. A candidate is only
    chosen when it is the single clear winner; a tie falls through to a question.
    """
    haystack = " ".join(re.findall(r"[a-z0-9]+", (user_text or "").lower()))
    if not haystack:
        return None
    scored = []
    for candidate in candidates:
        tokens = [t for t in re.findall(r"[a-z0-9]+", str(candidate.get("name", "")).lower()) if t]
        if not tokens:
            continue
        hits = sum(1 for token in tokens if token in haystack)
        scored.append((hits / len(tokens), candidate))
    if len(scored) < 2:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if scored[0][0] <= scored[1][0]:
        return None, scored  # type: ignore[return-value]
    return scored[0][1]


async def _execute_intent_plan(plan: IntentPlan, user_id: int, question: str) -> QueryResponse:
    """
    Resolve the target if needed, run the single action, render the result.

    Pending state is cleared here on every terminal outcome. Only a question
    leaves a pending intent behind, so a failed or completed request can never
    bleed into the user's next, unrelated message.
    """
    arguments = dict(plan.arguments)
    resolved: Optional[dict] = None

    if plan.needs_product:
        query_text = plan.targets[0] if plan.targets else ""
        candidates = await _resolve_candidates(user_id, query_text) if query_text else []
        if not candidates:
            clarification_store.clear(user_id)
            return _intent_error(
                f"No product in your inventory matches '{query_text}'. "
                "Check the name, or list your products to see what is available.",
                plan.intent,
            )
        if len(candidates) > 1:
            narrowed = _narrow_by_user_text(candidates, question)
            if isinstance(narrowed, dict):
                log.info(
                    f"[INTENT] disambiguated by user text -> id={narrowed['id']} "
                    f"name={narrowed.get('name')!r}"
                )
                candidates = [narrowed]
            else:
                clarification = Clarification(
                    question=_ambiguity_question(plan, candidates),
                    missing=["product_query"],
                )
                clarification_store.save_from_clarification(
                    user_id, question, _pending_raw(plan),
                    plan.intent, clarification,
                )
                pending = clarification_store.get(user_id)
                if pending is not None:
                    pending.candidates = candidates
                    clarification_store.save(user_id, pending)
                return _clarification_response(clarification, plan.intent)
        resolved = candidates[0]
        arguments["product_id"] = resolved["id"]

    # A destructive action is confirmed by the user before it runs, whether the
    # target was just resolved or was supplied directly.
    if plan.is_destructive and not plan.confirmed:
        if resolved is None:
            resolved = await _load_product(user_id, arguments.get("product_id"))
            if resolved is None:
                clarification_store.clear(user_id)
                return _intent_error(
                    "No product in your inventory matches that request.",
                    plan.intent,
                )
        question = (
            f"Delete '{resolved.get('name')}' from your inventory? "
            "This cannot be undone."
        )
        clarification = Clarification(question=question, missing=["confirmation"])
        clarification_store.save_from_clarification(
            user_id, question, _pending_raw(plan), plan.intent, clarification,
        )
        pending = clarification_store.get(user_id)
        if pending is not None:
            pending.stage = "confirm_delete"
            pending.resolved = dict(resolved)
            clarification_store.save(user_id, pending)
        return _clarification_response(clarification, plan.intent)

    arguments["user_id"] = user_id
    log.info(f"[INTENT] executing '{plan.tool_name}' args={_redact(arguments)}")
    exec_started = time.perf_counter()
    result = await mcp_manager.call_tool(plan.tool_name, arguments)
    exec_ms = int((time.perf_counter() - exec_started) * 1000)

    # The request is settled either way; nothing stays pending.
    clarification_store.clear(user_id)

    if isinstance(result, dict) and result.get("error"):
        error_text = str(result["error"])
        if error_text.startswith(("MCP session", "Tool call failed")):
            raise MCPUnavailableError(f"MCP tool '{plan.tool_name}' failed: {error_text}")
        _observe("execution_failed", intent=plan.intent,
                 canonical_tool=plan.tool_name, exec_ms=exec_ms)
        return _intent_error(f"{_label(plan)} failed: {error_text}", plan.intent,
                             stage="execution_failed")

    response = _render_intent_result(plan, result, resolved)
    _observe("execution_ok", intent=plan.intent, canonical_tool=plan.tool_name,
             exec_ms=exec_ms)
    return response


def _redact(arguments: dict) -> dict:
    """Never log the authenticated user id alongside a tool call."""
    return {k: v for k, v in arguments.items() if k != "user_id"}


def _label(plan: IntentPlan) -> str:
    mutation = {
        "create": "Product creation",
        "update": "Product update",
        "delete": "Product deletion",
    }
    if plan.kind in mutation:
        return mutation[plan.kind]
    return {
        "query_inventory_db": f"'{plan.targets[0]}'" if plan.targets else "your inventory",
        "get_product_details": f"'{plan.targets[0]}'" if plan.targets else "that product",
        "get_products_by_category": f"category '{plan.arguments.get('category')}'",
        "get_products_by_names": "the names you listed",
        "get_low_stock_items": f"products at or below {plan.arguments.get('threshold')}",
        "search_inventory": "your search",
    }.get(plan.intent, "your inventory")


def _render_intent_result(plan: IntentPlan, result, resolved: Optional[dict]) -> QueryResponse:
    """
    Build the user-facing answer from the committed MCP result only.

    No value in this text comes from the model, so a price or id can never be
    invented or rescaled.
    """
    tool = plan.tool_name
    label = _label(plan)

    if plan.kind in ("create", "update", "delete"):
        if not isinstance(result, dict) or result.get("success") is not True or result.get("id") is None:
            return _intent_error(f"{label} was not confirmed by the database.",
                                 plan.intent, stage="execution_unconfirmed")
        if plan.kind == "create":
            answer = (
                f"Created '{result['name']}' in {result['category']} with "
                f"stock {result['stock']}, price {_format_price(result['price'])}, "
                f"and supplier {result.get('supplier')}."
            )
        elif plan.kind == "delete":
            answer = f"Deleted '{result['name']}' from your inventory."
        else:
            if tool == "update_stock":
                answer = (
                    f"Updated '{result['name']}' stock from "
                    f"{result.get('old_stock')} to {result.get('new_stock')}. "
                    f"Price remains {_format_price(result.get('price', 0))}."
                )
            else:
                before = result.get("before") or {}
                changed = []
                for field in result.get("updated_fields", []):
                    if field == "stock":
                        changed.append(f"stock {before.get('stock')} → {result.get('stock')}")
                    elif field == "price":
                        changed.append(
                            f"price {_format_price(before.get('price', 0))}"
                            f" → {_format_price(result.get('price', 0))}"
                        )
                    elif field == "category":
                        changed.append(
                            f"category {before.get('category')} → {result.get('category')}"
                        )
                summary = "; ".join(changed) if changed else "already had the requested values"
                answer = f"Updated '{result['name']}': {summary}."
        if plan.note:
            answer = f"{answer} {plan.note}"
        return QueryResponse(answer=answer, tool_used=None, data=_public_rows([result]))

    # Reads: an empty dict is how get_product_details reports "not found".
    if tool == "get_product_details":
        if not isinstance(result, dict) or not result or result.get("error"):
            return _intent_error("No product matched that request.", plan.intent)
        return QueryResponse(
            answer=_format_product_read_response([result], _label(plan)),
            tool_used=None,
            data=_public_rows([result]),
        )

    rows = _normalize_client_rows(result)
    if tool == "get_all_categories":
        categories = [r.get("value", r) if isinstance(r, dict) else r for r in rows]
        if not categories:
            return QueryResponse(
                answer="You have no product categories yet.", tool_used=None, data=None,
            )
        listing = ", ".join(str(c) for c in categories)
        return QueryResponse(
            answer=f"You have {len(categories)} categories: {listing}.",
            tool_used=None,
            data=rows,
        )

    if tool == "get_inventory_analytics":
        stats = result if isinstance(result, dict) else {}
        if not stats or stats.get("total_products", 0) in (0, None):
            return QueryResponse(
                answer="Your inventory is empty, so there is nothing to summarise yet.",
                tool_used=None,
                data=rows,
            )
        answer = (
            f"You have {stats.get('total_products')} products "
            f"({stats.get('total_items')} units in total), "
            f"total inventory value {_format_price(stats.get('total_inventory_value', 0))}, "
            f"average price {_format_price(stats.get('average_price', 0))}. "
            f"Most expensive: {stats.get('most_expensive')}. "
            f"Cheapest: {stats.get('cheapest')}."
        )
        return QueryResponse(answer=answer, tool_used=None, data=rows)

    if tool == "get_category_analytics":
        if not rows:
            return QueryResponse(
                answer="You have no product categories yet.", tool_used=None, data=rows,
            )
        lines = [
            f"- {r.get('category')}: {r.get('product_count')} products, "
            f"{r.get('total_stock')} units, average price "
            f"{_format_price(r.get('avg_price', 0))}"
            for r in rows if isinstance(r, dict)
        ]
        answer = f"Breakdown across {len(rows)} categories:\n" + "\n".join(lines)
        return QueryResponse(answer=answer, tool_used=None, data=rows)

    answer = _format_product_read_response(rows, _label(plan))
    if not rows and plan.arguments.get("name"):
        answer = (
            f"No product in your inventory matches "
            f"'{plan.arguments['name']}'. Check the name or list your products."
        )
    return QueryResponse(answer=answer, tool_used=None, data=_public_rows(rows))


async def _settle_product_from_text(question: str, query_text: str, raw: dict,
                                    allowed_ids: set[int], user_id: int) -> Optional[IntentPlan]:
    """
    Settle a "which product?" question from the database instead of asking.

    The model often asks for disambiguation even when the user's own words are
    already decisive ("basmati rice 1kg"). Candidate rows come from the database
    and the tie-break uses only the user's text, so nothing is guessed.
    """
    if not query_text:
        return None
    candidates = await _resolve_candidates(user_id, query_text)
    if len(candidates) > 1:
        narrowed = _narrow_by_user_text(candidates, question)
        if not isinstance(narrowed, dict):
            return None
        candidates = [narrowed]
    if len(candidates) != 1:
        return None
    chosen = candidates[0]
    merged = dict(raw or {})
    merged["status"] = "ready"
    merged["product_queries"] = [str(chosen.get("name"))]
    merged["product_id"] = int(chosen["id"])
    settled = build_intent(merged, question)
    outcome = validate_intent(settled, question, allowed_ids=allowed_ids | {int(chosen["id"])})
    return outcome if isinstance(outcome, IntentPlan) else None


async def _run_llm_intent(question: str, user_id: int, tools: list[dict]) -> QueryResponse:
    """Full pipeline: extract -> merge -> validate -> resolve -> execute -> render."""
    available = {t.get("function", {}).get("name") for t in tools}
    if "query_inventory_db" not in available:
        raise MCPUnavailableError("Inventory MCP tools are not available.")

    started = time.perf_counter()
    pending = clarification_store.get(user_id)
    client = _get_openai_client()

    # A pending delete waits for a yes/no, not for more field values.
    if pending is not None and pending.stage == "confirm_delete":
        return await _handle_delete_confirmation(pending, question, user_id, started)

    extract_started = time.perf_counter()
    raw = await _extract_raw_intent(client, question, pending)
    extract_ms = int((time.perf_counter() - extract_started) * 1000)
    if raw is None:
        clarification_store.clear(user_id)
        return _intent_error(
            "I could not turn that into an inventory action. "
            "Try naming the product and the change, for example "
            "'set the price of Widget to 500'."
        )

    intent = build_intent(raw, question)

    # A follow-up answer completes the pending request; it never replaces it.
    # The user has however moved on if their message names a different product,
    # so an abandoned question must not absorb the new request.
    if pending is not None and pending.raw_intent:
        pending_targets = {
            (normalise_product_query(t) or "").lower()
            for t in (pending.raw_intent.get("product_queries") or [])
        } - {""}
        incoming_targets = {
            (normalise_product_query(t) or "").lower()
            for t in (intent.product_queries or [])
        } - {""}
        pending_intent = normalise_intent_name(pending.raw_intent.get("intent"))
        incoming_intent = normalise_intent_name(intent.intent)

        moved_on = bool(
            (pending_intent and incoming_intent and pending_intent != incoming_intent)
            or (pending_targets and incoming_targets
                and pending_targets.isdisjoint(incoming_targets))
        )
        if moved_on:
            _observe("pending_abandoned", pending=pending_intent,
                     incoming=incoming_intent)
            clarification_store.clear(user_id)
            pending = None
        else:
            merged = merge_intent(pending.raw_intent, raw)
            # The answer's own wording is also a source of required values.
            merged = salvage_missing_create_fields(merged, question)
            intent = build_intent(merged, question)
            raw = merged

    candidates = list(pending.candidates) if pending is not None else []
    allowed_ids = {int(c["id"]) for c in candidates if c.get("id") is not None}

    if candidates and intent.select_candidate:
        wanted = intent.select_candidate.strip().lower()
        for candidate in candidates:
            if str(candidate.get("name", "")).strip().lower() == wanted:
                intent.product_id = int(candidate["id"])
                intent.product_queries = [str(candidate.get("name"))]
                break
        else:
            return _intent_error(
                "I could not tell which of the listed products you meant. "
                "Please repeat the full product name."
            )

    # A first-turn create may also have stated values the model did not extract.
    if pending is None:
        raw = salvage_missing_create_fields(raw, question)
        intent = build_intent(raw, question)

    outcome = validate_intent(intent, question, allowed_ids=allowed_ids)
    _observe(
        "validated",
        intent=intent.intent,
        result=type(outcome).__name__,
        extract_ms=extract_ms,
    )

    if isinstance(outcome, Clarification) and intent.intent in MUTATING_INTENTS:
        # For a mutation the backend decides what is missing, never the model.
        # The model often under-reports (asking only for price while supplier
        # is also absent), which is the bug this replaces.
        forced = build_intent({**raw, "status": "ready"}, question)
        recomputed = validate_intent(forced, question, allowed_ids=allowed_ids)
        if isinstance(recomputed, Clarification):
            outcome = recomputed
            _observe("missing_recomputed", intent=intent.intent,
                     missing=list(recomputed.missing))

    if isinstance(outcome, Clarification):
        # If the only thing unclear is the product, let the database and the
        # user's own words settle it rather than asking a needless question.
        if outcome.missing == ["product_query"] and intent.product_queries:
            settled = await _settle_product_from_text(
                question, intent.product_queries[0], raw, allowed_ids, user_id
            )
            if settled is not None:
                _observe("product_settled_from_text", intent=settled.intent,
                         canonical_tool=settled.tool_name)
                outcome = settled

    if isinstance(outcome, Rejection):
        clarification_store.clear(user_id)
        return _intent_error(outcome.message, intent.intent)

    if isinstance(outcome, Clarification):
        attempts = pending.attempts if pending is not None else 0
        if attempts >= MAX_CLARIFICATIONS:
            clarification_store.clear(user_id)
            return _intent_error(
                "I still need a detail before I can continue. "
                "Please restate the request with the product name and the value to change.",
                intent.intent,
            )
        clarification_store.save_from_clarification(
            user_id, pending.original_question if pending else question,
            raw, intent.intent, outcome,
        )
        return _clarification_response(outcome, intent.intent)

    if outcome.tool_name not in available:
        clarification_store.clear(user_id)
        return _intent_error(
            f"Required MCP tool '{outcome.tool_name}' is unavailable.", intent.intent
        )

    _observe(
        "planned",
        intent=outcome.intent,
        canonical_tool=outcome.tool_name,
        model_tool=intent.tool,
        destructive=outcome.is_destructive or None,
    )

    response = await _execute_intent_plan(outcome, user_id, question)
    _observe(
        "finished",
        intent=outcome.intent,
        canonical_tool=outcome.tool_name,
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    return response


async def _handle_delete_confirmation(pending, question: str, user_id: int,
                                      started: float) -> QueryResponse:
    """Execute a resolved delete only on a clear yes; a no changes nothing."""
    name = pending.resolved.get("name") or "that product"
    if _is_negative(question):
        clarification_store.clear(user_id)
        _observe("delete_cancelled", intent=pending.intent)
        return QueryResponse(
            answer=f"Understood. I did not delete '{name}'.", tool_used=None, data=None,
        )
    if not _is_affirmative(question):
        # Not an answer: keep the pending intent and ask again, concisely.
        clarification_store.save(user_id, pending)
        _observe("delete_unanswered", intent=pending.intent)
        return QueryResponse(
            answer=(
                f"I still need a yes or no. Delete '{name}' from your inventory? "
                "This cannot be undone."
            ),
            tool_used=None,
            data=None,
        )

    arguments = {"product_id": int(pending.resolved["id"]), "user_id": user_id}
    result = await mcp_manager.call_tool("delete_product", arguments)
    if isinstance(result, dict) and result.get("error"):
        clarification_store.clear(user_id)
        _observe("delete_failed", intent=pending.intent, canonical_tool="delete_product")
        return _intent_error(
            f"Product deletion failed: {result['error']}", pending.intent
        )
    if not isinstance(result, dict) or result.get("success") is not True:
        clarification_store.clear(user_id)
        _observe("delete_unconfirmed", intent=pending.intent, canonical_tool="delete_product")
        return _intent_error("Product deletion was not confirmed by the database.",
                             pending.intent)

    clarification_store.clear(user_id)
    _observe(
        "delete_executed",
        intent=pending.intent,
        canonical_tool="delete_product",
        product_id=result.get("id"),
        total_ms=int((time.perf_counter() - started) * 1000),
    )
    return QueryResponse(
        answer=f"Deleted '{result['name']}' from your inventory.",
        tool_used=None,
        data=_public_rows([result]),
    )


async def run_query(question: str, current_user: dict) -> QueryResponse:
    """
    Execute one inventory request.

    Primary path: the LLM emits a typed intent, Python validates it, the target
    is resolved from PostgreSQL, one MCP tool runs, and the answer is rendered
    from the committed result. With USE_LLM_INTENT disabled this falls back to
    the legacy regex router plus the general Groq tool loop.
    """
    user_id = current_user["id"]
    log.info(f"[AGENT] Query from user_id={user_id}: {question!r}")

    tools = mcp_manager.get_tools()
    if not tools:
        raise MCPUnavailableError("Inventory MCP tools are not available.")

    if USE_LLM_INTENT:
        return await _run_llm_intent(question, user_id, tools)

    routed = route_inventory_request(question)
    if routed is not None:
        return await _execute_routed_request(routed, user_id, tools)

    client = _get_openai_client()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tool_used: Optional[str] = None
    data_result: Optional[list] = None
    product_data_result: Optional[list] = None
    canonical_product_tool: Optional[str] = None

    def _llm_call():
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            max_tokens=1024,
        )

    loop = asyncio.get_running_loop()
    for turn in range(MAX_TURNS):
        log.info(f"[AGENT] Turn {turn + 1}/{MAX_TURNS}")
        response = await loop.run_in_executor(None, _llm_call)
        msg = response.choices[0].message

        if not msg.tool_calls:
            if canonical_product_tool and product_data_result:
                answer = (
                    msg.content
                    if msg.content and _numeric_claims_preserved(msg.content, product_data_result)
                    else _format_product_read_response(product_data_result, "your search")
                )
                return QueryResponse(
                    answer=answer,
                    tool_used=canonical_product_tool,
                    data=data_result or product_data_result,
                )
            return QueryResponse(
                answer=msg.content or "No response generated.",
                tool_used=tool_used,
                data=data_result,
            )

        messages.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in msg.tool_calls
            ],
        })

        for tool_call in msg.tool_calls:
            name = tool_call.function.name
            if name in PRODUCT_READ_TOOLS and canonical_product_tool is not None:
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps({
                        "error": f"Product result already supplied by {canonical_product_tool}; duplicate read skipped."
                    }),
                })
                continue

            tool_used = name if tool_used is None else f"{tool_used}, {name}"
            try:
                arguments = json.loads(tool_call.function.arguments)
            except json.JSONDecodeError:
                arguments = {}
            arguments["user_id"] = user_id

            log.info(f"[AGENT] Calling tool '{name}' args={arguments}")
            result = await mcp_manager.call_tool(name, arguments)
            if isinstance(result, dict) and result.get("error"):
                error_text = str(result["error"])
                if error_text.startswith(("MCP session", "Tool call failed")):
                    raise MCPUnavailableError(f"MCP tool '{name}' failed: {error_text}")

            normalized = _normalize_client_rows(result)
            if normalized:
                data_result = (data_result or []) + normalized
            if name in PRODUCT_READ_TOOLS and canonical_product_tool is None and normalized:
                canonical_product_tool = name
                product_data_result = normalized

            llm_content = result
            if isinstance(result, list) and len(result) > 50:
                llm_content = result[:50] + [{
                    "_notice": f"Showing 50 of {len(result)} results. Remaining omitted to save context limit."
                }]
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(llm_content, default=str),
            })

    log.warning("[AGENT] Exhausted max turns without final answer")
    return QueryResponse(
        answer="I was unable to complete the query after multiple attempts.",
        tool_used=tool_used,
        data=data_result,
    )
