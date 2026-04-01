"""Config adapter — rewrite rendered config lines to match actual port wiring.

When the cabling validator finds an *adaptable* connection (a flexible port
wired to a different device than intended) this module rewrites the affected
interface block so the final config matches reality rather than the template.

Example
-------
The template says::

    interface GigabitEthernet1/0/10
     description Server svr-compute-01 iLO
     switchport access vlan 100

But CDP shows svr-compute-02 connected instead.  The adapter patches the
description to reflect the actual device::

    interface GigabitEthernet1/0/10
     description Server svr-compute-02 iLO  ← patched
     switchport access vlan 100
"""

from __future__ import annotations

import logging
import re

from bare_metal_automation.models import CablingResult

logger = logging.getLogger(__name__)


class ConfigAdapter:
    """Rewrite config lines to match actual flexible-port assignments."""

    def adapt(
        self,
        config_lines: list[str],
        cabling_results: list[CablingResult],
        device_hostname: str,
    ) -> list[str]:
        """Apply adaptations to *config_lines* based on *cabling_results*.

        Only ``adaptable`` results are processed — other statuses are left
        untouched (blocking issues are caught upstream before this runs).

        Returns a new list with patched lines.
        """
        # Build patch map: local_port → actual_remote hostname
        patches: dict[str, str] = {
            r.local_port: (r.actual_remote or "")
            for r in cabling_results
            if r.status == "adaptable" and r.actual_remote
        }

        if not patches:
            return config_lines

        logger.info(
            f"{device_hostname}: Adapting {len(patches)} flexible "
            f"port(s): {list(patches.keys())}"
        )

        adapted: list[str] = []
        current_interface: str | None = None

        for line in config_lines:
            # Track current interface context
            iface_match = re.match(r"^interface\s+(\S+)", line.strip())
            if iface_match:
                current_interface = iface_match.group(1)
                adapted.append(line)
                continue

            # Patch description lines inside adaptable interfaces
            if current_interface and current_interface in patches:
                desc_match = re.match(r"^(\s*)description\s+(.+)$", line)
                if desc_match:
                    indent = desc_match.group(1)
                    old_desc = desc_match.group(2)
                    new_device = patches[current_interface]
                    new_desc = self._rewrite_description(old_desc, new_device)
                    if new_desc != old_desc:
                        logger.debug(
                            f"  {current_interface}: description "
                            f"'{old_desc}' → '{new_desc}'"
                        )
                    adapted.append(f"{indent}description {new_desc}")
                    continue

            adapted.append(line)

        return adapted

    # ── Internal ───────────────────────────────────────────────────────────

    def _rewrite_description(self, description: str, new_device: str) -> str:
        """Replace a device reference in *description* with *new_device*.

        Handles common description patterns:
          "Server svr-compute-01 iLO"   → "Server svr-compute-02 iLO"
          "Uplink to svr-compute-01"    → "Uplink to svr-compute-02"
          "svr-compute-01"              → "svr-compute-02"
        """
        # Try to find and replace a hostname-like token (alphanumeric + hyphens)
        updated = re.sub(
            r"\b[a-z][a-z0-9\-]+\b",
            lambda m: new_device if "-" in m.group(0) else m.group(0),
            description,
            count=1,
        )
        return updated if updated != description else f"{description} (actual: {new_device})"
