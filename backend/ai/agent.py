"""
ai/agent.py — Agentic LLM orchestration loop.

Extracted from main.py. Preserves the full multi-turn tool-calling loop.
user_id is now explicitly passed into every tool call for data isolation.
"""

import os
import json
import asyncio
import logging
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel

from mcp_bridge.client_manager import mcp_manager

log = logging.getLogger(__name__)

GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
PROVIDER = "groq"
API_KEY = GROQ_API_KEY
BASE_URL = "https://api.groq.com/openai/v1"
MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")

MAX_TURNS: int = int(os.environ.get("LLM_MAX_TURNS", "10"))

SYSTEM_PROMPT = """You are StockQuery AI, an intelligent inventory assistant with read and write access to a real PostgreSQL database.

RULES (follow strictly):
1. ANALYTICS & CHARTS: For graphs, charts, breakdowns, or category lists → use `get_inventory_analytics`, `get_category_analytics`, or `get_all_categories`.
2. OPTIONAL PARAMS: Tool parameters are optional. To find expensive items, call `search_inventory(sort_by="price_desc")` — leave other params null.
3. STOCK UPDATES: Use `update_stock` with `product_name` directly. You do not need to search for an ID first.
4. MULTI-STEP: Chain multiple tool calls without stopping (Search → Update → Analytics).
5. RESPONSES: Summarize results in natural language. Never paste raw JSON. If empty, say "No products found".
6. NO HALLUCINATION: Only use data from tool results. Never invent values.
7. USER ISOLATION: Every tool automatically filters to the current user's data. You do not need to provide user_id.
"""


class QueryResponse(BaseModel):
    answer: str
    tool_used: Optional[str] = None
    data: Optional[list] = None


def _get_openai_client() -> OpenAI:
    if not API_KEY:
        raise ValueError("GROQ_API_KEY is not configured.")
    return OpenAI(base_url=BASE_URL, api_key=API_KEY)


async def run_query(question: str, current_user: dict) -> QueryResponse:
    """
    Full multi-turn agentic loop.

    user_id is injected into every MCP tool call automatically,
    ensuring complete data isolation between users.
    """
    user_id = current_user["id"]
    log.info(f"[AGENT] Query from user_id={user_id}: {question!r}")

    client = _get_openai_client()
    tools = mcp_manager.get_tools()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": question},
    ]
    tool_used: Optional[str] = None
    data_result: Optional[list] = None

    def _llm_call():
        return client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=tools or None,
            tool_choice="auto" if tools else None,
            max_tokens=1024,
        )

    loop = asyncio.get_running_loop()

    for turn in range(MAX_TURNS):
        log.info(f"[AGENT] Turn {turn + 1}/{MAX_TURNS}")
        response = await loop.run_in_executor(None, _llm_call)
        msg = response.choices[0].message

        if not msg.tool_calls:
            log.info(f"[AGENT] Final answer on turn {turn + 1}")
            return QueryResponse(
                answer=msg.content or "No response generated.",
                tool_used=tool_used,
                data=data_result,
            )

        # Append assistant message (clean schema for strict APIs like Groq)
        msg_dict = {
            "role": "assistant",
            "content": msg.content or "",
        }
        if msg.tool_calls:
            msg_dict["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": tc.type,
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                } for tc in msg.tool_calls
            ]
        messages.append(msg_dict)

        # Execute each tool call — inject user_id for tenant isolation
        for tc in msg.tool_calls:
            name = tc.function.name
            tool_used = name if tool_used is None else f"{tool_used}, {name}"

            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            # Inject user_id into every tool call for data isolation
            args["user_id"] = user_id

            log.info(f"[AGENT] Calling tool '{name}' args={args}")
            result = await mcp_manager.call_tool(name, args)

            # Accumulate list/dict results for the frontend table
            if isinstance(result, list) and result:
                data_result = (data_result or []) + result
            elif isinstance(result, dict) and "error" not in result:
                data_result = (data_result or []) + [result]

            llm_content = result
            if isinstance(result, list) and len(result) > 50:
                llm_content = result[:50] + [{"_notice": f"Showing 50 of {len(result)} results. Remaining omitted to save context limit."}]

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(llm_content, default=str),
            })

    log.warning("[AGENT] Exhausted max turns without final answer")
    return QueryResponse(
        answer="I was unable to complete the query after multiple attempts.",
        tool_used=tool_used,
        data=None,
    )
