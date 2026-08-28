import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import axios from 'axios';
import L from 'leaflet';
import { MapContainer, TileLayer, CircleMarker, Popup, GeoJSON } from 'react-leaflet';
import MarkerClusterGroup from 'react-leaflet-cluster';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import './App.css';

// ---------------------------------------------------------------------------
// Constants & Configuration
// ---------------------------------------------------------------------------

const API_HOST = process.env.REACT_APP_API_URL || 'http://localhost:8000';
const API_BASE_URL = `${API_HOST}/api`;

const CLASSIFICATION_CONFIG = {
  'Unplanned Industrial Fire': {
    color: '#dc2626',
    bgLight: 'rgba(220, 38, 38, 0.15)',
    border: 'rgba(220, 38, 38, 0.4)',
    label: 'Unplanned Industrial Fire',
    shortDesc: 'High risk anomalous fire inside or adjacent to industrial zone',
  },
  'Persistent Industrial Source': {
    color: '#2563eb',
    bgLight: 'rgba(37, 99, 235, 0.15)',
    border: 'rgba(37, 99, 235, 0.4)',
    label: 'Persistent Industrial Source',
    shortDesc: 'Known industrial flare or regulated thermal stack source',
  },
  'Wildfire / Other Biomass Burning': {
    color: '#f97316',
    bgLight: 'rgba(249, 115, 22, 0.15)',
    border: 'rgba(249, 115, 22, 0.4)',
    label: 'Wildfire / Biomass Burning',
    shortDesc: 'Agricultural stubble burn or vegetation fire distant from plants',
  },
};

const DEFAULT_COLOR = '#94a3b8';

function getCategoryColor(classification) {
  if (!classification) return DEFAULT_COLOR;
  const match = Object.keys(CLASSIFICATION_CONFIG).find(
    (key) => key.toLowerCase() === classification.trim().toLowerCase()
  );
  if (match) return CLASSIFICATION_CONFIG[match].color;

  // Fallback fuzzy matches
  const lower = classification.toLowerCase();
  if (lower.includes('unplanned') || lower.includes('industrial fire')) return '#dc2626';
  if (lower.includes('persistent') || lower.includes('flare') || lower.includes('stack')) return '#2563eb';
  if (lower.includes('wildfire') || lower.includes('biomass') || lower.includes('stubble')) return '#f97316';
  return DEFAULT_COLOR;
}

function formatDistanceKm(meters) {
  if (meters === null || meters === undefined || isNaN(meters)) return 'N/A';
  const km = meters / 1000;
  return `${km.toFixed(2)} km`;
}

function formatDateDisplay(dateStr, timeStr) {
  if (!dateStr) return 'N/A';
  let formattedTime = '';
  if (timeStr) {
    const padded = String(timeStr).padStart(4, '0');
    formattedTime = ` ${padded.slice(0, 2)}:${padded.slice(2, 4)} UTC`;
  }
  return `${dateStr}${formattedTime}`;
}

// Custom Marker Cluster Icon Generator
const createClusterCustomIcon = (cluster) => {
  const count = cluster.getChildCount();
  let clusterSize = 'small';
  if (count >= 100) {
    clusterSize = 'large';
  } else if (count >= 10) {
    clusterSize = 'medium';
  }

  // Check if any child marker represents an unplanned industrial fire
  const markers = cluster.getAllChildMarkers();
  let hasHighRisk = false;
  for (let i = 0; i < markers.length; i++) {
    const opts = markers[i].options;
    if (opts?.fillColor === '#dc2626' || opts?.pathOptions?.fillColor === '#dc2626') {
      hasHighRisk = true;
      break;
    }
  }

  const formattedCount = count > 9999 ? `${(count / 1000).toFixed(1)}k` : count.toLocaleString();

  return L.divIcon({
    html: `<div class="cluster-badge cluster-${clusterSize} ${hasHighRisk ? 'cluster-has-fire' : ''}"><span>${formattedCount}</span></div>`,
    className: 'custom-cluster-wrapper',
    iconSize: L.point(40, 40, true),
  });
};

