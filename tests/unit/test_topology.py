"""Tests for the topology builder — graph construction, BFS, config order."""

from __future__ import annotations

import networkx as nx
import pytest

from bare_metal_automation.models import (
    CDPNeighbour,
    DeviceState,
    DiscoveredDevice,
)
from bare_metal_automation.topology.builder import TopologyBuilder

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture
def builder() -> TopologyBuilder:
    return TopologyBuilder()


def _make_device(
    ip: str,
    serial: str,
    hostname: str,
    cdp_neighbours: list[CDPNeighbour] | None = None,
) -> DiscoveredDevice:
    return DiscoveredDevice(
        ip=ip,
        serial=serial,
        hostname=hostname,
        intended_hostname=hostname,
        cdp_neighbours=cdp_neighbours or [],
        state=DeviceState.IDENTIFIED,
    )


@pytest.fixture
def three_node_topology() -> dict[str, DiscoveredDevice]:
    """Laptop -- Core -- Access (linear topology)."""
    return {
        "10.0.0.1": _make_device(
            "10.0.0.1", "S-CORE", "core-sw",
            cdp_neighbours=[
                CDPNeighbour(
                    local_port="Gi1/0/48",
                    remote_device_id="access-sw",
                    remote_port="Gi1/0/1",
                    remote_platform="WS-C3850",
                    remote_ip="10.0.0.2",
                ),
            ],
        ),
        "10.0.0.2": _make_device(
            "10.0.0.2", "S-ACCESS", "access-sw",
            cdp_neighbours=[
                CDPNeighbour(
                    local_port="Gi1/0/1",
                    remote_device_id="core-sw",
                    remote_port="Gi1/0/48",
                    remote_platform="WS-C3850",
                    remote_ip="10.0.0.1",
                ),
            ],
        ),
    }


# ── Graph construction ───────────────────────────────────────────────────


class TestBuildGraph:
    def test_nodes_added(
        self, builder: TopologyBuilder, three_node_topology: dict
    ) -> None:
        graph = builder.build_graph(three_node_topology)

        assert graph.number_of_nodes() == 2
        assert graph.has_node("10.0.0.1")
        assert graph.has_node("10.0.0.2")

    def test_edges_added(
        self, builder: TopologyBuilder, three_node_topology: dict
    ) -> None:
        graph = builder.build_graph(three_node_topology)

        assert graph.number_of_edges() == 1
        assert graph.has_edge("10.0.0.1", "10.0.0.2")

    def test_node_attributes(
        self, builder: TopologyBuilder, three_node_topology: dict
    ) -> None:
        graph = builder.build_graph(three_node_topology)

        node_data = graph.nodes["10.0.0.1"]
        assert node_data["serial"] == "S-CORE"
        assert node_data["hostname"] == "core-sw"

    def test_edge_attributes(
        self, builder: TopologyBuilder, three_node_topology: dict
    ) -> None:
        graph = builder.build_graph(three_node_topology)

        edge_data = graph.edges["10.0.0.1", "10.0.0.2"]
        # Both directions add the same edge; last write wins in undirected graph.
        # The important thing is the edge has port attributes.
        assert "local_port" in edge_data
        assert "remote_port" in edge_data

    def test_empty_devices(self, builder: TopologyBuilder) -> None:
        graph = builder.build_graph({})
        assert graph.number_of_nodes() == 0
        assert graph.number_of_edges() == 0

    def test_cdp_neighbour_not_in_graph_no_edge(
        self, builder: TopologyBuilder
    ) -> None:
        """A CDP neighbour referencing an unknown device should not create an edge."""
        devices = {
            "10.0.0.1": _make_device(
                "10.0.0.1", "S1", "sw1",
                cdp_neighbours=[
                    CDPNeighbour(
                        local_port="Gi1/0/1",
                        remote_device_id="unknown-device",
                        remote_port="Gi1/0/1",
                        remote_platform="",
                        remote_ip="10.0.0.99",
                    ),
                ],
            ),
        }

        graph = builder.build_graph(devices)

        assert graph.number_of_nodes() == 1
        assert graph.number_of_edges() == 0

    def test_fqdn_cdp_resolution(self, builder: TopologyBuilder) -> None:
        """CDP device IDs with domain suffixes should resolve to short hostname."""
        devices = {
            "10.0.0.1": _make_device(
                "10.0.0.1", "S1", "sw1",
                cdp_neighbours=[
                    CDPNeighbour(
                        local_port="Gi1/0/1",
                        remote_device_id="sw2.domain.local",
                        remote_port="Gi1/0/2",
                        remote_platform="",
                        remote_ip="10.0.0.2",
                    ),
                ],
            ),
            "10.0.0.2": _make_device("10.0.0.2", "S2", "sw2"),
        }

        graph = builder.build_graph(devices)

        assert graph.has_edge("10.0.0.1", "10.0.0.2")


# ── BFS depth and config order ───────────────────────────────────────────


