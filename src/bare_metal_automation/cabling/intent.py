"""Cabling intent loader — parse expected connections from a YAML rules file.

The cabling-rules YAML describes the intended physical topology for each site
template.  It lives alongside the config templates and is referenced from the
inventory device spec via the ``cabling_rules`` key.

Example YAML structure
----------------------
::

    # configs/cabling/site-template-A.yaml
    connections:
      - local_device: sw-core-01
        local_port: GigabitEthernet1/0/48
        remote_device: sw-access-01
        remote_port: GigabitEthernet0/1
        description: "Core uplink"
        flexible: false

      - local_device: sw-core-01
        local_port: GigabitEthernet1/0/1
        remote_device: svr-compute-01
        remote_port: ""
        description: "Server access — port can vary"
        flexible: true
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)


@dataclass
class CablingRule:
    """A single intended physical connection between two devices."""

    local_device: str
    local_port: str
    remote_device: str
    remote_port: str | None = None
    description: str = ""
    flexible: bool = False  # True → port mismatch is adaptable, not blocking


def load_cabling_rules(path: str | Path) -> list[CablingRule]:
    """Load cabling rules from a YAML file.

    Returns a flat list of ``CablingRule`` objects.  An empty list is
    returned (with a warning) if the file doesn't exist or is malformed.
    """
    p = Path(path)
    if not p.exists():
        logger.warning(f"Cabling rules file not found: {p}")
        return []

    try:
        with open(p) as f:
            data = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse cabling rules YAML: {e}")
        return []

    rules: list[CablingRule] = []
    for entry in data.get("connections", []):
        try:
            rules.append(
                CablingRule(
                    local_device=entry["local_device"],
                    local_port=entry["local_port"],
                    remote_device=entry["remote_device"],
                    remote_port=entry.get("remote_port") or None,
                    description=entry.get("description", ""),
                    flexible=bool(entry.get("flexible", False)),
                )
            )
        except KeyError as e:
            logger.warning(f"Skipping malformed cabling rule (missing key {e}): {entry}")

    logger.info(f"Loaded {len(rules)} cabling rule(s) from {p}")
    return rules


def rules_for_device(
    rules: list[CablingRule],
    hostname: str,
) -> list[CablingRule]:
    """Filter *rules* to those where *hostname* is the local device."""
    return [r for r in rules if r.local_device == hostname]


@dataclass
class CablingIntent:
    """Per-deployment cabling intent: a flat collection of rules with helpers."""

    rules: list[CablingRule] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> CablingIntent:
        return cls(rules=load_cabling_rules(path))

    def for_device(self, hostname: str) -> list[CablingRule]:
        return rules_for_device(self.rules, hostname)

    def port_map(self, hostname: str) -> dict[str, CablingRule]:
        """Return ``{local_port: CablingRule}`` for quick lookup."""
        return {r.local_port: r for r in self.for_device(hostname)}
