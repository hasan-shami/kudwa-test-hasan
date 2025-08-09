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

def list_tables():
    with ro_conn() as c:
        rows = c.execute("""
            SELECT name FROM sqlite_master
            WHERE type='table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """).fetchall()
        return [r["name"] for r in rows]

def describe_table(table: str):
    # Escape any single quotes in the table name to avoid SQL injection
    safe_table = table.replace("'", "''")
    with ro_conn() as c:
        cols = c.execute(f"PRAGMA table_info('{safe_table}')").fetchall()
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

def run_select(sql: str, params: dict | None = None, max_rows: int = 1000):
    with ro_conn() as c:
        cur = c.execute(sql, params or {})
        cols = [d[0] for d in cur.description]
        rows = cur.fetchmany(max_rows)
        data = [dict(zip(cols, r)) for r in rows]
        return {"columns": cols, "rows": data}
