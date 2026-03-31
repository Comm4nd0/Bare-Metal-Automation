"""Parallel execution engine — run device operations concurrently by BFS depth."""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)


def group_devices_by_depth(
    devices: list[DiscoveredDevice],
    ascending: bool = False,
) -> list[list[DiscoveredDevice]]:
    """Group devices by BFS depth for parallel execution.

    Returns a list of device groups, ordered from highest depth (furthest
    from laptop) to lowest. Devices within the same group can be
    processed in parallel since they don't sit on each other's
    management paths.
    """
    by_depth: dict[int, list[DiscoveredDevice]] = defaultdict(list)
    no_depth: list[DiscoveredDevice] = []

    for device in devices:
        if device.bfs_depth is not None:
            by_depth[device.bfs_depth].append(device)
        else:
            no_depth.append(device)

    # Sort depths: descending for provisioning (outside-in), ascending for reset (inside-out)
    sorted_depths = sorted(by_depth.keys(), reverse=not ascending)
    groups = [by_depth[d] for d in sorted_depths]

    # Devices with no depth go last (likely servers/NTP not in the graph)
    if no_depth:
        groups.append(no_depth)

    return groups


def run_parallel_by_depth(
    devices: list[DiscoveredDevice],
    operation: Callable[[DiscoveredDevice], bool],
    max_workers: int = 4,
    stop_on_failure: bool = False,
) -> dict[str, bool]:
    """Execute an operation on devices grouped by BFS depth.

    Devices at the same depth run in parallel. Each depth group must
    complete before the next (closer) group starts. This preserves the
    outside-in ordering constraint — we never reconfigure a device that
    sits on the management path to a device we're still working on.

    Args:
        devices: List of devices to process.
        operation: Callable that takes a device and returns success bool.
        max_workers: Maximum concurrent threads per depth group.
        stop_on_failure: If True, stop processing when any device fails
            within a depth group (prevents configuring closer devices
            when a further one failed).

    Returns:
        Dict mapping device serial to success/failure.
    """
    groups = group_devices_by_depth(devices)
    results: dict[str, bool] = {}

    for group in groups:
        if not group:
            continue

        depth = group[0].bfs_depth
        depth_label = f"depth {depth}" if depth is not None else "unordered"
        serials = [d.serial or d.ip for d in group]

        if len(group) == 1:
            logger.info(f"Processing {serials[0]} ({depth_label})")
            results[group[0].serial or group[0].ip] = operation(group[0])
        else:
            logger.info(
                f"Processing {len(group)} devices in parallel "
                f"({depth_label}): {serials}"
            )
            group_results = _run_group_parallel(
                group, operation, max_workers
            )
            results.update(group_results)

        # Check for failures in this depth group
        group_failures = [
            d.serial or d.ip
            for d in group
            if not results.get(d.serial or d.ip, False)
        ]
        if group_failures and stop_on_failure:
            logger.error(
                f"Stopping: {len(group_failures)} device(s) failed at "
                f"{depth_label}: {group_failures}"
            )
            break

    return results


def _run_group_parallel(
    devices: list[DiscoveredDevice],
    operation: Callable[[DiscoveredDevice], bool],
    max_workers: int,
) -> dict[str, bool]:
    """Run an operation on a group of devices concurrently."""
    results: dict[str, bool] = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_device = {
            executor.submit(operation, device): device
            for device in devices
        }

        for future in as_completed(future_to_device):
            device = future_to_device[future]
            key = device.serial or device.ip
            try:
                results[key] = future.result()
            except Exception as e:
                logger.error(
                    f"{device.intended_hostname or key}: "
                    f"Unhandled exception: {e}"
                )
                results[key] = False

    return results


def run_parallel_by_depth_ascending(
    devices: list[DiscoveredDevice],
    operation: Callable[[DiscoveredDevice], bool],
    max_workers: int = 4,
    stop_on_failure: bool = False,
) -> dict[str, bool]:
    """Execute an operation on devices grouped by BFS depth, shallowest first.

    This is the reverse of ``run_parallel_by_depth`` — devices closest to
    the laptop (lowest depth) are processed first.  Use this for factory
    reset operations where inside-out ordering is required: reset leaf
    devices first so the management path stays intact until the last device.
    """
    groups = group_devices_by_depth(devices, ascending=True)
    results: dict[str, bool] = {}

    for group in groups:
        if not group:
            continue

        depth = group[0].bfs_depth
        depth_label = f"depth {depth}" if depth is not None else "unordered"
        serials = [d.serial or d.ip for d in group]

        if len(group) == 1:
            logger.info(f"Processing {serials[0]} ({depth_label})")
            results[group[0].serial or group[0].ip] = operation(group[0])
        else:
            logger.info(
                f"Processing {len(group)} devices in parallel "
                f"({depth_label}): {serials}"
            )
            group_results = _run_group_parallel(
                group, operation, max_workers
            )
            results.update(group_results)

        # Check for failures in this depth group
        group_failures = [
            d.serial or d.ip
            for d in group
            if not results.get(d.serial or d.ip, False)
        ]
        if group_failures and stop_on_failure:
            logger.error(
                f"Stopping: {len(group_failures)} device(s) failed at "
                f"{depth_label}: {group_failures}"
            )
            break

    return results


def run_independent_parallel(
    devices: list[DiscoveredDevice],
    operation: Callable[[DiscoveredDevice], bool],
    max_workers: int = 4,
) -> dict[str, bool]:
    """Run an operation on fully independent devices (e.g. servers, NTP).

    Unlike run_parallel_by_depth, this doesn't respect depth ordering —
    all devices run concurrently. Use this for devices that don't sit on
    each other's management paths (servers via iLO, NTP appliances).
    """
    if not devices:
        return {}

    serials = [d.serial or d.ip for d in devices]
    logger.info(
        f"Processing {len(devices)} independent devices in parallel: "
        f"{serials}"
    )

    return _run_group_parallel(devices, operation, max_workers)
