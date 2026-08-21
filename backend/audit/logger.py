"""Structured audit trail — SQLite + optional JSONL, secrets redacted."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.config import get_settings
from backend.db import connect

JSONL_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"

_SECRET_KEYS = {
    "razorpay_key_secret",
    "key_secret",
    "secret",
    "password",
    "authorization",
    "api_key",
    "llm_api_key",
}
_SECRET_PATTERN = re.compile(r"(rzp_test_|rzp_live_)[A-Za-z0-9]+")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in _SECRET_KEYS or str(k).lower().endswith("_secret"):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _SECRET_PATTERN.sub(r"\1***", value)
    return value


def log_event(
    event_type: str,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    proposal_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one audit event. Safe to call from request handlers."""
    event = {
        "ts": _now_iso(),
        "event_type": event_type,
        "user_id": user_id,
        "session_id": session_id,
        "proposal_id": proposal_id,
        "payload": redact(payload or {}),
    }

    # Ensure DB exists (noop if already initialized)
    get_settings().sqlite_path.parent.mkdir(parents=True, exist_ok=True)

    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_events (ts, session_id, user_id, event_type, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event["ts"],
                session_id,
                user_id,
                event_type,
                json.dumps({**event["payload"], "proposal_id": proposal_id}),
            ),
        )
        conn.commit()
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    event["id"] = row_id

    try:
        JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass

    return event


def list_events(
    *,
    proposal_id: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if session_id:
        clauses.append("session_id = ?")
        params.append(session_id)
    if user_id:
        clauses.append("user_id = ?")
        params.append(user_id)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = (
        f"SELECT id, ts, session_id, user_id, event_type, payload_json "
        f"FROM audit_events {where} ORDER BY id DESC LIMIT ?"
    )
    params.append(limit)

    with connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    events: list[dict[str, Any]] = []
    for row in rows:
        payload = json.loads(row["payload_json"])
        if proposal_id and payload.get("proposal_id") != proposal_id:
            continue
        events.append(
            {
                "id": row["id"],
                "ts": row["ts"],
                "session_id": row["session_id"],
                "user_id": row["user_id"],
                "event_type": row["event_type"],
                "payload": payload,
            }
        )
    return list(reversed(events))
