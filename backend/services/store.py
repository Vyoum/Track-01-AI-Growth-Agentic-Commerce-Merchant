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


def list_proposals_by_status(status: ProposalStatus, limit: int = 50) -> list[Proposal]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT payload_json FROM proposals
            WHERE status = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (status.value, limit),
        ).fetchall()
    return [Proposal.model_validate(json.loads(r["payload_json"])) for r in rows]


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


def get_payment(payment_id: str) -> PaymentRecord | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM payments WHERE id = ?",
            (payment_id,),
        ).fetchone()
    if not row:
        return None
    return PaymentRecord.model_validate(json.loads(row["payload_json"]))


def get_payment_by_razorpay_order(razorpay_order_id: str) -> PaymentRecord | None:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT payload_json FROM payments
            WHERE razorpay_order_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (razorpay_order_id,),
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


def claim_proposal_for_payment(proposal_id: str) -> tuple[bool, Proposal | None]:
    """Atomically move one awaiting proposal to payment_pending.

    BEGIN IMMEDIATE serializes competing writers. The status predicate is the
    compare-and-swap: only one caller can claim a proposal for Razorpay work.
    """
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT payload_json FROM proposals WHERE id = ?",
            (proposal_id,),
        ).fetchone()
        if not row:
            conn.rollback()
            return False, None

        proposal = Proposal.model_validate(json.loads(row["payload_json"]))
        if proposal.status != ProposalStatus.AWAITING_CONFIRMATION:
            conn.rollback()
            return False, proposal

        proposal.status = ProposalStatus.PAYMENT_PENDING
        if proposal.confirmed_at is None:
            proposal.confirmed_at = _now()
        payload = proposal.model_dump(mode="json")
        cursor = conn.execute(
            """
            UPDATE proposals
            SET status = ?, payload_json = ?, total_inr = ?
            WHERE id = ? AND status = ?
            """,
            (
                ProposalStatus.PAYMENT_PENDING.value,
                json.dumps(payload),
                proposal.total_inr,
                proposal.id,
                ProposalStatus.AWAITING_CONFIRMATION.value,
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            latest = get_proposal(proposal_id)
            return False, latest
        conn.commit()
        return True, proposal


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
