/**
 * device-grid.js
 *
 * Subscribes to the deployment WebSocket and updates the device status table
 * in real time as devices progress through phases.
 *
 * Depends on: deployment_detail.html rendering elements with IDs:
 *   #device-row-<device_id>     — the <tr> for this device
 *   #device-status-<device_id>  — the status badge <span>
 *   #device-phase-<device_id>   — the current phase name <td>
 *   #device-ip-<device_id>      — the discovered IP <td>
 *
 * Usage (called from deployment_detail.html):
 *   initDeviceGrid(deploymentId);
 */

'use strict';

function initDeviceGrid(deploymentId) {
  // We piggyback on the same WebSocket opened by phase-tracker.js if available,
  // but open our own if not (safe to have two consumers on the same group).
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${protocol}://${window.location.host}/ws/deployments/${deploymentId}/`;

  let socket = null;
  let reconnectDelay = 1000;
  const MAX_RECONNECT_DELAY = 30000;

  // Log buffer per device (device_id → [{level, message, timestamp}])
  const logBuffer = {};
  const MAX_LOG_LINES = 200;

  function connect() {
    socket = new WebSocket(wsUrl);

    socket.addEventListener('open', () => {
      console.log('[device-grid] WS connected');
      reconnectDelay = 1000;
    });

    socket.addEventListener('close', () => {
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    });

    socket.addEventListener('error', () => socket.close());

    socket.addEventListener('message', (ev) => {
      let event;
      try { event = JSON.parse(ev.data); } catch { return; }
      handleEvent(event);
    });
  }

  // -------------------------------------------------------------------------
  // Colour → CSS badge class mapping
  // -------------------------------------------------------------------------
  const COLOUR_BADGE = {
    grey:  'badge-grey',
    blue:  'badge-blue',
    green: 'badge-green',
    amber: 'badge-amber',
    red:   'badge-red',
    teal:  'badge-teal',
    cyan:  'badge-cyan',
  };

  const ROW_CLASS = {
    grey:  '',
    blue:  'device-running',
    green: 'device-done',
    amber: 'device-done',
    red:   'device-failed',
  };

  // Human-readable status labels (matching Django choices)
  const STATUS_LABEL = {
    pending:           'Pending',
    discovered:        'Discovered',
    cabling_validated: 'Cabling Validated',
    firmware_staged:   'Firmware Staged',
    configuring:       'Configuring',
    configured:        'Configured',
    provisioning:      'Provisioning',
    provisioned:       'Provisioned',
    verified:          'Verified',
    failed:            'Failed',
    missing:           'Missing',
  };

  function handleEvent(event) {
    switch (event.type) {
      case 'device.status_changed':
        updateDeviceStatus(event);
        break;
      case 'device.log':
        appendDeviceLog(event);
        break;
    }
  }

  // -------------------------------------------------------------------------
  // DOM updates
  // -------------------------------------------------------------------------

  function updateDeviceStatus(event) {
    const { device_id, hostname, status, status_colour, current_phase_name, discovered_ip } = event;

    const row = document.getElementById(`device-row-${device_id}`);
    if (!row) return;

    // Update badge
    const badge = document.getElementById(`device-status-${device_id}`);
    if (badge) {
      // Remove old badge colour classes
      badge.className = 'badge';
      const colourClass = COLOUR_BADGE[status_colour] || 'badge-grey';
      badge.classList.add(colourClass);
      badge.textContent = STATUS_LABEL[status] || status;
    }

    // Update current phase cell
    const phaseCell = document.getElementById(`device-phase-${device_id}`);
    if (phaseCell && current_phase_name) {
      phaseCell.textContent = current_phase_name;
    }

    // Update IP cell
    if (discovered_ip) {
      const ipCell = document.getElementById(`device-ip-${device_id}`);
      if (ipCell) ipCell.textContent = discovered_ip;
    }

    // Update row highlight class
    row.className = ROW_CLASS[status_colour] || '';

    // Flash the row briefly to draw attention
    row.style.transition = 'background-color 0.3s';
    row.style.backgroundColor = 'rgba(77, 166, 255, 0.08)';
    setTimeout(() => { row.style.backgroundColor = ''; }, 600);
  }

  function appendDeviceLog(event) {
    const { device_id, level, message, timestamp } = event;

    if (!logBuffer[device_id]) logBuffer[device_id] = [];
    const buf = logBuffer[device_id];
    buf.push({ level, message, timestamp });
    if (buf.length > MAX_LOG_LINES) buf.shift();

    // If there is an open <details> log viewer for this device, append there
    const logViewer = document.getElementById(`device-logs-${device_id}`);
    if (logViewer) {
      const entry = document.createElement('div');
      entry.style.cssText = `padding:0.15rem 0; color:${logColour(level)}`;
      const ts = timestamp ? new Date(timestamp).toTimeString().slice(0, 8) : '';
      entry.textContent = `${ts} [${level}] ${message}`;
      logViewer.appendChild(entry);
      logViewer.scrollTop = logViewer.scrollHeight;
    }
  }

  function logColour(level) {
    return { ERROR: '#ff6b6b', WARN: '#ffb84d', DEBUG: '#666' }[level] || '#aaa';
  }

  connect();
}
