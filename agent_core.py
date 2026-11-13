"""
agent_core.py — The multi-agent stack, shared by the Streamlit app and the eval
harness so both exercise the IDENTICAL agent (no drift between demo and benchmark).

Contains:
  * agent instructions
  * the MCP toolset factory (stdio subprocess -> read-only SQLite + runbooks)
  * build_coordinator()  — coordinator + telemetry / runbook / weather specialists
  * AnalystBackend       — a persistent asyncio loop that keeps the MCP subprocess
                           sessions alive and runs one query to completion.

No Streamlit dependency here on purpose.
"""

import asyncio
import sys
import threading

from config import BASE_DIR, MCP_SERVER_PATH, MODEL
from guardrails import after_model_cost_tracker, before_model_guardrail

APP_NAME = "noc_virtual_analyst"
USER_ID = "noc_operator"
SESSION_ID = "noc_session_001"

# --------------------------------------------------------------------------- #
# Instructions
# --------------------------------------------------------------------------- #
TELEMETRY_INSTR = """You are the Telemetry Analyst for a satellite NOC. You answer by
querying a read-only SQLite telemetry database through MCP tools.

WORKFLOW
- Discover the schema dynamically: call mcp_list_tables, then mcp_describe_table on the
  tables you need. Do NOT assume columns — verify them.
- Run queries with mcp_read_query using a single read-only SELECT. Mutations are blocked.

DATA RULES
- timestamp_myt is TEXT 'YYYY-MM-DD HH:MM:00' in MYT; it sorts lexicographically, so use
  string BETWEEN for time ranges.
- latency 9999.0 means TIMEOUT, not a real RTT — exclude it from averages
  (e.g. WHERE latency_rtt_ms < 9999).
- Triage on DEVIATION FROM CLEAR-SKY baseline, not absolutes:
    iDirect: downstream_snr_db vs clearsky_snr_db ; Jupiter: sqf vs clearsky_sqf.
    Starlink has no SNR — use state, currently_obstructed, fraction_obstructed_pct,
    pop_ping_drop_rate, pop_ping_latency_ms, and the alerts flags.
- "One vs many" is the key scoping question: group by the shared component
  (iDirect line_card/inroute_group/outroute, Jupiter beam/gateway/ipgw, Starlink pop/
  ground_station). If many terminals degrade together, JOIN hub_components
  ON component_id = <shared col> AND timestamp_myt = <same minute> to confirm a hub-side
  fault (status, fault_code, fault_detail).
- For trends, AGGREGATE (hourly via substr(timestamp_myt,1,13)||':00', AVG/SUM) instead
  of returning thousands of rows. Keep result sets small (add LIMIT).

Return concrete figures (values, timestamps, counts). Be concise and factual."""

RUNBOOK_INSTR = """You are a NOC first-level (L1) troubleshooting assistant for satellite
network operations. You guide L1 operators through first-level checks and tell them
precisely when and how to escalate.

Load the right runbook first: call mcp_list_policies, then mcp_read_policy on the file
for the system in question (idirect-hub-terminal.md, hns-jupiter.md, or starlink.md).

GROUND RULES
- Answer ONLY from the knowledge base. If the KB doesn't cover it, say so and route to
  escalation — do NOT invent state codes, thresholds, IPs, or commands.
- State/diagnostic codes vary by model/software. Never assert an exact numeric code as
  fact unless it is in the KB; otherwise give the code CATEGORY and say to verify against
  the terminal manual / NMS.
- Scope every fault first: ONE terminal vs MANY on the same inroute/outroute/line card/
  beam/gateway. Many together => hub/gateway/space-segment side => escalate.
- Enforce L1 boundaries: no config edits, no RF/pointing/commissioning, no gateway/line-
  card/PP actions, no account/billing changes. Those are ESCALATE, not steps.
- Prefer deviation-from-clear-sky reasoning over absolute signal numbers.

RESPONSE FORMAT
1. Most-likely cause(s), briefly.
2. Ordered first-level checks (concrete, safe, one action each).
3. Escalation trigger + the exact pre-escalation data bundle to collect first.
Be concise and operational."""

WEATHER_INSTR = """You are the Weather Checker for a satellite NOC. Ka-band (JUPITER) and,
to a lesser extent, Ku-band (iDirect) links suffer RAIN FADE, so current weather at a
terminal's physical site helps confirm or rule out a weather-driven signal drop.

- You are given a location/site (e.g. Kuala Lumpur, Kuching, Miri, Johor Bahru,
  Singapore). Use google_search to find the CURRENT weather there.
- Report a one-line summary (conditions, rain yes/no, intensity if known) and state
  whether rain fade is a plausible contributor.
- NEVER fabricate. If search returns nothing useful, say the weather is unavailable and
  that rain fade cannot be confirmed from weather data."""

