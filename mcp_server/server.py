"""
server.py — StockQuery AI MCP Server

Exposes all inventory tools via JSON-RPC.
ALL queries are scoped to the authenticated user's data via user_id.
Runs as a standalone HTTP service (port 8001) by default,
or stdio (for Claude Desktop / FastAPI subprocess) when MCP_TRANSPORT=stdio.

Database: PostgreSQL via SQLAlchemy (backend/db/connection.py + models.py)
"""

import os
import sys
import logging
from pathlib import Path
from typing import Optional

# ─── Path bootstrap ──────────────────────────────────────────
# Allow imports from backend/db/ regardless of cwd
_ROOT = Path(__file__).parent.parent / "backend"
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP
from sqlalchemy import func
from sqlalchemy.orm import Session

from db.connection import SessionLocal
from db.models import Product, StockAuditLog

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
    "StockQuery Inventory Server",
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
                Product.name.ilike(f"%{product_name}%"),
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
# Tool 3: Advanced search with filters & sorting
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
    - limit: maximum number of products to return (pagination)
    - offset: starting offset for pagination
    Returns empty list if no products match.
    """
    log.info(
        f"[DB] search_inventory | user_id={user_id} name={name!r} category={category!r} "
        f"price=[{min_price},{max_price}] stock_threshold={stock_threshold} sort={sort_by} "
        f"limit={limit} offset={offset}"
    )
    db = _get_db()
    try:
        query = db.query(Product).filter(Product.user_id == user_id)

        if name:
            query = query.filter(Product.name.ilike(f"%{name}%"))
        if category:
            query = query.filter(Product.category.ilike(f"%{category}%"))
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
            .filter(Product.user_id == user_id, Product.category.ilike(f"%{category}%"))
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
    if not names:
        return []

    db = _get_db()
    try:
        from sqlalchemy import or_
        conditions = [Product.name.ilike(f"%{n}%") for n in names]
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
# Tool 9: Update product stock level (Write Capability)
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
            query = db.query(Product).filter(
                Product.user_id == user_id,
                Product.name.ilike(f"%{product_name}%"),
            )
            if supplier:
                query = query.filter(Product.supplier.ilike(f"%{supplier}%"))

            matches = query.all()

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
        target.stock = new_quantity

        # Write audit log
        audit = StockAuditLog(
            user_id=user_id,
            product_id=target.id,
            old_stock=old_stock,
            new_stock=new_quantity,
            action="ai_update",
        )
        db.add(audit)
        db.commit()

        log.info(f"[DB] update_stock | SUCCESS: '{target.name}' {old_stock} → {new_quantity}")
        return {
            "success": True,
            "message": f"Updated '{target.name}' stock to {new_quantity}.",
            "product_id": target.id,
            "old_stock": old_stock,
            "new_stock": new_quantity,
        }
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
