from __future__ import annotations
import os
import re
import sqlparse
from typing import Any, Dict, List
from .db import list_tables, describe_table, run_select

MAX_ROWS = int(os.getenv("MAX_ROWS", "1000"))

DANGEROUS = re.compile(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|REPLACE|ATTACH|DETACH|VACUUM|PRAGMA)\b", re.IGNORECASE)

def ensure_safe_sql(sql: str) -> str:
    # Quick checks
    if ";" in sql.strip().rstrip(";"):
        # Disallow multiple statements
        raise ValueError("Multiple SQL statements are not allowed.")
    if DANGEROUS.search(sql):
        raise ValueError("Only read-only SELECT queries are allowed.")
    parsed = sqlparse.parse(sql)
    if not parsed or parsed[0].get_type().upper() != "SELECT":
        raise ValueError("Only SELECT queries are allowed.")
    return sql

# Exposed tool functions (called by the LLM)
def tool_list_tables() -> Dict[str, Any]:
    return {"tables": list_tables(include_views=True, include_tables=False)}

def tool_describe_table(table_name: str) -> Dict[str, Any]:
    return {"table": table_name, "columns": describe_table(table_name)}

def tool_run_sql(sql: str, named_params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe = ensure_safe_sql(sql)
    return run_select(safe, named_params, max_rows=MAX_ROWS)

def tool_sample_rows(table_name: str, limit: int = 5):
    # basic preview to see column names and example values
    safe_table = table_name.replace("'", "''")
    return run_select(f"SELECT * FROM '{safe_table}' LIMIT :lim", {"lim": limit})

def tool_distinct_values(table_name: str, column: str, limit: int = 100):
    safe_table = table_name.replace("'", "''")
    safe_col = column.replace('"', '""')
    sql = f'SELECT DISTINCT "{safe_col}" AS value FROM "{safe_table}" WHERE "{safe_col}" IS NOT NULL ORDER BY 1 LIMIT :lim'
    return run_select(sql, {"lim": limit})

# JSON schemas for tool calling
tool_schemas = [
    {
        "type": "function",
        "name": "tool_list_tables",
        "description": "List all available tables in the SQLite database.",
        "parameters": {"type": "object", "properties": {}}
    },
    {
        "type": "function",
        "name": "tool_describe_table",
        "description": "Get column names and types for a table.",
        "parameters": {
            "type": "object",
            "required": ["table_name"],
            "properties": {"table_name": {"type": "string"}}
        }
    },
    {
        "type": "function",
        "name": "tool_run_sql",
        "description": "Execute a read-only SELECT with named parameters. Use the 'named_params' object (NOT 'parameters').",
        "parameters": {
            "type": "object",
            "required": ["sql"],
            "properties": {
                "sql": {"type": "string"},
                "named_params": {
                    "type": "object",
                    "description": "Key-value pairs for SQL bindings, e.g., {'year': 2024} for :year",
                    "additionalProperties": True
                }
            }
        }
    },
    {
        "type": "function",
        "name": "tool_sample_rows",
        "description": "Preview a few rows from a table to see actual values.",
        "parameters": {
            "type": "object",
            "required": ["table_name"],
            "properties": {
                "table_name": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 5}
            }
        }
    },
    {
        "type": "function",
        "name": "tool_distinct_values",
        "description": "List distinct values in a column to understand valid categories.",
        "parameters": {
            "type": "object",
            "required": ["table_name", "column"],
            "properties": {
                "table_name": {"type": "string"},
                "column": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 100}
            }
        }
    },
]

