/**
 * phase-tracker.js
 *
 * Subscribes to the deployment WebSocket and updates the phase pipeline
 * traffic lights in real time.
 *
 * Depends on: deployment_detail.html rendering elements with IDs:
 *   #phase-<phase_number>   — the phase item anchor
 *   #progress-bar           — the progress bar fill div
 *   #progress-pct           — the progress percentage text
 *
 * Usage (called from deployment_detail.html):
 *   initPhaseTracker(deploymentId);
 */

'use strict';

function initPhaseTracker(deploymentId) {
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${protocol}://${window.location.host}/ws/deployments/${deploymentId}/`;

  let socket = null;
  let reconnectDelay = 1000;
  const MAX_RECONNECT_DELAY = 30000;

  function connect() {
    socket = new WebSocket(wsUrl);

    socket.addEventListener('open', () => {
      console.log('[phase-tracker] WS connected');
      reconnectDelay = 1000;
      // Send periodic pings to keep the connection alive
      socket._pingInterval = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: 'ping' }));
        }
      }, 25000);
    });

    socket.addEventListener('close', (ev) => {
      console.log('[phase-tracker] WS closed', ev.code);
      clearInterval(socket._pingInterval);
      // Reconnect with exponential back-off (max 30s)
      setTimeout(connect, reconnectDelay);
      reconnectDelay = Math.min(reconnectDelay * 2, MAX_RECONNECT_DELAY);
    });

    socket.addEventListener('error', (err) => {
      console.warn('[phase-tracker] WS error', err);
      socket.close();
    });

    socket.addEventListener('message', (ev) => {
      let event;
      try {
        event = JSON.parse(ev.data);
      } catch {
        return;
      }
      handleEvent(event);
    });
  }

  // -------------------------------------------------------------------------
  // Traffic light colours mapped from phase status
  // -------------------------------------------------------------------------
  const STATUS_COLOUR = {
    pending:   'grey',
    running:   'blue',
    completed: 'green',
    warning:   'amber',
    failed:    'red',
    skipped:   'grey',
  };

  function handleEvent(event) {
    switch (event.type) {
      case 'phase.started':
        setPhaseColour(event.phase_number, 'blue', true);
        break;

      case 'phase.completed':
        setPhaseColour(event.phase_number, event.warning_count ? 'amber' : 'green', false);
        updateProgress();
        break;

      case 'phase.failed':
        setPhaseColour(event.phase_number, 'red', false);
        break;

      case 'deployment.completed':
        showBanner('Deployment completed successfully!', 'green');
        break;

      case 'deployment.failed':
        showBanner(`Deployment failed: ${event.error_message || 'unknown error'}`, 'red');
        break;

      case 'pong':
        break;
    }
  }

  // -------------------------------------------------------------------------
  // DOM helpers
  // -------------------------------------------------------------------------

  function setPhaseColour(phaseNumber, colour, running) {
    const item = document.getElementById(`phase-${phaseNumber}`);
    if (!item) return;

    const dot = item.querySelector('.phase-dot');
    if (!dot) return;

    // Remove all tl- classes
    dot.classList.forEach(cls => {
      if (cls.startsWith('tl-')) dot.classList.remove(cls);
    });
    dot.classList.add(`tl-${colour}`);

    // Remove existing pulse
    const oldPulse = dot.querySelector('.phase-pulse');
    if (oldPulse) oldPulse.remove();

    if (running) {
      const pulse = document.createElement('div');
      pulse.className = 'phase-pulse';
      dot.appendChild(pulse);
      item.classList.add('current');
    } else {
      item.classList.remove('current');
    }

    // Update label colour
    const label = item.querySelector('.phase-label');
    if (label) {
      label.style.color = running ? '#fff' : '';
    }
  }

  function updateProgress() {
    // Count completed/skipped phases from the DOM
    const pipeline = document.getElementById('phase-pipeline');
    if (!pipeline) return;

    const dots = pipeline.querySelectorAll('.phase-dot');
    let total = dots.length;
    let done = 0;
    dots.forEach(dot => {
      if (dot.classList.contains('tl-green') || dot.classList.contains('tl-amber')) {
        done++;
      }
    });

    const pct = total ? Math.round(done / total * 100) : 0;

    const bar = document.getElementById('progress-bar');
    if (bar) bar.style.width = `${pct}%`;

    const label = document.getElementById('progress-pct');
    if (label) label.textContent = `${pct}%`;
  }

  function showBanner(message, colour) {
    const existing = document.getElementById('phase-tracker-banner');
    if (existing) existing.remove();

    const banner = document.createElement('div');
    banner.id = 'phase-tracker-banner';
    banner.style.cssText = `
      position: fixed; top: 1rem; right: 1rem;
      padding: 0.75rem 1.25rem;
      border-radius: 6px;
      font-size: 0.875rem;
      z-index: 9999;
      max-width: 400px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.4);
      ${colour === 'green'
        ? 'background:#0d3d2a; border:1px solid #1a6b4a; color:#4caf82;'
        : 'background:#4a0d0d; border:1px solid #8b1a1a; color:#ff6b6b;'}
    `;
    banner.textContent = message;
    document.body.appendChild(banner);

    setTimeout(() => banner.remove(), 8000);
  }

  connect();
}
