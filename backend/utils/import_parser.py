"""
utils/import_parser.py — Parsing utility for CSV and Excel sheets.

Implements column resolving, type coercion, and schema validation.
Only 'name' is mandatory; other fields default if missing.
Supports dataset classification (inventory, catalog, transaction, unknown).
"""

import io
import csv
import logging
from typing import Optional, Tuple, List, Dict
import openpyxl

log = logging.getLogger(__name__)

# Column aliases for auto-matching headers
COL_MAP = {
    "name":     ["name", "product name", "product_name", "item name", "item_name", "product", "item", "description", "itemdescription"],
    "category": ["category", "catagory", "category name", "category_name", "type", "department", "section"],
    "stock":    ["stock", "quantity", "stock quantity", "stock_quantity", "qty", "units", "inventory"],
    "price":    ["price", "unit price", "unit_price", "selling price", "selling_price", "mrp", "cost"],
    "supplier": ["supplier", "supplier name", "supplier_name", "vendor", "brand", "manufacturer", "source"],
}


def resolve_column_headers(headers: List[str]) -> Dict[str, str]:
    """Map standard fields to actual headers case-insensitively."""
    resolved = {}
    lower_headers = [h.lower().strip() for h in headers]
    for field, candidates in COL_MAP.items():
        for c in candidates:
            if c in lower_headers:
                # Find original header with matching case
                idx = lower_headers.index(c)
                resolved[field] = headers[idx]
                break
    return resolved


def detect_dataset_type(headers: List[str], resolved_mappings: Dict[str, str]) -> Tuple[str, float]:
    """
    Classify dataset type and return (dataset_type, confidence).
    Types: inventory, catalog, transaction, unknown.
    """
    lower_headers = [h.lower().strip() for h in headers]
    
    # Check transaction signatures
    has_member = any("member" in h or "customer" in h or "user" in h or "card" in h or "id" in h for h in lower_headers)
    has_date = any("date" in h or "time" in h or "timestamp" in h for h in lower_headers)
    has_transaction = any("transaction" in h or "basket" in h or "purchase" in h for h in lower_headers)
    
    has_name = "name" in resolved_mappings
    has_stock = "stock" in resolved_mappings
    has_price = "price" in resolved_mappings

    # 1. Transaction dataset: has member/basket indicators, transaction timestamps, and item names
    if (has_member or has_transaction) and has_date and has_name:
        confidence = 0.95
        if has_transaction and has_member:
            confidence = 0.98
        return "transaction", confidence
        
    # 2. Inventory dataset: has name and a distinct stock level column
    if has_name and has_stock:
        confidence = 0.90
        if has_price:
            confidence = 0.95
        return "inventory", confidence
        
    # 3. Product catalog: has name but no stock column
    if has_name:
        confidence = 0.85
        return "catalog", confidence

    # 4. Unknown: cannot determine mapping, require manual configuration
    if len(headers) > 0:
        return "unknown", 0.50

    return "unknown", 0.0


def parse_file_rows(file_bytes: bytes, filename: str) -> Tuple[List[str], List[Dict[str, str]]]:
    """
    Parse CSV or XLSX file and return (headers, list of raw rows).
    """
    ext = filename.lower().split(".")[-1]
    if ext == "xlsx":
        return _parse_xlsx(file_bytes)
    else:
        # Default fallback to CSV
        return _parse_csv(file_bytes)


def _parse_csv(file_bytes: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    content = file_bytes.decode("utf-8", errors="ignore")
    f = io.StringIO(content)
    reader = csv.DictReader(f)
    headers = [h.strip() for h in (reader.fieldnames or []) if h.strip()]
    rows = []
    for r in reader:
        # Filter out completely empty rows
        if not any(val.strip() for val in r.values() if val):
            continue
        rows.append({k.strip(): (v.strip() if v else "") for k, v in r.items() if k})
    return headers, rows


def _parse_xlsx(file_bytes: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    sheet = wb.active
    headers = []
    rows = []
    for i, row_vals in enumerate(sheet.iter_rows(values_only=True)):
        if i == 0:
            headers = [str(cell).strip() for cell in row_vals if cell is not None and str(cell).strip()]
            continue
        if not any(cell is not None for cell in row_vals):
            continue
        row_dict = {}
        for j, val in enumerate(row_vals):
            if j < len(headers):
                header = headers[j]
                row_dict[header] = str(val).strip() if val is not None else ""
        rows.append(row_dict)
    return headers, rows


def validate_row(row: Dict[str, str], mappings: Dict[str, str], row_index: int) -> Tuple[Optional[Dict], Optional[Dict]]:
    """
    Validate a single raw row against column mappings.
    Returns:
        (validated_product_dict, None) if successful.
        (None, error_dict) if validation fails.
    """
    mapped_name = mappings.get("name")
    mapped_stock = mappings.get("stock")
    mapped_price = mappings.get("price")
    mapped_category = mappings.get("category")
    mapped_supplier = mappings.get("supplier")

    errors = []

    # ── 1. Name Validation (Mandatory) ──
    name = ""
    if not mapped_name or mapped_name not in row:
        errors.append({"field": "name", "error": "Mapped column name not found in row."})
    else:
        name = row[mapped_name].strip()
        if not name:
            errors.append({"field": "name", "error": "Product name is empty."})
        elif len(name) > 200:
            name = name[:200]

    # ── 2. Stock Validation (Optional: Defaults to 0) ──
    stock = 0
    if mapped_stock and mapped_stock in row:
        stock_raw = row[mapped_stock].strip()
        if stock_raw:
            try:
                # Handle potential float strings (e.g., '10.0')
                stock = int(float(stock_raw))
                if stock < 0:
                    errors.append({"field": "stock", "error": "Stock cannot be negative."})
            except ValueError:
                errors.append({"field": "stock", "error": f"Invalid stock number format: '{stock_raw}'"})

    # ── 3. Price Validation (Optional: Defaults to 0.0) ──
    price = 0.0
    if mapped_price and mapped_price in row:
        price_raw = row[mapped_price].strip().replace("$", "").replace("₹", "").replace(",", "")
        if price_raw:
            try:
                price = round(float(price_raw), 2)
                if price < 0:
                    errors.append({"field": "price", "error": "Price cannot be negative."})
            except ValueError:
                errors.append({"field": "price", "error": f"Invalid price format: '{price_raw}'"})

    # ── 4. Category Validation (Optional: Defaults to "Uncategorized") ──
    category = "Uncategorized"
    if mapped_category and mapped_category in row:
        val = row[mapped_category].strip()
        if val:
            category = val[:100]

    # ── 5. Supplier Validation (Optional: Defaults to None) ──
    supplier = None
    if mapped_supplier and mapped_supplier in row:
        val = row[mapped_supplier].strip()
        if val:
            supplier = val[:255]

    if errors:
        return None, {
            "row_index": row_index,
            "errors": errors,
            "raw_row": row
        }

    return {
        "name": name,
        "category": category,
        "stock": stock,
        "price": price,
        "supplier": supplier
    }, None
