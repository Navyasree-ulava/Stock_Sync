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

from openai import OpenAI
from pydantic import BaseModel

from mcp_bridge.client_manager import mcp_manager

log = logging.getLogger(__name__)

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
PROVIDER = "groq"
API_KEY = GROQ_API_KEY
BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_MODEL = "openai/gpt-oss-20b"
MODEL = os.environ.get("LLM_MODEL", DEFAULT_GROQ_MODEL)
MAX_TURNS: int = int(os.environ.get("LLM_MAX_TURNS", "10"))

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
    value = re.sub(r"^(?:the|my)\s+", "", value.strip(), flags=re.IGNORECASE)
    value = re.sub(r"\s+category$", "", value, flags=re.IGNORECASE)
    value = value.strip(" ,.;:?")
    return value.title() if value.islower() else value


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


async def run_query(question: str, current_user: dict) -> QueryResponse:
    """Execute one canonical routed request or the general Groq agent loop."""
    user_id = current_user["id"]
    log.info(f"[AGENT] Query from user_id={user_id}: {question!r}")

    tools = mcp_manager.get_tools()
    if not tools:
        raise MCPUnavailableError("Inventory MCP tools are not available.")

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
