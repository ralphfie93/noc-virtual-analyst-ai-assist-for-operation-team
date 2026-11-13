# 🛰️ NOC Virtual Analyst — AI Assist for the Operations Team

A production-grade **Enterprise Intelligence Virtual Analyst** for a satellite network
operations centre (NOC). It helps L1 operators triage **iDirect**, **HNS JUPITER** and
**Starlink** terminals (and the shared hub) by grounding every answer in real telemetry
and the team's runbooks — safely, observably, and read-only.

Built with **Google ADK** (multi-agent), the **Model Context Protocol** (MCP), and
**Streamlit**.

---

## Architecture summary

The analyst is a **coordinator-plus-specialists** multi-agent system. A coordinator agent
(`gemini-3.5-flash`) owns the conversation and delegates to three specialists via ADK
`AgentTool`s: a **Telemetry Analyst** that answers with live SQL over the telemetry
database, a **Runbook Advisor** that answers only from the NOC runbooks, and a **Weather
Checker** that uses Google Search grounding to corroborate rain-fade diagnoses. All data
access goes through a **real stdio MCP server** (`mcp_server.py`, launched as a subprocess
by ADK's `McpToolset`) that exposes *discovery primitives* — `mcp_list_tables`,
`mcp_describe_table`, `mcp_read_query`, `mcp_list_policies`, `mcp_read_policy`. Because the
schema is discovered dynamically at call time, the agent adapts to database changes with
**zero code edits** (demonstrated by the Live Migration Simulator).

Safety is enforced in **two tiers**. At the **input tier**, an ADK `before_model_callback`
masks PII (emails, phones, cards, ID codes), blocks prompt-injection attempts, and refuses
off-topic requests before the model is ever called. At the **data tier**, the MCP server is
the only path to the database: it opens every connection with a physical read-only lock
(`file:...?mode=ro`), verifies each query with a `sqlparse` **AST check** that blocks any
mutation/DDL however it is phrased, and simply **never registers** a write tool
(protocol-level least privilege). Every model completion is metered by an
`after_model_callback` that accumulates token counts and estimated USD cost, surfaced live
in the UI and in the automated **LLM-as-a-Judge** evaluation harness.

---

## Pillars → where they live

| Pillar | Implementation |
|---|---|
| 1 — Multi-agent / tools | `agent_core.py` — coordinator + telemetry / runbook / weather specialists |
| 2 — Enterprise integration (MCP) | `mcp_server.py` — SQLite + runbooks via MCP discovery primitives |
| 3 — Input guardrails | `guardrails.py` — PII masking, prompt-injection + off-topic blocking |
| 3 — Data guardrails | `mcp_server.py` — `is_sql_mutation` AST check + read-only URI |
| 4 — Observability / cost | `guardrails.py` `after_model_cost_tracker` + top-bar cost chip & Tools → Cost tab |
| 5 — Evaluation harness | `eval.py` + `capstone_golden.json` (LLM-as-a-Judge, 1–5) |
| 6 — Deployment | `app.py`, `requirements.txt`, `st.secrets`, Streamlit Cloud |

Extras: incident-correlation triage, telemetry charts, incident-report export, and the
live migration simulator (zero-code schema evolution).

---

## Project layout

```
app.py                 Streamlit UI (chat, charts, export, migration simulator)
agent_core.py          Multi-agent stack (shared by app + eval)
mcp_server.py          Stdio MCP server: 5 tools, AST guard, read-only URI
guardrails.py          Input guardrails + cost-tracking callback
config.py              Paths, model names, pricing
build_db.py            Downsamples the 136 MB source DB -> committed terminal_ops.db
eval.py                LLM-as-a-Judge harness
capstone_golden.json   Golden test cases (grounded in seeded incidents)
Database/              terminal_ops.db (committed) + schema.sql + README_SCHEMA.md
Knowledgebase/         NOC runbooks (iDirect / JUPITER / Starlink)
.streamlit/            secrets.toml.example
```

---

## Run locally

```bash
pip install -r requirements.txt

# 1) Build the committed DB from the 136 MB source (one-time; needs the source file)
python build_db.py

# 2) Provide a Gemini Developer API key (Google AI Studio)
#    Windows PowerShell:  $env:GOOGLE_API_KEY="..."
#    bash:                export GOOGLE_API_KEY=...
#    or copy .streamlit/secrets.toml.example -> .streamlit/secrets.toml

# 3) Launch
streamlit run app.py
```

## Run the benchmark

```bash
python eval.py            # all golden cases
python eval.py --limit 3  # first 3
```

Prints a per-case 1–5 table, the mean score, and the candidate's token/USD spend.

---

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub (the 136 MB source DB is git-ignored; the ~44 MB
   `terminal_ops.db` **is** committed and well under GitHub's 100 MB limit).
2. On [share.streamlit.io](https://share.streamlit.io), create an app from the repo with
   `app.py` as the entry point.
3. Under **App → Settings → Secrets**, add:
   ```toml
   GOOGLE_API_KEY = "your_gemini_api_key"
   ```
4. Deploy. The MCP server runs as a subprocess inside the same container.

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
Candidate spend: 1,894,113 in + 32,397 out tokens across 89 model calls = $3.13 USD
```

All seven golden cases scored 5/5: data accuracy, incident triage / one-vs-many
scoping, rain-fade + weather grounding, the FAP-throttle nuance (healthy signal vs
real fault), and both guardrails (destructive-mutation block + off-topic refusal).

---

## Notes & limitations

- **Mock data.** The telemetry is synthetic (seeded, reproducible); verify against the
  live NMS before acting on anything.
- **Pricing constants** in `config.py` are the published Gemini 3.5-class figures at build
  time — confirm current rates at <https://ai.google.dev/pricing>.
- The runbooks contain `<FILL>`/`TODO` placeholders for organisation-internal specifics; once
  filled with hostnames/contacts, **make the repo private**.
