---
system: iDirect (VSAT)
platform: GEO TDMA/SCPC (iDirect Evolution / iDX; iNFINITI legacy)
audience: NOC L1 / first-level
scope: First-level remote-state triage from the hub NMS + basic terminal checks. NOT for hub reconfig, line-card/PP work, option-file edits, or RF commissioning.
last_reviewed: 2026-11-12
owner: <FILL: D&A team / Rafie>
---

# iDirect Hub & Terminal — NOC First-Level Troubleshooting

> **Agent guidance:** For iDirect, the first question is always **"is the remote In Network?"** and **"is this one remote or many?"** One remote down = remote-side (RF/power/cable/pointing/weather). Many remotes on the same inroute/outroute/line card/beam = **hub- or space-segment-side → ESCALATE**. L1 reads state in iMonitor and does safe checks; L1 does **not** edit configs in iBuilder or touch hub hardware.

## 1. System overview (context only)
- **Topology:** Remotes (satellite routers) ⇄ GEO satellite ⇄ **Hub** (Protocol Processor + line cards, inroutes/outroutes) ⇄ teleport/network. TDMA upstream (shared inroute), TDM/DVB-S2 downstream (outroute).
- **Band:** typically Ku (also Ka/C). **Rain fade** affects the link, more severe at higher bands.
- **Tools:**
  - **iMonitor** — real-time + historical state, SATCOM view, latency, C/N, power. *L1 lives here.*
  - **iBuilder** — configuration (option files, power, clear-sky C/N). *L2+ only.*
  - **iSite / local modem UI / console** — on-site commissioning, local SNR/telnet checks.
- NMS access: <FILL: iMonitor/NMS host + credentials policy>

## 2. Remote state — the primary signal (iMonitor)
| State | Meaning | First read |
|---|---|---|
| **In Network** | Acquired, passing traffic | Healthy. If user still complains → look at C/N, latency, LAN/CPE |
| **Acquisition** | Trying to join (ranging) | Transient OK; stuck here = Tx/pointing/power/reference issue |
| **Out of Network / Offline** | Not communicating with hub | Core triage (§4A) |
| **Fault** | Modem/line reporting a fault | Check fault detail; power/hardware |

## 2A. Modem front-panel LEDs (equipment-level read)
> **Source:** iDX *X1 Series Satellite Router Installation, Support, and Maintenance Guide*, **Table 3-1 "X1 Router Front Panel LED Indicators."** The manual states the **LED state definitions may be software-version dependent** and are also documented in the iDX Web iSite User Guide / Satellite Router Installation & Commissioning Guide / Release Notes. The **X1/X3/X5/X7** Evolution routers use the same **RX / TX / NET / POWER** labels; confirm against the exact model's ISM before treating a code as fact. **<FILL: Evolution model (X1/X3/X5/X7) + iDX release>**
>
> The **X1 Outdoor Router** has **only one physical LED**; during install, Web iSite shows **four simulated LEDs** that mirror this table.

| LED | State | Documented meaning |
|---|---|---|
| **RX** | Off | Receiver is disabled or not configured |
| | Solid Yellow | Downstream carrier configured, demodulator **not** locked |
| | Slow Flashing Yellow | Downstream carrier configured + demod locked, **NCR not locked** |
| | **Solid Green** | Downstream carrier configured, **demod and NCR locked** (Rx healthy) |
| | Flashing Red | *(all LEDs flashing red)* software exception / bad options file |
| **TX** | Solid Yellow | Transmitter **disabled** |
| | **Solid Green** | Transmitter **enabled** |
| | Flashing Red | *(all LEDs flashing red)* software exception / bad options file |
| **NET** | Flashing Yellow | Demodulator **not** locked on downstream |
| | Solid Yellow | Demodulator locked on downstream |
| | **2-Second Flashing Green** | Demod locked, **network acquisition in progress** |
| | **1-Second Flashing Green** | Demod + NCR locked, **network acquisition in progress** |
| | **Solid Green** | **Network acquired, link layer up (In Network)** |
| | Flashing Red | *(all LEDs flashing red)* software exception / bad options file |
| **POWER** | Off | No / low DC power input |
| | **Solid Green** | Acceptable DC power detected |
| | **Solid Yellow** | **BUC/LNB power fail** |
| | Flashing Red | *(all LEDs flashing red)* software exception / bad options file |

