# Terminal Performance Database — Schema & Metadata

**File:** `terminal_performance.db` (SQLite 3) · **Engine:** SQLite — open with Python's built-in `sqlite3`, no server needed.

This is a **mock** telemetry database for the NOC L1 troubleshooting assistant. It carries one week of
per-minute performance data for three satellite access technologies, plus the hub/space-segment
infrastructure each depends on. The metrics and thresholds mirror the `Knowledgebase/` runbooks
(`idirect-hub-terminal.md`, `hns-jupiter.md`, `starlink.md`) so the assistant can read live-looking
telemetry and apply the same triage logic.

> ⚠️ **Mock data.** Values are synthetic (seeded, reproducible). Site names, component IDs and incident
> windows are invented for training/demo. Do not treat as real operations data.

---

## 1. Fast facts

| Property | Value |
|---|---|
| Database | 1 SQLite file, **4 tables** |
| Time window | **2026-11-05 00:00:00 → 2026-11-11 23:59:00 MYT** (UTC+8), inclusive of both ends at 1-min steps |
| Sampling | every **1 minute** → 10,080 samples per terminal |
| Fleet | **10 terminals per system** (30 total) + **27 hub components** |
| Row counts | `idirect_performance` 100,800 · `jupiter_performance` 100,800 · `starlink_performance` 100,800 · `hub_components` 272,160 |
| Timestamp format | TEXT `'YYYY-MM-DD HH:MM:00'`, **MYT (UTC+8)**, sorts lexicographically |
| Latency sentinel | `latency_rtt_ms` / `pop_ping_latency_ms` = **`9999.0` means "request timed out"** (no response) |
| Regenerate | `python generate_mock_db.py` (seed=42, fully reproducible; overwrites the `.db`) |

**Tables & what each is for**

| Table | System | Grain | Join key into `hub_components` |
|---|---|---|---|
| `idirect_performance` | iDirect (GEO TDMA/SCPC, Ku) | 1 row / remote / minute | `line_card` (also `inroute_group`, `outroute`) |
| `jupiter_performance` | HNS JUPITER (GEO Ka HTS) | 1 row / terminal / minute | `gateway`, `beam`, `ipgw` |
| `starlink_performance` | Starlink (LEO) | 1 row / dish / minute | `pop`, `ground_station` |
| `hub_components` | all three (partitioned by `system`) | 1 row / component / minute | — |

---

## 2. Each technology has its OWN hub (they share nothing)

Component IDs are namespaced `IDR-*` / `JUP-*` / `STAR-*`, and `hub_components.system` partitions the table.
A terminal only ever joins to a component of its **own** system.

**iDirect** (ST Engineering Evolution — hub-centric GEO):
`teleport` → `hub_chassis` → `protocol_processor` (1+1) → `line_card` → `outroute` (DVB-S2/S2X out) + `inroute_group` (D-TDMA in)
```
IDR-TELEPORT-KUL
└─ IDR-CHASSIS-01
   ├─ IDR-PP-01 ──┬─ IDR-LC-01 ─ IDR-OUT-01 / IDR-INR-A  (R01,R02,R03)
   │              └─ IDR-LC-02 ─ IDR-OUT-02 / IDR-INR-B  (R04,R05,R06,R07)
   └─ IDR-PP-02 ─── IDR-LC-03 ─ IDR-OUT-03 / IDR-INR-C  (R08,R09,R10)
```

**JUPITER** (Hughes — gateway-centric Ka HTS):
`satellite` / `nap` → `gateway` (RFT) → `spot_beam` + `ipgw` (T-IPGW per beam)
```
JUP-SAT-KA   JUP-NAP-01
                 ├─ JUP-GW-KUL ─┬─ JUP-BEAM-N1 + JUP-IPGW-N1  (T01..T04)
                 │              └─ JUP-BEAM-N2 + JUP-IPGW-N2  (T05..T07)
                 └─ JUP-GW-KCH ─── JUP-BEAM-S1 + JUP-IPGW-S1  (T08..T10)
```

