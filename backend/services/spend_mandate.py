"""Persistent spend mandate ledger — session / day / month caps.

Per-order stated budget still applies. Confirm takes a CAS reservation so two
carts cannot both spend the same remaining room. spent_so_far moves only after
signature-verified payment. Dismiss / fail releases the hold without incrementing
spent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from backend.db import connect
from backend.services.data_loader import load_policy

PERIODS = ("session", "day", "month")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _caps() -> dict[str, int]:
    cfg = load_policy().get("spend_mandates") or {}
    return {
        "session": int(cfg.get("session_cap_inr", 2000)),
        "day": int(cfg.get("day_cap_inr", 3000)),
        "month": int(cfg.get("month_cap_inr", 7000)),
    }


def _period_key(period: str, *, session_id: str | None, now: datetime) -> str:
    if period == "session":
        return (session_id or "no_session").strip() or "no_session"
    if period == "day":
        return now.date().isoformat()
    return now.strftime("%Y-%m")


def _mandate_id(user_id: str, period: str, period_key: str) -> str:
    return f"{user_id}:{period}:{period_key}"


def mandate_applies(*, session_id: str | None, force_mock: bool) -> bool:
    if force_mock:
        return False
    sid = session_id or ""
    return not sid.startswith("replay_")


def _row_to_dict(row, cap: int, period: str, period_key: str) -> dict[str, Any]:
    spent = int(row["spent_so_far_inr"]) if row else 0
    reserved = int(row["reserved_inr"]) if row and "reserved_inr" in row.keys() else 0
    remaining = max(0, cap - spent - reserved)
    return {
        "period": period,
        "period_key": period_key,
        "cap_inr": cap,
        "spent_so_far_inr": spent,
        "reserved_inr": reserved,
        "remaining_inr": remaining,
    }


def snapshot(user_id: str, session_id: str | None = None) -> dict[str, Any]:
    now = _now()
    caps = _caps()
    ledgers: list[dict[str, Any]] = []
    with connect() as conn:
        for period in PERIODS:
            key = _period_key(period, session_id=session_id, now=now)
            mid = _mandate_id(user_id, period, key)
            row = conn.execute(
                """
                SELECT spent_so_far_inr, reserved_inr
                FROM spend_mandates WHERE mandate_id = ?
                """,
                (mid,),
            ).fetchone()
            ledgers.append(_row_to_dict(row, caps[period], period, key))
    tightest = min(ledgers, key=lambda item: item["remaining_inr"])
    return {
        "user_id": user_id,
        "session_id": session_id,
        "ledgers": ledgers,
        "tightest_remaining_inr": tightest["remaining_inr"],
        "tightest_period": tightest["period"],
        "note": (
            "Persistent spend caps reduce impulsive repeat checkouts. "
            "You can still continue after an explicit warning."
        ),
    }


def _already_warned(proposal_id: str | None) -> bool:
    if not proposal_id:
        return False
    from backend.audit.logger import list_events

    events = list_events(limit=300)
    return any(
        e["event_type"] == "mandate_impulse_warning"
        and (e["payload"] or {}).get("proposal_id") == proposal_id
        for e in events
    )


def _warning(
    *,
    ledger: dict[str, Any],
    cart_total_inr: int,
    proposal_id: str | None,
) -> dict[str, Any]:
    remaining = ledger["remaining_inr"]
    return {
        "code": "MANDATE_CAP_WARNING",
        "period": ledger["period"],
        "period_key": ledger["period_key"],
        "cap_inr": ledger["cap_inr"],
        "spent_so_far_inr": ledger["spent_so_far_inr"],
        "reserved_inr": ledger.get("reserved_inr", 0),
        "remaining_inr": remaining,
        "cart_total_inr": cart_total_inr,
        "proposal_id": proposal_id,
        "bypass_allowed": True,
        "message": (
            f"Impulse-shopping warning: this cart (₹{cart_total_inr}) would pass "
            f"your {ledger['period']} spend cap (₹{ledger['cap_inr']}; "
            f"₹{remaining} left). You can continue anyway — we will not block "
            "the purchase, but this pause is here to reduce impulsive repeat orders."
        ),
    }


def _snapshot_on(conn, user_id: str, session_id: str | None, now: datetime) -> dict[str, Any]:
    caps = _caps()
    ledgers: list[dict[str, Any]] = []
    for period in PERIODS:
        key = _period_key(period, session_id=session_id, now=now)
        mid = _mandate_id(user_id, period, key)
        row = conn.execute(
            "SELECT spent_so_far_inr, reserved_inr FROM spend_mandates WHERE mandate_id = ?",
            (mid,),
        ).fetchone()
        ledgers.append(_row_to_dict(row, caps[period], period, key))
    tightest = min(ledgers, key=lambda item: item["remaining_inr"])
    return {
        "user_id": user_id,
        "session_id": session_id,
        "ledgers": ledgers,
        "tightest_remaining_inr": tightest["remaining_inr"],
        "tightest_period": tightest["period"],
        "note": (
            "Persistent spend caps reduce impulsive repeat checkouts. "
            "You can still continue after an explicit warning."
        ),
    }


def assert_within_mandate(
    *,
    user_id: str,
    session_id: str | None,
    cart_total_inr: int,
    proposal_id: str | None = None,
    force_mock: bool = False,
    acknowledge_impulse_warning: bool = False,
) -> dict[str, Any]:
    """Warn if needed, then CAS-reserve cart_total against all period ledgers."""
    state = snapshot(user_id, session_id)
    if not mandate_applies(session_id=session_id, force_mock=force_mock):
        return {
            **state,
            "enforced": False,
            "warning": None,
            "bypassed": False,
            "reserved": False,
        }
    if cart_total_inr <= 0:
        return {**state, "enforced": True, "warning": None, "bypassed": False, "reserved": False}

    from backend.audit.logger import log_event

    bypass = bool(acknowledge_impulse_warning or _already_warned(proposal_id))
    now = _now()
    ts = now.isoformat()
    caps = _caps()
    amount = int(cart_total_inr)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if proposal_id:
            existing = conn.execute(
                "SELECT amount_inr FROM spend_mandate_holds WHERE proposal_id = ?",
                (proposal_id,),
            ).fetchone()
            if existing:
                conn.commit()
                state = snapshot(user_id, session_id)
                return {
                    **state,
                    "enforced": True,
                    "warning": None,
                    "bypassed": bypass,
                    "reserved": True,
                }

        state = _snapshot_on(conn, user_id, session_id, now)
        blocking = next(
            (ledger for ledger in state["ledgers"] if amount > ledger["remaining_inr"]),
            None,
        )
        if blocking and not bypass:
            warning = _warning(
                ledger=blocking, cart_total_inr=amount, proposal_id=proposal_id
            )
            conn.commit()
            log_event(
                "mandate_impulse_warning",
                user_id=user_id,
                session_id=session_id,
                proposal_id=proposal_id,
                payload=warning,
            )
            raise HTTPException(status_code=409, detail=warning)

        for period in PERIODS:
            key = _period_key(period, session_id=session_id, now=now)
            mid = _mandate_id(user_id, period, key)
            cap = caps[period]
            conn.execute(
                """
                INSERT INTO spend_mandates (
                    mandate_id, user_id, period, period_key,
                    cap_inr, spent_so_far_inr, reserved_inr, updated_at
                ) VALUES (?, ?, ?, ?, ?, 0, 0, ?)
                ON CONFLICT(mandate_id) DO NOTHING
                """,
                (mid, user_id, period, key, cap, ts),
            )
            if bypass:
                cursor = conn.execute(
                    """
                    UPDATE spend_mandates
                    SET reserved_inr = reserved_inr + ?,
                        cap_inr = ?,
                        updated_at = ?
                    WHERE mandate_id = ?
                    """,
                    (amount, cap, ts, mid),
                )
            else:
                cursor = conn.execute(
                    """
                    UPDATE spend_mandates
                    SET reserved_inr = reserved_inr + ?,
                        cap_inr = ?,
                        updated_at = ?
                    WHERE mandate_id = ?
                      AND spent_so_far_inr + reserved_inr + ? <= cap_inr
                    """,
                    (amount, cap, ts, mid, amount),
                )
            if cursor.rowcount != 1:
                conn.rollback()
                state = snapshot(user_id, session_id)
                ledger = next(
                    item for item in state["ledgers"] if item["period"] == period
                )
                warning = _warning(
                    ledger=ledger, cart_total_inr=amount, proposal_id=proposal_id
                )
                log_event(
                    "mandate_impulse_warning",
                    user_id=user_id,
                    session_id=session_id,
                    proposal_id=proposal_id,
                    payload={**warning, "cas_lost": True},
                )
                raise HTTPException(status_code=409, detail=warning)

        if proposal_id:
            conn.execute(
                """
                INSERT INTO spend_mandate_holds (
                    proposal_id, user_id, session_id, amount_inr, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (proposal_id, user_id, session_id, amount, ts),
            )
        conn.commit()

    state = snapshot(user_id, session_id)
    if bypass and blocking:
        warning = _warning(
            ledger=blocking, cart_total_inr=amount, proposal_id=proposal_id
        )
        log_event(
            "mandate_cap_bypassed",
            user_id=user_id,
            session_id=session_id,
            proposal_id=proposal_id,
            payload=warning,
        )
        return {
            **state,
            "enforced": True,
            "warning": warning,
            "bypassed": True,
            "reserved": True,
        }
    return {
        **state,
        "enforced": True,
        "warning": None,
        "bypassed": False,
        "reserved": True,
    }


