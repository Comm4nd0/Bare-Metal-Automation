"""Bare Metal Automation — NetBox site lifecycle orchestrator.

This package manages the full lifecycle of a site in NetBox:
  - site_generate   : create a new site from a template
  - site_regenerate : detect and optionally fix drift vs template
  - fleet_scan      : report template versions across all sites
  - orchestrate     : end-to-end pipeline (generate → validate → export)
  - validators      : node-level validation helpers
"""

__version__ = "1.0.0"
