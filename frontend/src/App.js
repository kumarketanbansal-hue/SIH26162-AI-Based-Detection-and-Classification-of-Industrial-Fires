import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import axios from 'axios';
import L from 'leaflet';
import { MapContainer, TileLayer, CircleMarker, Popup, GeoJSON, useMap } from 'react-leaflet';
import MarkerClusterGroup from 'react-leaflet-cluster';
import 'leaflet/dist/leaflet.css';
import 'leaflet.markercluster/dist/MarkerCluster.css';
import 'leaflet.markercluster/dist/MarkerCluster.Default.css';
import './App.css';
import SystemInfoModal from './components/SystemInfoModal';


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

const CONFIDENCE_BADGE_CONFIG = {
  h: { label: 'H', color: '#f87171', bg: 'rgba(239, 68, 68, 0.15)', border: 'rgba(239, 68, 68, 0.4)' },
  high: { label: 'H', color: '#f87171', bg: 'rgba(239, 68, 68, 0.15)', border: 'rgba(239, 68, 68, 0.4)' },
  n: { label: 'N', color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.15)', border: 'rgba(245, 158, 11, 0.4)' },
  nominal: { label: 'N', color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.15)', border: 'rgba(245, 158, 11, 0.4)' },
  l: { label: 'L', color: '#60a5fa', bg: 'rgba(59, 130, 246, 0.15)', border: 'rgba(59, 130, 246, 0.4)' },
  low: { label: 'L', color: '#60a5fa', bg: 'rgba(59, 130, 246, 0.15)', border: 'rgba(59, 130, 246, 0.4)' },
};

function getConfidenceBadge(confidence, severityLevel) {
  if (confidence) {
    const key = String(confidence).trim().toLowerCase();
    if (CONFIDENCE_BADGE_CONFIG[key]) {
      return CONFIDENCE_BADGE_CONFIG[key];
    }
    const char = key.charAt(0);
    if (CONFIDENCE_BADGE_CONFIG[char]) {
      return CONFIDENCE_BADGE_CONFIG[char];
    }
  }
  if (severityLevel >= 3) return CONFIDENCE_BADGE_CONFIG['h'];
  if (severityLevel === 2) return CONFIDENCE_BADGE_CONFIG['n'];
  return CONFIDENCE_BADGE_CONFIG['l'];
}

function formatRawViirsConfidence(confidence, severityLevel) {
  if (confidence) {
    const char = String(confidence).trim().charAt(0).toUpperCase();
    if (['H', 'N', 'L'].includes(char)) return char;
  }
  if (severityLevel >= 3) return 'H';
  if (severityLevel === 2) return 'N';
  return 'L';
}

const TIER_CONFIG = {
  0: { label: 'Tier 0', title: 'Local Operator', color: '#60a5fa', bg: 'rgba(59, 130, 246, 0.15)' },
  1: { label: 'Tier 1', title: 'Safety Supervisor', color: '#fbbf24', bg: 'rgba(245, 158, 11, 0.15)' },
  2: { label: 'Tier 2', title: 'Emergency Command', color: '#f87171', bg: 'rgba(239, 68, 68, 0.15)' },
};

const DEFAULT_COLOR = '#94a3b8';

function getCategoryColor(classification) {
  if (!classification) return DEFAULT_COLOR;
  const match = Object.keys(CLASSIFICATION_CONFIG).find(
    (key) => key.toLowerCase() === classification.trim().toLowerCase()
  );
  if (match) return CLASSIFICATION_CONFIG[match].color;

  const lower = classification.toLowerCase();
  if (lower.includes('unplanned') || lower.includes('industrial fire')) return '#dc2626';
  if (lower.includes('persistent') || lower.includes('flare') || lower.includes('stack')) return '#2563eb';
  if (lower.includes('wildfire') || lower.includes('biomass') || lower.includes('stubble')) return '#f97316';
  return DEFAULT_COLOR;
}

