# AIchain Site/Feed Contract v5

## Purpose

This contract turns AIchain from:

- a public dashboard with a JSON ranking table
- and a separate local routing sidecar

into one system with two planes:

- the site/feed as the global catalog and information plane
- `openclaw-skill` + `aichaind` as the local execution plane

## Architectural rule

The site must publish a versioned catalog manifest.
`aichaind` must validate that manifest before using it for routing.

The feed is not just data.
It is the compatibility boundary between the public control/catalog plane and the private execution plane.

## Current rollout mode

Two modes are supported:

- `legacy_v4`
- `native_v5`

`legacy_v4` exists so the current live site can keep working while the local daemon migrates.
`native_v5` is the target contract.

## Required outcomes

The contract must let the local sidecar answer these questions without guessing:

- what schema version is this feed
- is it compatible with this `aichaind` build
- which models are the canonical `fast`, `heavy`, and `visual` roles
- what kind of global plane produced this feed
- what kind of local execution plane is expected to consume it
- which provider and routing metadata are stable enough to trust

## v5 top-level shape

```json
{
  "schema_version": "5.0.0",
  "manifest_type": "aichain.catalog",
  "system_status": "OPERATIONAL",
  "planes": {
    "global": {
      "kind": "catalog",
      "feed_url": "https://filokloi.github.io/AIchain/manifest.json"
    },
    "local": {
      "kind": "execution",
      "skill": "openclaw",
      "sidecar": "aichaind"
    }
  },
  "roles": {
    "fast": { "model": "google/gemini-2.5-flash" },
    "heavy": { "model": "openai/o3-pro" },
    "visual": { "model": "openai/gpt-4o" }
  },
  "capabilities": {
    "supports_a2a": false,
    "supports_loss_aware_compression": false
  },
  "routing_hierarchy": []
}
```

## Minimum validation rules

- `schema_version` must exist in native v5 mode
- `manifest_type` must equal `aichain.catalog`
- `planes.global.kind` must be `catalog`
- `planes.local.kind` must be `execution`
- `routing_hierarchy` must be non-empty
- every routing entry must define:
  - `model`
  - `tier`
  - `metrics.intelligence`
  - `metrics.speed`
  - `metrics.stability`
  - `metrics.cost`

## Legacy v4 compatibility

Until the live site publishes native v5, `aichaind` accepts the existing feed and derives roles from:

- first free/effective-free ranked model for `fast`
- `heavy_hitter.model` or max-intelligence ranked model for `heavy`
- first vision-capable ranked model for `visual`

This is transitional behavior, not the target design.

## Why this matters

Without this contract:

- the site is only a dashboard
- the sidecar guesses roles heuristically
- version drift becomes invisible
- the global plane and local plane evolve independently

With this contract:

- the site becomes the explicit global catalog plane
- the sidecar consumes a validated manifest
- role selection stops depending on hidden heuristics
- migration from v4 to v5 can happen without a hard break

## Immediate implementation status

Implemented now:

- validator in `aichaind`
- compatibility support for legacy v4 and native v5
- contract metadata attached to fetched routing tables
- role sourcing from contract metadata when available

Next step after this document:

- publish a native v5 manifest from the site/workflow
- make `aichaind` fail closed on incompatible manifests
- then move to canonical session lifecycle and hard policy enforcement
