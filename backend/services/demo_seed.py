"""Seed the demo SQLite database from the frozen JSON catalog and customers.

The checkout pipeline itself creates proposals, payments, and audit events so
dashboard metrics stay consistent with pricing rules. Customer profiles,
conversations, and spend-mandate ledgers are written to match those results
plus the historical orders in demo_users.json.

`demo_user_01` (Aisha Khan) is not put through a new paid checkout here, so
the hero “order my usual, under ₹800” path keeps a clear spend cap.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from backend.config import get_settings
from backend.db import init_db
from backend.models import (
    AddonDecisionRequest,
    ConfirmationRequest,
    CreateProposalRequest,
    FailPaymentRequest,
    ProposalStatus,
    VerifyPaymentRequest,
)
from backend.services import checkout
from backend.services.data_loader import load_campaigns, load_demo_users, load_policy
from backend.services import store

SEED_EVENT = "demo_seed_loaded"

JOURNEYS: list[dict[str, Any]] = [
    {"user_id": "demo_user_02", "budget": 1500, "decision": "accept"},
    {"user_id": "demo_user_03", "budget": 2000, "decision": "accept"},
    {"user_id": "demo_user_04", "budget": 900, "decision": "skip"},
    {"user_id": "demo_user_05", "budget": 1000, "decision": "accept"},
    {"user_id": "demo_user_06", "budget": 1200, "decision": "accept"},
    {"user_id": "demo_user_07", "budget": 800, "decision": "accept"},
    {"user_id": "demo_user_08", "budget": 1100, "decision": "skip"},
    {"user_id": "demo_user_09", "budget": 700, "decision": "accept"},
    {"user_id": "demo_user_10", "budget": 2500, "decision": "accept"},
    {"user_id": "demo_user_10", "budget": 2500, "decision": "accept", "suffix": "b"},
    {"user_id": "demo_user_11", "budget": 600, "decision": "accept"},
    {
        "user_id": "demo_user_11",
        "budget": 600,
        "decision": "accept",
        "suffix": "agent",
        "buyer_type": "external_agent",
    },
    {"user_id": "demo_user_12", "budget": 1500, "decision": "accept"},
    {"user_id": "demo_user_13", "budget": 900, "decision": "accept"},
    {"user_id": "demo_user_14", "budget": 800, "decision": "accept", "fail": True},
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _already_seeded(path: Path) -> bool:
    if not path.exists():
        return False
    with _connect(path) as conn:
        try:
            row = conn.execute(
                "SELECT 1 FROM audit_events WHERE event_type = ? LIMIT 1",
                (SEED_EVENT,),
            ).fetchone()
        except sqlite3.OperationalError:
            return False
    return row is not None


def _wipe(path: Path) -> None:
    with _connect(path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        for name in tables:
            if not name.replace("_", "").isalnum():
                continue
            conn.execute(f'DELETE FROM "{name}"')
        conn.commit()


def _insert_customers(path: Path) -> int:
    data = load_demo_users()
    with _connect(path) as conn:
        for user in data.get("users", []):
            conn.execute(
                """
                INSERT INTO customers (
                    user_id, name, email, phone, city, signup_date,
                    preferred_budget_inr, preferences_json, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user["user_id"],
                    user["name"],
                    user.get("email") or "",
                    user.get("phone") or "",
                    user.get("city"),
                    user.get("signup_date"),
                    user.get("preferred_budget_inr"),
                    json.dumps(user.get("preferences") or {}, ensure_ascii=False),
                    user.get("notes"),
                ),
            )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
    return int(count)


def _enable_templates() -> None:
    for campaign in load_campaigns().get("campaigns", []):
        campaign_id = str(campaign.get("id") or "")
        if not campaign_id or not campaign.get("enabled", True):
            continue
        store.set_campaign_policy(
            campaign_id,
            "enabled",
            note="Seeded for the buildathon demo",
        )


def _reject_denied_sku() -> None:
    try:
        checkout.create_proposal(
            CreateProposalRequest(
                user_id="demo_user_04",
                product_ids=["prod_gift_card"],
                stated_budget_inr=800,
                with_growth=False,
                session_id="seed_guardrail_gift_card",
            )
        )
    except HTTPException as exc:
        if exc.status_code != 400:
            raise
        return
    raise RuntimeError("Gift-card proposal was accepted; denied category did not hold")


