# dashboard/ — DEPRECATED

> **This directory is deprecated as of Sprint 5 (2026-04-04).**
> All functionality has been consolidated into
> `src/bare_metal_automation/dashboard/`.

## What was here

This directory contained two Django apps that were originally created as a
standalone Sprint 3 deployment tracker:

| App | Purpose |
|-----|---------|
| `deploy/` | Deployment tracking — Deployment, DeploymentPhase, DeploymentDevice, DeviceLog, FactoryReset, ResetPhase, DeviceResetCertificate |
| `fleet/` | Fleet compliance — SiteRecord, TemplateRecord, FleetScan, SiteComplianceRecord |

## Where things went

| Legacy model | Primary equivalent |
|---|---|
| `deploy.Deployment` | `dashboard.Deployment` (fields merged) |
| `deploy.DeploymentPhase` | `dashboard.DeploymentPhase` (**new**) |
| `deploy.DeploymentDevice` | `dashboard.Device` (similar; use Device for new code) |
| `deploy.DeviceLog` | `dashboard.DeploymentLog` |
| `deploy.FactoryReset` | `dashboard.FactoryReset` (**new**) |
| `deploy.ResetPhase` | `dashboard.ResetPhase` (**new**) |
| `deploy.DeviceResetCertificate` | `dashboard.DeviceResetCertificate` (**new**) |
| `fleet.SiteRecord` | `dashboard.SiteRecord` (**new**) |
| `fleet.TemplateRecord` | `dashboard.TemplateRecord` (**new**) |
| `fleet.FleetScan` | `dashboard.FleetScan` (**new**) |
| `fleet.SiteComplianceRecord` | `dashboard.SiteComplianceRecord` (**new**) |

## Migration path

1. Apply migration `0005_consolidation` to the primary dashboard database.
2. Run a data migration (not provided — site-specific) to copy records from
   the legacy database tables into the primary tables.
3. Update any direct imports of `deploy.models` or `fleet.models` to use
   `bare_metal_automation.dashboard.models`.
4. Remove this directory once data migration is complete.

## Management commands

The legacy management commands (`deploy_vcenter`, `configure_vnet`,
`deploy`, `discover`, etc.) now emit a deprecation warning and forward to
the primary implementations where possible.

**Do not add new features here.**
