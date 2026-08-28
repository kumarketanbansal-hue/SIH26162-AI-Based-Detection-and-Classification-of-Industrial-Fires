# SIH 2026: Satellite Thermal Monitoring & Industrial Flare Detection System

An end-to-end near-real-time thermal detection and ML-based flare monitoring system for India. The system automatically ingests satellite thermal radiation data from NASA FIRMS (VIIRS_SNPP), enriches each detection with high-performance PostGIS spatial features (distance to nearest industrial zone, distance to nearest power plant, and spatial DBSCAN recurrence count), classifies thermal points using a Random Forest classifier into persistent industrial flares, unplanned industrial fires, or wildfires/biomass burning, and surfaces interactive GeoJSON visualizations and analytical dashboards via a FastAPI backend and React frontend.

---

## Architecture Overview

```
 +-------------------------+
 |   NASA FIRMS API        | (VIIRS_SNPP NRT Thermal Points)
 +------------+------------+
              |
              v
 +------------+------------+
 |   PostGIS Database      | (sih_fire_db / Docker)
 |   (Thermal Points,      |
 |    Industrial Zones,    |
 |    Power Plants)        |
 +------------+------------+
              |
              v
 +------------+------------+
 |   Spatial Feature Engine| (PostGIS ST_Distance KNN & ST_ClusterDBSCAN)
 +------------+------------+
              |
              v
 +------------+------------+
 |   Random Forest Model   | (Scikit-Learn Classifier & Confidence Scorer)
 +------------+------------+
              |
              v
 +------------+------------+
 |   FastAPI Backend       | (GeoJSON Endpoints & Stats API)
 +------------+------------+
              |
              v
 +------------+------------+
 |   React Web Dashboard   | (Interactive Map & Analytics UI)
 +-------------------------+
```

---

## Quick Start

### 1. Clone the Repository
```bash
git clone <repository-url>
cd SIH_2026
```

### 2. Configure Environment Variables
Copy `.env.example` to `.env` and fill in your credentials:
```bash
cp .env.example .env
```
In `.env`, provide values for `FIRMS_MAP_KEY` and `DB_PASSWORD`:
```env
FIRMS_MAP_KEY=your_nasa_firms_map_key_here
DB_PASSWORD=your_postgres_password_here
DB_HOST=localhost
BBOX=68.1,6.7,97.4,35.5
```

### 3. Start Database Container
Launch the PostGIS database container using Docker Compose:
```bash
docker-compose up -d
```

> [!NOTE]
> `seed_data.sql` automatically pre-loads ~124,000 pre-processed thermal points during the initial container startup. The dashboard and API are ready to work immediately without running the full pipeline!

### 4. Run the Backend API
Install Python dependencies and start the FastAPI server:
```bash
pip install -r requirements.txt
python -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Run the Frontend Dashboard
Navigate to the frontend directory, install dependencies, build the production bundle, and serve it:
```bash
cd frontend
npm install
npm run build
npx serve -s build
```

---

## Pre-loaded Dataset

The container setup includes `seed_data.sql`, which pre-loads **~124,000 processed thermal detection points** into PostgreSQL on first container start. This ensures the FastAPI endpoints and React dashboard function immediately with rich historical thermal points without needing to run the full data pipeline first.

---

## Known Limitations

- **Rule-Bootstrapped Training Labels**: Because no public ground-truth dataset for industrial thermal sources existed, training labels were bootstrapped using spatial domain rules (proximity to industrial zones/power plants and spatial recurrence). Consequently, the classifier's near-100% accuracy reflects rule-recovery rather than novel prediction.
- **ML Contribution**: The genuine machine learning contribution is **confidence scoring** and the **`needs_review` flag**. While hard rules enforce strict cutoff boundaries, the probabilistic Random Forest model evaluates boundary cases and flags detections with confidence scores below 0.70 for human operator review.

---

## Screenshots

<!-- Screenshots placeholder - add your screenshots below -->
```markdown
![Dashboard View](path/to/dashboard_screenshot.png)
![Interactive Map](path/to/map_screenshot.png)
```