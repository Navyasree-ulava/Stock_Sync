"""
tests/test_integration.py — Integration tests for the FastAPI backend.

Uses an in-memory SQLite engine via SQLAlchemy and overrides the DB
dependency so no real PostgreSQL connection is needed.

Covers:
  - Auth: register, login (JSON), logout, weak password
  - JWT protection: /query, /inventory, /history all require Bearer token
  - Inventory: CRUD (create, read, update, delete, stats, search)
  - History: GET, POST, DELETE + user isolation
  - /users/me: authenticated profile
  - Health check
"""

import os
import sys
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch

# ── Path & env bootstrap (must happen before any app import) ──
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["RATELIMIT_ENABLED"] = "false"   # disable slowapi in all test routes
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production-32chars!")
os.environ.setdefault("GROQ_API_KEY", "test-key")

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from db.models import Base
from db.connection import get_db

# ── Shared in-memory engine ───────────────────────────────────
_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

@event.listens_for(_engine, "connect")
def _fk_on(conn, _):
    conn.execute("PRAGMA foreign_keys=ON")

Base.metadata.create_all(bind=_engine)
_Session = sessionmaker(bind=_engine)


def _override_get_db():
    db = _Session()
    try:
        yield db
    finally:
        db.close()


# ── App import + patch MCP ───────────────────────────────────
with patch("mcp_bridge.client_manager.MCPManager.start", new_callable=AsyncMock), \
     patch("mcp_bridge.client_manager.MCPManager.stop", new_callable=AsyncMock), \
     patch("mcp_bridge.client_manager.MCPManager.get_tools", return_value=[]):
    from main import app

app.dependency_overrides[get_db] = _override_get_db

# Disable rate limiting by making every request look like a unique IP
import uuid
app.state.limiter._key_func = lambda request: str(uuid.uuid4())


# ── Fixtures ──────────────────────────────────────────────────

@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _register_and_login(client, full_name, email, password="Password1"):
    client.post("/auth/register", json={
        "full_name": full_name,
        "email": email,
        "password": password,
        "business_name": "Apex Retailers LLC"
    })
    resp = client.post("/auth/login", json={"email": email, "password": password})
    token = resp.json().get("access_token", "")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def auth_headers(client):
    return _register_and_login(client, "integtest", "integtest@example.com")


# ── Auth ──────────────────────────────────────────────────────

