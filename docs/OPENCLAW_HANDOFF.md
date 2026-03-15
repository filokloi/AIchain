# AIchain OpenClaw Integration & Operator Handoff

Welcome to AIchain within OpenClaw! This guide explains how the AIchain native bridge functions, how to understand routing metadata inside the UI, and day-one operator protocols.

---

## 1. How AIchain Hooks into OpenClaw
OpenClaw considers AIchain a "Skill". When OpenClaw wants to run a chat completion through the proxy, it does **not** call the AI APIs directly. Instead:
1. OpenClaw invokes our CLI wrapper: `python <openclaw-dir>/ai-chain-skill/skill.py chat "Message..."` 
2. `skill.py` immediately authenticates via your local `~/.openclaw/aichain/.auth_token`.
3. The request is forwarded to `aichaind` listening on `http://127.0.0.1:8080`.
4. The proxy computes the optimal cost/intelligence route (the "Brain"), executes the generation, and returns the strictly formatted result back to OpenClaw.

If `aichaind` is entirely offline or unreachable, `skill.py` handles the failure cleanly and warns the operator that the AIchain sidecar must be started.

## 2. Setting up the OpenClaw Dashboard Overlay
AIchain v5 introduces an **Overlay Dashboard Chip** natively injected into the OpenClaw UI. This tells you exactly what model is fulfilling your request and what rule was used. 

**To Install the Bridge Chip**:
Inside the `aichaind` ecosystem, you can manually test string injection logic using `python -m aichaind.ui.openclaw_install`. However, for v1 stability, the standalone launcher scripts `start-aichain` automatically provision the proxy on port 8080. OpenClaw natively exposes HTML UI customizations; you only need to ensure the daemon is running in the background.

## 3. The "Two-Brain" Concept for Operators
AIchain is specifically designed to balance intelligence vs cost without manual intervention.

- `fast_brain` ($0 tier / local): Used for standard conversational prompts.
- `heavy_brain` (Premium Claude/GPT-4 / Gemini): Reserved automatically when reasoning, coding, or schema extraction is strictly necessary, OR when the Fast Brain fails repeatedly.

**Session Control**:
You can click the AIchain Overlay Chip in your UI to force manual lock combinations (e.g. `Lock to gpt-4`). This bypasses cost optimization for the duration of that session.

## 4. Operator Troubleshooting Sandbox
If OpenClaw is throwing routing errors:
1. **Check the Proxy**: Are your `start-aichain` scripts running? `skill.py` cannot route traffic if `aichaind` is dead. 
2. **Consult the Doctor**: Run `python tools/aichain_doctor.py`. This will check port bounds and credentials.
3. **Check the Status Endpoint**: Browse to `http://127.0.0.1:8080/status`. You will see `"bridge_connected": true` and `"system": "NORMAL"`. If it says `"DEGRADED"`, the proxy is experiencing heavy API failures and is likely blocking aggressive re-routing.

This dual-layer architecture guarantees OpenClaw remains thin and fast while AIchain handles the severe complexities of budget limits and connection volatility.
