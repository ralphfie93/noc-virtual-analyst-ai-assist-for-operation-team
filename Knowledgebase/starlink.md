---
system: Starlink
platform: LEO VSAT (SpaceX)
audience: NOC L1 / first-level
scope: First-level status checks, triage, and escalation. NOT for RF alignment, hub/gateway work, or account provisioning.
last_reviewed: 2026-11-12
owner: <FILL: D&A team / Rafie>
---

# Starlink — NOC First-Level Troubleshooting

> **Agent guidance:** Use this file for any Starlink terminal/service-line issue. Always identify the *terminal state* and *active alerts* before guessing a cause. If a step requires hub/gateway access, RF alignment, or account/billing changes, mark it **ESCALATE** — L1 does not perform those.

## 1. System overview (context only)
- **Topology:** LEO constellation. User terminal ("Dishy") ⇄ satellites ⇄ ground station (PoP/gateway) ⇄ internet. No manual pointing — phased-array auto-aligns; it needs an unobstructed view of a *large* patch of sky, not a fixed azimuth.
- **Latency:** ~25–60 ms typical (vs ~500–600 ms for GEO). High/erratic latency is abnormal.
- **Hardware variants:** Standard (rectangular Gen2/Gen3), Flat High Performance, Enterprise. Commercial deployments use Business/Enterprise service lines managed via the Starlink portal/API.
- **Key difference vs GEO (iDirect/HNS):** most degradation is **obstruction, thermal, power/cable, or a satellite-handover/scheduling** issue — not rain fade or pointing.

## 2. Where to check status
| Access method | What it gives | Notes |
|---|---|---|
| Starlink app → **Statistics / Visibility / Debug Data** | State, uptime, ping/latency, throughput, obstruction map, alerts | Must be on the Starlink LAN |
| Browser `http://192.168.100.1` (aka `dishy.starlink.com`) | Same dish stats, no app needed | Works even with no internet |
| **gRPC** `192.168.100.1:9200` (`get_status`, `get_history`, `get_device_info`) | Machine-readable status/history; obstruction + alert flags | This is what your monitoring pipeline should poll |
| **Starlink Enterprise portal / API** | Service-line state (active/suspended), account, fleet view across many terminals | <FILL: portal URL + who has access> |
| Starlink **status / outage page** | Known network-wide outages | Check this before deep-diving a single-site "offline" |

> If using **bypass mode** behind your own router, the dish is still at `192.168.100.1` *only if that subnet is routable*. If unreachable, that's a LAN/routing problem, not a dish fault.

## 2A. WiFi / SSID connectivity & router lights
> **This is a LAN/router-layer problem, separate from the satellite link.** Being able to *join the WiFi* and *having internet* are two different things. Use this section for "can't see / can't join the **STARLINK** WiFi"; use §4 for "joined WiFi but no internet."
>
> **<FILL: Starlink router model (Gen2 WiFi router / Gen3 router) + is bypass mode the standard deployment behind your own router?>**

**Router light states** — *Source: Starlink **Gen 3 Router Setup Guide (Standard)**, "Can't Get Online?"*
| Router light | Meaning |
|---|---|
| **Flashing White** | Trying to connect. If it can't reach the internet within ~20 min, the light turns red |
| **Solid White** | Connected to the internet (the light turns off after ~1 hour) |
| **No Light** | **No power** to the router |
| **Red** | **Not connected to the internet** |
| **Violet** | Router is in **bypass mode** — Starlink-router WiFi is **disabled** (light turns off after ~1 hour). **Factory reset required to exit bypass mode.** |

Mesh line color in the app: **White** = connected · **Yellow** = poor signal · **Red** = disconnected.

**Can't see / can't join the `STARLINK` WiFi network:**
Default WiFi network name (SSID) is **`STARLINK`**. If it's missing or won't accept you:
1. **Bypass mode (most common cause of a missing SSID).** In bypass mode the Starlink router's WiFi is **fully disabled**, so no `STARLINK` SSID appears (**violet** light).
   - If the operator **intentionally runs bypass** behind its own router, this is **expected** → connect to **your** router SSID, not `STARLINK`.
   - If Starlink-router WiFi is actually needed: disable bypass in the Starlink app (Router settings) **+ power cycle**; if the app can't reach the router, a **factory reset** is required (this **erases** the custom SSID, password, and all settings).