class TestConfigOrder:
    def test_linear_topology_order(self, builder: TopologyBuilder) -> None:
        """Laptop -> Core -> Access should produce [Access, Core]."""
        devices = {
            "10.0.0.1": _make_device(
                "10.0.0.1", "S-CORE", "core",
                cdp_neighbours=[
                    CDPNeighbour(
                        local_port="Gi1/0/48",
                        remote_device_id="access",
                        remote_port="Gi1/0/1",
                        remote_platform="",
                        remote_ip="10.0.0.2",
                    ),
                ],
            ),
            "10.0.0.2": _make_device("10.0.0.2", "S-ACCESS", "access",
                cdp_neighbours=[
                    CDPNeighbour(
                        local_port="Gi1/0/1",
                        remote_device_id="core",
                        remote_port="Gi1/0/48",
                        remote_platform="",
                        remote_ip="10.0.0.1",
                    ),
                ],
            ),
        }

        graph = builder.build_graph(devices)
        # Laptop is at 10.255.0.1, connected to core at 10.0.0.1
        graph.add_node("10.255.0.1", serial=None, hostname="laptop", role="laptop")
        graph.add_edge("10.255.0.1", "10.0.0.1")

        order = builder.calculate_config_order(graph, "10.255.0.1")

        # Access (depth=2) should come before Core (depth=1) — outside-in
        assert order == ["S-ACCESS", "S-CORE"]

    def test_root_not_in_graph_gets_added(
        self, builder: TopologyBuilder
    ) -> None:
        """If root_ip is not a graph node, it should be added."""
        devices = {
            "10.0.0.1": _make_device("10.0.0.1", "S1", "sw1"),
        }
        graph = builder.build_graph(devices)

        builder.calculate_config_order(graph, "10.255.0.1")

        assert graph.has_node("10.255.0.1")
        # S1 may or may not be reachable depending on edges
        # but root should be in the graph

    def test_disconnected_node_not_in_order(
        self, builder: TopologyBuilder
    ) -> None:
        """Disconnected nodes cannot be reached by BFS and have no serial in output."""
        devices = {
            "10.0.0.1": _make_device("10.0.0.1", "S1", "sw1"),
            "10.0.0.2": _make_device("10.0.0.2", "S2", "sw2"),
        }
        graph = builder.build_graph(devices)
        # No edges — nodes are disconnected
        graph.add_node("10.255.0.1", serial=None, hostname="laptop", role="laptop")
        graph.add_edge("10.255.0.1", "10.0.0.1")

        order = builder.calculate_config_order(graph, "10.255.0.1")

        # Only S1 is reachable from laptop
        assert "S1" in order
        assert "S2" not in order

    def test_same_depth_sorted_by_serial(
        self, builder: TopologyBuilder
    ) -> None:
        """Devices at the same BFS depth should be sorted alphabetically by serial."""
        devices = {
            "10.0.0.1": _make_device(
                "10.0.0.1", "S-CORE", "core",
                cdp_neighbours=[
                    CDPNeighbour(
                        local_port="Gi1/0/1", remote_device_id="access-b",
                        remote_port="Gi1/0/1", remote_platform="", remote_ip="10.0.0.2",
                    ),
                    CDPNeighbour(
                        local_port="Gi1/0/2", remote_device_id="access-a",
                        remote_port="Gi1/0/1", remote_platform="", remote_ip="10.0.0.3",
                    ),
                ],
            ),
            "10.0.0.2": _make_device("10.0.0.2", "S-B", "access-b",
                cdp_neighbours=[CDPNeighbour(
                    local_port="Gi1/0/1", remote_device_id="core",
                    remote_port="Gi1/0/1", remote_platform="", remote_ip="10.0.0.1",
                )],
            ),
            "10.0.0.3": _make_device("10.0.0.3", "S-A", "access-a",
                cdp_neighbours=[CDPNeighbour(
                    local_port="Gi1/0/1", remote_device_id="core",
                    remote_port="Gi1/0/2", remote_platform="", remote_ip="10.0.0.1",
                )],
            ),
        }
        graph = builder.build_graph(devices)
        graph.add_node("10.255.0.1", serial=None, hostname="laptop", role="laptop")
        graph.add_edge("10.255.0.1", "10.0.0.1")

        order = builder.calculate_config_order(graph, "10.255.0.1")

        # S-A and S-B are at depth 2, S-CORE at depth 1
        assert order[0] == "S-A"  # Alphabetically first at depth 2
        assert order[1] == "S-B"
        assert order[2] == "S-CORE"


# ── Cycle detection ──────────────────────────────────────────────────────


class TestCycleDetection:
    def test_no_cycles(self, builder: TopologyBuilder) -> None:
        graph = nx.Graph()
        graph.add_edge("A", "B")
        graph.add_edge("B", "C")

        cycles = builder.detect_loops(graph)

        assert cycles == []

    def test_cycle_detected(self, builder: TopologyBuilder) -> None:
        graph = nx.Graph()
        graph.add_edge("A", "B")
        graph.add_edge("B", "C")
        graph.add_edge("C", "A")

        cycles = builder.detect_loops(graph)

        assert len(cycles) == 1


# ── Management path ──────────────────────────────────────────────────────


class TestManagementPath:
    def test_path_exists(self, builder: TopologyBuilder) -> None:
        graph = nx.Graph()
        graph.add_edge("laptop", "core")
        graph.add_edge("core", "access")

        path = builder.get_management_path(graph, "laptop", "access")

        assert path == ["laptop", "core", "access"]

    def test_no_path(self, builder: TopologyBuilder) -> None:
        graph = nx.Graph()
        graph.add_node("laptop")
        graph.add_node("isolated")

        path = builder.get_management_path(graph, "laptop", "isolated")

        assert path is None


# ── Topology export ──────────────────────────────────────────────────────


class TestExportTopology:
    def test_export_structure(
        self, builder: TopologyBuilder, three_node_topology: dict
    ) -> None:
        graph = builder.build_graph(three_node_topology)

        export = builder.export_topology(graph)

        assert "nodes" in export
        assert "edges" in export
        assert len(export["nodes"]) == 2
        assert len(export["edges"]) == 1

        node_ids = {n["id"] for n in export["nodes"]}
        assert "10.0.0.1" in node_ids
        assert "10.0.0.2" in node_ids

    def test_export_empty_graph(self, builder: TopologyBuilder) -> None:
        graph = nx.Graph()

        export = builder.export_topology(graph)

        assert export["nodes"] == []
        assert export["edges"] == []
