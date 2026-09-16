import React from 'react';
import { HelpIcon, CloseIcon } from './MaterialIcons';

export default function SystemInfoModal({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <div className="settings-modal-backdrop" onClick={onClose}>
      <div
        className="settings-modal-card system-info-modal-card"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="system-info-title"
      >
        {/* Modal Header */}
        <div className="settings-modal-header">
          <div className="settings-header-title">
            <span className="settings-header-icon">
              <HelpIcon size={22} />
            </span>
            <h2 id="system-info-title">System Information</h2>
          </div>
          <button
            className="settings-close-btn"
            onClick={onClose}
            aria-label="Close system information"
          >
            <CloseIcon size={20} />
          </button>
        </div>

        {/* Modal Body */}
        <div className="settings-modal-body">
          {/* App Overview */}
          <div className="settings-section">
            <div className="settings-label-row">
              <span className="settings-section-title">AGeny (SIH26162)</span>
              <span className="live-pill">
                <span className="live-dot"></span> Active Sentinel
              </span>
            </div>
            <p className="settings-section-desc">
              AI-Based Detection and Classification of Industrial Fires using Near-Real-Time NASA FIRMS satellite telemetry with Automated 3-Tier Alerting.
            </p>
          </div>

          {/* System Telemetry Parameters */}
          <div className="settings-section">
            <span className="settings-section-title">Telemetry & Satellite Constellation</span>
            <div className="system-info-details-table">
              <div className="system-info-table-row">
                <span className="info-row-key">Satellite Sensors</span>
                <strong className="info-row-val">VIIRS (Suomi-NPP, NOAA-20, NOAA-21)</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">Spatial Resolution</span>
                <strong className="info-row-val">375m I-band (Nominal pixel size)</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">Telemetry Feeds</span>
                <strong className="info-row-val">Fire Radiative Power (FRP), Brightness Temp (K)</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">Live Polling Rate</span>
                <strong className="info-row-val">Real-time 3-second cadence (Configurable)</strong>
              </div>
            </div>
          </div>

          {/* Automated Escalation Protocol */}
          <div className="settings-section">
            <span className="settings-section-title">Automated 3-Tier Escalation Protocol</span>
            <div className="system-info-details-table">
              <div className="system-info-table-row">
                <span className="info-row-key">Tier 0 (0s)</span>
                <strong className="info-row-val">Local Plant Operator & Emergency SMS/Email</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">Tier 1 (30s)</span>
                <strong className="info-row-val">Safety Supervisor & Zone Fire Marshall</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">Tier 2 (60s)</span>
                <strong className="info-row-val">Emergency Command & Municipal Response</strong>
              </div>
              <div className="system-info-table-row">
                <span className="info-row-key">High-Confidence Override</span>
                <strong className="info-row-val">Parallel Dispatch (Tiers 0, 1 & 2 at 0s)</strong>
              </div>
            </div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="settings-modal-footer">
          <button className="settings-done-btn" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