def _run_journey(spec: dict[str, Any], index: int) -> dict[str, Any]:
    user_id = spec["user_id"]
    budget = int(spec["budget"])
    session_id = f"seed_{user_id}_{spec.get('suffix', index)}"
    proposal = checkout.create_proposal(
        CreateProposalRequest(
            user_id=user_id,
            use_usual=True,
            stated_budget_inr=budget,
            with_growth=True,
            session_id=session_id,
            buyer_type=spec.get("buyer_type"),
        )
    )
    decision = spec.get("decision")
    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION and decision:
        if proposal.growth_offer is None:
            raise RuntimeError(f"{session_id} is awaiting an add-on with no offer")
        proposal = checkout.decide_addon(
            proposal.id,
            AddonDecisionRequest(
                decision=decision,
                product_id=proposal.growth_offer.product_id,
            ),
        )
    if proposal.status != ProposalStatus.AWAITING_CONFIRMATION:
        raise RuntimeError(f"{session_id} stuck in {proposal.status.value}")

    confirmed = checkout.confirm_proposal(
        proposal.id,
        ConfirmationRequest(
            expected_total_inr=proposal.total_inr,
            user_id=user_id,
            idempotency_key=f"seedpay_{proposal.id}",
            acknowledge_impulse_warning=True,
        ),
        force_mock=True,
    )
    if spec.get("fail"):
        checkout.fail_payment(proposal.id, FailPaymentRequest(reason="user_cancelled"))
        return {
            "user_id": user_id,
            "session_id": session_id,
            "proposal_id": proposal.id,
            "status": "failed",
            "total_inr": proposal.total_inr,
            "budget": budget,
            "decision": decision,
        }

    payment = confirmed["payment"]
    checkout.verify_payment(
        VerifyPaymentRequest(
            payment_id=payment.id,
            razorpay_order_id=payment.razorpay_order_id or "",
            razorpay_payment_id=f"pay_mock_seed_{proposal.id[-8:]}",
            razorpay_signature="mock_ok_seed",
        )
    )
    return {
        "user_id": user_id,
        "session_id": session_id,
        "proposal_id": proposal.id,
        "status": "paid",
        "total_inr": proposal.total_inr,
        "budget": budget,
        "decision": decision,
        "buyer_type": spec.get("buyer_type") or "human",
    }


def _date_key(raw: str | None) -> str | None:
    if not raw:
        return None
    return raw[:10]


def _month_key(raw: str | None) -> str | None:
    day = _date_key(raw)
    return day[:7] if day else None


