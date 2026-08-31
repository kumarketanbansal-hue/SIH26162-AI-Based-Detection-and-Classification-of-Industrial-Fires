"""
fetch_firms.py
--------------
Fetches Near-Real-Time (NRT) VIIRS_SNPP fire radiative power data for India
from the NASA FIRMS API and ingests it into a PostGIS-enabled PostgreSQL table.

Usage:
    python fetch_firms.py

Environment variables (loaded from .env):
    FIRMS_MAP_KEY  – Your NASA FIRMS MAP_KEY
    DB_PASSWORD    – PostgreSQL password for user 'postgres'
"""

import csv
import io
import os
import sys

import psycopg2
import requests
from dotenv import load_dotenv
load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIRMS_DAYS   = 5   # Look-back window (FIRMS Area API max = 5)
FIRMS_SOURCE = "VIIRS_SNPP_NRT"

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = 5432
DB_NAME = "sih_fire_db"
DB_USER = "postgres"

# CSV columns returned by the FIRMS area API
FIRMS_COLUMNS = [
    "latitude", "longitude", "brightness", "scan", "track",
    "acq_date", "acq_time", "satellite", "confidence", "version",
    "bright_t31", "frp", "daynight",
]

# ---------------------------------------------------------------------------
# DDL
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
    classification TEXT,                  -- filled later by the classifier
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


def fetch_csv(map_key, bbox):
    """Download FIRMS NRT data for the given bounding box and parse the CSV rows."""
    url = (
        f"https://firms.modaps.eosdis.nasa.gov/api/area/csv"
        f"/{map_key}/{FIRMS_SOURCE}/{bbox}/{FIRMS_DAYS}"
    )
    print(f"[FIRMS] Fetching: {url}")

    response = requests.get(url, timeout=60)
    response.raise_for_status()

    content = response.text
    if not content.strip():
        print("[FIRMS] API returned an empty response -- no active fires detected.")
        return []

    reader = csv.DictReader(io.StringIO(content))
    rows = list(reader)
    print(f"[FIRMS] Received {len(rows)} record(s) from API.")
    return rows


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
    """Create the thermal_points and india_boundary tables (and PostGIS extension) if not present."""
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute(CREATE_TABLE_SQL)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS india_boundary (
            id   SERIAL PRIMARY KEY,
            geom GEOMETRY(Geometry, 4326)
        );
    """)


def safe_float(value):
    """Convert a string to float; return None on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def insert_rows(cur, rows):
    """
    Insert FIRMS rows into thermal_points only if they fall inside India's boundary.

    Duplicates are identified by (latitude, longitude, acq_date, acq_time).
    Returns (inserted_count, duplicate_count, outside_boundary_count).
    """
    inserted = 0
    duplicates = 0
    outside_boundary = 0

    insert_sql = """
        INSERT INTO thermal_points
            (latitude, longitude, geom, brightness, frp, confidence,
             acq_date, acq_time, satellite, daynight)
        SELECT
            %(latitude)s, %(longitude)s,
            ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326),
            %(brightness)s, %(frp)s, %(confidence)s,
            %(acq_date)s, %(acq_time)s, %(satellite)s, %(daynight)s
        WHERE EXISTS (
            SELECT 1 FROM india_boundary b
            WHERE ST_Within(ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326), b.geom)
        )
        ON CONFLICT (latitude, longitude, acq_date, acq_time) DO NOTHING;
    """

    boundary_check_sql = """
        SELECT EXISTS (
            SELECT 1 FROM india_boundary b
            WHERE ST_Within(ST_SetSRID(ST_MakePoint(%s, %s), 4326), b.geom)
        );
    """

    # Unique constraint for duplicate detection (created once, idempotently)
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

    for row in rows:
        lat = safe_float(row.get("latitude"))
        lon = safe_float(row.get("longitude"))

        if lat is None or lon is None:
            print(f"[WARN] Skipping row with invalid coordinates: {row}")
            outside_boundary += 1
            continue

        params = {
            "lat":        lat,
            "lon":        lon,
            "latitude":   lat,
            "longitude":  lon,
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
            # Determine if point was skipped for being outside boundary or a duplicate
            cur.execute(boundary_check_sql, (lon, lat))
            is_inside = cur.fetchone()[0]
            if not is_inside:
                outside_boundary += 1
            else:
                duplicates += 1

    return inserted, duplicates, outside_boundary


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    map_key, db_password, bbox = load_env()

    # 1. Fetch data from FIRMS API
    rows = fetch_csv(map_key, bbox)
    if not rows:
        print("[DONE] Nothing to insert.")
        return

    # 2. Connect to PostgreSQL
    print(f"[DB] Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT} ...")
    try:
        conn = get_connection(db_password)
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Could not connect to the database: {exc}")
        sys.exit(1)

    # 3. Ingest
    with conn:
        with conn.cursor() as cur:
            ensure_table(cur)
            inserted, duplicates, outside_boundary = insert_rows(cur, rows)

    conn.close()

    # 4. Summary
    print("\n-- Ingestion summary ------------------------------------------")
    print(f"  New points inserted       : {inserted}")
    print(f"  Duplicates skipped        : {duplicates}")
    print(f"  Outside boundary skipped  : {outside_boundary}")
    print("---------------------------------------------------------------")


if __name__ == "__main__":
    main()
