"""
server.py — StockSync MCP Server

Exposes all inventory tools via JSON-RPC.
ALL queries are scoped to the authenticated user's data via user_id.
Runs as a standalone HTTP service (port 8001) by default,
or stdio (for Claude Desktop / FastAPI subprocess) when MCP_TRANSPORT=stdio.

Database: PostgreSQL via SQLAlchemy (backend/db/connection.py + models.py)
"""

import os
import sys
import logging
import math
import difflib
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# ─── Path bootstrap ──────────────────────────────────────────
# Allow imports from backend/db/ regardless of cwd
_ROOT = Path(__file__).parent.parent / "backend"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
load_dotenv(Path(__file__).parent.parent / ".env")

from mcp.server.fastmcp import FastMCP
from sqlalchemy import delete, func, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from db.connection import SessionLocal
from db.models import Product, StockAuditLog, User

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MCP-SERVER] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Config ──────────────────────────────────────────────────
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", "8001"))

mcp = FastMCP(
    "StockSync Inventory Server",
    host=MCP_HOST,
    port=MCP_PORT,
    streamable_http_path="/mcp",
)


# ─── DB Helpers ──────────────────────────────────────────────
def _get_db() -> Session:
    return SessionLocal()


def _serialize(p: Product) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "category": p.category,
        "stock": p.stock,
        "price": float(p.price),
        "supplier": p.supplier,
    }


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# A fuzzy candidate must score at least this high to be considered at all.
_FUZZY_FLOOR = 0.72
# The winner must beat the runner-up by this much, otherwise the name is
# reported as ambiguous instead of silently picking one product.
_FUZZY_MARGIN = 0.05

# products.stock is a 4-byte integer column. Reject out-of-range values up
# front so the caller gets a clear message instead of a database error.
_MAX_STOCK = 2_147_483_647
# Matches the REST ingest layer so a price entered by chat and by the UI
# are stored identically.
_PRICE_DECIMALS = 2
# Upper bound for a single page of results.
_MAX_LIMIT = 10_000


def _validate_stock(value, field: str = "new_stock") -> tuple[Optional[int], Optional[str]]:
    """Return (stock, error). Rejects non-integers and out-of-range values."""
    if value is None:
        return None, None
    if isinstance(value, bool):
        return None, f"{field} must be a whole number."
    if isinstance(value, float):
        if not math.isfinite(value) or not float(value).is_integer():
            return None, f"{field} must be a whole number."
        value = int(value)
    if not isinstance(value, int):
        return None, f"{field} must be a whole number."
    if value < 0:
        return None, f"{field} cannot be negative."
    if value > _MAX_STOCK:
        return None, f"{field} must be {_MAX_STOCK} or less."
    return value, None


def _validate_price(value, field: str = "new_price") -> tuple[Optional[float], Optional[str]]:
    """Return (rounded_price, error). Rejects non-finite and negative prices."""
    if value is None:
        return None, None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None, f"{field} must be a number."
    if not math.isfinite(float(value)):
        return None, f"{field} must be a finite number."
    if float(value) < 0:
        return None, f"{field} cannot be negative."
    return round(float(value), _PRICE_DECIMALS), None


def _name_similarity(query: str, candidate: str) -> float:
    """
    Token-aware similarity in [0, 1].

    Blends a whole-string ratio with a per-token score so that a differing
    size or variant token ("1kg" vs "5kg") separates near-identical names,
    while a single misspelled token ("ricce" vs "rice") still matches.
    """
    whole = difflib.SequenceMatcher(None, query, candidate).ratio()
    query_tokens = query.split()
    candidate_tokens = candidate.split()
    if not query_tokens or not candidate_tokens:
        return whole
    token_total = 0.0
    for token in query_tokens:
        token_total += max(
            difflib.SequenceMatcher(None, token, other).ratio()
            for other in candidate_tokens
        )
    token_score = token_total / len(query_tokens)
    return (whole + token_score) / 2


