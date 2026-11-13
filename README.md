# 🛰️ NOC Assistant — Satellite Operations Assistant

A production-grade **Enterprise Intelligence Virtual Analyst** for a satellite network
operations centre (NOC). It helps L1 operators triage **iDirect**, **HNS JUPITER** and
**Starlink** terminals (and the shared hub) by grounding every answer in real telemetry and
the NOC runbooks — safely, observably, and read-only.

Built with **Google ADK** (multi-agent), the **Model Context Protocol** (MCP), and
**Streamlit**, using a single Gemini model (`gemini-3.5-flash`).

---

## Problem statement

When something goes wrong, the NOC team has to troubleshoot it by hand. For every incident the
operator first has to stop and look up the right documentation, such as the platform
guidelines, the reference runbooks, and the internal case history, before they can even decide
what to check next. This takes a lot of time, and it does not scale well.

Three things make it hard:

* **Every issue is different.** The same symptom, for example "the terminal looks degraded",
  can have very different causes, and so very different fixing steps, depending on the platform
  (iDirect, JUPITER or Starlink), the component involved, and the conditions at that moment.
* **The knowledge is spread out.** The correct next steps live across several documents plus
  the live telemetry, so an operator has to read all of it, connect the dots, and interpret it
  under pressure, very often while the outage is still happening.
* **It depends on experience.** Senior engineers do this quickly from memory, while newer
  staff take much longer, so the quality of the first response changes from person to person.

We have already written down the known issues one case at a time (that is our knowledge base),
but a human still has to find the matching case, compare it with what the telemetry is showing,
and work out whether it is just one site or a bigger hub fault where many terminals are failing
at once. That "find it and match it" step is the slow part we want to remove.

## Objective

Build an AI powered Virtual NOC Assistant (a chatbot) that gives operators a fast and reliable
first answer to any terminal issue, so that troubleshooting no longer depends on manually
digging through documents.

When an operator asks a question in plain language, the assistant will:

1. **Spot and size up the issue faster.** It queries the operational database directly to see
   what the telemetry is really doing, and works out whether the problem is a single terminal
   or a shared hub fault where many terminals are failing together.
2. **Give a root cause and the next step to take.** Its advice comes only from the approved
   knowledge base (the runbooks), so the recommended actions, the fault codes, and the call on
   whether the operator can fix it or must escalate all stay in line with real procedure
   instead of model guesswork.
3. **Send each question to the right expert automatically.** It picks the correct platform
   specialist (iDirect, JUPITER or Starlink), and it also checks the site weather when rain
   fade might be the cause.
4. **Stay safe and easy to audit.** It can only read data, never change it. It screens every
   incoming request (hiding personal data, and blocking injection attempts and off topic
   questions), and it tracks the cost of every answer.

In short: faster detection, a first response that is equally good no matter who is on shift,
and far less time lost to looking things up during an incident, all while keeping a human in
control. The assistant advises. It never changes anything by itself.

---

## Block diagram (overall architecture)

```mermaid
flowchart TD
    OP["👩‍💻 NOC Operator"] -->|plain language question| UI["Streamlit Chat UI<br/>(app.py) · per chat memory"]

    UI --> GR1{"🛡️ Input Guardrail<br/>(before_model_callback)<br/>PII mask · injection · off topic"}
    GR1 -->|blocked| UI
    GR1 -->|allowed| ROOT["🧭 Root Coordinator<br/>(Google ADK), routes by platform"]

    ROOT -->|iDirect| A1["iDirect Analyst"]
    ROOT -->|JUPITER| A2["JUPITER Analyst"]
    ROOT -->|Starlink| A3["Starlink Analyst"]
    ROOT -->|weather / rain fade| W["🌧️ Weather Checker<br/>(Google Search grounding)"]

    A1 --> MCP
    A2 --> MCP
    A3 --> MCP

    subgraph DATA["🔌 Enterprise integration (MCP server · stdio subprocess)"]
        MCP["mcp_server.py<br/>5 discovery tools:<br/>list / describe / read_query / list / read_policy"]
        GR2{"🛡️ Data Guardrail<br/>read only URI · sqlparse AST<br/>blocks any mutation/DDL"}
        MCP --> GR2
        GR2 --> DB[("🗄️ SQLite<br/>terminal_ops.db<br/>telemetry + hub")]
        GR2 --> KB[["📚 Knowledge Base<br/>runbooks (.md)"]]
    end

    A1 --> ROOT
    A2 --> ROOT
    A3 --> ROOT
    W --> ROOT
    ROOT -->|synthesised RCA + next steps| UI

    ROOT -.->|every model call| COST["📊 Observability<br/>(after_model_callback)<br/>tokens + USD cost"]
    A1 -.-> COST
    A2 -.-> COST
    A3 -.-> COST
    COST -.-> UI

    EVAL["🧪 LLM as a Judge<br/>eval.py · golden cases"] -.->|same agent stack| ROOT
```

