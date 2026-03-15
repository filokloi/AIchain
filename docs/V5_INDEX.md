# AIchain v5 Technical Index

This index provides a structured map of the AIchain v5 "Native Operator" baseline. Use these documents to understand the system architecture, operational procedures, and the formal verification audits performed during the v5 release cycle.

## 🚀 Operations & Deployment
- **[Operator Runbook](OPERATOR_RUNBOOK.md)**: The core reference for booting, diagnosing, and monitoring the `aichaind` sidecar.
- **[OpenClaw Handoff](OPENCLAW_HANDOFF.md)**: Essential context and onboarding for the first-time operator.
- **[Deployment Reproducibility](DEPLOYMENT_REPRODUCIBILITY.md)**: Summary of the bootstrap scripts (`setup.ps1`, `install.sh`) and their dependency logic.

## 🏗 Architecture & Baseline
- **[V5 Baseline Inventory](V5_BASELINE.md)**: The architectural "map" of the core modules, handlers, and security boundaries.
- **[Release Notes v5](RELEASE_NOTES_V5.md)**: High-level feature list and the "Postulate-driven" design philosophy of this release.

## ⚖️ Verification & Technical Reviews
The following documents summarize the rigorous evidence-based verification steps taken to stabilize the v5 baseline.

- **[V5 Baseline Freeze Review](V5_BASELINE_FREEZE_REVIEW.md)**: Formal audit of the repository cleanliness, tagging precision, and security baseline.
- **[Operator Metrics Review](OPERATOR_METRICS_REVIEW.md)**: Implementation review of the in-memory telemetry system and `/status` monitoring.
- **[POSIX Confirmation Review](POSIX_CONFIRMATION_REVIEW.md)**: Honors the "Partially Verified" status and documents platform-specific constraints.
- **[Rollout Validation Review](ROLLOUT_VALIDATION_REVIEW.md)**: Audit of fresh-machine bootstrap behavior beyond the original development node.
- **[OpenClaw Integration Review](OPENCLAW_INTEGRATION_REVIEW.md)**: Verification of the bridge logic, visual dashboard, and session-aware routing.
- **[Burn-in & Closure Review](BURN_IN_AND_CLOSURE.md)**: Certification of the Godmode routing, multi-brain logic, and initial stability passes.

---
*AIchain v5 is a stable, resting baseline. Future development should branch from the `v5.0.0-stable` tag.*
