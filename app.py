"""
app.py — NOC Assistant (AI Assist for the Operations Team).

A production-grade multi-agent assistant for satellite network operations,
built on Google ADK + MCP + Streamlit. See README.md for the architecture summary.

UI: aurora gradient, a floating chat panel, a centered welcome with a big input and
realistic suggestion cards, and a left rail with per-chat history, Tools and Settings.
Each conversation has its OWN agent session, so chats never share context.
"""

import os
import sqlite3
import uuid
from datetime import datetime

import streamlit as st

import mcp_server  # in-process read-only helpers for charts / schema inspector
from config import DB_PATH, MODEL

APP_TITLE = "NOC Assistant"

# --------------------------------------------------------------------------- #
# 1. Page config + styling
# --------------------------------------------------------------------------- #
st.set_page_config(page_title=APP_TITLE, page_icon="🛰️", layout="wide",
                   initial_sidebar_state="expanded")

st.markdown(
    """
    <style>
      @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
      :root { --accent:#5B5BD6; --accent-soft:#EEF0FF; --ink:#1A1A2E; --muted:#6B7280;
              --line:#ECECF3; --panel:#FAFAFC; }
      html, body, [class*="css"], .stMarkdown, .stButton button, input, textarea {
        font-family:'Inter', system-ui, sans-serif !important; }
      #MainMenu, footer { visibility:hidden; height:0; }

      .stApp { background: linear-gradient(135deg,#E9EDFF 0%,#F3EBFF 28%,#FDEBF4 55%,
               #EAF2FF 80%,#EFEBFF 100%); }
      [data-testid="stAppViewContainer"], [data-testid="stMain"],
      [data-testid="stHeader"] { background: transparent !important; }

      [data-testid="stMain"] .block-container {
        background:#FFFFFF; border-radius:26px; border:1px solid rgba(2,6,23,.05);
        box-shadow:0 20px 60px rgba(91,91,214,.12); max-width:900px;
        margin-top:1.4rem; margin-bottom:2rem; padding:2.2rem 2.6rem 2.6rem; }

      .hero-greet { text-align:center; font-size:2.35rem; font-weight:700; color:var(--ink);
        letter-spacing:-0.02em; margin:.6rem 0 .4rem; }
      .hero-sub { text-align:center; color:var(--muted); font-size:1.02rem; line-height:1.5;
        max-width:54ch; margin:0 auto 1.5rem; }

      .st-key-welcomebox [data-baseweb="input"],
      .st-key-welcomebox [data-baseweb="base-input"] { border-radius:14px !important;
        box-shadow:0 6px 18px rgba(2,6,23,.06) !important; }
      .st-key-welcomebox input {
        padding:10px 14px !important; font-size:0.98rem !important; border-radius:14px !important; }
      .st-key-welcomebox button {
        border-radius:14px !important; background:var(--accent) !important; color:#fff !important;
        font-weight:700 !important; font-size:1.05rem !important;
        height:42px !important; min-height:42px !important; padding:0 !important; }

      .st-key-cards button {
        border:1px solid var(--line) !important; border-radius:16px !important;
        background:var(--panel) !important; color:var(--ink) !important; padding:15px !important;
        min-height:92px; font-weight:500 !important; justify-content:flex-start !important;
        text-align:left !important; box-shadow:none !important;
        transition:transform .12s ease,border-color .12s ease,background .12s ease; }
      .st-key-cards button:hover { border-color:var(--accent) !important;
        background:#fff !important; transform:translateY(-2px); }
      .st-key-cards button p { text-align:left !important; width:100%; margin:0;
        white-space:normal; line-height:1.35; }

      [data-testid="stChatInput"] { border-radius:24px !important;
        box-shadow:0 8px 24px rgba(2,6,23,.07); }

      .cost-chip { display:inline-block; padding:5px 12px; border-radius:999px;
        background:var(--accent-soft); color:var(--accent); font-size:0.8rem;
        font-weight:600; white-space:nowrap; }

      [data-testid="stSidebar"] { background:rgba(255,255,255,.72);
        backdrop-filter:blur(8px); border-right:1px solid var(--line); }
      [data-testid="stSidebarUserContent"] { position:relative;
        min-height: calc(100vh - 90px); padding-bottom: 7rem; }
      [data-testid="stLayoutWrapper"]:has(> .st-key-railbottom) {
        position:absolute; bottom:0; left:0; right:0; }
      [data-testid="stSidebar"] .stButton button { text-align:left !important;
        justify-content:flex-start !important; border-radius:10px !important;
        font-weight:500 !important; }
      .rail-title { font-size:1.15rem; font-weight:700; color:var(--ink); margin:.2rem 0 .1rem; }
      .rail-sub { color:var(--muted); font-size:.78rem; margin-bottom:.6rem; }
      .rail-head { color:var(--muted); font-size:.72rem; font-weight:600;
        letter-spacing:.04em; margin:.9rem 0 .3rem; }
      .rail-empty { color:var(--muted); font-size:.8rem; font-style:italic; padding:.2rem 0; }
    </style>
    """,
    unsafe_allow_html=True,
)

