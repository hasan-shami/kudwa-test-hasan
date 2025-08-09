from __future__ import annotations
import os, json
from typing import Dict, Any, List
from openai import OpenAI
from .prompts import SYSTEM
from .tools import tool_schemas, tool_list_tables, tool_describe_table, tool_run_sql

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LOG_LLM = os.getenv("LOG_LLM", "0") == "1"

client = OpenAI()

TOOL_IMPL = {
    "tool_list_tables": lambda args: tool_list_tables(),
    "tool_describe_table": lambda args: tool_describe_table(args["table_name"]),
    "tool_run_sql":     lambda args: tool_run_sql(args["sql"], args.get("named_params")),
}

def _log(label: str, obj: Any):
    if LOG_LLM:
        try:
            print(f"[LLM DEBUG] {label}:", json.dumps(obj, indent=2)[:4000])
        except Exception:
            print(f"[LLM DEBUG] {label} (non-json):", str(obj)[:4000])

def _extract_text(resp) -> str:
    # Prefer output_text; fall back to concatenating message segments
    try:
        if getattr(resp, "output_text", None):
            return resp.output_text
    except Exception:
        pass
    # Fallback: scan outputs for message text segments
    parts = []
    try:
        for item in getattr(resp, "output", []) or []:
            if getattr(item, "type", "") == "message" and getattr(item, "content", None):
                for seg in item.content:
                    if getattr(seg, "type", "") == "output_text" and getattr(seg, "text", None):
                        parts.append(seg.text)
    except Exception:
        pass
    return "".join(parts).strip()

def run_agent(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    # 1) First call – expect tool calls
    first = client.responses.create(
        model=MODEL,
        input=[{"role": "system", "content": SYSTEM}, *messages],
        tools=tool_schemas,
        temperature=0.2,
    )
    _log("first_response", first.model_dump())

    print("OUTPUT TYPES:", [getattr(x, "type", None) for x in (first.output or [])])
    for i, x in enumerate(first.output or []):
        print(f"ITEM {i} TYPE:", getattr(x, "type", None))
        if getattr(x, "type", None) == "message":
            print("  message role:", getattr(x, "role", None))
            print("  segments:", [getattr(s, "type", None) for s in (x.content or [])])

    def _iter_calls(resp):
        for item in (getattr(resp, "output", []) or []):
            t = getattr(item, "type", "") or ""
            if t in ("function_call", "tool_call"):
                call = getattr(item, "function_call", None) or getattr(item, "tool_call", None)
                if call:
                    yield item, call

    tool_outputs = []
    func_calls = []
    for item in (first.output or []):
        if getattr(item, "type", "") == "function_call":
            func_calls.append({
                "type": "function_call",
                "id": getattr(item, "id", None),  # keep if available
                "call_id": item.call_id,  # REQUIRED
                "name": item.name,
                "arguments": item.arguments or "{}",
            })

            # 2) Execute tools and build outputs
            func_outputs = []
            for fc in func_calls:
                name = fc["name"]
                args_json = fc["arguments"] or "{}"
                try:
                    args = json.loads(args_json)
                except Exception:
                    args = {}
                impl = TOOL_IMPL.get(name)
                try:
                    result = impl(args) if impl else {"error": f"unknown tool '{name}'"}
                except Exception as e:
                    result = {"error": str(e)}

                func_outputs.append({
                    "type": "function_call_output",
                    "call_id": fc["call_id"],  # MUST MATCH the function_call above
                    "output": json.dumps(result),  # string, not array
                })

    final_resp = first
    if func_outputs:
        final_resp = client.responses.create(
            model=MODEL,
            input=[
                {"role": "system", "content": SYSTEM},
                *messages,
                *func_calls,  # <-- include original function_call(s)
                *func_outputs,  # <-- then their outputs
            ],
            tools=tool_schemas,
            tool_choice="auto",
            temperature=0.2,
        )
        _log("second_response", final_resp.model_dump())

    text = _extract_text(final_resp)

    # Try to parse the expected JSON envelope from the model
    result: Dict[str, Any]
    try:
        result = json.loads(text)
    except Exception:
        result = {"answer": text}

    # Ensure keys exist
    return {
        "answer": result.get("answer", text or "(no answer)"),
        "table_preview": result.get("table_preview"),
        "followups": result.get("followups", []),
    }