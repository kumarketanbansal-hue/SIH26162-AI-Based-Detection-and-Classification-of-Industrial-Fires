"""
historical_backfill.py
-----------------------
Backfills historical VIIRS_SNPP Near-Real-Time thermal point detections for India
from the NASA FIRMS API over the last 6 months in 5-day chunks.

Usage:
    python historical_backfill.py

Environment variables (loaded from .env):
    FIRMS_MAP_KEY  – NASA FIRMS MAP_KEY
    DB_PASSWORD    – PostgreSQL password for user 'postgres'
    BBOX           – Bounding box string, e.g. "68.0,6.5,97.5,35.5"
    DB_HOST        – PostgreSQL host (default: localhost)
"""

import csv
import io
import os
import sys
import time
from datetime import date, timedelta

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIRMS_SOURCE   = "VIIRS_SNPP_NRT"
CHUNK_DAYS     = 5      # Maximum days allowed per area query with date parameter
BACKFILL_DAYS  = 185    # ~6 months of historical data (37 chunks of 5 days)
RATE_LIMIT_SEC = 1.0    # Courtesy delay between NASA FIRMS API calls

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sih_fire_db")
DB_USER = os.getenv("DB_USER", "postgres")

# ---------------------------------------------------------------------------
# DDL & Schema Setup
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS thermal_points (
    id             SERIAL PRIMARY KEY,
    latitude       DOUBLE PRECISION  NOT NULL,
    longitude      DOUBLE PRECISION  NOT NULL,
    geom           GEOMETRY(Point, 4326),
    brightness     DOUBLE PRECISION,
    frp            DOUBLE PRECISION,
    confidence     TEXT,
    acq_date       DATE,
    acq_time       TEXT,
    satellite      TEXT,
    daynight       TEXT,
    classification TEXT,                  -- filled later by classifier
    inserted_at    TIMESTAMP DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load required environment variables from .env and return them."""
    load_dotenv()
    map_key     = os.getenv("FIRMS_MAP_KEY")
    db_password = os.getenv("DB_PASSWORD")
    bbox        = os.getenv("BBOX")

    missing = [
        k for k, v in [
            ("FIRMS_MAP_KEY", map_key),
            ("DB_PASSWORD",   db_password),
            ("BBOX",          bbox),
        ]
        if not v
    ]
    if missing:
        print(f"[ERROR] Missing environment variable(s): {', '.join(missing)}")
        print("  -> Fill in your .env file before running this script.")
        sys.exit(1)

    return map_key, db_password, bbox


def get_connection(db_password):
    """Return an open psycopg2 connection to sih_fire_db."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=db_password,
    )


def ensure_table(cur):
    """Create the thermal_points table, PostGIS extension, and unique constraint."""
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute(CREATE_TABLE_SQL)
    cur.execute("""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'uq_thermal_lat_lon_date_time'
            ) THEN
                ALTER TABLE thermal_points
                ADD CONSTRAINT uq_thermal_lat_lon_date_time
                UNIQUE (latitude, longitude, acq_date, acq_time);
            END IF;
        END
        $$;
    """)


def safe_float(value):
    """Convert string to float; return None on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def fetch_chunk_csv(map_key, bbox, start_date_str):
    """
    Download FIRMS data for a specific 5-day chunk starting on start_date_str.
    Returns parsed list of dictionary rows.
    """
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv"
        f"/{map_key}/{FIRMS_SOURCE}/{bbox}/{CHUNK_DAYS}/{start_date_str}"
    )

    response = requests.get(url, timeout=60)
    response.raise_for_status()

    content = response.text
    if not content.strip():
        return []

    # If FIRMS returns an error message as plain text instead of CSV
    if "Invalid" in content or "Error" in content:
        if not content.startswith("latitude"):
            raise ValueError(f"FIRMS API returned message: {content.strip()}")

    reader = csv.DictReader(io.StringIO(content))
    return list(reader)


def insert_rows(cur, rows):
    """
    Insert FIRMS rows into thermal_points table.
    Duplicates are skipped via unique constraint uq_thermal_lat_lon_date_time.
    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    insert_sql = """
        INSERT INTO thermal_points
            (latitude, longitude, geom, brightness, frp, confidence,
             acq_date, acq_time, satellite, daynight)
        VALUES (
            %(lat)s, %(lon)s,
            ST_SetSRID(ST_MakePoint(%(lon)s, %(lat)s), 4326),
            %(brightness)s, %(frp)s, %(confidence)s,
            %(acq_date)s, %(acq_time)s, %(satellite)s, %(daynight)s
        )
        ON CONFLICT DO NOTHING
    """

    for row in rows:
        lat = safe_float(row.get("latitude"))
        lon = safe_float(row.get("longitude"))

        if lat is None or lon is None:
            skipped += 1
            continue

        params = {
            "lat":        lat,
            "lon":        lon,
            "brightness": safe_float(row.get("brightness")),
            "frp":        safe_float(row.get("frp")),
            "confidence": row.get("confidence", "").strip() or None,
            "acq_date":   row.get("acq_date",   "").strip() or None,
            "acq_time":   row.get("acq_time",   "").strip() or None,
            "satellite":  row.get("satellite",  "").strip() or None,
            "daynight":   row.get("daynight",   "").strip() or None,
        }

        cur.execute(insert_sql, params)
        if cur.rowcount == 1:
            inserted += 1
        else:
            skipped += 1

    return inserted, skipped