**Starlink** (LEO — **no beams, no line cards, no fixed per-site hub**):
dynamic `satellite` → `ground_station` (IP-transparent relay) → `pop` (home PoP) → internet
```
STAR-POP-SIN ─ STAR-GS-SIN   (D01..D05)
STAR-POP-JHB ─ STAR-GS-JHB   (D06..D10)
```

---

## 3. Column dictionary

Legend: **unit** · **healthy** (nominal range) · **investigate** (triage trigger, per KB) · meaning.
"static" = constant per terminal across the week (baseline/config).

### 3.1 `idirect_performance` — mirrors `idirect-hub-terminal.md` §3

| Column | Type | Unit · Healthy · Investigate | Meaning |
|---|---|---|---|
| `reading_id` | INTEGER PK | — | Row id |
| `timestamp_myt` | TEXT | MYT | Sample time |
| `remote_id` | TEXT | — | Remote id (`IDR-R01`..`R10`) |
| `remote_name` | TEXT | — | Site name |
| `beam` | TEXT | — | Ku space-segment beam label |
| `line_card` | TEXT | — | **FK → `hub_components` (`IDR-LC-*`)** |
| `inroute_group` | TEXT | — | FK → `IDR-INR-*` (shared TDMA inbound) |
| `outroute` | TEXT | — | FK → `IDR-OUT-*` (DVB-S2 outbound) |
| `network_state` | TEXT | enum | `In Network` / `Acquisition` / `Out of Network` / `Fault` — **the primary signal** |
| `modem_led` | TEXT | enum | `green` (ok) / `flashing_green` (acq/reboot) / `amber` (up, no traffic → LAN/CPE) / `red` (fault) |
| `downstream_snr_db` | REAL | dB · ~8–15 · **>2–3 dB below `clearsky_snr_db`** | Remote Rx C/N (space→remote). NULL when down |
| `clearsky_snr_db` | REAL | dB · static | Clear-sky baseline — **compare against this, not absolutes** |
| `upstream_cn_db` | REAL | dB · ≥ `ucp_target_db` · below target/erratic | Hub Rx of this remote's burst |
| `ucp_target_db` | REAL | dB · static (9.0) | Uplink power-control target |
| `tx_power_dbm` | REAL | dBm · below max · **pinned at `tx_max_power_dbm`** | Tx power. Pinned = no headroom (fade/BUC/pointing) |
| `tx_max_power_dbm` | REAL | dBm · static | Max Tx power |
| `rx_power_dbm` | REAL | dBm · stable · drifting/low | Rx composite power (Rx chain/LNB/cable) |
| `latency_rtt_ms` | REAL | ms · ~520–600 (GEO) · timeouts | RTT. **`9999` = timeout** |
| `inroute_alloc_pct` | REAL | % · adequate · chronically starved | TDMA slot allocation granted (congestion vs fault) |
| `tdm_lock_lost_count` | INTEGER | count · not incrementing · **incrementing** | Cumulative outroute-lock losses (Rx pointing/LNB/interference) |
| `temperature_c` | REAL | °C · nominal · high | Modem temperature |
| `downlink_throughput_mbps` | REAL | Mbps | Instantaneous downlink rate |
| `uplink_throughput_mbps` | REAL | Mbps | Instantaneous uplink rate |
| `plan_cir_mbps` | REAL | Mbps · static | Subscribed/committed downlink rate (basis for utilization) |
| `utilization_pct` | REAL | % | `downlink / plan_cir × 100` (can exceed 100 on burst) |
| `data_volume_mb` | REAL | MB | (down+up) volume in this 1-min interval → `SUM()` = total traffic |

### 3.2 `jupiter_performance` — mirrors `hns-jupiter.md` §2–3

