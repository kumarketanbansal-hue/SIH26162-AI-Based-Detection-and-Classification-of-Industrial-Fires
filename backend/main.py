"""
FastAPI Backend for SIH 2026 Fire & Flare Monitoring System.
"""

import json
import os
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ---------------------------------------------------------------------------
# Environment & Database Configuration
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)
load_dotenv()  # Fallback to local .env if available

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "sih_fire_db")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")


@contextmanager
def get_db_connection():
    """Context manager for acquiring and closing database connections."""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
    )
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FastAPI Application Initialization
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SIH 2026 Thermal Monitoring & Flare Detection API",
    description="Backend API serving GeoJSON map layers and statistics for satellite thermal detections.",
    version="1.0.0",
)

# Enable CORS for local dev / all origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helper Functions
# ---------------------------------------------------------------------------

def serialize_value(val: Any) -> Any:
    """Convert non-serializable database types to JSON-serializable formats."""
    if isinstance(val, (date, datetime)):
        return val.isoformat()
    return val


def build_geojson_feature(geom_json_str: Optional[str], properties: Dict[str, Any]) -> Dict[str, Any]:
    """Construct a single GeoJSON Feature object."""
    geometry = json.loads(geom_json_str) if geom_json_str else None
    clean_props = {k: serialize_value(v) for k, v in properties.items()}
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": clean_props,
    }


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {
        "service": "SIH 2026 Thermal API",
        "status": "online",
        "endpoints": [
            "/api/thermal-points",
            "/api/thermal-points?hours=24",
            "/api/industrial-zones",
            "/api/power-plants",
            "/api/stats",
        ],
    }


@app.get("/api/thermal-points")
def get_thermal_points(
    hours: Optional[float] = Query(None, description="Filter to points within the last N hours (defaults to 24 if not provided)"),
    limit: int = Query(5000, description="Maximum number of rows to return", ge=1, le=100000),
):
    """
    Returns thermal detections from thermal_points table as a GeoJSON FeatureCollection.
    Optionally filter by hours based on acq_date and acq_time. Defaults to 24 hours if not specified.
    Limits maximum number of points returned (default 5000).
    """
    query = """
        SELECT
            id,
            latitude,
            longitude,
            frp,
            brightness,
            confidence,
            acq_date,
            acq_time,
            classification,
            confidence_score,
            needs_review,
            dist_to_industrial_m,
            dist_to_powerplant_m,
            recurrence_count,
            ST_AsGeoJSON(geom) AS geom_json
        FROM thermal_points
    """
    params: List[Any] = []

    # Default to 24 hours when hours parameter is not provided
    effective_hours = hours if hours is not None else 24.0

    if effective_hours > 0:
        query += """
            WHERE to_timestamp(
                acq_date::text || ' ' || LPAD(COALESCE(NULLIF(TRIM(acq_time), ''), '0000'), 4, '0'),
                'YYYY-MM-DD HH24MI'
            ) >= (NOW() AT TIME ZONE 'UTC' - (%s || ' hours')::interval)
        """
        params.append(str(effective_hours))

    query += " ORDER BY classification IS NOT NULL DESC, acq_date DESC, acq_time DESC LIMIT %s"
    params.append(limit)

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        features = []
        for row in rows:
            geom_json = row.pop("geom_json")
            features.append(build_geojson_feature(geom_json, dict(row)))

        return {
            "type": "FeatureCollection",
            "features": features,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


@app.get("/api/industrial-zones")
def get_industrial_zones():
    """
    Returns all industrial zones as a GeoJSON FeatureCollection with name, landuse, and man_made properties.
    """
    # industrial_zones table uses wkb_geometry
    query = """
        SELECT
            name,
            landuse,
            man_made,
            ST_AsGeoJSON(wkb_geometry) AS geom_json
        FROM industrial_zones
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                rows = cur.fetchall()

        features = []
        for row in rows:
            geom_json = row.pop("geom_json")
            features.append(build_geojson_feature(geom_json, dict(row)))

        return {
            "type": "FeatureCollection",
            "features": features,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


@app.get("/api/power-plants")
def get_power_plants():
    """
    Returns all power plants as a GeoJSON FeatureCollection with name, capacity_mw, and primary_fuel properties.
    """
    query = """
        SELECT
            name,
            capacity_mw,
            primary_fuel,
            ST_AsGeoJSON(geom) AS geom_json
        FROM power_plants
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                rows = cur.fetchall()

        features = []
        for row in rows:
            geom_json = row.pop("geom_json")
            features.append(build_geojson_feature(geom_json, dict(row)))

        return {
            "type": "FeatureCollection",
            "features": features,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


@app.get("/api/stats")
def get_stats():
    """
    Returns summary statistics: total thermal points, count per classification category, needs_review count, and most recent ingestion timestamp.
    """
    count_query = "SELECT COUNT(*) AS total FROM thermal_points;"
    class_query = """
        SELECT
            COALESCE(classification, 'unclassified') AS category,
            COUNT(*) AS count
        FROM thermal_points
        GROUP BY classification;
    """
    review_query = "SELECT COUNT(*) AS total_needs_review FROM thermal_points WHERE needs_review = TRUE;"
    recent_query = "SELECT MAX(inserted_at) AS most_recent_ingestion FROM thermal_points;"

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(count_query)
                total_points = cur.fetchone()["total"]

                cur.execute(class_query)
                class_rows = cur.fetchall()
                count_per_classification = {
                    row["category"]: row["count"] for row in class_rows
                }

                cur.execute(review_query)
                review_row = cur.fetchone()
                total_needs_review = review_row["total_needs_review"] if review_row else 0

                cur.execute(recent_query)
                recent_row = cur.fetchone()
                most_recent_ingestion = (
                    serialize_value(recent_row["most_recent_ingestion"])
                    if recent_row and recent_row["most_recent_ingestion"]
                    else None
                )

        return {
            "total_thermal_points": total_points,
            "count_per_classification": count_per_classification,
            "total_needs_review": total_needs_review,
            "most_recent_ingestion": most_recent_ingestion,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
