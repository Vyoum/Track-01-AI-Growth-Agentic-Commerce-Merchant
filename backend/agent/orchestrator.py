"""Groq-powered agent orchestrator with deterministic money gates."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from backend.agent.gates import try_gate_action
from backend.agent.prompts import get_system_prompt
from backend.agent.session import append_message, get_or_create, save
from backend.agent.tool_runner import run_tool
from backend.agent.tools_schema import TOOL_DEFINITIONS
from backend.audit.logger import log_event
from backend.config import get_settings


MAX_TOOL_ROUNDS = 6


def _extract_budget(message: str, default: int = 800) -> int:
    for pat in [
        r"under\s*₹?\s*(\d+)",
        r"below\s*₹?\s*(\d+)",
        r"₹\s*(\d+)",
        r"(\d+)\s*inr",
    ]:
        m = re.search(pat, message, re.IGNORECASE)
        if m:
            return int(m.group(1))
    return default


def _strip_markdown(text: str) -> str:
    """Remove markdown emphasis so chat bubbles stay plain text."""
    cleaned = text or ""
    cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
    cleaned = re.sub(r"__(.+?)__", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"\1", cleaned)
    cleaned = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", cleaned)
    cleaned = cleaned.replace("**", "").replace("__", "")
    # Leading markdown list markers / orphan asterisks at line start
    cleaned = re.sub(r"(?m)^\s*[\*\-•]\s+", "", cleaned)
    cleaned = re.sub(r"(?m)^\s*\*\s*", "", cleaned)
    return cleaned.strip()


def _format_items_numbered(items: list[dict[str, Any]] | list[Any]) -> str:
    lines: list[str] = []
    for i, item in enumerate(items, start=1):
        if isinstance(item, dict):
            name = item.get("name") or item.get("product_id") or "Item"
            price = item.get("line_total_inr", item.get("unit_price_inr", 0))
        else:
            name = getattr(item, "name", "Item")
            price = getattr(item, "line_total_inr", getattr(item, "unit_price_inr", 0))
        lines.append(f"{i}. {name} – ₹{price}")
    return "\n".join(lines)


def _confirmation_reply(
    *,
    items: list[Any],
    total_inr: int,
    budget_inr: int | None,
    source_reason: str | None = None,
) -> str:
    parts: list[str] = []
    if source_reason:
        parts.append(source_reason.rstrip(".") + ".")
    parts.append("Here are your items:")
    parts.append(_format_items_numbered(items))
    if budget_inr is not None:
        parts.append(f"Total: ₹{total_inr} (within your ₹{budget_inr} budget).")
    else:
        parts.append(f"Total: ₹{total_inr}.")
    parts.append('When you\'re ready, just say "confirm payment."')
    return "\n".join(parts)


def _normalize_assistant_reply(
    reply: str,
    *,
    proposal: dict[str, Any] | None = None,
) -> str:
    cleaned = _strip_markdown(reply)
    if not proposal:
        return cleaned
    status = proposal.get("status")
    items = proposal.get("items") or []
    if status == "awaiting_confirmation" and items:
        return _confirmation_reply(
            items=items,
            total_inr=int(proposal.get("total_inr") or 0),
            budget_inr=proposal.get("stated_budget_inr"),
            source_reason=proposal.get("source_reason"),
        )
    return cleaned


def _fallback_reply(user_id: str, message: str, session: dict[str, Any]) -> dict[str, Any]:
    """Minimal deterministic path when Groq key is missing."""
    norm = message.lower()
    if "usual" in norm or "regular" in norm or "same as last" in norm:
        budget = _extract_budget(message)
        session["stated_budget_inr"] = budget
        result = run_tool(
            "create_proposal_from_usual",
            {"user_id": user_id, "stated_budget_inr": budget, "with_growth": True},
            session_id=session["session_id"],
        )
        if "error" in result:
            return {
                "reply": f"Sorry, I couldn't build that order: {result['error']}",
                "tool_used": "fallback",
                "tool_trace": ["create_proposal_from_usual"],
            }
        session["proposal_id"] = result["proposal_id"]
        if result.get("status") == "awaiting_merchant_approval":
            reply = (
                f"{result['source_reason']}. "
                "An optional add-on is waiting for the store to enable it on Merchant. "
                "I will not add it until then."
            )
        elif (result.get("campaign") or {}).get("merchant_approval_status") == "paused":
            reply = (
                f"{result['source_reason']}. "
                "An optional add-on is waiting for the store to enable the template on Merchant. "
                "You can still confirm payment for this baseline cart."
            )
        elif result.get("growth_offer"):
            reply = (
                f"{result['source_reason']}. "
                f"{result['growth_offer']['offer_text']}"
            )
        else:
            reply = _confirmation_reply(
                items=result.get("items") or [],
                total_inr=int(result.get("total_inr") or 0),
                budget_inr=result.get("stated_budget_inr") or budget,
                source_reason=result.get("source_reason"),
            )
        return {
            "reply": _strip_markdown(reply),
            "proposal_id": result["proposal_id"],
            "tool_used": "fallback",
            "tool_trace": ["create_proposal_from_usual"],
        }

    return {
        "reply": (
            "I can help order your usual (e.g. 'Order my usual, under ₹800'). "
            "Add GROQ_API_KEY for full conversational support."
        ),
        "tool_used": "fallback",
        "tool_trace": [],
    }


def _groq_client():
    from openai import OpenAI

    settings = get_settings()
    key = settings.effective_llm_api_key
    if not key:
        return None
    return OpenAI(api_key=key, base_url=settings.llm_base_url)


def _run_groq_loop(
    session: dict[str, Any],
    user_message: str,
    user_id: str,
) -> dict[str, Any]:
    client = _groq_client()
    if client is None:
        return _fallback_reply(user_id, user_message, session)

    settings = get_settings()
    messages: list[dict[str, Any]] = [{"role": "system", "content": get_system_prompt()}]
    recent_messages = session.get("messages", [])[-20:]
    for m in recent_messages:
        messages.append({"role": m["role"], "content": m["content"]})
    # run_agent_turn stores the current user message before entering this loop.
    # Append only for defensive direct callers so Groq never sees it twice.
    last_message = recent_messages[-1] if recent_messages else {}
    if (
        last_message.get("role") != "user"
        or last_message.get("content") != user_message
    ):
        messages.append({"role": "user", "content": user_message})

    proposal_id_from_tools: str | None = session.get("proposal_id")
    tool_trace: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.chat.completions.create(
                model=settings.llm_model,
                messages=messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                temperature=0.2,
            )
        except Exception as exc:  # noqa: BLE001
            return {
                "reply": f"I couldn't reach the AI service ({exc}). Try again shortly.",
                "proposal_id": proposal_id_from_tools,
                "tool_trace": tool_trace,
                "tool_used": "groq_error",
                "error": str(exc),
            }

        choice = response.choices[0].message

        if choice.tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": choice.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in choice.tool_calls
                    ],
                }
            )
            for tc in choice.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                if name.startswith("create_proposal") and "user_id" not in args:
                    args["user_id"] = user_id
                result = run_tool(name, args, session_id=session["session_id"])
                tool_trace.append(name)
                if result.get("proposal_id"):
                    proposal_id_from_tools = result["proposal_id"]
                    session["proposal_id"] = proposal_id_from_tools
                    if result.get("stated_budget_inr"):
                        session["stated_budget_inr"] = result["stated_budget_inr"]
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            continue

        reply = _strip_markdown((choice.content or "").strip())
        if not reply:
            reply = "How can I help with your order?"
        return {
            "reply": reply,
            "proposal_id": proposal_id_from_tools,
            "tool_trace": tool_trace,
            "tool_used": "groq",
        }

    return {
        "reply": "I need a moment — please try again or simplify your request.",
        "proposal_id": proposal_id_from_tools,
        "tool_trace": tool_trace,
        "tool_used": "groq",
    }


def run_agent_turn(
    *,
    message: str,
    user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    uid = user_id or settings.demo_user_id
    session = get_or_create(session_id, uid)
    sid = session["session_id"]

    append_message(session, "user", message)
    log_event(
        "agent_user_message",
        user_id=uid,
        session_id=sid,
        proposal_id=session.get("proposal_id"),
        payload={"message": message},
    )

    # Deterministic gates first (addon / payment confirm)
    gate = try_gate_action(
        proposal_id=session.get("proposal_id"),
        user_id=uid,
        message=message,
    )
    if gate:
        gate_reply = _strip_markdown(gate["reply"])
        append_message(session, "assistant", gate_reply)
        if gate.get("proposal"):
            session["proposal_id"] = gate["proposal"].get("id")
        if gate.get("payment"):
            session["last_payment_id"] = gate["payment"].get("id")
        save(session)
        log_event(
            "agent_gate_handled",
            user_id=uid,
            session_id=sid,
            proposal_id=session.get("proposal_id"),
            payload={"handled_by": gate.get("handled_by")},
        )
        return {
            "session_id": sid,
            "reply": gate_reply,
            "handled_by": gate.get("handled_by"),
            "model": None,
            "tool_trace": [],
            "proposal": gate.get("proposal"),
            "checkout": gate.get("checkout"),
            "payment": gate.get("payment"),
            "growth_summary": gate.get("growth_summary"),
        }

    # Groq tool-calling loop
    agent_result = _run_groq_loop(session, message, uid)
    if agent_result.get("error") and (
        "usual" in message.lower() or "regular" in message.lower()
    ):
        agent_result = _fallback_reply(uid, message, session)
        agent_result["tool_used"] = "fallback_after_groq_error"

    proposal_data = None
    pid = agent_result.get("proposal_id") or session.get("proposal_id")
    if pid:
        from backend.services import checkout as checkout_svc

        try:
            p = checkout_svc.get_proposal(pid)
            proposal_data = p.model_dump(mode="json")
            session["proposal_id"] = pid
        except Exception:  # noqa: BLE001
            proposal_data = None

    reply = _normalize_assistant_reply(
        agent_result["reply"],
        proposal=proposal_data,
    )
    append_message(session, "assistant", reply)
    save(session)

    handled_by = agent_result.get("tool_used", "groq")
    tool_trace = agent_result.get("tool_trace", [])
    log_event(
        "agent_reply",
        user_id=uid,
        session_id=sid,
        proposal_id=pid,
        payload={
            "handled_by": handled_by,
            "model": settings.llm_model if handled_by == "groq" else None,
            "tool_trace": tool_trace,
            "fallback": handled_by.startswith("fallback"),
        },
    )

    return {
        "session_id": sid,
        "reply": reply,
        "handled_by": handled_by,
        "model": settings.llm_model if handled_by == "groq" else None,
        "tool_trace": tool_trace,
        "proposal": proposal_data,
        "checkout": None,
        "payment": None,
        "growth_summary": None,
    }