# --------------------------------------------------------------------------- #
# 2. Secrets / API key resolution
# --------------------------------------------------------------------------- #
api_key = ""
try:
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
except Exception:  # noqa: BLE001
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


@st.cache_resource(show_spinner="Starting the NOC Assistant…")
def get_backend():
    return AnalystBackend()


# --------------------------------------------------------------------------- #
# 3. Read-only DB helper (schema inspector)
# --------------------------------------------------------------------------- #
def schema_text() -> str:
    import json
    lines = []
    for t in json.loads(mcp_server.mcp_list_tables())["tables"]:
        info = json.loads(mcp_server.mcp_describe_table(t))
        lines.append(f"{t}")
        for c in info["columns"]:
            lines.append(f"    {c['name']:<26} {c['type']}{' PK' if c['pk'] else ''}")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 4. Live Migration Simulator
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
VERBOSITY_DIRECTIVES = {"Brief": "Keep the answer very short.",
                        "Balanced": "Use a moderate length.",
                        "Detailed": "Be comprehensive."}
DEFAULT_SETTINGS = {"name": APP_TITLE, "tone": "Concise L1", "custom": "",
                    "verbosity": "Balanced", "emoji": True}


def persona_preamble(s: dict) -> str:
    tone = (s["custom"].strip() if s["tone"] == "Custom" and s["custom"].strip()
            else TONE_DIRECTIVES.get(s["tone"]) or "Be clear and accurate.")
    emoji = "Occasional helpful emojis are fine." if s["emoji"] else "Do not use emojis."
    return (f"[Assistant persona: you are '{s['name']}', a NOC operations assistant. "
            f"Tone: {tone} Verbosity: {VERBOSITY_DIRECTIVES[s['verbosity']]} {emoji}]")


# --------------------------------------------------------------------------- #
# 6. Conversation state — each chat has its OWN agent session (no cross-talk)
# --------------------------------------------------------------------------- #
def new_conversation():
    # prune empty conversations so blank "New chat" rows don't pile up
    for cid in [k for k, v in st.session_state.conversations.items() if not v["messages"]]:
        del st.session_state.conversations[cid]
    cid = uuid.uuid4().hex[:8]
    st.session_state.conversations[cid] = {
        "title": "New chat", "messages": [], "created": datetime.now()}
    st.session_state.current_id = cid
    return cid


def delete_conversation(cid):
    st.session_state.conversations.pop(cid, None)
    if st.session_state.current_id == cid:
        remaining = sorted(st.session_state.conversations.items(),
                           key=lambda kv: kv[1]["created"], reverse=True)
        if remaining:
            st.session_state.current_id = remaining[0][0]
        else:
            new_conversation()


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
        st.session_state.settings = {"name": name.strip() or APP_TITLE, "tone": tone,
                                     "custom": custom, "verbosity": verbosity,
                                     "emoji": emoji}
        st.rerun()
    if c2.button("Reset to defaults", use_container_width=True):
        st.session_state.settings = dict(DEFAULT_SETTINGS)
        st.rerun()


@st.dialog("🛠️ Tools", width="large")
def tools_dialog():
    t1, t2, t3 = st.tabs(["🔧 Migration", "🗄️ Schema", "💰 Cost"])
    with t1:
        st.caption("Add a table at runtime and watch MCP discover it with zero agent-code "
                   "change (dynamic discovery). Then ask: *“what data tables can you query "
                   "right now?”*")
        if demo_table_exists():
            st.success(f"`{DEMO_TABLE}` is live. Ask: *“what maintenance windows are "
                       f"scheduled?”*")
            if st.button("Remove demo table", use_container_width=True):
                drop_demo_table()
                st.rerun()
        elif st.button(f"➕ Add `{DEMO_TABLE}` table", use_container_width=True):
            add_demo_table()
            st.rerun()
    with t2:
        st.text(schema_text())
    with t3:
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
# 8. Sidebar — history rail + Tools/Settings
# --------------------------------------------------------------------------- #
backend = get_backend()