2. **Router power / light.** **No Light** → check the PoE/power supply and the dish/router cable at both ends (bent/pinched/water ingress).
3. **Renamed / hidden SSID** from a prior setup → look for the **custom** name, not `STARLINK`; the default is on the router label/QR.
4. **Wrong password** after setup → a **factory reset** restores the defaults (label/QR).
5. After a **factory reset**, the default network can take **several minutes** to reappear — don't declare failure immediately.
6. **See the SSID but keeps dropping / weak** → 2.4 vs 5 GHz, distance/obstacles, or too many clients (LAN/Wi-Fi, not the satellite link).

**Official "Can't Get Online?" steps (Gen 3 guide):** open the Starlink app → check for **alerts / outages / obstructions**; check the **router light** (table above); make sure everything is securely plugged in and there's **no cable/hardware damage**; **power cycle** (unplug/replug); **factory reset** (press the reset button with a paper clip); if none work, contact **Starlink Customer Support** in the app or at starlink.com.

> **Routing rule:** joined the WiFi but **no internet / Red light** → that's the **dish/link** path → go to **§4A**, not this section.

> **L1 boundary:** a **factory reset wipes all router config** (SSID/password/bypass). Confirm against SOP / the account owner before resetting a managed terminal. **<FILL: SOP ref>**

## 3. Key health indicators
| Indicator (gRPC field) | Healthy | Investigate | Meaning |
|---|---|---|---|
| `state` | `CONNECTED` | `BOOTING` > 2–3 min, `SEARCHING`, `OFFLINE`, `STOWED` | Dish link state |
| `uptime` | Rising steadily | Resets to ~0 unexpectedly | Low uptime = recent reboot (power/firmware/thermal) |
| `pop_ping_drop_rate` | ~0 | Sustained > a few % | Fraction of pings lost to the PoP |
| `pop_ping_latency_ms` | ~25–60 ms | Sustained > ~100 ms or spiky | Round-trip to PoP |
| `currently_obstructed` | `False` | `True` (sustained) | Sky blockage right now |
| `fraction_obstructed` / obstruction % | Near 0% | > ~1% and correlating with drops | % of sky view blocked over the window |
| `downlink/uplink_throughput_bps` | Matches plan under load | Near 0 while "connected" | Data plane vs control plane |
| `alerts` (see §5) | empty | any set | Dish-reported fault flags |

> **Do NOT rely on a numeric `SNR`.** SpaceX deprecated the numeric SNR field; it is not a reliable signal-quality readout. Use `snr_persistently_low` **alert**, obstruction %, ping drop, and latency instead.

> **Baseline it:** record each site's clear-view obstruction % and typical latency once stable. Triage on *deviation from that baseline*, not absolutes.

## 4. Symptom → first-level flow

### A. Terminal OFFLINE / no connectivity
1. Check **Starlink status/outage page** and portal — rule out a known PoP/regional outage. → if outage: **inform, wait, no site action.**
2. Confirm **service line is active** in the portal (not suspended/expired). → if suspended: **ESCALATE** to account owner. <FILL: contact>
3. Reach the dish: `ping 192.168.100.1`. Unreachable → LAN/cabling/power issue, not the dish link:
   - Verify power at the PoE/power supply; check the dish cable at both ends (bent/pinched/water ingress).
   - Verify router LAN and that `192.168.100.1` is routable (bypass mode).
4. Dish reachable, `state != CONNECTED`:
   - `SEARCHING`/`BOOTING` persistently → check **obstruction** (§B) and **thermal** alerts (§5); power-cycle once.
   - Reboot (app → reboot, or power cycle). Re-check after ~2–3 min.
5. Still offline after reboot + cabling/power verified + service active → **ESCALATE** (hardware RMA path / Starlink support). Collect §7 bundle.