COORDINATOR_INSTR = """You are the NOC Virtual Analyst — the coordinator of the
satellite operations assistant. You help L1 operators triage iDirect, HNS JUPITER and
Starlink terminals and the hub.

SCOPE (hard rule)
- Answer ONLY questions about satellite network operations: terminal/hub telemetry,
  incidents, troubleshooting, the runbooks, and weather at a terminal site (for rain
  fade). Politely refuse anything else and redirect to ops topics. Never help with
  unrelated tasks (coding, trivia, creative writing, etc.).
- You are strictly READ-ONLY. Never suggest or perform data mutations.

TOOLS (delegate — don't guess)
- telemetry_analyst: live data / SQL over the telemetry DB (numbers, timelines, counts,
  deviation-from-clear-sky, one-vs-many scoping, hub JOINs).
- runbook_advisor: procedures, state/alert code meaning, root causes, L1 boundaries,
  escalation and the pre-escalation data bundle.
- weather_checker: current weather at a terminal's site when rain fade is suspected
  (pass the site/city, which you can get from the telemetry site name).

INCIDENT TRIAGE PATTERN (for "why is <terminal> degraded / down at <time>?")
1. telemetry_analyst: pull the terminal's timeline and deviation from clear-sky.
2. telemetry_analyst: scope one-vs-many — are peers on the same component affected? If
   so, JOIN hub_components to check for a hub-side fault.
3. If a rain-fade signature appears (transient SNR/SQF dip, Tx pinned), ask
   weather_checker for the site's weather.
4. runbook_advisor: map the signature to the ranked root cause, first-level checks, and
   escalation trigger + data bundle.
5. Synthesize ONE concise, operational answer with concrete figures. State clearly
   whether this is a single-site issue or a hub/space-segment issue that must escalate."""


# --------------------------------------------------------------------------- #
# Agent construction
# --------------------------------------------------------------------------- #
def make_toolset(tool_filter):
    from google.adk.tools.mcp_tool.mcp_toolset import McpToolset
    from google.adk.tools.mcp_tool import StdioConnectionParams
    from mcp import StdioServerParameters

    return McpToolset(
        connection_params=StdioConnectionParams(
            server_params=StdioServerParameters(
                command=sys.executable,
                args=[str(MCP_SERVER_PATH)],
                cwd=str(BASE_DIR),
            ),
            timeout=30,
        ),
        tool_filter=list(tool_filter),
    )


def build_coordinator():
    from google.adk.agents import Agent
    from google.adk.tools import google_search
    from google.adk.tools.agent_tool import AgentTool

    telemetry = Agent(
        name="telemetry_analyst", model=MODEL,
        description="Queries the read-only telemetry database via MCP SQL tools.",
        instruction=TELEMETRY_INSTR,
        tools=[make_toolset(["mcp_list_tables", "mcp_describe_table", "mcp_read_query"])],
        after_model_callback=after_model_cost_tracker,
    )
    runbook = Agent(
        name="runbook_advisor", model=MODEL,
        description="Answers from the NOC runbooks via MCP policy tools.",
        instruction=RUNBOOK_INSTR,
        tools=[make_toolset(["mcp_list_policies", "mcp_read_policy"])],
        after_model_callback=after_model_cost_tracker,
    )
    weather = Agent(
        name="weather_checker", model=MODEL,
        description="Checks current weather at a terminal site (rain-fade context).",
        instruction=WEATHER_INSTR,
        tools=[google_search],
        after_model_callback=after_model_cost_tracker,
    )
    return Agent(
        name="noc_coordinator", model=MODEL,
        description="NOC Virtual Analyst coordinator.",
        instruction=COORDINATOR_INSTR,
        tools=[AgentTool(agent=telemetry), AgentTool(agent=runbook),
               AgentTool(agent=weather)],
        before_model_callback=before_model_guardrail,
        after_model_callback=after_model_cost_tracker,
    )


class AnalystBackend:
    """Persistent asyncio loop keeping MCP subprocess sessions alive across calls."""

    def __init__(self):
        from google.adk.runners import Runner
        from google.adk.sessions import InMemorySessionService

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self._spin, daemon=True).start()

        self.coordinator = build_coordinator()
        self.session_service = InMemorySessionService()
        self.runner = Runner(agent=self.coordinator, app_name=APP_NAME,
                             session_service=self.session_service)
        self._submit(self.session_service.create_session(
            app_name=APP_NAME, user_id=USER_ID, session_id=SESSION_ID))

    def _spin(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result()

    async def _ask(self, text, session_id):
        from google.genai import types
        content = types.Content(role="user", parts=[types.Part.from_text(text=text)])
        final_chunks, last_text, err = [], "", None
        async for event in self.runner.run_async(
                user_id=USER_ID, session_id=session_id, new_message=content):
            if getattr(event, "error_message", None):
                err = event.error_message
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        last_text = part.text
                        if event.is_final_response():
                            final_chunks.append(part.text)
        answer = "".join(final_chunks).strip() or last_text.strip()
        if answer:
            return answer
        if err:
            return f"⚠️ The model returned an error: {err}"
        return "⚠️ No response was produced. (Possible rate limit or tool timeout — try again.)"

    def ensure_session(self, session_id):
        try:
            self._submit(self.session_service.create_session(
                app_name=APP_NAME, user_id=USER_ID, session_id=session_id))
        except Exception:  # noqa: BLE001 - already exists
            pass

    def ask(self, text: str, session_id: str = SESSION_ID) -> str:
        return self._submit(self._ask(text, session_id))