| Column | Type | Unit · Healthy · Investigate | Meaning |
|---|---|---|---|
| `reading_id` | INTEGER PK | — | Row id |
| `timestamp_myt` | TEXT | MYT | Sample time |
| `terminal_san` | TEXT | — | Terminal SAN id (`JUP-T01`..`T10`) |
| `site_name` | TEXT | — | Site name |
| `beam` | TEXT | — | **FK → `JUP-BEAM-*`** (spot beam) |
| `ipgw` | TEXT | — | **FK → `JUP-IPGW-*`** (T-IPGW per beam) |
| `gateway` | TEXT | — | **FK → `JUP-GW-*`** |
| `state_code` | TEXT | enum | `OK` / `11.x` pointing / `12.x` degraded / `13.x` Tx-radio / `14.x` no-devices / `21.x` ethernet / `30.x` overheating. **Codes are categories — verify exact numerics against the Hughes manual** |
| `state_category` | TEXT | enum | Human label for `state_code` |
| `diagnostic_code` | TEXT | nullable | SCC diagnostic text (verbatim when escalating) |
| `sqf` | INTEGER | — · ~ baseline · **0/1 = no signal**, unstable = fade/pointing | Signal Quality Factor (higher=better; **not dB**) |
| `clearsky_sqf` | INTEGER | static | Clear-sky SQF baseline — triage on deviation |
| `frames_with_no_signal` | INTEGER | count · not rising · rising | Cumulative receive-instability counter |
| `tx_status` | TEXT | enum | `Transmitting` / `Idle` / `Fault` |
| `tx_power_dbm` | REAL | dBm | Transmit power |
| `data_allowance_state` | TEXT | enum | `Normal` / `FAP_Throttled` (allowance exhausted → looks slow) / `Suspended` |
| `registration_state` | TEXT | enum | `Registered` / `Commissioning` / `Unregistered` |
| `latency_rtt_ms` | REAL | ms · ~560–640 (GEO) · timeouts | RTT. **`9999` = timeout** |
| `temperature_c` | REAL | °C · nominal · high (30.x) | Terminal/ODU temperature |
| `downlink_throughput_mbps` | REAL | Mbps | Instantaneous downlink |
| `uplink_throughput_mbps` | REAL | Mbps | Instantaneous uplink |
| `plan_cir_mbps` | REAL | Mbps · static | Subscribed rate |
| `utilization_pct` | REAL | % | `downlink / plan_cir × 100` |
| `data_volume_mb` | REAL | MB | (down+up) per-minute volume |

### 3.3 `starlink_performance` — mirrors `starlink.md` §3 + §5

> **No numeric SNR column** — SpaceX deprecated it. Triage on obstruction, ping drop, latency, and alerts.

| Column | Type | Unit · Healthy · Investigate | Meaning |
|---|---|---|---|
| `reading_id` | INTEGER PK | — | Row id |
| `timestamp_myt` | TEXT | MYT | Sample time |
| `terminal_id` | TEXT | — | Dish id (`STAR-D01`..`D10`) |
| `service_line_id` | TEXT | — | Service line id |
| `site_name` | TEXT | — | Site name |
| `ground_station` | TEXT | — | **FK → `STAR-GS-*`** |
| `pop` | TEXT | — | **FK → `STAR-POP-*`** (home PoP) |
| `hardware_variant` | TEXT | enum | `Standard Gen3` / `Flat High Performance` / `Enterprise` |
| `state` | TEXT | enum | `CONNECTED` / `BOOTING` / `SEARCHING` / `OFFLINE` / `STOWED` |
| `uptime_s` | INTEGER | s · rising · **resets to ~0** | Seconds since last reboot (reset = reboot/power/thermal) |
| `pop_ping_drop_rate` | REAL | 0–1 · ~0 · sustained >few % | Fraction of pings lost to the PoP |
| `pop_ping_latency_ms` | REAL | ms · ~25–60 · >~100/spiky | RTT to PoP. **`9999` = timeout** |
| `currently_obstructed` | INTEGER | 0/1 · 0 · 1 sustained | Sky blocked right now |
| `fraction_obstructed_pct` | REAL | % · ~0 · >~1% w/ drops | % of sky view blocked over window |
| `service_line_state` | TEXT | enum | `active` / `suspended` |
| `alerts` | TEXT | csv · '' | Comma-joined gRPC flags: `obstructed`, `thermal_throttle`, `slow_ethernet_speeds`, `snr_persistently_low`, `motors_stuck`, `software_update_reboot_pending` |
| `downlink_throughput_mbps` | REAL | Mbps | Instantaneous downlink |
| `uplink_throughput_mbps` | REAL | Mbps | Instantaneous uplink |
| `plan_cir_mbps` | REAL | Mbps · static | Plan rate |
| `utilization_pct` | REAL | % | `downlink / plan_cir × 100` |
| `data_volume_mb` | REAL | MB | (down+up) per-minute volume |

