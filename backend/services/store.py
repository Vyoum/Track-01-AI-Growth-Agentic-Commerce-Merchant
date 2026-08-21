"""SQLite persistence for proposals and payments."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from backend.db import connect
from backend.models import PaymentRecord, PaymentStatus, Proposal, ProposalStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def save_proposal(proposal: Proposal) -> None:
    payload = proposal.model_dump(mode="json")
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO proposals (
                id, user_id, session_id, status, total_inr,
                payload_json, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                status=excluded.status,
                total_inr=excluded.total_inr,
                payload_json=excluded.payload_json,
                expires_at=excluded.expires_at
            """,
            (
                proposal.id,
                proposal.user_id,
                proposal.session_id,
                proposal.status.value,
                proposal.total_inr,
                json.dumps(payload),
                proposal.created_at.isoformat(),
                proposal.expires_at.isoformat(),
            ),
        )
        conn.commit()


def get_proposal(proposal_id: str) -> Proposal | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
    if not row:
        return None
    return Proposal.model_validate(json.loads(row["payload_json"]))


def save_payment(payment: PaymentRecord) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO payments (
                id, proposal_id, razorpay_order_id, status,
                amount_inr, payload_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                razorpay_order_id=excluded.razorpay_order_id,
                status=excluded.status,
                amount_inr=excluded.amount_inr,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (
                payment.id,
                payment.proposal_id,
                payment.razorpay_order_id,
                payment.status.value,
                payment.amount_inr,
                json.dumps(payment.model_dump(mode="json")),
                payment.created_at.isoformat(),
                payment.updated_at.isoformat(),
            ),
        )
        conn.commit()


def get_payment_by_proposal(proposal_id: str) -> PaymentRecord | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM payments
            WHERE proposal_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (proposal_id,),
        ).fetchone()
    if not row:
        return None
    return PaymentRecord.model_validate(json.loads(row["payload_json"]))


def get_payment_by_idempotency(idempotency_key: str) -> PaymentRecord | None:
    with connect() as conn:
        rows = conn.execute("SELECT payload_json FROM payments").fetchall()
    for row in rows:
        payload: dict[str, Any] = json.loads(row["payload_json"])
        if payload.get("payload", {}).get("idempotency_key") == idempotency_key:
            return PaymentRecord.model_validate(payload)
    return None


def mark_proposal_status(proposal: Proposal, status: ProposalStatus) -> Proposal:
    proposal.status = status
    if status == ProposalStatus.CONFIRMED and proposal.confirmed_at is None:
        proposal.confirmed_at = _now()
    save_proposal(proposal)
    return proposal


def mark_payment_status(payment: PaymentRecord, status: PaymentStatus) -> PaymentRecord:
    payment.status = status
    payment.updated_at = _now()
    save_payment(payment)
    return payment
