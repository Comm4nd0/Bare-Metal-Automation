"""Prepare Build runner — pull data from NetBox and stage files.

Mirrors the deployment.py threading pattern exactly:
    GET  /api/prepare/nodes/    → list available nodes from NetBox
    POST /api/prepare/start/    → start preparation in background
    POST /api/prepare/stop/     → cancel preparation
    GET  /api/prepare/status/   → return preparation progress
"""

from __future__ import annotations

import copy
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

# Module-level preparation state (mirrors deployment.py pattern)
_prepare_thread: threading.Thread | None = None
_prepare_lock = threading.Lock()
_prepare_stop = threading.Event()
_prepare_state: dict[str, Any] = {
    "phase": "idle",
    "progress": 0,
    "message": "",
    "error": "",
    "node_tag": "",
    "device_count": 0,
}


def _update_state(**kwargs: Any) -> None:
    """Update the shared preparation state dict."""
    global _prepare_state
    _prepare_state.update(kwargs)


def _run_prepare(node_tag: str) -> None:
    """Thread target: run the full preparation sequence."""
    try:
        from django.conf import settings as django_settings

        netbox_url = getattr(django_settings, "BMA_NETBOX_URL", "")
        netbox_token = getattr(django_settings, "BMA_NETBOX_TOKEN", "")
        git_repo_url = getattr(django_settings, "BMA_GIT_REPO_URL", "")
        git_branch = getattr(
            django_settings, "BMA_GIT_REPO_BRANCH", "main",
        )
        git_path = getattr(django_settings, "BMA_GIT_REPO_PATH", "configs")
        inventory_path = getattr(
            django_settings,
            "BMA_INVENTORY_PATH",
            "configs/inventory/inventory.yaml",
        )

        # Phase 1: Connect to NetBox
        _update_state(
            phase="connecting",
            progress=10,
            message="Connecting to NetBox...",
        )
        if _prepare_stop.is_set():
            return

        from bare_metal_automation.netbox.client import (
            NetBoxClient,
            NetBoxConnectionError,
        )

        try:
            client = NetBoxClient(netbox_url, netbox_token)
            status = client.ping()
            _update_state(
                message=(
                    f"Connected to NetBox "
                    f"{status.get('netbox-version', '')}"
                ),
            )
        except NetBoxConnectionError as e:
            _update_state(
                phase="error",
                error=f"Cannot reach NetBox: {e}",
            )
            return
        except Exception as e:
            _update_state(
                phase="error",
                error=f"NetBox connection failed: {e}",
            )
            return

        if _prepare_stop.is_set():
            return

        # Phase 2: Fetch devices
        _update_state(
            phase="fetching_devices",
            progress=25,
            message=f"Fetching devices for node {node_tag}...",
        )

        from bare_metal_automation.netbox.loader import NetBoxLoader

        loader = NetBoxLoader(client)

        try:
            devices = client.get_devices_by_tag(node_tag.lower())
            _update_state(
                message=f"Found {len(devices)} devices",
                device_count=len(devices),
            )
        except Exception as e:
            _update_state(
                phase="error",
                error=f"Failed to fetch devices: {e}",
            )
            return

        if _prepare_stop.is_set():
            return

        # Phase 3: Fetch config contexts and IPs
        _update_state(
            phase="fetching_configs",
            progress=40,
            message="Fetching device configurations...",
        )

        if _prepare_stop.is_set():
            return

        # Phase 4: Fetch IPAM data
        _update_state(
            phase="fetching_ipam",
            progress=50,
            message="Fetching IP addressing and VLANs...",
        )

        if _prepare_stop.is_set():
            return

        # Phase 5: Map to BMA inventory
        _update_state(
            phase="mapping",
            progress=60,
            message="Building inventory from NetBox data...",
        )

        try:
            inventory = loader.load_node(node_tag)
            _update_state(
                message=(
                    f"Mapped {len(inventory.devices)} devices"
                ),
                device_count=len(inventory.devices),
            )
        except Exception as e:
            _update_state(
                phase="error",
                error=f"Failed to build inventory: {e}",
            )
            return

        if _prepare_stop.is_set():
            return

        # Phase 6: Sync git repo
        _update_state(
            phase="syncing_git",
            progress=70,
            message="Syncing templates and firmware repo...",
        )

        if git_repo_url:
            from bare_metal_automation.netbox.git import (
                GitRepoError,
                GitRepoManager,
            )

            try:
                git = GitRepoManager(
                    repo_url=git_repo_url,
                    local_path=git_path,
                    branch=git_branch,
                )
                result = git.sync()
                _update_state(
                    message=(
                        f"Repo {result['status']} "
                        f"(commit {result['commit']})"
                    ),
                )
            except GitRepoError as e:
                _update_state(
                    phase="error",
                    error=f"Git sync failed: {e}",
                )
                return
        else:
            _update_state(
                message="No git repo configured — using local files",
            )

        if _prepare_stop.is_set():
            return

        # Phase 7: Verify files
        _update_state(
            phase="verifying_files",
            progress=85,
            message="Verifying templates and firmware files...",
        )

        if git_repo_url:
            missing = git.verify_files(inventory)
            if missing:
                missing_list = "\n".join(
                    f"  • {m}" for m in missing[:10]
                )
                extra = ""
                if len(missing) > 10:
                    extra = f"\n  ... and {len(missing) - 10} more"
                _update_state(
                    phase="error",
                    error=(
                        f"{len(missing)} file(s) missing from repo:"
                        f"\n{missing_list}{extra}"
                    ),
                )
                return
            _update_state(
                message="All files verified",
            )
        else:
            _update_state(
                message="Skipped file verification (no git repo)",
            )

        if _prepare_stop.is_set():
            return

        # Phase 8: Generate inventory YAML
        _update_state(
            phase="generating_yaml",
            progress=95,
            message="Generating inventory file...",
        )

        try:
            loader.save_inventory_yaml(inventory, inventory_path)
            _update_state(
                message=f"Inventory saved to {inventory_path}",
            )
        except Exception as e:
            _update_state(
                phase="error",
                error=f"Failed to save inventory: {e}",
            )
            return

        # Complete!
        _update_state(
            phase="complete",
            progress=100,
            message=(
                f"Ready — {len(inventory.devices)} devices "
                f"prepared for node {node_tag}"
            ),
        )
        logger.info(
            "Prepare complete: node %s, %d devices, inventory at %s",
            node_tag, len(inventory.devices), inventory_path,
        )

    except Exception as e:
        logger.exception("Prepare build failed")
        _update_state(
            phase="error",
            error=f"Unexpected error: {e}",
        )