### 3.4 `hub_components` — per-system infrastructure health

| Column | Type | Notes |
|---|---|---|
| `reading_id` | INTEGER PK | Row id |
| `timestamp_myt` | TEXT | Sample time (MYT) |
| `component_id` | TEXT | **What terminals JOIN to** (`IDR-*` / `JUP-*` / `STAR-*`) |
| `component_type` | TEXT | iDirect: `teleport`,`hub_chassis`,`protocol_processor`,`line_card`,`outroute`,`inroute_group` · Jupiter: `satellite`,`nap`,`gateway`,`spot_beam`,`ipgw` · Starlink: `pop`,`ground_station` |
| `system` | TEXT | `iDirect` / `Jupiter` / `Starlink` (partition key) |
| `parent_component` | TEXT | Next element up the same system's chain (NULL at top) |
| `status` | TEXT | `OK` / `Degraded` / `Down` / `Maintenance` |
| `fault_code` | TEXT | e.g. `LC_HW_FAULT`, `PP_HIGH_CPU`, `IPGW_UNREACHABLE`, `POP_OUTAGE` (NULL when OK) |
| `fault_detail` | TEXT | Human description (NULL when OK) |
| `cpu_pct` | REAL | For processing elements (PP/IPGW/GW/NAP/PoP); NULL otherwise |
| `temperature_c` | REAL | Component temperature |
| `agg_throughput_mbps` | REAL | Approx aggregate carried (0 while Down) |
| `terminals_served` | INTEGER | Static count of terminals under this component |

---

## 4. Seeded-incident catalog (for validating the assistant)

All windows are MYT. "Signature" is what a correct diagnosis should surface. Everything outside these windows
is nominal with light noise + a business-hours diurnal traffic curve (weekends lighter).

### iDirect
| # | Terminal(s) | Window | Root cause | Signature |
|---|---|---|---|---|
| 1 | `IDR-R03` (Kuching) | Nov 7 14:00–16:00 | **Rain fade** (transient) | C/N ↓ ~7 dB vs clear-sky, `tx_power_dbm` **pinned at max**, latency timeouts, self-recovers |
| 2 | `IDR-R09` (Miri) | Nov 9 10:00 → end | **Pointing drift** (clear sky) | Both C/N ↓ ~4 dB, Tx pinned at max, **sustained**, no weather |
| 3 | `IDR-R01` (KL) | Nov 10 09:00–13:00 | **Site power / hardware** | `network_state='Out of Network'`, `modem_led='red'`, metrics NULL, traffic 0 |
| 4 | `IDR-R04..R07` (all on `IDR-LC-02`) | Nov 8 11:00–12:30 | **Line-card outage** | All 4 remotes Out of Network together → JOIN shows `IDR-LC-02` `status='Down'` (`LC_HW_FAULT`) |
| 5 | `IDR-R01..R07` (under `IDR-PP-01`) | Nov 6 03:00–05:00 | **PP degraded** | Mild latency bump + inroute starvation; `IDR-PP-01` `status='Degraded'` (`PP_HIGH_CPU`, cpu ~90%+) |