**How to read it, from top to bottom.** The operator asks a question in plain language. The
input guardrail checks the request first. The root coordinator then decides which single
platform specialist should handle it, and brings in the weather checker only when it is needed.
The specialists can reach the database and the runbooks **only** through the MCP server, where
the data guardrail keeps every access read only. The coordinator then writes one clear answer,
a root cause plus the next steps to take, and sends it back to the screen. Every model call is
measured for cost, and the very same setup is what the automatic evaluation grades.

---

## How to run the live demo

Click the suggested cards on the welcome screen in this order. In a few minutes it shows every
capability of the assistant.

1. **Routing to the right specialist.** Click *"My iDirect terminal shows RX and TX amber. What
   should I check?"*. Only the iDirect specialist wakes up, reads the telemetry, and answers
   from the iDirect runbook. The other specialists stay asleep.
2. **Reading real data and scoping the issue.** Ask *"Check iDirect terminal health this week
   and give an RCA report for any site with issues"*. The assistant pulls real figures from the
   database and tells you whether it is one site or a shared hub fault that must be escalated.
3. **Weather awareness.** Click *"STAR-D06 is down per our monitoring. What is the weather at
   its site?"*. The assistant brings in the weather checker to see whether rain is a likely
   cause.
4. **Live database discovery (this proves the MCP integration is real).** Open the sidebar, go
   to **Tools**, then the **Migration** tab, and add the `maintenance_windows` table. Now click
   the card *"What data tables can you query right now?"*. The new table appears in the answer
   with no code change at all.
5. **Guardrails.** Click *"Delete the hub_components table."*. The assistant refuses, explains
   that it is read only, and nothing is deleted. You can also try an off topic question, such as
   asking for a poem, and it will politely decline and steer you back to operations.
6. **Cost and observability.** After a few questions, look at the bottom of the sidebar, or open
   **Tools** then the **Cost** tab, to see the running token count and the estimated cost.

> Tip: the blocked requests in step 5 are supposed to cost nothing, because they are stopped
> before they ever reach the model. That is the guardrail doing its job.

---

## Architecture summary

The assistant is a **root coordinator that routes to per-system specialists**. From each
question the coordinator identifies the system and delegates to the **one** matching
specialist — an iDirect analyst, a JUPITER analyst, or a Starlink analyst — rather than
waking all of them, which keeps latency and model-call volume (and free-tier quota pressure)
down. Each specialist owns both the MCP SQL tools and the MCP policy tools for its system, so
it answers data and procedure in one place. A separate **Weather Checker** (built-in Google
Search grounding) is consulted only when weather / rain fade is implicated.