def start_prepare(node_tag: str) -> dict[str, str]:
    """Start build preparation in a background thread."""
    global _prepare_thread

    from django.conf import settings as django_settings

    # Check NetBox is configured
    if not getattr(django_settings, "BMA_NETBOX_URL", ""):
        return {"status": "netbox_not_configured"}

    # Mutual exclusion
    from .deployment import deployment_status
    from .rollback import rollback_status
    from .simulation import simulation_status

    if deployment_status()["running"]:
        return {"status": "deployment_running"}
    if simulation_status()["running"]:
        return {"status": "simulation_running"}
    if rollback_status()["running"]:
        return {"status": "rollback_running"}

    with _prepare_lock:
        if _prepare_thread is not None and _prepare_thread.is_alive():
            return {"status": "already_running"}

        _prepare_stop.clear()
        _update_state(
            phase="starting",
            progress=0,
            message="Starting preparation...",
            error="",
            node_tag=node_tag,
            device_count=0,
        )

        _prepare_thread = threading.Thread(
            target=_run_prepare,
            args=(node_tag,),
            name="bma-prepare",
            daemon=True,
        )
        _prepare_thread.start()
        return {"status": "started"}


def stop_prepare() -> dict[str, str]:
    """Cancel the running preparation."""
    with _prepare_lock:
        if _prepare_thread is None or not _prepare_thread.is_alive():
            return {"status": "not_running"}

        _prepare_stop.set()
        _update_state(phase="cancelled", message="Cancelled by user")
        return {"status": "stopping"}


def prepare_status() -> dict[str, Any]:
    """Return current preparation state."""
    with _prepare_lock:
        running = (
            _prepare_thread is not None
            and _prepare_thread.is_alive()
        )
    state = copy.copy(_prepare_state)
    state["running"] = running
    return state