def _write_mandates(path: Path, journeys: list[dict[str, Any]]) -> int:
    caps = load_policy().get("spend_mandates") or {}
    session_cap = int(caps.get("session_cap_inr", 2000))
    day_cap = int(caps.get("day_cap_inr", 3000))
    month_cap = int(caps.get("month_cap_inr", 7000))
    data = load_demo_users()
    now = _now()

    month_spent: dict[tuple[str, str], int] = defaultdict(int)
    day_spent: dict[tuple[str, str], int] = defaultdict(int)
    session_spent: dict[tuple[str, str], int] = defaultdict(int)

    for order in data.get("orders", []):
        if order.get("status") != "completed":
            continue
        user_id = order.get("user_id")
        created = order.get("created_at")
        total = int(order.get("total_inr") or 0)
        month = _month_key(created)
        day = _date_key(created)
        if user_id and month:
            month_spent[(user_id, month)] += total
        if user_id and day:
            day_spent[(user_id, day)] += total

    with _connect(path) as conn:
        paid_rows = conn.execute(
            """
            SELECT pay.amount_inr, pay.updated_at, prop.user_id, prop.session_id
            FROM payments AS pay
            JOIN proposals AS prop ON prop.id = pay.proposal_id
            WHERE pay.status = 'paid'
            """
        ).fetchall()
    for row in paid_rows:
        user_id = row["user_id"]
        total = int(row["amount_inr"] or 0)
        month = _month_key(row["updated_at"])
        day = _date_key(row["updated_at"])
        if user_id and month:
            month_spent[(user_id, month)] += total
        if user_id and day:
            day_spent[(user_id, day)] += total
        if user_id and row["session_id"]:
            session_spent[(user_id, row["session_id"])] += total

    today = datetime.now(timezone.utc).date()
    current_day = today.isoformat()
    current_month = today.strftime("%Y-%m")
    users = [u["user_id"] for u in data.get("users", [])]
    for user_id in users:
        month_spent.setdefault((user_id, current_month), 0)
        day_spent.setdefault((user_id, current_day), 0)

    for journey in journeys:
        session_spent.setdefault((journey["user_id"], journey["session_id"]), 0)

    rows: list[tuple] = []
    for (user_id, key), spent in month_spent.items():
        rows.append(
            (
                f"{user_id}:month:{key}",
                user_id,
                "month",
                key,
                month_cap,
                spent,
                0,
                now,
            )
        )
    for (user_id, key), spent in day_spent.items():
        rows.append(
            (
                f"{user_id}:day:{key}",
                user_id,
                "day",
                key,
                day_cap,
                spent,
                0,
                now,
            )
        )
    for (user_id, key), spent in session_spent.items():
        rows.append(
            (
                f"{user_id}:session:{key}",
                user_id,
                "session",
                key,
                session_cap,
                spent,
                0,
                now,
            )
        )

    with _connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO spend_mandates (
                mandate_id, user_id, period, period_key, cap_inr,
                spent_so_far_inr, reserved_inr, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(mandate_id) DO UPDATE SET
                spent_so_far_inr=excluded.spent_so_far_inr,
                cap_inr=excluded.cap_inr,
                reserved_inr=excluded.reserved_inr,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM spend_mandates").fetchone()[0]
    return int(count)


def _write_conversations(path: Path, journeys: list[dict[str, Any]]) -> int:
    data = load_demo_users()
    by_user: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for journey in journeys:
        by_user[journey["user_id"]].append(journey)
    now = _now()
    rows = []
    for user in data.get("users", []):
        user_id = user["user_id"]
        user_journeys = by_user.get(user_id) or []
        latest = user_journeys[-1] if user_journeys else None
        budget = int(user.get("preferred_budget_inr") or 800)
        first = user["name"].split()[0]
        if latest and latest["status"] == "paid":
            reply = (
                f"Paid ₹{latest['total_inr']} for {first}. "
                "Prices came from the catalog, and the add-on stayed optional."
            )
        elif latest and latest["status"] == "failed":
            reply = (
                f"Checkout for ₹{latest['total_inr']} was cancelled. "
                "No charge was captured. Ask again whenever you want the usual."
            )
        else:
            reply = (
                f"Welcome, {first}. I can rebuild your usual under ₹{budget} "
                "and offer at most one add-on."
            )
        state = {
            "user_id": user_id,
            "messages": [
                {
                    "role": "user",
                    "content": f"Order my usual, under ₹{budget}",
                },
                {"role": "assistant", "content": reply},
            ],
            "proposal_id": latest["proposal_id"] if latest else None,
            "stated_budget_inr": budget,
            "last_payment_id": None,
        }
        session_id = latest["session_id"] if latest else f"seed_{user_id}_intro"
        created = f"{user.get('signup_date') or '2026-01-01'}T10:00:00+05:30"
        rows.append(
            (
                session_id,
                user_id,
                created,
                now,
                json.dumps(state, ensure_ascii=False),
            )
        )

    with _connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO conversations (id, user_id, created_at, updated_at, state_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_id=excluded.user_id,
                state_json=excluded.state_json,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        conn.commit()
        count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
    return int(count)


def _summary(path: Path, *, skipped: bool) -> dict[str, Any]:
    with _connect(path) as conn:
        def count(table: str) -> int:
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

        paid = conn.execute(
            "SELECT COUNT(*) FROM payments WHERE status = 'paid'"
        ).fetchone()[0]
        return {
            "skipped": skipped,
            "db_path": str(path),
            "customers": count("customers"),
            "conversations": count("conversations"),
            "spend_mandates": count("spend_mandates"),
            "proposals": count("proposals"),
            "payments": count("payments"),
            "paid_payments": int(paid),
            "audit_events": count("audit_events"),
        }


def seed_demo_database(db_path: Path | None = None, *, force: bool = False) -> dict[str, Any]:
    """Populate the SQLite file `get_settings()` already points at.

    Pass `db_path` only as a check. Set `DATABASE_URL` first so checkout writes
    land in the same file.
    """
    settings = get_settings()
    path = Path(db_path) if db_path is not None else settings.sqlite_path
    if path.resolve() != settings.sqlite_path.resolve():
        raise RuntimeError(
            "DATABASE_URL must point at db_path before seeding "
            f"(settings={settings.sqlite_path}, requested={path})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    init_db(path)
    if not force and _already_seeded(path):
        return _summary(path, skipped=True)

    if force:
        _wipe(path)

    customers = _insert_customers(path)
    _enable_templates()
    _reject_denied_sku()
    journeys = [_run_journey(spec, index) for index, spec in enumerate(JOURNEYS, start=1)]
    mandates = _write_mandates(path, journeys)
    conversations = _write_conversations(path, journeys)

    from backend.audit.logger import log_event

    log_event(
        SEED_EVENT,
        payload={
            "customers": customers,
            "journeys": len(journeys),
            "paid": sum(1 for j in journeys if j["status"] == "paid"),
            "failed": sum(1 for j in journeys if j["status"] == "failed"),
            "spend_mandates": mandates,
            "conversations": conversations,
        },
    )
    summary = _summary(path, skipped=False)
    summary["journeys"] = journeys
    return summary


def export_sqlite_csv(db_path: Path, out_dir: Path) -> list[str]:
    """Write one CSV per user table. Returns the file names created."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    with _connect(db_path) as conn:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        for name in tables:
            if not name.replace("_", "").isalnum():
                continue
            cursor = conn.execute(f'SELECT * FROM "{name}"')
            filename = f"{name}.csv"
            target = out_dir / filename
            with target.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([col[0] for col in cursor.description or []])
                writer.writerows(cursor)
            written.append(filename)
    return written