def _resolve_product(db: Session, user_id: int, product_name: str, supplier: Optional[str] = None) -> list[Product]:
    """Resolve exact, partial, then unique fuzzy product-name matches."""
    normalized_name = product_name.strip()
    query = db.query(Product).filter(Product.user_id == user_id)
    if supplier:
        query = query.filter(
            Product.supplier.ilike(f"%{_escape_like(supplier.strip())}%", escape="\\")
        )

    exact_matches = query.filter(
        func.lower(Product.name) == normalized_name.lower()
    ).all()
    if exact_matches:
        return exact_matches

    partial_matches = query.filter(
        Product.name.ilike(f"%{_escape_like(normalized_name)}%", escape="\\")
    ).all()
    if partial_matches:
        return partial_matches

    candidates = query.all()
    scored = [
        (_name_similarity(normalized_name.lower(), product.name.lower()), product)
        for product in candidates
    ]
    scored = [pair for pair in scored if pair[0] >= _FUZZY_FLOOR]
    if not scored:
        return []

    scored.sort(key=lambda pair: pair[0], reverse=True)
    best_score, best_product = scored[0]
    if len(scored) == 1:
        return [best_product]

    runner_up = scored[1][0]
    # Only resolve when the winner is clearly better than the next candidate.
    # "basmati ricce 1kg" must pick 1kg over 5kg, while a bare
    # "basmati rice" stays ambiguous instead of guessing.
    if best_score - runner_up < _FUZZY_MARGIN:
        tied = [product for score, product in scored if best_score - score < _FUZZY_MARGIN]
        return tied
    return [best_product]


