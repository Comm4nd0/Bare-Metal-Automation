"""BFS ordering — calculate outside-in configuration sequence.

The laptop is the BFS root.  Devices furthest from the laptop (highest depth)
are configured *first*, so we never reconfigure the path we're currently
managing over.

Topology graph nodes are keyed by serial number (see ``graph.py``).
"""

from __future__ import annotations

import logging
from collections import deque

import networkx as nx

from bare_metal_automation.models import DiscoveredDevice

logger = logging.getLogger(__name__)

# Virtual serial used for the laptop node when it isn't a discovered device
LAPTOP_SERIAL = "__laptop__"


def calculate_bfs_depths(
    graph: nx.Graph,
    root_serial: str,
) -> dict[str, int]:
    """Return ``{serial: depth}`` via BFS from *root_serial*.

    If *root_serial* is not in the graph a virtual laptop node is added and
    connected to any node whose CDP data lists the laptop's IP.
    """
    if root_serial not in graph:
        # Add a virtual laptop node so BFS has a root
        graph.add_node(root_serial, hostname="laptop", role="laptop")

    depths: dict[str, int] = {root_serial: 0}
    queue: deque[str] = deque([root_serial])

    while queue:
        current = queue.popleft()
        for neighbour in graph.neighbors(current):
            if neighbour not in depths:
                depths[neighbour] = depths[current] + 1
                queue.append(neighbour)

    return depths


def outside_in_order(
    graph: nx.Graph,
    root_serial: str,
    devices: dict[str, DiscoveredDevice],
) -> list[str]:
    """Return serials sorted highest-depth first (outside-in).

    Also mutates each ``DiscoveredDevice``:
      - ``bfs_depth`` ← depth from the laptop
      - ``config_order`` ← 0-based position in the returned list

    Args:
        graph:       Topology graph (nodes = serials).
        root_serial: Serial of the laptop / root node.
        devices:     ``ip → DiscoveredDevice`` mapping for BFS annotation.

    Returns:
        Ordered list of serials, deepest first.
    """
    depths = calculate_bfs_depths(graph, root_serial)

    # Build serial → device reverse lookup
    serial_to_device: dict[str, DiscoveredDevice] = {
        d.serial: d for d in devices.values() if d.serial
    }

    # Collect (depth, serial) for all real devices (skip the virtual laptop node)
    depth_pairs: list[tuple[int, str]] = []
    for serial, depth in depths.items():
        if serial == root_serial or serial not in serial_to_device:
            continue
        depth_pairs.append((depth, serial))

    # Sort: descending depth, then serial for determinism within a depth level
    depth_pairs.sort(key=lambda x: (-x[0], x[1]))

    ordered_serials: list[str] = []
    for i, (depth, serial) in enumerate(depth_pairs):
        ordered_serials.append(serial)
        device = serial_to_device[serial]
        device.bfs_depth = depth
        device.config_order = i

    logger.info(
        f"Outside-in config order ({len(ordered_serials)} devices): "
        + ", ".join(
            f"{s}(d={depths.get(s, '?')})" for s in ordered_serials[:8]
        )
        + ("..." if len(ordered_serials) > 8 else "")
    )
    return ordered_serials
