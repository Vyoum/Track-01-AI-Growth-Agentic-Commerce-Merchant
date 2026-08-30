"""Deterministic addon/payment gates — LLM cannot bypass these."""

from __future__ import annotations

import re
from typing import Literal

from backend.models import AddonDecisionRequest, ConfirmationRequest, ProposalStatus
from backend.services import checkout, store
from backend.services.data_loader import load_policy

GateKind = Literal["addon_accept", "addon_skip", "payment_confirm", "payment_cancel", "none"]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _matches_any(text: str, phrases: list[str]) -> bool:
    norm = _normalize(text)
    for p in phrases:
        p_norm = _normalize(p)
        if p_norm and (norm == p_norm or p_norm in norm or norm in p_norm):
            return True
    return False


def classify_user_gate_message(message: str) -> GateKind:
    policy = load_policy()
    wording = policy.get("confirmation_wording", {})
    norm = _normalize(message)

    if _matches_any(norm, wording.get("payment_cancel_phrases", [])):
        return "payment_cancel"
    if _matches_any(norm, wording.get("payment_confirm_phrases", [])):
        return "payment_confirm"
    if _matches_any(norm, wording.get("addon_skip_phrases", [])):
        return "addon_skip"
    if _matches_any(norm, wording.get("addon_accept_phrases", [])):
        return "addon_accept"

    # Common natural variants
    if norm in {"yes", "yeah", "yep", "ok", "okay"}:
        return "addon_accept"
    if norm.startswith("yes,") or norm.startswith("yes "):
        if "pay" in norm or "confirm" in norm:
            return "payment_confirm"
        if "add" in norm or "usual" not in norm:
            return "addon_accept"

    return "none"


def try_gate_action(
    *,
    proposal_id: str | None,
    user_id: str,
    message: str,
) -> dict | None:
    """Run gated money actions from explicit user phrases. Returns result dict or None."""
    if not proposal_id:
        return None

    proposal = store.get_proposal(proposal_id)
    if not proposal:
        return None

    gate = classify_user_gate_message(message)

    if proposal.status == ProposalStatus.AWAITING_MERCHANT_APPROVAL:
        if gate in {"addon_accept", "addon_skip", "payment_confirm"}:
            camp = proposal.campaign_decision
            return {
                "handled_by": "merchant_gate",
                "reply": (
                    "A growth campaign is ready but needs merchant approval first. "
                    + (
                        f"Campaign: {camp.campaign_name} ({camp.opportunity}). "
                        if camp
                        else ""
                    )
                    + "Ask the merchant desk to Approve, then I can offer it to you."
                ),
                "proposal": proposal.model_dump(mode="json"),
                "checkout": None,
                "payment": None,
            }

    if proposal.status == ProposalStatus.AWAITING_ADDON_DECISION:
        if gate == "addon_accept":
            updated = checkout.decide_addon(
                proposal_id,
                AddonDecisionRequest(
                    decision="accept",
                    product_id=proposal.growth_offer.product_id if proposal.growth_offer else None,
                ),
            )
            from backend.agent.guardrails import payment_confirm_prompt

            return {
                "handled_by": "addon_gate",
                "reply": (
                    f"Added to your order. New total ₹{updated.total_inr}. "
                    f"{payment_confirm_prompt(updated)}"
                ),
                "proposal": updated.model_dump(mode="json"),
                "checkout": None,
                "payment": None,
            }
        if gate == "addon_skip":
            updated = checkout.decide_addon(
                proposal_id,
                AddonDecisionRequest(decision="skip"),
            )
            from backend.agent.guardrails import payment_confirm_prompt

            return {
                "handled_by": "addon_gate",
                "reply": (
                    f"Okay, keeping your usual at ₹{updated.total_inr}. "
                    f"{payment_confirm_prompt(updated)}"
                ),
                "proposal": updated.model_dump(mode="json"),
                "checkout": None,
                "payment": None,
            }
        # Block payment confirm while add-on pending
        if gate == "payment_confirm":
            return {
                "handled_by": "addon_gate",
                "reply": (
                    "Please accept or skip the optional add-on first, "
                    "then confirm payment."
                ),
                "proposal": proposal.model_dump(mode="json"),
                "checkout": None,
                "payment": None,
            }

    if proposal.status == ProposalStatus.AWAITING_CONFIRMATION:
        if gate == "payment_confirm":
            result = checkout.confirm_proposal(
                proposal_id,
                ConfirmationRequest(
                    expected_total_inr=proposal.total_inr,
                    user_id=user_id,
                    idempotency_key=f"chat_{proposal_id}_{proposal.total_inr}",
                ),
            )
            pay = result["payment"]
            checkout_payload = result.get("checkout")
            reply = (
                f"Payment order created for ₹{proposal.total_inr}. "
                + (
                    "Complete payment in the Razorpay checkout window."
                    if checkout_payload and not checkout_payload.get("mock")
                    else "Mock mode: use Verify in UI or complete test checkout."
                )
            )
            return {
                "handled_by": "payment_gate",
                "reply": reply,
                "proposal": result["proposal"].model_dump(mode="json"),
                "checkout": checkout_payload,
                "payment": pay.model_dump(mode="json") if pay else None,
                "growth_summary": result.get("growth_summary"),
            }
        if gate == "addon_accept":
            return {
                "handled_by": "payment_gate",
                "reply": (
                    "There is no pending add-on on this order. "
                    "Say 'confirm payment' when you're ready to pay."
                ),
                "proposal": proposal.model_dump(mode="json"),
                "checkout": None,
                "payment": None,
            }

    if gate == "payment_cancel" and proposal.status in {
        ProposalStatus.AWAITING_CONFIRMATION,
        ProposalStatus.AWAITING_ADDON_DECISION,
        ProposalStatus.AWAITING_MERCHANT_APPROVAL,
        ProposalStatus.PAYMENT_PENDING,
    }:
        cancelled = checkout.cancel_proposal(proposal_id)
        return {
            "handled_by": "payment_gate",
            "reply": "Order cancelled. Start a new request whenever you like.",
            "proposal": cancelled.model_dump(mode="json"),
            "checkout": None,
            "payment": None,
        }

    return None
