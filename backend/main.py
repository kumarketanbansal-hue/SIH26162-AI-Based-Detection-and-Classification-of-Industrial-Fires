"""
FastAPI Backend for SIH 2026 Fire & Flare Monitoring System.
"""

import asyncio
import json
import os
import smtplib
import traceback
import urllib.parse
from contextlib import asynccontextmanager, contextmanager
from datetime import date, datetime
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

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
DB_SSLMODE = os.getenv("DB_SSLMODE", "prefer")

ALERT_EMAIL_ADDRESS = os.getenv("ALERT_EMAIL_ADDRESS", "")
ALERT_EMAIL_APP_PASSWORD = os.getenv("ALERT_EMAIL_APP_PASSWORD", "")
TIER_0_EMAIL = os.getenv("TIER_0_EMAIL", "")
TIER_0_NAME = os.getenv("TIER_0_NAME", "")
TIER_1_EMAIL = os.getenv("TIER_1_EMAIL", "")
TIER_1_NAME = os.getenv("TIER_1_NAME", "")
TIER_2_EMAIL = os.getenv("TIER_2_EMAIL", "")
TIER_2_NAME = os.getenv("TIER_2_NAME", "")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").strip().rstrip("/")

# Strong reference set for background asyncio tasks to prevent premature garbage collection
_background_tasks: Set[asyncio.Task] = set()



@contextmanager
def get_db_connection():
    """Context manager for acquiring and closing database connections."""
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        sslmode=DB_SSLMODE,
    )
    try:
        yield conn
    finally:
        conn.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Ensure incidents table exists on backend startup if DB is accessible."""
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS incidents (
                        id SERIAL PRIMARY KEY,
                        latitude DOUBLE PRECISION,
                        longitude DOUBLE PRECISION,
                        frp DOUBLE PRECISION,
                        severity_level INTEGER,
                        current_tier INTEGER DEFAULT 0,
                        status TEXT DEFAULT 'pending',
                        created_at TIMESTAMP DEFAULT NOW(),
                        acknowledged_at TIMESTAMP NULL,
                        acknowledged_by TEXT NULL,
                        tier_0_sent_at TIMESTAMP NULL,
                        tier_1_sent_at TIMESTAMP NULL,
                        tier_2_sent_at TIMESTAMP NULL,
                        source_thermal_point_id INTEGER NULL,
                        classification TEXT NULL,
                        confidence TEXT NULL,
                        alert_mode TEXT DEFAULT 'sequential'
                    );
                    CREATE INDEX IF NOT EXISTS idx_incidents_created_at ON incidents (created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents (status);
                    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS source_thermal_point_id INTEGER NULL;
                    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS classification TEXT NULL;
                    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS confidence TEXT NULL;
                    ALTER TABLE incidents ADD COLUMN IF NOT EXISTS alert_mode TEXT DEFAULT 'sequential';
                """)
                conn.commit()
    except Exception as exc:
        print(f"[DB] Startup table initialization note: {exc}")
    yield


# ---------------------------------------------------------------------------
# FastAPI Application Initialization
# ---------------------------------------------------------------------------

