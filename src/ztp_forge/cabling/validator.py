"""Cabling validator — compare physical CDP data against intended configuration."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from rich.console import Console
from rich.table import Table

from ztp_forge.models import (
    CablingResult,
    DeploymentInventory,
    DiscoveredDevice,
    IntendedConnection,
)

logger = logging.getLogger(__name__)
console = Console()


class CablingValidator:
    """Compares actual cabling (from CDP) against intended design (from config templates)."""

    def __init__(self, inventory: DeploymentInventory) -> None:
        self.inventory = inventory
        self._hostname_to_serial: dict[str, str] = {}
        for serial, spec in inventory.devices.items():
            self._hostname_to_serial[spec["hostname"]] = serial

    def validate_all(
        self,
        devices: dict[str, DiscoveredDevice],
    ) -> dict[str, list[CablingResult]]:
        """Validate cabling for all identified devices.

        Returns {serial: [CablingResult, ...]} for each device.
        """
        results: dict[str, list[CablingResult]] = {}

        for ip, device in devices.items():
            if device.serial is None or device.template_path is None:
                continue

            intended = self._parse_intended_connections(device)
            actual = self._build_actual_connections(device, devices)
            results[device.serial] = self._diff_connections(intended, actual, device)

        return results

    def _parse_intended_connections(
        self, device: DiscoveredDevice
    ) -> dict[str, IntendedConnection]:
        """Parse a Jinja2 config template to extract intended connections.

        Looks for interface descriptions that reference other hostnames,
        and port-channel member interfaces.
        """
        intended: dict[str, IntendedConnection] = {}

        if not device.template_path:
            return intended

        template_path = Path("configs/templates") / device.template_path
        if not template_path.exists():
            logger.warning(f"Template not found: {template_path}")
            return intended

        with open(template_path) as f:
            content = f.read()

        # Parse interface blocks
        # Matches patterns like:
        #   interface GigabitEthernet1/0/48
        #     description Uplink to sw-core-01
        current_interface = None
        current_description = ""
        is_flexible = False

        for line in content.split("\n"):
            line = line.strip()

            # Match interface declaration
            iface_match = re.match(r"interface\s+(\S+)", line)
            if iface_match:
                # Save previous interface if it had a meaningful description
                if current_interface and current_description:
                    conn = self._description_to_connection(
                        current_interface, current_description, is_flexible
                    )
                    if conn:
                        intended[current_interface] = conn

                current_interface = iface_match.group(1)
                current_description = ""
                is_flexible = False
                continue

            # Match description
            desc_match = re.match(r"description\s+(.+)", line)
            if desc_match and current_interface:
                current_description = desc_match.group(1)

            # Server-facing ports are flexible (can adapt to actual cabling)
            if re.match(r"switchport mode access", line):
                is_flexible = True

        # Don't forget the last interface
        if current_interface and current_description:
            conn = self._description_to_connection(
                current_interface, current_description, is_flexible
            )
            if conn:
                intended[current_interface] = conn

        return intended

    def _description_to_connection(
        self,
        interface: str,
        description: str,
        is_flexible: bool,
    ) -> IntendedConnection | None:
        """Convert an interface description to an IntendedConnection.

        Expects descriptions like:
          "Uplink to sw-core-01"
          "Downlink to sw-access-01 Gi1/0/48"
          "Server svr-compute-01 iLO"
          "To fw-perim-01 Gi0/0"
        """
        # Try to extract a hostname reference
        for hostname in self._hostname_to_serial:
            if hostname in description:
                # Try to extract a port reference too
                port_match = re.search(
                    r"(Gi\S+|Te\S+|Fa\S+|Eth\S+|Port-channel\s*\d+)",
                    description,
                )
                return IntendedConnection(
                    local_port=interface,
                    remote_hostname=hostname,
                    remote_port=port_match.group(1) if port_match else None,
                    description=description,
                    is_flexible=is_flexible,
                )

        return None

    def _build_actual_connections(
        self,
        device: DiscoveredDevice,
        all_devices: dict[str, DiscoveredDevice],
    ) -> dict[str, tuple[str, str]]:
        """Build a map of actual connections from CDP: {local_port: (remote_hostname, remote_port)}."""
        actual: dict[str, tuple[str, str]] = {}

        # Map CDP device IDs back to our intended hostnames
        serial_to_hostname: dict[str, str] = {}
        for ip, d in all_devices.items():
            if d.intended_hostname and d.serial:
                serial_to_hostname[d.serial] = d.intended_hostname
            if d.hostname:
                serial_to_hostname[d.hostname] = d.intended_hostname or d.hostname

        for neighbour in device.cdp_neighbours:
            remote_name = neighbour.remote_device_id.split(".")[0]
            resolved_name = serial_to_hostname.get(remote_name, remote_name)
            actual[neighbour.local_port] = (resolved_name, neighbour.remote_port)

        return actual

    def _diff_connections(
        self,
        intended: dict[str, IntendedConnection],
        actual: dict[str, tuple[str, str]],
        device: DiscoveredDevice,
    ) -> list[CablingResult]:
        """Diff intended vs actual connections for a single device."""
        results: list[CablingResult] = []

        # Check each intended connection
        for port, intent in intended.items():
            if port in actual:
                actual_remote, actual_port = actual[port]

                if actual_remote == intent.remote_hostname:
                    if intent.remote_port and actual_port != intent.remote_port:
                        results.append(CablingResult(
                            local_port=port,
                            status="wrong_port",
                            actual_remote=actual_remote,
                            actual_remote_port=actual_port,
                            intended_remote=intent.remote_hostname,
                            intended_remote_port=intent.remote_port,
                            message=f"Right device, wrong port "
                                    f"(expected {intent.remote_port}, got {actual_port})",
                        ))
                    else:
                        results.append(CablingResult(
                            local_port=port,
                            status="correct",
                            actual_remote=actual_remote,
                            actual_remote_port=actual_port,
                            intended_remote=intent.remote_hostname,
                            intended_remote_port=intent.remote_port,
                        ))
                elif intent.is_flexible:
                    results.append(CablingResult(
                        local_port=port,
                        status="adaptable",
                        actual_remote=actual_remote,
                        actual_remote_port=actual_port,
                        intended_remote=intent.remote_hostname,
                        message=f"Flexible port — will adapt config to match {actual_remote}",
                    ))
                else:
                    results.append(CablingResult(
                        local_port=port,
                        status="wrong_device",
                        actual_remote=actual_remote,
                        actual_remote_port=actual_port,
                        intended_remote=intent.remote_hostname,
                        message=f"Expected {intent.remote_hostname}, found {actual_remote}",
                    ))
            else:
                results.append(CablingResult(
                    local_port=port,
                    status="missing",
                    intended_remote=intent.remote_hostname,
                    intended_remote_port=intent.remote_port,
                    message=f"No device detected — expected {intent.remote_hostname}",
                ))

        # Check for unexpected connections (in actual but not in intended)
        intended_ports = set(intended.keys())
        for port, (remote, remote_port) in actual.items():
            if port not in intended_ports:
                results.append(CablingResult(
                    local_port=port,
                    status="unexpected",
                    actual_remote=remote,
                    actual_remote_port=remote_port,
                    message=f"Unexpected connection to {remote} — not in design",
                ))

        return results

    def print_report(self, results: dict[str, list[CablingResult]]) -> None:
        """Print a formatted cabling validation report."""
        totals = {"correct": 0, "adaptable": 0, "wrong_device": 0,
                  "wrong_port": 0, "missing": 0, "unexpected": 0}

        for serial, device_results in results.items():
            spec = self.inventory.devices.get(serial, {})
            hostname = spec.get("hostname", serial)

            table = Table(title=f"  {hostname} ({serial})")
            table.add_column("Port", style="cyan")
            table.add_column("Status")
            table.add_column("Details")

            for r in device_results:
                totals[r.status] = totals.get(r.status, 0) + 1

                status_style = {
                    "correct": "[green]✓ Correct[/]",
                    "adaptable": "[blue]↔ Adaptable[/]",
                    "wrong_device": "[red]✗ Wrong Device[/]",
                    "wrong_port": "[yellow]⚠ Wrong Port[/]",
                    "missing": "[red]✗ Missing[/]",
                    "unexpected": "[yellow]⚠ Unexpected[/]",
                }
                table.add_row(r.local_port, status_style.get(r.status, r.status), r.message)

            console.print(table)

        # Summary
        console.print("\n[bold]Cabling Summary[/]")
        console.print(f"  [green]Correct:[/]    {totals['correct']}")
        console.print(f"  [blue]Adaptable:[/] {totals['adaptable']}")
        console.print(f"  [yellow]Wrong port:[/] {totals['wrong_port']}")
        console.print(f"  [red]Wrong device:[/] {totals['wrong_device']}")
        console.print(f"  [red]Missing:[/]    {totals['missing']}")
        console.print(f"  [yellow]Unexpected:[/] {totals['unexpected']}")

        blockers = totals["wrong_device"] + totals["missing"]
        if blockers:
            console.print(f"\n  [bold red]{blockers} blocking issue(s) — deployment cannot proceed[/]")
        else:
            console.print(f"\n  [bold green]No blocking issues — ready to deploy[/]")
