"""
train_classifier.py
-------------------
Loads thermal fire detection data from sih_fire_db, generates rule-based
training labels, engineers temporal and satellite features, trains a
Random Forest classifier, evaluates it, saves the model, and writes model
predictions, confidence scores, and review flags back into thermal_points.

Usage:
    python train_classifier.py

Environment variables (loaded from .env):
    DB_PASSWORD  - PostgreSQL password for user 'postgres'
    DB_HOST      - PostgreSQL host (default: localhost)

Output files:
    model.pkl    - Trained RandomForestClassifier (scikit-learn, via joblib)
"""

import os
import sys

import joblib
import numpy as np
import pandas as pd
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import classification_report
from sklearn.model_selection import train_test_split

load_dotenv()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = 5432
DB_NAME = "sih_fire_db"
DB_USER = "postgres"

MODEL_PATH = os.path.join(os.path.dirname(__file__), "model.pkl")

FEATURES = [
    "frp",
    "brightness",
    "confidence",
    "dist_to_industrial_m",
    "dist_to_powerplant_m",
    "recurrence_count",
    "hour_of_day",
    "month",
    "satellite_encoded",
    "is_night",
]

# Label strings (also used as class names in the report)
LABEL_PERSISTENT  = "Persistent Industrial Source"
LABEL_UNPLANNED   = "Unplanned Industrial Fire"
LABEL_WILDFIRE    = "Wildfire / Other Biomass Burning"

# ---------------------------------------------------------------------------
# DB helpers
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


def load_dataframe(conn):
    """
    Load all rows from thermal_points into a pandas DataFrame,
    including raw spatial, temporal, and satellite metadata columns.
    """
    query = """
        SELECT
            id,
            frp,
            brightness,
            confidence,
            dist_to_industrial_m,
            dist_to_powerplant_m,
            recurrence_count,
            satellite,
            daynight,
            acq_time,
            acq_date
        FROM thermal_points
        WHERE dist_to_industrial_m IS NOT NULL
          AND dist_to_powerplant_m IS NOT NULL
          AND recurrence_count     IS NOT NULL;
    """
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query)
        rows = cur.fetchall()

    df = pd.DataFrame(rows)
    print(f"[DATA] Loaded {len(df)} rows from thermal_points.")
    return df


def ensure_prediction_columns(cur):
    """Ensure confidence_score and needs_review columns exist on thermal_points."""
    cur.execute("""
        ALTER TABLE thermal_points
            ADD COLUMN IF NOT EXISTS confidence_score DOUBLE PRECISION,
            ADD COLUMN IF NOT EXISTS needs_review BOOLEAN;
    """)


def write_predictions(conn, ids, predictions, confidence_scores, needs_review_flags):
    """
    Bulk-update thermal_points with classification, confidence_score, and needs_review
    flags for every row.
    """
    update_sql = """
        UPDATE thermal_points
        SET    classification   = %(label)s,
               confidence_score = %(score)s,
               needs_review     = %(needs_review)s
        WHERE  id               = %(id)s;
    """
    params = [
        {
            "id": int(i),
            "label": str(lbl),
            "score": float(score),
            "needs_review": bool(nr),
        }
        for i, lbl, score, nr in zip(ids, predictions, confidence_scores, needs_review_flags)
    ]

    with conn.cursor() as cur:
        ensure_prediction_columns(cur)
        cur.executemany(update_sql, params)
    conn.commit()
    print(f"[DB] Wrote classification, confidence scores, and review flags for {len(params)} rows.")


# ---------------------------------------------------------------------------
# Labelling (Unchanged - rule-based training labels)
# ---------------------------------------------------------------------------

def label_row(row):
    """
    Rule-based training label for a single thermal detection row.

    Returns one of:
        "Persistent Industrial Source"
        "Unplanned Industrial Fire"
        "Wildfire / Other Biomass Burning"
    """
    near_industry = (
        row["dist_to_industrial_m"] < 500 or
        row["dist_to_powerplant_m"] < 500
    )
    near_industry_wide = (
        row["dist_to_industrial_m"] < 2000 or
        row["dist_to_powerplant_m"] < 2000
    )
    recurring = row["recurrence_count"] >= 2

    if near_industry and recurring:
        return LABEL_PERSISTENT
    if near_industry_wide and not recurring:
        return LABEL_UNPLANNED
    return LABEL_WILDFIRE


# ---------------------------------------------------------------------------
# Feature Engineering Helpers
# ---------------------------------------------------------------------------

def encode_confidence(df):
    """
    Map VIIRS confidence text values to numeric for the model.
    'l' -> 0, 'n' -> 1, 'h' -> 2, anything else -> 1 (nominal).
    """
    mapping = {"l": 0, "n": 1, "h": 2}
    df["confidence"] = (
        df["confidence"]
        .astype(str)
        .str.lower()
        .str.strip()
        .map(mapping)
        .fillna(1)
        .astype(int)
    )
    return df


