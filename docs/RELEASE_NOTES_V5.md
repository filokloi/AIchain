# AIchain v5.0.0-stable — Release Notes

## Overview
AIchain v5 introduces a mature, stable "Two-Brain" architecture that seamlessly provisions LLM proxy streams for OpenClaw. This release moves past rapid prototyping into a hardened, burned-in, and reproducible deployment pattern for single-operator hosts.

## Key Features & Hardening (v5 Scope)
1. **Deterministic State Machine**: A resilient loop (`aichaind.core.state_machine`) guarantees safe API fault handling across cloud networks.
2. **Quota Exhaustion Tracking**: Actively manages provider 429 logic, temporarily demoting faltering providers without throwing fatal errors to the end-user.
3. **Reproducible Bootstrap**: Native support for zero-magic setups via `setup.ps1` and `install.sh`. Operators run exactly two scripts to achieve an entire OpenClaw AI proxy.
4. **Native OpenClaw UI Injection**: A floating companion UI chip injects into OpenClaw (via `openclaw_bridge.js`) giving operators explicit routing transparency right on the dashboard.
5. **Preflight Diagnostics**: Bundled `aichain_doctor.py` natively spots port-binding and module issues before launching, completely removing guesswork for system operators setting up new nodes.

## What is INTENTIONALLY Excluded
- **Multi-User Capabilities**: The proxy isolates state per host, preventing tenant isolation leaks. Complex IAM layers are deferred.
- **Speculative Provider Integrations**: We ship deep optimizations for Gemini, OpenRouter, DeepSeek, and Local LMStudio profiles specifically. Unstable networks were excluded to protect the Fast-Brain/Heavy-Hitter dualism.

## Future Operator Guidance
This concludes v5 feature expansion. Any ongoing enhancements should strictly focus on API logic bugs, extending metrics exports, or integrating Multi-User architectures via a new v6 feature branch.
