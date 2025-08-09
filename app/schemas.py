from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict

class ChatRequest(BaseModel):
    session_id: str = Field(..., description="Client-side conversation id")
    message: str

class ChatResponse(BaseModel):
    answer: str
    table_preview: Optional[List[Dict[str, Any]]] = None
    followups: Optional[List[str]] = None