function formatDistance(meters, unit = 'km') {
  if (meters === null || meters === undefined || isNaN(meters)) return 'N/A';
  const m = parseFloat(meters);
  if (isNaN(m)) return 'N/A';
  if (unit === 'miles') {
    const miles = m * 0.000621371;
    return `${miles.toFixed(2)} mi`;
  }
  const km = m / 1000;
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

function formatConfidenceScore(score) {
  if (score === null || score === undefined || score === '' || isNaN(score)) return 'N/A';
  const num = parseFloat(score);
  if (isNaN(num)) return 'N/A';
  const percent = num <= 1.0 ? Math.round(num * 100) : Math.round(num);
  return `${percent}%`;
}

// Robust Human Review Evaluation Logic (Flagged if needs_review flag is set or confidence_score < 0.70)
function isPointFlaggedForReview(props) {
  if (!props) return false;
  if (
    props.needs_review === true ||
    props.needs_review === 1 ||
    props.needs_review === 'true' ||
    props.needs_review === 't'
  ) {
    return true;
  }
  if (props.confidence_score !== null && props.confidence_score !== undefined && props.confidence_score !== '') {
    const score = parseFloat(props.confidence_score);
    if (!isNaN(score)) {
      if (score <= 1.0 && score < 0.70) return true;
      if (score > 1.0 && score < 70) return true;
    }
  }
  return false;
}

function getEscalationCountdown(incident, nowTick) {
  if (incident.status !== 'pending') {
    return null;
  }
  if (incident.current_tier >= 2) {
    return { isMax: true, seconds: 0, nextTier: null };
  }

  let baseTimeStr = null;
  let nextTier = 1;
  if (incident.current_tier === 0) {
    baseTimeStr = incident.tier_0_sent_at || incident.created_at;
    nextTier = 1;
  } else if (incident.current_tier === 1) {
    baseTimeStr = incident.tier_1_sent_at || incident.tier_0_sent_at || incident.created_at;
    nextTier = 2;
  }

  if (!baseTimeStr) return null;

  const baseTimestamp = new Date(baseTimeStr).getTime();
  const targetTimestamp = baseTimestamp + 60 * 1000;
  const current = nowTick || Date.now();
  const diffMs = targetTimestamp - current;
  const remainingSeconds = Math.max(0, Math.ceil(diffMs / 1000));

  return {
    isMax: false,
    seconds: remainingSeconds,
    nextTier,
  };
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

  const markers = cluster.getAllChildMarkers();
  let hasHighRisk = false;
  let hasNeedsReview = false;

  for (let i = 0; i < markers.length; i++) {
    const opts = markers[i].options || {};
    const pathOpts = opts.pathOptions || opts;

    if (pathOpts.fillColor === '#dc2626' || opts.fillColor === '#dc2626') {
      hasHighRisk = true;
    }
    if (
      opts.isReview ||
      pathOpts.color === '#facc15' ||
      opts.color === '#facc15' ||
      pathOpts.dashArray === '4, 4' ||
      pathOpts.dashArray === '4'
    ) {
      hasNeedsReview = true;
    }

    if (hasHighRisk && hasNeedsReview) break;
  }

  const formattedCount = count > 9999 ? `${(count / 1000).toFixed(1)}k` : count.toLocaleString();

  return L.divIcon({
    html: `<div class="cluster-badge cluster-${clusterSize} ${hasHighRisk ? 'cluster-has-fire' : ''} ${hasNeedsReview ? 'cluster-has-review' : ''}">
      <span>${formattedCount}</span>
      ${hasNeedsReview ? '<span class="cluster-review-indicator" title="Contains detections flagged for review">⚠️</span>' : ''}
    </div>`,
    className: 'custom-cluster-wrapper',
    iconSize: L.point(40, 40, true),
  });
};

// Map FlyTo Recenter Helper
function MapFlyController({ targetCoords }) {
  const map = useMap();
  useEffect(() => {
    if (targetCoords && Array.isArray(targetCoords) && targetCoords.length === 2) {
      map.flyTo(targetCoords, 11, { duration: 1.2 });
    }
  }, [targetCoords, map]);
  return null;
}

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

  // Top Sidebar Navigation State: 'ANALYTICS' | 'INCIDENTS'
  const [sidebarTab, setSidebarTab] = useState('ANALYTICS');

  // Help Modal State
  const [isHelpOpen, setIsHelpOpen] = useState(false);

  // Incidents Escalation State
  const [incidents, setIncidents] = useState([]);
  const [isSimulating, setIsSimulating] = useState(false);
  const [simulatingSuccess, setSimulatingSuccess] = useState(null);
  const [triggeringPointId, setTriggeringPointId] = useState(null);
  const [pointIncidentStatus, setPointIncidentStatus] = useState({});
  const [nowTick, setNowTick] = useState(Date.now());
  const [mapTargetCoords, setMapTargetCoords] = useState(null);

  // Filters & Layer Toggles
  const [selectedHours, setSelectedHours] = useState(null);
  const [activeCategoryFilter, setActiveCategoryFilter] = useState('ALL');
  const [showOnlyNeedsReview, setShowOnlyNeedsReview] = useState(false);
  const [selectedPoint, setSelectedPoint] = useState(null);
  const [showIndustrialZones, setShowIndustrialZones] = useState(false);
  const [showPowerPlants, setShowPowerPlants] = useState(false);
  const [lastRefreshed, setLastRefreshed] = useState(new Date());

  const isInitialMount = useRef(true);
  const knownIncidentIdsRef = useRef(new Set());
  const hasInitializedIncidentsRef = useRef(false);
  const incidentsStringRef = useRef('');

  // Global Incident Polling (Optimized to prevent spurious re-renders and popup flicker)
  const fetchIncidents = useCallback(async () => {
    try {
      const res = await axios.get(`${API_BASE_URL}/incidents`);
      if (res.data && Array.isArray(res.data)) {
        const sorted = [...res.data].sort(
          (a, b) => new Date(b.created_at) - new Date(a.created_at)
        );

        knownIncidentIdsRef.current = new Set(sorted.map((i) => i.id));
        hasInitializedIncidentsRef.current = true;

        // Prevent state mutation if incident payload is identical
        const serialized = JSON.stringify(sorted);
        if (serialized !== incidentsStringRef.current) {
          incidentsStringRef.current = serialized;
          setIncidents(sorted);
        }
      }
    } catch (err) {
      console.error('Error fetching incidents queue:', err);
    }
  }, []);

  // Live polling for active incidents (3-second cadence)
  useEffect(() => {
    fetchIncidents();
    const incidentInterval = setInterval(() => {
      fetchIncidents();
    }, 3000);
    return () => clearInterval(incidentInterval);
  }, [fetchIncidents]);

  // 1-second ticker for live countdown rendering
  useEffect(() => {
    const ticker = setInterval(() => {
      setNowTick(Date.now());
    }, 1000);
    return () => clearInterval(ticker);
  }, []);

  // Fetch thermal points & stats (Sample limit capped at 4,000 for high rendering performance)
  const fetchThermalData = useCallback(async (hours = selectedHours) => {
    try {
      const pointsUrl = hours !== null
        ? `${API_BASE_URL}/thermal-points?hours=${hours}&limit=4000`
        : `${API_BASE_URL}/thermal-points?hours=0&limit=4000`;

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

  // Initial load
  useEffect(() => {
    let isMounted = true;

    const loadInitialData = async () => {
      setLoading(true);
      setError(null);
      try {
        const initialPointsUrl = `${API_BASE_URL}/thermal-points?hours=0&limit=4000`;

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

  // Update thermal points when selectedHours changes
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    fetchThermalData(selectedHours);
  }, [selectedHours, fetchThermalData]);

  // Manual refresh handler — Resets and re-syncs all map data & incident queues
  const handleManualRefresh = async () => {
    setLoading(true);
    await Promise.all([fetchThermalData(), fetchIncidents()]);
    setLoading(false);
  };

  // Targeted Incident Response Trigger for a specific Thermal Point
  const handleTriggerIncidentForPoint = useCallback(async (pointProps) => {
    if (!pointProps || !pointProps.id) return;
    setTriggeringPointId(pointProps.id);
    setPointIncidentStatus((prev) => ({
      ...prev,
      [pointProps.id]: { loading: true, message: null },
    }));

    try {
      const res = await axios.post(`${API_BASE_URL}/incidents/simulate`, {
        thermal_point_id: pointProps.id,
      });
      await fetchIncidents();
      const incId = res.data?.id;
      const isParallel = res.data?.alert_mode === 'parallel';
      const msg = `Incident #${incId} created (${isParallel ? 'Parallel' : 'Sequential'}) — Tier 0 alert sent.`;
      setPointIncidentStatus((prev) => ({
        ...prev,
        [pointProps.id]: { success: true, message: msg },
      }));
      setTimeout(() => {
        setPointIncidentStatus((prev) => {
          const next = { ...prev };
          delete next[pointProps.id];
          return next;
        });
      }, 8000);
    } catch (err) {
      console.error('Error triggering targeted incident:', err);
      const errMsg = err.response?.data?.detail || 'Failed to trigger incident response';
      setPointIncidentStatus((prev) => ({
        ...prev,
        [pointProps.id]: { success: false, message: errMsg },
      }));
    } finally {
      setTriggeringPointId(null);
    }
  }, [fetchIncidents]);

  // Simulate Incident Handler
  const handleSimulateIncident = async () => {
    setIsSimulating(true);
    setSimulatingSuccess(null);
    try {
      const res = await axios.post(`${API_BASE_URL}/incidents/simulate`, {});
      await fetchIncidents();
      const incidentId = res.data?.id ?? 1;
      setSimulatingSuccess(`Incident #${incidentId} created! Tier 0 alert dispatched.`);
      setTimeout(() => setSimulatingSuccess(null), 6000);
      if (res.data?.latitude && res.data?.longitude) {
        setMapTargetCoords([res.data.latitude, res.data.longitude]);
      }
    } catch (err) {
      console.error('Error simulating incident:', err);
      alert('Failed to simulate incident. Ensure backend is running.');
    } finally {
      setIsSimulating(false);
    }
  };

  // Manual In-App Acknowledge Handler
  const handleAcknowledgeIncident = async (incidentId) => {
    try {
      await axios.post(`${API_BASE_URL}/incidents/${incidentId}/acknowledge`, {
        acknowledged_by: 'Dashboard Operator',
      });
      await fetchIncidents();
    } catch (err) {
      console.error('Error acknowledging incident:', err);
    }
  };

  // Set of active pending incident source IDs for instant lookup
  const activeIncidentSourceIds = useMemo(() => {
    const ids = new Set();
    incidents.forEach((inc) => {
      if (inc.status === 'pending' && inc.source_thermal_point_id) {
        ids.add(inc.source_thermal_point_id);
      }
    });
    return ids;
  }, [incidents]);

  // Helper to find associated incident for a thermal point
  const findAssociatedIncident = useCallback(
    (pointProps) => {
      if (!pointProps) return null;
      const ptId = pointProps.id;
      if (ptId) {
        const match = incidents.find((inc) => inc.source_thermal_point_id === ptId);
        if (match) return match;
      }
      const lat = pointProps.latitude ?? pointProps.geometry?.coordinates?.[1];
      const lng = pointProps.longitude ?? pointProps.geometry?.coordinates?.[0];
      if (lat !== undefined && lng !== undefined) {
        return incidents.find(
          (inc) =>
            Math.abs(inc.latitude - lat) < 0.0001 &&
            Math.abs(inc.longitude - lng) < 0.0001
        );
      }
      return null;
    },
    [incidents]
  );

  // Render Alert Escalation & Recipient Division Info
  const renderIncidentEscalationDetails = useCallback((incident) => {
    if (!incident) return null;
    const currentTier = incident.current_tier ?? 0;
    const isParallel = incident.alert_mode === 'parallel';
    const isAcknowledged = incident.status === 'acknowledged';

    const tier0Dispatched = Boolean(incident.tier_0_sent_at || currentTier >= 0);
    const tier1Dispatched = Boolean(incident.tier_1_sent_at || currentTier >= 1 || isParallel);
    const tier2Dispatched = Boolean(incident.tier_2_sent_at || currentTier >= 2 || isParallel);

    let tierLabel = 'Tier 0 (Local Operator)';
    if (currentTier === 1) tierLabel = 'Tier 1 (Safety Supervisor)';
    if (currentTier >= 2) tierLabel = 'Tier 2 (Emergency Command)';

    return (
      <div className="incident-escalation-inspector-card">
        <div className="escalation-card-header">
          <span className="escalation-header-title">🚨 ALERT DISPATCH & ESCALATION</span>
          <span className="escalation-id-badge">#{incident.id}</span>
        </div>

        <div className="escalation-details-grid">
          <div className="escalation-row">
            <span className="esc-label">Status:</span>
            <span className={`esc-val ${isAcknowledged ? 'status-ack' : 'status-pending'}`}>
              {isAcknowledged
                ? `✓ Acknowledged (${incident.acknowledged_by || 'Operator'})`
                : '⚡ Active Alert (Pending Response)'}
            </span>
          </div>

          <div className="escalation-row">
            <span className="esc-label">Active Tier:</span>
            <span className="esc-val tier-highlight">{tierLabel}</span>
          </div>

          <div className="escalation-row">
            <span className="esc-label">Dispatch Mode:</span>
            <span className="esc-val">
              {isParallel ? 'Parallel (Simultaneous)' : 'Sequential (60s Escalation)'}
            </span>
          </div>
        </div>

        <div className="escalation-recipients-section">
          <span className="recipients-title">Alert Recipients:</span>
          <div className="recipients-chips-row">
            <span className={`recipient-chip ${tier0Dispatched ? 'dispatched' : 'pending'}`}>
              {tier0Dispatched ? '✓' : '○'} Tier 0 Operator
            </span>
            <span className={`recipient-chip ${tier1Dispatched ? 'dispatched' : 'pending'}`}>
              {tier1Dispatched ? '✓' : '○'} Tier 1 Supervisor
            </span>
            <span className={`recipient-chip ${tier2Dispatched ? 'dispatched' : 'pending'}`}>
              {tier2Dispatched ? '✓' : '○'} Tier 2 Command
            </span>
          </div>
        </div>
      </div>
    );
  }, []);

  // Render Incident Trigger Button inside Popup & Inspector
  const renderIncidentTriggerButton = useCallback(
    (pointProps, isInspector = false) => {
      if (!pointProps) return null;
      const ptId = pointProps.id;
      const isPersistent =
        pointProps.classification &&
        pointProps.classification.trim().toLowerCase() === 'persistent industrial source';

      const associatedIncident = findAssociatedIncident(pointProps);
      const hasActiveIncident = Boolean(associatedIncident && associatedIncident.status === 'pending');
      const isAcknowledged = Boolean(associatedIncident && associatedIncident.status === 'acknowledged');
      const isTriggering = ptId ? triggeringPointId === ptId : false;
      const statusInfo = ptId ? pointIncidentStatus[ptId] : null;

      return (
        <div className={isInspector ? 'inspector-action-section' : 'popup-action-row'}>
          {isPersistent ? (
            <div className="incident-status-locked-badge persistent-locked">
              <span>🔒 Regulated Source — No Alert Trigger</span>
            </div>
          ) : hasActiveIncident ? (
            <div className="incident-status-locked-badge active-locked">
              <span className="status-dot-pulsing"></span>
              <span>✓ Incident Active (#{associatedIncident.id})</span>
            </div>
          ) : isAcknowledged ? (
            <div className="incident-status-locked-badge acknowledged-locked">
              <span>✓ Incident Acknowledged (#{associatedIncident.id})</span>
            </div>
          ) : (
            <button
              className="trigger-incident-btn active-trigger"
              onClick={(e) => {
                if (e && e.stopPropagation) e.stopPropagation();
                handleTriggerIncidentForPoint(pointProps);
              }}
              disabled={isTriggering}
              title="Dispatch emergency escalation alerts for this detection"
            >
              {isTriggering ? (
                <>
                  <span className="spinner-inline"></span>
                  <span>Dispatching Escalation...</span>
                </>
              ) : (
                <>
                  <span>🔥 Trigger Incident Response</span>
                </>
              )}
            </button>
          )}

          {statusInfo && statusInfo.message && (
            <div
              className={`point-incident-inline-msg ${statusInfo.success ? 'msg-success' : 'msg-error'
                }`}
            >
              {statusInfo.success ? '✓ ' : '⚠️ '} {statusInfo.message}
            </div>
          )}
        </div>
      );
    },
    [findAssociatedIncident, pointIncidentStatus, triggeringPointId, handleTriggerIncidentForPoint]
  );

  const activeClassification = activeCategoryFilter !== 'ALL' ? activeCategoryFilter : null;

  // Points Needing Review Count (Evaluated across all active points with < 0.70 confidence threshold)
  const needsReviewCount = useMemo(() => {
    return points.filter((pt) => isPointFlaggedForReview(pt.properties)).length;
  }, [points]);

  // Filtered thermal points (Mutually exclusive: Needs Review toggle or Classification filter)
  const filteredPoints = useMemo(() => {
    if (showOnlyNeedsReview) {
      return points.filter((feature) => isPointFlaggedForReview(feature.properties));
    }
    if (activeCategoryFilter !== 'ALL') {
      return points.filter((feature) => {
        const cls = feature.properties?.classification || 'Unclassified';
        return cls.toLowerCase() === activeCategoryFilter.toLowerCase();
      });
    }
    return points;
  }, [points, activeCategoryFilter, showOnlyNeedsReview]);

  // Pending Incidents Count
  const pendingIncidentsCount = useMemo(() => {
    return incidents.filter((inc) => inc.status === 'pending').length;
  }, [incidents]);

  // Category counts
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

  // Render Clustered Markers (Base classifications are strictly preserved; popups use stable IDs)
  const renderedMarkers = useMemo(() => {
    return filteredPoints.map((feature) => {
      const lat = feature.properties?.latitude ?? feature.geometry?.coordinates?.[1];
      const lng = feature.properties?.longitude ?? feature.geometry?.coordinates?.[0];

      if (lat === undefined || lng === undefined || isNaN(lat) || isNaN(lng)) {
        return null;
      }

      const props = feature.properties || {};
      const pointId = props.id || `${lat.toFixed(4)}_${lng.toFixed(4)}`;
      const classification = props.classification || 'Unclassified';
      const catColor = getCategoryColor(classification);
      const isPointNeedingReview = isPointFlaggedForReview(props);
      const hasActiveIncident = props.id && activeIncidentSourceIds.has(props.id);
      const severityLevel = props.severity_level || 1;
      const rawViirsBadge = { bg: '#334155', color: '#f8fafc', border: '#475569' };

      const associatedIncident = findAssociatedIncident(props);

      return (
        <CircleMarker
          key={`tp-${pointId}`}
          center={[lat, lng]}
          radius={isPointNeedingReview ? 8 : 6}
          pathOptions={{
            fillColor: catColor,
            fillOpacity: 0.9,
            color: isPointNeedingReview ? '#facc15' : (hasActiveIncident ? '#f59e0b' : '#ffffff'),
            weight: isPointNeedingReview ? 3 : 1.5,
            dashArray: isPointNeedingReview ? '4, 4' : undefined,
            className: isPointNeedingReview
              ? 'pulsing-review-ring'
              : (hasActiveIncident ? 'pulsing-incident-ring' : ''),
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
                  backgroundColor: catColor,
                  color: '#ffffff',
                }}
              >
                {classification}
                {hasActiveIncident && ' 🔥 [Active Incident]'}
              </div>

              {isPointNeedingReview && (
                <div className="popup-review-alert-badge">
                  <span className="alert-icon">⚠️</span>
                  <span>Flagged for Human Review (Confidence &lt; 0.70)</span>
                </div>
              )}

              <div className="popup-content">
                <div className="popup-metric-grid">
                  <div className="popup-metric">
                    <span className="metric-title">Fire Radiative Power</span>
                    <span className="metric-data highlight">
                      {props.frp ? `${props.frp} MW` : 'N/A'}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Classification Confidence</span>
                    <span
                      className="metric-data"
                      style={{
                        color: isPointNeedingReview ? '#facc15' : '#3b82f6',
                        fontWeight: 700,
                      }}
                    >
                      {formatConfidenceScore(props.confidence_score)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Raw VIIRS Confidence</span>
                    <span className="metric-data">
                      <span
                        className="popup-badge"
                        style={{
                          backgroundColor: rawViirsBadge.bg,
                          color: rawViirsBadge.color,
                          borderColor: rawViirsBadge.border,
                        }}
                      >
                        {formatRawViirsConfidence(props.confidence, severityLevel)}
                      </span>
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Severity Level</span>
                    <span className="metric-data">
                      <span className="popup-badge severity-badge">
                        Level {severityLevel}
                      </span>
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Acquisition Time</span>
                    <span className="metric-data">
                      {formatDateDisplay(props.acq_date, props.acq_time)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Brightness (T21)</span>
                    <span className="metric-data">
                      {props.brightness ? `${props.brightness} K` : 'N/A'}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Dist to Industrial Zone</span>
                    <span className="metric-data">
                      {formatDistance(props.dist_to_industrial_m)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Dist to Power Plant</span>
                    <span className="metric-data">
                      {formatDistance(props.dist_to_powerplant_m)}
                    </span>
                  </div>

                  <div className="popup-metric">
                    <span className="metric-title">Recurrence Count</span>
                    <span className="metric-data">
                      {props.recurrence_count ?? 'N/A'}
                    </span>
                  </div>
                </div>

                {/* Alert Dispatch & Escalation Info for Reported Points */}
                {associatedIncident && renderIncidentEscalationDetails(associatedIncident)}

                <div className="popup-footer-row">
                  <div className="popup-coords">
                    📍 {lat.toFixed(4)}, {lng.toFixed(4)}
                  </div>
                  {renderIncidentTriggerButton(props, pointId)}
                </div>
              </div>
            </div>
          </Popup>
        </CircleMarker>
      );
    });
  }, [
    filteredPoints,
    activeIncidentSourceIds,
    renderIncidentTriggerButton,
    findAssociatedIncident,
    renderIncidentEscalationDetails,
  ]);

  // Industrial Zone Popup Handler
  const onEachIndustrialZone = useCallback((feature, layer) => {
    const props = feature.properties || {};
    const name = props.name || 'Industrial Zone';
    const landuse = props.landuse || 'industrial';
    const manMade = props.man_made || 'N/A';

    const popupHtml = `
      <div class="popup-card">
        <div class="popup-header-tag" style="background-color: #8b5cf6; color: #ffffff;">
          🏭 Industrial Zone
        </div>
        <div class="popup-content">
          <div style="font-weight: 700; font-size: 0.95rem; margin-bottom: 8px; color: #ffffff;">
            ${name}
          </div>
          <div class="popup-metric-grid">
            <div class="popup-metric">
              <span className="metric-title">Land Use</span>
              <span className="metric-data" style="text-transform: capitalize;">${landuse}</span>
            </div>
            <div class="popup-metric">
              <span className="metric-title">Man Made</span>
              <span className="metric-data" style="text-transform: capitalize;">${manMade}</span>
            </div>
          </div>
        </div>
      </div>
    `;
    layer.bindPopup(popupHtml, { className: 'thermal-popup' });
  }, []);

  return (
    <div className="dashboard-container">
      {/* System Information Modal */}
      <SystemInfoModal
        isOpen={isHelpOpen}
        onClose={() => setIsHelpOpen(false)}
      />

      {/* Left Sidebar Component (Dark Slate & Navy-Blue) */}
      <aside className="dashboard-sidebar">
        {/* Sidebar Header */}
        <div className="sidebar-header">
          <div className="badge-row">
            <span className="live-pill">
              <span className="live-dot"></span> LIVE VIIRS FEED
            </span>
            <span className="sih-tag">SIH26162</span>
            <button
              className="help-icon-btn"
              onClick={() => setIsHelpOpen(true)}
              title="System Information & Telemetry Guide"
              aria-label="System Info and Help"
            >
              ?
            </button>
          </div>
          <h1 className="app-title">AGeny</h1>
          <p className="app-subtitle">
            AI Based Detection and Classification of Industrial Fires
          </p>
        </div>

        {/* TOP TAB NAVIGATION: DIRECTLY BELOW APP BRANDING HEADER */}
        <div className="sidebar-tab-bar top-sidebar-tab-bar">
          <button
            className={`sidebar-tab-btn ${sidebarTab === 'ANALYTICS' ? 'active' : ''}`}
            onClick={() => setSidebarTab('ANALYTICS')}
          >
            <span>📊 Dashboard</span>
          </button>
          <button
            className={`sidebar-tab-btn ${sidebarTab === 'INCIDENTS' ? 'active' : ''}`}
            onClick={() => setSidebarTab('INCIDENTS')}
          >
            <span>🚨 Alerts</span>
            {pendingIncidentsCount > 0 && (
              <span className="tab-badge-pill">{pendingIncidentsCount}</span>
            )}
          </button>
        </div>

        {/* TAB 1 CONTENT: DASHBOARD */}
        {sidebarTab === 'ANALYTICS' && (
          <div className="tab-panel-content">
            {/* Global Stats Overview Card */}
            <div className="stats-overview-card">
              <div className="stat-item main-stat">
                <span className="stat-label">Total Active Detections</span>
                <div className="stat-value">
                  {loading
                    ? '...'
                    : (selectedHours === null
                      ? (stats?.total_thermal_points ?? points.length)
                      : points.length
                    ).toLocaleString()}
                </div>
              </div>

              <div className="stat-subrow">
                <div className="stat-item">
                  <span className="stat-label">Visible on Map</span>
                  <span className="stat-subvalue">{loading ? '...' : filteredPoints.length.toLocaleString()}</span>
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

            {/* Flagged for Review Interactive Card */}
            <div
              className={`stat-card-review ${showOnlyNeedsReview ? 'selected' : ''}`}
              onClick={() => {
                const next = !showOnlyNeedsReview;
                setShowOnlyNeedsReview(next);
                if (next) {
                  setActiveCategoryFilter('ALL');
                }
              }}
              title={showOnlyNeedsReview ? 'Click to clear review filter (show all points)' : 'Click to filter map to only points needing review'}
            >
              <div className="review-card-top">
                <div className="review-title-wrap">
                  <span className="review-badge-icon">⚠️</span>
                  <span className="review-title">Flagged for Review</span>
                </div>
                <span className="review-count">
                  {selectedHours === null
                    ? (stats?.total_needs_review ?? needsReviewCount)
                    : needsReviewCount}
                </span>
              </div>
              <div className="review-meta-row">
                <span>Borderline Anomaly Triage (&lt; 0.70 Conf)</span>
                <span className="review-filter-hint">
                  {showOnlyNeedsReview ? 'Active Filter (Click to Clear)' : 'Click to Filter'}
                </span>
              </div>
            </div>

            {/* Time Window Filter Section */}
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
                * "All Time" displays a sample (max 4,000 detections) when dataset is large.
              </p>
            </div>

            {/* Map Context Layers Toggle Section */}
            <div className="layers-toggle-section">
              <label className="section-label">Map Context Layers</label>
              <div className="layer-toggles-card">
                <label className="layer-toggle-row">
                  <div className="layer-info">
                    <span className="layer-swatch industrial-swatch"></span>
                    <div className="layer-text">
                      <span className="layer-name">Industrial Zones</span>
                      <span className="layer-meta">
                        {industrialZones?.features ? `${industrialZones.features.length.toLocaleString()} zones` : 'OSM Polygons'}
                      </span>
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    className="material-checkbox"
                    checked={showIndustrialZones}
                    onChange={(e) => setShowIndustrialZones(e.target.checked)}
                  />
                </label>

                <label className="layer-toggle-row">
                  <div className="layer-info">
                    <span className="layer-swatch powerplant-swatch"></span>
                    <div className="layer-text">
                      <span className="layer-name">Power Plants</span>
                      <span className="layer-meta">
                        {powerPlants.length ? `${powerPlants.length.toLocaleString()} facilities` : 'WRI / IND DB'}
                      </span>
                    </div>
                  </div>
                  <input
                    type="checkbox"
                    className="material-checkbox"
                    checked={showPowerPlants}
                    onChange={(e) => setShowPowerPlants(e.target.checked)}
                  />
                </label>
              </div>
            </div>

            {/* Classification Breakdown Section */}
            <div className="categories-section">
              <div className="section-header-row">
                <label className="section-label">Classification Breakdown</label>
                {(activeCategoryFilter !== 'ALL' || showOnlyNeedsReview) && (
                  <button
                    className="reset-filter-link"
                    onClick={() => {
                      setActiveCategoryFilter('ALL');
                      setShowOnlyNeedsReview(false);
                    }}
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
                      onClick={() => {
                        setShowOnlyNeedsReview(false);
                        setActiveCategoryFilter(isActive ? 'ALL' : catKey);
                      }}
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
          </div>
        )}

        {/* TAB 2 CONTENT: INCIDENT RESPONSE & ESCALATION */}
        {sidebarTab === 'INCIDENTS' && (
          <div className="tab-panel-content">
            <div className="incident-response-section">
              <div className="section-header-row">
                <label className="section-label">Incident Response & Escalation</label>
                {pendingIncidentsCount > 0 && (
                  <span className="pending-badge-pill">{pendingIncidentsCount} Active</span>
                )}
              </div>

              {/* Simulate Incident Action Card */}
              <div className="simulate-action-card">
                <button
                  className="simulate-trigger-btn"
                  onClick={handleSimulateIncident}
                  disabled={isSimulating}
                >
                  {isSimulating ? (
                    <>
                      <span className="spinner-inline"></span>
                      <span>Simulating & Dispatching...</span>
                    </>
                  ) : (
                    <>
                      <span>🔥 Simulate Incident</span>
                    </>
                  )}
                </button>

                {simulatingSuccess && (
                  <div className="simulate-success-banner">
                    <span>✓</span> {simulatingSuccess}
                  </div>
                )}
              </div>

              {/* Active Queue List (Dark Slate Theme Matching Specifications) */}
              <div className="incidents-list-container">
                {incidents.length === 0 ? (
                  <div className="incidents-empty-state">
                    <div className="empty-icon">🛡️</div>
                    <h4>No Incidents in Queue</h4>
                    <p>
                      Click <strong>"🔥 Simulate Incident"</strong> to test live 3-Tier escalation.
                    </p>
                  </div>
                ) : (
                  incidents.map((incident) => {
                    const confBadge = getConfidenceBadge(incident.confidence, incident.severity_level);
                    const tier = TIER_CONFIG[incident.current_tier] || TIER_CONFIG[0];
                    const isPending = incident.status === 'pending';
                    const isParallel = incident.alert_mode === 'parallel';
                    const countdown = getEscalationCountdown(incident, nowTick);
                    const isAck = !isPending;

                    return (
                      <div
                        key={`incident-card-dark-${incident.id}`}
                        className={`incident-card-dark ${isAck ? 'card-ack' : 'card-pending'}`}
                      >
                        {/* Top Badge Row */}
                        <div className="incident-card-top-row">
                          <div className="incident-badges-left">
                            <span className="incident-pill-id">#{incident.id}</span>
                            {incident.classification && (
                              <span
                                className="incident-pill-class"
                                style={{
                                  backgroundColor: incident.classification.toLowerCase().includes('unplanned')
                                    ? 'rgba(220, 38, 38, 0.2)'
                                    : incident.classification.toLowerCase().includes('wildfire')
                                      ? 'rgba(249, 115, 22, 0.2)'
                                      : 'rgba(37, 99, 235, 0.2)',
                                  color: getCategoryColor(incident.classification),
                                  borderColor: getCategoryColor(incident.classification),
                                }}
                              >
                                {incident.classification}
                              </span>
                            )}
                            <span
                              className="incident-pill-conf"
                              style={{
                                color: confBadge.color,
                                backgroundColor: confBadge.bg,
                                borderColor: confBadge.border,
                              }}
                            >
                              {confBadge.label}
                            </span>
                          </div>

                          <span
                            className="incident-pill-tier"
                            style={{
                              color: isParallel ? '#f87171' : tier.color,
                              backgroundColor: isParallel ? 'rgba(239, 68, 68, 0.15)' : tier.bg,
                              borderColor: isParallel ? '#f87171' : tier.color,
                            }}
                          >
                            {isParallel ? 'Parallel (Tiers 0-2)' : `${tier.label} (${tier.title})`}
                          </span>
                        </div>

                        {/* Inner Data Grid Box (FRP & Coordinates) */}
                        <div className="incident-data-box">
                          <div className="data-box-row">
                            <span className="data-box-lbl">Fire Radiative Power:</span>
                            <span className="data-box-val-frp">
                              {incident.frp !== undefined && incident.frp !== null
                                ? `${Number(incident.frp).toFixed(1)} MW`
                                : 'N/A'}
                            </span>
                          </div>
                          <div className="data-box-row">
                            <span className="data-box-lbl">Coordinates:</span>
                            <code className="data-box-coords">
                              {incident.latitude?.toFixed(4)}, {incident.longitude?.toFixed(4)}
                            </code>
                          </div>
                        </div>

                        {/* Stepper Progress Line */}
                        <div className="incident-stepper-wrap">
                          <div className="incident-stepper-track">
                            <div className={`step-node ${incident.tier_0_sent_at || isAck ? 'step-completed' : 'step-pending'}`}>
                              <span className="step-num">0</span>
                            </div>
                            <div className={`step-line ${incident.current_tier >= 1 || isAck ? 'line-active' : ''}`}></div>
                            <div className={`step-node ${incident.tier_1_sent_at || (incident.current_tier >= 1) || isAck ? 'step-completed' : 'step-pending'}`}>
                              <span className="step-num">1</span>
                            </div>
                            <div className={`step-line ${incident.current_tier >= 2 || isAck ? 'line-active' : ''}`}></div>
                            <div className={`step-node ${incident.tier_2_sent_at || (incident.current_tier >= 2) || isAck ? 'step-completed' : 'step-pending'}`}>
                              <span className="step-num">2</span>
                            </div>
                          </div>
                          <div className="stepper-labels-row">
                            <span>Tier 0 (Operator)</span>
                            <span>Tier 1 (Supervisor)</span>
                            <span>Tier 2 (Command)</span>
                          </div>
                          {!isAck && !isParallel && (
                            <div className="stepper-sub-note">
                              ⏱️ Auto-escalating in 60s if unacknowledged
                            </div>
                          )}
                        </div>

                        {/* Status / Acknowledgment Banner */}
                        {isAck ? (
                          <div className="incident-ack-banner">
                            <div className="ack-banner-title">
                              <span className="ack-check-icon">✓</span>
                              <strong>Acknowledged by {incident.acknowledged_by || 'Dashboard Operator'}</strong>
                            </div>
                            {incident.acknowledged_at && (
                              <div className="ack-banner-time">
                                Logged at: {new Date(incident.acknowledged_at).toLocaleTimeString()}
                              </div>
                            )}
                          </div>
                        ) : (
                          <div className="incident-pending-banner">
                            <div className="pending-banner-title">
                              <span className="pending-pulse-dot"></span>
                              <strong>Status: Pending Response</strong>
                              {isParallel && <span className="parallel-mode-badge">⚡ Parallel Mode</span>}
                            </div>
                            {isParallel ? (
                              <div className="pending-countdown-text">
                                High Confidence: Tiers 0, 1 & 2 dispatched simultaneously
                              </div>
                            ) : countdown && (
                              <div className="pending-countdown-text">
                                {countdown.isMax ? (
                                  <span>🚨 Tier 2 Sent (District Command Active)</span>
                                ) : (
                                  <span>
                                    Auto-Escalating to <strong>Tier {countdown.nextTier}</strong> in{' '}
                                    <strong className="countdown-sec">{countdown.seconds}s</strong> if unacknowledged
                                  </span>
                                )}
                              </div>
                            )}
                          </div>
                        )}

                        {/* Card Footer */}
                        <div className="incident-card-dark-footer">
                          <div className="incident-date-stamp">
                            <span>📅 {new Date(incident.created_at).toLocaleString(undefined, {
                              month: 'short',
                              day: 'numeric',
                              hour: '2-digit',
                              minute: '2-digit',
                              second: '2-digit',
                            })}</span>
                          </div>

                          <div className="incident-card-actions">
                            {isPending && (
                              <button
                                className="dark-card-ack-btn"
                                onClick={() => handleAcknowledgeIncident(incident.id)}
                                title="Acknowledge this incident"
                              >
                                ✓ Acknowledge
                              </button>
                            )}
                            <button
                              className="dark-card-locate-btn"
                              onClick={() => setMapTargetCoords([incident.latitude, incident.longitude])}
                              title="Center map on incident coordinates"
                            >
                              📍 Locate
                            </button>
                          </div>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>
            </div>
          </div>
        )}

        {/* Selected Point Inspector (Active Across Tabs at bottom of sidebar) */}
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
                <span>Confidence:</span>
                <strong
                  style={{
                    color: isPointFlaggedForReview(selectedPoint) ? '#facc15' : '#3b82f6',
                  }}
                >
                  {formatConfidenceScore(selectedPoint.confidence_score)}
                  {isPointFlaggedForReview(selectedPoint) && ' (Review Required)'}
                </strong>
              </div>
              <div className="inspector-row">
                <span>FRP (Power):</span>
                <strong>{selectedPoint.frp ? `${selectedPoint.frp} MW` : 'N/A'}</strong>
              </div>
              <div className="inspector-row">
                <span>Distance to Industrial:</span>
                <strong>{formatDistance(selectedPoint.dist_to_industrial_m)}</strong>
              </div>
              <div className="inspector-row">
                <span>Distance to Power Plant:</span>
                <strong>{formatDistance(selectedPoint.dist_to_powerplant_m)}</strong>
              </div>
              <div className="inspector-row">
                <span>Coordinates:</span>
                <code>
                  {selectedPoint.latitude?.toFixed(4)}, {selectedPoint.longitude?.toFixed(4)}
                </code>
              </div>
            </div>

            {/* Alert Escalation Info in Inspector */}
            {findAssociatedIncident(selectedPoint) &&
              renderIncidentEscalationDetails(findAssociatedIncident(selectedPoint))}

            {renderIncidentTriggerButton(selectedPoint, true)}
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
          <MapFlyController targetCoords={mapTargetCoords} />

          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors | NASA FIRMS'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />

          {/* 1. Industrial Zones Polygon Layer */}
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

          {/* 2. Power Plants Layer */}
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

          {/* 3. Clustered Thermal Point Markers Layer (Dynamic key forces clean cluster recalculation on filter changes) */}
          <MarkerClusterGroup
            key={`cluster-group-${showOnlyNeedsReview ? 'review' : (activeClassification || 'all')}-${filteredPoints.length}-${selectedHours === null ? 'all' : selectedHours}`}
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
            <span className="legend-count-pill">{filteredPoints.length.toLocaleString()} Detections</span>
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

            {/* Review Flag Highlight */}
            <div className="legend-row">
              <span className="legend-bullet-review"></span>
              <span className="legend-label">Flagged for Review (&lt; 0.70 Conf)</span>
            </div>

            {/* Active Incident Pulsing Ring Indicator */}
            <div className="legend-row">
              <span className="legend-bullet-pulsing-ring"></span>
              <span className="legend-label">
                Active Incident {pendingIncidentsCount > 0 ? `(${pendingIncidentsCount})` : ''}
              </span>
            </div>

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
          <div className="legend-attribution-row">
            <span>NASA FIRMS &bull; OpenStreetMap</span>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