# ─────────────────────────────────────────────────────────────
# Tool 1: Search by name (partial match)
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def query_inventory_db(user_id: int, product_name: str) -> list[dict]:
    """
    Search inventory for products whose name contains the given string.
    Only returns products belonging to the authenticated user.
    Use this when the user asks about a specific product by name.
    Returns a list of matching product records (empty list = not found).
    """
    log.info(f"[DB] query_inventory_db | user_id={user_id} name LIKE '%{product_name}%'")
    db = _get_db()
    try:
        products = (
            db.query(Product)
            .filter(
                Product.user_id == user_id,
                Product.name.ilike(f"%{_escape_like(product_name)}%", escape="\\"),
            )
            .order_by(Product.name)
            .all()
        )
        if not products:
            # Fallback to case-insensitive fuzzy match on typos using difflib
            import difflib
            all_prods = db.query(Product).filter(Product.user_id == user_id).all()
            if all_prods:
                lower_product_name = product_name.lower().strip()
                name_map = {p.name.lower().strip(): p.name for p in all_prods}
                matches = difflib.get_close_matches(lower_product_name, list(name_map.keys()), n=3, cutoff=0.5)
                if matches:
                    original_matched_names = [name_map[m] for m in matches]
                    products = (
                        db.query(Product)
                        .filter(
                            Product.user_id == user_id,
                            Product.name.in_(original_matched_names),
                        )
                        .order_by(Product.name)
                        .all()
                    )

        result = [_serialize(p) for p in products]
        log.info(f"[DB] query_inventory_db | {len(result)} match(es) for '{product_name}'")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 2: Get product by numeric ID
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_product_details(user_id: int, product_id: int) -> dict:
    """
    Retrieve the complete record for a single product by its numeric ID.
    Only returns the product if it belongs to the authenticated user.
    Returns an empty dict {} if the product is not found.
    """
    log.info(f"[DB] get_product_details | user_id={user_id} product_id={product_id}")
    db = _get_db()
    try:
        product = (
            db.query(Product)
            .filter(Product.id == product_id, Product.user_id == user_id)
            .first()
        )
        if product:
            log.info(f"[DB] get_product_details | found: '{product.name}'")
            return _serialize(product)
        log.warning(f"[DB] get_product_details | no product id={product_id} for user_id={user_id}")
        return {}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 3: Create a product
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def create_product(
    user_id: int,
    name: str,
    category: str,
    stock: int,
    price: float,
    supplier: str = "Unknown",
) -> dict:
    """Insert exactly one product for the authenticated user's inventory."""
    normalized_name = (name or "").strip()
    normalized_category = (category or "General").strip() or "General"
    normalized_supplier = (supplier or "Unknown").strip() or "Unknown"

    if not normalized_name:
        return {"error": "Product name is required."}
    stock_value, stock_error = _validate_stock(stock, "stock")
    if stock_error:
        return {"error": stock_error}
    price_value, price_error = _validate_price(price, "price")
    if price_error:
        return {"error": price_error}

    normalized_name = normalized_name[:255]
    normalized_category = normalized_category[:100]
    normalized_supplier = normalized_supplier[:255]

    db = _get_db()
    try:
        # Serialize concurrent creates for this tenant and reject an exact-name retry.
        db.query(User.id).filter(User.id == user_id).with_for_update().one()
        existing = db.query(Product.id).filter(
            Product.user_id == user_id,
            func.lower(Product.name) == normalized_name.lower(),
        ).first()
        if existing:
            return {"error": f"A product named '{normalized_name}' already exists."}

        stmt = (
            insert(Product)
            .values(
                user_id=user_id,
                name=normalized_name,
                category=normalized_category,
                stock=int(stock_value),
                price=float(price_value),
                supplier=normalized_supplier,
            )
            .returning(Product.id)
        )
        product_id = db.execute(stmt).scalar_one()
        db.commit()
        product = db.get(Product, product_id)
        if product is None:
            db.rollback()
            return {"error": "Product insert could not be verified."}

        result = _serialize(product)
        result.update({
            "success": True,
            "message": f"Created '{product.name}' with ID {product.id}.",
        })
        log.info(f"[DB] create_product | SUCCESS user_id={user_id} id={product_id} name='{product.name}'")
        return result
    except SQLAlchemyError as exc:
        db.rollback()
        log.error(f"[DB] create_product failed for user_id={user_id}: {exc}", exc_info=True)
        return {"error": "Product could not be created."}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 4: Advanced search with filters & sorting
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def search_inventory(
    user_id: int,
    name: Optional[str] = None,
    category: Optional[str] = None,
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    stock_threshold: Optional[int] = None,
    sort_by: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict]:
    """
    Search products with optional filters and sorting. Only searches the authenticated user's inventory.
    - name: partial name match
    - category: partial category match
    - max_price / min_price: price range filter
    - stock_threshold: return items with stock <= this value
    - sort_by: price_asc | price_desc | stock_asc | stock_desc
    - limit: maximum number of products to return (between 1 and 10000)
    - offset: starting offset for pagination (0 or greater)
    Returns empty list if no products match.
    Raises ValueError for an out-of-range limit/offset or a non-finite price.
    """
    log.info(
        f"[DB] search_inventory | user_id={user_id} name={name!r} category={category!r} "
        f"price=[{min_price},{max_price}] stock_threshold={stock_threshold} sort={sort_by} "
        f"limit={limit} offset={offset}"
    )
    # This tool is declared -> list[dict]; FastMCP validates the return type,
    # so an invalid argument must be raised rather than returned as a dict.
    if limit < 1 or limit > _MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_MAX_LIMIT}.")
    if offset < 0:
        raise ValueError("offset cannot be negative.")
    if min_price is not None and not math.isfinite(float(min_price)):
        raise ValueError("min_price must be a finite number.")
    if max_price is not None and not math.isfinite(float(max_price)):
        raise ValueError("max_price must be a finite number.")
    db = _get_db()
    try:
        query = db.query(Product).filter(Product.user_id == user_id)

        if name:
            query = query.filter(Product.name.ilike(f"%{_escape_like(name)}%", escape="\\"))
        if category:
            query = query.filter(Product.category.ilike(f"%{_escape_like(category)}%", escape="\\"))
        if max_price is not None:
            query = query.filter(Product.price <= max_price)
        if min_price is not None:
            query = query.filter(Product.price >= min_price)
        if stock_threshold is not None:
            query = query.filter(Product.stock <= stock_threshold)

        sort_map = {
            "price_asc":  Product.price.asc(),
            "price_desc": Product.price.desc(),
            "stock_asc":  Product.stock.asc(),
            "stock_desc": Product.stock.desc(),
        }
        order = sort_map.get(sort_by, Product.name.asc())
        products = query.order_by(order).offset(offset).limit(limit).all()

        result = [_serialize(p) for p in products]
        log.info(f"[DB] search_inventory | {len(result)} result(s)")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 4: Low-stock alert
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_low_stock_items(user_id: int, threshold: int = 10) -> list[dict]:
    """
    Return all products whose current stock is below the given threshold.
    Only returns products belonging to the authenticated user.
    Default threshold is 10 units. Ordered by stock ascending.
    """
    log.info(f"[DB] get_low_stock_items | user_id={user_id} stock < {threshold}")
    db = _get_db()
    try:
        products = (
            db.query(Product)
            .filter(Product.user_id == user_id, Product.stock < threshold)
            .order_by(Product.stock.asc())
            .all()
        )
        result = [_serialize(p) for p in products]
        log.info(f"[DB] get_low_stock_items | {len(result)} item(s) below threshold")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 5: All distinct categories
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_all_categories(user_id: int) -> list[str]:
    """
    Return a sorted list of all distinct product categories for the authenticated user.
    Use this when the user asks what categories are available.
    """
    log.info(f"[DB] get_all_categories | user_id={user_id}")
    db = _get_db()
    try:
        rows = (
            db.query(Product.category)
            .filter(Product.user_id == user_id)
            .distinct()
            .order_by(Product.category)
            .all()
        )
        result = [r[0] for r in rows]
        log.info(f"[DB] get_all_categories | {len(result)} categories")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 6: Products by category
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_products_by_category(user_id: int, category: str) -> list[dict]:
    """
    Return all products in a specific category (case-insensitive partial match).
    Only returns products belonging to the authenticated user.
    """
    log.info(f"[DB] get_products_by_category | user_id={user_id} category='{category}'")
    db = _get_db()
    try:
        products = (
            db.query(Product)
            .filter(
                Product.user_id == user_id,
                Product.category.ilike(f"%{_escape_like(category)}%", escape="\\"),
            )
            .order_by(Product.name)
            .all()
        )
        result = [_serialize(p) for p in products]
        log.info(f"[DB] get_products_by_category | {len(result)} product(s)")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 6.5: Products by a list of exact or partial names
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_products_by_names(user_id: int, names: list[str]) -> list[dict]:
    """
    Retrieve products that match any of the names in the provided list.
    Only returns products belonging to the authenticated user.
    Use this when you need to fetch semantic subsets (e.g., 'only fruits' or 'only vegetables')
    from a combined category by providing a list of all known fruit/vegetable names.
    """
    log.info(f"[DB] get_products_by_names | user_id={user_id} names={names}")
    # An empty pattern would match every product, so drop blank entries
    # instead of silently returning the whole inventory.
    cleaned = [n for n in (names or []) if isinstance(n, str) and n.strip()]
    if not cleaned:
        return []

    db = _get_db()
    try:
        from sqlalchemy import or_
        conditions = [Product.name.ilike(f"%{_escape_like(n.strip())}%", escape="\\") for n in cleaned]
        products = (
            db.query(Product)
            .filter(Product.user_id == user_id, or_(*conditions))
            .order_by(Product.name)
            .all()
        )
        result = [_serialize(p) for p in products]
        log.info(f"[DB] get_products_by_names | {len(result)} product(s)")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 7: Inventory analytics (totals + extremes)
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_inventory_analytics(user_id: int) -> dict:
    """
    High-level inventory statistics for the authenticated user:
    total products, total stock, total value, average price,
    most expensive and cheapest product.
    """
    log.info(f"[DB] get_inventory_analytics | user_id={user_id}")
    db = _get_db()
    try:
        stats = (
            db.query(
                func.count(Product.id).label("total_products"),
                func.coalesce(func.sum(Product.stock), 0).label("total_items"),
                func.sum(Product.price * Product.stock).label("total_inventory_value"),
                func.avg(Product.price).label("average_price"),
            )
            .filter(Product.user_id == user_id)
            .first()
        )

        # Get extremes in separate queries for clarity
        most_expensive = (
            db.query(Product.name)
            .filter(Product.user_id == user_id)
            .order_by(Product.price.desc())
            .limit(1)
            .scalar()
        )
        cheapest = (
            db.query(Product.name)
            .filter(Product.user_id == user_id)
            .order_by(Product.price.asc())
            .limit(1)
            .scalar()
        )

        result = {
            "total_products": stats.total_products if stats else 0,
            "total_items": int(stats.total_items) if stats else 0,
            "total_inventory_value": float(stats.total_inventory_value) if stats and stats.total_inventory_value else 0.0,
            "average_price": float(stats.average_price) if stats and stats.average_price else 0.0,
            "most_expensive": most_expensive or "N/A",
            "cheapest": cheapest or "N/A",
        }
        log.info(
            f"[DB] get_inventory_analytics | {result['total_products']} products, "
            f"value={result['total_inventory_value']}"
        )
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool 8: Per-category analytics
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def get_category_analytics(user_id: int) -> list[dict]:
    """
    Per-category breakdown for the authenticated user:
    product count, total stock, avg price.
    Ordered by product count descending.
    """
    log.info(f"[DB] get_category_analytics | user_id={user_id}")
    db = _get_db()
    try:
        rows = (
            db.query(
                Product.category,
                func.count(Product.id).label("product_count"),
                func.coalesce(func.sum(Product.stock), 0).label("total_stock"),
                func.avg(Product.price).label("avg_price"),
            )
            .filter(Product.user_id == user_id)
            .group_by(Product.category)
            .order_by(func.count(Product.id).desc())
            .all()
        )
        result = [
            {
                "category": r.category,
                "product_count": r.product_count,
                "total_stock": int(r.total_stock),
                "avg_price": round(float(r.avg_price), 2) if r.avg_price else 0.0,
            }
            for r in rows
        ]
        log.info(f"[DB] get_category_analytics | {len(result)} categories")
        return result
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool: Delete a product
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def delete_product(
    user_id: int,
    product_id: Optional[int] = None,
    product_name: Optional[str] = None,
    supplier: Optional[str] = None,
) -> dict:
    """Delete exactly one product owned by the authenticated user."""
    db = _get_db()
    try:
        db.query(User.id).filter(User.id == user_id).with_for_update().one()
        target: Optional[Product] = None

        if product_id is not None:
            target = db.query(Product).filter(
                Product.id == product_id,
                Product.user_id == user_id,
            ).first()
        elif product_name:
            normalized_name = product_name.strip()
            if normalized_name.isdigit():
                return {"error": "A numeric product name is ambiguous; provide product_id instead."}
            query = db.query(Product).filter(Product.user_id == user_id)
            if supplier:
                query = query.filter(
                    Product.supplier.ilike(f"%{_escape_like(supplier.strip())}%", escape="\\")
                )
            exact_matches = query.filter(
                func.lower(Product.name) == normalized_name.lower()
            ).all()
            matches = exact_matches or query.filter(
                Product.name.ilike(f"%{_escape_like(normalized_name)}%", escape="\\")
            ).all()
            if len(matches) == 1:
                target = matches[0]
            elif len(matches) > 1:
                return {
                    "error": f"Multiple products match '{product_name}'.",
                    "matches": [_serialize(product) for product in matches],
                }
        else:
            return {"error": "Provide product_id or product_name."}

        if target is None:
            return {"error": "Product not found in your inventory."}

        product_data = _serialize(target)
        stmt = delete(Product).where(
            Product.id == target.id,
            Product.user_id == user_id,
        ).returning(Product.id)
        deleted_id = db.execute(stmt).scalar_one_or_none()
        if deleted_id is None:
            db.rollback()
            return {"error": "Product delete could not be verified."}
        db.commit()

        product_data.update({
            "success": True,
            "message": f"Deleted '{product_data['name']}' (ID {product_data['id']}).",
            "deleted_id": deleted_id,
        })
        log.info(f"[DB] delete_product | SUCCESS user_id={user_id} id={deleted_id}")
        return product_data
    except SQLAlchemyError as exc:
        db.rollback()
        log.error(f"[DB] delete_product failed for user_id={user_id}: {exc}", exc_info=True)
        return {"error": "Product could not be deleted."}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool: Update product fields (stock, price, category)
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def update_product(
    user_id: int,
    product_id: Optional[int] = None,
    product_name: Optional[str] = None,
    supplier: Optional[str] = None,
    new_stock: Optional[int] = None,
    new_price: Optional[float] = None,
    new_category: Optional[str] = None,
) -> dict:
    """Update one or more fields on exactly one tenant-owned product."""
    if new_stock is None and new_price is None and new_category is None:
        return {"error": "Provide at least one new stock, price, or category value."}
    stock_value, stock_error = _validate_stock(new_stock, "new_stock")
    if stock_error:
        return {"error": stock_error}
    price_value, price_error = _validate_price(new_price, "new_price")
    if price_error:
        return {"error": price_error}
    if new_category is not None and not new_category.strip():
        return {"error": "Category cannot be empty."}

    db = _get_db()
    try:
        db.query(User.id).filter(User.id == user_id).with_for_update().one()
        target: Optional[Product] = None
        if product_id is not None:
            target = db.query(Product).filter(
                Product.id == product_id,
                Product.user_id == user_id,
            ).first()
        elif product_name:
            if product_name.strip().isdigit():
                return {"error": "A numeric product name is ambiguous; provide product_id instead."}
            matches = _resolve_product(db, user_id, product_name, supplier)
            if len(matches) > 1:
                return {
                    "error": f"Multiple products match '{product_name}'.",
                    "matches": [_serialize(product) for product in matches],
                }
            if matches:
                target = matches[0]
        else:
            return {"error": "Provide product_id or product_name."}

        if target is None:
            return {"error": "Product not found in your inventory."}

        before = _serialize(target)
        updated_fields = []
        if stock_value is not None and stock_value != target.stock:
            target.stock = int(stock_value)
            updated_fields.append("stock")
        if price_value is not None and price_value != float(target.price):
            target.price = float(price_value)
            updated_fields.append("price")
        if new_category is not None and new_category.strip() != target.category:
            target.category = new_category.strip()[:100]
            updated_fields.append("category")

        if not updated_fields:
            return {
                **_serialize(target),
                "success": True,
                "updated_fields": [],
                "message": f"'{target.name}' already has the requested values.",
            }

        if "stock" in updated_fields:
            db.add(StockAuditLog(
                user_id=user_id,
                product_id=target.id,
                old_stock=before["stock"],
                new_stock=target.stock,
                action="ai_update",
            ))

        db.commit()
        db.refresh(target)
        result = _serialize(target)
        log.info(
            f"[DB] update_product | SUCCESS user_id={user_id} id={target.id} "
            f"fields={updated_fields}"
        )
        return {
            **result,
            "success": True,
            "updated_fields": updated_fields,
            "before": before,
            "message": f"Updated '{target.name}' ({', '.join(updated_fields)}).",
        }
    except SQLAlchemyError as exc:
        db.rollback()
        log.error(f"[DB] update_product failed for user_id={user_id}: {exc}", exc_info=True)
        return {"error": "Product could not be updated."}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────
