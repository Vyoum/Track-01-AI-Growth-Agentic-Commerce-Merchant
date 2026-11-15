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

-- Mirror of demo_users.json so a seeded SQLite file is self-contained.
CREATE TABLE IF NOT EXISTS customers (
    user_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL,
    city TEXT,
    signup_date TEXT,
    preferred_budget_inr INTEGER,
    preferences_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT
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

-- Merchant identity is intentionally separate from buyer/customer identity.
-- Passwords and session tokens are never stored in plaintext.
CREATE TABLE IF NOT EXISTS merchants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    password_salt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS merchant_sessions (
    token_hash TEXT PRIMARY KEY,
    merchant_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    revoked_at TEXT,
    FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_merchant_sessions_merchant_id
ON merchant_sessions(merchant_id);
CREATE INDEX IF NOT EXISTS ix_merchant_sessions_expires_at
ON merchant_sessions(expires_at);

-- Campaign state is tenant-scoped. The older campaign_policies table remains
-- readable for the built-in demo tenant during migration.
CREATE TABLE IF NOT EXISTS merchant_campaign_policies (
    merchant_id TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    status TEXT NOT NULL,
    note TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (merchant_id, campaign_id),
    FOREIGN KEY (merchant_id) REFERENCES merchants(id) ON DELETE CASCADE
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
    conn.execute("PRAGMA foreign_keys = ON")
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


def _ensure_demo_merchant(conn: sqlite3.Connection) -> None:
    """Compatibility tenant for existing demo proposals and policy state."""
    conn.execute(
        """
        INSERT OR IGNORE INTO merchants (
            id, name, slug, email, password_hash, password_salt,
            status, created_at, updated_at
        ) VALUES (
            'merchant_demo', 'Demo Fitness Store', 'demo-fitness-store',
            'demo-system@invalid.local', 'disabled', 'disabled',
            'system', '1970-01-01T00:00:00+00:00', '1970-01-01T00:00:00+00:00'
        )
        """
    )


def init_db(db_path: Path | None = None) -> Path:
    settings = get_settings()
    path = db_path or settings.sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect(path) as conn:
        conn.executescript(SCHEMA)
        _ensure_demo_merchant(conn)
        _migrate_spend_mandate_columns(conn)
        conn.commit()
    return path
