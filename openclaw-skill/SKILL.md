---
name: AIchain
description: Local-first AI routing bridge for OpenClaw. Uses the aichaind sidecar to choose the best available model automatically, or to honor a manual session lock when the user requests it.
allowed-tools: Exec(*)
---

# AIchain Skill

AIchain is the OpenClaw-facing bridge for the local `aichaind` sidecar.

Use this skill when the user wants:
- automatic model routing across local, API, and OAuth-backed models
- a manual model lock for the current session
- routing explanations, access status, or model selection details
- the best available model for the task under the current user entitlements

## Runtime

- Sidecar endpoint: `http://127.0.0.1:8080`
- Auth token path: `~/.openclaw/aichain/.auth_token`

## Commands

Run the bridge directly:

```powershell
python skill.py chat "Explain this code"
python skill.py chat "Use GPT-5.4 for this session" --session-id my-session
python skill.py chat "Lock to DeepSeek Chat" --manual --manual-model deepseek/deepseek-chat --manual-provider deepseek --persist --session-id my-session
python skill.py chat "Return to auto routing" --auto --persist --session-id my-session
python skill.py status
python skill.py start
```

## Behavior

- In `auto` mode, AIchain uses the global catalog plus local access state to pick the best available model.
- In `manual` mode, AIchain keeps the selected model locked for the session when `--persist` is used.
- Free-language control intents are supported. The user does not need rigid commands if the intent is clear.

## Notes

- The public AIchain site remains global and independent.
- User-specific controls belong in OpenClaw and the local sidecar, not in the public site.
