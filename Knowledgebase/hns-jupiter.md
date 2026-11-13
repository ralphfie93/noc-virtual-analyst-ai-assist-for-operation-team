---
system: HNS (Hughes Network Systems) — JUPITER System
platform: GEO (typically Ka-band), Hughes gateway + Jupiter terminals (HT20xx/HT series)
audience: NOC L1 / first-level
scope: First-level terminal status checks via the System Control Center + gateway/NMS state read. NOT for commissioning, beam/gateway reconfig, or RF alignment.
last_reviewed: 2026-11-12
owner: <FILL: D&A team / Rafie>
---

# HNS JUPITER — NOC First-Level Troubleshooting

> **Agent guidance:** For HNS, read the terminal's **System Status page** and its **State Code** first, then **SQF** (receive signal). As with iDirect, scope it: one terminal vs many on the same **beam/gateway** — many = gateway/space-segment side → **ESCALATE**. JUPITER is typically **Ka-band**, so **rain fade is a prominent and usually transient** cause. L1 reads status and does safe checks; L1 does not commission, re-point, or touch the gateway.

## 1. System overview (context only)
- **Topology:** JUPITER terminal (indoor modem + outdoor radio/transmitter + antenna) ⇄ GEO satellite **spot beam** ⇄ **Gateway** ⇄ core/NMS ⇄ internet.
- **Band:** typically **Ka** (spot-beam). Ka is more rain-sensitive than Ku/C — expect weather-correlated fades.
- **Terminal-side tool — System Control Center (SCC):**
  - `http://192.168.0.1` (or `http://systemcontrolcenter.com`); some models expose Wi-Fi config on a different IP (e.g. `192.168.42.1`). **<FILL: terminal model + exact SCC IP>**
  - Pages: **System Status**, **Reception Information**, **Transmission Information**, **Terminal Status**, **System Information**, Connectivity Test.
- **Gateway/NMS side:** terminal state, beam, data allowance, provisioning. **<FILL: Hughes NMS/portal + access policy>**

## 2. The two numbers to read first
### a) State Code (SCC → System Status)
State codes indicate what stage/fault the terminal is in. **Exact numeric codes vary by terminal model and software — cross-check against the Hughes manual for your model / your NMS.** By category:

| Code family (verify exact) | Category | Typical meaning | L1 reading |
|---|---|---|---|
| ~11.x | **Antenna pointing** | Pointing/alignment error | Dish disturbed/misaligned → RF (ESCALATE/dispatch) |
| ~12.x | **Signal degraded** | Rx degraded, often **rain fade** | Check weather + SQF; usually transient |
| ~13.x | **Transmitter / radio** | Tx/radio (ODU) fault | Tx chain / radio / cable → RF review |
| ~14.x | **No devices connected** | Nothing on LAN behind modem | LAN/CPE side, not the satellite link |
| ~21.x | **Ethernet** | LAN cable unplugged/port | Reseat/replace cable |
| ~26–35 | **Various / commissioning** | Probing, registration, acquisition faults | Often commissioning-time; if live, capture + ESCALATE |
| ~30.x | **Overheating** | Thermal | Ventilation/placement |
| `OK` / no code | Nominal | — | Look elsewhere (CPE/plan/app) |

> Also note the **Diagnostic Code** shown on the SCC when present — include it verbatim when escalating.

### b) SQF — Signal Quality Factor (SCC → System Status: "Satellite Receive Signal Strength")
- SQF is the receive-quality metric (not dB C/N). **Higher is better.**
- **SQF of 0 or 1 → effectively no beacon / no usable receive signal** → treat as down-link: pointing, radio, cable, blockage, or a beam/gateway outage.
- SQF **jumping around** → unstable receive → fade, pointing drift, partial blockage, or interference.
- **Record each terminal's clear-sky SQF** so you can judge "degraded" vs normal. **<FILL: per-site SQF baselines / thresholds>**

## 3. Other indicators
| Where | Indicator | Investigate |
|---|---|---|
| Reception Information | Receive signal strength (SQF), **Frames with no signal**, TIPS received, red-flagged params | Frames-with-no-signal rising, red flags present |
| Transmission Information | Transmit status/power | Tx not keying / errors |
| Terminal Status / System Information | Software download status, registration/commissioning state, SAN | Stuck download, un-registered |
| Gateway/NMS | Beam, terminal online/offline, **data allowance / FAP** state | Suspended / allowance-exhausted throttling |

## 3A. Terminal front-panel LEDs (equipment-level read)
> **JUPITER/HT terminals are managed through the SCC — the front-panel LEDs are only a coarse indicator. The authoritative read is the SCC `State Code` + `SQF` (§2), not the lights.**
>
> **No model-specific LED table is reproduced here** because an authoritative HT2000/HT2010 LED table was not available at write time. The rows below are the **documented Hughes model-family convention** only — **verify against your terminal model's user guide before treating any LED as fact. <FILL: Hughes terminal model (e.g. HT2000/HT2010/HT1100) + its LED table>.**

