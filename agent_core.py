"""
agent_core.py — The agent stack, shared by the Streamlit app and the eval harness so
both exercise the IDENTICAL agent (no drift between demo and benchmark).

Architecture: a ROOT coordinator that ROUTES each question to the ONE relevant
per-system specialist (so a question about iDirect only wakes the iDirect agent, etc.),
plus a weather specialist used only when weather/rain-fade is implicated:

  * idirect_analyst   — iDirect remotes + iDirect hub  (telemetry + idirect runbook)
  * jupiter_analyst   — JUPITER terminals + Ka hub      (telemetry + jupiter runbook)
  * starlink_analyst  — Starlink dishes + PoP hub       (telemetry + starlink runbook)
  * weather_checker   — current site weather via built-in google_search

Each system specialist has both the MCP SQL tools and the MCP policy tools, so it can
answer data + procedure for its system in one place. The root owns the input guardrail;
every agent reports cost via after_model_callback. MCP discovery stays dynamic.

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

SYSTEM_TOOLS = ["mcp_list_tables", "mcp_describe_table", "mcp_read_query",
                "mcp_list_policies", "mcp_read_policy"]

# --------------------------------------------------------------------------- #
# Shared data rules + per-system specifics
# --------------------------------------------------------------------------- #
_COMMON = """EFFICIENCY (minimise tool calls): you already know your system's tables (named
below) — go STRAIGHT to mcp_read_query with ONE read-only SELECT; do NOT call mcp_list_tables
or mcp_describe_table unless a query errors or you must find a NEW table. Read your runbook
with mcp_read_policy ONLY when the question needs procedure, root-cause ranking, codes, L1
boundaries or escalation — SKIP it for pure data lookups. Prefer a single combined query over
several.

timestamp_myt is TEXT 'YYYY-MM-DD HH:MM:00' (MYT) and sorts lexicographically — use string
BETWEEN for ranges. latency 9999.0 = TIMEOUT (exclude from averages). Aggregate hourly for
trends; keep result sets small. When you do read the runbook, answer ONLY from it (never
invent codes/thresholds). Return concrete figures; say whether an issue is single-site or a
hub-side fault that must ESCALATE."""

IDIRECT_INSTR = f"""You are the iDirect Analyst (ST Engineering Evolution, GEO Ku). You cover
iDirect remotes (IDR-R01..R10) in idirect_performance and the iDirect hub in hub_components
(line cards IDR-LC-*, protocol processors IDR-PP-*, inroute groups, outroutes).

{_COMMON}

iDirect specifics:
- Triage on deviation: downstream_snr_db vs clearsky_snr_db, upstream_cn_db vs ucp_target_db.
  tx_power_dbm pinned at tx_max_power_dbm = no headroom (fade/pointing/BUC). Watch
  network_state (In Network/Acquisition/Out of Network/Fault) and modem_led.
- One vs many: group by line_card / inroute_group / outroute. If many remotes fail together,
  JOIN hub_components ON component_id = line_card AND timestamp_myt to confirm a hub fault.
- Your runbook is idirect-hub-terminal.md."""

JUPITER_INSTR = f"""You are the JUPITER Analyst (Hughes, GEO Ka HTS). You cover JUPITER
terminals (JUP-T01..T10) in jupiter_performance and the Ka hub in hub_components (gateways
JUP-GW-*, spot beams JUP-BEAM-*, IPGWs JUP-IPGW-*).

{_COMMON}

JUPITER specifics:
- Triage on deviation: sqf vs clearsky_sqf. state_code categories: 11.x pointing, 12.x
  degraded, 13.x Tx-radio, 30.x overheating. data_allowance_state = FAP_Throttled means a
  fair-access throttle (slow but HEALTHY signal — not a fault). latency 9999 = timeout.
- One vs many: group by beam / gateway / ipgw. If many terminals lose service together, JOIN
  hub_components ON component_id = ipgw (or gateway/beam) AND timestamp_myt.
- Your runbook is hns-jupiter.md."""

STARLINK_INSTR = f"""You are the Starlink Analyst (LEO). You cover Starlink dishes
(STAR-D01..D10) in starlink_performance and the PoP/ground-station hub in hub_components
(STAR-POP-*, STAR-GS-*).

