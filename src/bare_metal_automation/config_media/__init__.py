"""Config & Media Generation — Pillar 2 of Bare Metal Automation.

Transforms NetBox device data and file-server media into a complete,
self-contained deployment bundle ready for offline provisioning.

Modules:
    renderer           — Jinja2 config rendering from NetBox device context
    inventory_export   — Generate BMA inventory.yaml from NetBox
    firmware_catalogue — Load and resolve firmware_catalogue.yaml
    media_collector    — Copy firmware/ISO/cert files with checksum verification
    bundle_packager    — Assemble the final deployment bundle
    generate           — CLI entry point that orchestrates all of the above
"""
