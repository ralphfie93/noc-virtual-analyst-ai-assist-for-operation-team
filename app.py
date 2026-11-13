"""
app.py — NOC Copilot (AI Assist for the Operations Team).

A production-grade multi-agent assistant for satellite network operations,
built on Google ADK + MCP + Streamlit. See README.md for the architecture summary.

UI: a Copilot-inspired console — conversation history in the left rail, a clean
centered chat, suggestion starters, a Tools menu, and a Settings modal (personality).

Pillars wired here:
  1. Multi-agent  — coordinator + telemetry / runbook / weather specialists (agent_core)
  2. Enterprise integration — real stdio MCP server (mcp_server.py) via McpToolset
  3. Guardrails   — input tier (guardrails.py) + data tier (mcp_server.py)
  4. Cost telemetry — after_model_cost_tracker on every agent, shown in the Tools menu
  6. Deployment   — st.secrets, requirements.txt, Streamlit Community Cloud
"""

import os
import sqlite3
import uuid
from datetime import datetime

import pandas as pd
import streamlit as st

import mcp_server  # in-process read-only helpers for charts / schema inspector
from config import DB_PATH, MODEL

# --------------------------------------------------------------------------- #
# 1. Page config + styling
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="NOC Copilot",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      :root {
        --accent:#4F46E5; --accent-soft:#EEF0FF; --ink:#0F172A; --muted:#64748B;
        --line:#E8E8EF; --surface:#FFFFFF; --panel:#FAFAFC;
        --ok:#16A34A; --warn:#D97706; --crit:#DC2626;
      }
      html, body, [class*="css"], .stMarkdown, .stButton button, input, textarea {
        font-family:'Inter', system-ui, sans-serif !important;
      }
      #MainMenu, footer { visibility:hidden; height:0; }
      .block-container { max-width: 820px; padding-top: 2.6rem; padding-bottom: 7rem; }

      /* Hero */
      .hero-greet { font-size:2.2rem; font-weight:700; color:var(--ink);
        letter-spacing:-0.02em; margin:0.2rem 0 0.35rem; }
      .hero-sub { color:var(--muted); font-size:1.05rem; line-height:1.5;
        max-width:60ch; margin-bottom:1.6rem; }

      /* Suggestion starters — soft, airy, with a leading glyph (scoped) */
      .st-key-suggestions button {
        border:1px solid var(--line) !important; border-radius:16px !important;
        background:var(--panel) !important; color:var(--ink) !important;
        padding:15px 16px !important; min-height:58px; font-weight:500 !important;
        justify-content:flex-start !important; text-align:left !important;
        box-shadow:none !important; transition:transform .12s ease, border-color .12s ease, background .12s ease;
      }
      .st-key-suggestions button:hover {
        border-color:var(--accent) !important; background:var(--surface) !important;
        transform:translateY(-2px);
      }
      .st-key-suggestions button p { text-align:left !important; width:100%; margin:0; }

      /* Chat input */
      [data-testid="stChatInput"] { border-radius:24px !important; }

      /* Chat messages — gentle bubbles */
      [data-testid="stChatMessage"] { padding:2px 0 6px; }

      /* Cost chip */
      .cost-chip { display:inline-block; padding:5px 12px; border-radius:999px;
        background:var(--accent-soft); color:var(--accent); font-size:0.8rem;
        font-weight:600; white-space:nowrap; }

      /* Sidebar conversation rail */
      [data-testid="stSidebar"] { background:var(--panel); border-right:1px solid var(--line); }
      [data-testid="stSidebar"] .stButton button {
        text-align:left !important; justify-content:flex-start !important;
        border-radius:10px !important; font-weight:500 !important;
      }
      .rail-title { font-size:1.15rem; font-weight:700; color:var(--ink); margin:0.2rem 0 0.1rem; }
      .rail-sub { color:var(--muted); font-size:0.78rem; margin-bottom:0.6rem; }
      .rail-head { color:var(--muted); font-size:0.72rem; font-weight:600;
        letter-spacing:0.04em; margin:0.8rem 0 0.3rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# 2. Secrets / API key resolution (st.secrets -> env; force Gemini Dev API)
# --------------------------------------------------------------------------- #
api_key = ""
try:
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
except Exception:  # noqa: BLE001 - no secrets.toml locally is fine
    api_key = ""
api_key = api_key or os.environ.get("GOOGLE_API_KEY", "")
if api_key:
    os.environ["GOOGLE_API_KEY"] = api_key
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

if not os.environ.get("GOOGLE_API_KEY"):
    st.error(
        "⚠️ **GOOGLE_API_KEY is not configured.**\n\n"
        "Add it under **App → Settings → Secrets** on Streamlit Cloud:\n"
        "```toml\nGOOGLE_API_KEY = \"your_gemini_api_key\"\n```\n"
        "or set it as an environment variable locally."
    )
    st.stop()

from agent_core import AnalystBackend  # noqa: E402
from guardrails import COST            # noqa: E402


@st.cache_resource(show_spinner="Starting the NOC Copilot…")
def get_backend():
    return AnalystBackend()


# --------------------------------------------------------------------------- #
# 3. Read-only DB helpers (charts + schema inspector)
# --------------------------------------------------------------------------- #
CHART_CONFIG = {
    "idirect_performance": ("remote_id",
        ["downstream_snr_db", "clearsky_snr_db", "latency_rtt_ms", "tx_power_dbm",
         "utilization_pct"]),
    "jupiter_performance": ("terminal_san",
        ["sqf", "clearsky_sqf", "latency_rtt_ms", "temperature_c", "utilization_pct"]),
    "starlink_performance": ("terminal_id",
        ["pop_ping_latency_ms", "pop_ping_drop_rate", "fraction_obstructed_pct",
         "utilization_pct"]),
    "hub_components": ("component_id",
        ["cpu_pct", "agg_throughput_mbps", "temperature_c"]),
}


@st.cache_data(show_spinner=False)
def list_entities(table: str, id_col: str):
    conn = mcp_server._connect_ro()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT {id_col} FROM {table} ORDER BY {id_col}").fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


@st.cache_data(show_spinner=False)
def chart_frame(table: str, id_col: str, entity: str, metrics: tuple):
    cols = ", ".join(["timestamp_myt", *metrics])
    conn = mcp_server._connect_ro()
    try:
        df = pd.read_sql_query(
            f"SELECT {cols} FROM {table} WHERE {id_col} = ? ORDER BY timestamp_myt",
            conn, params=[entity])
    finally:
        conn.close()
    df["timestamp_myt"] = pd.to_datetime(df["timestamp_myt"])
    df = df.set_index("timestamp_myt").replace(9999.0, pd.NA)
    return df


def schema_text() -> str:
    import json
    lines = []
    for t in json.loads(mcp_server.mcp_list_tables())["tables"]:
        info = json.loads(mcp_server.mcp_describe_table(t))
        lines.append(f"{t}")
        for c in info["columns"]:
            flags = " PK" if c["pk"] else ""
            lines.append(f"    {c['name']:<26} {c['type']}{flags}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 4. Live Migration Simulator (zero-code schema evolution)
# --------------------------------------------------------------------------- #
DEMO_TABLE = "maintenance_windows"


def add_demo_table():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {DEMO_TABLE} ("
            "id INTEGER PRIMARY KEY, system TEXT, component_id TEXT, "
            "start_myt TEXT, end_myt TEXT, reason TEXT)")
        conn.execute(f"DELETE FROM {DEMO_TABLE}")
        conn.executemany(
            f"INSERT INTO {DEMO_TABLE} (system, component_id, start_myt, end_myt, reason)"
            " VALUES (?,?,?,?,?)",
            [("iDirect", "IDR-LC-01", "2026-11-14 02:00:00", "2026-11-14 04:00:00",
              "Scheduled line-card firmware upgrade"),
             ("Jupiter", "JUP-GW-KUL", "2026-11-15 01:00:00", "2026-11-15 03:00:00",
              "Gateway RFT maintenance"),
             ("Starlink", "STAR-POP-SIN", "2026-11-16 00:00:00", "2026-11-16 00:30:00",
              "PoP router reboot")])
        conn.commit()
    finally:
        conn.close()


def drop_demo_table():
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute(f"DROP TABLE IF EXISTS {DEMO_TABLE}")
        conn.commit()
    finally:
        conn.close()


def demo_table_exists() -> bool:
    import json
    return DEMO_TABLE in json.loads(mcp_server.mcp_list_tables())["tables"]


# --------------------------------------------------------------------------- #
# 5. Personality
# --------------------------------------------------------------------------- #
TONE_DIRECTIVES = {
    "Concise L1": "Be terse and operational; lead with the answer, minimal prose.",
    "Detailed Analyst": "Explain thoroughly, showing the underlying data and reasoning.",
    "Friendly Mentor": "Use a warm, encouraging tone and briefly teach the 'why'.",
    "Formal Report": "Write formally and structured, report-style, with clear headings.",
    "Custom": None,
}
VERBOSITY_DIRECTIVES = {
    "Brief": "Keep the answer very short.",
    "Balanced": "Use a moderate length.",
    "Detailed": "Be comprehensive.",
}
DEFAULT_SETTINGS = {
    "name": "NOC Copilot", "tone": "Concise L1", "custom": "",
    "verbosity": "Balanced", "emoji": True,
}


def persona_preamble(s: dict) -> str:
    tone = (s["custom"].strip() if s["tone"] == "Custom" and s["custom"].strip()
            else TONE_DIRECTIVES.get(s["tone"]) or "Be clear and accurate.")
    emoji = "Occasional helpful emojis are fine." if s["emoji"] else "Do not use emojis."
    return (f"[Assistant persona: you are '{s['name']}', a NOC operations assistant. "
            f"Tone: {tone} Verbosity: {VERBOSITY_DIRECTIVES[s['verbosity']]} {emoji}]")


# --------------------------------------------------------------------------- #
# 6. Conversation state (chat history)
# --------------------------------------------------------------------------- #
def new_conversation():
    cid = uuid.uuid4().hex[:8]
    st.session_state.conversations[cid] = {
        "title": "New chat", "messages": [], "created": datetime.now()}
    st.session_state.current_id = cid
    return cid


if "settings" not in st.session_state:
    st.session_state.settings = dict(DEFAULT_SETTINGS)
if "conversations" not in st.session_state:
    st.session_state.conversations = {}
if ("current_id" not in st.session_state
        or st.session_state.current_id not in st.session_state.conversations):
    new_conversation()

settings = st.session_state.settings
convo = st.session_state.conversations[st.session_state.current_id]
messages = convo["messages"]


# --------------------------------------------------------------------------- #
# 7. Settings + Tools modals
# --------------------------------------------------------------------------- #
@st.dialog("⚙️ Assistant settings")
def settings_dialog():
    s = st.session_state.settings
    name = st.text_input("Assistant name", value=s["name"])
    tones = list(TONE_DIRECTIVES.keys())
    tone = st.segmented_control("Tone", tones, default=s["tone"]) or s["tone"]
    custom = s["custom"]
    if tone == "Custom":
        custom = st.text_area("Custom style instruction", value=s["custom"],
                              placeholder="e.g. Always show the SQL you ran.")
    verbs = list(VERBOSITY_DIRECTIVES.keys())
    verbosity = st.segmented_control("Verbosity", verbs,
                                     default=s["verbosity"]) or s["verbosity"]
    emoji = st.toggle("Use emojis in replies", value=s["emoji"])
    st.caption("Applied to your next message — no restart needed.")
    c1, c2 = st.columns(2)
    if c1.button("Save", type="primary", use_container_width=True):
        st.session_state.settings = {"name": name.strip() or "NOC Copilot", "tone": tone,
                                     "custom": custom, "verbosity": verbosity,
                                     "emoji": emoji}
        st.rerun()
    if c2.button("Reset to defaults", use_container_width=True):
        st.session_state.settings = dict(DEFAULT_SETTINGS)
        st.rerun()


@st.dialog("🛠️ Tools", width="large")
def tools_dialog():
    t1, t2, t3, t4 = st.tabs(["📈 Charts", "🔧 Migration", "🗄️ Schema", "💰 Cost"])
    with t1:
        c1, c2 = st.columns(2)
        table = c1.selectbox("System table", list(CHART_CONFIG.keys()))
        id_col, metrics = CHART_CONFIG[table]
        entity = c2.selectbox(id_col, list_entities(table, id_col))
        chosen = st.multiselect("Metrics", metrics, default=metrics[:2])
        if entity and chosen:
            df = chart_frame(table, id_col, entity, tuple(chosen))
            st.line_chart(df[list(chosen)])
            st.caption(f"{len(df):,} samples · latency timeouts (9999) shown as gaps.")
    with t2:
        st.caption("Add a table at runtime and watch MCP discover it with zero agent-code "
                   "change (dynamic discovery).")
        if demo_table_exists():
            st.success(f"`{DEMO_TABLE}` is live. Try: *“what maintenance windows are "
                       f"scheduled?”*")
            if st.button("Remove demo table", use_container_width=True):
                drop_demo_table()
                st.rerun()
        else:
            if st.button(f"➕ Add `{DEMO_TABLE}` table", use_container_width=True):
                add_demo_table()
                st.rerun()
    with t3:
        st.text(schema_text())
    with t4:
        snap = COST.snapshot()
        a, b = st.columns(2)
        a.metric("Input tokens", f"{snap['input_tokens']:,}")
        b.metric("Output tokens", f"{snap['output_tokens']:,}")
        a.metric("Model calls", snap["calls"])
        b.metric("Est. cost (USD)", f"${snap['usd']:.4f}")
        if st.button("Reset telemetry", use_container_width=True):
            COST.reset()
            st.rerun()


# --------------------------------------------------------------------------- #
# 8. Sidebar — conversation history rail
# --------------------------------------------------------------------------- #
backend = get_backend()

with st.sidebar:
    st.markdown("<div class='rail-title'>🛰️ NOC Copilot</div>"
                "<div class='rail-sub'>Satellite operations assistant</div>",
                unsafe_allow_html=True)
    if st.button("➕  New chat", use_container_width=True, type="primary"):
        new_conversation()
        st.rerun()

    st.markdown("<div class='rail-head'>RECENT</div>", unsafe_allow_html=True)
    for cid, c in sorted(st.session_state.conversations.items(),
                         key=lambda kv: kv[1]["created"], reverse=True):
        active = cid == st.session_state.current_id
        label = ("💬  " + (c["title"][:30] + "…" if len(c["title"]) > 30 else c["title"]))
        if st.button(label, key=f"conv_{cid}", use_container_width=True,
                     type="secondary", disabled=active):
            st.session_state.current_id = cid
            st.rerun()

# --------------------------------------------------------------------------- #
# 9. Top actions (right-aligned)
# --------------------------------------------------------------------------- #
_, actions = st.columns([0.5, 0.5])
with actions:
    snap = COST.snapshot()
    st.markdown(f"<div style='text-align:right;margin-bottom:6px'>"
                f"<span class='cost-chip'>${snap['usd']:.4f} · {snap['calls']} calls</span>"
                f"</div>", unsafe_allow_html=True)
    b_tools, b_set = st.columns(2)
    if b_tools.button("🛠️ Tools", use_container_width=True):
        tools_dialog()
    if b_set.button("⚙️ Settings", use_container_width=True):
        settings_dialog()

# --------------------------------------------------------------------------- #
# 10. Welcome (empty) or transcript
# --------------------------------------------------------------------------- #
SUGGESTED = [
    ("📊", "Give me a network health summary"),
    ("🚨", "What incidents happened this week?"),
    ("🛰️", "Help me troubleshoot a terminal that's offline"),
    ("📉", "Which terminals had weak or dropping signal?"),
    ("📋", "What should I collect before escalating an outage?"),
    ("💬", "What can you help me with?"),
]

if not messages:
    hour = datetime.now().hour
    greet = ("Good morning" if hour < 12 else
             "Good afternoon" if hour < 18 else "Good evening")
    st.markdown(f"<div class='hero-greet'>{greet} 👋</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='hero-sub'>I'm {settings['name']}. I read live terminal and "
                f"hub telemetry, follow the NOC runbooks, and can check site weather — "
                f"all read-only. What are we looking at today?</div>",
                unsafe_allow_html=True)
    with st.container(key="suggestions"):
        cols = st.columns(2)
        for i, (icon, text) in enumerate(SUGGESTED):
            if cols[i % 2].button(f"{icon}  {text}", key=f"sg_{i}",
                                  use_container_width=True):
                st.session_state.pending = text
                st.rerun()
else:
    for m in messages:
        avatar = "🛰️" if m["role"] == "assistant" else "🧑‍💻"
        with st.chat_message(m["role"], avatar=avatar):
            st.markdown(m["content"])

# --------------------------------------------------------------------------- #
# 11. Input handling
# --------------------------------------------------------------------------- #
prompt = None
if st.session_state.get("pending"):
    prompt = st.session_state.pop("pending")
else:
    typed = st.chat_input(f"Message {settings['name']}…")
    if typed:
        prompt = typed

if prompt:
    if convo["title"] == "New chat":
        convo["title"] = prompt
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user", avatar="🧑‍💻"):
        st.markdown(prompt)
    styled = f"{persona_preamble(settings)}\n\nUser question: {prompt}"
    with st.chat_message("assistant", avatar="🛰️"):
        with st.spinner("Coordinating specialists — telemetry, runbooks & weather "
                        "(this can take 20–40s)…"):
            try:
                answer = backend.ask(styled)
            except Exception as exc:  # noqa: BLE001
                answer = f"⚠️ Error: {exc}"
        st.markdown(answer)
    messages.append({"role": "assistant", "content": answer})
    st.rerun()

# --------------------------------------------------------------------------- #
# 12. Incident-report export (under the last answer)
# --------------------------------------------------------------------------- #
if messages and messages[-1]["role"] == "assistant":
    last_user = next((m["content"] for m in reversed(messages)
                      if m["role"] == "user"), "")
    report = (
        f"# NOC Incident / Analysis Report\n\n"
        f"- **Generated:** {datetime.now():%Y-%m-%d %H:%M} (local)\n"
        f"- **Query:** {last_user}\n\n"
        f"## Analyst finding\n\n{messages[-1]['content']}\n\n"
        f"---\n*Generated by the NOC Copilot. Mock training data; verify against live "
        f"NMS before acting.*\n")
    st.download_button(
        "📥 Download incident report (.md)", report,
        file_name=f"noc_report_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown")
