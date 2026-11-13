# NOC First-Level Troubleshooting — Knowledge Base

LLM-grounding corpus for a NOC L1 troubleshooting assistant (Python + LLM API + Streamlit).

## Files
| File | System |
|---|---|
| `starlink.md` | Starlink (LEO VSAT) |
| `idirect-hub-terminal.md` | iDirect hub & remote terminals (GEO TDMA) |
| `hns-jupiter.md` | HNS JUPITER terminals + gateway (GEO Ka) |

## Shared schema (keep this consistent when adding systems)
Each file: YAML front-matter → **overview → where to check status → key indicators (table) → symptom→flow → state/alert codes → common root causes (ranked) → pre-escalation data bundle → L1 boundaries.** This uniformity is what keeps the agent's behavior predictable across systems and makes chunking clean.

Each system file may also carry an **equipment / front-panel LED** section (iDirect §2A, HNS §3A) for hands-on terminal-light triage; Starlink adds a **WiFi/SSID & router-lights** section (§2A). Embedded vendor tables are quoted with an inline source citation; model/firmware-dependent details carry a caveat or `<FILL>` rather than being asserted.

## Wiring into the app
Two viable paths:

1. **Stuff-in-context (start here).** Total corpus is small; concatenate the relevant file(s) into the system/context on each turn. Simplest, no vector DB, deterministic. Add a lightweight router: detect system from the query and inject only that file to save tokens. Keywords:
- **Starlink** — `starlink`, `dishy`, `ssid`, `wifi`, `bypass`, `router light`, `obstruction`.
- **iDirect** — `idirect`, `evolution`, `remote`, `inroute`, `outroute`, `line card`, `rx/tx/net led`, `options file`, `web isite`.
- **HNS Jupiter** — `hns`, `hughes`, `jupiter`, `sqf`, `state code`, `scc`.
- Equipment terms shared across systems: `led`, `rx`, `tx`, `net`, `light`, `blinking`, `red/green/amber` — route by the system named in the query; if none, inject all three.

If ambiguous, inject all three.
2. **RAG (when the corpus grows).** Chunk by `##`/`###` heading (the schema makes headings semantically clean), embed, retrieve top-k, cite the source file + heading. Overkill for 3 files; worth it once you add more systems/runbooks.

> Note for Streamlit Cloud: the LLM API key goes in **`st.secrets`** (Streamlit secrets), never committed. These `.md` files can live in the repo; nothing here is sensitive **until you fill the `<FILL>`/`TODO` placeholders** — once those contain internal hostnames/contacts, make the repo **private**.

## Agent system prompt (template)

```
You are a NOC first-level (L1) troubleshooting assistant for satellite network
operations. You guide L1 operators through first-level checks and tell them
precisely when and how to escalate.

GROUND RULES
- Answer ONLY from the provided knowledge base. If the KB doesn't cover it, say so
  and route to escalation — do NOT invent state codes, thresholds, IPs, or commands.
- State/diagnostic codes vary by model/software. Never assert an exact numeric code
  as fact unless it is in the KB; otherwise give the code CATEGORY and tell the
  operator to verify against the terminal manual / NMS.
- Scope every fault first: ONE terminal vs MANY on the same inroute/outroute/line
  card/beam/gateway. Many together => hub/gateway/space-segment side => escalate;
  do not walk a per-site tree.
- Enforce L1 boundaries: no config edits (iBuilder/NMS), no RF/pointing/commissioning,
  no gateway/line-card/PP actions, no account/billing/provisioning changes. Those are
  ESCALATE, not steps.
- Prefer deviation-from-clear-sky-baseline reasoning over absolute signal numbers.

RESPONSE FORMAT
1. Most-likely-cause(s), briefly.
2. Ordered first-level checks (concrete, safe, one action each).
3. Explicit escalation trigger + the exact data bundle to collect first (from the
   KB's "Before escalating" list).
Be concise and operational. No filler.

If the operator is distressed or the situation is a major outage, stay calm, scope
it, and escalate fast.
```

## TODO — organisation-specific fills (search `<FILL>` / `TODO` in each file)
- [ ] NMS/portal hosts + access policy: iDirect iMonitor, Hughes NMS, Starlink Enterprise portal.
- [ ] Terminal SCC IP per Hughes model in use.
- [ ] Per-network baselines/thresholds: iDirect clear-sky C/N + UCP target; Hughes clear-sky SQF; Starlink baseline obstruction%/latency.
- [ ] Naming conventions: remote DID / SAN / service-line IDs; beam/gateway/inroute/line-card labels.
- [ ] Escalation matrix: L2 / hub-teleport / gateway ops / vendor contacts (link to your `people/` contacts), ticketing ref.
- [ ] Any site-specific SOPs (e.g., whether L1 may soft-reset a single remote/terminal).
- [ ] Verify Hughes state-code numerics against your terminal model's manual (replace the ~category rows with exact codes).
- [ ] Confirm exact equipment models so the generic LED tables can be made exact: iDirect Evolution model (X1/X3/X5/X7) + iDX release; Hughes HT model (HT2000/HT2010/HT1100) + its LED table; Starlink router gen (Gen2/Gen3) + whether bypass is the standard deployment.

## Embedded source references (equipment/LED sections)
- **iDirect §2A** — iDX *X1 Series Satellite Router Installation, Support, and Maintenance Guide*: Table 3-1 (front-panel LEDs) and Table 6-1 (troubleshooting). State definitions may be software-version dependent; X3/X5/X7 share the RX/TX/NET/POWER labels — verify per model.
- **Starlink §2A** — *Gen 3 Router Setup Guide (Standard)*: router light table + "Can't Get Online?" steps.
- **HNS §3A** — no authoritative HT2000/HT2010 LED table was retrievable; the section is documented model-family convention only and is flagged to verify against the model's user guide.

## Maintenance
- `last_reviewed` in each front-matter — bump on edit.
- Validate exact state codes/metrics against vendor docs and your own incident history; the KB is seeded from public references + general VSAT practice and should be corrected to your operational reality before production use.
```
