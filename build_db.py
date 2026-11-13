"""
build_db.py — Downsample the 136 MB source telemetry DB into a <100 MB file that
can be committed to GitHub and deployed on Streamlit Community Cloud.

Strategy (preserves incident fidelity where it matters):
  Keep a row if ANY of:
    1. its minute is a multiple of 5   -> 5-minute base grain for background trends
    2. its timestamp falls in a bounded "high-resolution" incident window
       -> full per-minute onset/recovery for transient incidents
    3. (hub_components only) status <> 'OK'
       -> every fault row kept at full resolution, so MIN/MAX incident boundaries
          stay exact

This is a one-time DEV step. It reads Database/terminal_performance.db (the 136 MB
source, which is .gitignore'd) and writes Database/terminal_ops.db (committed).

Run:  python build_db.py
"""

import os
import sqlite3
import sys

from config import DB_PATH, SOURCE_DB_PATH, SCHEMA_SQL_PATH

TABLES = [
    "idirect_performance",
    "jupiter_performance",
    "starlink_performance",
    "hub_components",
]

# Bounded transient-incident windows (MYT) kept at full per-minute resolution.
# Derived from Database/README_SCHEMA.md §4 (only the time-bounded ones; sustained
# "-> end" incidents read fine at 5-min grain).
HIGH_RES_WINDOWS = [
    ("2026-11-06 03:00:00", "2026-11-06 05:00:00"),  # iDirect IDR-PP-01 degraded
    ("2026-11-06 15:00:00", "2026-11-06 16:30:00"),  # Jupiter JUP-T02 Ka rain fade
    ("2026-11-07 14:00:00", "2026-11-07 16:00:00"),  # iDirect IDR-R03 rain fade
    ("2026-11-08 11:00:00", "2026-11-08 12:30:00"),  # iDirect IDR-LC-02 line-card outage
    ("2026-11-08 13:00:00", "2026-11-08 17:00:00"),  # Jupiter JUP-T05 overheating
    ("2026-11-09 14:00:00", "2026-11-09 14:40:00"),  # Starlink STAR-POP-SIN outage
    ("2026-11-10 08:00:00", "2026-11-10 10:00:00"),  # Starlink STAR-D06 offline/reboot
    ("2026-11-10 09:00:00", "2026-11-10 13:00:00"),  # iDirect IDR-R01 site power
    ("2026-11-11 09:00:00", "2026-11-11 10:00:00"),  # Jupiter JUP-IPGW-N1 outage
    # Starlink STAR-D03 daily obstruction (Nov 6-10, 16:00-18:00)
    ("2026-11-06 16:00:00", "2026-11-06 18:00:00"),
    ("2026-11-07 16:00:00", "2026-11-07 18:00:00"),
    ("2026-11-08 16:00:00", "2026-11-08 18:00:00"),
    ("2026-11-09 16:00:00", "2026-11-09 18:00:00"),
    ("2026-11-10 16:00:00", "2026-11-10 18:00:00"),
    # Starlink STAR-D08 thermal throttle (Nov 7 & 8, 12:00-15:00)
    ("2026-11-07 12:00:00", "2026-11-07 15:00:00"),
    ("2026-11-08 12:00:00", "2026-11-08 15:00:00"),
]

# Known seeded hub faults we assert survive the downsample (sanity check).
EXPECTED_FAULTS = {"PP_HIGH_CPU", "LC_HW_FAULT", "POP_OUTAGE", "IPGW_UNREACHABLE"}


def _keep_predicate(table: str) -> str:
    """Build the WHERE clause that decides which rows to carry over."""
    minute_rule = "CAST(strftime('%M', timestamp_myt) AS INTEGER) % 5 = 0"
    window_rules = " OR ".join(
        f"(timestamp_myt BETWEEN '{s}' AND '{e}')" for s, e in HIGH_RES_WINDOWS
    )
    clause = f"({minute_rule}) OR ({window_rules})"
    if table == "hub_components":
        clause += " OR (status <> 'OK')"
    return clause


def main() -> int:
    if not SOURCE_DB_PATH.exists():
        print(f"ERROR: source DB not found: {SOURCE_DB_PATH}")
        print("This is the 136 MB file you prepared; it must be present to build.")
        return 1

    schema_sql = SCHEMA_SQL_PATH.read_text(encoding="utf-8")

    if DB_PATH.exists():
        DB_PATH.unlink()

    print(f"Building {DB_PATH.name} from {SOURCE_DB_PATH.name} ...")
    dest = sqlite3.connect(DB_PATH)
    try:
        dest.executescript(schema_sql)  # create tables + indexes (DROP IFs are harmless)
        dest.execute(f"ATTACH DATABASE '{SOURCE_DB_PATH.as_posix()}' AS src")

        for table in TABLES:
            before = dest.execute(f"SELECT COUNT(*) FROM src.{table}").fetchone()[0]
            dest.execute(
                f"INSERT INTO {table} SELECT * FROM src.{table} "
                f"WHERE {_keep_predicate(table)}"
            )
            after = dest.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            print(f"  {table:<24} {before:>8,} -> {after:>8,} rows "
                  f"({after / before:.0%})")

        dest.commit()
        dest.execute("DETACH DATABASE src")
        dest.execute("VACUUM")
        dest.commit()

        # --- sanity checks -------------------------------------------------- #
        faults = {
            r[0]
            for r in dest.execute(
                "SELECT DISTINCT fault_code FROM hub_components "
                "WHERE fault_code IS NOT NULL"
            ).fetchall()
        }
        missing = EXPECTED_FAULTS - faults
        print("\nSeeded hub faults present:", sorted(faults))
        if missing:
            print(f"  WARNING: missing expected faults: {sorted(missing)}")
        else:
            print("  OK — all 4 expected hub faults survived the downsample.")
    finally:
        dest.close()

    size_mb = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"\n{DB_PATH.name}: {size_mb:.1f} MB")
    if size_mb >= 100:
        print("  WARNING: >=100 MB — GitHub will reject it. Increase the base grain "
              "(e.g. % 10 instead of % 5) in _keep_predicate().")
        return 2
    print("  OK — under GitHub's 100 MB limit; safe to commit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
