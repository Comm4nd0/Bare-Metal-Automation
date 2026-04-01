"""Cabling diff — compare actual CDP neighbour data against cabling intent.

Each connection is placed in one of five categories:

  correct     Port wired to the right device on the right port.
  adaptable   Port wired to a different device/port but the rule is marked
              flexible — config will be adjusted automatically.
  mismatched  Port wired to the wrong device (blocking).
  missing     Expected port has no CDP neighbour (blocking).
  unexpected  CDP neighbour on a port that has no rule (warning only).
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from bare_metal_automation.cabling.intent import CablingRule
from bare_metal_automation.models import CablingResult, CDPNeighbour

logger = logging.getLogger(__name__)


class ActualConnection(NamedTuple):
    remote_hostname: str
    remote_port: str


def diff_device(
    intended: dict[str, CablingRule],       # local_port → rule
    actual: dict[str, ActualConnection],    # local_port → (remote_hostname, remote_port)
) -> list[CablingResult]:
    """Diff one device's intended vs actual connections.

    Args:
        intended: Port-indexed rules from :func:`~.intent.CablingIntent.port_map`.
        actual:   Port-indexed CDP data from :func:`cdp_to_actual`.

    Returns:
        One ``CablingResult`` per connection checked.
    """
    results: list[CablingResult] = []

    # --- Check intended connections ---
    for port, rule in intended.items():
        if port not in actual:
            results.append(CablingResult(
                local_port=port,
                status="missing",
                intended_remote=rule.remote_device,
                intended_remote_port=rule.remote_port or "",
                message=f"No CDP neighbour — expected {rule.remote_device}",
            ))
            continue

        conn = actual[port]

        if conn.remote_hostname == rule.remote_device:
            # Right device — check port
            if rule.remote_port and conn.remote_port != rule.remote_port:
                results.append(CablingResult(
                    local_port=port,
                    status="wrong_port",
                    actual_remote=conn.remote_hostname,
                    actual_remote_port=conn.remote_port,
                    intended_remote=rule.remote_device,
                    intended_remote_port=rule.remote_port,
                    message=(
                        f"Right device, wrong port — "
                        f"expected {rule.remote_port}, got {conn.remote_port}"
                    ),
                ))
            else:
                results.append(CablingResult(
                    local_port=port,
                    status="correct",
                    actual_remote=conn.remote_hostname,
                    actual_remote_port=conn.remote_port,
                    intended_remote=rule.remote_device,
                    intended_remote_port=rule.remote_port or "",
                ))

        elif rule.flexible:
            results.append(CablingResult(
                local_port=port,
                status="adaptable",
                actual_remote=conn.remote_hostname,
                actual_remote_port=conn.remote_port,
                intended_remote=rule.remote_device,
                message=(
                    f"Flexible port — found {conn.remote_hostname} "
                    f"instead of {rule.remote_device}; config will adapt"
                ),
            ))

        else:
            results.append(CablingResult(
                local_port=port,
                status="wrong_device",
                actual_remote=conn.remote_hostname,
                actual_remote_port=conn.remote_port,
                intended_remote=rule.remote_device,
                intended_remote_port=rule.remote_port or "",
                message=(
                    f"Wrong device — expected {rule.remote_device}, "
                    f"found {conn.remote_hostname}"
                ),
            ))

    # --- Unexpected connections ---
    for port, conn in actual.items():
        if port not in intended:
            results.append(CablingResult(
                local_port=port,
                status="unexpected",
                actual_remote=conn.remote_hostname,
                actual_remote_port=conn.remote_port,
                message=f"Unexpected cable to {conn.remote_hostname} — not in design",
            ))

    return results


def cdp_to_actual(
    neighbours: list[CDPNeighbour],
    cdp_id_to_hostname: dict[str, str],
) -> dict[str, ActualConnection]:
    """Convert CDP neighbour list to a port-indexed actual-connection map.

    Args:
        neighbours:          Raw CDP neighbour entries.
        cdp_id_to_hostname:  Maps CDP device-ID → intended hostname so the
                             diff can compare against human-readable names.

    Returns:
        ``{local_port: ActualConnection}``
    """
    actual: dict[str, ActualConnection] = {}

    for nbr in neighbours:
        remote_id = nbr.remote_device_id.split(".")[0]
        resolved = (
            cdp_id_to_hostname.get(remote_id)
            or cdp_id_to_hostname.get(nbr.remote_device_id)
            or remote_id
        )
        actual[nbr.local_port] = ActualConnection(
            remote_hostname=resolved,
            remote_port=nbr.remote_port,
        )

    return actual
