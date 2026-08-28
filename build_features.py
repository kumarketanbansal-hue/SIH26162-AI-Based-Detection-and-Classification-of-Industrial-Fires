"""
build_features.py
-----------------
Enriches every row in thermal_points with three spatial feature columns:

    dist_to_industrial_m  DOUBLE PRECISION
        Distance in metres from the thermal point to the nearest polygon in
        industrial_zones, computed with ST_Distance(::geography) via KNN index scan.

    dist_to_powerplant_m  DOUBLE PRECISION
        Distance in metres from the thermal point to the nearest point in
        power_plants, computed with ST_Distance(::geography) via KNN index scan.

    recurrence_count      INTEGER
        Number of OTHER thermal_points rows within ~500m (0.0045 deg),
        computed efficiently via PostGIS ST_ClusterDBSCAN (single-pass indexed clustering).

Usage:
    python build_features.py

Environment variables (loaded from .env):
    DB_PASSWORD  - PostgreSQL password for user 'postgres'
    DB_HOST      - PostgreSQL host (default: localhost)
"""

import os
import sys
import time

import psycopg2
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sih_fire_db")
DB_USER = os.getenv("DB_USER", "postgres")

# ---------------------------------------------------------------------------
# SQL statements
# ---------------------------------------------------------------------------

# Add columns idempotently (PostgreSQL 9.6+)
ADD_COLUMNS_SQL = [
    """
    ALTER TABLE thermal_points
        ADD COLUMN IF NOT EXISTS dist_to_industrial_m DOUBLE PRECISION;
    """,
    """
    ALTER TABLE thermal_points
        ADD COLUMN IF NOT EXISTS dist_to_powerplant_m DOUBLE PRECISION;
    """,
    """
    ALTER TABLE thermal_points
        ADD COLUMN IF NOT EXISTS recurrence_count INTEGER;
    """,
]

# 1. PostGIS ST_ClusterDBSCAN recurrence count computation
# eps = 0.0045 deg (~500m at Indian latitudes), minpoints = 2 (noise points get cluster_id = NULL)
UPDATE_RECURRENCE_DBSCAN_SQL = """
WITH clustered AS (
    SELECT
        id,
        ST_ClusterDBSCAN(geom, eps := 0.0045, minpoints := 2) OVER () AS cluster_id
    FROM thermal_points
),
cluster_sizes AS (
    SELECT
        cluster_id,
        COUNT(*)::integer AS sz
    FROM clustered
    WHERE cluster_id IS NOT NULL
    GROUP BY cluster_id
)
UPDATE thermal_points AS tp
SET recurrence_count = COALESCE(cs.sz - 1, 0)
FROM clustered AS cl
LEFT JOIN cluster_sizes AS cs ON cs.cluster_id = cl.cluster_id
WHERE tp.id = cl.id;
"""

# 2. KNN (<->) nearest facility distance computation
UPDATE_KNN_DISTANCES_SQL = """
UPDATE thermal_points AS tp
SET
    dist_to_industrial_m = (
        SELECT ST_Distance(tp.geom::geography, iz.wkb_geometry::geography)
        FROM   industrial_zones AS iz
        ORDER  BY tp.geom <-> iz.wkb_geometry  -- KNN index scan for nearest
        LIMIT  1
    ),

    dist_to_powerplant_m = (
        SELECT ST_Distance(tp.geom::geography, pp.geom::geography)
        FROM   power_plants AS pp
        ORDER  BY tp.geom <-> pp.geom          -- KNN index scan for nearest
        LIMIT  1
    );
"""

ANALYZE_SQL = """
ANALYZE thermal_points;
ANALYZE industrial_zones;
ANALYZE power_plants;
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
    """Return an open psycopg2 connection to sih_fire_db."""
    return psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=db_password,
    )


def add_columns(cur):
    """Add the three feature columns to thermal_points if they don't exist."""
    for sql in ADD_COLUMNS_SQL:
        cur.execute(sql)
    print("[DDL] Feature columns ensured (dist_to_industrial_m, "
          "dist_to_powerplant_m, recurrence_count).")


