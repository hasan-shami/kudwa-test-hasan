from __future__ import annotations
import os, json, re, logging
from typing import Dict, Any, List, Optional, Set
from openai import OpenAI
from datetime import datetime
from uuid import uuid4
from .prompts import SYSTEM
from .tools import tool_schemas, tool_list_tables, tool_describe_table, tool_run_sql,tool_sample_rows, tool_distinct_values

from dotenv import load_dotenv
load_dotenv(override=False)

MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
LOG_LLM = os.getenv("LOG_LLM", "0") == "1"
LOG_FILE = os.getenv("LLM_LOG_FILE")
API_KEY = os.getenv("OPENAI_API_KEY")
assert API_KEY, "OPENAI_API_KEY not found"
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("llm")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(LOG_FILE, encoding="utf-8") if LOG_FILE else logging.StreamHandler()
    # We emit already-serialized JSON; keep formatter minimal.
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    return logger

_LOGGER = _setup_logger()

def _log_event(kind: str, **fields):
    if not LOG_LLM:
        return
    evt = {"ts": datetime.utcnow().isoformat(timespec="seconds") + "Z", "kind": kind}
    evt.update(fields)
    try:
        _LOGGER.info(json.dumps(evt, default=str))
    except Exception as e:
        # best-effort logging; never raise
        _LOGGER.info(json.dumps({"ts": evt["ts"], "kind": "log_error", "error": str(e)}))

# Keep your printable debug if you like
def _log(label: str, obj: Any):
    if LOG_LLM:
        try:
            print(f"[LLM DEBUG] {label}:", json.dumps(obj, indent=2)[:4000])
        except Exception:
            print(f"[LLM DEBUG] {label} (non-json):", str(obj)[:4000])

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

_SQL_TABLE_RE = re.compile(r"\b(?:from|join)\s+([\"`\[]?)([A-Za-z_][\w\.$]*?)\1\b", re.IGNORECASE)

def _tables_from_sql(sql: str) -> List[str]:
    seen: Set[str] = set()
    for m in _SQL_TABLE_RE.finditer(sql or ""):
        t = m.group(2)
        # strip schema qualifiers + quotes/brackets
        t = t.split(".")[-1].strip().strip("[]`\"")
        if t:
            seen.add(t)
    return sorted(seen)
def _usage_dict(resp) -> Optional[Dict[str, int]]:
    try:
        u = getattr(resp, "usage", None)
        if u is None:
            return None
        # resp.usage may be a model; try model_dump then dict fallbacks
        ud = u.model_dump() if hasattr(u, "model_dump") else dict(u)
        return {
            "input_tokens": ud.get("input_tokens") or ud.get("prompt_tokens"),
            "output_tokens": ud.get("output_tokens") or ud.get("completion_tokens"),
            "total_tokens": ud.get("total_tokens"),
        }
    except Exception:
        return None

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
    trace_id = str(uuid4())
    now = datetime.now()
    context = context or {}
    context.update({
        "current_date": now.strftime("%Y-%m-%d"),
        "current_year": now.year,
        "current_quarter": (now.month - 1) // 3 + 1,
        "current_month": now.month
    })
    dev_msg = {
        "role": "developer",
        "content": (
            "Request context (authoritative parameters to use for SQL named bindings): "
            + json.dumps(context)
        )
    }

    base_input = [{"role": "system", "content": SYSTEM}, dev_msg, *messages]

    used_tables: Set[str] = set()
    total_in = total_out = total_total = 0

    _log_event("agent_start", trace_id=trace_id, model=MODEL, context=context)

    def call_model(cur_input, round_no: int):
        resp = client.responses.create(
            model=MODEL,
            input=cur_input,
            tools=tool_schemas,
            tool_choice="auto",
            temperature=0.2,
        )
        _log("responses.create", resp.model_dump())
        u = _usage_dict(resp)
        if u:
            nonlocal total_in, total_out, total_total
            total_in += (u.get("input_tokens") or 0)
            total_out += (u.get("output_tokens") or 0)
            total_total += (u.get("total_tokens") or 0)
            _log_event("token_usage_round", trace_id=trace_id, round=round_no, usage=u)
        return resp

    def _merge_context_params(sql: str, named_params: Dict[str, Any]) -> Dict[str, Any]:
        # Find all :placeholders (case-insensitive)
        needed_raw = set(re.findall(r":(\w+)", sql))
        needed = {p for p in needed_raw}

        # Normalize context keys to lowercase for tolerant matching
        ctx_lc = {str(k).lower(): v for k, v in (context or {}).items()}

        # Build a tolerant view of named_params
        out = dict(named_params or {})

        # Alias map for common LLM variations
        alias_map = {
            "yr": "year",
            "yy": "year",
            "yyyy": "year",
            "year": "year",
            "thisyear": "current_year",
            "q": "quarter",
            "qtr": "quarter",
            "quarter": "quarter",
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

    for round_no in range(1, MAX_ROUNDS + 1):
        resp = call_model(cur_input, round_no)
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

            if name in ("tool_describe_table", "tool_sample_rows", "tool_distinct_values"):
                table_key = "table_name" if "table_name" in args else "table"
                tname = args.get(table_key)
                if tname:
                    used_tables.add(tname)
                _log_event("tool_call", trace_id=trace_id, tool=name, args=args)

            try:
                if name == "tool_run_sql":
                    sql = args.get("sql", "") or ""
                    params = args.get("named_params") or args.get("parameters") or {}
                    params = _merge_context_params(sql, params)

                    # table discovery from SQL
                    sql_tables = _tables_from_sql(sql)
                    for t in sql_tables:
                        used_tables.add(t)

                    # log the query + params + tables
                    _log_event(
                        "sql_exec",
                        trace_id=trace_id,
                        sql=sql,
                        params=params,
                        tables=sql_tables
                    )

                    result = TOOL_IMPL["tool_run_sql"]({"sql": sql, "named_params": params})
                    # tiny result summary to avoid huge logs
                    rows = (len(result) if isinstance(result, list) else 1) if result is not None else 0
                    _log_event("sql_result", trace_id=trace_id, approx_rows=rows)


                else:
                    impl = TOOL_IMPL.get(name)
                    result = impl(args) if impl else {"error": f"unknown tool '{name}'"}

            except Exception as e:
                result = {"error": str(e)}
                _log_event("tool_error", trace_id=trace_id, tool=name, error=str(e))


            func_outputs.append({
                "type": "function_call_output",
                "call_id": fc["call_id"],            # MUST match
                "output": json.dumps(result),        # STRING
            })

        # Prepare next-round input by appending both the calls and their outputs
        cur_input = list(base_input) + func_calls + func_outputs

    # Final usage roll-up
    if total_in or total_out or total_total:
        _log_event("token_usage_total", trace_id=trace_id,
                   tokens={"input": total_in, "output": total_out, "total": total_total})

    # Extract final text
    text = _extract_text(final_resp)

    # Parse your JSON envelope if present
    try:
        result = json.loads(text)
    except Exception:
        result = {"answer": text}
        # Final audit summary
    if used_tables:
        _log_event("tables_used", trace_id=trace_id, tables=sorted(used_tables))
    _log_event("agent_done", trace_id=trace_id,
               used_tables=sorted(used_tables),
               tokens={"input": total_in, "output": total_out, "total": total_total})

    return {
        "answer": result.get("answer", text or "(no answer)"),
        "table_preview": result.get("table_preview"),
        "followups": result.get("followups", []),
    }