### JUPITER
| # | Terminal(s) | Window | Root cause | Signature |
|---|---|---|---|---|
| 1 | `JUP-T02` | Nov 6 15:00–16:30 | **Ka rain fade** (transient) | SQF dips toward ~25, `state_code='12.x'`, `frames_with_no_signal` rising, self-recovers |
| 2 | `JUP-T07` | Nov 9 12:00 → end | **FAP / allowance throttle** | `data_allowance_state='FAP_Throttled'`, throughput collapses (~1–2 Mbps), **SQF normal, `state_code='OK'`** |
| 3 | `JUP-T09` | Nov 7 08:00 → end | **Pointing fault** | `state_code='11.x'`, SQF low & unstable (~40) in clear sky, sustained |
| 4 | `JUP-T05` | Nov 8 13:00–17:00 | **Overheating** | `state_code='30.x'`, `temperature_c` ~70–82°C, throughput reduced |
| 5 | `JUP-T01..T04` (all on `JUP-IPGW-N1`) | Nov 11 09:00–10:00 | **IPGW / beam outage** | SQF fine but **no IP service** (throughput 0, latency timeout) → JOIN shows `JUP-IPGW-N1` `status='Down'` (`IPGW_UNREACHABLE`) |

### Starlink
| # | Terminal(s) | Window | Root cause | Signature |
|---|---|---|---|---|
| 1 | `STAR-D03` | Nov 6–10, 16:00–18:00 daily | **Obstruction** (siting) | `currently_obstructed=1`, `fraction_obstructed_pct` 2–5%, ping drop ↑, `alerts` has `obstructed` |
| 2 | `STAR-D08` | Nov 7 & 8, 12:00–15:00 | **Thermal throttle** | `alerts` has `thermal_throttle`, throughput ~halved |
| 3 | `STAR-D06` | Nov 10 08:00–10:00 | **Offline / reboot** | `state='OFFLINE'`, `uptime_s` resets to 0, traffic 0 |
| 4 | `STAR-D02` | Nov 9 00:00–12:00 | **Slow ethernet** | `alerts` has `slow_ethernet_speeds`, throughput capped low |
| 5 | `STAR-D01..D05` (home `STAR-POP-SIN`) | Nov 9 14:00–14:40 | **PoP outage** | All 5 dishes `OFFLINE` together → JOIN shows `STAR-POP-SIN` `status='Down'` (`POP_OUTAGE`) |

---

## 5. Example queries

