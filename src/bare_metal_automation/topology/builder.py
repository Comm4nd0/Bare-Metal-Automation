"""Topology builder — construct network graph and derive configuration order."""

from __future__ import annotations

import logging
from collections import deque

import networkx as nx

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)


class TopologyBuilder:
    """Builds a network topology graph from CDP data and calculates config order."""

    def build_graph(self, devices: dict[str, DiscoveredDevice]) -> nx.Graph:
        """Build a NetworkX graph from discovered CDP neighbour tables.

        Nodes are keyed by IP address. Edges represent physical links with
        port information stored as edge attributes.
        """
        graph = nx.Graph()

        # Add all discovered devices as nodes
        for ip, device in devices.items():
            graph.add_node(
                ip,
                serial=device.serial,
                hostname=device.intended_hostname or device.hostname,
                role=device.role,
                platform=device.platform,
            )

        # Build IP lookup by CDP device ID (hostname) for edge matching
        hostname_to_ip: dict[str, str] = {}
        for ip, device in devices.items():
            if device.hostname:
                hostname_to_ip[device.hostname] = ip
                # CDP often reports FQDN — also index the short name
                hostname_to_ip[device.hostname.split(".")[0]] = ip
            if device.intended_hostname:
                hostname_to_ip[device.intended_hostname] = ip

        # Add edges from CDP neighbour data
        for ip, device in devices.items():
            for neighbour in device.cdp_neighbours:
                remote_id = neighbour.remote_device_id.split(".")[0]

                # Resolve remote device to an IP in our graph
                remote_ip = (
                    hostname_to_ip.get(remote_id)
                    or hostname_to_ip.get(neighbour.remote_device_id)
                    or neighbour.remote_ip
                )

                if remote_ip and graph.has_node(remote_ip):
                    graph.add_edge(
                        ip,
                        remote_ip,
                        local_port=neighbour.local_port,
                        remote_port=neighbour.remote_port,
                    )
                else:
                    logger.warning(
                        f"CDP neighbour {neighbour.remote_device_id} on "
                        f"{device.hostname}:{neighbour.local_port} not found in graph"
                    )

        logger.info(
            f"Topology: {graph.number_of_nodes()} nodes, "
            f"{graph.number_of_edges()} edges"
        )
        return graph

    def calculate_config_order(
        self,
        graph: nx.Graph,
        root_ip: str,
    ) -> list[str]:
        """Calculate device configuration order using reverse BFS from the laptop.

        Devices furthest from the laptop are configured first (outside-in)
        so we never reconfigure the path we're standing on.

        Returns a list of serial numbers in configuration order.
        """
        if root_ip not in graph:
            # Laptop might not be a graph node — find the node directly connected
            # (the nearest switch). We add a virtual laptop node.
            graph.add_node(root_ip, serial=None, hostname="laptop", role="laptop")

        # BFS to assign depth
        depths: dict[str, int] = {root_ip: 0}
        queue: deque[str] = deque([root_ip])

        while queue:
            current = queue.popleft()
            for neighbour in graph.neighbors(current):
                if neighbour not in depths:
                    depths[neighbour] = depths[current] + 1
                    queue.append(neighbour)

        # Assign depth to device objects and collect serials
        device_depths: list[tuple[int, str]] = []
        for ip, depth in depths.items():
            node_data = graph.nodes[ip]
            serial = node_data.get("serial")
            if serial:  # Skip the laptop node
                device_depths.append((depth, serial))

        # Sort by depth descending — furthest first
        # Within same depth, sort by serial for determinism
        device_depths.sort(key=lambda x: (-x[0], x[1]))

        config_order = [serial for _, serial in device_depths]

        logger.info(f"Configuration order: {config_order}")
        return config_order

    def detect_loops(self, graph: nx.Graph) -> list[list[str]]:
        """Detect any loops in the topology that might indicate cabling errors."""
        cycles = list(nx.cycle_basis(graph))
        if cycles:
            logger.warning(f"Detected {len(cycles)} loop(s) in topology")
        return cycles

    def get_management_path(
        self,
        graph: nx.Graph,
        root_ip: str,
        target_ip: str,
    ) -> list[str] | None:
        """Find the path from laptop to a target device.

        Used to verify management reachability won't be broken during config.
        """
        try:
            return nx.shortest_path(graph, root_ip, target_ip)
        except nx.NetworkXNoPath:
            logger.error(f"No path from laptop to {target_ip}")
            return None

    def export_topology(self, graph: nx.Graph) -> dict:
        """Export topology as a JSON-serialisable dict for the dashboard."""
        nodes = []
        for ip, data in graph.nodes(data=True):
            nodes.append({
                "id": ip,
                "hostname": data.get("hostname", ip),
                "role": data.get("role", "unknown"),
                "platform": data.get("platform", "unknown"),
                "serial": data.get("serial"),
            })

        edges = []
        for u, v, data in graph.edges(data=True):
            edges.append({
                "source": u,
                "target": v,
                "local_port": data.get("local_port", ""),
                "remote_port": data.get("remote_port", ""),
            })

        return {"nodes": nodes, "edges": edges}
