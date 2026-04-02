"""Rollback runner — runs factory reset in a background thread.

Mirrors the deployment.py threading pattern exactly:
    POST /api/rollback/start/   → start background thread
    POST /api/rollback/stop/    → signal stop after current phase
    POST /api/rollback/resume/  → resume from rollback checkpoint
    GET  /api/rollback/status/  → return runner state
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from django.db import close_old_connections

logger = logging.getLogger(__name__)

# Module-level rollback state (mirrors deployment.py pattern)
_rollback_thread: threading.Thread | None = None
_rollback_lock = threading.Lock()
_rollback_stop = threading.Event()
_rollback_id: int | None = None

DEFAULT_ROLLBACK_CHECKPOINT = ".bma-rollback-checkpoint.json"
DEFAULT_DEPLOY_CHECKPOINT = ".bma-checkpoint.json"


def _run_rollback(resume: bool = False) -> None:
    """Thread target: run (or resume) the rollback orchestrator."""
    global _rollback_id

    try:
        close_old_connections()

        from bare_metal_automation.dashboard.models import (
            Deployment,
            DeploymentLog,
        )
        from bare_metal_automation.rollback.orchestrator import (
            RollbackOrchestrator,
        )

        # Find the most recent deployment to associate logs with
        dep = Deployment.objects.order_by("-started_at").first()
        if dep is None:
            logger.error("No deployment found for rollback")
            return

        _rollback_id = dep.pk

        # Phase change callback — keeps the dashboard ORM in sync
        def _on_phase_change(phase_value: str) -> None:
            close_old_connections()
            Deployment.objects.filter(pk=dep.pk).update(
                phase=phase_value,
            )
            DeploymentLog.objects.create(
                deployment=dep,
                level="INFO",
                phase=phase_value,
                message=f"Rollback phase: {phase_value}",
            )

        if resume:
            orch = RollbackOrchestrator.from_checkpoint(
                rollback_checkpoint=DEFAULT_ROLLBACK_CHECKPOINT,
                stop_event=_rollback_stop,
                on_phase_change=_on_phase_change,
            )
        else:
            orch = RollbackOrchestrator(
                deployment_checkpoint=DEFAULT_DEPLOY_CHECKPOINT,
                rollback_checkpoint=DEFAULT_ROLLBACK_CHECKPOINT,
                stop_event=_rollback_stop,
                on_phase_change=_on_phase_change,
            )

        # Update ORM to show rollback started
        close_old_connections()
        Deployment.objects.filter(pk=dep.pk).update(
            phase="rollback_pre_flight",
        )
        DeploymentLog.objects.create(
            deployment=dep,
            level="WARNING",
            phase="rollback_pre_flight",
            message=(
                "Rollback resumed from checkpoint"
                if resume
                else "ROLLBACK TO FACTORY initiated"
            ),
        )

        # Run the rollback
        final_phase = orch.run_full_rollback(resume=resume)

        # Update final ORM state
        close_old_connections()
        phase_value = final_phase.value
        if _rollback_stop.is_set() and phase_value not in (
            "rollback_complete", "rollback_failed",
        ):
            Deployment.objects.filter(pk=dep.pk).update(
                phase="stopped",
            )
            DeploymentLog.objects.create(
                deployment=dep,
                level="WARNING",
                phase="stopped",
                message="Rollback stopped by user — checkpoint saved",
            )
        elif phase_value == "rollback_complete":
            Deployment.objects.filter(pk=dep.pk).update(
                phase="rollback_complete",
            )
            DeploymentLog.objects.create(
                deployment=dep,
                level="INFO",
                phase="rollback_complete",
                message="All devices reset to factory state",
            )
        else:
            Deployment.objects.filter(pk=dep.pk).update(
                phase=phase_value,
            )

    except Exception:
        logger.exception("Rollback failed")
        close_old_connections()
        if _rollback_id is not None:
            from bare_metal_automation.dashboard.models import (
                Deployment,
                DeploymentLog,
            )

            Deployment.objects.filter(pk=_rollback_id).update(
                phase="rollback_failed",
            )
            try:
                dep = Deployment.objects.get(pk=_rollback_id)
                DeploymentLog.objects.create(
                    deployment=dep,
                    level="ERROR",
                    phase="rollback_failed",
                    message="Rollback crashed — see server logs",
                )
            except Deployment.DoesNotExist:
                logger.debug(
                    "Deployment %s gone, skipping crash log", _rollback_id,
                )
    finally:
        _rollback_id = None
        close_old_connections()


def start_rollback() -> dict[str, str]:
    """Start a rollback in a background thread."""
    global _rollback_thread

    # Mutual exclusion with deployment and simulation
    from .deployment import deployment_status
    from .simulation import simulation_status

    if deployment_status()["running"]:
        return {"status": "deployment_running"}
    if simulation_status()["running"]:
        return {"status": "simulation_running"}

    with _rollback_lock:
        if _rollback_thread is not None and _rollback_thread.is_alive():
            return {"status": "already_running"}

        # Check that a deployment checkpoint exists
        if not Path(DEFAULT_DEPLOY_CHECKPOINT).exists():
            return {"status": "no_deployment_checkpoint"}

        _rollback_stop.clear()
        _rollback_thread = threading.Thread(
            target=_run_rollback,
            kwargs={"resume": False},
            name="bma-rollback",
            daemon=True,
        )
        _rollback_thread.start()
        return {"status": "started"}


def stop_rollback() -> dict[str, str]:
    """Signal the running rollback to stop after current phase."""
    with _rollback_lock:
        if _rollback_thread is None or not _rollback_thread.is_alive():
            return {"status": "not_running"}

        _rollback_stop.set()
        return {"status": "stopping"}


def resume_rollback() -> dict[str, str]:
    """Resume a rollback from the last checkpoint."""
    global _rollback_thread

    # Mutual exclusion
    from .deployment import deployment_status
    from .simulation import simulation_status

    if deployment_status()["running"]:
        return {"status": "deployment_running"}
    if simulation_status()["running"]:
        return {"status": "simulation_running"}

    with _rollback_lock:
        if _rollback_thread is not None and _rollback_thread.is_alive():
            return {"status": "already_running"}

        checkpoint = Path(DEFAULT_ROLLBACK_CHECKPOINT)
        if not checkpoint.exists():
            return {"status": "no_checkpoint"}

        _rollback_stop.clear()
        _rollback_thread = threading.Thread(
            target=_run_rollback,
            kwargs={"resume": True},
            name="bma-rollback",
            daemon=True,
        )
        _rollback_thread.start()
        return {"status": "resumed"}


def rollback_status() -> dict[str, object]:
    """Return current rollback runner state."""
    with _rollback_lock:
        running = (
            _rollback_thread is not None
            and _rollback_thread.is_alive()
        )
    checkpoint_exists = Path(DEFAULT_ROLLBACK_CHECKPOINT).exists()
    return {
        "running": running,
        "rollback_id": _rollback_id,
        "checkpoint_exists": checkpoint_exists,
    }
