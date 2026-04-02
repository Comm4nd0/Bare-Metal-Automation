"""Deployment runner — runs a real deployment in a background thread.

Mirrors the simulation.py threading pattern exactly:
    POST /api/deployment/start/   → start background thread
    POST /api/deployment/stop/    → signal stop after current phase
    POST /api/deployment/resume/  → resume from checkpoint
    GET  /api/deployment/status/  → return runner state
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Module-level deployment state (mirrors simulation.py pattern)
_deployment_thread: threading.Thread | None = None
_deployment_lock = threading.Lock()
_deployment_stop = threading.Event()
_deployment_id: int | None = None

DEFAULT_CHECKPOINT = ".bma-checkpoint.json"


def _run_deployment(
    inventory_path: str,
    resume: bool = False,
    checkpoint_path: str = DEFAULT_CHECKPOINT,
) -> None:
    """Thread target: run (or resume) the orchestrator."""
    global _deployment_id

    try:
        close_old_connections()

        from bare_metal_automation.dashboard.events import (
            deployment_log,
            device_status_changed,
            phase_started,
        )
        from bare_metal_automation.dashboard.models import Deployment, DeploymentLog, Device
        from bare_metal_automation.inventory import load_inventory
        from bare_metal_automation.models import DiscoveredDevice
        from bare_metal_automation.orchestrator import Orchestrator

        # Create or find the dashboard ORM record
        if resume:
            orch = Orchestrator.from_checkpoint(
                checkpoint_path=checkpoint_path,
                stop_event=_deployment_stop,
            )
            inv = load_inventory(orch.inventory_path)
            dep = Deployment.objects.order_by("-started_at").first()
            if dep is None:
                dep = Deployment.objects.create(
                    name=inv.name,
                    bootstrap_subnet=inv.bootstrap_subnet,
                    laptop_ip=inv.laptop_ip,
                    management_vlan=inv.management_vlan,
                )
        else:
            inv = load_inventory(Path(inventory_path))
            dep = Deployment.objects.create(
                name=inv.name,
                bootstrap_subnet=inv.bootstrap_subnet,
                laptop_ip=inv.laptop_ip,
                management_vlan=inv.management_vlan,
            )
            orch = Orchestrator(
                inventory_path=inventory_path,
                checkpoint_path=checkpoint_path,
                stop_event=_deployment_stop,
            )

        _deployment_id = dep.pk

        # ── Phase change callback ─────────────────────────────────────────
        def _on_phase_change(phase_value: str) -> None:
            close_old_connections()
            Deployment.objects.filter(pk=dep.pk).update(phase=phase_value)
            DeploymentLog.objects.create(
                deployment=dep,
                level="INFO",
                phase=phase_value,
                message=f"Phase: {phase_value}",
            )
            phase_started(dep.pk, phase_value)
            deployment_log(dep.pk, "INFO", f"Phase started: {phase_value}", phase_value)

        # ── Device discovered callback ────────────────────────────────────
        def _on_device_discovered(device: DiscoveredDevice) -> None:
            if device.serial is None:
                return
            close_old_connections()
            try:
                Device.objects.update_or_create(
                    deployment=dep,
                    serial=device.serial,
                    defaults={
                        "ip": device.ip,
                        "mac": device.mac or "",
                        "platform": device.device_platform or "",
                        "hostname": device.hostname or "",
                        "intended_hostname": device.intended_hostname or "",
                        "role": device.role or "",
                        "state": device.state.value,
                        "bfs_depth": device.bfs_depth,
                        "config_order": device.config_order,
                    },
                )
                device_status_changed(
                    dep.pk,
                    device.serial,
                    device.intended_hostname or device.hostname or device.ip,
                    device.state.value,
                    "Device discovered",
                )
            except Exception as e:
                logger.warning(f"Failed to create Device record for {device.serial}: {e}")

        # ── Device state change callback ──────────────────────────────────
        def _on_device_change(device: DiscoveredDevice, message: str = "") -> None:
            if device.serial is None:
                return
            close_old_connections()
            hostname = device.intended_hostname or device.hostname or device.ip
            try:
                Device.objects.filter(
                    deployment=dep, serial=device.serial
                ).update(
                    state=device.state.value,
                    bfs_depth=device.bfs_depth,
                    config_order=device.config_order,
                )
                DeploymentLog.objects.create(
                    deployment=dep,
                    level="INFO",
                    phase=dep.phase,
                    message=f"{hostname}: {message or device.state.value}",
                )
                device_status_changed(
                    dep.pk,
                    device.serial,
                    hostname,
                    device.state.value,
                    message,
                )
            except Exception as e:
                logger.warning(
                    f"Failed to update Device record for {device.serial}: {e}"
                )

        orch.on_phase_change = _on_phase_change
        orch.on_device_discovered = _on_device_discovered
        orch.on_device_change = _on_device_change

        # Log start/resume
        DeploymentLog.objects.create(
            deployment=dep,
            level="INFO",
            phase=dep.phase,
            message="Deployment resumed" if resume else "Deployment started",
        )

        # Run the orchestrator
        state = orch.run_full_deployment(resume=resume)

        # Determine final ORM phase
        close_old_connections()
        if _deployment_stop.is_set() and state.phase.value not in ("complete", "failed"):
            Deployment.objects.filter(pk=dep.pk).update(phase="stopped")
            DeploymentLog.objects.create(
                deployment=dep,
                level="WARNING",
                phase="stopped",
                message="Deployment stopped by user — checkpoint saved",
            )
        elif state.phase.value == "complete":
            Deployment.objects.filter(pk=dep.pk).update(phase="complete")
            DeploymentLog.objects.create(
                deployment=dep,
                level="INFO",
                phase="complete",
                message="Deployment completed successfully",
            )
        else:
            Deployment.objects.filter(pk=dep.pk).update(phase=state.phase.value)

    except Exception:
        logger.exception("Deployment failed")
        close_old_connections()
        if _deployment_id is not None:
            from bare_metal_automation.dashboard.models import Deployment, DeploymentLog

            Deployment.objects.filter(pk=_deployment_id).update(phase="failed")
            try:
                dep = Deployment.objects.get(pk=_deployment_id)
                DeploymentLog.objects.create(
                    deployment=dep,
                    level="ERROR",
                    phase="failed",
                    message="Deployment crashed — see server logs",
                )
            except Deployment.DoesNotExist:
                logger.debug("Deployment %s no longer exists, skipping crash log", _deployment_id)
    finally:
        _deployment_id = None
        close_old_connections()


def start_deployment() -> dict[str, str]:
    """Start a new deployment in a background thread."""
    global _deployment_thread

    # Mutual exclusion with simulation and rollback
    from .rollback import rollback_status
    from .simulation import simulation_status

    if simulation_status()["running"]:
        return {"status": "simulation_running"}
    if rollback_status()["running"]:
        return {"status": "rollback_running"}

    with _deployment_lock:
        if _deployment_thread is not None and _deployment_thread.is_alive():
            return {"status": "already_running"}

        _deployment_stop.clear()
        inventory_path = getattr(
            settings,
            "BMA_INVENTORY_PATH",
            "configs/inventory/inventory.yaml",
        )
        _deployment_thread = threading.Thread(
            target=_run_deployment,
            args=(inventory_path,),
            kwargs={"resume": False},
            name="bma-deployment",
            daemon=True,
        )
        _deployment_thread.start()
        return {"status": "started"}


def stop_deployment() -> dict[str, str]:
    """Signal the running deployment to stop after current phase."""
    with _deployment_lock:
        if _deployment_thread is None or not _deployment_thread.is_alive():
            return {"status": "not_running"}

        _deployment_stop.set()
        return {"status": "stopping"}


def resume_deployment() -> dict[str, str]:
    """Resume a deployment from the last checkpoint."""
    global _deployment_thread

    # Mutual exclusion with simulation and rollback
    from .rollback import rollback_status
    from .simulation import simulation_status

    if simulation_status()["running"]:
        return {"status": "simulation_running"}
    if rollback_status()["running"]:
        return {"status": "rollback_running"}

    with _deployment_lock:
        if _deployment_thread is not None and _deployment_thread.is_alive():
            return {"status": "already_running"}

        checkpoint = Path(DEFAULT_CHECKPOINT)
        if not checkpoint.exists():
            return {"status": "no_checkpoint"}

        _deployment_stop.clear()
        inventory_path = getattr(
            settings,
            "BMA_INVENTORY_PATH",
            "configs/inventory/inventory.yaml",
        )
        _deployment_thread = threading.Thread(
            target=_run_deployment,
            args=(inventory_path,),
            kwargs={"resume": True},
            name="bma-deployment",
            daemon=True,
        )
        _deployment_thread.start()
        return {"status": "resumed"}


def deployment_status() -> dict[str, object]:
    """Return current deployment runner state."""
    with _deployment_lock:
        running = _deployment_thread is not None and _deployment_thread.is_alive()
    checkpoint_exists = Path(DEFAULT_CHECKPOINT).exists()
    return {
        "running": running,
        "deployment_id": _deployment_id,
        "checkpoint_exists": checkpoint_exists,
    }
