"""
routes/inventory.py — Inventory ingest and stats endpoints.

Handles CSV ingestion (/ingest) and dashboard stats (/stats).
All operations are user_id scoped — no cross-user data leakage.
"""

from typing import Annotated, Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from fastapi.responses import StreamingResponse
import io
import logging
import json
from pydantic import BaseModel, field_validator

from auth.dependencies import CurrentUser
from db.connection import get_db

from sqlalchemy.orm import Session
from sqlalchemy import func, distinct, or_
from db.models import Product, StockAuditLog
from utils.import_parser import parse_file_rows, resolve_column_headers, validate_row, detect_dataset_type

log = logging.getLogger(__name__)
router = APIRouter(prefix="/inventory", tags=["inventory"])

# ── Pydantic Models ────────────────────────────────────────────────────────────

class ProductIngest(BaseModel):
    name: str
    category: str
    stock: int
    price: float
    supplier: str = "Unknown"

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Product name cannot be empty.")
        return v[:200]

    @field_validator("category")
    @classmethod
    def category_sanitize(cls, v: str) -> str:
        return (v.strip() or "General")[:100]

    @field_validator("stock")
    @classmethod
    def stock_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("Stock cannot be negative.")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("Price cannot be negative.")
        return round(v, 2)

    @field_validator("supplier")
    @classmethod
    def supplier_sanitize(cls, v: str) -> str:
        return (v.strip() or "Unknown")[:100]


