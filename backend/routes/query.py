"""
routes/query.py — The AI query endpoint.

Orchestrates the LLM agentic loop. Separated from auth and inventory concerns.
Rate limited to prevent LLM API abuse.

After each successful query, both the user question and AI answer are
automatically persisted to chat_history for the authenticated user.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from openai import APIConnectionError, AuthenticationError, NotFoundError, RateLimitError
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy.orm import Session

from auth.dependencies import CurrentUser
from db.connection import get_db
from db.models import ChatHistory
from ai.agent import AIConfigurationError, MCPUnavailableError, QueryResponse, run_query

from sqlalchemy import select

log = logging.getLogger(__name__)
router = APIRouter(prefix="/query", tags=["query"])
limiter = Limiter(key_func=get_remote_address)

MAX_CONTENT = 4000   # truncate very long AI answers before storing
STORE_LIMIT  = 500   # per-user cap; oldest rows are pruned automatically


class QueryRequest(BaseModel):
    question: str


def _persist_exchange(
    db: Session,
    user_id: int,
    question: str,
    answer: str,
    tool_used: str | None,
) -> None:
    """
    Save the user question and AI answer as two ChatHistory rows.
    Prunes oldest rows when the per-user cap is exceeded.
    Errors here are logged but never bubble up to the caller.
    """
    try:
        db.add(ChatHistory(
            user_id=user_id,
            role="user",
            content=question[:MAX_CONTENT],
            tool_used=None,
        ))
        db.add(ChatHistory(
            user_id=user_id,
            role="ai",
            content=answer[:MAX_CONTENT],
            tool_used=tool_used,
        ))
        db.flush()

        # Prune: keep only the most recent STORE_LIMIT rows
        keep_ids = (
            select(ChatHistory.id)
            .filter(ChatHistory.user_id == user_id)
            .order_by(ChatHistory.id.desc())
            .limit(STORE_LIMIT)
        )
        db.query(ChatHistory).filter(
            ChatHistory.user_id == user_id,
            ChatHistory.id.notin_(keep_ids),
        ).delete(synchronize_session=False)

        db.commit()
        log.info(f"[HISTORY] Persisted exchange for user_id={user_id}")
    except Exception as e:
        db.rollback()
        log.error(f"[HISTORY] Failed to persist exchange for user_id={user_id}: {e}")


@router.post("", response_model=QueryResponse)
@limiter.limit("30/minute")
async def query_inventory(
    request: Request,
    body: QueryRequest,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """
    Natural language inventory query endpoint.
    Requires a valid Bearer JWT token.
    Rate limited to 30 requests/minute per IP.

    Flow:
      1. Validate question
      2. Run agentic LLM loop (MCP → PostgreSQL, scoped to user_id)
      3. Persist user question + AI answer to chat_history
      4. Return QueryResponse
    """
    question = body.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="Question cannot be empty.")

    try:
        result = await run_query(question=question, current_user=current_user)
    except AuthenticationError as exc:
        log.error("[QUERY] Groq authentication failed", exc_info=True)
        raise HTTPException(status_code=503, detail="AI service authentication failed. Check GROQ_API_KEY.")
    except NotFoundError as exc:
        log.error("[QUERY] Configured Groq model is unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="The configured AI model is unavailable. Check LLM_MODEL.")
    except RateLimitError:
        raise HTTPException(status_code=429, detail="AI service rate limit reached. Please wait and retry.")
    except (APIConnectionError, AIConfigurationError):
        log.error("[QUERY] AI provider is unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="AI service is not configured or currently unavailable.")
    except MCPUnavailableError:
        log.error("[QUERY] Inventory MCP service is unavailable", exc_info=True)
        raise HTTPException(status_code=503, detail="Inventory assistant is temporarily unavailable. Please try again.")
    except Exception:
        log.exception(f"[QUERY] Failed for user_id={current_user['id']}")
        raise HTTPException(status_code=500, detail="Query failed. Please try again.")

    # Persist both sides of the exchange
    _persist_exchange(
        db=db,
        user_id=current_user["id"],
        question=question,
        answer=result.answer,
        tool_used=result.tool_used,
    )

    return result