app = FastAPI(
    title="SIH 2026 Thermal Monitoring & Flare Detection API",
    description="Backend API serving GeoJSON map layers, incident escalation, and statistics for satellite thermal detections.",
    version="1.0.0",
    lifespan=lifespan,
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
# Helper Functions & Models
# ---------------------------------------------------------------------------

def serialize_value(val: Any) -> Any:
    """Convert non-serializable database types to JSON-serializable formats."""
    if isinstance(val, datetime):
        iso_str = val.isoformat()
        if not iso_str.endswith("Z") and "+" not in iso_str:
            return iso_str + "Z"
        return iso_str
    if isinstance(val, date):
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


def compute_severity(frp: float) -> int:
    """
    Computes severity level based on Fire Radiative Power (FRP):
    - returns 1 if frp < 50
    - returns 2 if frp < 200
    - returns 3 if frp < 500
    - else 4
    """
    if frp < 50:
        return 1
    elif frp < 200:
        return 2
    elif frp < 500:
        return 3
    else:
        return 4


def send_alert_email(tier_name: str, tier_email: str, incident: Dict[str, Any]):
    """
    Connects to Gmail via smtplib.SMTP_SSL('smtp.gmail.com', 465), logs in using
    ALERT_EMAIL_ADDRESS and ALERT_EMAIL_APP_PASSWORD from .env, and sends a plain text
    email to tier_email with subject 'FIRE ALERT - Tier {N} - Severity {level}' and a body
    containing latitude, longitude, FRP, severity level, incident id, and a clickable
    acknowledgement link using PUBLIC_BASE_URL.
    """
    tier_num = incident.get("current_tier", 0)
    severity = incident.get("severity_level", 1)
    incident_id = incident.get("id")
    lat = incident.get("latitude")
    lon = incident.get("longitude")
    frp = incident.get("frp")

    ack_name_param = urllib.parse.quote(tier_name) if tier_name else "Email%20Link"
    ack_url = f"{PUBLIC_BASE_URL}/api/incidents/{incident_id}/ack?name={ack_name_param}"

    subject = f"FIRE ALERT - Tier {tier_num} - Severity {severity}"
    body = (
        f"FIRE INCIDENT ALERT\n"
        f"-------------------\n"
        f"Tier: Tier {tier_num} ({tier_name})\n"
        f"Incident ID: {incident_id}\n"
        f"Severity Level: {severity}\n"
        f"Latitude: {lat}\n"
        f"Longitude: {lon}\n"
        f"FRP: {frp}\n"
        f"Status: {incident.get('status', 'pending')}\n\n"
        f"Click here to acknowledge this alert:\n"
        f"{ack_url}\n\n"
        f"Please take prompt action or click the link above to acknowledge this incident."
    )

    if not ALERT_EMAIL_ADDRESS or not ALERT_EMAIL_APP_PASSWORD:
        print(f"[ALERT EMAIL] Warning: ALERT_EMAIL_ADDRESS or ALERT_EMAIL_APP_PASSWORD not set. Skipping email to {tier_email}.")
        return

    if not tier_email:
        print(f"[ALERT EMAIL] Warning: Recipient email for Tier {tier_num} ({tier_name}) is not set. Skipping.")
        return

    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = ALERT_EMAIL_ADDRESS
        msg["To"] = tier_email

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(ALERT_EMAIL_ADDRESS, ALERT_EMAIL_APP_PASSWORD)
            server.sendmail(ALERT_EMAIL_ADDRESS, [tier_email], msg.as_string())
        print(f"[ALERT EMAIL] Successfully sent Tier {tier_num} alert email to {tier_name} ({tier_email}) for incident #{incident_id}.")
    except Exception as exc:
        print(f"[ALERT EMAIL] Error sending alert email to {tier_email}: {exc}")


async def escalate_incident_task(incident_id: int):
    """
    Background escalation task:
    - Waits 60 seconds (1 minute)
    - Checks if incident's status is still 'pending'
    - If so, sends Tier 1 email, updates current_tier and tier_1_sent_at
    - Waits another 60 seconds (1 minute)
    - Checks again, and if still pending, sends Tier 2 email and updates current_tier and tier_2_sent_at
    - (Tier 2 is the final tier, no further escalation after that)
    """
    try:
        print(f"[ESCALATION] Started background escalation task for Incident #{incident_id} (60s interval)")

        # Wait 60 seconds before escalating to Tier 1
        await asyncio.sleep(60)

        # Step 1: Check & Escalate to Tier 1
        updated_incident_tier1 = None
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM incidents WHERE id = %s;", (incident_id,))
                incident = cur.fetchone()
                if not incident or incident["status"] != "pending":
                    print(f"[ESCALATION] Incident #{incident_id} is not pending (status: {incident.get('status') if incident else 'not found'}). Stopping escalation.")
                    return

                cur.execute(
                    """
                    UPDATE incidents
                    SET current_tier = 1,
                        tier_1_sent_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    RETURNING *;
                    """,
                    (incident_id,),
                )
                conn.commit()
                row = cur.fetchone()
                if row:
                    updated_incident_tier1 = dict(row)

        if not updated_incident_tier1:
            print(f"[ESCALATION] Incident #{incident_id} was acknowledged or modified during Tier 1 update. Stopping.")
            return

        print(f"[ESCALATION] Incident #{incident_id} escalated to Tier 1 (Facility Supervisor). Dispatching email...")
        send_alert_email(TIER_1_NAME, TIER_1_EMAIL, updated_incident_tier1)

        # Wait another 60 seconds before escalating to Tier 2
        await asyncio.sleep(60)

        # Step 2: Check & Escalate to Tier 2 (Final Tier)
        updated_incident_tier2 = None
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM incidents WHERE id = %s;", (incident_id,))
                incident = cur.fetchone()
                if not incident or incident["status"] != "pending":
                    print(f"[ESCALATION] Incident #{incident_id} is not pending (status: {incident.get('status') if incident else 'not found'}). Stopping escalation.")
                    return

                cur.execute(
                    """
                    UPDATE incidents
                    SET current_tier = 2,
                        tier_2_sent_at = NOW()
                    WHERE id = %s AND status = 'pending'
                    RETURNING *;
                    """,
                    (incident_id,),
                )
                conn.commit()
                row = cur.fetchone()
                if row:
                    updated_incident_tier2 = dict(row)

        if not updated_incident_tier2:
            print(f"[ESCALATION] Incident #{incident_id} was acknowledged or modified during Tier 2 update. Stopping.")
            return

        print(f"[ESCALATION] Incident #{incident_id} escalated to Tier 2 (District Command). Dispatching email...")
        send_alert_email(TIER_2_NAME, TIER_2_EMAIL, updated_incident_tier2)
        print(f"[ESCALATION] Completed escalation lifecycle for Incident #{incident_id}.")

    except Exception as exc:
        print(f"[ESCALATION ERROR] Exception in escalate_incident_task for Incident #{incident_id}: {exc}")
        traceback.print_exc()



class SimulateIncidentRequest(BaseModel):
    thermal_point_id: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    frp: Optional[float] = None
    confidence: Optional[str] = None


class AcknowledgeIncidentRequest(BaseModel):
    acknowledged_by: str


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
            "/api/incidents",
            "/api/incidents/simulate",
            "/api/incidents/{incident_id}/acknowledge",
            "/api/incidents/{incident_id}/ack",
        ],
    }


