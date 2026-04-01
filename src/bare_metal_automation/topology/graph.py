"""NetworkX topology graph builder — nodes keyed by serial number.

Builds an undirected graph where:
  - Nodes  = devices, keyed by serial number (stable identifier)
  - Edges  = physical cables, annotated with port labels from CDP data

Using serials as keys (vs IP addresses) means the graph survives DHCP
lease renewal and is stable across discovery retries.
"""

from __future__ import annotations

import logging

import networkx as nx

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)


def build_graph(devices: dict[str, DiscoveredDevice]) -> nx.Graph:
    """Build a NetworkX undirected graph from CDP neighbour tables.

    Args:
        devices: Mapping of ``ip → DiscoveredDevice`` as produced by the
                 discovery engine.  Devices with ``serial=None`` are skipped.

    Returns:
        An ``nx.Graph`` where each node is a device serial and each edge is
        a physical cable with ``local_port`` / ``remote_port`` attributes.
    """
    graph: nx.Graph = nx.Graph()

    # Index: serial → device for edge resolution
    serial_to_device: dict[str, DiscoveredDevice] = {}
    # Index: CDP device-ID strings → serial (CDP reports FQDNs / hostnames)
    cdp_id_to_serial: dict[str, str] = {}

    for device in devices.values():
        if not device.serial:
            continue
        serial_to_device[device.serial] = device

        # Node attributes
        graph.add_node(
            device.serial,
            ip=device.ip,
            hostname=device.intended_hostname or device.hostname or device.ip,
            role=device.role or "unknown",
            platform=device.device_platform or device.platform or "unknown",
            state=device.state.value,
            bfs_depth=device.bfs_depth,
        )

        # Build CDP-ID → serial lookup (handles FQDNs and short names)
        if device.hostname:
            cdp_id_to_serial[device.hostname] = device.serial
            cdp_id_to_serial[device.hostname.split(".")[0]] = device.serial
        if device.intended_hostname:
            cdp_id_to_serial[device.intended_hostname] = device.serial
            cdp_id_to_serial[device.intended_hostname.split(".")[0]] = device.serial

    # Also index by IP for CDP entries that only carry an IP
    ip_to_serial: dict[str, str] = {d.ip: d.serial for d in devices.values() if d.serial}

    # Add edges from CDP neighbour data
    for device in devices.values():
        if not device.serial:
            continue
        for nbr in device.cdp_neighbours:
            remote_id = nbr.remote_device_id.split(".")[0]

            # Resolve to serial
            remote_serial = (
                cdp_id_to_serial.get(remote_id)
                or cdp_id_to_serial.get(nbr.remote_device_id)
                or ip_to_serial.get(nbr.remote_ip, "")
            )

            if not remote_serial or remote_serial not in graph:
                logger.debug(
                    f"CDP neighbour '{nbr.remote_device_id}' on "
                    f"{device.intended_hostname}:{nbr.local_port} "
                    f"not resolved to a known device — skipped"
                )
                continue

            # Avoid duplicate edges (CDP is bidirectional)
            if not graph.has_edge(device.serial, remote_serial):
                graph.add_edge(
                    device.serial,
                    remote_serial,
                    local_port=nbr.local_port,
                    remote_port=nbr.remote_port,
                )

    logger.info(
        f"Topology graph: {graph.number_of_nodes()} nodes, "
        f"{graph.number_of_edges()} edges"
    )
    return graph
