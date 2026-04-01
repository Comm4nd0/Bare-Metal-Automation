"""Topology visualisation — export graph data for the dashboard D3.js renderer.

The dashboard renders an interactive force-directed graph using D3.js.
This module converts the NetworkX topology into the JSON format expected
by the frontend ``/api/topology/`` endpoint.

Node schema
-----------
  id       : serial number (stable key)
  label    : hostname for display
  group    : numeric group (by role) for D3 colour coding
  role     : role string
  platform : platform string
  ip       : bootstrap IP
  state    : current device state string
  depth    : BFS depth from laptop (used for radial layout hints)

Edge schema
-----------
  source      : serial of source device
  target      : serial of target device
  local_port  : port on source
  remote_port : port on target
  label       : combined "Gi1/0/1 ↔ Gi1/0/48"
"""

from __future__ import annotations

import networkx as nx

# Map role → integer group for D3 colour scale
_ROLE_GROUP: dict[str, int] = {
    "core-switch": 1,
    "distribution-switch": 2,
    "access-switch": 3,
    "border-router": 4,
    "perimeter-firewall": 5,
    "compute-node": 6,
    "management-server": 7,
    "ntp-server": 8,
    "laptop": 0,
}


def export_for_d3(graph: nx.Graph) -> dict:
    """Convert *graph* to the JSON structure expected by the D3.js renderer.

    Returns a dict with ``nodes``, ``edges``, and ``metadata`` keys.
    """
    nodes = []
    for serial, data in graph.nodes(data=True):
        role = data.get("role", "unknown")
        nodes.append({
            "id": serial,
            "label": data.get("hostname", serial),
            "group": _ROLE_GROUP.get(role, 9),
            "role": role,
            "platform": data.get("platform", "unknown"),
            "ip": data.get("ip", ""),
            "state": data.get("state", "unknown"),
            "depth": data.get("bfs_depth"),
        })

    edges = []
    for u, v, data in graph.edges(data=True):
        local_port = data.get("local_port", "")
        remote_port = data.get("remote_port", "")
        label = f"{local_port} ↔ {remote_port}" if local_port and remote_port else ""
        edges.append({
            "source": u,
            "target": v,
            "local_port": local_port,
            "remote_port": remote_port,
            "label": label,
        })

    metadata = {
        "node_count": graph.number_of_nodes(),
        "edge_count": graph.number_of_edges(),
    }

    return {"nodes": nodes, "edges": edges, "metadata": metadata}
