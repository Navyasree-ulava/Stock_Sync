"""
main.py — StockSync FastAPI Backend

Architecture:
  React Frontend
      ↓
  FastAPI (this file)
      ↓
  JWT Authentication (auth/dependencies.py)
      ↓
  Agentic LLM Loop (ai/agent.py)  →  MCP Client (mcp_bridge/)
      ↓                                      ↓
  PostgreSQL (db/)  ←────────── MCP Server (mcp_server/server.py)

Single source of truth: PostgreSQL. SQLite is fully removed.
"""

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# ─── Path bootstrap ───────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
load_dotenv(Path(__file__).parent.parent / ".env")

# ─── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [BACKEND] %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ─── DB & Migrations ──────────────────────────────────────────
from db.connection import engine
from db.migrations import run_migrations

# ─── MCP Manager ──────────────────────────────────────────────
from mcp_bridge.client_manager import mcp_manager

# ─── Routers ──────────────────────────────────────────────────
from routes.auth import router as auth_router
from routes.inventory import router as inventory_router
from routes.history import router as history_router
from routes.query import router as query_router
from routes.tts import router as tts_router
from routes.users import router as users_router


# ─── Lifespan ────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──
    log.info("=== StockSync starting up ===")

    # 1. Run DB migrations (creates tables if not exist)
    try:
        run_migrations(engine)
    except Exception as e:
        log.error(f"DB migration failed: {e}", exc_info=True)
        raise

    # 2. Initialize MCP server subprocess
    try:
        await mcp_manager.start()
        tool_count = len(mcp_manager.get_tools())
        log.info(f"MCP ready: {tool_count} tools loaded.")
    except Exception as e:
        log.error(f"MCP startup failed: {e}", exc_info=True)
        raise

    yield

    # ── Shutdown ──
    log.info("=== StockSync shutting down ===")
    await mcp_manager.stop()


# ─── FastAPI App ──────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="StockSync",
    description=(
        "Natural language inventory management powered by Llama-3.3-70B via Groq. "
        "Single source of truth: PostgreSQL."
    ),
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Rate limiter error handler
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS — origins loaded from env var so production URLs need no code change
_raw_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost,http://localhost:80,http://localhost:5173")
_allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Routers ─────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(inventory_router)
app.include_router(history_router)
app.include_router(query_router)
app.include_router(tts_router)
app.include_router(users_router)


# ─── Health ──────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health_check():
    from ai.agent import API_KEY, MODEL, PROVIDER

    database_status = "ok"
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            connection.execute(text(
                "SELECT id, full_name, email, business_name, hashed_password, is_active FROM users LIMIT 0"
            ))
            connection.execute(text(
                "SELECT id, user_id, name, category, stock, price, supplier FROM products LIMIT 0"
            ))
    except Exception as exc:
        database_status = "unavailable"
        log.error("Database health check failed: %s", exc)

    mcp_status = "ok" if mcp_manager.get_tools() else "unavailable"
    overall_status = "ok" if database_status == mcp_status == "ok" else "degraded"
    payload = {
        "status": overall_status,
        "service": "StockSync",
        "version": "3.0.0",
        "database": database_status,
        "mcp": mcp_status,
        "ai_configured": bool(API_KEY),
        "provider": PROVIDER,
        "model": MODEL,
    }
    if overall_status != "ok":
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
    return payload
