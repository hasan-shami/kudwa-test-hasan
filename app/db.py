from __future__ import annotations
import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.getenv("DB_PATH", "./data.db")

# Use URI mode=ro to prevent writes
URI = f"file:{os.path.abspath(DB_PATH)}?mode=ro"

@contextmanager
def ro_conn():
    conn = sqlite3.connect(URI, uri=True, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def list_tables(
    include_views: bool = True,
    include_tables: bool = True,
    only: list[str] | None = None
):
    with ro_conn() as c:
        types = []
        if include_tables:
            types.append("table")
        if include_views:
            types.append("view")

        if not types:
            return []  # nothing to include

        placeholders = ",".join(f"'{t}'" for t in types)

        rows = c.execute(f"""
            SELECT name, type
            FROM sqlite_schema
            WHERE type IN ({placeholders})
              AND name NOT LIKE 'sqlite_%'
            ORDER BY CASE type WHEN 'view' THEN 0 ELSE 1 END, name
        """).fetchall()

        names = [r["name"] for r in rows]
        if only:
            only_set = set(only)
            names = [n for n in names if n in only_set]
        return names


def describe_table(table: str):
    """
    Works for both tables and views.
    Tries PRAGMA first; if empty (some views), falls back to a 0-row SELECT to read column names.
    """
    # Escape quotes safely for SQLite identifiers
    safe_name = table.replace('"', '""')

    with ro_conn() as c:
        # Prefer xinfo (includes hidden cols) and works for most tables/views
        cols = c.execute(f'PRAGMA table_xinfo("{safe_name}")').fetchall()

        if cols:
            return [
                {
                    "cid": row["cid"],
                    "name": row["name"],
                    "type": row["type"],
                    "notnull": row["notnull"],
                    "dflt_value": row["dflt_value"],
                    "pk": row["pk"],
                }
                for row in cols
            ]

        # Fallback: infer from a 0-row SELECT (common for views without declared types)
        cur = c.execute(f'SELECT * FROM "{safe_name}" LIMIT 0')
        desc = cur.description or []
        return [
            {
                "cid": i,
                "name": d[0],
                "type": "",        # type is unknown for views via this path
                "notnull": 0,
                "dflt_value": None,
                "pk": 0,
            }
            for i, d in enumerate(desc)
        ]

def run_select(sql: str, params: dict | None = None, max_rows: int = 1000):
    with ro_conn() as c:
        cur = c.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        data = [dict(zip(cols, r)) for r in rows]
        return {"columns": cols, "rows": data}
