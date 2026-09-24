"""
seed_db.py — Seed PostgreSQL with demo user and inventory products.

Creates:
  - Demo user: demo@stockquery.ai / demo123
  - 1000+ products from inventory_1000.csv, all assigned to demo user

Run: python backend/seed_db.py [path/to/products.csv]
Default CSV: backend/inventory_1000.csv
"""

import os
import sys
import csv
import logging
from pathlib import Path

# Bootstrap path so we can import from backend/db/
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from db.connection import SessionLocal, engine
from db.models import Base, User, Product, StockAuditLog, ChatHistory

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SEED] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── Demo credentials ─────────────────────────────────────────
DEMO_EMAIL    = "demo@stockquery.ai"
DEMO_PASSWORD = "demo123"

# ─── CSV column aliases ───────────────────────────────────────
COL_MAP = {
    "name":     ["name", "product_name", "item_name", "product", "item", "description"],
    "category": ["category", "catagory", "category_name", "type", "department", "section"],
    "stock":    ["stock", "quantity", "stock_quantity", "qty", "units", "inventory"],
    "price":    ["price", "unit_price", "selling_price", "mrp", "cost"],
    "supplier": ["supplier", "supplier_name", "vendor", "brand", "manufacturer", "source"],
}


def _hash_password(plain: str) -> str:
    """Hash a plain-text password using bcrypt directly."""
    import bcrypt
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def ensure_tables() -> None:
    """Create all tables and apply migrations if they exist."""
    from db.migrations import run_migrations
    run_migrations(engine)


