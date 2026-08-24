"""Server-side chat session state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from backend.db import connect


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    return f"sess_{uuid.uuid4().hex[:16]}"


def get_or_create(session_id: str | None, user_id: str) -> dict[str, Any]:
    sid = session_id or new_session_id()
    with connect() as conn:
        row = conn.execute(
            "SELECT state_json, created_at FROM conversations WHERE id = ?",
            (sid,),
        ).fetchone()
        if row:
            state = json.loads(row["state_json"])
            state["session_id"] = sid
            state["user_id"] = user_id
            return state

        state = {
            "session_id": sid,
            "user_id": user_id,
            "messages": [],
            "proposal_id": None,
            "stated_budget_inr": None,
            "last_payment_id": None,
        }
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO conversations (id, user_id, created_at, updated_at, state_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (sid, user_id, now, now, json.dumps(state)),
        )
        conn.commit()
    return state


def save(state: dict[str, Any]) -> None:
    sid = state["session_id"]
    now = _now_iso()
    payload = {k: v for k, v in state.items() if k != "session_id"}
    with connect() as conn:
        conn.execute(
            """
            UPDATE conversations SET updated_at = ?, state_json = ? WHERE id = ?
            """,
            (now, json.dumps(payload), sid),
        )
        if conn.total_changes == 0:
            conn.execute(
                """
                INSERT INTO conversations (id, user_id, created_at, updated_at, state_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (sid, state.get("user_id", "demo_user_01"), now, now, json.dumps(payload)),
            )
        conn.commit()


def append_message(state: dict[str, Any], role: str, content: str) -> None:
    state.setdefault("messages", []).append({"role": role, "content": content})
    # Keep last 20 turns for context window
    if len(state["messages"]) > 40:
        state["messages"] = state["messages"][-40:]
