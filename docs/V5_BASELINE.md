# AIchain v5 Baseline Inventory

*This document serves as the formal architectural map of AIchain at the v5.0.0-stable code freeze.*

## Core Runtime Baseline
The beating heart of AIchain v5:
- `aichaind/main.py`: The daemon orchestration layer executing token management, log hooking, and route evaluation telemetry.
- `aichaind/core/state_machine.py`: Zero-inference logical routing plane. Dictates when to use `fast_brain` versus escalating to `heavy_brain`.
- `aichaind/providers/base.py`: The universal adapter class that heavily enforces strict unified timeout resolutions across all API connections.
- `openclaw-skill/skill.py`: The explicit UI bridge tying OpenClaw `/chat` invocations downstream into `aichaind` without parsing Python JSON stacks on failures.

## Bootstrap and Reproducibility
The deployment mechanism:
- `setup.ps1` & `install.sh`: Operating-system specific scripts handling Python execution environments and `~/.openclaw` directory constructions.
- `start-aichain.ps1` & `start-aichain.sh`: Launchers locking `PYTHONPATH` correctly.
- `tools/aichain_doctor.py`: Diagnostics executable for assessing system boundaries.
- `tools/aichain_smoke_test.py`: Native proxy health evaluation tool mimicking fully authenticated API transactions.

## OpenClaw Native Integration
- `aichaind/ui/openclaw_bridge.py`: JavaScript generator for the OpenClaw visual dashboard overlay.
- `aichaind/ui/companion_panel.py`: Standalone explicit web control UI matching session tracking logic with exact AI payload routing.

## Document Archives
- `docs/BURN_IN_AND_CLOSURE.md`: The Godmode and test-pass certification document locking down initial proxy logic.
- `docs/DEPLOYMENT_REPRODUCIBILITY.md`: Formal verification of cross-node bootstrap execution logic.
- `docs/OPENCLAW_HANDOFF.md`: The operator's day-one manual detailing the runtime commands context.
- `docs/OPENCLAW_INTEGRATION_REVIEW.md`: The formal verification of clean AIchain-OpenClaw execution handling.

Future modifications to the above structures violate the v5 baseline and represent v6+ architecture revisions.