def release_reservation(
    *,
    user_id: str,
    session_id: str | None,
    amount_inr: int,
    proposal_id: str | None = None,
    skip: bool = False,
) -> dict[str, Any]:
    """Drop a confirm-time hold (dismiss, failed signature, Razorpay create fail)."""
    if skip or amount_inr <= 0:
        return snapshot(user_id, session_id)

    now = _now()
    ts = now.isoformat()
    caps = _caps()
    amount = int(amount_inr)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if proposal_id:
            deleted = conn.execute(
                "DELETE FROM spend_mandate_holds WHERE proposal_id = ?",
                (proposal_id,),
            )
            if deleted.rowcount != 1:
                conn.commit()
                return snapshot(user_id, session_id)
        for period in PERIODS:
            key = _period_key(period, session_id=session_id, now=now)
            mid = _mandate_id(user_id, period, key)
            cap = caps[period]
            conn.execute(
                """
                UPDATE spend_mandates
                SET reserved_inr = MAX(0, reserved_inr - ?),
                    cap_inr = ?,
                    updated_at = ?
                WHERE mandate_id = ?
                """,
                (amount, cap, ts, mid),
            )
        conn.commit()
    return snapshot(user_id, session_id)


def record_verified_spend(
    *,
    user_id: str,
    session_id: str | None,
    amount_inr: int,
    proposal_id: str | None = None,
    skip: bool = False,
) -> dict[str, Any]:
    """Move a reservation into spent_so_far, or no-op when replay skipped."""
    if skip or amount_inr <= 0:
        return snapshot(user_id, session_id)

    now = _now()
    ts = now.isoformat()
    caps = _caps()
    amount = int(amount_inr)

    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        had_hold = True
        if proposal_id:
            deleted = conn.execute(
                "DELETE FROM spend_mandate_holds WHERE proposal_id = ?",
                (proposal_id,),
            )
            had_hold = deleted.rowcount == 1
        for period in PERIODS:
            key = _period_key(period, session_id=session_id, now=now)
            mid = _mandate_id(user_id, period, key)
            cap = caps[period]
            if had_hold:
                conn.execute(
                    """
                    INSERT INTO spend_mandates (
                        mandate_id, user_id, period, period_key,
                        cap_inr, spent_so_far_inr, reserved_inr, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(mandate_id) DO UPDATE SET
                        spent_so_far_inr = spend_mandates.spent_so_far_inr + excluded.spent_so_far_inr,
                        reserved_inr = MAX(0, spend_mandates.reserved_inr - excluded.spent_so_far_inr),
                        cap_inr = excluded.cap_inr,
                        updated_at = excluded.updated_at
                    """,
                    (mid, user_id, period, key, cap, amount, ts),
                )
            else:
                # Legacy path / double-verify: do not add spent twice if hold is gone.
                conn.execute(
                    """
                    INSERT INTO spend_mandates (
                        mandate_id, user_id, period, period_key,
                        cap_inr, spent_so_far_inr, reserved_inr, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 0, ?)
                    ON CONFLICT(mandate_id) DO UPDATE SET
                        cap_inr = excluded.cap_inr,
                        updated_at = excluded.updated_at
                    """,
                    (mid, user_id, period, key, cap, amount, ts),
                )
        conn.commit()

    state = snapshot(user_id, session_id)
    from backend.audit.logger import log_event

    log_event(
        "mandate_spend_recorded",
        user_id=user_id,
        session_id=session_id,
        proposal_id=proposal_id,
        payload={
            "amount_inr": amount,
            "ledgers": state["ledgers"],
        },
    )
    return state