def get_or_create_demo_user(db) -> User:
    """Return existing demo user or create a new one, ensuring the password is correct."""
    hashed = _hash_password(DEMO_PASSWORD)
    user = db.query(User).filter(User.email == DEMO_EMAIL).first()
    if user:
        log.info(f"Demo user exists, updating password hash to ensure correctness: email={user.email}")
        user.hashed_password = hashed
        user.full_name = "Demo User"
        user.business_name = "Apex Grocery Store"
        db.commit()
        db.refresh(user)
        return user

    user = User(
        full_name="Demo User",
        email=DEMO_EMAIL,
        business_name="Apex Grocery Store",
        hashed_password=hashed,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    log.info(f"Created demo user: id={user.id} email={user.email}")
    return user


def clear_user_products(db, user_id: int) -> None:
    """Delete all existing products for this user before re-seeding."""
    deleted = db.query(Product).filter(Product.user_id == user_id).delete()
    db.commit()
    log.info(f"Cleared {deleted} existing products for user_id={user_id}")


def _resolve_columns(headers: list[str]) -> dict:
    """Map our field names to actual CSV column names."""
    resolved = {}
    lower_headers = [h.lower().strip() for h in headers]
    for field, candidates in COL_MAP.items():
        for c in candidates:
            if c in lower_headers:
                resolved[field] = c
                break
    return resolved


def load_from_csv(db, user_id: int, csv_path: str) -> int:
    """
    Load products from CSV into PostgreSQL, all tagged with user_id.
    Returns number of rows inserted.
    """
    if not os.path.exists(csv_path):
        log.error(f"CSV not found: {csv_path}")
        return 0

    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        resolved = _resolve_columns(headers)

        if "name" not in resolved:
            log.error(f"Cannot find a 'name' column in CSV. Headers: {headers}")
            return 0

        batch = []
        skipped = 0

        for row in reader:
            lower_row = {k.lower().strip(): v for k, v in row.items()}
            try:
                name = str(lower_row.get(resolved.get("name", ""), "")).strip()[:200]
                if not name:
                    skipped += 1
                    continue

                category = str(lower_row.get(resolved.get("category", ""), "General")).strip()[:100] or "General"
                stock_raw = lower_row.get(resolved.get("stock", ""), "0") or "0"
                stock = int(float(stock_raw))

                price_raw = str(lower_row.get(resolved.get("price", ""), "0.0")).replace("$", "").replace(",", "").strip()
                price = round(float(price_raw or "0.0"), 2)

                supplier = str(lower_row.get(resolved.get("supplier", ""), "Unknown")).strip()[:100] or "Unknown"

                batch.append(Product(
                    user_id=user_id,
                    name=name,
                    category=category,
                    stock=stock,
                    price=price,
                    supplier=supplier,
                ))
            except (ValueError, KeyError):
                skipped += 1
                continue

        db.bulk_save_objects(batch)
        db.commit()
        log.info(f"Inserted {len(batch)} products (skipped {skipped} malformed rows).")
        return len(batch)


BUILTIN_PRODUCTS = [
    # (name, category, stock, price, supplier)
    ("Whole Milk 1L",         "Dairy",         4,    55.00, "FreshFarm Ltd."),
    ("Cheddar Cheese 200g",   "Dairy",        18,   185.00, "DairyBest Co."),
    ("Unsalted Butter 500g",  "Dairy",         7,   130.00, "FreshFarm Ltd."),
    ("Greek Yogurt 400g",     "Dairy",        22,    90.00, "NutriDairy Pvt."),
    ("Paneer 250g",           "Dairy",         3,   110.00, "LocalFresh Farms"),
    ("Basmati Rice 1kg",      "Grains",        5,   120.00, "AgroSupply Co."),
    ("Whole Wheat Flour 1kg", "Grains",       35,    65.00, "GrainMaster Ltd."),
    ("Rolled Oats 500g",      "Grains",       50,    85.00, "HealthGrain Inc."),
    ("USB-C Hub 7-Port",      "Electronics",  12,  1499.00, "TechZone India"),
    ("Bluetooth Speaker 20W", "Electronics",  45,  2299.00, "SoundMax Pvt."),
    ("Classic Potato Chips",  "Snacks",       80,    35.00, "SnackWorld Pvt."),
    ("Digestive Biscuits",    "Snacks",       60,    55.00, "BakeBest Co."),
    ("Anti-Dandruff Shampoo", "Personal Care",15,   299.00, "CleanCare Pvt."),
    ("Moisturizing Soap",     "Personal Care",70,    55.00, "PureGlow Ltd."),
]


def load_builtin(db, user_id: int) -> int:
    objects = [
        Product(user_id=user_id, name=n, category=c, stock=s, price=p, supplier=sup)
        for n, c, s, p, sup in BUILTIN_PRODUCTS
    ]
    db.bulk_save_objects(objects)
    db.commit()
    return len(objects)


def print_summary(db, user_id: int) -> None:
    from sqlalchemy import func
    rows = (
        db.query(Product.category, func.count(Product.id))
        .filter(Product.user_id == user_id)
        .group_by(Product.category)
        .order_by(Product.category)
        .all()
    )
    total = db.query(Product).filter(Product.user_id == user_id).count()
    print("\n── Inventory Summary ────────────────────────")
    for cat, cnt in rows:
        print(f"   {cat:30s} {cnt:>4} products")
    print(f"   {'TOTAL':30s} {total:>4} products")
    print("─────────────────────────────────────────────\n")


def main() -> None:
    ensure_tables()

    db = SessionLocal()
    try:
        demo_user = get_or_create_demo_user(db)
        clear_user_products(db, demo_user.id)

        # Determine CSV path
        default_csv = Path(__file__).parent / "inventory_1000.csv"
        csv_path = sys.argv[1] if len(sys.argv) > 1 else str(default_csv)

        count = load_from_csv(db, demo_user.id, csv_path)

        if count == 0:
            log.warning("CSV load failed or empty — falling back to built-in seed data.")
            count = load_builtin(db, demo_user.id)
            log.info(f"Seeded {count} built-in products.")
        else:
            log.info(f"Seeded {count} products from: {csv_path}")

        print_summary(db, demo_user.id)

        print("╔══════════════════════════════════════════╗")
        print("║        Demo Credentials                  ║")
        print("╠══════════════════════════════════════════╣")
        print(f"║  Email   : {DEMO_EMAIL:<30} ║")
        print(f"║  Password: {DEMO_PASSWORD:<30} ║")
        print("╚══════════════════════════════════════════╝\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
