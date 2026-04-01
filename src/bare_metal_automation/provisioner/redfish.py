"""HPE iLO 5 Redfish API client — authentication and session management.

Extracts the low-level HTTP plumbing from ``server.py`` into a standalone
module so it can be reused by ``ilo.py``, ``installer.py``, and the
factory-reset sanitiser.

Features
--------
- Persistent ``requests.Session`` (TCP keep-alive, connection pooling)
- Session token authentication (preferred over per-request Basic auth)
- Automatic retry on transient 503/504 errors (iLO resets mid-task)
- Convenience GET / POST / PATCH / PUT / DELETE that raise on HTTP errors
- System-level helpers: inventory, power state, POST state
"""

from __future__ import annotations

import logging
import time

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger(__name__)

# Redfish standard paths
REDFISH_ROOT = "/redfish/v1"
REDFISH_SYSTEMS = f"{REDFISH_ROOT}/Systems/1"
REDFISH_MANAGERS = f"{REDFISH_ROOT}/Managers/1"
REDFISH_CHASSIS = f"{REDFISH_ROOT}/Chassis/1"
REDFISH_ACCOUNT_SVC = f"{REDFISH_ROOT}/AccountService"
REDFISH_UPDATE_SVC = f"{REDFISH_ROOT}/UpdateService"
REDFISH_SESSIONS = f"{REDFISH_ROOT}/SessionService/Sessions"

# Retry parameters for transient iLO errors
_MAX_RETRIES = 3
_RETRY_BACKOFF = 10  # seconds


class RedfishError(Exception):
    """Raised when a Redfish API call fails."""


