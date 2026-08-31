"""
clean_outside_india.py
----------------------
Deletes all rows from thermal_points that fall outside India's boundary:
DELETE FROM thermal_points WHERE NOT EXISTS (
    SELECT 1 FROM india_boundary b WHERE ST_Within(thermal_points.geom, b.geom)
);

Usage:
    python clean_outside_india.py
"""

import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sih_fire_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD")


def main():
    if not DB_PASSWORD:
        print("[ERROR] Missing DB_PASSWORD in environment.")
        sys.exit(1)

    print("=" * 60)
    print("  SIH 2026 -- Clean Thermal Points Outside India Boundary")
    print("=" * 60)

    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
        )
    except Exception as exc:
        print(f"[ERROR] Database connection failed: {exc}")
        sys.exit(1)

    try:
        with conn.cursor() as cur:
            # Check if india_boundary table exists and has rows
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'india_boundary';")
            exists = cur.fetchone()[0] > 0

            boundary_count = 0
            if exists:
                cur.execute("SELECT COUNT(*) FROM india_boundary;")
                boundary_count = cur.fetchone()[0]

            if not exists or boundary_count == 0:
                print("[INFO] 'india_boundary' table is empty or missing. Loading boundary first...")
                from load_india_boundary import load_boundary, find_geojson_file
                geojson_path = find_geojson_file()
                load_boundary(conn, geojson_path)

            # Get total points before deletion
            cur.execute("SELECT COUNT(*) FROM thermal_points;")
            total_before = cur.fetchone()[0]
            print(f"[DB] Total thermal points before cleanup: {total_before:,}")

            # Execute deletion
            delete_sql = """
                DELETE FROM thermal_points
                WHERE NOT EXISTS (
                    SELECT 1 FROM india_boundary b
                    WHERE ST_Within(thermal_points.geom, b.geom)
                );
            """
            cur.execute(delete_sql)
            deleted_count = cur.rowcount
            conn.commit()

            # Get total points after deletion
            cur.execute("SELECT COUNT(*) FROM thermal_points;")
            total_after = cur.fetchone()[0]

            print(f"[SUCCESS] Deleted {deleted_count:,} row(s) falling outside India's boundary.")
            print(f"[DB] Remaining thermal points inside India: {total_after:,}")

    except Exception as exc:
        conn.rollback()
        print(f"[ERROR] Failed during cleanup: {exc}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