# Tool: Update product stock level (Write Capability)
# ─────────────────────────────────────────────────────────────
@mcp.tool()
def update_stock(
    user_id: int,
    new_quantity: int,
    product_id: Optional[int] = None,
    product_name: Optional[str] = None,
    supplier: Optional[str] = None,
) -> dict:
    """
    Update the stock level for a product owned by the authenticated user.
    You can provide either the numeric product_id OR the product_name
    (and optionally supplier to narrow it down).
    SECURITY: Only modifies products that belong to the current user (user_id).
    Use this when the user mentions receiving a shipment, selling items, or correcting stock levels.
    """
    log.info(f"[DB] update_stock | user_id={user_id} id={product_id} name={product_name} new_qty={new_quantity}")
    stock_value, stock_error = _validate_stock(new_quantity, "new_quantity")
    if stock_error:
        return {"error": stock_error}

    db = _get_db()
    try:
        target: Optional[Product] = None

        if product_id is not None:
            target = (
                db.query(Product)
                .filter(Product.id == product_id, Product.user_id == user_id)
                .first()
            )
            if not target:
                log.warning(f"[DB] update_stock | product id={product_id} not found for user_id={user_id}")
                return {"error": f"Product with ID {product_id} not found in your inventory."}

        elif product_name is not None:
            if product_name.strip().isdigit():
                return {"error": "A numeric product name is ambiguous; provide product_id instead."}
            matches = _resolve_product(db, user_id, product_name, supplier)

            if len(matches) == 0:
                return {"error": f"No products found matching name '{product_name}' in your inventory."}
            elif len(matches) > 1:
                return {
                    "error": f"Multiple products match '{product_name}'. Please be more specific or provide a supplier.",
                    "matches": [_serialize(p) for p in matches],
                }
            target = matches[0]

        else:
            return {"error": "You must provide either product_id or product_name."}

        old_stock = target.stock
        target.stock = stock_value

        # Write audit log
        audit = StockAuditLog(
            user_id=user_id,
            product_id=target.id,
            old_stock=old_stock,
            new_stock=stock_value,
            action="ai_update",
        )
        db.add(audit)
        db.commit()
        db.refresh(target)

        product_data = _serialize(target)
        log.info(f"[DB] update_stock | SUCCESS: '{target.name}' {old_stock} → {stock_value}")
        return {
            **product_data,
            "success": True,
            "message": f"Updated '{target.name}' stock to {stock_value}.",
            "product_id": target.id,
            "old_stock": old_stock,
            "new_stock": stock_value,
        }
    except SQLAlchemyError as exc:
        db.rollback()
        log.error(f"[DB] update_stock failed for user_id={user_id}: {exc}", exc_info=True)
        return {"error": "Product stock could not be updated."}
    finally:
        db.close()


# ─── Entry point ──────────────────────────────────────────────
if __name__ == "__main__":
    transport = os.getenv("MCP_TRANSPORT", "http")
    if transport == "stdio":
        log.info("Starting MCP server — stdio transport (subprocess mode)")
        mcp.run(transport="stdio")
    else:
        log.info(f"Starting MCP server — streamable-http on {MCP_HOST}:{MCP_PORT}/mcp")
        mcp.run(transport="streamable-http")