class IngestRequest(BaseModel):
    mode: str  # "append" or "replace"
    products: list[ProductIngest]

    @field_validator("mode")
    @classmethod
    def mode_valid(cls, v: str) -> str:
        if v not in ("append", "replace"):
            raise ValueError("mode must be 'append' or 'replace'.")
        return v


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/ingest")
async def ingest_dataset(
    request: IngestRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Ingest products from the CSV upload modal.
    All products are tagged with the current user's ID.
    Replace mode only deletes THIS user's products — never another user's.
    """
    user_id = current_user["id"]

    if request.mode == "replace":
        log.info(f"[INGEST] Replace mode: deleting products for user_id={user_id}")
        db.query(Product).filter(Product.user_id == user_id).delete()

    inserted = 0
    for p in request.products:
        new_prod = Product(
            user_id=user_id,
            name=p.name,
            category=p.category,
            stock=p.stock,
            price=p.price,
            supplier=p.supplier
        )
        db.add(new_prod)
        inserted += 1

    db.commit()
    log.info(f"[INGEST] user_id={user_id} ingested {inserted} products (mode={request.mode})")
    return {
        "status": "ok",
        "message": f"Successfully ingested {inserted} products.",
        "inserted": inserted,
    }


@router.get("/sample-csv")
async def download_sample_csv():
    """Return a template CSV format for importing products."""
    csv_content = (
        "Product Name,Category,Stock Quantity,Price,Supplier\n"
        "Basmati Rice 1kg,Grains,50,120.00,AgroSupply Co.\n"
        "Whole Milk 1L,Dairy,20,55.00,FreshFarm Ltd.\n"
        "USB-C Hub 7-Port,Electronics,10,1499.00,TechZone India\n"
    )
    return StreamingResponse(
        io.StringIO(csv_content),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=stockquery_sample.csv"}
    )


@router.post("/preview")
async def preview_import(
    current_user: CurrentUser,
    file: UploadFile = File(...),
):
    """
    Parse uploaded file (CSV or XLSX) and return headers,
    resolved mappings, raw preview rows (max 20), validation warnings,
    and dataset classification with confidence scoring.
    """
    # Verify file extension
    filename = file.filename or "file.csv"
    ext = filename.lower().split(".")[-1]
    if ext not in ("csv", "xlsx"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported file format. Please upload a .csv or .xlsx file."
        )

    try:
        content_bytes = await file.read()
        headers, raw_rows = parse_file_rows(content_bytes, filename)
    except Exception as parse_err:
        log.error(f"[IMPORT] File parsing failed for {filename}: {parse_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {str(parse_err)}"
        )

    column_mappings = resolve_column_headers(headers)
    dataset_type, confidence = detect_dataset_type(headers, column_mappings)
    preview_rows = raw_rows[:20]

    # Validate preview rows to flag early warnings
    validation_errors = []
    for idx, row in enumerate(preview_rows):
        _, err = validate_row(row, column_mappings, idx + 1)
        if err:
            validation_errors.append(err)

    return {
        "headers": headers,
        "preview_rows": preview_rows,
        "column_mappings": column_mappings,
        "validation_errors": validation_errors,
        "total_rows": len(raw_rows),
        "dataset_type": dataset_type,
        "confidence": confidence
    }


@router.post("/import")
async def execute_import(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
    strategy: str = Form("skip"),
    mappings: str = Form("{}"),
):
    """
    Import inventory rows from CSV/XLSX into PostgreSQL database.
    Supported strategies: skip, update, replace_all.
    """
    # 1. Parse and validate parameters
    try:
        mappings_dict = json.loads(mappings)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mappings must be a valid JSON string."
        )

    if not mappings_dict.get("name"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Mapping for 'name' is mandatory."
        )

    if strategy not in ("skip", "update", "replace_all"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid duplicate strategy. Supported: 'skip', 'update', 'replace_all'."
        )

    # 2. Parse file
    filename = file.filename or "file.csv"
    try:
        content_bytes = await file.read()
        headers, raw_rows = parse_file_rows(content_bytes, filename)
    except Exception as parse_err:
        log.error(f"[IMPORT] File parsing failed during import for {filename}: {parse_err}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse file: {str(parse_err)}"
        )

    # Detect dataset type to apply appropriate loading defaults
    resolved_headers_map = resolve_column_headers(headers)
    # Use resolved mapping merged with user's overrides to classify
    effective_mappings = {**resolved_headers_map, **mappings_dict}
    dataset_type, confidence = detect_dataset_type(headers, effective_mappings)

    user_id = current_user["id"]

    # 3. Apply replace_all strategy by dropping existing records first
    if strategy == "replace_all":
        log.info(f"[IMPORT] replace_all strategy triggered: clearing current inventory for user_id={user_id}")
        db.query(Product).filter(Product.user_id == user_id).delete()
        db.commit()

    # 4. Fetch existing database entries to evaluate conflicts
    existing_products = db.query(Product).filter(Product.user_id == user_id).all()
    existing_map = {p.name.lower().strip(): p for p in existing_products}

    # 5. Process all rows & validate
    valid_prods = []
    failed_rows = []
    failed_count = 0

    for idx, row in enumerate(raw_rows):
        val_prod, err = validate_row(row, mappings_dict, idx + 1)
        if err:
            if len(failed_rows) < 5:
                log.warning(f"[IMPORT] Row {idx + 1} validation failed. Errors: {err.get('errors')}. Row: {row}")
            failed_rows.append(err)
            failed_count += 1
            continue

        # If it is classified as a transaction log or catalog, override stock value to 0
        if dataset_type in ("transaction", "catalog"):
            val_prod["stock"] = 0

        valid_prods.append(val_prod)

    # 6. De-duplicate product names case-insensitively for catalogs/transactions
    if dataset_type in ("transaction", "catalog"):
        seen_names = {}
        for vp in valid_prods:
            name_key = vp["name"].lower().strip()
            if name_key not in seen_names:
                seen_names[name_key] = vp
        valid_prods = list(seen_names.values())

    inserted_count = 0
    updated_count = 0
    skipped_count = 0

    # 7. Write unique records to database
    for val_prod in valid_prods:
        name_key = val_prod["name"].lower().strip()
        
        if name_key in existing_map:
            if strategy == "skip":
                skipped_count += 1
            elif strategy == "update":
                p = existing_map[name_key]
                p.stock = val_prod["stock"]
                p.price = val_prod["price"]
                p.category = val_prod["category"]
                p.supplier = val_prod["supplier"]
                updated_count += 1
        else:
            # Create new product record
            new_p = Product(
                user_id=user_id,
                name=val_prod["name"],
                category=val_prod["category"],
                stock=val_prod["stock"],
                price=val_prod["price"],
                supplier=val_prod["supplier"]
            )
            db.add(new_p)
            # Cache locally to handle duplicate entries within the list
            existing_map[name_key] = new_p
            inserted_count += 1

    db.commit()
    log.info(f"[IMPORT] Scoped user_id={user_id} processed {len(raw_rows)} rows. Result: {inserted_count} inserted, {updated_count} updated, {skipped_count} skipped.")

    return {
        "status": "success",
        "dataset_type": dataset_type,
        "total_rows": len(raw_rows),
        "unique_products": len(valid_prods),
        "inserted": inserted_count,
        "updated": updated_count,
        "skipped": skipped_count,
        "failed": failed_count,
        "failed_rows": failed_rows
    }



@router.get("/stats")
async def get_inventory_stats(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Return live dashboard stats for the sidebar.
    Scoped to the current user.
    """
    user_id = current_user["id"]
    
    stats = db.query(
        func.count(Product.id).label("total_products"),
        func.coalesce(func.sum(Product.stock), 0).label("total_units"),
        func.count(distinct(Product.category)).label("total_categories")
    ).filter(Product.user_id == user_id).first()

    return {
        "total_products": stats.total_products,
        "total_units": stats.total_units,
        "total_categories": stats.total_categories,
    }


@router.get("/categories")
async def get_user_categories(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Return distinct categories for the current user as a flat list of strings.
    """
    user_id = current_user["id"]
    rows = db.query(Product.category).distinct().filter(Product.user_id == user_id).order_by(Product.category).all()
    return [r[0] for r in rows if r[0]]


# ── Product CRUD ───────────────────────────────────────────────────────────────

class ProductUpdate(BaseModel):
    """All fields optional — only provided fields are updated."""
    name: Optional[str] = None
    category: Optional[str] = None
    stock: Optional[int] = None
    price: Optional[float] = None
    supplier: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Product name cannot be empty.")
            return v[:200]
        return v

    @field_validator("stock")
    @classmethod
    def stock_non_negative(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v < 0:
            raise ValueError("Stock cannot be negative.")
        return v

    @field_validator("price")
    @classmethod
    def price_non_negative(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and v < 0:
            raise ValueError("Price cannot be negative.")
        return round(v, 2) if v is not None else v


@router.get("/products")
async def list_products(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    search: str = "",
    category: str = "",
    page: int = 1,
    per_page: int = 50,
):
    """
    Return a paginated, searchable list of the user's products.
    Supports optional ?search= and ?category= query params.
    """
    user_id = current_user["id"]
    page = max(1, page)
    per_page = min(per_page, 200)
    offset = (page - 1) * per_page

    query = db.query(Product).filter(Product.user_id == user_id)

    if search.strip():
        query = query.filter(Product.name.ilike(f"%{search.strip()}%"))
    if category.strip():
        query = query.filter(Product.category.ilike(f"%{category.strip()}%"))

    total = query.count()
    products = query.order_by(Product.name.asc()).offset(offset).limit(per_page).all()

    def serialize(p: Product) -> dict:
        return {
            "id": p.id,
            "user_id": p.user_id,
            "name": p.name,
            "category": p.category,
            "stock": p.stock,
            "price": p.price,
            "supplier": p.supplier,
            "created_at": p.created_at.isoformat() if p.created_at else None,
            "updated_at": p.updated_at.isoformat() if p.updated_at else None
        }

    return {
        "products": [serialize(p) for p in products],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 1,
    }


@router.post("/products", status_code=status.HTTP_201_CREATED)
async def create_product(
    product: ProductIngest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Add a single product to the user's inventory."""
    user_id = current_user["id"]
    new_prod = Product(
        user_id=user_id,
        name=product.name,
        category=product.category,
        stock=product.stock,
        price=product.price,
        supplier=product.supplier
    )
    db.add(new_prod)
    db.commit()
    db.refresh(new_prod)
    
    log.info(f"[CRUD] user_id={user_id} created product id={new_prod.id} name='{product.name}'")
    return {
        "id": new_prod.id,
        "user_id": new_prod.user_id,
        "name": new_prod.name,
        "category": new_prod.category,
        "stock": new_prod.stock,
        "price": new_prod.price,
        "supplier": new_prod.supplier,
        "created_at": new_prod.created_at.isoformat() if new_prod.created_at else None,
        "updated_at": new_prod.updated_at.isoformat() if new_prod.updated_at else None
    }


@router.put("/products/{product_id}")
async def update_product(
    product_id: int,
    updates: ProductUpdate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Update specific fields of an existing product. Only provided fields are changed."""
    user_id = current_user["id"]

    existing = db.query(Product).filter(Product.id == product_id, Product.user_id == user_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found in your inventory.")

    fields = updates.model_dump(exclude_none=True)
    if not fields:
        return {
            "id": existing.id,
            "user_id": existing.user_id,
            "name": existing.name,
            "category": existing.category,
            "stock": existing.stock,
            "price": existing.price,
            "supplier": existing.supplier
        }

    old_stock = existing.stock

    for k, v in fields.items():
        setattr(existing, k, v)

    # Write audit log if stock changed
    if "stock" in fields:
        audit_entry = StockAuditLog(
            user_id=user_id,
            product_id=product_id,
            old_stock=old_stock,
            new_stock=fields["stock"],
            action="manual_edit"
        )
        db.add(audit_entry)

    db.commit()
    db.refresh(existing)
    
    log.info(f"[CRUD] user_id={user_id} updated product id={product_id} fields={list(fields.keys())}")
    return {
        "id": existing.id,
        "user_id": existing.user_id,
        "name": existing.name,
        "category": existing.category,
        "stock": existing.stock,
        "price": existing.price,
        "supplier": existing.supplier,
        "created_at": existing.created_at.isoformat() if existing.created_at else None,
        "updated_at": existing.updated_at.isoformat() if existing.updated_at else None
    }


@router.delete("/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    product_id: int,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Permanently delete a product from the user's inventory."""
    user_id = current_user["id"]
    existing = db.query(Product).filter(Product.id == product_id, Product.user_id == user_id).first()
    if not existing:
        raise HTTPException(status_code=404, detail="Product not found in your inventory.")
        
    db.delete(existing)
    db.commit()
    log.info(f"[CRUD] user_id={user_id} deleted product id={product_id}")


# ── Audit Log Viewer ───────────────────────────────────────────────────────────

@router.get("/audit")
async def get_audit_log(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
    per_page: int = 50,
):
    """
    Return paginated stock change audit log for the authenticated user.
    Joins with products table to include the product name.
    """
    user_id = current_user["id"]
    page = max(1, page)
    per_page = min(per_page, 100)
    offset = (page - 1) * per_page

    total = db.query(StockAuditLog).filter(StockAuditLog.user_id == user_id).count()

    # Outer join to product to get name
    rows = db.query(
        StockAuditLog.id,
        StockAuditLog.product_id,
        func.coalesce(Product.name, '(deleted product)').label("product_name"),
        func.coalesce(Product.category, '').label("category"),
        StockAuditLog.old_stock,
        StockAuditLog.new_stock,
        StockAuditLog.action,
        StockAuditLog.created_at
    ).select_from(StockAuditLog).join(
        Product, Product.id == StockAuditLog.product_id, isouter=True
    ).filter(
        StockAuditLog.user_id == user_id
    ).order_by(
        StockAuditLog.id.desc()
    ).offset(offset).limit(per_page).all()

    entries = []
    for r in rows:
        entries.append({
            "id": r.id,
            "product_id": r.product_id,
            "product_name": r.product_name,
            "category": r.category,
            "old_stock": r.old_stock,
            "new_stock": r.new_stock,
            "action": r.action,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })

    return {
        "entries": entries,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if total else 1,
    }