| Indicator (convention) | Normal | What it tells you |
|---|---|---|
| **Power** | On | Modem has power. Off → power/PSU/outlet. |
| **System** | On (SCC System Status **green** = within normal parameters) | Overall terminal health; not green → read the SCC State Code. |
| **Receive** | Blinks when **receiving** frames | Not active / no receive → cross-check **SQF 0/1** → §4A (weather/pointing/radio/cable/blockage or beam outage). |
| **Transmit** | Blinks when **sending** frames | Never keys → Tx/radio (ODU) side → maps to **State Code ~13.x** family → RF review. |
| **LAN** | Link/activity on Ethernet | No link → reseat/replace LAN cable; check CPE (≈ State Code 14.x/21.x). |

**LED → what to do:** confirm the symptom on the LEDs, then **always open the SCC and read `State Code` + `SQF`** before acting — the LED is the hint, the SCC is the diagnosis. Reaching the SCC itself (`192.168.0.1`) fails → modem **power** or LAN cabling.

## 4. Symptom → first-level flow

### A. Terminal offline / "no internet"
1. **Scope:** are multiple terminals on the **same beam/gateway** down? → **gateway/space-segment side → ESCALATE** with the affected list + common beam. Don't chase one site.
2. Read **SCC System Status → State Code + SQF**:
   - **SQF 0/1** → no receive. Check site weather (heavy rain → likely fade, watch for recovery). If clear sky → pointing/radio/cable/blockage → RF review.
   - State code in **pointing (11.x)** or **transmitter (13.x)** family → RF/radio → dispatch/ESCALATE.
   - State code **14.x "no devices" / 21.x ethernet** → LAN/CPE side: reseat/replace LAN cable, check CPE. Satellite link may be fine.
3. **Reach the SCC** (`192.168.0.1`). If the SCC itself is unreachable → modem **power** or LAN cabling; verify power and Ethernet.
4. **Data allowance / provisioning:** check NMS — allowance exhausted (FAP) causes severe throttling that *looks* like an outage; suspended/unprovisioned looks like offline. <FILL: where to check> → account action = **ESCALATE**.
5. Power-cycle the modem once; recheck State Code/SQF after it re-acquires (allow a few minutes for software/registration).
6. Clear sky, SCC reachable, SQF still 0/1 after reboot → **ESCALATE** (RF/radio/gateway). Collect §6.

### B. Slow / intermittent
1. **Rain fade (Ka):** weather-correlated SQF dips + degraded (12.x) → transient; confirm recovery after the cell passes.
2. **SQF unstable in clear sky** → pointing drift / partial blockage / radio → RF review.
3. **Frames with no signal rising** → receive instability → same as above.
4. **Allowance/FAP throttling:** steady slow speeds, good SQF, no state code → check data allowance in NMS.
5. Good SQF + no state code + no allowance issue → **LAN/CPE/application** side.

### C. Stuck in commissioning/registration (new or re-installed site)
- Probing/registration/DAPT/software-download states (26–35 family): usually an install-phase issue (coords, SAN/PIN, pointing/peaking for best SQF, software download). L1 confirms symptoms; **re-commissioning/pointing is RF/field → ESCALATE/dispatch.**

### D. Many terminals on one beam/gateway
- **ESCALATE to gateway ops.** Capture: affected terminals, common beam/gateway, start time, whether weather-wide or clear. Do not individually troubleshoot.

## 5. Common root causes (ranked for JUPITER single-terminal)
1. **Rain fade (Ka)** — transient, weather-correlated; watch for self-recovery.
2. **Pointing drift / dish disturbed** (SQF low/unstable in clear sky, 11.x).
3. **Radio/transmitter (ODU) or IFL cable** (13.x; Tx faults, water-ingressed/damaged cable).
4. **LAN/CPE** (14.x/21.x — link up, user side broken).
5. **Data allowance / FAP / provisioning** (NMS-side; looks like outage or slowness).
6. **Thermal** (30.x).
7. **Beam/gateway or space-segment outage** (many terminals — ESCALATE).
8. **Commissioning not completed** (new sites).

## 6. Before escalating — collect this
- Terminal SAN / site ID + **beam + gateway**. <FILL: naming convention>
- **State Code** (exact) + **Diagnostic Code** (verbatim) from SCC.
- **SQF** now vs clear-sky baseline; SQF stability.
- Reception info (frames-with-no-signal, red flags), Transmission status.
- Software/registration state; data-allowance state from NMS.
- Site weather; recent physical/power/config change; reboot already tried?
- Scope: other affected terminals on the same beam/gateway.
- **Escalate to:** <FILL: internal L2 / gateway ops / Hughes vendor contact — e.g. see /people/fauzi>

## 7. L1 boundaries (do NOT do)
- No commissioning, antenna pointing/peaking, or cross-pol.
- No radio/ODU or IFL field work (dispatch).
- No gateway/beam/provisioning config changes.
- No allowance/account/billing changes (ESCALATE).