// ---------------------------------------------------------------------------
// Main Dashboard Application
// ---------------------------------------------------------------------------

function App() {
  const [points, setPoints] = useState([]);
  const [stats, setStats] = useState(null);
  const [industrialZones, setIndustrialZones] = useState(null);
  const [powerPlants, setPowerPlants] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  
  // Filters & Layer Toggles - Default to 120 hours (5 Days) on initial page load
  const [selectedHours, setSelectedHours] = useState(120);
  const [activeCategoryFilter, setActiveCategoryFilter] = useState('ALL');
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [showIndustrialZones, setShowIndustrialZones] = useState(false);
  const [showPowerPlants, setShowPowerPlants] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(new Date());
  const isInitialMount = useRef(true);

  // Fetch thermal points & stats (refreshed when time filter changes)
  const fetchThermalData = useCallback(async (hours = selectedHours) => {
    try {
      const pointsUrl = hours !== null
        ? `${API_BASE_URL}/thermal-points?hours=${hours}&limit=5000`
        : `${API_BASE_URL}/thermal-points?hours=0&limit=5000`;

      const [pointsRes, statsRes] = await Promise.all([
        axios.get(pointsUrl),
        axios.get(`${API_BASE_URL}/stats`),
      ]);

      setPoints(pointsRes.data?.features || []);
      setStats(statsRes.data || null);
      setLastRefreshed(new Date());
    } catch (err) {
      console.error('Error fetching thermal points/stats:', err);
      setError(
        err.response?.data?.detail ||
          `Failed to connect to FastAPI backend at ${API_HOST}. Ensure the server is running.`
      );
    }
  }, [selectedHours]);

  // Initial load: fetch thermal points (5 Days default), stats, industrial zones, and power plants
  useEffect(() => {
    let isMounted = true;

    const loadInitialData = async () => {
      setLoading(true);
      setError(null);
      try {
        const initialPointsUrl = `${API_BASE_URL}/thermal-points?hours=120&limit=5000`;
        const [pointsRes, statsRes, zonesRes, plantsRes] = await Promise.allSettled([
          axios.get(initialPointsUrl),
          axios.get(`${API_BASE_URL}/stats`),
          axios.get(`${API_BASE_URL}/industrial-zones`),
          axios.get(`${API_BASE_URL}/power-plants`),
        ]);

        if (isMounted) {
          if (pointsRes.status === 'fulfilled') {
            setPoints(pointsRes.value.data?.features || []);
          }
          if (statsRes.status === 'fulfilled') {
            setStats(statsRes.value.data || null);
          }
          if (zonesRes.status === 'fulfilled') {
            setIndustrialZones(zonesRes.value.data || null);
          }
          if (plantsRes.status === 'fulfilled') {
            setPowerPlants(plantsRes.value.data?.features || []);
          }
          setLastRefreshed(new Date());
        }
      } catch (err) {
        if (isMounted) {
          console.error('Error in initial load:', err);
          setError('Failed to load map data from backend.');
        }
      } finally {
        if (isMounted) setLoading(false);
      }
    };

    loadInitialData();
    return () => {
      isMounted = false;
    };
  }, []);

  // Update thermal points when selectedHours changes (after initial mount)
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    fetchThermalData(selectedHours);
  }, [selectedHours, fetchThermalData]);

  // Manual refresh handler
  const handleManualRefresh = async () => {
    setLoading(true);
    await fetchThermalData();
    setLoading(false);
  };

  // Filtered thermal points based on category filter
  const filteredPoints = useMemo(() => {
    if (activeCategoryFilter === 'ALL') return points;
    return points.filter((feature) => {
      const cls = feature.properties?.classification || 'Unclassified';
      return cls.toLowerCase() === activeCategoryFilter.toLowerCase();
    });
  }, [points, activeCategoryFilter]);

  // Memoized thermal point CircleMarkers for MarkerClusterGroup to prevent rebuilds on zoom/pan
  const renderedMarkers = useMemo(() => {
    return filteredPoints.map((feature, idx) => {
      const props = feature.properties || {};
      const lat = props.latitude ?? feature.geometry?.coordinates?.[1];
      const lng = props.longitude ?? feature.geometry?.coordinates?.[0];

      if (lat === undefined || lng === undefined || isNaN(lat) || isNaN(lng)) {
        return null;
      }

      const markerColor = getCategoryColor(props.classification);

      return (
        <CircleMarker
          key={`thermal-${props.id || idx}`}
          center={[lat, lng]}
          radius={6}
          pathOptions={{
            fillColor: markerColor,
            fillOpacity: 0.9,
            color: '#ffffff',
            weight: 1.5,
          }}
          eventHandlers={{
            click: () => setSelectedPoint(props),
          }}
        >
          <Popup className="thermal-popup">
            <div className="popup-card">
              <div
                className="popup-header-tag"
                style={{
                  backgroundColor: markerColor,
                }}
              >
                {props.classification || 'Thermal Detection'}
              </div>

              <div className="popup-content">
                <div className="popup-metric-grid">
                  <div className="popup-metric">
                    <span className="metric-title">FRP (Radiative Power)</span>
                    <span className="metric-data highlight">
                      {props.frp !== undefined && props.frp !== null ? `${props.frp} MW` : 'N/A'}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Brightness Temp</span>
                    <span className="metric-data">
                      {props.brightness ? `${props.brightness} K` : 'N/A'}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Confidence</span>
                    <span className="metric-data capitalize">
                      {props.confidence || 'Nominal'}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Dist to Industrial Zone</span>
                    <span className="metric-data">
                      {formatDistanceKm(props.dist_to_industrial_m)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Dist to Power Plant</span>
                    <span className="metric-data">
                      {formatDistanceKm(props.dist_to_powerplant_m)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Recurrence Count (500m)</span>
                    <span className="metric-data">
                      {props.recurrence_count ?? '1'}
                    </span>
                  </div>
                </div>

                <div className="popup-footer-row">
                  <div className="popup-date">
                    📅 {formatDateDisplay(props.acq_date, props.acq_time)}
                  </div>
                  <div className="popup-coords">
                    📍 {lat.toFixed(4)}, {lng.toFixed(4)}
                  </div>
                </div>
              </div>
            </div>
          </Popup>
        </CircleMarker>
      );
    });
  }, [filteredPoints]);

  // Dynamic counts for current dataset
  const categoryCounts = useMemo(() => {
    const counts = {
      'Unplanned Industrial Fire': 0,
      'Persistent Industrial Source': 0,
      'Wildfire / Other Biomass Burning': 0,
      Unclassified: 0,
    };

    points.forEach((pt) => {
      const cls = pt.properties?.classification;
      if (cls && counts[cls] !== undefined) {
        counts[cls] += 1;
      } else {
        counts.Unclassified += 1;
      }
    });

    return counts;
  }, [points]);

  // Industrial Zone Popup & Styling Handler
  const onEachIndustrialZone = useCallback((feature, layer) => {
    const props = feature.properties || {};
    const name = props.name || 'Industrial Zone';
    const landuse = props.landuse || 'industrial';
    const manMade = props.man_made || 'N/A';

    const popupHtml = `
      <div class="popup-card">
        <div class="popup-header-tag" style="background-color: #8b5cf6;">
          🏭 Industrial Zone
        </div>
        <div class="popup-content">
          <div style="font-weight: 700; font-size: 0.95rem; margin-bottom: 8px; color: #ffffff;">
            ${name}
          </div>
          <div class="popup-metric-grid">
            <div class="popup-metric">
              <span class="metric-title">Land Use</span>
              <span class="metric-data" style="text-transform: capitalize;">${landuse}</span>
            </div>
            <div class="popup-metric">
              <span class="metric-title">Man Made</span>
              <span class="metric-data" style="text-transform: capitalize;">${manMade}</span>
            </div>
          </div>
        </div>
      </div>
    `;
    layer.bindPopup(popupHtml, { className: 'thermal-popup' });
  }, []);

  return (
    <div className="dashboard-container">
      {/* Sidebar Controls & Analytics */}
      <aside className="dashboard-sidebar">
        {/* Header Branding */}
        <div className="sidebar-header">
          <div className="badge-row">
            <span className="live-pill">
              <span className="live-dot"></span> LIVE VIIRS FEED
            </span>
            <span className="sih-tag">SIH26162</span>
          </div>
          <h1 className="app-title">Thermal Intelligence & Industrial Fire Detection</h1>
          <p className="app-subtitle">
            Near-Real-Time NASA FIRMS Sentinel with PostGIS & ML Classifier
          </p>
        </div>

        {/* Global Stats Banner */}
        <div className="stats-overview-card">
          <div className="stat-item main-stat">
            <span className="stat-label">Total Active Detections</span>
            <span className="stat-value">
              {loading
                ? '...'
                : (selectedHours === null
                    ? (stats?.total_thermal_points ?? points.length)
                    : points.length
                  ).toLocaleString()}
            </span>
          </div>

          <div className="stat-subrow">
            <div className="stat-item">
              <span className="stat-label">Visible on Map</span>
              <span className="stat-subvalue">{loading ? '...' : filteredPoints.length}</span>
            </div>
            <div className="stat-item">
              <span className="stat-label">Latest Ingestion</span>
              <span className="stat-subvalue ingestion-time">
                {stats?.most_recent_ingestion
                  ? new Date(stats.most_recent_ingestion).toLocaleString(undefined, {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })
                  : 'N/A'}
              </span>
            </div>
          </div>
        </div>

        {/* Time Window Filter */}
        <div className="filter-section">
          <label className="section-label">Time Window Filter</label>
          <div className="time-button-group">
            {[
              { label: 'All Time', value: null },
              { label: '24 Hours', value: 24 },
              { label: '48 Hours', value: 48 },
              { label: '5 Days', value: 120 },
            ].map((opt) => (
              <button
                key={String(opt.value)}
                className={`time-btn ${selectedHours === opt.value ? 'active' : ''}`}
                onClick={() => setSelectedHours(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="filter-sample-note">
            * "All Time" displays a sample (max 5,000 detections) when dataset is large.
          </p>
        </div>

        {/* Map Reference Layers Toggle */}
        <div className="layers-toggle-section">
          <label className="section-label">Map Context Layers</label>
          <div className="layer-toggles-card">
            {/* Industrial Zones Toggle */}
            <label className="layer-toggle-row">
              <div className="layer-info">
                <span className="layer-swatch industrial-swatch"></span>
                <div className="layer-text">
                  <span className="layer-name">Industrial Zones</span>
                  <span className="layer-meta">
                    {industrialZones?.features ? `${industrialZones.features.length} zones` : 'OSM Polygons'}
                  </span>
                </div>
              </div>
              <input
                type="checkbox"
                className="layer-checkbox"
                checked={showIndustrialZones}
                onChange={(e) => setShowIndustrialZones(e.target.checked)}
              />
            </label>

            {/* Power Plants Toggle */}
            <label className="layer-toggle-row">
              <div className="layer-info">
                <span className="layer-swatch powerplant-swatch"></span>
                <div className="layer-text">
                  <span className="layer-name">Power Plants</span>
                  <span className="layer-meta">
                    {powerPlants.length ? `${powerPlants.length} facilities` : 'WRI / IND DB'}
                  </span>
                </div>
              </div>
              <input
                type="checkbox"
                className="layer-checkbox"
                checked={showPowerPlants}
                onChange={(e) => setShowPowerPlants(e.target.checked)}
              />
            </label>
          </div>
        </div>

        {/* Classification Breakdown Cards */}
        <div className="categories-section">
          <div className="section-header-row">
            <label className="section-label">Classification Breakdown</label>
            {activeCategoryFilter !== 'ALL' && (
              <button
                className="reset-filter-link"
                onClick={() => setActiveCategoryFilter('ALL')}
              >
                Reset Filter
              </button>
            )}
          </div>

          <div className="category-cards-list">
            {Object.entries(CLASSIFICATION_CONFIG).map(([catKey, config]) => {
              const count = categoryCounts[catKey] || 0;
              const percent = points.length ? Math.round((count / points.length) * 100) : 0;
              const isActive = activeCategoryFilter === catKey;

              return (
                <div
                  key={catKey}
                  className={`category-card ${isActive ? 'selected' : ''}`}
                  onClick={() =>
                    setActiveCategoryFilter(isActive ? 'ALL' : catKey)
                  }
                  style={{
                    borderColor: isActive ? config.color : undefined,
                    backgroundColor: isActive ? config.bgLight : undefined,
                  }}
                >
                  <div className="category-card-top">
                    <div className="cat-title-wrap">
                      <span
                        className="cat-dot"
                        style={{ backgroundColor: config.color }}
                      ></span>
                      <span className="cat-name">{config.label}</span>
                    </div>
                    <span
                      className="cat-count"
                      style={{ color: config.color }}
                    >
                      {count}
                    </span>
                  </div>

                  <p className="cat-desc">{config.shortDesc}</p>

                  <div className="cat-progress-track">
                    <div
                      className="cat-progress-bar"
                      style={{
                        width: `${percent}%`,
                        backgroundColor: config.color,
                      }}
                    ></div>
                  </div>
                  <div className="cat-progress-meta">
                    <span>{percent}% of detections</span>
                    <span className="click-to-filter-hint">
                      {isActive ? 'Active filter (click to clear)' : 'Click to filter'}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Selected Point Inspector */}
        {selectedPoint && (
          <div className="selected-point-inspector">
            <div className="inspector-header">
              <span className="inspector-title">Active Point Inspector</span>
              <button
                className="close-inspector-btn"
                onClick={() => setSelectedPoint(null)}
              >
                ×
              </button>
            </div>
            <div className="inspector-body">
              <div className="inspector-row">
                <span>Class:</span>
                <strong
                  style={{
                    color: getCategoryColor(selectedPoint.classification),
                  }}
                >
                  {selectedPoint.classification || 'Unclassified'}
                </strong>
              </div>
              <div className="inspector-row">
                <span>FRP (Power):</span>
                <strong>{selectedPoint.frp ? `${selectedPoint.frp} MW` : 'N/A'}</strong>
              </div>
              <div className="inspector-row">
                <span>Distance to Industrial:</span>
                <strong>{formatDistanceKm(selectedPoint.dist_to_industrial_m)}</strong>
              </div>
              <div className="inspector-row">
                <span>Distance to Power Plant:</span>
                <strong>{formatDistanceKm(selectedPoint.dist_to_powerplant_m)}</strong>
              </div>
              <div className="inspector-row">
                <span>Coordinates:</span>
                <code>
                  {selectedPoint.latitude?.toFixed(4)}, {selectedPoint.longitude?.toFixed(4)}
                </code>
              </div>
            </div>
          </div>
        )}

        {/* Sidebar Footer */}
        <div className="sidebar-footer">
          <button
            className="refresh-btn"
            onClick={handleManualRefresh}
            disabled={loading}
          >
            {loading ? (
              <span className="spinner"></span>
            ) : (
              <span className="refresh-icon">↻</span>
            )}
            {loading ? 'Refreshing Data...' : 'Refresh Thermal Feed'}
          </button>
          <span className="last-sync-text">
            Synced: {lastRefreshed.toLocaleTimeString()}
          </span>
        </div>
      </aside>

      {/* Interactive Map Area */}
      <main className="dashboard-map-area">
        {error && (
          <div className="error-banner">
            <div className="error-content">
              <strong>Connection Warning:</strong> {error}
            </div>
            <button className="error-retry-btn" onClick={handleManualRefresh}>
              Retry Connection
            </button>
          </div>
        )}

        <MapContainer
          center={[22.0, 79.0]}
          zoom={5}
          scrollWheelZoom={true}
          className="leaflet-map-canvas"
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | NASA FIRMS'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          {/* 1. Industrial Zones Polygon Layer (Toggleable) */}
          {showIndustrialZones && industrialZones && (
            <GeoJSON
              key={`industrial-zones-${industrialZones.features?.length || 0}`}
              data={industrialZones}
              style={() => ({
                fillColor: '#8b5cf6',
                fillOpacity: 0.2,
                color: '#8b5cf6',
                weight: 1.5,
                opacity: 1,
              })}
              onEachFeature={onEachIndustrialZone}
            />
          )}

          {/* 2. Power Plants Layer (Toggleable) */}
          {showPowerPlants &&
            powerPlants.map((plant, idx) => {
              const lat = plant.properties?.latitude ?? plant.geometry?.coordinates?.[1];
              const lng = plant.properties?.longitude ?? plant.geometry?.coordinates?.[0];

              if (lat === undefined || lng === undefined || isNaN(lat) || isNaN(lng)) {
                return null;
              }

              const props = plant.properties || {};

              return (
                <CircleMarker
                  key={`plant-${props.id || idx}`}
                  center={[lat, lng]}
                  radius={4}
                  pathOptions={{
                    fillColor: '#eab308',
                    fillOpacity: 0.9,
                    color: '#ffffff',
                    weight: 1.5,
                  }}
                >
                  <Popup className="thermal-popup">
                    <div className="popup-card">
                      <div
                        className="popup-header-tag"
                        style={{ backgroundColor: '#eab308', color: '#1e293b' }}
                      >
                        ⚡ Power Plant
                      </div>
                      <div className="popup-content">
                        <div
                          style={{
                            fontWeight: 700,
                            fontSize: '0.95rem',
                            marginBottom: '8px',
                            color: '#ffffff',
                          }}
                        >
                          {props.name || 'Unnamed Power Plant'}
                        </div>
                        <div className="popup-metric-grid">
                          <div className="popup-metric">
                            <span className="metric-title">Capacity (MW)</span>
                            <span className="metric-data highlight">
                              {props.capacity_mw !== undefined && props.capacity_mw !== null
                                ? `${props.capacity_mw} MW`
                                : 'N/A'}
                            </span>
                          </div>
                          <div className="popup-metric">
                            <span className="metric-title">Primary Fuel</span>
                            <span
                              className="metric-data"
                              style={{ textTransform: 'capitalize' }}
                            >
                              {props.primary_fuel || 'N/A'}
                            </span>
                          </div>
                        </div>
                        <div className="popup-footer-row">
                          <div className="popup-coords">
                            📍 {lat.toFixed(4)}, {lng.toFixed(4)}
                          </div>
                        </div>
                      </div>
                    </div>
                  </Popup>
                </CircleMarker>
              );
            })}

          {/* 3. Clustered Thermal Point Markers Layer */}
          <MarkerClusterGroup
            chunkedLoading={true}
            iconCreateFunction={createClusterCustomIcon}
            maxClusterRadius={50}
            spiderfyOnMaxZoom={true}
            showCoverageOnHover={false}
            zoomToBoundsOnClick={true}
          >
            {renderedMarkers}
          </MarkerClusterGroup>
        </MapContainer>

        {/* Map Legend (Bottom-Left) */}
        <div className="map-legend-overlay">
          <div className="legend-header">
            <span className="legend-title">Map Legend</span>
            <span className="legend-count-pill">{filteredPoints.length} Detections</span>
          </div>
          <div className="legend-items">
            {/* Thermal Classifications */}
            {Object.entries(CLASSIFICATION_CONFIG).map(([catKey, config]) => (
              <div key={catKey} className="legend-row">
                <span
                  className="legend-bullet"
                  style={{ backgroundColor: config.color }}
                ></span>
                <span className="legend-label">{config.label}</span>
              </div>
            ))}

            {/* Optional Layer Swatches when toggled */}
            {showIndustrialZones && (
              <div className="legend-row dynamic-legend-item">
                <span className="legend-bullet-poly"></span>
                <span className="legend-label">Industrial Zone (OSM)</span>
              </div>
            )}

            {showPowerPlants && (
              <div className="legend-row dynamic-legend-item">
                <span className="legend-bullet-plant"></span>
                <span className="legend-label">Power Plant (Facility)</span>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