with st.sidebar:
    st.markdown(f"<div class='rail-title'>🛰️ {APP_TITLE}</div>"
                "<div class='rail-sub'>Satellite operations assistant</div>",
                unsafe_allow_html=True)
    if st.button("➕  New chat", use_container_width=True, type="primary"):
        new_conversation()
        st.rerun()

    st.markdown("<div class='rail-head'>RECENT</div>", unsafe_allow_html=True)
    recent = [(cid, c) for cid, c in sorted(
        st.session_state.conversations.items(),
        key=lambda kv: kv[1]["created"], reverse=True) if c["messages"]]
    if not recent:
        st.markdown("<div class='rail-empty'>No conversations yet</div>",
                    unsafe_allow_html=True)
    for cid, c in recent:
        active = cid == st.session_state.current_id
        title = c["title"][:26] + "…" if len(c["title"]) > 26 else c["title"]
        row, bin_ = st.columns([0.82, 0.18])
        if row.button(("💬  " + title), key=f"conv_{cid}",
                      use_container_width=True, disabled=active):
            st.session_state.current_id = cid
            st.rerun()
        if bin_.button("🗑️", key=f"del_{cid}", help="Delete chat"):
            delete_conversation(cid)
            st.rerun()

    with st.container(key="railbottom"):
        st.divider()
        snap = COST.snapshot()
        st.markdown(f"<span class='cost-chip'>💰 ${snap['usd']:.4f} · {snap['calls']} calls"
                    f"</span>", unsafe_allow_html=True)
        st.write("")
        if st.button("🛠️  Tools", use_container_width=True):
            tools_dialog()
        if st.button("⚙️  Settings", use_container_width=True):
            settings_dialog()

# --------------------------------------------------------------------------- #
# 9. Welcome (empty) or transcript
# --------------------------------------------------------------------------- #
WELCOME_CARDS = [
    ("📊", "Check iDirect terminal health this week and give an RCA report for any site "
           "with issues"),
    ("🔧", "My iDirect terminal shows RX and TX amber. What should I check?"),
    ("🌐", "Why can't I get internet from my JUPITER terminal?"),
    ("🌧️", "STAR-D06 is down per our monitoring. What is the weather at its site?"),
    ("🗄️", "What data tables can you query right now?"),
    ("🛡️", "Delete the hub_components table."),
]

if not messages:
    hour = datetime.now().hour
    greet = ("Good morning" if hour < 12 else
             "Good afternoon" if hour < 18 else "Good evening")
    st.markdown(f"<div class='hero-greet'>{greet}, how can I help?</div>",
                unsafe_allow_html=True)
    st.markdown("<div class='hero-sub'>I'm the NOC Virtual AI Assistant. I read live "
                "iDirect, JUPITER HNS and Starlink telemetry, follow the NOC knowledge "
                "base, and can check site weather.</div>", unsafe_allow_html=True)

    with st.container(key="welcomebox"):
        _, mid, _ = st.columns([1, 6, 1])
        with mid:
            with st.form("welcome_form", clear_on_submit=True, border=False):
                ic1, ic2 = st.columns([0.87, 0.13], vertical_alignment="bottom")
                q = ic1.text_input("msg", label_visibility="collapsed",
                                   placeholder=f"Message {settings['name']}…")
                sent = ic2.form_submit_button("➤", use_container_width=True)
            if sent and q and q.strip():
                st.session_state.pending = q.strip()
                st.rerun()

    with st.container(key="cards"):
        row1 = st.columns(3)
        row2 = st.columns(3)
        for i, (icon, text) in enumerate(WELCOME_CARDS):
            col = (row1 if i < 3 else row2)[i % 3]
            if col.button(f"{icon}  {text}", key=f"sg_{i}", use_container_width=True):
                st.session_state.pending = text
                st.rerun()
else:
    for m in messages:
        avatar = "🛰️" if m["role"] == "assistant" else "🧑‍💻"
        with st.chat_message(m["role"], avatar=avatar):
            st.markdown(m["content"])

# --------------------------------------------------------------------------- #
# 10. Input handling — per-conversation session (isolated memory)
# --------------------------------------------------------------------------- #
prompt = None
if st.session_state.get("pending"):
    prompt = st.session_state.pop("pending")
elif messages:
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
    session_id = st.session_state.current_id            # one ADK session per chat
    backend.ensure_session(session_id)
    with st.chat_message("assistant", avatar="🛰️"):
        with st.spinner("Working on it — routing, telemetry, runbooks & weather "
                        "(this can take 20–40s)…"):
            try:
                answer = backend.ask(styled, session_id=session_id)
            except Exception as exc:  # noqa: BLE001
                answer = f"⚠️ Error: {exc}"
        st.markdown(answer)
    messages.append({"role": "assistant", "content": answer})
    st.rerun()

# --------------------------------------------------------------------------- #
# 11. Incident-report export
# --------------------------------------------------------------------------- #
if messages and messages[-1]["role"] == "assistant":
    last_user = next((m["content"] for m in reversed(messages)
                      if m["role"] == "user"), "")
    report = (
        f"# NOC Incident / Analysis Report\n\n"
        f"- **Generated:** {datetime.now():%Y-%m-%d %H:%M} (local)\n"
        f"- **Query:** {last_user}\n\n"
        f"## Analyst finding\n\n{messages[-1]['content']}\n\n"
        f"---\n*Generated by the {APP_TITLE}. Mock training data; verify against live "
        f"NMS before acting.*\n")
    st.download_button(
        "📥 Download incident report (.md)", report,
        file_name=f"noc_report_{datetime.now():%Y%m%d_%H%M}.md",
        mime="text/markdown")
