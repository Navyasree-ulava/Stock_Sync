"""
ai/clarification.py — Per-user pending intent state for the clarification loop.

When the model reports missing information, or the backend finds an ambiguous
product, the partial intent is parked here together with the conversation
needed to complete it. The next message from the same user is combined with
that context, so the user never has to repeat the original request.

Storage is in-process and bounded. A single backend process is the intended
deployment (the MCP server is already a private stdio subprocess); with more
than one backend replica this store must move to shared storage.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from .intent_schema import Clarification

# A pending intent is forgotten after this long so an abandoned clarification
# cannot be resumed weeks later.
TTL_SECONDS = 15 * 60
MAX_PENDING_USERS = 5_000
MAX_HISTORY = 6


@dataclass
class PendingIntent:
    """Everything needed to finish an interrupted request."""

    original_question: str
    intent: str = ""
    raw_intent: dict = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    clarification_question: str = ""
    candidates: list[dict] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    attempts: int = 0

    def to_context(self) -> dict:
        return {
            "original_request": self.original_question,
            "intent_so_far": self.raw_intent,
            "still_missing": list(self.missing),
            "candidates": [
                {"id": c.get("id"), "name": c.get("name"), "category": c.get("category")}
                for c in self.candidates[:10]
            ],
            "question_you_asked": self.clarification_question,
        }


class ClarificationStore:
    """Bounded, TTL'd, thread-safe map of user_id -> PendingIntent."""

    def __init__(self, ttl_seconds: int = TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._items: dict[int, PendingIntent] = {}
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        expired = [uid for uid, item in self._items.items() if now - item.created_at > self._ttl]
        for uid in expired:
            self._items.pop(uid, None)
        if len(self._items) > MAX_PENDING_USERS:
            oldest = sorted(self._items, key=lambda uid: self._items[uid].created_at)
            for uid in oldest[: len(self._items) - MAX_PENDING_USERS]:
                self._items.pop(uid, None)

    def get(self, user_id: int) -> Optional[PendingIntent]:
        now = time.time()
        with self._lock:
            self._prune(now)
            item = self._items.get(user_id)
            if item is None:
                return None
            if now - item.created_at > self._ttl:
                self._items.pop(user_id, None)
                return None
            return item

    def save(self, user_id: int, pending: PendingIntent) -> None:
        now = time.time()
        with self._lock:
            self._prune(now)
            self._items[user_id] = pending

    def clear(self, user_id: int) -> None:
        with self._lock:
            self._items.pop(user_id, None)

    def save_from_clarification(
        self,
        user_id: int,
        question: str,
        raw_intent: dict,
        intent: str,
        clarification: Clarification,
    ) -> PendingIntent:
        previous = self.get(user_id)
        pending = PendingIntent(
            original_question=question,
            intent=intent,
            raw_intent=dict(raw_intent or {}),
            missing=list(clarification.missing),
            clarification_question=clarification.question,
            attempts=(previous.attempts + 1) if previous else 1,
        )
        self.save(user_id, pending)
        return pending


clarification_store = ClarificationStore()
