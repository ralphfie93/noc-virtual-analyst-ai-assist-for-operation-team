"""
mcp_server.py — Model Context Protocol server for the NOC Virtual Analyst.

This is a stand-alone stdio MCP server (launched as a subprocess by the ADK
``McpToolset`` in app.py). It is the ENTERPRISE INTEGRATION layer (Pillar 2) and
enforces the DATA-TIER guardrail (Pillar 3):

  * Dynamic discovery — no schema is hard-coded into the agent prompt:
        mcp_list_tables()        -> table names (from sqlite_master)
        mcp_describe_table(name) -> columns + types (from PRAGMA table_info)
        mcp_read_query(sql)      -> run a validated read-only SELECT
        mcp_list_policies()      -> runbook/policy filenames
        mcp_read_policy(name)    -> full markdown of one runbook

  * Protocol-level least privilege — NO mutation tool is ever registered.
  * AST verification — is_sql_mutation() uses sqlparse to block DROP/DELETE/
    UPDATE/INSERT/ALTER/TRUNCATE/... however the query is phrased.
  * Physical read-only lock — every connection uses file:...?mode=ro.

Run stand-alone for testing:  python mcp_server.py   (speaks MCP over stdio)
"""

import json
import sqlite3

import sqlparse
from mcp.server.mcpserver import MCPServer

from config import DB_PATH, KB_DIR

server = MCPServer("terminal-ops")

MAX_ROWS = 500  # cap result sets so per-minute data never floods the model context

# Statement keywords that must never appear in a read query (defense in depth).
FORBIDDEN = {
    "DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "TRUNCATE", "REPLACE",
    "CREATE", "RENAME", "ATTACH", "DETACH", "PRAGMA", "UPSERT", "VACUUM",
    "GRANT", "REVOKE", "EXEC", "REINDEX", "ANALYZE",
}


# --------------------------------------------------------------------------- #
# Data-tier guardrail
# --------------------------------------------------------------------------- #
def is_sql_mutation(sql: str) -> bool:
    """AST check: True if the query is anything other than a single read SELECT.

    Blocks mutations/DDL regardless of phrasing, rejects multi-statement payloads,
    and permits read-only SELECT (including WITH ... SELECT CTEs).
    """
    cleaned = (sql or "").strip().rstrip(";").strip()
    if not cleaned:
        return True

    parsed = sqlparse.parse(cleaned)
    if len(parsed) != 1:          # exactly one statement — no stacked queries
        return True

    stmt = parsed[0]
    stmt_type = stmt.get_type()
    if stmt_type != "SELECT":
        # allow CTEs, which sqlparse may type as UNKNOWN; require they start WITH
        first = next(
            (t for t in stmt.tokens if not t.is_whitespace and t.ttype is not None
             or (not t.is_whitespace)),
            None,
        )
        first_word = first.value.upper() if first else ""
        if not first_word.startswith("WITH"):
            return True

    for token in stmt.flatten():
        if token.value.upper() in FORBIDDEN:
            return True
    return False


def _connect_ro() -> sqlite3.Connection:
    """Open a strictly read-only connection via URI (OS-level write lock)."""
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_names() -> list[str]:
    conn = _connect_ro()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# MCP tools (discovery primitives — read only)
# --------------------------------------------------------------------------- #
@server.tool(description="List all tables available in the telemetry database.")
def mcp_list_tables() -> str:
    """Return the current table names (discovered live from sqlite_master)."""
    return json.dumps({"tables": _table_names()})


@server.tool(description="Describe one table's columns and types (PRAGMA table_info).")
def mcp_describe_table(table_name: str) -> str:
    """Return the column schema for ``table_name`` discovered at call time."""
    tables = _table_names()
    if table_name not in tables:
        return json.dumps({
            "error": f"Unknown table '{table_name}'. Available: {tables}"
        })
    conn = _connect_ro()
    try:
        info = conn.execute(f"PRAGMA table_info('{table_name}')").fetchall()
    finally:
        conn.close()
    columns = [{"name": r[1], "type": r[2], "notnull": bool(r[3]),
                "pk": bool(r[5])} for r in info]
    return json.dumps({"table": table_name, "columns": columns})


@server.tool(description="Run a READ-ONLY SQL SELECT against the telemetry database. "
                         "Mutations (INSERT/UPDATE/DELETE/DROP/...) are rejected.")
def mcp_read_query(sql: str) -> str:
    """Execute a validated read-only query and return columns + rows (capped)."""
    if is_sql_mutation(sql):
        return json.dumps({
            "error": "GUARDRAIL: only a single read-only SELECT is permitted. "
                     "Mutations and DDL are blocked."
        })
    conn = _connect_ro()
    try:
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(MAX_ROWS + 1)
        truncated = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        return json.dumps({
            "columns": columns,
            "rows": [list(r) for r in rows],
            "row_count": len(rows),
            "truncated": truncated,
        })
    except Exception as exc:  # noqa: BLE001 - surface DB errors back to the agent
        return json.dumps({"error": f"Database error: {exc}"})
    finally:
        conn.close()


@server.tool(description="List the available NOC runbook / policy documents.")
def mcp_list_policies() -> str:
    """Return the markdown runbook filenames in the knowledge base."""
    files = sorted(p.name for p in KB_DIR.glob("*.md"))
    return json.dumps({"policies": files})


@server.tool(description="Read the full text of one NOC runbook / policy document.")
def mcp_read_policy(name: str) -> str:
    """Return the full markdown of ``name`` (basename only; no path traversal)."""
    safe = (name or "").strip().replace("\\", "/").split("/")[-1]
    if not safe.endswith(".md"):
        safe += ".md"
    target = KB_DIR / safe
    available = sorted(p.name for p in KB_DIR.glob("*.md"))
    if safe not in available:
        return json.dumps({"error": f"Unknown policy '{name}'. Available: {available}"})
    return json.dumps({"policy": safe, "content": target.read_text(encoding="utf-8")})


if __name__ == "__main__":
    server.run(transport="stdio")
