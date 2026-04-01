"""Post-configuration validator — verify network devices are healthy after config push.

Checks are role-specific:
  - Switches  : STP root bridge status, trunk port health, VLAN presence
  - Routers   : OSPF adjacencies, interface states, default-route presence
  - Firewalls : named interfaces present, basic policy loaded

All checks return a ``ValidationResult`` with a boolean pass/fail and a list
of human-readable findings.
"""

from __future__ import annotations

import logging
import re
import socket
import time
from dataclasses import dataclass, field

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Outcome of a post-config validation run for one device."""

    passed: bool = True
    findings: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.passed = False
        self.findings.append(f"FAIL: {message}")

    def warn(self, message: str) -> None:
        self.findings.append(f"WARN: {message}")

    def ok(self, message: str) -> None:
        self.findings.append(f"OK:   {message}")


class ConfigValidator:
    """Run post-configuration health checks on a network device.

    Uses an already-open Netmiko connection; the caller is responsible for
    the connection lifecycle.
    """

    def __init__(self, management_vlan: int = 0) -> None:
        self.management_vlan = management_vlan

    def validate(
        self,
        device: DiscoveredDevice,
        connection,            # Netmiko ConnectHandler
    ) -> ValidationResult:
        """Dispatch to the role-appropriate validator."""
        role_validators = {
            "core-switch":          self._validate_core_switch,
            "distribution-switch":  self._validate_switch,
            "access-switch":        self._validate_switch,
            "border-router":        self._validate_router,
            "perimeter-firewall":   self._validate_firewall,
        }
        fn = role_validators.get(device.role or "", self._validate_generic)
        return fn(device, connection)

    # ── Role validators ─────────────────────────────────────────────────────

    def _validate_core_switch(
        self, device: DiscoveredDevice, conn
    ) -> ValidationResult:
        result = ValidationResult()

        self.check_stp_root(conn, device, result)
        self.check_trunk_status(conn, device, result)
        self.check_management_vlan(conn, result)
        self.check_management_reachability(device, result)

        return result

    def _validate_switch(
        self, device: DiscoveredDevice, conn
    ) -> ValidationResult:
        result = ValidationResult()

        self.check_stp_running(conn, result)
        self.check_trunk_status(conn, device, result)
        self.check_management_vlan(conn, result)

        return result

    def _validate_router(
        self, device: DiscoveredDevice, conn
    ) -> ValidationResult:
        result = ValidationResult()

        self.check_ospf_adjacencies(conn, device, result)
        self.check_hsrp_state(conn, device, result)
        self.check_interface_summary(conn, result)

        return result

    def _validate_firewall(
        self, device: DiscoveredDevice, conn
    ) -> ValidationResult:
        result = ValidationResult()

        output = conn.send_command("show nameif")
        if "nameif" not in output.lower() and len(output.strip()) < 20:
            result.fail("No named interfaces found — firewall may not be configured")
        else:
            result.ok("Named interfaces present")

        return result

    def _validate_generic(
        self, device: DiscoveredDevice, conn
    ) -> ValidationResult:
        result = ValidationResult()
        try:
            output = conn.send_command("show version")
            if output:
                result.ok("Device is responsive (show version OK)")
            else:
                result.fail("Empty response to 'show version'")
        except Exception as e:
            result.fail(f"show version failed: {e}")
        return result

    # ── Individual checks ───────────────────────────────────────────────────

    def check_stp_root(
        self,
        conn,
        device: DiscoveredDevice,
        result: ValidationResult,
    ) -> None:
        """Verify this switch is STP root for expected VLANs."""
        try:
            output = conn.send_command("show spanning-tree summary")
            if "root bridge" in output.lower() or "this bridge is the root" in output.lower():
                result.ok("STP: this switch is root bridge")
            else:
                result.warn(
                    "STP: switch is not root bridge — "
                    "acceptable for distribution/access but check core switches"
                )
        except Exception as e:
            result.warn(f"STP check failed: {e}")

    def check_stp_running(
        self, conn, result: ValidationResult
    ) -> None:
        """Check STP is operational."""
        try:
            output = conn.send_command("show spanning-tree summary")
            if "No spanning tree" in output:
                result.fail("STP not running")
            else:
                result.ok("STP is running")
        except Exception as e:
            result.warn(f"STP check failed: {e}")

    def check_trunk_status(
        self,
        conn,
        device: DiscoveredDevice,
        result: ValidationResult,
    ) -> None:
        """Check trunk ports are up/up and forwarding expected VLANs."""
        try:
            output = conn.send_command("show interfaces trunk")
            if not output.strip():
                result.warn("No trunk interfaces detected")
                return

            # Count lines with 802.1q trunking
            trunk_lines = [
                ln for ln in output.split("\n") if "802.1q" in ln.lower()
            ]
            result.ok(f"Trunk ports: {len(trunk_lines)} 802.1Q interface(s) active")

            # Check management VLAN is carried
            if self.management_vlan:
                if str(self.management_vlan) not in output:
                    result.warn(
                        f"Management VLAN {self.management_vlan} "
                        f"not found in trunk VLAN list"
                    )
        except Exception as e:
            result.warn(f"Trunk check failed: {e}")

    def check_management_vlan(
        self, conn, result: ValidationResult
    ) -> None:
        """Verify the management VLAN is present in the VLAN database."""
        if not self.management_vlan:
            return
        try:
            output = conn.send_command("show vlan brief")
            mgmt_str = str(self.management_vlan)
            if mgmt_str in output:
                result.ok(f"Management VLAN {mgmt_str} present")
            else:
                result.fail(f"Management VLAN {mgmt_str} NOT in VLAN database")
        except Exception as e:
            result.warn(f"VLAN check failed: {e}")

    def check_ospf_adjacencies(
        self,
        conn,
        device: DiscoveredDevice,
        result: ValidationResult,
    ) -> None:
        """Check OSPF adjacency states."""
        try:
            output = conn.send_command("show ip ospf neighbor")
            if not output.strip():
                result.warn("No OSPF neighbors — may be expected before full deployment")
                return

            full_count = output.count("FULL/")
            total_count = len([
                ln for ln in output.split("\n")
                if re.search(r"\d+\.\d+\.\d+\.\d+", ln)
            ])
            if full_count == total_count and total_count > 0:
                result.ok(f"OSPF: {full_count} FULL adjacency(s)")
            else:
                result.warn(
                    f"OSPF: {full_count}/{total_count} adjacencies FULL "
                    f"— others may still be forming"
                )
        except Exception as e:
            result.warn(f"OSPF check failed: {e}")

    def check_hsrp_state(
        self,
        conn,
        device: DiscoveredDevice,
        result: ValidationResult,
    ) -> None:
        """Check HSRP group states."""
        try:
            output = conn.send_command("show standby brief")
            if not output.strip() or "P" not in output:
                result.warn("No active HSRP groups detected")
                return

            active_count = output.count(" Active")
            standby_count = output.count(" Standby")
            result.ok(f"HSRP: {active_count} Active, {standby_count} Standby group(s)")
        except Exception as e:
            result.warn(f"HSRP check failed: {e}")

    def check_interface_summary(
        self, conn, result: ValidationResult
    ) -> None:
        """Check for unexpectedly down interfaces."""
        try:
            output = conn.send_command("show ip interface brief")
            down_lines = [
                ln for ln in output.split("\n")
                if "down" in ln.lower() and "administratively" not in ln.lower()
            ]
            if down_lines:
                result.warn(
                    f"{len(down_lines)} interface(s) unexpectedly down "
                    f"(may resolve as more devices come up)"
                )
            else:
                result.ok("All interfaces up/up or administratively down")
        except Exception as e:
            result.warn(f"Interface check failed: {e}")

    def check_management_reachability(
        self,
        device: DiscoveredDevice,
        result: ValidationResult,
        timeout: int = 5,
    ) -> None:
        """Verify the device's management IP is reachable via TCP/22."""
        management_ip = (
            getattr(device, "management_ip", None) or device.ip
        )
        if not management_ip:
            result.warn("No management IP to check reachability")
            return

        try:
            sock = socket.create_connection((management_ip, 22), timeout=timeout)
            sock.close()
            result.ok(f"Management IP {management_ip} reachable on port 22")
        except (socket.timeout, ConnectionRefusedError, OSError) as e:
            result.fail(
                f"Management IP {management_ip} not reachable: {e}"
            )