```sql
-- (a) Single-terminal timeline (iDirect remote around the rain fade)
SELECT timestamp_myt, network_state, downstream_snr_db, clearsky_snr_db,
       tx_power_dbm, tx_max_power_dbm, latency_rtt_ms
FROM   idirect_performance
WHERE  remote_id='IDR-R03'
  AND  timestamp_myt BETWEEN '2026-11-07 13:30' AND '2026-11-07 16:30'
ORDER  BY timestamp_myt;

-- (b) Deviation-from-baseline: iDirect remotes below clear-sky C/N right now
SELECT remote_id, downstream_snr_db, clearsky_snr_db,
       ROUND(clearsky_snr_db - downstream_snr_db,1) AS drop_db
FROM   idirect_performance
WHERE  timestamp_myt='2026-11-07 15:00:00'
  AND  downstream_snr_db < clearsky_snr_db - 3
ORDER  BY drop_db DESC;

-- (c) Scope "one vs many": how many remotes Out of Network per line card at a time
SELECT line_card, COUNT(DISTINCT remote_id) AS remotes_down
FROM   idirect_performance
WHERE  timestamp_myt='2026-11-08 11:30:00' AND network_state='Out of Network'
GROUP  BY line_card;

-- (d) HUB CROSS-RELATION: is a mass outage hub-side? (iDirect line card)
SELECT i.line_card, h.status, h.fault_code, h.fault_detail,
       COUNT(DISTINCT i.remote_id) AS remotes_down
FROM   idirect_performance i
JOIN   hub_components h
  ON   h.component_id = i.line_card AND h.timestamp_myt = i.timestamp_myt
WHERE  i.timestamp_myt='2026-11-08 11:30:00' AND i.network_state='Out of Network'
GROUP  BY i.line_card, h.status, h.fault_code, h.fault_detail;
-- Jupiter: JOIN ... ON h.component_id = j.ipgw      (STAR-POP outage: h.component_id = s.pop)

-- (e) Jupiter FAP throttle: slow but healthy signal (looks like an outage, isn't)
SELECT terminal_san, data_allowance_state, sqf, clearsky_sqf,
       downlink_throughput_mbps, plan_cir_mbps
FROM   jupiter_performance
WHERE  timestamp_myt='2026-11-10 14:00:00' AND data_allowance_state='FAP_Throttled';

-- (f) Starlink obstruction events with the alert flag set
SELECT terminal_id, timestamp_myt, fraction_obstructed_pct, pop_ping_drop_rate, alerts
FROM   starlink_performance
WHERE  currently_obstructed=1 AND alerts LIKE '%obstructed%'
ORDER  BY timestamp_myt LIMIT 20;

-- (g) TOP-5 terminals by total traffic over the week (GB)
SELECT remote_id,
       ROUND(SUM(data_volume_mb)/1024.0,1) AS total_gb,
       ROUND(AVG(utilization_pct),1)       AS avg_util_pct
FROM   idirect_performance
GROUP  BY remote_id
ORDER  BY total_gb DESC
LIMIT  5;                       -- BOTTOM-5: ORDER BY total_gb ASC

-- (h) TOP-5 by average utilization vs plan (most loaded)
SELECT terminal_san, ROUND(AVG(utilization_pct),1) AS avg_util_pct, plan_cir_mbps
FROM   jupiter_performance
GROUP  BY terminal_san
ORDER  BY avg_util_pct DESC
LIMIT  5;

-- (i) Hourly aggregation (keep LLM result sets small on per-minute data)
SELECT substr(timestamp_myt,1,13) || ':00' AS hour_myt,
       ROUND(AVG(pop_ping_latency_ms),1)   AS avg_latency_ms,
       ROUND(AVG(pop_ping_drop_rate),4)    AS avg_drop,
       ROUND(SUM(data_volume_mb),1)        AS volume_mb
FROM   starlink_performance
WHERE  terminal_id='STAR-D03'
GROUP  BY hour_myt
ORDER  BY hour_myt;

-- (j) All hub faults in the week (what broke, when, where)
SELECT system, component_id, component_type, status, fault_code,
       MIN(timestamp_myt) AS started, MAX(timestamp_myt) AS ended
FROM   hub_components
WHERE  status <> 'OK'
GROUP  BY system, component_id, component_type, status, fault_code
ORDER  BY started;
```

---

## 6. Notes for the LLM / app

- **Per-minute granularity is fine-grained.** For trend questions, aggregate (hourly `AVG`/`SUM`, query (i))
  rather than returning thousands of raw rows.
- **Triage on deviation, not absolutes** — compare `downstream_snr_db` to `clearsky_snr_db` (iDirect) and
  `sqf` to `clearsky_sqf` (Jupiter), per the KB.
- **"Many vs one" is the key scoping question.** Group by the shared component (`line_card` / `beam` / `gateway`
  / `ipgw` / `pop`), then JOIN `hub_components` to confirm a hub-side root cause (KB §4D → escalate).
- **Latency `9999`** is a timeout sentinel, not a real 9.999 s RTT — exclude it from latency averages.
- **File size:** the `.db` is ~136 MB (per-minute × 40 series). If you commit it, consider Git LFS or
  `.gitignore` + regenerate via `generate_mock_db.py`.
