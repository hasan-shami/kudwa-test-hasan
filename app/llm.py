from __future__ import annotations
import os, json, re
from typing import Dict, Any, List
from openai import OpenAI
from .prompts import SYSTEM
from .tools import tool_schemas, tool_list_tables, tool_describe_table, tool_run_sql,tool_sample_rows, tool_distinct_values

from dotenv import load_dotenv
load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LOG_LLM = os.getenv("LOG_LLM", "0") == "1"
API_KEY = os.getenv("OPENAI_API_KEY")
assert API_KEY, "OPENAI_API_KEY not found"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TOOL_IMPL = {
    "tool_list_tables": lambda args: tool_list_tables(),
    "tool_describe_table": lambda args: tool_describe_table(args["table_name"]),
    "tool_run_sql": lambda args: tool_run_sql(
        args["sql"],
        # accept either "named_params" or "parameters"
        args.get("named_params") or args.get("parameters") or {}
    ),
    "tool_sample_rows": lambda args: tool_sample_rows(args["table_name"], args.get("limit", 5)),
    "tool_distinct_values": lambda args: tool_distinct_values(args["table_name"], args["column"],
                                                              args.get("limit", 100)),
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

def run_agent(messages: List[Dict[str, str]], context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    context = context or {}

    dev_msg = {
        "role": "developer",
        "content": (
            "Request context (authoritative parameters to use for SQL named bindings): "
            + json.dumps(context)
        )
    }

    base_input = [{"role": "system", "content": SYSTEM}, dev_msg, *messages]

    def call_model(cur_input):
        resp = client.responses.create(
            model=MODEL,
            input=cur_input,
            tools=tool_schemas,
            tool_choice="auto",
            temperature=0.2,
        )
        _log("responses.create", resp.model_dump())
        return resp

    def _merge_context_params(sql: str, named_params: Dict[str, Any]) -> Dict[str, Any]:
        # Find all :placeholders (case-insensitive)
        needed_raw = set(re.findall(r":(\w+)", sql))
        needed = {p for p in needed_raw}

        # Normalize context keys to lowercase for tolerant matching
        ctx_lc = {str(k).lower(): v for k, v in (context or {}).items()}

        # Also build a tolerant view of named_params (don’t rename, just help matching)
        out = dict(named_params or {})

        # Alias map for common LLM variations
        alias_map = {
            "yr": "year",
            "yy": "year",
            "yyyy": "year",
            "year": "year",
            "q": "quarter",
            "qtr": "quarter",
            "quarter": "quarter",
            # add more if you see them in logs
        }

        for k in needed:
            if k in out:
                continue  # model already supplied it
            k_lc = k.lower()
            # exact lower match from context
            if k_lc in ctx_lc:
                out[k] = ctx_lc[k_lc]
                continue
            # alias match (e.g., :QTR -> context['quarter'])
            alias = alias_map.get(k_lc)
            if alias and alias in ctx_lc:
                out[k] = ctx_lc[alias]
                continue

        return out

    # Up to N tool rounds
    MAX_ROUNDS = 5
    cur_input = list(base_input)
    final_resp = None

    for _round in range(MAX_ROUNDS):
        resp = call_model(cur_input)
        final_resp = resp

        # Collect all function calls in this round
        func_calls = []
        for item in (resp.output or []):
            if getattr(item, "type", "") == "function_call":
                func_calls.append({
                    "type": "function_call",
                    "id": getattr(item, "id", None),
                    "call_id": item.call_id,           # REQUIRED for echo
                    "name": item.name,
                    "arguments": item.arguments or "{}",
                })

        # If no tool calls, we’re done
        if not func_calls:
            break

        # Execute calls and build outputs
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
                if name == "tool_run_sql":
                    sql = args.get("sql", "")
                    params = args.get("named_params") or args.get("parameters") or {}
                    params = _merge_context_params(sql, params)  # <-- enforce your context
                    result = TOOL_IMPL["tool_run_sql"]({"sql": sql, "named_params": params})
                else:
                    result = impl(args) if impl else {"error": f"unknown tool '{name}'"}
            except Exception as e:
                result = {"error": str(e)}

            func_outputs.append({
                "type": "function_call_output",
                "call_id": fc["call_id"],            # MUST match
                "output": json.dumps(result),        # STRING
            })

        # Prepare next-round input by appending both the calls and their outputs
        cur_input = list(base_input) + func_calls + func_outputs

    # Extract final text
    text = _extract_text(final_resp)

    # Parse your JSON envelope if present
    try:
        result = json.loads(text)
    except Exception:
        result = {"answer": text}

    return {
        "answer": result.get("answer", text or "(no answer)"),
        "table_preview": result.get("table_preview"),
        "followups": result.get("followups", []),
    }