-- ===================================================================
-- Mock satellite terminal performance DB (iDirect / JUPITER / Starlink)
-- Timestamps are MYT (UTC+8), format 'YYYY-MM-DD HH:MM:00'.
-- Latency value 9999.0 ms = "request timed out" (no response).
-- ===================================================================

DROP TABLE IF EXISTS idirect_performance;
CREATE TABLE idirect_performance (
    reading_id          INTEGER PRIMARY KEY,
    timestamp_myt       TEXT    NOT NULL,   -- 'YYYY-MM-DD HH:MM:00' MYT
    remote_id           TEXT    NOT NULL,   -- e.g. IDR-R03
    remote_name         TEXT    NOT NULL,   -- site name
    beam                TEXT    NOT NULL,   -- Ku space-segment beam label
    line_card           TEXT    NOT NULL,   -- FK -> hub_components.component_id (IDR-LC-*)
    inroute_group       TEXT    NOT NULL,   -- FK -> hub_components (IDR-INR-*)
    outroute            TEXT    NOT NULL,   -- FK -> hub_components (IDR-OUT-*)
    network_state       TEXT    NOT NULL,   -- In Network | Acquisition | Out of Network | Fault
    modem_led           TEXT    NOT NULL,   -- green | flashing_green | amber | red
    downstream_snr_db   REAL,               -- C/N remote Rx (space->remote)
    clearsky_snr_db     REAL    NOT NULL,   -- static baseline
    upstream_cn_db      REAL,               -- C/N at hub (remote burst)
    ucp_target_db       REAL    NOT NULL,   -- static UCP target
    tx_power_dbm        REAL,               -- current Tx power
    tx_max_power_dbm    REAL    NOT NULL,   -- static; tx pinned here = no headroom
    rx_power_dbm        REAL,               -- Rx composite power
    latency_rtt_ms      REAL,               -- 9999 = timeout
    inroute_alloc_pct   REAL,               -- TDMA slot allocation granted
    tdm_lock_lost_count INTEGER,            -- cumulative outroute lock losses
    temperature_c       REAL,
    downlink_throughput_mbps REAL,
    uplink_throughput_mbps   REAL,
    plan_cir_mbps       REAL    NOT NULL,   -- static subscribed/committed rate
    utilization_pct     REAL,               -- throughput / plan * 100
    data_volume_mb      REAL                -- down+up volume in this 1-min interval
);

DROP TABLE IF EXISTS jupiter_performance;
CREATE TABLE jupiter_performance (
    reading_id          INTEGER PRIMARY KEY,
    timestamp_myt       TEXT    NOT NULL,
    terminal_san        TEXT    NOT NULL,   -- e.g. JUP-T02
    site_name           TEXT    NOT NULL,
    beam                TEXT    NOT NULL,   -- FK -> hub_components (JUP-BEAM-*)
    ipgw                TEXT    NOT NULL,   -- FK -> hub_components (JUP-IPGW-*)
    gateway             TEXT    NOT NULL,   -- FK -> hub_components (JUP-GW-*)
    state_code          TEXT    NOT NULL,   -- OK | 11.x | 12.x | 13.x | 14.x | 21.x | 30.x
    state_category      TEXT    NOT NULL,   -- human label
    diagnostic_code     TEXT,               -- verbatim SCC diagnostic, nullable
    sqf                 INTEGER,            -- Signal Quality Factor (higher=better; 0/1=no signal)
    clearsky_sqf        INTEGER NOT NULL,   -- static baseline
    frames_with_no_signal INTEGER,          -- cumulative
    tx_status           TEXT,               -- Transmitting | Idle | Fault
    tx_power_dbm        REAL,
    data_allowance_state TEXT NOT NULL,     -- Normal | FAP_Throttled | Suspended
    registration_state  TEXT    NOT NULL,   -- Registered | Commissioning | Unregistered
    latency_rtt_ms      REAL,               -- 9999 = timeout
    temperature_c       REAL,
    downlink_throughput_mbps REAL,
    uplink_throughput_mbps   REAL,
    plan_cir_mbps       REAL    NOT NULL,
    utilization_pct     REAL,
    data_volume_mb      REAL
);

