# StockSync

### AI-powered inventory management through natural language.

StockSync is a full-stack inventory management platform that lets users manage their inventory using natural-language commands.

Instead of filling out forms or remembering specific commands, users can simply describe what they want:

> **"Add an iPhone to electronics with 60 units at ₹100000 from Apple."**

StockSync understands the request, converts it into a structured operation, validates it, and executes the appropriate inventory action.

---

## ✨ What StockSync Does

- 🧠 **Natural-language inventory management**
- 📦 Create, update, search, and delete products
- 💬 Multi-turn conversations for incomplete requests
- 📊 Inventory dashboard and analytics
- 🔎 Product search and filtering
- 🗂️ Category-based organization
- 📥 CSV/XLSX inventory import
- 🔐 JWT authentication
- 👥 Multi-user tenant isolation
- 🛡️ Confirmation for destructive operations
- 🔌 MCP-based tool execution
- 🗄️ PostgreSQL persistence
- 🐳 Dockerized development environment

---

## 🧠 How It Works

StockSync uses an LLM to understand natural-language requests, while keeping business logic and database operations under deterministic backend control.

```text
                         User
                          │
                          ▼
                ┌──────────────────┐
                │  Natural Language │
                │     Request       │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │  LLM Intent       │
                │    Extraction     │
                └────────┬─────────┘
                         │
                         ▼
          ┌─────────────────────────────┐
          │      Backend Application    │
          │                             │
          │  Validation                 │
          │  Authorization              │
          │  Intent Handling            │
          │  Clarification              │
          │  Tool Selection             │
          └──────────────┬──────────────┘
                         │
                         ▼
                ┌──────────────────┐
                │    MCP Tools     │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │    PostgreSQL    │
                └────────┬─────────┘
                         │
                         ▼
                ┌──────────────────┐
                │ User-facing      │
                │ Response         │
                └──────────────────┘
```

### Core Design Principle

> **The LLM understands the request. The backend controls what actually happens.**

The model is used for language understanding and structured intent extraction.

Application validation, authorization, tool selection, and database operations remain under backend control.

---

## 💬 Natural-Language Interaction

Users can interact with the system naturally.

### Create

```text
Add an iPhone to electronics with 60 units,
price 100000, supplier Apple.
```

### Search

```text
Show all products in electronics.
```

### Filter

```text
Show products with stock below 10.
```

### Update

```text
Change iPhone price to 90000.
```

### Delete

```text
Delete iPhone.
```

Destructive operations require confirmation before execution.

---

## 🔄 Conversational Clarification

StockSync can continue an operation when the initial request does not contain all required information.

```text
User:
Add iPhone to electronics.

StockSync:
What price, stock quantity, and supplier should I use?

User:
₹100000, 60 units, Apple.

StockSync:
Created iPhone successfully.
```

The conversation state allows users to provide missing information naturally without repeating the entire request.

---

## 🏗️ Architecture

The application is divided into independent layers:

### Frontend

Provides the user interface for inventory management, analytics, imports, and conversational interaction.

### Backend

Handles:

- Authentication
- Request processing
- Validation
- Intent handling
- Authorization
- Clarification state
- Inventory APIs
- Application business logic

### LLM Layer

Converts natural-language requests into structured intents that the backend can process.

### MCP Layer

Provides typed tools for inventory operations between the application and database layer.

### Database

PostgreSQL stores users, products, inventory information, and application data.

---

## 🛠️ Tech Stack

| Category | Technologies |
|---|---|
| Frontend | React, Vite, Axios, Recharts |
| Backend | Python, FastAPI, Uvicorn |
| AI | LLM-based intent extraction |
| Tooling | Model Context Protocol (MCP) |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| Authentication | JWT, PyJWT, bcrypt |
| Testing | pytest, pytest-asyncio |
| Infrastructure | Docker, Docker Compose |

---

## 📁 Project Structure

```text
StockSync/
│
├── backend/
│   ├── ai/
│   ├── auth/
│   ├── db/
│   ├── mcp_bridge/
│   ├── routes/
│   ├── utils/
│   └── main.py
│
├── mcp_server/
│   └── server.py
│
├── frontend/
│   └── src/
│
├── tests/
│
├── docker-compose.yml
├── .env.example
└── README.md
```

---

## 🚀 Getting Started

### Prerequisites

- Docker
- Docker Compose
- Groq API key

### Clone the repository

```bash
git clone <repository-url>
cd StockSync
```

### Configure environment variables

```bash
cp .env.example .env
```

Configure the required variables:

```env
SECRET_KEY=<your-secret-key>
GROQ_API_KEY=<your-groq-api-key>
DATABASE_URL=<your-database-url>
```

Refer to `.env.example` for the complete configuration.

### Start the application

```bash
docker compose up --build
```

The application will be available at:

| Service | Address |
|---|---|
| Frontend | `http://localhost` |
| Backend | `http://127.0.0.1:8080` |
| Health Check | `http://127.0.0.1:8080/health` |
| PostgreSQL | `127.0.0.1:5433` |

### Seed sample data

```bash
docker exec -it stocksync-backend python seed_db.py
```

---

## 🧪 Testing

Run the automated test suite with:

```bash
cd backend
python -m pytest ../tests -q
```

The test suite covers:

- Authentication and authorization
- Tenant isolation
- Inventory operations
- Database behavior
- MCP tools
- Intent processing
- Conversational clarification
- Input validation
- Integration flows

---

## 🔐 Security

StockSync keeps application control separate from LLM interpretation.

Key security measures include:

- JWT-based authentication
- Password hashing
- Tenant-scoped database access
- Backend-controlled user identity
- Input validation
- Controlled MCP tool execution
- Confirmation for destructive operations
- Sanitized responses

The LLM does not directly execute SQL or access another user's inventory.

---

## 🎯 Why StockSync?

Traditional inventory systems require users to interact with structured forms and predefined workflows.

StockSync explores a different interaction model:

```text
Traditional Application

User → Form → API → Database
```

versus:

```text
StockSync

User → Natural Language → Intent → Backend → MCP → Database
```

The goal is to make inventory operations feel more conversational while retaining the reliability and control expected from a traditional backend application.

---

## 📌 Example Workflow

```text
User
│
│  "Add 50 Logitech mice to accessories at ₹2500 each."
│
▼
LLM
│
│  Structured intent
│
▼
Backend
│
│  Validate + authorize + resolve operation
│
▼
MCP
│
│  Execute typed inventory operation
│
▼
PostgreSQL
│
│  Persist inventory
│
▼
StockSync
│
│  Return result
│
▼
User
```

---

## 📈 Project Highlights

- Full-stack inventory management application
- Natural-language interface
- LLM-based structured intent extraction
- Deterministic backend business logic
- MCP tool integration
- PostgreSQL persistence
- JWT authentication and tenant isolation
- Conversational multi-turn workflows
- Automated test coverage
- Docker-based deployment

---

## License

No license has currently been specified for this repository.