def engineer_temporal_features(df):
    """
    Create new temporal and satellite feature columns from raw metadata:
      - hour_of_day: extracted as integer from acq_time (HHMM format -> HH)
      - month: extracted as integer from acq_date
      - satellite_encoded: unique satellite identifier mapped to integer
      - is_night: 1 if daynight == 'N', else 0
    """
    # 1. hour_of_day from acq_time (e.g. "1430" -> 14, "0345" -> 3)
    def parse_hour(val):
        if pd.isna(val) or val is None:
            return 0
        s = str(val).strip().zfill(4)
        try:
            return int(s[:2])
        except (ValueError, TypeError):
            return 0

    df["hour_of_day"] = df["acq_time"].apply(parse_hour)

    # 2. month from acq_date (1 to 12)
    df["month"] = pd.to_datetime(df["acq_date"], errors="coerce").dt.month.fillna(1).astype(int)

    # 3. satellite_encoded (label encode satellite name)
    unique_sats = sorted(df["satellite"].dropna().astype(str).unique())
    sat_map = {sat: idx for idx, sat in enumerate(unique_sats)}
    df["satellite_encoded"] = df["satellite"].astype(str).map(sat_map).fillna(0).astype(int)

    # 4. is_night (1 if 'N', else 0)
    df["is_night"] = (df["daynight"].astype(str).str.upper().str.strip() == "N").astype(int)

    return df


def prepare_features(df):
    """Return X (feature matrix) and the row ids, after cleaning and feature engineering."""
    df = df.copy()
    df = encode_confidence(df)
    df = engineer_temporal_features(df)
    df[FEATURES] = df[FEATURES].apply(pd.to_numeric, errors="coerce").fillna(0)
    X = df[FEATURES]
    ids = df["id"]
    return X, ids


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    print("=" * 63)
    print("  train_classifier.py  --  SIH Fire Source Classifier")
    print("=" * 63)
    print()

    db_password = load_env()

    # 1. Connect & load data
    print(f"[DB] Connecting to {DB_NAME} on {DB_HOST}:{DB_PORT} ...")
    try:
        conn = get_connection(db_password)
    except psycopg2.OperationalError as exc:
        print(f"[ERROR] Could not connect to the database: {exc}")
        sys.exit(1)

    df = load_dataframe(conn)

    if df.empty:
        print("[ERROR] No rows loaded -- run build_features.py first.")
        sys.exit(1)

    # 2. Sort chronologically by acq_date & acq_time
    df = df.sort_values(by=["acq_date", "acq_time"]).reset_index(drop=True)

    # 3. Generate rule-based training labels (labeling rule is untouched)
    df["classification"] = df.apply(label_row, axis=1)
    label_counts = df["classification"].value_counts()
    print("\n[LABELS] Class distribution (rule-based):")
    for label, count in label_counts.items():
        print(f"  {label:<40} {count:>5} rows")

    # 4. Prepare feature matrix with temporal & satellite features
    X, ids = prepare_features(df)
    y = df["classification"]

    # 5. Temporal train / test split (earliest 80% train, most recent 20% test)
    split_idx = int(len(df) * 0.80)
    train_df = df.iloc[:split_idx]
    test_df = df.iloc[split_idx:]

    X_train = X.iloc[:split_idx]
    X_test = X.iloc[split_idx:]
    y_train = y.iloc[:split_idx]
    y_test = y.iloc[split_idx:]

    train_start = train_df["acq_date"].min()
    train_end = train_df["acq_date"].max()
    test_start = test_df["acq_date"].min()
    test_end = test_df["acq_date"].max()

    print(f"\n[SPLIT] Temporal Train/Test Split (80/20 Chronological):")
    print(f"  Train set : {len(X_train):>5} rows  |  Period: {train_start} to {train_end}")
    print(f"  Test set  : {len(X_test):>5} rows  |  Period: {test_start} to {test_end}")

    # 6. Train Random Forest
    print("\n[TRAIN] Fitting RandomForestClassifier (n_estimators=200, random_state=42) ...")
    clf = RandomForestClassifier(n_estimators=200, random_state=42, n_jobs=-1)
    clf.fit(X_train, y_train)
    print("[TRAIN] Done.")

    # 7. Evaluation on test set
    y_pred = clf.predict(X_test)
    print("\n[EVAL] Classification report on held-out test set:")
    print("-" * 63)
    print(classification_report(y_test, y_pred))

    # 8. Feature importances
    importances = sorted(
        zip(FEATURES, clf.feature_importances_),
        key=lambda t: t[1],
        reverse=True,
    )
    print("[IMPORTANCE] Feature importances (descending):")
    for feat, score in importances:
        bar = "#" * int(score * 40)
        print(f"  {feat:<28} {score:.4f}  {bar}")

    # 9. Save model
    joblib.dump(clf, MODEL_PATH)
    print(f"\n[MODEL] Saved to '{MODEL_PATH}'")

    # 10. Compute predictions, confidence scores, and review flags for ALL rows
    print("\n[DB] Predicting all rows, computing confidence scores and review flags ...")
    all_predictions = clf.predict(X)
    probabilities = clf.predict_proba(X)
    confidence_scores = np.max(probabilities, axis=1)
    needs_review_flags = confidence_scores < 0.70

    # Write predictions back to PostgreSQL
    write_predictions(conn, ids, all_predictions, confidence_scores, needs_review_flags)

    conn.close()

    # 10. Final summary statistics
    review_count = int(np.sum(needs_review_flags))
    avg_confidence = float(np.mean(confidence_scores))
    total_rows = len(all_predictions)

    print("\n" + "=" * 63)
    print("  Classification & Confidence Scoring Complete")
    print("=" * 63)
    print(f"  Total rows evaluated         : {total_rows}")
    print(f"  Average confidence score     : {avg_confidence:.4f} ({avg_confidence * 100:.1f}%)")
    print(f"  Flagged for review (< 0.70)  : {review_count} rows ({review_count / total_rows * 100:.1f}%)")
    print("=" * 63)


if __name__ == "__main__":
    main()
