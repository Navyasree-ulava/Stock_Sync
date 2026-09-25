# StockSync

StockSync is a multi-tenant retail inventory application with a React dashboard, FastAPI API, PostgreSQL persistence, and an optional natural-language inventory assistant. The assistant uses an OpenAI-compatible model from Groq and accesses inventory through Model Context Protocol (MCP) tools.

> **Project status:** the CRUD, import, authentication, and AI flows are implemented, but parts of the UI and deployment setup are still experimental. See [Known limitations](#known-limitations) before using it in production.

## Features

- Email/password registration and login with bcrypt-hashed passwords
- HS256 JWT authentication and per-user data isolation
- Product create, read, update, delete, search, category filtering, and pagination
- CSV and XLSX import with column auto-detection, preview, and duplicate strategies
- Dashboard and client-side analytics based on the current user's products
- Natural-language inventory queries through ten MCP tools
- AI-assisted stock quantity updates, scoped to the authenticated user
- Persistent chat history for the most recent 100 messages
- Browser speech recognition and optional ElevenLabs text-to-speech
- REST API documentation at `/docs` and `/redoc`

## Architecture

```text
┌──────────────────────────────────────────────────────────┐
│ Browser                                                    │
│ React 18 + Vite                                            │
└───────────────────────────┬──────────────────────────────┘
                            │ HTTP
                            ▼
┌──────────────────────────────────────────────────────────┐
│ Nginx (production frontend container, port 80)             │
│ Serves React assets and proxies /auth, /inventory, etc.  │
└───────────────────────────┬──────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────┐
│ FastAPI backend (port 8000; host port 8080 in Compose)    │
│ JWT auth · REST routes · AI agent · rate limits          │
│                                                          │
│ Starts mcp_server/server.py as a local stdio subprocess   │
└───────────────┬───────────────────────────┬──────────────┘
                │ SQLAlchemy                │ OpenAI API
                ▼                           ▼
┌───────────────────────────┐   ┌──────────────────────────┐
│ PostgreSQL 15             │   │ Groq                     │
│ users                     │   │ model + tool calling      │
│ products                  │   └──────────────────────────┘
│ stock_audit_log           │
│ chat_history              │◀── MCP tool subprocess uses
└───────────────────────────┘    the same database models
```

The MCP server is **not** a separate Docker Compose service. FastAPI starts it as a child process over stdio and passes the backend environment to it. PostgreSQL is the only persistent data store; there is no vector database, Redis, queue, or object store.

### Technology

| Area | Technology |
| --- | --- |
| Backend | Python 3.11, FastAPI, Uvicorn, Pydantic |
| Database | PostgreSQL 15, SQLAlchemy 2.x, Psycopg 2 |
| Authentication | bcrypt, PyJWT |
| AI | OpenAI Python SDK, Groq, tool calling |
| MCP | Python MCP/FastMCP over stdio |
| Frontend | React 18, Vite 6, Axios, Recharts |
| Styling | Custom vanilla CSS |
| Production serving | Nginx |
| Containers | Docker and Docker Compose |
| Tests | pytest with SQLite test overrides |

## Quick start with Docker

### Prerequisites

- [Docker Desktop](https://docs.docker.com/desktop/) with Docker Compose
- A Groq API key if you want AI queries; CRUD features work without one
- An ElevenLabs API key and voice ID only if you want text-to-speech

### 1. Create the backend environment file

From the repository root:

```bash
# macOS / Linux
cp .env.example .env

# Windows PowerShell
Copy-Item .env.example .env
```

Generate a JWT signing secret and put it in `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

At minimum, configure:

```env
SECRET_KEY=your_generated_random_value
```

To use the AI assistant, set your Groq key:

```env
GROQ_API_KEY=your_groq_key
```

### 2. Start the stack

```bash
docker compose up --build
```

The default ports are:

| Service | URL |
| --- | --- |
| React application | http://localhost |
| FastAPI Swagger UI | http://localhost:8080/docs |
| FastAPI ReDoc | http://localhost:8080/redoc |
| Health endpoint | http://localhost:8080/health |
| PostgreSQL (host) | `localhost:5433` |

Open http://localhost, register an account, and import a CSV/XLSX file. New accounts intentionally start with an empty inventory.

### Common Docker commands

```bash
# Run in the background
docker compose up -d --build

# Follow logs
docker compose logs -f backend

# Stop containers but keep the database volume
docker compose down

# Stop containers and delete the local database volume
docker compose down -v
```

`docker compose down -v` permanently deletes the local PostgreSQL data stored in the `stocksync-pgdata` volume.

## Existing installations after the StockSync rename

The application, containers, PostgreSQL role/database, MCP server identity, and browser-storage keys now use **StockSync** / `stocksync`.

- Existing browser sessions are migrated automatically from `sq_token` and `sq_user` to `stocksync_token` and `stocksync_user`.
- The Compose project and volume are now `stocksync` and `stocksync-pgdata`. An older PostgreSQL volume is not renamed automatically; back it up and migrate it with `pg_dump`/`pg_restore` before removing the old volume.
- For a disposable local installation with no data to preserve, remove the old containers and start the new stack with `docker compose up --build`.
- Editing `render.yaml` does not rename an already-created Render service or database. Rename or migrate those resources in the Render dashboard.
- Rename the GitHub repository, Vercel project, deployed hostname, and `VITE_API_URL`/CORS settings in their respective dashboards.

## Local development without Docker

Use Docker only for PostgreSQL and run the backend and frontend on the host.

### Prerequisites

- Python 3.11
- Node.js 20 and npm
- Docker, if you want to use the Compose database

### 1. Start PostgreSQL

```bash
docker compose up -d db
```

The database is published on host port `5433`.

### 2. Set up and run the backend

From the repository root:

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# Windows PowerShell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
```

Install dependencies and create the schema:

```bash
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt
cd backend
python -c "from db.connection import engine; from db.migrations import run_migrations; run_migrations(engine)"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

The root `.env` file is loaded by `backend/main.py` and by `backend/seed_db.py`. On backend startup, FastAPI runs the schema setup and starts the MCP subprocess automatically.

### 3. Run the frontend

In a second terminal:

```bash
cd frontend

# macOS / Linux
cp .env.example .env.local

# Windows PowerShell
Copy-Item .env.example .env.local

npm ci
npm run dev
```

Open http://localhost:5173. The template sets the browser API base URL to http://localhost:8000.

## Environment variables

### Backend and AI

The backend loads the repository-root `.env` file.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `SECRET_KEY` | **Yes** | none | HS256 JWT signing key; the backend refuses to import without it |
| `DATABASE_URL` | Yes in practice | local PostgreSQL URL | SQLAlchemy connection string; Compose overrides it with the internal service URL |
| `POSTGRES_USER` | Docker Compose | `stocksync` | PostgreSQL role used by the Compose database |
| `POSTGRES_PASSWORD` | Docker Compose | `stocksync_password` | Local Compose database credential; change it for shared environments |
| `POSTGRES_DB` | Docker Compose | `stocksync` | Local Compose database name |
| `ALLOWED_ORIGINS` | No | localhost origins | Comma-separated CORS allowlist |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `60` | Access-token lifetime |
| `GROQ_API_KEY` | Required for `/query` | none | Groq API credential |
| `LLM_MODEL` | No | `llama-3.3-70b-versatile` | Overrides the Groq model |
| `LLM_MAX_TURNS` | No | `10` | Maximum model/tool loop iterations |
| `ELEVENLABS_API_KEY` | For TTS | none | ElevenLabs credential |
| `ELEVENLABS_VOICE_ID` | For TTS | none | ElevenLabs voice ID |
| `MCP_HOST` | Standalone MCP only | `0.0.0.0` | Standalone MCP HTTP bind address |
| `MCP_PORT` | Standalone MCP only | `8001` | Standalone MCP HTTP port |
| `MCP_TRANSPORT` | Standalone MCP only | `http` | `http` or `stdio`; FastAPI forces `stdio` for its child process |

The default Groq model is `llama-3.3-70b-versatile`.

### Frontend

Vite reads variables from `frontend/.env.local` during local development.

| Variable | Purpose |
| --- | --- |
| `VITE_API_URL` | Browser-reachable API base URL; use `http://localhost:8000` for host development |

For the Docker stack, the frontend is compiled with an empty `VITE_API_URL`, so requests use relative URLs and Nginx proxies them to FastAPI. Vite variables are embedded into the browser bundle at build time—never put secrets in `VITE_*` variables.

## Import format

The importer accepts `.csv` and `.xlsx` files. Only the product-name column is mandatory.

```csv
Product Name,Category,Stock Quantity,Price,Supplier
Basmati Rice 1kg,Grains,50,120.00,AgroSupply Co.
Whole Milk 1L,Dairy,20,55.00,FreshFarm Ltd.
```

Common aliases such as `item_name`, `quantity`, `unit_price`, `vendor`, and `manufacturer` are detected automatically. The preview classifies files as inventory, catalog, transaction, or unknown. Import strategies are:

- `skip`: leave an existing duplicate unchanged
- `update`: update matching products
- `replace_all`: delete the current user's existing inventory before importing

A sample template is also available from `GET /inventory/sample-csv`.

## API overview

Interactive documentation is available at http://localhost:8080/docs while the API is running.

Except for public routes, endpoints require:

```http
Authorization: Bearer <access_token>
```

| Method | Path | Auth | Description |
| --- | --- | --- | --- |
| `GET` | `/health` | Public | Static liveness and provider/model metadata |
| `POST` | `/auth/register` | Public | Create an account with an empty inventory |
| `POST` | `/auth/login` | Public | Return a JWT and user profile |
| `POST` | `/auth/logout` | Bearer | Log the logout event; the token is not revoked |
| `POST` | `/inventory/ingest` | Bearer | Legacy JSON append/replace ingestion |
| `GET` | `/inventory/sample-csv` | Public | Download the sample CSV |
| `POST` | `/inventory/preview` | Bearer | Parse and preview a CSV/XLSX file |
| `POST` | `/inventory/import` | Bearer | Import a mapped CSV/XLSX file |
| `GET` | `/inventory/stats` | Bearer | Product, stock-unit, and category totals |
| `GET` | `/inventory/categories` | Bearer | Distinct product categories |
| `GET` | `/inventory/products` | Bearer | Search, category filter, and paginated products |
| `POST` | `/inventory/products` | Bearer | Create a product |
| `PUT` | `/inventory/products/{id}` | Bearer | Update a product |
| `DELETE` | `/inventory/products/{id}` | Bearer | Delete a product |
| `GET` | `/inventory/audit` | Bearer | Paginated manual/AI stock changes |
| `POST` | `/query` | Bearer | Run the natural-language agent loop |
| `GET` | `/history` | Bearer | Return the latest 100 persisted messages |
| `POST` | `/history/message` | Bearer | Persist one message manually |
| `DELETE` | `/history` | Bearer | Delete the user's persisted history |
| `GET` | `/users/me` | Bearer | Read the current profile |
| `POST` | `/tts` | Bearer | Stream ElevenLabs MP3 audio |

Rate limits are applied to registration (5/minute/IP), login (10/minute/IP), and AI queries (30/minute/IP).

## MCP tools

The assistant receives these tool schemas from `mcp_server/server.py`:

1. `query_inventory_db` — partial or fuzzy product-name search
2. `get_product_details` — fetch one product by ID
3. `search_inventory` — filter, sort, and paginate products
4. `get_low_stock_items` — return products below a threshold
5. `get_all_categories` — list distinct categories
6. `get_products_by_category` — list products in a category
7. `get_products_by_names` — match a list of names
8. `get_inventory_analytics` — totals, value, average price, and extremes
9. `get_category_analytics` — per-category counts, stock, and average price
10. `update_stock` — update one product's stock quantity

The backend injects the authenticated user's ID into every tool call. The model cannot create or delete products, but it can change stock through `update_stock`.

## Data model

| Table | Purpose |
| --- | --- |
| `users` | Account, business name, password hash, active state |
| `products` | User-owned product, category, stock, price, and supplier |
| `stock_audit_log` | Manual and AI stock quantity changes |
| `chat_history` | Persisted user/AI question-answer rows |

Schema setup uses SQLAlchemy `create_all` plus a small set of ad-hoc `ALTER TABLE` checks in `backend/db/migrations.py`; it is not a versioned migration system such as Alembic.

## Tests and builds

The backend suite uses SQLite and does not require a running PostgreSQL server. Test packages are not included in `backend/requirements.txt`, so install them separately:

```bash
python -m pip install pytest pytest-asyncio
python -m pytest -v
```

The repository currently defines 53 tests across authentication/security, integration, and migration coverage. The suite does not perform real LLM or TTS calls, and there is currently no frontend test or lint command.

Build the frontend with:

```bash
cd frontend
npm ci
npm run build
npm run preview
```

## Optional demo data

The seeder creates or resets a local demo account and **deletes that account's existing products** before inserting demo inventory.

```bash
docker exec -it stocksync-backend python seed_db.py
```

Local credentials:

```text
Email: demo@stocksync.ai
Password: demo123
```

Use this only for local development.

## Project structure

```text
StockSync/
├── backend/
│   ├── ai/agent.py                 # LLM and MCP tool loop
│   ├── auth/                       # Password and JWT handling
│   ├── db/                         # SQLAlchemy models, engine, schema setup
│   ├── mcp_bridge/                 # MCP subprocess lifecycle
│   ├── routes/                     # FastAPI auth/inventory/AI/history/TTS routes
│   ├── utils/import_parser.py      # CSV/XLSX parsing and column mapping
│   ├── main.py                     # FastAPI application and lifespan
│   ├── seed_db.py                  # Optional destructive demo seeder
│   └── requirements.txt
├── frontend/
│   ├── src/App.jsx                 # Application state and state-based navigation
│   ├── src/components/             # Landing, auth, dashboard, inventory, analytics, chat
│   ├── src/index.css               # Application design system
│   ├── package.json
│   └── vite.config.js
├── mcp_server/server.py            # Ten inventory MCP tools
├── tests/                          # pytest suite
├── .env.example                    # Backend environment template
├── .dockerignore
├── docker-compose.yml
├── Dockerfile.backend
├── Dockerfile.frontend
└── render.yaml                     # Render blueprint scaffold
```

## Known limitations

- The Settings page is a visual prototype. Save, account deletion, password change, and product deletion are not persisted by the backend.
- Logout clears browser storage but does not revoke the JWT before it expires.
- Chat messages are stored and displayed, but prior messages are not included in future LLM prompts; the assistant has no conversational memory.
- Dashboard value and low-stock analytics are calculated in the browser from at most 200 fetched products and are not authoritative for larger inventories.
- TTS is requested automatically only after a voice-originated question; there is no general replay button.
- “Clear Session” hides current UI messages but does not delete persisted server history.
- Audit history covers manual stock edits and AI stock updates, not every create/import/delete operation, and its UI component is not mounted.
- The AI stock-update tool does not currently reject negative quantities like the REST product schema does.
- `GET /health` is a static liveness response and does not verify PostgreSQL, MCP, or the LLM provider.
- The standalone MCP HTTP server has no authentication. Do not expose it publicly; the normal application uses a private stdio subprocess.
- `render.yaml`, `frontend/vercel.json`, and the split-deployment workflow are deployment scaffolds and need verification before production use.
- No first-party `LICENSE` file is present in this checkout. Confirm the intended license with the upstream project before redistributing it.

## Security notes

- Never commit `.env`, `.env.local`, API keys, JWT secrets, certificates, or database dumps.
- `.gitignore` ignores local environment files while explicitly allowing `.env.example` templates.
- `.dockerignore` excludes local environment files, dependency folders, caches, and build artifacts from Docker build contexts.
- Use a long random `SECRET_KEY`, HTTPS, restricted CORS origins, and managed secrets before any public deployment.
- The AI write path should be treated as a privileged operation until stronger confirmation, validation, and auditing are added.
