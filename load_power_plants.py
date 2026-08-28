"""
load_power_plants.py
--------------------
Reads the Global Power Plant Database CSV for India (database_IND.csv),
creates a `power_plants` table in sih_fire_db (PostGIS-enabled PostgreSQL),
and inserts every row, building a Point geometry from the longitude/latitude
columns. Rows with missing or unparseable coordinates are skipped.

Usage:
    python load_power_plants.py

Environment variables (loaded from .env):
    DB_PASSWORD  - PostgreSQL password for user 'postgres'
"""

import csv
import os
import sys

import psycopg2
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CSV_FILE = os.path.join(os.path.dirname(__file__), "database_IND.csv")

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = 5432
DB_NAME = "sih_fire_db"
DB_USER = "postgres"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS power_plants (
    id           SERIAL PRIMARY KEY,
    name         TEXT,
    capacity_mw  DOUBLE PRECISION,
    primary_fuel TEXT,
    geom         GEOMETRY(Point, 4326),
    inserted_at  TIMESTAMP DEFAULT NOW()
);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load required environment variables from .env and return them."""
    load_dotenv()
    db_password = os.getenv("DB_PASSWORD")

    if not db_password:
        print("[ERROR] Missing environment variable: DB_PASSWORD")
        print("  -> Fill in your .env file before running this script.")
        sys.exit(1)

    return db_password


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
    """Create the power_plants table (and PostGIS extension) if not present."""
    cur.execute("CREATE EXTENSION IF NOT EXISTS postgis;")
    cur.execute(CREATE_TABLE_SQL)


def safe_float(value):
    """Convert a string to float; return None on failure."""
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def read_csv(filepath):
    """Read the CSV file and return a list of DictReader rows."""
    with open(filepath, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    print(f"[CSV] Read {len(rows)} record(s) from '{filepath}'.")
    return rows


def insert_rows(cur, rows):
    """
    Insert power plant rows into the power_plants table.

    Geometry is built from longitude/latitude using ST_SetSRID + ST_MakePoint.
    Rows with missing or invalid coordinates are skipped.

    Returns (inserted_count, skipped_count).
    """
    inserted = 0
    skipped  = 0

    insert_sql = """
        INSERT INTO power_plants (name, capacity_mw, primary_fuel, geom)
        VALUES (
            %(name)s,
            %(capacity_mw)s,
            %(primary_fuel)s,
            ST_SetSRID(ST_MakePoint(%(longitude)s, %(latitude)s), 4326)
        )
    """

    for row in rows:
        lat = safe_float(row.get("latitude"))
        lon = safe_float(row.get("longitude"))

        if lat is None or lon is None:
            name_val = row.get("name", "").strip()
            print(
                f"[WARN] Skipping row with missing/invalid coordinates: "
                f"name={name_val!r}, "
                f"lat={row.get('latitude')!r}, lon={row.get('longitude')!r}"
            )
            skipped += 1
            continue

        params = {
            "name":         row.get("name",         "").strip() or None,
            "capacity_mw":  safe_float(row.get("capacity_mw")),
            "primary_fuel": row.get("primary_fuel", "").strip() or None,
            "latitude":     lat,
            "longitude":    lon,
        }

        cur.execute(insert_sql, params)
        inserted += 1

    return inserted, skipped


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    db_password = load_env()

    # 1. Read the CSV
    if not os.path.isfile(CSV_FILE):
        print(f"[ERROR] CSV file not found: {CSV_FILE}")
        sys.exit(1)

    rows = read_csv(CSV_FILE)
    if not rows:
        print("[DONE] CSV is empty -- nothing to insert.")
        return

    # 2. Connect to PostgreSQL
    print(f"[DB] Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT} ...")
    try:
        conn = get_connection(db_password)
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Could not connect to the database: {exc}")
        sys.exit(1)

    # 3. Create table and ingest
    with conn:
        with conn.cursor() as cur:
            ensure_table(cur)
            inserted, skipped = insert_rows(cur, rows)

    conn.close()

    # 4. Summary
    print("\n-- Ingestion summary ------------------------------------------")
    print(f"  Rows inserted : {inserted}")
    print(f"  Rows skipped  : {skipped}")
    print("---------------------------------------------------------------")


if __name__ == "__main__":
    main()