{_COMMON}

Starlink specifics:
- No SNR. Triage on state (CONNECTED/BOOTING/SEARCHING/OFFLINE/STOWED), currently_obstructed,
  fraction_obstructed_pct, pop_ping_drop_rate, pop_ping_latency_ms, uptime_s (a reset = a
  reboot), and the alerts flags (obstructed, thermal_throttle, slow_ethernet_speeds, ...).
- One vs many: group by pop. If many dishes on the same PoP go offline together, JOIN
  hub_components ON component_id = pop AND timestamp_myt to confirm a PoP outage.
- Your runbook is starlink.md."""

WEATHER_INSTR = """You are the Weather Checker for a satellite NOC. Rain fade affects Ka-band
(JUPITER) most, and Ku-band (iDirect) somewhat; heavy weather can also coincide with Starlink
degradation. Given a location/site, use google_search for the CURRENT weather and report a
one-line summary plus whether weather is a plausible contributor. NEVER fabricate; if nothing
useful is found, say weather is unavailable."""

ROOT_INSTR = """You are the NOC Assistant — the coordinator of a multi-system satellite
operations assistant (iDirect, HNS JUPITER, Starlink and the shared hub).

SCOPE (hard rule): answer ONLY satellite network operations questions — telemetry, incidents,
troubleshooting, runbooks, and site weather. Politely refuse anything else and redirect. You
are strictly READ-ONLY; never suggest or perform data changes.

ROUTING — this is your main job. Identify the system from the question and delegate to the
ONE matching specialist; do NOT call the others:
- iDirect / remote / IDR-* / line card / inroute / outroute  -> idirect_analyst
- JUPITER / HNS / Hughes / JUP-* / SQF / state code / beam / gateway / IPGW -> jupiter_analyst
- Starlink / dish / STAR-* / obstruction / PoP / dishy        -> starlink_analyst
- weather / rain fade at a site -> weather_checker (only when weather is implicated)
Only when a question is explicitly cross-fleet ("across all systems", "any terminal
anywhere") may you consult more than one specialist. If the system is ambiguous, ask a brief
clarifying question instead of calling every agent.

Pass the specialist the full user question. Then synthesise ONE concise, operational answer
with concrete figures, and state clearly whether the issue is single-site or a hub/space-
segment fault that must ESCALATE. Lead with the answer."""


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


def _system_agent(name, description, instruction):
    from google.adk.agents import Agent
    return Agent(
        name=name, model=MODEL, description=description, instruction=instruction,
        tools=[make_toolset(SYSTEM_TOOLS)],
        after_model_callback=after_model_cost_tracker,
    )


def build_coordinator():
    """Return the root coordinator that routes to per-system specialists + weather."""
    from google.adk.agents import Agent
    from google.adk.tools import google_search
    from google.adk.tools.agent_tool import AgentTool

    idirect = _system_agent(
        "idirect_analyst",
        "iDirect remotes (IDR-R*) and iDirect hub: line cards, protocol processors, "
        "inroutes/outroutes. Ku-band, SNR/CN deviation, network_state.",
        IDIRECT_INSTR)
    jupiter = _system_agent(
        "jupiter_analyst",
        "HNS JUPITER terminals (JUP-T*) and Ka hub: gateways, spot beams, IPGWs. "
        "SQF, state codes, FAP throttling, rain fade.",
        JUPITER_INSTR)
    starlink = _system_agent(
        "starlink_analyst",
        "Starlink dishes (STAR-D*) and PoP/ground-station hub. Obstruction, ping drop, "
        "alerts, PoP outages.",
        STARLINK_INSTR)
    weather = Agent(
        name="weather_checker", model=MODEL,
        description="Current weather at a terminal/dish site (rain-fade / weather context).",
        instruction=WEATHER_INSTR, tools=[google_search],
        after_model_callback=after_model_cost_tracker,
    )
    return Agent(
        name="noc_coordinator", model=MODEL,
        description="NOC Assistant — routes to per-system specialists.",
        instruction=ROOT_INSTR,
        tools=[AgentTool(agent=idirect), AgentTool(agent=jupiter),
               AgentTool(agent=starlink), AgentTool(agent=weather)],
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
