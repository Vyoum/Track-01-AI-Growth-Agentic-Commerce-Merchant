"""Groq-powered agent orchestrator with deterministic money gates."""

from __future__ import annotations

import json
import re
import uuid
from typing import Any

from backend.agent.gates import try_gate_action
from backend.agent.prompts import SYSTEM_PROMPT
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
            return {"reply": f"Sorry, I couldn't build that order: {result['error']}"}
        session["proposal_id"] = result["proposal_id"]
        if result.get("growth_offer"):
            reply = result["growth_offer"]["offer_text"]
        else:
            reply = (
                f"I found your usual for ₹{result['total_inr']}. "
                "Say 'confirm payment' when you're ready."
            )
        return {"reply": reply, "proposal_id": result["proposal_id"], "tool_used": "fallback"}

    return {
        "reply": (
            "I can help order your usual (e.g. 'Order my usual, under ₹800'). "
            "Add GROQ_API_KEY for full conversational support."
        ),
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
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for m in session.get("messages", [])[-20:]:
        messages.append({"role": m["role"], "content": m["content"]})
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

        reply = (choice.content or "").strip()
        if not reply:
            reply = "How can I help with your order?"
        return {
            "reply": reply,
            "proposal_id": proposal_id_from_tools,
            "tool_trace": tool_trace,
        }

    return {
        "reply": "I need a moment — please try again or simplify your request.",
        "proposal_id": proposal_id_from_tools,
        "tool_trace": tool_trace,
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
        append_message(session, "assistant", gate["reply"])
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
            "reply": gate["reply"],
            "handled_by": gate.get("handled_by"),
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
    reply = agent_result["reply"]
    append_message(session, "assistant", reply)
    save(session)

    proposal_data = None
    pid = agent_result.get("proposal_id") or session.get("proposal_id")
    if pid:
        from backend.services import checkout as checkout_svc

        try:
            p = checkout_svc.get_proposal(pid)
            proposal_data = p.model_dump(mode="json")
            session["proposal_id"] = pid
            save(session)
        except Exception:  # noqa: BLE001
            pass

    log_event(
        "agent_reply",
        user_id=uid,
        session_id=sid,
        proposal_id=pid,
        payload={
            "tool_trace": agent_result.get("tool_trace", []),
            "fallback": agent_result.get("tool_used") == "fallback",
        },
    )

    return {
        "session_id": sid,
        "reply": reply,
        "handled_by": agent_result.get("tool_used", "groq"),
        "proposal": proposal_data,
        "checkout": None,
        "payment": None,
        "growth_summary": None,
    }