class RedfishClient:
    """Low-level Redfish HTTP client for HPE iLO 5.

    Supports both Basic auth (quick probes) and session tokens (long-running
    provisioning tasks where Basic-auth headers can cause session conflicts).

    Usage::

        client = RedfishClient("10.255.1.10", "Administrator", "adminpass")
        system = client.get(REDFISH_SYSTEMS)
        client.post(
            f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
            {"ResetType": "GracefulRestart"},
        )
        client.close()
    """

    def __init__(
        self,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
        use_session_auth: bool = False,
    ) -> None:
        self.host = host
        self.base_url = f"https://{host}"
        self._username = username
        self._password = password
        self.verify = verify_ssl
        self._session_token: str | None = None

        self._http = requests.Session()
        self._http.verify = verify_ssl
        self._http.headers.update({
            "Content-Type": "application/json",
            "OData-Version": "4.0",
        })

        if use_session_auth:
            self._create_session()
        else:
            self._http.auth = HTTPBasicAuth(username, password)

    # ── Session management ─────────────────────────────────────────────────

    def _create_session(self) -> None:
        """Exchange credentials for a session token (X-Auth-Token)."""
        resp = self._http.post(
            f"{self.base_url}{REDFISH_SESSIONS}",
            json={"UserName": self._username, "Password": self._password},
        )
        if resp.status_code not in (200, 201):
            logger.warning(
                f"Session creation failed ({resp.status_code}) — "
                f"falling back to Basic auth"
            )
            self._http.auth = HTTPBasicAuth(self._username, self._password)
            return

        token = resp.headers.get("X-Auth-Token")
        if token:
            self._session_token = token
            self._http.headers["X-Auth-Token"] = token
            self._http.auth = None
            logger.debug(f"Redfish session created for {self.host}")
        else:
            self._http.auth = HTTPBasicAuth(self._username, self._password)

    def close(self) -> None:
        """Delete the iLO session token (logout) and close HTTP connections."""
        if self._session_token:
            try:
                sessions = self._http.get(
                    f"{self.base_url}{REDFISH_SESSIONS}"
                ).json()
                for member in sessions.get("Members", []):
                    s = self._http.get(
                        f"{self.base_url}{member['@odata.id']}"
                    ).json()
                    if s.get("UserName") == self._username:
                        self._http.delete(
                            f"{self.base_url}{member['@odata.id']}"
                        )
                        break
            except Exception:
                pass
            self._session_token = None
        self._http.close()

    def __enter__(self) -> "RedfishClient":
        return self

    def __exit__(self, *_) -> None:  # type: ignore[override]
        self.close()

    # ── HTTP verbs ─────────────────────────────────────────────────────────

    def get(self, path: str) -> dict:
        return self._request("GET", path).json()

    def post(
        self, path: str, data: dict | None = None
    ) -> requests.Response:
        return self._request("POST", path, json=data or {})

    def patch(self, path: str, data: dict) -> requests.Response:
        return self._request("PATCH", path, json=data)

    def put(self, path: str, data: dict) -> requests.Response:
        return self._request("PUT", path, json=data)

    def delete(self, path: str) -> requests.Response:
        return self._request("DELETE", path)

    def _request(
        self,
        method: str,
        path: str,
        **kwargs,  # type: ignore[no-untyped-def]
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                resp = self._http.request(method, url, **kwargs)
                if resp.status_code in (503, 504) and attempt < _MAX_RETRIES:
                    logger.debug(
                        f"iLO transient {resp.status_code} on {method} {path} "
                        f"— retry {attempt}/{_MAX_RETRIES}"
                    )
                    time.sleep(_RETRY_BACKOFF * attempt)
                    continue
                resp.raise_for_status()
                return resp
            except requests.exceptions.ConnectionError:
                if attempt < _MAX_RETRIES:
                    time.sleep(_RETRY_BACKOFF * attempt)
                    continue
                raise
        raise RedfishError(f"All {_MAX_RETRIES} attempts failed: {method} {path}")

    # ── Convenience helpers ────────────────────────────────────────────────

    def system_inventory(self) -> dict:
        """Return the /redfish/v1/Systems/1 resource."""
        return self.get(REDFISH_SYSTEMS)

    def power_state(self) -> str:
        """Return current server power state (e.g. 'On', 'Off')."""
        return self.system_inventory().get("PowerState", "Unknown")

    def post_state(self) -> str:
        """Return iLO POST state (e.g. 'FinishedPost', 'InPost')."""
        system = self.system_inventory()
        return (
            system.get("Oem", {}).get("Hpe", {}).get("PostState", "Unknown")
        )

    def wait_for_post(self, timeout: int = 600, poll: int = 15) -> bool:
        """Block until the server completes POST after a reboot.

        Returns True if POST completed within *timeout* seconds.
        """
        start = time.time()
        while time.time() - start < timeout:
            try:
                state = self.post_state()
                if state in ("FinishedPost", "InPostDiscoveryComplete"):
                    return True
            except Exception:
                pass  # iLO may be temporarily unreachable during reset
            time.sleep(poll)
        logger.warning(f"POST did not complete within {timeout}s on {self.host}")
        return False

    def wait_for_ilo(self, timeout: int = 300, poll: int = 15) -> bool:
        """Wait for iLO to become reachable after a firmware reset/update."""
        start = time.time()
        while time.time() - start < timeout:
            try:
                self.get(REDFISH_ROOT)
                return True
            except Exception:
                time.sleep(poll)
        logger.warning(f"iLO at {self.host} not reachable after {timeout}s")
        return False

    def reset_server(self, reset_type: str = "GracefulRestart") -> None:
        """Trigger a server reset via Redfish."""
        try:
            self.post(
                f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                {"ResetType": reset_type},
            )
            logger.info(f"Server {self.host} reset ({reset_type})")
        except requests.exceptions.HTTPError:
            # GracefulRestart may fail if OS is not up — try ForceRestart
            if reset_type == "GracefulRestart":
                self.post(
                    f"{REDFISH_SYSTEMS}/Actions/ComputerSystem.Reset",
                    {"ResetType": "ForceRestart"},
                )
                logger.info(f"Server {self.host} force-restarted")
            else:
                raise