# ---------------------------------------------------------------------------
# Entry Point
# ---------------------------------------------------------------------------

def main():
    map_key, db_password, bbox = load_env()

    # Calculate 5-day chunks going backward from today
    today = date.today()
    chunks = []
    current_offset = 0

    while current_offset < BACKFILL_DAYS:
        # Start date of the 5-day window
        chunk_start = today - timedelta(days=current_offset + CHUNK_DAYS)
        chunks.append(chunk_start.strftime("%Y-%m-%d"))
        current_offset += CHUNK_DAYS

    total_chunks = len(chunks)

    print("=================================================================")
    print(f" NASA FIRMS 6-Month Historical Backfill: {total_chunks} chunks ({BACKFILL_DAYS} days)")
    print(f" Target Region (BBOX): {bbox}")
    print(f" Date range: {chunks[-1]} to {chunks[0]}")
    print("=================================================================\n")

    # Connect to PostgreSQL
    print(f"[DB] Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT} ...")
    try:
        conn = get_connection(db_password)
        conn.autocommit = False
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Could not connect to the database: {exc}")
        sys.exit(1)

    with conn:
        with conn.cursor() as cur:
            ensure_table(cur)

    total_inserted = 0
    total_skipped = 0
    successful_chunks = 0
    failed_chunks = []

    # Iterate through each 5-day chunk backward in time
    for idx, start_date_str in enumerate(chunks, start=1):
        try:
            # 1. Fetch CSV from FIRMS API
            rows = fetch_chunk_csv(map_key, bbox, start_date_str)

            # 2. Insert into database
            chunk_inserted = 0
            chunk_skipped = 0
            if rows:
                with conn:
                    with conn.cursor() as cur:
                        chunk_inserted, chunk_skipped = insert_rows(cur, rows)

            total_inserted += chunk_inserted
            total_skipped += chunk_skipped
            successful_chunks += 1

            # 3. Print progress
            print(
                f"[{idx}/{total_chunks}] {start_date_str}: "
                f"{chunk_inserted} new, {chunk_skipped} duplicates "
                f"({len(rows)} fetched)"
            )

        except Exception as exc:
            err_msg = str(exc)
            failed_chunks.append((start_date_str, err_msg))
            print(f"[{idx}/{total_chunks}] {start_date_str}: [FAILED] Error: {err_msg}")

        # 4. Respect rate limits
        if idx < total_chunks:
            time.sleep(RATE_LIMIT_SEC)

    conn.close()

    # -----------------------------------------------------------------------
    # Final Backfill Summary
    # -----------------------------------------------------------------------
    print("\n=================================================================")
    print(" Backfill Run Complete")
    print("=================================================================")
    print(f" Total chunks processed : {total_chunks} ({successful_chunks} successful, {len(failed_chunks)} failed)")
    print(f" Total new rows inserted: {total_inserted}")
    print(f" Total duplicate rows   : {total_skipped}")

    if failed_chunks:
        print("\n Failed Chunks Detail:")
        for chunk_date, error in failed_chunks:
            print(f"   - {chunk_date}: {error}")
    else:
        print(" All chunks completed with 0 errors.")
    print("=================================================================\n")


if __name__ == "__main__":
    main()