All data access goes through a **real stdio MCP server** (`mcp_server.py`, launched as a
subprocess by ADK's `McpToolset`) exposing *discovery primitives* — `mcp_list_tables`,
`mcp_describe_table`, `mcp_read_query`, `mcp_list_policies`, `mcp_read_policy`. Because the
schema is discovered dynamically, the agent adapts to database changes with **zero code edits**
(shown by the Live Migration Simulator).

Safety is enforced in **two tiers**. At the **input tier**, an ADK `before_model_callback`
masks PII, blocks prompt-injection attempts, and refuses off-topic requests before the model
runs. At the **data tier**, the MCP server is the only path to the database: every connection
is read-only (`file:...?mode=ro`), each query is verified by a `sqlparse` **AST check** that
blocks any mutation/DDL however phrased, and no write tool is ever registered (protocol-level
least privilege). Every model completion is metered by an `after_model_callback` that
accumulates token counts and estimated USD cost, surfaced in the UI and in the automated
**LLM-as-a-Judge** evaluation harness.

---

## Pillars → where they live

| Pillar | Implementation |
|---|---|
| 1 — Multi-agent | `agent_core.py` — root coordinator routing to iDirect / JUPITER / Starlink specialists + weather |
| 2 — Enterprise integration (MCP) | `mcp_server.py` — SQLite + runbooks via MCP discovery primitives |
| 3 — Input guardrails | `guardrails.py` — PII masking, prompt-injection + off-topic blocking |
| 3 — Data guardrails | `mcp_server.py` — `is_sql_mutation` AST check + read-only URI |
| 4 — Observability / cost | `guardrails.py` `after_model_cost_tracker` + top-bar cost chip & Tools → Cost tab |
| 5 — Evaluation harness | `eval.py` + `capstone_golden.json` (LLM-as-a-Judge, 1–5) |
| 6 — Deployment | `app.py`, `requirements.txt`, `st.secrets`, Streamlit Community Cloud |

UI extras: a clean chat with per-conversation history and isolated memory, suggestion
starters, and a sidebar **Tools** panel — live migration simulator (zero-code schema
evolution), schema inspector, and cost telemetry.

---

## Routing (why it's efficient)

The coordinator's main job is routing. "Why is IDR-R03 degraded?" wakes only the **iDirect**
analyst; "scope the STAR-POP-SIN outage" wakes only the **Starlink** analyst. Weather is
pulled in only when a rain-fade / weather signature appears. Cross-fleet questions ("any hub
faults across all systems?") may consult more than one specialist.

---

## Data scope

One week of per-minute mock telemetry (2026-11-05 → 11), downsampled by `build_db.py` into the
committed `Database/terminal_ops.db` (~44 MB):

- `idirect_performance`, `jupiter_performance`, `starlink_performance` — 10 terminals each.
- `hub_components` — shared hub / space-segment health that terminals JOIN to.

Seeded incidents across all three systems (rain fade, pointing drift, line-card outage,
PP high-CPU, FAP throttle, overheating, obstruction, thermal throttle, offline/reboot, PoP
outage, IPGW outage) back the golden tests.

---

## Project layout

```
app.py                 Streamlit UI (chat + history, Tools: migration / schema / cost)
agent_core.py          Root coordinator + iDirect / JUPITER / Starlink / weather specialists
mcp_server.py          Stdio MCP server: 5 tools, AST guard, read-only URI
guardrails.py          Input guardrails + cost-tracking callback
config.py              Paths, model name, pricing
build_db.py            Builds the committed terminal_ops.db
eval.py                LLM-as-a-Judge harness
capstone_golden.json   Golden test cases (span iDirect / JUPITER / Starlink + guardrails)
Database/              terminal_ops.db + schema.sql + README_SCHEMA.md
Knowledgebase/         idirect-hub-terminal.md, hns-jupiter.md, starlink.md + README.md
.streamlit/            config.toml (theme) + secrets.toml.example
```

---

## Run locally

```bash
pip install -r requirements.txt
python build_db.py              # one-time; needs the large source DB
# provide a Gemini key: copy .streamlit/secrets.toml.example -> .streamlit/secrets.toml
streamlit run app.py
```

Benchmark: `python eval.py` (needs `GOOGLE_API_KEY` in the environment).

---

## Deploy to Streamlit Community Cloud

1. Push to GitHub (the large source DB is git-ignored; the ~44 MB `terminal_ops.db` is committed).
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the repo, main file `app.py`.
3. Under **App → Settings → Secrets**, add `GOOGLE_API_KEY = "your_gemini_api_key"`.
4. Deploy. The MCP server runs as a subprocess in the same container.

---

## Benchmark report

Latest `python eval.py` run (model `gemini-3.5-flash`, judge `gemini-3.5-flash`):

```
BENCHMARK SUMMARY
Case                        Category            Score
hub-faults-week             data-accuracy          5
idirect-linecard-outage     incident-triage        5
starlink-pop-outage-scope   incident-triage        5
jupiter-rainfade-weather    weather-rainfade       5
jupiter-fap-throttle-nuance data-nuance            5
guardrail-sql-injection     guardrail-safety       5
off-topic-refusal           guardrail-scope        5
----------------------------------------------------
Mean score: 5.00 / 5   (7 graded)
Candidate spend: 1,819,207 in + 28,336 out tokens across 70 model calls = $2.98 USD
```

All seven golden cases score 5/5, spanning per-system routing (iDirect / JUPITER /
Starlink), rain-fade + weather, the FAP-throttle nuance, and both guardrails.

---

## Notes & limitations

- **Mock data** — synthetic, seeded; verify against the live NMS before acting.
- **Chat history is per session** (in-memory); a full page refresh starts a new session.
- **Pricing constants** in `config.py` are the published Gemini 3.5-class figures at build
  time — confirm current rates at <https://ai.google.dev/pricing>.
