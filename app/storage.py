from __future__ import annotations
from typing import List, Dict
from collections import defaultdict

# In-memory store; swap with DB table if you want persistence
CONVOS: dict[str, List[Dict[str, str]]] = defaultdict(list)

def add_message(session_id: str, role: str, content: str):
    CONVOS[session_id].append({"role": role, "content": content})

def get_history(session_id: str) -> List[Dict[str, str]]:
    return CONVOS.get(session_id, [])[-20:]  # last 20 turns
