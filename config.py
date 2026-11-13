"""
Shared configuration for the NOC Virtual Analyst capstone.

Imported by both the Streamlit app (``app.py``) and the stand-alone MCP server
(``mcp_server.py``). All paths are resolved from this file's location so they work
identically when run locally, from a subprocess, or on Streamlit Community Cloud.
"""

from pathlib import Path

# --------------------------------------------------------------------------- #
# Paths (absolute, derived from __file__ so the MCP subprocess resolves them too)
# --------------------------------------------------------------------------- #
BASE_DIR = Path(__file__).resolve().parent

DB_PATH = BASE_DIR / "Database" / "terminal_ops.db"            # committed, app reads this
SOURCE_DB_PATH = BASE_DIR / "Database" / "terminal_performance.db"  # 136MB source (gitignored)
SCHEMA_SQL_PATH = BASE_DIR / "Database" / "schema.sql"
KB_DIR = BASE_DIR / "Knowledgebase"
MCP_SERVER_PATH = BASE_DIR / "mcp_server.py"

# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
MODEL = "gemini-3.5-flash"        # coordinator + all worker specialists
JUDGE_MODEL = "gemini-3.5-flash"  # LLM-as-a-Judge in eval.py (single model everywhere)

# --------------------------------------------------------------------------- #
# Pricing (USD per 1,000,000 tokens) for the cost-tracking callback.
# NOTE: confirm current rates at https://ai.google.dev/pricing — these are the
# published Gemini 3.5-class figures at build time and are easy to correct here.
# --------------------------------------------------------------------------- #
PRICING = {
    "gemini-3.5-flash": {"input": 1.50, "output": 9.00},
}
DEFAULT_PRICE = {"input": 1.50, "output": 9.00}


def price_for(model_name: str) -> dict:
    """Return the {input, output} USD/1M price for a model, falling back safely."""
    if not model_name:
        return DEFAULT_PRICE
    for key, price in PRICING.items():
        if key in model_name:
            return price
    return DEFAULT_PRICE
