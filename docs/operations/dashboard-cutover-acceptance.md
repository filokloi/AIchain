# Dashboard Cutover Acceptance

## Current intent
- Primary dashboard artifact: `catalog_manifest.json`
- Rollback artifact: `ai_routing_table.json`
- Cutover is not considered operationally closed until the canonical dashboard path is verified in a real GitHub Pages deployment and remains stable for a burn-in period.

## Acceptance criteria for stable canonical dashboard mode
1. `index.html` fetches `catalog_manifest.json` first and validates `manifest_type = aichain.catalog`.
2. Rollback to `ai_routing_table.json` remains available and visibly signaled when used.
3. `catalog_manifest.json` reports:
   - `public_artifact_readiness.dashboard_switch_ready = true`
   - `canonical_public_artifact.migration_state = safe_to_switch_dashboard_to_canonical_artifact`
4. GitHub Actions passes:
   - dashboard cutover tests
   - release contract verification (`tools/verify_dashboard_release.py`)
5. Live GitHub Pages deployment serves the updated `index.html` and renders canonical runtime statuses.
6. A burn-in period completes without requiring rollback.

## Burn-in recommendation
- Minimum burn-in: 3 successful arbitration/deploy cycles or 72 hours, whichever is longer.
- During burn-in, legacy rollback must stay enabled.

## What keeps rollback active
- Canonical manifest fetch failure
- Canonical manifest schema/contract validation failure
- `dashboard_switch_ready != true`
- Production rendering regression discovered after deployment

## Conditions to remove legacy feed dependency later
Only after all are true:
1. Burn-in completed without rollback activation.
2. Canonical manifest remains stable across scheduled runs.
3. No unresolved dashboard regressions or artifact compatibility issues remain.
4. A separate removal change is prepared and reviewed; rollback is not removed in the same change that performs cutover.