### B. Intermittent drops / packet loss
1. Check `currently_obstructed` + obstruction map + `fraction_obstructed`. Sustained obstruction → **physical siting issue**: dispatch/advise relocation or clear the blockage. L1 does not re-site remotely.
2. Check `alerts` for `thermal_throttle`, `slow_ethernet_speeds`, `motors_stuck`.
3. Correlate drop timing with uptime resets → recurring reboots (power instability / firmware).
4. No obstruction, no alerts, drops persist → likely network/scheduling side → **ESCALATE** with `get_history` capture.

### C. Slow throughput (connected, low Mbps)
1. Confirm `state=CONNECTED`, low drop, normal latency — if so the link is fine; look at **LAN/CPE/plan**:
   - Plan/priority tier limits or deprioritization at congestion (check plan + usage in portal).
   - Local: duplex/cable (`slow_ethernet_speeds` alert), CPE, Wi-Fi vs wired.
2. High drop/latency alongside slow throughput → treat as §B, not a throughput problem.

### D. High / erratic latency
- Spikes with obstruction → §B. Steady high latency with no obstruction/alerts → PoP/routing → **ESCALATE** with history capture.

### E. Thermal
- Alert `thermal_throttle` (reduced performance) or `thermal_shutdown` (offline until cooled). Check mounting/ventilation/direct heat. Recurring thermal shutdown → **ESCALATE** (hardware).

## 5. Alert flags (gRPC `alert_detail`) — what L1 should do
| Alert | Meaning | L1 action |
|---|---|---|
| `obstructed` / high obstruction | Sky view blocked | Siting/clearance (§B) |
| `thermal_throttle` | Overheating, throttled | Ventilation/placement; monitor |
| `thermal_shutdown` | Overheating, offline | Cool/relocate; recurring → ESCALATE |
| `mast_not_near_vertical` / `dish_not_pointing` | Dish not settled / knocked | Check mount; let it re-align |
| `motors_stuck` | Actuator fault | ESCALATE (hardware) |
| `slow_ethernet_speeds` | Cable/port negotiated low | Reseat/replace dish cable; check port |
| `roaming` / `unexpected_location` | Terminal outside its service area/geofence | Confirm location vs service line; ESCALATE to account if fixed-site |
| `software_update_reboot_pending` | Firmware updating | Expect brief reboot; don't chase a transient drop |
| `snr_persistently_low` | Link quality poor | Check obstruction/siting first |

> Exact alert names can shift across firmware — treat unknown flags as "degraded, capture and escalate."

## 6. Common root causes (ranked for Starlink)
1. **Obstruction** (trees/structures/new construction, snow/debris on dish).
2. **Power/cable** — PoE supply, pinched or water-ingressed dish cable, loose RJ45.
3. **Thermal** (enclosed mount, direct sun, no airflow).
4. **Firmware reboot** (transient, self-resolving).
5. **Service-line state** (suspended/expired/geofence).
6. **Network/PoP-side or regional outage** (confirm on status page before site work).
7. **WiFi/SSID access** (§2A): **bypass mode disabling the `STARLINK` SSID**, router with **No Light** (power/cable), renamed/hidden SSID, or wrong password — a router/LAN-layer issue, not the satellite link.

## 7. Before escalating — collect this
- Terminal ID / service line ID / site. <FILL: naming convention>
- `get_status` + `get_device_info` (firmware/hardware) output.
- `get_history` capture spanning the problem window (ping drop, obstruction, latency).
- Obstruction map screenshot + obstruction %.
- Active alerts.
- Uptime (to show reboots), and whether a reboot was already tried.
- Service-line state from portal; outage-page status.
- **Escalate to:** <FILL: internal L2 / Starlink Enterprise support channel / account owner>

## 8. L1 boundaries (do NOT do)
- No RF/pointing alignment (auto; physical re-siting is a dispatch, not a remote fix).
- No account/billing/service-line changes.
- No firmware forcing.
- No hub/gateway/PoP actions.
