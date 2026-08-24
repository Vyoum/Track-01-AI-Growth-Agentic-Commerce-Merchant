"""Agent chat API."""

from __future__ import annotations

from pydantic import BaseModel, Field

from backend.agent.orchestrator import run_agent_turn
from backend.config import get_settings
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api", tags=["agent"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    user_id: str | None = None


@router.post("/chat")
def api_chat(body: ChatRequest):
    settings = get_settings()
    if not settings.effective_llm_api_key:
        # Still allow fallback + gates without Groq
        pass
    try:
        return run_agent_turn(
            message=body.message.strip(),
            user_id=body.user_id or settings.demo_user_id,
            session_id=body.session_id,
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Agent error: {exc}") from exc
