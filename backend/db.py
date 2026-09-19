"""SQLite bootstrap for demo-scale persistence."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from backend.config import get_settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    session_id TEXT,
    user_id TEXT,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT,
    status TEXT NOT NULL,
    total_inr INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    razorpay_order_id TEXT,
    status TEXT NOT NULL,
    amount_inr INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Last-line defense: a proposal can own at most one payment record.
CREATE UNIQUE INDEX IF NOT EXISTS ux_payments_proposal_id
ON payments(proposal_id);

-- Merchant enables a growth template once; runtime applies it within limits.
CREATE TABLE IF NOT EXISTS campaign_policies (
    campaign_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL
);

-- Persistent agent spend caps (session / day / month). spent_so_far moves
-- only after signature-verified payment. reserved_inr is a CAS hold taken
-- at confirm so two carts cannot both spend the same remaining room.
CREATE TABLE IF NOT EXISTS spend_mandates (
    mandate_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    period TEXT NOT NULL,
    period_key TEXT NOT NULL,
    cap_inr INTEGER NOT NULL,
    spent_so_far_inr INTEGER NOT NULL DEFAULT 0,
    reserved_inr INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acp_sessions (
    checkout_session_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    idempotency_key TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_acp_sessions_idempotency
ON acp_sessions(idempotency_key);


-- One hold per proposal so confirm retries do not double-reserve.
CREATE TABLE IF NOT EXISTS spend_mandate_holds (
    proposal_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    session_id TEXT,
    amount_inr INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


@contextmanager
def connect(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    path = db_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 10000")
    try:
        yield conn
    finally:
        conn.close()


def _migrate_spend_mandate_columns(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(spend_mandates)").fetchall()}
    if cols and "reserved_inr" not in cols:
        conn.execute(
            "ALTER TABLE spend_mandates ADD COLUMN reserved_inr INTEGER NOT NULL DEFAULT 0"
        )


def init_db(db_path: Path | None = None) -> Path:
    settings = get_settings()
    path = db_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate_spend_mandate_columns(conn)
        conn.commit()
    return path