DROP TABLE IF EXISTS starlink_performance;
CREATE TABLE starlink_performance (
    reading_id          INTEGER PRIMARY KEY,
    timestamp_myt       TEXT    NOT NULL,
    terminal_id         TEXT    NOT NULL,   -- e.g. STAR-D03
    service_line_id     TEXT    NOT NULL,
    site_name           TEXT    NOT NULL,
    ground_station      TEXT    NOT NULL,   -- FK -> hub_components (STAR-GS-*)
    pop                 TEXT    NOT NULL,   -- home PoP; FK -> hub_components (STAR-POP-*)
    hardware_variant    TEXT    NOT NULL,   -- Standard Gen3 | Flat High Performance | Enterprise
    state               TEXT    NOT NULL,   -- CONNECTED | BOOTING | SEARCHING | OFFLINE | STOWED
    uptime_s            INTEGER,            -- since last reboot
    pop_ping_drop_rate  REAL,               -- 0..1 fraction of pings lost
    pop_ping_latency_ms REAL,               -- ~25-60 healthy; 9999 = timeout
    currently_obstructed INTEGER,           -- 0/1
    fraction_obstructed_pct REAL,           -- % of sky view blocked over window
    service_line_state  TEXT    NOT NULL,   -- active | suspended
    alerts              TEXT,               -- comma-joined gRPC alert flags ('' if none)
    downlink_throughput_mbps REAL,
    uplink_throughput_mbps   REAL,
    plan_cir_mbps       REAL    NOT NULL,
    utilization_pct     REAL,
    data_volume_mb      REAL
);

DROP TABLE IF EXISTS hub_components;
CREATE TABLE hub_components (
    reading_id          INTEGER PRIMARY KEY,
    timestamp_myt       TEXT    NOT NULL,
    component_id        TEXT    NOT NULL,   -- IDR-* | JUP-* | STAR-*  (terminals JOIN here)
    component_type      TEXT    NOT NULL,   -- per-system type (see README)
    system              TEXT    NOT NULL,   -- iDirect | Jupiter | Starlink
    parent_component    TEXT,               -- next component up the same system's chain
    status              TEXT    NOT NULL,   -- OK | Degraded | Down | Maintenance
    fault_code          TEXT,               -- nullable
    fault_detail        TEXT,               -- nullable
    cpu_pct             REAL,               -- for processing elements
    temperature_c       REAL,
    agg_throughput_mbps REAL,               -- approx aggregate carried
    terminals_served    INTEGER NOT NULL    -- static count
);

CREATE INDEX idx_idr_remote_ts  ON idirect_performance (remote_id, timestamp_myt);
CREATE INDEX idx_idr_ts         ON idirect_performance (timestamp_myt);
CREATE INDEX idx_idr_lc         ON idirect_performance (line_card);

CREATE INDEX idx_jup_san_ts     ON jupiter_performance (terminal_san, timestamp_myt);
CREATE INDEX idx_jup_ts         ON jupiter_performance (timestamp_myt);
CREATE INDEX idx_jup_gw         ON jupiter_performance (gateway);
CREATE INDEX idx_jup_beam       ON jupiter_performance (beam);

CREATE INDEX idx_star_term_ts   ON starlink_performance (terminal_id, timestamp_myt);
CREATE INDEX idx_star_ts        ON starlink_performance (timestamp_myt);
CREATE INDEX idx_star_pop       ON starlink_performance (pop);

CREATE INDEX idx_hub_comp_ts    ON hub_components (component_id, timestamp_myt);
CREATE INDEX idx_hub_ts         ON hub_components (timestamp_myt);
CREATE INDEX idx_hub_status     ON hub_components (status);