def ensure_spatial_indexes(cur):
    """
    Create GIST spatial indexes on the tables used in the UPDATE if they are
    missing. These are required for the KNN (<->) operator and DBSCAN clustering.
    """
    indexes = [
        ("idx_thermal_points_geom",
         "CREATE INDEX IF NOT EXISTS idx_thermal_points_geom "
         "ON thermal_points USING GIST (geom);"),
        ("idx_power_plants_geom",
         "CREATE INDEX IF NOT EXISTS idx_power_plants_geom "
         "ON power_plants USING GIST (geom);"),
    ]
    for name, sql in indexes:
        cur.execute(sql)
        print(f"[DDL] Index ensured: {name}")


def analyze_tables(cur):
    """
    Run ANALYZE on thermal_points, industrial_zones, and power_plants
    to ensure the PostgreSQL query planner has current table statistics.
    """
    print("[ANALYZE] Running ANALYZE on thermal_points, industrial_zones, power_plants ...")
    t0 = time.perf_counter()
    cur.execute(ANALYZE_SQL)
    elapsed = time.perf_counter() - t0
    print(f"[ANALYZE] Done in {elapsed:.2f}s.")
    return elapsed


def update_recurrence_dbscan(cur):
    """
    Compute recurrence_count using PostGIS ST_ClusterDBSCAN.
    """
    print("[DBSCAN] Computing recurrence counts via ST_ClusterDBSCAN (eps=0.0045, minpoints=2) ...")
    t0 = time.perf_counter()
    cur.execute(UPDATE_RECURRENCE_DBSCAN_SQL)
    elapsed = time.perf_counter() - t0
    rows_updated = cur.rowcount
    print(f"[DBSCAN] Done in {elapsed:.2f}s ({rows_updated} rows updated).")
    return rows_updated, elapsed


def update_knn_distances(cur):
    """
    Compute dist_to_industrial_m and dist_to_powerplant_m using KNN (<->) index scans.
    """
    print("[KNN] Computing nearest industrial zone and power plant distances ...")
    t0 = time.perf_counter()
    cur.execute(UPDATE_KNN_DISTANCES_SQL)
    elapsed = time.perf_counter() - t0
    rows_updated = cur.rowcount
    print(f"[KNN] Done in {elapsed:.2f}s ({rows_updated} rows updated).")
    return rows_updated, elapsed


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    db_password = load_env()

    # 1. Connect
    print(f"[DB] Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT} ...")
    try:
        conn = get_connection(db_password)
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Could not connect to the database: {exc}")
        sys.exit(1)

    conn.autocommit = False

    try:
        with conn.cursor() as cur:
            # 2. Add columns
            add_columns(cur)

            # 3. Ensure GIST indexes for spatial queries
            ensure_spatial_indexes(cur)
            conn.commit()

            # 4. Refresh planner statistics
            analyze_elapsed = analyze_tables(cur)
            conn.commit()

            # 5. Step A: DBSCAN Recurrence Count (Single-pass indexed clustering)
            rec_rows, dbscan_elapsed = update_recurrence_dbscan(cur)
            conn.commit()

            # 6. Step B: KNN Facility Distances
            dist_rows, knn_elapsed = update_knn_distances(cur)
            conn.commit()

    except Exception as exc:
        conn.rollback()
        print(f"[ERROR] An error occurred, transaction rolled back: {exc}")
        raise
    finally:
        conn.close()

    # 7. Summary
    total_time = analyze_elapsed + dbscan_elapsed + knn_elapsed
    print("\n-- Feature build summary --------------------------------------")
    print(f"  Rows updated       : {rec_rows}")
    print(f"  ANALYZE time       : {analyze_elapsed:.2f}s")
    print(f"  DBSCAN cluster time: {dbscan_elapsed:.2f}s (recurrence_count)")
    print(f"  KNN distance time  : {knn_elapsed:.2f}s (dist_to_industrial, dist_to_powerplant)")
    print(f"  Total pipeline time: {total_time:.2f}s")
    print("---------------------------------------------------------------")


if __name__ == "__main__":
    main()
