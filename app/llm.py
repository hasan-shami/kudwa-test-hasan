from __future__ import annotations
import os, json
from openai import OpenAI
from typing import Dict, Any, List
from .prompts import SYSTEM
from .tools import tool_schemas, tool_list_tables, tool_describe_table, tool_run_sql

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
client = OpenAI()

# Local dispatcher mapping tool name -> function
TOOL_IMPL = {
    "tool_list_tables": lambda args: tool_list_tables(),
    "tool_describe_table": lambda args: tool_describe_table(args["table_name"]),
    "tool_run_sql": lambda args: tool_run_sql(args["sql"], args.get("named_params")),
}

def run_agent(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    """
    Single-turn tool loop:
      user/system messages -> model -> (optional) tool calls -> model -> final JSON
    """
    response = client.responses.create(
        model=MODEL,
        messages=[{"role": "system", "content": SYSTEM}] + messages,
        tools=tool_schemas,
        temperature=0.2,
    )

    # Process tool calls (parallel friendly)
    tool_outputs = []
    for item in response.output:
        if item.type == "tool_call":
            name = item.tool_call.name
            args = json.loads(item.tool_call.arguments or "{}")
            result = TOOL_IMPL[name](args)
            tool_outputs.append({
                "tool_call_id": item.tool_call.id,
                "output": json.dumps(result)
            })

    # If tools were called, send their outputs back for a final answer
    if tool_outputs:
        response = client.responses.create(
            model=MODEL,
            messages=[{"role": "system", "content": SYSTEM}] + messages,
            tool_outputs=tool_outputs,
            temperature=0.2,
        )

    # Expecting a single JSON object from the model
    final_chunks = [c for c in response.output if c.type == "message"]
    text = ""
    if final_chunks and final_chunks[0].content:
        text = "".join([seg.text for seg in final_chunks[0].content if getattr(seg, "text", None)])

    try:
        return json.loads(text)
    except Exception:
        return {"answer": text.strip()[:2000], "table_preview": None, "followups": []}
