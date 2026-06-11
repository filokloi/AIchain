# AIchain v5 Installation

## What gets installed

AIchain v5 is split into two local parts:

- `openclaw-skill/skill.py`
  - thin bridge from OpenClaw to the sidecar
- `aichaind`
  - local routing, policy, provider, security, and execution daemon

The public site is not the runtime.
The site only publishes the global catalog and manifest used by the sidecar.

## Default catalog source

```text
https://filokloi.github.io/AIchain/catalog_manifest.json
```

## Local setup

```powershell
cd C:\Users\filok\.openclaw\workspace
git clone https://github.com/filokloi/AIchain.git
cd AIchain
pip install -e .
```

Installing the package (instead of just the requirements) registers the
`aichaind` console command and makes the package importable from anywhere.
For development extras (pytest): `pip install -e .[dev]`.

## Start the sidecar

```powershell
aichaind config/default.json
# or equivalently:
python -m aichaind.main config/default.json
```

## Use the thin skill bridge

```powershell
python .\openclaw-skill\skill.py status
python .\openclaw-skill\skill.py chat "hello"
```

## Notes

- The sidecar reads provider credentials from the local OpenClaw config / environment.
- Other users can use the same package structure with their own credentials.
- The legacy `ai-chain-skill` path remains in the repository only for compatibility and migration.

## Current gaps

Still pending before wider distribution:

- canonical session lifecycle in live request handling
- stronger policy/privacy enforcement
- final private workspace skill packaging

Recently addressed:

- package/install path cleanup: `pyproject.toml` now provides a standard
  `pip install -e .` flow with an `aichaind` console script, and the test
  suite no longer depends on machine-specific absolute paths.