class TestAuth:
    def test_register_returns_201(self, client):
        resp = client.post("/auth/register", json={
            "full_name": "New User",
            "email": "newuser@test.com",
            "password": "ValidPass1",
            "business_name": " Apex Corp"
        })
        assert resp.status_code == 201
        assert resp.json() == {"message": "User created successfully"}

    def test_duplicate_register_returns_409(self, client):
        client.post("/auth/register", json={
            "full_name": "Dup User",
            "email": "dup@test.com",
            "password": "ValidPass1",
        })
        resp = client.post("/auth/register", json={
            "full_name": "Dup User",
            "email": "dup@test.com",
            "password": "ValidPass1",
        })
        assert resp.status_code == 409

    def test_login_returns_access_token_and_user(self, client):
        client.post("/auth/register", json={
            "full_name": "Login User",
            "email": "loginuser@test.com",
            "password": "ValidPass1",
        })
        resp = client.post("/auth/login", json={
            "email": "loginuser@test.com",
            "password": "ValidPass1",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["user"]["full_name"] == "Login User"
        assert body["user"]["email"] == "loginuser@test.com"

    def test_login_wrong_password_returns_401(self, client):
        resp = client.post("/auth/login", json={
            "email": "loginuser@test.com",
            "password": "WrongPassword1",
        })
        assert resp.status_code == 401

    def test_weak_password_returns_422(self, client):
        resp = client.post("/auth/register", json={
            "full_name": "Weak User",
            "email": "weak@test.com",
            "password": "short",
        })
        assert resp.status_code == 422

    def test_logout_invalidates_or_logs_out(self, client):
        headers = _register_and_login(client, "Logout User", "logoutuser@test.com")
        resp = client.post("/auth/logout", headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"message": "Logged out successfully"}


# ── JWT Protection ────────────────────────────────────────────

class TestJWTProtection:
    def test_query_without_token_returns_401(self, client):
        resp = client.post("/query", json={"question": "test"})
        assert resp.status_code == 401

    def test_inventory_without_token_returns_401(self, client):
        resp = client.get("/inventory/products")
        assert resp.status_code == 401

    def test_history_without_token_returns_401(self, client):
        resp = client.get("/history")
        assert resp.status_code == 401

    def test_users_me_without_token_returns_401(self, client):
        resp = client.get("/users/me")
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        resp = client.get("/users/me", headers={"Authorization": "Bearer invalidtoken"})
        assert resp.status_code == 401


# ── /users/me ─────────────────────────────────────────────────

class TestUsersMe:
    def test_me_returns_profile(self, client, auth_headers):
        resp = client.get("/users/me", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["full_name"] == "integtest"
        assert body["email"] == "integtest@example.com"
        assert body["business_name"] == "Apex Retailers LLC"
        assert "id" in body


# ── Inventory CRUD ────────────────────────────────────────────

class TestInventory:
    def _create_product(self, client, auth_headers, name="Test Widget", category="Tools", stock=50, price=9.99, supplier="SupplierA"):
        return client.post("/inventory/products", headers=auth_headers, json={
            "name": name,
            "category": category,
            "stock": stock,
            "price": price,
            "supplier": supplier,
        })

    def test_create_product_returns_201(self, client, auth_headers):
        resp = self._create_product(client, auth_headers)
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Test Widget"
        assert body["stock"] == 50

    def test_list_products_returns_own_products(self, client, auth_headers):
        self._create_product(client, auth_headers, "Listed Item")
        resp = client.get("/inventory/products", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "products" in body
        assert body["total"] >= 1

    def test_update_product_changes_stock(self, client, auth_headers):
        create = self._create_product(client, auth_headers, "Update Target")
        pid = create.json()["id"]
        resp = client.put(f"/inventory/products/{pid}", headers=auth_headers, json={"stock": 99})
        assert resp.status_code == 200
        assert resp.json()["stock"] == 99

    def test_delete_product_removes_it(self, client, auth_headers):
        create = self._create_product(client, auth_headers, "Delete Target")
        pid = create.json()["id"]
        del_resp = client.delete(f"/inventory/products/{pid}", headers=auth_headers)
        assert del_resp.status_code == 204
        resp = client.get("/inventory/products", headers=auth_headers)
        ids = [p["id"] for p in resp.json()["products"]]
        assert pid not in ids

    def test_update_other_users_product_returns_404(self, client):
        # Register a second user
        headers_b = _register_and_login(client, "otherinvuser", "otherinv@test.com")
        # Create a product as integtest
        integtest_headers = _register_and_login(client, "integ2", "integ2@test.com")
        create = client.post("/inventory/products", headers=integtest_headers, json={
            "name": "Private Widget",
            "category": "X",
            "stock": 1,
            "price": 1.0,
            "supplier": "S",
        })
        pid = create.json()["id"]
        # otherinvuser tries to edit it
        resp = client.put(f"/inventory/products/{pid}", headers=headers_b, json={"stock": 999})
        assert resp.status_code == 404

    def test_stats_endpoint(self, client, auth_headers):
        resp = client.get("/inventory/stats", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert "total_products" in body
        assert "total_units" in body

    def test_get_categories(self, client, auth_headers):
        self._create_product(client, auth_headers, "Unique Test Name", category="Special Category Key")
        resp = client.get("/inventory/categories", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        assert "Special Category Key" in body

    def test_search_filter(self, client, auth_headers):
        self._create_product(client, auth_headers, "Searchable Gadget")
        resp = client.get("/inventory/products?search=Gadget", headers=auth_headers)
        assert resp.status_code == 200
        assert any("Gadget" in p["name"] for p in resp.json()["products"])

    def test_download_sample_csv(self, client, auth_headers):
        resp = client.get("/inventory/sample-csv", headers=auth_headers)
        assert resp.status_code == 200
        assert "attachment; filename=stockquery_sample.csv" in resp.headers["content-disposition"]
        assert "Product Name,Category" in resp.text

    def test_preview_csv(self, client, auth_headers):
        csv_data = (
            "Product Name,Category,Stock Quantity,Price,Supplier\n"
            "Widget A,Tools,10,19.99,Supplier X\n"
            "Widget B,Tools,-5,5.00,Supplier Y\n"
        )
        files = {"file": ("test.csv", csv_data, "text/csv")}
        resp = client.post("/inventory/preview", headers=auth_headers, files=files)
        assert resp.status_code == 200
        body = resp.json()
        assert "Product Name" in body["headers"]
        assert body["total_rows"] == 2
        assert len(body["preview_rows"]) == 2
        assert body["column_mappings"]["name"] == "Product Name"
        assert body["column_mappings"]["stock"] == "Stock Quantity"
        assert len(body["validation_errors"]) == 1
        assert body["validation_errors"][0]["row_index"] == 2
        assert any(e["field"] == "stock" for e in body["validation_errors"][0]["errors"])

    def test_import_csv_skip(self, client, auth_headers):
        self._create_product(client, auth_headers, "Duplicate Item")
        
        csv_data = (
            "Product Name,Category,Stock Quantity,Price,Supplier\n"
            "Duplicate Item,Tools,100,50.00,Supplier X\n"
            "New Unique Item,Grains,15,5.50,Supplier Y\n"
        )
        files = {"file": ("test.csv", csv_data, "text/csv")}
        data = {
            "strategy": "skip",
            "mappings": '{"name": "Product Name", "stock": "Stock Quantity", "category": "Category", "price": "Price", "supplier": "Supplier"}'
        }
        resp = client.post("/inventory/import", headers=auth_headers, files=files, data=data)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["inserted"] == 1
        assert body["skipped"] == 1
        
        p_resp = client.get("/inventory/products?search=Duplicate+Item", headers=auth_headers)
        dup_prod = [p for p in p_resp.json()["products"] if p["name"] == "Duplicate Item"][0]
        assert dup_prod["stock"] == 50

    def test_import_csv_update(self, client, auth_headers):
        self._create_product(client, auth_headers, "Update Item")
        
        csv_data = (
            "Product Name,Category,Stock Quantity,Price,Supplier\n"
            "Update Item,Tools,100,50.00,Supplier New\n"
        )
        files = {"file": ("test.csv", csv_data, "text/csv")}
        data = {
            "strategy": "update",
            "mappings": '{"name": "Product Name", "stock": "Stock Quantity", "category": "Category", "price": "Price", "supplier": "Supplier"}'
        }
        resp = client.post("/inventory/import", headers=auth_headers, files=files, data=data)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["updated"] == 1
        
        p_resp = client.get("/inventory/products?search=Update+Item", headers=auth_headers)
        upd_prod = [p for p in p_resp.json()["products"] if p["name"] == "Update Item"][0]
        assert upd_prod["stock"] == 100
        assert upd_prod["price"] == 50.0

    def test_import_csv_replace_all(self, client, auth_headers):
        self._create_product(client, auth_headers, "Old Item")
        
        csv_data = (
            "Product Name,Category,Stock Quantity,Price,Supplier\n"
            "Brand New Item,Dairy,12,1.99,Supplier Z\n"
        )
        files = {"file": ("test.csv", csv_data, "text/csv")}
        data = {
            "strategy": "replace_all",
            "mappings": '{"name": "Product Name", "stock": "Stock Quantity", "category": "Category", "price": "Price", "supplier": "Supplier"}'
        }
        resp = client.post("/inventory/import", headers=auth_headers, files=files, data=data)
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "success"
        assert body["inserted"] == 1
        
        p_resp = client.get("/inventory/products", headers=auth_headers)
        p_names = [p["name"] for p in p_resp.json()["products"]]
        assert "Old Item" not in p_names
        assert "Brand New Item" in p_names



# ── Chat History ──────────────────────────────────────────────

class TestHistory:
    def test_get_empty_history(self, client, auth_headers):
        client.delete("/history", headers=auth_headers)
        resp = client.get("/history", headers=auth_headers)
        assert resp.status_code == 200
        assert resp.json()["messages"] == []

    def test_save_and_retrieve_message(self, client, auth_headers):
        client.delete("/history", headers=auth_headers)
        save = client.post("/history/message", headers=auth_headers, json={
            "role": "user",
            "content": "What is my stock level?",
        })
        assert save.status_code == 201
        assert save.json()["ok"] is True

        resp = client.get("/history", headers=auth_headers)
        messages = resp.json()["messages"]
        assert len(messages) == 1
        assert messages[0]["content"] == "What is my stock level?"
        assert messages[0]["role"] == "user"

    def test_invalid_role_rejected(self, client, auth_headers):
        resp = client.post("/history/message", headers=auth_headers, json={
            "role": "system",
            "content": "Inject something",
        })
        assert resp.status_code == 201
        assert resp.json()["ok"] is False

    def test_clear_history(self, client, auth_headers):
        client.post("/history/message", headers=auth_headers, json={
            "role": "ai",
            "content": "Your stock is fine.",
        })
        client.delete("/history", headers=auth_headers)
        resp = client.get("/history", headers=auth_headers)
        assert resp.json()["messages"] == []

    def test_history_isolation_between_users(self, client, auth_headers):
        """User A's history must not appear when user B queries their history."""
        client.delete("/history", headers=auth_headers)
        client.post("/history/message", headers=auth_headers, json={
            "role": "user",
            "content": "User A secret message",
        })
        headers_b = _register_and_login(client, "historyb", "historyb@test.com")
        resp = client.get("/history", headers=headers_b)
        contents = [m["content"] for m in resp.json()["messages"]]
        assert "User A secret message" not in contents


# ── Health ────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["database"] == "postgresql"


# ── Typo Tolerance and Fuzzy Queries ──────────────────────────

class TestFuzzyTypoTolerance:
    def test_fuzzy_matching_on_typos(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))
        from server import query_inventory_db
        from db.models import Product, User
        db = _Session()
        try:
            # Seed user 999 if not exists to satisfy foreign key constraints
            user_id = 999
            u = db.query(User).filter(User.id == user_id).first()
            if not u:
                u = User(id=user_id, full_name="fuzzytest", email="fuzzy@test.com", hashed_password="hash")
                db.add(u)
                db.flush()

            db.query(Product).filter(Product.user_id == user_id).delete()
            db.add(Product(user_id=user_id, name="Whole Milk", category="Dairy", stock=50, price=2.50))
            db.add(Product(user_id=user_id, name="tropical fruit", category="Fruit", stock=10, price=1.99))
            db.commit()

            # Patch server's DB session to reuse our seeded database context
            with patch("server._get_db", return_value=db):
                # Test fuzzy matches
                # "whol milk" -> "Whole Milk"
                matches1 = query_inventory_db(user_id=user_id, product_name="whol milk")
                assert len(matches1) == 1
                assert matches1[0]["name"] == "Whole Milk"

                # "tropicl fruit" -> "tropical fruit"
                matches2 = query_inventory_db(user_id=user_id, product_name="tropicl fruit")
                assert len(matches2) == 1
                assert matches2[0]["name"] == "tropical fruit"
        finally:
            db.close()


# ── Empty Inventory Queries ───────────────────────────────────

class TestEmptyInventoryQueries:
    def test_search_on_empty_database(self, client):
        # Register a brand new timestamped user to guarantee an isolated database
        import time
        username = f"empty_{int(time.time())}"
        email = f"{username}@test.com"
        headers = _register_and_login(client, username, email)

        # Clear the auto-seeded products for this fresh user
        db = _Session()
        try:
            from db.models import Product, User
            user = db.query(User).filter(User.email == email).first()
            if user:
                db.query(Product).filter(Product.user_id == user.id).delete()
                db.commit()
        finally:
            db.close()

        resp = client.get("/inventory/products", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["products"] == []


# ── Import Failures ───────────────────────────────────────────

class TestImportFailures:
    def test_missing_required_name_mapping(self, client, auth_headers):
        csv_data = "Product Name,Category,Stock Quantity\nWidget A,Tools,10\n"
        files = {"file": ("test.csv", csv_data, "text/csv")}
        data = {
            "strategy": "skip",
            "mappings": '{"stock": "Stock Quantity"}'  # missing 'name' mapping
        }
        resp = client.post("/inventory/import", headers=auth_headers, files=files, data=data)
        assert resp.status_code == 422
        assert "name" in resp.json()["detail"].lower()

    def test_corrupted_file_upload(self, client, auth_headers):
        files = {"file": ("test.xlsx", b"invalid binary content", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        data = {
            "strategy": "skip",
            "mappings": '{"name": "Product Name"}'
        }
        resp = client.post("/inventory/import", headers=auth_headers, files=files, data=data)
        assert resp.status_code == 400
        assert "parse" in resp.json()["detail"].lower()


# ── Search Pagination ──────────────────────────────────────────

class TestSearchPagination:
    def test_search_limit_and_offset(self):
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent / "mcp_server"))
        from server import search_inventory
        from db.models import Product, User
        db = _Session()
        try:
            # Seed 5 items for user 999
            user_id = 999
            u = db.query(User).filter(User.id == user_id).first()
            if not u:
                u = User(id=user_id, full_name="fuzzytest", email="fuzzy@test.com", hashed_password="hash")
                db.add(u)
                db.flush()

            db.query(Product).filter(Product.user_id == user_id).delete()
            for i in range(5):
                db.add(Product(user_id=user_id, name=f"Product {i}", category="General", stock=10, price=1.0))
            db.commit()

            with patch("server._get_db", return_value=db):
                # Search with limit=2, offset=0
                res1 = search_inventory(user_id=user_id, limit=2, offset=0)
                assert len(res1) == 2
                assert res1[0]["name"] == "Product 0"
                assert res1[1]["name"] == "Product 1"

                # Search with limit=2, offset=2
                res2 = search_inventory(user_id=user_id, limit=2, offset=2)
                assert len(res2) == 2
                assert res2[0]["name"] == "Product 2"
                assert res2[1]["name"] == "Product 3"
        finally:
            db.close()
