from __future__ import annotations
from fastapi import APIRouter, HTTPException
from ..schemas import ChatRequest, ChatResponse
from ..storage import add_message, get_history
from ..llm import run_agent

router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("", response_model=ChatResponse)
def chat(req: ChatRequest):
    # Build dialogue: keep short history for context-aware followups
    history = get_history(req.session_id)
    history = history + [{"role": "user", "content": req.message}]
    try:
        result = run_agent(history)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Log turn
    add_message(req.session_id, "user", req.message)
    add_message(req.session_id, "assistant", result.get("answer", ""))

    return ChatResponse(
        answer=result.get("answer", ""),
        table_preview=result.get("table_preview"),
        followups=result.get("followups", []),
    )
