# Routing Policy: How Layer 2 Merges the Two Truths

This is the contract for `aichaind`'s decision loop. It is deterministic: same inputs → same chain. No LLM is consulted to route (an optional local classifier may pre-tag the request type; see step 1).

## Decision pipeline (per request)

**0. Inputs.** `catalog_manifest.json` (global truth), `user_truth.json` (layer 3), live local state (quota counters, spend counters, probe latencies, provider health).

**1. Classify the request → role.** Map the request to a catalog role (`fast` / `heavy` / `visual`). Default heuristics: attachment with image → `visual`; prompt length / harness hint / explicit tag → `heavy`; otherwise `fast`. Optionally a small local embedding classifier (via any local endpoint) refines this — it runs at zero marginal cost and its failure falls back to heuristics.

**2. Apply privacy boundary (hard filter).** Evaluate `privacy.rules` against request tags/keywords. The result restricts the candidate set (e.g. `local_only` → only `assets.local_models`). This happens before any scoring so no score can override a boundary.

**3. Compute effective cost per candidate.** For each catalog model reachable with the user's assets:

```
effective_cost(model, path) =
  local model                → 0
  free quota, remaining > 0  → 0            (and reserve quota)
  prepaid credit remaining   → catalog price, paid from credit first
  flat subscription w/ API   → 0 marginal   (up to declared fair-use; then catalog price)
  pay-as-you-go API key      → catalog blended price × estimated tokens
  unreachable (no key/path)  → excluded
```

The same model can appear as several *paths* (e.g. `:free` variant with quota left AND a paid key). Paths are scored independently.

**4. Score.** Take the model's catalog `role_score` for the classified role, then re-blend with the user's weights:

```
final_score(path) = Σ weight_u(d) × dimension(d)
```

where `weight_u` comes from `profile.mode` (preset) or `profile.custom_weights`, and the `cost` dimension is recomputed from `effective_cost` (not catalog list price — this is the whole point). Local probe latency overrides catalog speed where measured. Candidates below `profile.min_intelligence` are dropped.

**5. Budget guard.** If spend ≥ `soft_threshold × limit`, multiply the cost weight ×2 and re-rank. If a limit is hit and `hard_stop` is true, drop all paths with `effective_cost > 0`.

**6. Build the chain.** Sort surviving paths by `final_score` descending; take the top `max_fallback_depth`. If `sticky_session` is true and the conversation's current model is in the set, promote it to position 1.

**7. Execute with failover.** Try position 1; on rate-limit / 5xx / timeout, advance. Record the outcome in local telemetry (feeds step 3's quota counters and step 4's latency overrides). Never retry a path that failed with an auth error within the same session.

**8. Report.** Attach routing metadata to the response (model used, path type, effective cost incurred, chain attempted) so the user always sees *why* — transparency is a feature, not a log line.

## Worked example

User: `mode=economy`, `min_intelligence=55`, Gemini free quota (250 req/day, 180 left), Groq free quota exhausted, Anthropic pay-as-you-go key, Ollama with a local 8B model, daily limit $0.50 with $0.11 spent.

Request: 2,000-token coding question, no privacy tags → role `heavy`.

- Local 8B: cost 0, but intelligence 41 < 55 → dropped.
- Gemini flash-class via free quota: cost 0, intelligence 62 → strong candidate.
- Gemini pro-class via free quota: cost 0, intelligence 74 → top candidate.
- Claude via paid key: intelligence 88, effective cost ≈ $0.06 → in economy mode cost weight dominates → ranked below free paths, kept as fallback.

Chain: `gemini-pro(free) → gemini-flash(free) → claude(paid) → local-8b(last resort, floor waived only at final depth if `allow_floor_break_at_tail` is set)`. Quota counter decrements on success; the $0.06 path is only touched if both free paths fail.

## Minimal example `user_truth.json`

```json
{
  "version": 1,
  "profile": { "mode": "economy", "min_intelligence": 55 },
  "budget": { "currency": "USD", "daily_limit": 0.5, "monthly_limit": 10, "hard_stop": true },
  "assets": {
    "api_keys": [ { "provider": "anthropic", "key_ref": "ANTHROPIC_API_KEY" } ],
    "free_quotas": [ { "provider": "google", "quota_unit": "requests", "quota_per_day": 250, "reset_time_utc": "07:00" } ],
    "local_models": [ { "endpoint": "http://localhost:11434/v1", "model_id": "llama3.1:8b" } ]
  },
  "privacy": {
    "default_boundary": "any",
    "rules": [ { "match": { "tags": ["work"] }, "boundary": "local_only" } ]
  }
}
```

## Harness integration (agnostic by construction)

`aichaind` exposes `http://localhost:PORT/v1` as an OpenAI-compatible endpoint. Any harness — OpenClaw, Cline, LM Studio clients, custom agents — integrates by setting its base URL to the sidecar. Harnesses can pass hints via headers (`X-AIChain-Tag: work`, `X-AIChain-Role: heavy`) but nothing requires them to: the pipeline above works with a bare request.
