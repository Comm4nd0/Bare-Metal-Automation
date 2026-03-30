"""
API client for the Bare Metal Automation dashboard.

Used by the automation process (orchestrator, discovery, configurator) to push
status updates to the Django dashboard while a deployment is running.

Usage:
    from bare_metal_automation.dashboard.api_client import DashboardClient

    client = DashboardClient("http://localhost:8080")
    dep = client.create_deployment("DC-Rack-42", "10.255.0.0/16", "10.255.255.1", 100)
    client.add_device(dep["id"], ip="10.255.0.10", serial="FOC2145X0AB", ...)
    client.update_deployment(dep["id"], phase="discovery")
    client.update_device_by_serial(dep["id"], "FOC2145X0AB", state="configured")
    client.log(dep["id"], "INFO", "discovery", "Found 5 devices")
"""

from __future__ import annotations

import json
import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


class DashboardClient:
    """HTTP client for pushing status updates to the Bare Metal Automation dashboard API."""

    def __init__(self, base_url: str = "http://localhost:8080") -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.post(url, data=json.dumps(data))
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Dashboard API call failed: %s %s — %s", "POST", path, exc)
            return {}

    def _put(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.put(url, data=json.dumps(data))
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Dashboard API call failed: %s %s — %s", "PUT", path, exc)
            return {}

    def _get(self, path: str) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            resp = self.session.get(url)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Dashboard API call failed: %s %s — %s", "GET", path, exc)
            return {}

    # ── Deployment lifecycle ────────────────────────────────────────────────

    def create_deployment(
        self,
        name: str,
        bootstrap_subnet: str = "",
        laptop_ip: str = "",
        management_vlan: int = 0,
    ) -> dict[str, Any]:
        """Create a new deployment and return {"id": ..., "name": ...}."""
        return self._post("/api/deployments/", {
            "name": name,
            "bootstrap_subnet": bootstrap_subnet,
            "laptop_ip": laptop_ip,
            "management_vlan": management_vlan,
        })

    def update_deployment(self, deployment_id: int, **fields: Any) -> dict[str, Any]:
        """Update deployment fields (e.g. phase="discovery")."""
        return self._put(f"/api/deployments/{deployment_id}/update/", fields)

    def get_status(self) -> dict[str, Any]:
        """Get current deployment status."""
        return self._get("/api/status/")

    # ── Device management ───────────────────────────────────────────────────

    def add_device(self, deployment_id: int, **device_fields: Any) -> dict[str, Any]:
        """Register or update a discovered device. Must include 'ip'."""
        return self._post(f"/api/deployments/{deployment_id}/devices/", device_fields)

    def update_device(self, device_id: int, **fields: Any) -> dict[str, Any]:
        """Update a device by its database ID."""
        return self._put(f"/api/devices/{device_id}/update/", fields)

    def update_device_by_serial(
        self, deployment_id: int, serial: str, **fields: Any
    ) -> dict[str, Any]:
        """Update a device by serial number (convenience for automation)."""
        return self._post(
            f"/api/deployments/{deployment_id}/devices/serial/{serial}/",
            fields,
        )

    def get_device(self, device_id: int) -> dict[str, Any]:
        """Get device status including cabling results."""
        return self._get(f"/api/devices/{device_id}/status/")

    # ── Cabling results ─────────────────────────────────────────────────────

    def set_cabling_results(
        self, device_id: int, results: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Replace cabling validation results for a device."""
        return self._post(f"/api/devices/{device_id}/cabling/", {"results": results})

    # ── Logging ─────────────────────────────────────────────────────────────

    def log(
        self,
        deployment_id: int,
        level: str,
        phase: str,
        message: str,
    ) -> dict[str, Any]:
        """Add a log entry to the deployment."""
        return self._post(f"/api/deployments/{deployment_id}/logs/", {
            "level": level,
            "phase": phase,
            "message": message,
        })

    def get_logs(self, deployment_id: int, limit: int = 50) -> dict[str, Any]:
        """Retrieve recent log entries."""
        return self._get(f"/api/deployments/{deployment_id}/logs/?limit={limit}")