@app.get("/api/thermal-points")
def get_thermal_points(
    hours: Optional[float] = Query(None, description="Filter to points within the last N hours (defaults to 24 if not provided)"),
    limit: int = Query(4000, description="Maximum number of rows to return", ge=1, le=100000),
):
    """
    Returns thermal detections from thermal_points table as a GeoJSON FeatureCollection.
    Optionally filter by hours based on acq_date and acq_time. Defaults to 24 hours if not specified.
    Limits maximum number of points returned (default 4000).
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

    query += " ORDER BY needs_review DESC, classification IS NOT NULL DESC, acq_date DESC, acq_time DESC LIMIT %s"
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


# ---------------------------------------------------------------------------
# Incident Escalation Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/incidents/simulate")
async def simulate_incident(payload: Optional[SimulateIncidentRequest] = None):
    """
    Simulates or triggers a fire incident. Accepts optional {thermal_point_id, latitude, longitude, frp, confidence}.
    If thermal_point_id is provided, creates an incident for that specific thermal point.
    Defaults to a random real thermal_points row if not provided.
    Rejects Persistent Industrial Source points with HTTP 400.
    Branches dispatch based on VIIRS detection confidence:
    - 'l' or 'n' (Low / Nominal): sequential escalation (Tier 0 at 0s, Tier 1 at 30s, Tier 2 at 60s).
    - 'h' (High): parallel dispatch (Tier 0, 1, 2 fired simultaneously at 0s).
    """
    target_tp_id = payload.thermal_point_id if payload else None
    lat = payload.latitude if payload else None
    lon = payload.longitude if payload else None
    frp = payload.frp if payload else None
    confidence = payload.confidence if payload else None
    source_thermal_point_id = target_tp_id
    classification = None

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                tp_meta = {}
                if target_tp_id is not None:
                    # Targeted trigger for a specific thermal point
                    cur.execute(
                        """
                        SELECT
                            id, latitude, longitude, frp, classification, confidence,
                            brightness, acq_date, acq_time, confidence_score, needs_review,
                            dist_to_industrial_m, dist_to_powerplant_m, recurrence_count
                        FROM thermal_points
                        WHERE id = %s;
                        """,
                        (target_tp_id,),
                    )
                    tp = cur.fetchone()
                    if not tp:
                        raise HTTPException(status_code=404, detail=f"Thermal point #{target_tp_id} not found.")

                    classification = tp.get("classification")
                    # Server-side guard: reject Persistent Industrial Source
                    if classification and classification.strip().lower() == "persistent industrial source":
                        raise HTTPException(
                            status_code=400,
                            detail="Cannot trigger incident response for Persistent Industrial Source (known/regulated industrial thermal signature)."
                        )

                    # Check for active unacknowledged duplicate incident
                    cur.execute(
                        "SELECT id FROM incidents WHERE source_thermal_point_id = %s AND status = 'pending';",
                        (target_tp_id,),
                    )
                    existing_active = cur.fetchone()
                    if existing_active:
                        raise HTTPException(
                            status_code=400,
                            detail=f"An active incident (#{existing_active['id']}) already exists for this thermal point."
                        )

                    lat = float(tp["latitude"]) if lat is None else lat
                    lon = float(tp["longitude"]) if lon is None else lon
                    frp = float(tp["frp"]) if frp is None else frp
                    source_thermal_point_id = tp.get("id")
                    if confidence is None:
                        confidence = tp.get("confidence")

                    tp_meta = {
                        "brightness": tp.get("brightness"),
                        "acq_date": tp.get("acq_date"),
                        "acq_time": tp.get("acq_time"),
                        "confidence_score": tp.get("confidence_score"),
                        "needs_review": tp.get("needs_review"),
                        "dist_to_industrial_m": tp.get("dist_to_industrial_m"),
                        "dist_to_powerplant_m": tp.get("dist_to_powerplant_m"),
                        "recurrence_count": tp.get("recurrence_count"),
                    }
                elif lat is None or lon is None or frp is None:
                    cur.execute(
                        """
                        SELECT
                            id, latitude, longitude, frp, classification, confidence,
                            brightness, acq_date, acq_time, confidence_score, needs_review,
                            dist_to_industrial_m, dist_to_powerplant_m, recurrence_count
                        FROM thermal_points
                        WHERE classification != 'Persistent Industrial Source' OR classification IS NULL
                        ORDER BY RANDOM()
                        LIMIT 1;
                        """
                    )
                    tp = cur.fetchone()
                    if tp:
                        lat = lat if lat is not None else float(tp["latitude"])
                        lon = lon if lon is not None else float(tp["longitude"])
                        frp = frp if frp is not None else float(tp["frp"])
                        source_thermal_point_id = tp.get("id")
                        classification = tp.get("classification")
                        if confidence is None:
                            confidence = tp.get("confidence")
                        tp_meta = {
                            "brightness": tp.get("brightness"),
                            "acq_date": tp.get("acq_date"),
                            "acq_time": tp.get("acq_time"),
                            "confidence_score": tp.get("confidence_score"),
                            "needs_review": tp.get("needs_review"),
                            "dist_to_industrial_m": tp.get("dist_to_industrial_m"),
                            "dist_to_powerplant_m": tp.get("dist_to_powerplant_m"),
                            "recurrence_count": tp.get("recurrence_count"),
                        }
                    else:
                        # Fallback defaults if thermal_points is empty
                        lat = lat if lat is not None else 21.1458
                        lon = lon if lon is not None else 79.0882
                        frp = frp if frp is not None else 120.0
                        if confidence is None:
                            confidence = "n"

                # Server-side guard on manual payload
                if classification and classification.strip().lower() == "persistent industrial source":
                    raise HTTPException(
                        status_code=400,
                        detail="Cannot trigger emergency escalation for Persistent Industrial Source."
                    )

                severity = compute_severity(float(frp))
                conf_str = (str(confidence).strip().lower() if confidence else "n")
                is_high_confidence = conf_str in ("h", "high")
                alert_mode = "parallel" if is_high_confidence else "sequential"

                if is_high_confidence:
                    # Parallel dispatch mode: all tiers dispatched immediately
                    cur.execute(
                        """
                        INSERT INTO incidents (
                            latitude,
                            longitude,
                            frp,
                            severity_level,
                            current_tier,
                            status,
                            tier_0_sent_at,
                            tier_1_sent_at,
                            tier_2_sent_at,
                            source_thermal_point_id,
                            classification,
                            confidence,
                            alert_mode
                        ) VALUES (%s, %s, %s, %s, 2, 'pending', NOW(), NOW(), NOW(), %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (lat, lon, frp, severity, source_thermal_point_id, classification, confidence, alert_mode),
                    )
                else:
                    # Sequential dispatch mode: Tier 0 first, escalates if unacknowledged
                    cur.execute(
                        """
                        INSERT INTO incidents (
                            latitude,
                            longitude,
                            frp,
                            severity_level,
                            current_tier,
                            status,
                            tier_0_sent_at,
                            source_thermal_point_id,
                            classification,
                            confidence,
                            alert_mode
                        ) VALUES (%s, %s, %s, %s, 0, 'pending', NOW(), %s, %s, %s, %s)
                        RETURNING *;
                        """,
                        (lat, lon, frp, severity, source_thermal_point_id, classification, confidence, alert_mode),
                    )
                conn.commit()
                incident = dict(cur.fetchone())
                incident.update({k: v for k, v in tp_meta.items() if k not in incident or incident[k] is None})

        if is_high_confidence:
            print("[ALERT] VIIRS confidence=HIGH — dispatching Tier 0, Tier 1, Tier 2 simultaneously via Gmail")
            tier0_data = dict(incident, current_tier=0)
            tier1_data = dict(incident, current_tier=1)
            tier2_data = dict(incident, current_tier=2)
            send_alert_email(TIER_0_NAME, TIER_0_EMAIL, tier0_data)
            send_alert_email(TIER_1_NAME, TIER_1_EMAIL, tier1_data)
            send_alert_email(TIER_2_NAME, TIER_2_EMAIL, tier2_data)
        else:
            # Immediately call send_alert_email for Tier 0
            send_alert_email(TIER_0_NAME, TIER_0_EMAIL, incident)

            # Start background escalation task and hold a strong reference
            task = asyncio.create_task(escalate_incident_task(incident["id"]))
            _background_tasks.add(task)
            task.add_done_callback(_background_tasks.discard)

        return {k: serialize_value(v) for k, v in incident.items()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to simulate incident: {str(exc)}")


@app.post("/api/incidents/reset")
@app.delete("/api/incidents")
def reset_incidents():
    """
    Clears all incident history and resets the auto-incrementing ID sequence to 1.
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE incidents RESTART IDENTITY;")
                conn.commit()
        return {"status": "success", "message": "All incidents cleared. Next incident will start at ID #1."}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to reset incidents: {str(exc)}")


@app.post("/api/incidents/{incident_id}/acknowledge")
def acknowledge_incident(incident_id: int, payload: AcknowledgeIncidentRequest):
    """
    Sets incident status to 'acknowledged', acknowledged_at to now,
    and acknowledged_by to the given name via API.
    """
    if not payload.acknowledged_by or not payload.acknowledged_by.strip():
        raise HTTPException(status_code=400, detail="acknowledged_by cannot be empty.")

    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'acknowledged',
                        acknowledged_at = NOW(),
                        acknowledged_by = %s
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (payload.acknowledged_by.strip(), incident_id),
                )
                conn.commit()
                row = cur.fetchone()
                if not row:
                    raise HTTPException(status_code=404, detail=f"Incident with id {incident_id} not found.")

                return {k: serialize_value(v) for k, v in dict(row).items()}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


@app.get("/api/incidents/{incident_id}/ack", response_class=HTMLResponse)
def acknowledge_incident_via_link(incident_id: int, name: str = Query("Email Link")):
    """
    Click-to-acknowledge endpoint for plain email links.
    Sets incident status to 'acknowledged', acknowledged_at to now,
    acknowledged_by to query parameter 'name' (default 'Email Link'),
    and returns a friendly HTML confirmation page.
    """
    ack_name = name.strip() if name and name.strip() else "Email Link"
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE incidents
                    SET status = 'acknowledged',
                        acknowledged_at = COALESCE(acknowledged_at, NOW()),
                        acknowledged_by = COALESCE(NULLIF(acknowledged_by, ''), %s)
                    WHERE id = %s
                    RETURNING *;
                    """,
                    (ack_name, incident_id),
                )
                conn.commit()
                row = cur.fetchone()
                if not row:
                    html_not_found = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incident Not Found</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 36px;
            max-width: 480px;
            text-align: center;
            box-shadow: 0 10px 25px rgba(0,0,0,0.4);
        }}
        h1 {{ color: #ef4444; margin-bottom: 12px; font-size: 24px; }}
        p {{ color: #94a3b8; font-size: 16px; line-height: 1.5; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>Incident Not Found</h1>
        <p>Incident #{incident_id} could not be found in the database.</p>
    </div>
</body>
</html>"""
                    return HTMLResponse(content=html_not_found, status_code=404)

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Incident Acknowledged</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background: #0f172a;
            color: #f8fafc;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            margin: 0;
            padding: 20px;
            box-sizing: border-box;
        }}
        .card {{
            background: #1e293b;
            border: 1px solid #334155;
            border-radius: 16px;
            padding: 40px;
            max-width: 480px;
            text-align: center;
            box-shadow: 0 10px 25px rgba(0,0,0,0.5);
        }}
        .badge {{
            display: inline-block;
            background: rgba(34, 197, 94, 0.15);
            color: #22c55e;
            border: 1px solid rgba(34, 197, 94, 0.3);
            border-radius: 9999px;
            padding: 6px 16px;
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        h1 {{
            color: #f8fafc;
            margin: 0 0 12px 0;
            font-size: 28px;
            font-weight: 700;
        }}
        p {{
            color: #94a3b8;
            font-size: 16px;
            line-height: 1.6;
            margin: 0 0 20px 0;
        }}
        .meta {{
            background: #0f172a;
            border-radius: 8px;
            padding: 12px;
            font-size: 14px;
            color: #cbd5e1;
            margin-bottom: 20px;
        }}
        .hint {{
            font-size: 13px;
            color: #64748b;
            margin: 0;
        }}
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">✓ Acknowledged</div>
        <h1>Incident Acknowledged</h1>
        <p>You marked this fire incident (<strong>#{incident_id}</strong>) as acknowledged by <strong>{ack_name}</strong>.</p>
        <div class="meta">
            Status: <strong>Acknowledged</strong> | Escalation: <strong>Halted</strong>
        </div>
        <p class="hint">You can safely close this browser tab.</p>
    </div>
</body>
</html>"""
        return HTMLResponse(content=html_content, status_code=200)
    except Exception as exc:
        err_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Error</title>
    <style>
        body {{ font-family: sans-serif; background: #0f172a; color: #fff; text-align: center; padding: 50px; }}
        h1 {{ color: #ef4444; }}
    </style>
</head>
<body>
    <h1>Error Acknowledging Incident</h1>
    <p>{exc}</p>
</body>
</html>"""
        return HTMLResponse(content=err_html, status_code=500)


@app.get("/api/incidents")
def get_incidents():
    """
    Returns all incidents ordered by created_at descending as a JSON list of plain objects including all columns
    joined with source thermal_points details (brightness, dist_to_industrial_m, dist_to_powerplant_m, recurrence_count, acq_date, acq_time).
    """
    query = """
        SELECT
            i.id,
            i.latitude,
            i.longitude,
            i.frp,
            i.severity_level,
            i.current_tier,
            i.status,
            i.created_at,
            i.acknowledged_at,
            i.acknowledged_by,
            i.tier_0_sent_at,
            i.tier_1_sent_at,
            i.tier_2_sent_at,
            i.source_thermal_point_id,
            COALESCE(i.classification, tp.classification) AS classification,
            COALESCE(i.confidence, tp.confidence) AS confidence,
            i.alert_mode,
            tp.brightness,
            tp.acq_date,
            tp.acq_time,
            tp.confidence_score,
            tp.needs_review,
            tp.dist_to_industrial_m,
            tp.dist_to_powerplant_m,
            tp.recurrence_count
        FROM incidents i
        LEFT JOIN thermal_points tp ON i.source_thermal_point_id = tp.id
        ORDER BY i.created_at DESC;
    """
    try:
        with get_db_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query)
                rows = cur.fetchall()

        return [{k: serialize_value(v) for k, v in dict(row).items()} for row in rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Database error: {str(exc)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
