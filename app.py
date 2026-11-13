"""
app.py — NOC Virtual Analyst (AI Assist for the Operations Team).

A production-grade multi-agent assistant for satellite network operations,
built on Google ADK + MCP + Streamlit. See README.md for the architecture summary.

UI: a Microsoft Copilot-inspired layout — centered chat, suggestion cards, a minimal
top bar with a Tools menu and a Settings modal (assistant personality).

Pillars wired here:
  1. Multi-agent  — coordinator + telemetry / runbook / weather specialists (agent_core)
  2. Enterprise integration — real stdio MCP server (mcp_server.py) via McpToolset
  3. Guardrails   — input tier (guardrails.py) + data tier (mcp_server.py)
  4. Cost telemetry — after_model_cost_tracker on every agent, shown in the Tools menu
  6. Deployment   — st.secrets, requirements.txt, Streamlit Community Cloud
"""

import os
import sqlite3
from datetime import datetime

import pandas as pd
import streamlit as st

import mcp_server  # in-process read-only helpers for charts / schema inspector
from config import DB_PATH, MODEL

# --------------------------------------------------------------------------- #
# 1. Page config + Copilot-style CSS
# --------------------------------------------------------------------------- #
st.set_page_config(
    page_title="NOC Copilot",
    page_icon="🛰️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
    <style>
      :root { --accent:#4F46E5; --surface:#FFFFFF; --line:#E5E7EB; --muted:#6B7280; }
      /* Hide default Streamlit chrome for a cleaner app shell */
      #MainMenu, footer, [data-testid="stToolbar"] { visibility:hidden; height:0; }
      /* Centered content column (Copilot-like) */
      .block-container { max-width: 880px; padding-top: 1.2rem; padding-bottom: 6rem; }
      /* Suggestion cards = secondary buttons */
      button[kind="secondary"] {
          border-radius:14px !important; border:1px solid var(--line) !important;
          padding:14px 16px !important; text-align:left !important;
          background:var(--surface) !important; box-shadow:0 1px 2px rgba(0,0,0,.04);
          transition:all .15s ease; font-weight:500; min-height:64px;
      }
      button[kind="secondary"]:hover {
          border-color:var(--accent) !important; transform:translateY(-2px);
          box-shadow:0 8px 20px rgba(79,70,229,.14) !important;
      }
      /* Minimal top-bar actions = tertiary buttons */
      button[kind="tertiary"] { color:var(--muted) !important; font-weight:600; }
      button[kind="tertiary"]:hover { color:var(--accent) !important; }
      /* Rounded chat input */
      [data-testid="stChatInput"] { border-radius:26px !important; border:1px solid var(--line); }
      /* Cost chip */
      .cost-chip {
          display:inline-block; padding:6px 12px; border-radius:999px;
          background:#EEF2FF; color:var(--accent); font-size:0.8rem; font-weight:600;
          border:1px solid #E0E7FF; white-space:nowrap;
      }
      .hero-greet { font-size:2.1rem; font-weight:700; margin:0.2rem 0 0.1rem; }
      .hero-sub { color:var(--muted); font-size:1.02rem; margin-bottom:1.3rem; }
      .brand { font-size:1.25rem; font-weight:700; }
      .brand small { color:var(--muted); font-weight:500; font-size:0.8rem; }
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

if "settings" not in st.session_state:
    st.session_state.settings = dict(DEFAULT_SETTINGS)
if "messages" not in st.session_state:
    st.session_state.messages = []


def persona_preamble(s: dict) -> str:
    tone = (s["custom"].strip() if s["tone"] == "Custom" and s["custom"].strip()
            else TONE_DIRECTIVES.get(s["tone"]) or "Be clear and accurate.")
    emoji = "Occasional helpful emojis are fine." if s["emoji"] else "Do not use emojis."
    return (f"[Assistant persona: you are '{s['name']}', a NOC operations assistant. "
            f"Tone: {tone} Verbosity: {VERBOSITY_DIRECTIVES[s['verbosity']]} {emoji}]")


# --------------------------------------------------------------------------- #
# 6. Settings + Tools modals
# --------------------------------------------------------------------------- #
@st.dialog("⚙️ Assistant settings")
def settings_dialog():
    s = st.session_state.settings
    name = st.text_input("Assistant name", value=s["name"])
    tones = list(TONE_DIRECTIVES.keys())
    tone = st.segmented_control("Tone", tones, default=s["tone"]) or s["tone"]
    custom = s["custom"]
    if tone == "Custom":
        custom = st.text_area("Custom style instruction",
                              value=s["custom"], placeholder="e.g. Always show the SQL used.")
    verbs = list(VERBOSITY_DIRECTIVES.keys())
    verbosity = st.segmented_control("Verbosity", verbs, default=s["verbosity"]) or s["verbosity"]
    emoji = st.toggle("Use emojis in replies", value=s["emoji"])
    st.caption("Personality is applied to the next message — no restart needed.")

    c1, c2 = st.columns(2)
    if c1.button("Save", type="primary", use_container_width=True):
        st.session_state.settings = {"name": name.strip() or "NOC Copilot", "tone": tone,
                                     "custom": custom, "verbosity": verbosity, "emoji": emoji}
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
            st.success(f"`{DEMO_TABLE}` is live. Try asking: *“what maintenance windows "
                       f"are scheduled?”*")
            if st.button("Remove demo table", use_container_width=True):
                drop_demo_table()
                st.rerun()
        else:
            if st.button(f"➕ Add `{DEMO_TABLE}` table", use_container_width=True):
                add_demo_table()
                st.rerun()

    with t3:
        st.text(schema_text())  # st.text avoids the LocalTunnel highlighter crash

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
# 7. Top bar
# --------------------------------------------------------------------------- #
backend = get_backend()
settings = st.session_state.settings

left, right = st.columns([0.45, 0.55])
with left:
    st.markdown(f"<div class='brand'>🛰️ {settings['name']}<br>"
                f"<small>{MODEL} · read-only · guardrailed</small></div>",
                unsafe_allow_html=True)
with right:
    snap = COST.snapshot()
    chip, b_tools, b_set, b_new = st.columns([0.34, 0.22, 0.24, 0.20])
    chip.markdown(f"<div style='text-align:right;padding-top:6px'>"
                  f"<span class='cost-chip'>${snap['usd']:.4f} · {snap['calls']} calls</span>"
                  f"</div>", unsafe_allow_html=True)
    if b_tools.button("🛠️ Tools", type="tertiary", use_container_width=True):
        tools_dialog()
    if b_set.button("⚙️ Settings", type="tertiary", use_container_width=True):
        settings_dialog()
    if b_new.button("🆕 New", type="tertiary", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

st.divider()

# --------------------------------------------------------------------------- #
# 8. Welcome (empty state) or chat transcript
# --------------------------------------------------------------------------- #
SUGGESTED = [
    "What hub faults occurred this week, and when?",
    "Why were the terminals on IDR-LC-02 out of network on Nov 8?",
    "Scope the STAR-POP-SIN outage on Nov 9 — one dish or many?",
    "Is JUP-T02's signal dip on Nov 6 ~15:00 rain fade? Check the weather.",
    "Summarize IDR-R03 around Nov 7 14:00 and recommend next steps.",
    "Delete the hub_components table.",
]

if not st.session_state.messages:
    hour = datetime.now().hour
    greet = ("Good morning" if hour < 12 else
             "Good afternoon" if hour < 18 else "Good evening")
    st.markdown(f"<div class='hero-greet'>{greet} 👋</div>", unsafe_allow_html=True)
    st.markdown(f"<div class='hero-sub'>I'm {settings['name']} — ask me about iDirect, "
                f"HNS JUPITER, Starlink terminals, the hub, incidents, or site weather."
                f"</div>", unsafe_allow_html=True)
    cols = st.columns(2)
    for i, q in enumerate(SUGGESTED):
        if cols[i % 2].button(q, key=f"sg_{i}", type="secondary",
                              use_container_width=True):
            st.session_state.pending = q
            st.rerun()
else:
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

# --------------------------------------------------------------------------- #
# 9. Input handling
# --------------------------------------------------------------------------- #
prompt = None
if st.session_state.get("pending"):
    prompt = st.session_state.pop("pending")
else:
    typed = st.chat_input(f"Message {settings['name']}…")
    if typed:
        prompt = typed

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    styled = f"{persona_preamble(settings)}\n\nUser question: {prompt}"
    with st.chat_message("assistant"):
        with st.spinner("Analyzing telemetry, runbooks and weather…"):
            try:
                answer = backend.ask(styled)
            except Exception as exc:  # noqa: BLE001
                answer = f"⚠️ Error: {exc}"
        st.markdown(answer)
    st.session_state.messages.append({"role": "assistant", "content": answer})
    st.rerun()

# --------------------------------------------------------------------------- #
# 10. Incident-report export (under the last answer)
# --------------------------------------------------------------------------- #
if st.session_state.messages and st.session_state.messages[-1]["role"] == "assistant":
    last_user = next((m["content"] for m in reversed(st.session_state.messages)
                      if m["role"] == "user"), "")
    report = (
        f"# NOC Incident / Analysis Report\n\n"
        f"- **Generated:** {datetime.now():%Y-%m-%d %H:%M} (local)\n"
        f"- **Query:** {last_user}\n\n"
        f"## Analyst finding\n\n{st.session_state.messages[-1]['content']}\n\n"
        f"---\n*Generated by the NOC Copilot. Mock training data; verify against live "
        f"NMS before acting.*\n")
    st.download_button(
        "📥 Download incident report (.md)", report,
        file_name=f"noc_report_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown")
