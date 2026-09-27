# StockSync

### AI-powered inventory management through natural language.

StockSync is a full-stack inventory management platform that lets users manage their inventory using natural-language commands.

Instead of filling out forms or remembering specific commands, users can simply describe what they want:

> **"Add an iPhone to electronics with 60 units at ₹100000 from Apple."**

StockSync understands the request, converts it into a structured operation, validates it, and executes the appropriate inventory action.

---

## ✨ What StockSync Does

- 🧠 **Natural-language inventory management**
- 🎙️ **Voice-based inventory commands**
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

Input can come from either **text or voice**.

```text
                         User
                          │
                    ┌─────┴─────┐
                    │           │
                 Text         Voice
                    │           │
                    │     ElevenLabs API
                    │           │
                    └─────┬─────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │  LLM Intent      │
                 │    Extraction    │
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

Users can interact with the system naturally through **text or voice**.

### Text

```text
Add an iPhone to electronics with 60 units,
price 100000, supplier Apple.
```

### Voice

Users can speak inventory commands naturally, for example:

> **"Add an iPhone to electronics. We have 60 units, the price is one lakh, and Apple is the supplier."**

Voice input is processed using the **ElevenLabs API** and then passed through the same natural-language intent pipeline used for text requests.

This means both text and voice ultimately use the same backend workflow:

```text
Text / Voice
     ↓
Intent Extraction
     ↓
Validation
     ↓
Inventory Operation
     ↓
Database
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

### Voice Layer

Uses the **ElevenLabs API** to support voice-based interaction with the inventory system.

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
| Voice | ElevenLabs API |
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
- ElevenLabs API key *(required for voice features)*

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
GROQ_API_KEY=<your-groq-api-key>
DATABASE_URL=<your-database-url>
ELEVENLABS_API_KEY=<your-elevenlabs-api-key>
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

