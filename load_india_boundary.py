"""
load_india_boundary.py
----------------------
Reads india_boundary.geojson (a single GeoJSON FeatureCollection with one country polygon/multipolygon),
creates a table `india_boundary` in the PostgreSQL database with columns:
    id   SERIAL PRIMARY KEY
    geom GEOMETRY(Geometry, 4326)

and inserts the boundary geometry using ST_SetSRID(ST_GeomFromGeoJSON(...), 4326).
Prints a confirmation with the row count and geometry type when done.

Usage:
    python load_india_boundary.py

Environment variables (loaded from .env):
    DB_HOST      - PostgreSQL host (default: localhost)
    DB_PORT      - PostgreSQL port (default: 5432)
    DB_NAME      - Database name (default: sih_fire_db)
    DB_USER      - Database user (default: postgres)
    DB_PASSWORD  - PostgreSQL password for user
    DB_SSLMODE   - SSL mode (default: prefer)
"""

import json
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Configuration & Environment
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sih_fire_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

CREATE_TABLE_SQL = """
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS india_boundary (
    id SERIAL PRIMARY KEY,
    geom GEOMETRY(Geometry, 4326)
);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_env():
    """Load required environment variables from .env and return DB password."""
    load_dotenv()
    db_password = os.getenv("DB_PASSWORD")
    if not db_password:
        print("[ERROR] Missing environment variable: DB_PASSWORD")
        print("  -> Fill in your .env file before running this script.")
        sys.exit(1)
    return db_password


def get_connection(db_password):
    """Return an open psycopg2 connection to the database."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=db_password,
        sslmode=DB_SSLMODE,
    )


def ensure_table(cur):
    """Create PostGIS extension and india_boundary table if they do not exist."""
    cur.execute(CREATE_TABLE_SQL)


def find_geojson_file():
    """Locate india_boundary.geojson (or fallback to india_boundary.geojson.txt)."""
    base_dir = Path(__file__).resolve().parent
    candidates = [
        base_dir / "india_boundary.geojson",
        base_dir / "india_boundary.geojson.txt",
        Path("india_boundary.geojson"),
        Path("india_boundary.geojson.txt"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()

    print(f"[ERROR] Could not find india_boundary.geojson in '{base_dir}'.")
    sys.exit(1)


def load_boundary(conn, filepath):
    """Parse GeoJSON file, ensure table, and insert boundary geometry."""
    print(f"[GeoJSON] Reading file: {filepath}")
    with open(filepath, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    geometries = []
    if data.get("type") == "FeatureCollection":
        for feature in data.get("features", []):
            geom = feature.get("geometry")
            if geom:
                geometries.append(geom)
    elif data.get("type") == "Feature":
        geom = data.get("geometry")
        if geom:
            geometries.append(geom)
    elif "coordinates" in data and "type" in data:
        geometries.append(data)
    else:
        print("[ERROR] Unrecognized GeoJSON structure.")
        sys.exit(1)

    if not geometries:
        print("[ERROR] No geometries found in GeoJSON file.")
        sys.exit(1)

    print(f"[GeoJSON] Extracted {len(geometries)} geometry/feature(s).")

    with conn.cursor() as cur:
        ensure_table(cur)

        # Clear existing rows to prevent duplicate country boundaries on re-runs
        cur.execute("TRUNCATE TABLE india_boundary RESTART IDENTITY;")

        insert_sql = """
            INSERT INTO india_boundary (geom)
            VALUES (ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326));
        """

        for geom in geometries:
            geom_str = json.dumps(geom)
            cur.execute(insert_sql, (geom_str,))

        conn.commit()

        # Query row count and geometry type
        cur.execute("""
            SELECT COUNT(*), ST_GeometryType(geom)
            FROM india_boundary
            GROUP BY ST_GeometryType(geom);
        """)
        results = cur.fetchall()

    return results


def main():
    print("=" * 60)
    print("  SIH 2026 -- Load India Boundary to PostGIS")
    print("=" * 60)

    db_password = load_env()
    geojson_path = find_geojson_file()

    try:
        conn = get_connection(db_password)
        print(f"[DB] Connected to PostgreSQL '{DB_NAME}' on {DB_HOST}:{DB_PORT} (sslmode={DB_SSLMODE}).")
    except Exception as exc:
        print(f"[ERROR] Failed to connect to database: {exc}")
        sys.exit(1)

    try:
        results = load_boundary(conn, geojson_path)
        print("=" * 60)
        total_rows = sum(r[0] for r in results)
        for count, geom_type in results:
            print(f"[CONFIRMATION] Successfully loaded table 'india_boundary' -> Row Count: {count}, Geometry Type: {geom_type}")
        print(f"[SUMMARY] Total Rows Inserted: {total_rows}")
        print("=" * 60)
    except Exception as exc:
        conn.rollback()
        print(f"[ERROR] Failed during boundary ingestion: {exc}")
        sys.exit(1)
    finally:
        conn.close()
        print("[DB] Connection closed.")


if __name__ == "__main__":
    main()
