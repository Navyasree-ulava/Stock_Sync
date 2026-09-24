"""
routes/history.py — Persistent chat history endpoints.

Stores user queries and AI responses in the DB so they survive
page refreshes and browser restarts.
"""

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel

from auth.dependencies import CurrentUser
from db.connection import get_db

from sqlalchemy.orm import Session
from db.models import ChatHistory

log = logging.getLogger(__name__)
router = APIRouter(prefix="/history", tags=["history"])

MAX_HISTORY = 100   # rows returned per fetch
STORE_LIMIT = 500   # hard cap of rows kept per user (oldest pruned)


# ── Pydantic Models ────────────────────────────────────────────────────────────

class MessageCreate(BaseModel):
    role: str           # 'user' | 'ai'
    content: str
    tool_used: Optional[str] = None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
async def get_history(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Return the last 100 messages for the authenticated user (oldest first)."""
    user_id = current_user["id"]
    rows = db.query(ChatHistory).filter(
        ChatHistory.user_id == user_id
    ).order_by(ChatHistory.id.desc()).limit(MAX_HISTORY).all()

    # Return in chronological order (oldest first for rendering)
    messages = []
    for r in reversed(rows):
        messages.append({
            "id": r.id,
            "role": r.role,
            "content": r.content,
            "tool_used": r.tool_used,
            "created_at": r.created_at.isoformat() if r.created_at else None
        })
        
    return {"messages": messages}


@router.post("/message", status_code=status.HTTP_201_CREATED)
async def save_message(
    body: MessageCreate,
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Persist a single chat message (user query OR ai response)."""
    user_id = current_user["id"]

    if body.role not in ("user", "ai"):
        return {"ok": False, "reason": "Invalid role"}

    content = body.content.strip()
    if not content:
        return {"ok": False, "reason": "Empty content"}

    new_msg = ChatHistory(
        user_id=user_id,
        role=body.role,
        content=content[:4000],
        tool_used=body.tool_used
    )
    db.add(new_msg)
    db.flush() # Flush to get new ID before subquery

    # Prune oldest rows if over the per-user cap
    from sqlalchemy import select
    subq = (
        select(ChatHistory.id)
        .filter(ChatHistory.user_id == user_id)
        .order_by(ChatHistory.id.desc())
        .limit(STORE_LIMIT)
    )
    
    db.query(ChatHistory).filter(
        ChatHistory.user_id == user_id,
        ChatHistory.id.notin_(subq)
    ).delete(synchronize_session=False)

    db.commit()
    log.info(f"[HISTORY] Saved role={body.role} for user_id={user_id}")
    return {"ok": True}


@router.delete("", status_code=status.HTTP_200_OK)
async def clear_history(
    current_user: CurrentUser,
    db: Annotated[Session, Depends(get_db)],
):
    """Delete all chat history for the authenticated user."""
    user_id = current_user["id"]
    db.query(ChatHistory).filter(ChatHistory.user_id == user_id).delete()
    db.commit()
    log.info(f"[HISTORY] Cleared all history for user_id={user_id}")
    return {"ok": True, "message": "History cleared."}