**LED → what to do (routes into §4):**
- **RX not Solid Green (Off / Yellow / flashing yellow):** downstream demod/NCR not locked → Rx-side problem. Scope first (many remotes? → ESCALATE), then weather/fade, LNB power, Rx coax/connector, pointing/blockage, wrong Rx freq. → **§4A / §4B (Rx chain).**
- **NET flashing green (1-sec or 2-sec):** **acquisition in progress — this is the normal transient while joining.** If it never goes **Solid Green**, the modem is stuck acquiring → Tx chain / Tx power / pointing / 10 MHz reference / recent options-file push. → **§4A steps 3–5.** (NET **flashing yellow** instead = demod not locked = Rx problem, above.)
- **TX Solid Yellow (won't enable):** transmitter disabled — check enable/config state (not an RF fault by itself). TX Solid Green = transmitting.
- **POWER Solid Yellow = BUC/LNB power fail:** check Tx/Rx coax for short or water ingress, and the BUC/LNB. **POWER Off** = site/DC power (verify PSU and power connection). POWER Solid Green = power OK.
- **ALL LEDs flashing red = software exception or bad options file.** This is the real meaning of "red" on these units (there is **no** solid-red per-LED fault). → **ESCALATE** for an L2 options-file / software reload; L1 does a reset only if SOP permits.

**Equipment facts (iDX X1 ISM, Table 6-1 + Ch. 3):** default management IP **`192.168.0.1`**; rear panel has **Transmit / Receive coax** and a **10/100 LAN (RJ-45)**, **24 VDC** input. Troubleshooting events: *Router not functioning* → compare LEDs to the table above; *POWER OFF* → verify/test power to the module; *POWER ON but not functioning* → test power, check other LEDs; *cannot reach Web iSite* → likely bad options file/settings/software (L2). *Lost IP* → a Level-1 (Recovery) reset returns the router to the default `192.168.0.1`. **L1 does not reload options files or force resets outside SOP.**

## 3. Key metrics & how to read them
> **Always compare against the remote's clear-sky baseline**, not just absolute numbers. A 3–5 dB drop from clear-sky C/N during rain is fade; a drop with clear weather is a hardware/pointing/interference problem.

| Metric (iMonitor) | Typical/healthy* | Investigate | Notes |
|---|---|---|---|
| **Downstream SNR (C/N)** | ~8–15 dB (link-dependent) | > ~2–3 dB below clear-sky baseline | Remote Rx quality (space→remote) |
| **Upstream C/N** (at hub) | At/above **UCP target** (often ~9 dB) | Below UCP target, or erratic | Hub Rx of this remote's burst |
| **Tx Power** (dBm) | Stable, below Max Power | Pinned at **Max Power** | Ran out of headroom → fade, BUC, or pointing |
| **Rx composite power** | Stable | Drifting/low | Rx chain / LNB / cable |
| **Latency tab** | Consistent RTT | "request timed out" entries | Loss/instability |
| **Inroute slot allocation** | Adequate under load | Chronically starved (%) | Congestion vs fault |
| **TDM lock lost count** | Not incrementing | Incrementing | Rx losing outroute lock |
| **Temp** | Nominal | High | Environmental/hardware |

\* Ranges are illustrative — **<FILL: per-network clear-sky C/N and UCP targets>**.

## 4. Symptom → first-level flow

### A. Single remote Out of Network
1. **Scope it first:** are other remotes on the **same inroute/outroute/line card/beam** also down?
   - **Many down together → hub/space-segment side → ESCALATE immediately** (don't chase one site). Note the common element (line card / inroute group / PP / beam).
   - Only this one → continue.
2. **Weather:** check site weather. Heavy rain + Tx pinned at Max Power + C/N collapsed = **rain fade** → monitor for recovery; escalate only if it doesn't recover after the cell passes.
3. **Reachability:** can you reach the modem on its LAN side (ping/console)?
   - Modem dead / no power → **site power** or hardware. Dispatch/advise power check.
   - Modem up, stuck in **Acquisition** → Tx/pointing/reference:
     - Confirm no recent physical change (dish knocked, cable disconnected).
     - If **NET is Solid Green (In Network)** but the user still has no traffic → it's a **LAN/CPE/routing** problem behind the modem, not the satellite link.
4. **Recent change:** did a config push / software upgrade / reset precede this? → correlate; may need L2 to roll back. **ESCALATE** with details.
5. Modem reachable, points fine, clear sky, still not acquiring → **ESCALATE** (option file / hub mapping / RF). Collect §6.

### B. Remote In Network but poor performance / drops
1. Check **Downstream C/N** and **Upstream C/N** vs baseline.
   - Both low in bad weather → fade.
   - Low with clear sky → **Rx chain** (LNB/cable) if Downstream low; **Tx chain** (BUC/cable/pointing) if Upstream low / Tx pinned at Max.
2. **Tx Power at Max Power** persistently → insufficient uplink headroom: pointing drift, BUC degradation, cable loss, or under-sized link. Flag for RF review.
3. **Latency tab shows timeouts / inroute starvation** → congestion vs loss:
   - Many remotes affected → hub/inroute capacity → **ESCALATE**.
   - One remote, with low upstream C/N → RF, per above.
4. **TDM lock lost incrementing** → Rx losing outroute: pointing/LNB/interference → RF review.
5. Link metrics all nominal but user complains → **LAN/CPE/application side**, not the satellite link.

### C. Intermittent (flapping In/Out of Network)
- Correlate with weather (fade at fade margin), **craft/station-keeping box** motion (C/N dips on a daily cycle), BUC thermal, or power instability. Capture a C/N + Tx-power + Rx-power timeline across a flap (before/during/after) for L2. **ESCALATE** with the capture.

### D. Many remotes degraded at once
- **Do not troubleshoot individually.** Identify the shared element — line card, Protocol Processor, inroute group, outroute, teleport RF, satellite/beam, or a recent NMS change — and **ESCALATE to hub/teleport ops** with the list of affected remotes and the common factor.

## 5. Common root causes (ranked for iDirect single-remote)
1. **Rain fade** (transient; watch for self-recovery).
2. **Pointing drift / dish disturbed** (Tx pinned at max, both C/N down in clear sky).
3. **Tx chain**: BUC fault, cable loss/water, missing/weak **10 MHz reference**, wrong Tx IF range.
4. **Rx chain**: LNB fault, cable, Rx IF/LO mismatch, acquisition range too narrow.
5. **Site power** (modem/BUC/LNB power).
6. **LAN/CPE/routing** (modem **In Network** — NET Solid Green — but no user traffic; the problem is behind the modem, not the satellite link).
7. **Interference** (adjacent satellite/terrestrial — upstream C/N erratic without weather).
8. **Config/software change** on hub or remote.

## 6. Before escalating — collect this
- Remote name/DID + which **inroute group / outroute / line card / beam / PP** it's on. <FILL: naming convention>
- Current **state** + how long + whether flapping.
- **Downstream C/N, Upstream C/N, Tx Power (vs Max), Rx power, Temp** — current + a short timeline across the event (iMonitor export).
- Latency tab sample (timeouts), inroute allocation %.
- Scope: list of **other affected remotes** sharing an element (critical for hub-side triage).
- Site weather; any recent physical/config/software change.
- **Escalate to:** <FILL: internal L2 / hub-teleport ops / vendor>

## 7. L1 boundaries (do NOT do)
- No **iBuilder** config edits (option files, power, C/N targets, mapping).
- No **line-card / Protocol Processor / chassis / reference-clock** actions.
- No RF commissioning, cross-pol, or antenna alignment.
- No bulk remote resets. (A single remote soft reset only if your SOP permits — <FILL: SOP ref>.)
