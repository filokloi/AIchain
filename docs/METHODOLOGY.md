# AIchain Catalog Methodology

**Version:** 1.0 · **Applies to:** `catalog_manifest.json` (v5 contract) · **Refresh cadence:** every 12 hours via GitHub Actions

This document explains exactly how every score in the AIchain catalog is produced, from which sources, with which formula, so that anyone can verify or reproduce the results. If a number in the catalog cannot be traced back through this document, that is a bug — [open an issue](https://github.com/filokloi/AIchain/issues).

---

## 1. Principles

1. **Deterministic over generative.** Scores are computed by a pipeline (`tools/arbitrator.py`), not written by an LLM. Where an LLM assists in collecting unstructured information (e.g. promotion announcements), its output is treated as a *candidate* that must pass validation before entering the catalog.
2. **Traceable.** Every model entry records the source and timestamp of each raw input.
3. **Reproducible.** `python tools/arbitrator.py && python tools/build_catalog_manifest.py` regenerates the catalog from the same inputs.
4. **Separation of fact and preference.** The catalog contains only *global truth* (facts equal for all users). User-specific weighting — subscriptions, budget, privacy, free quotas — is applied locally by `aichaind` and never enters the published manifest.

---

## 2. Data sources

| Dimension | Primary source | Secondary / cross-check | Update path |
|---|---|---|---|
| Pricing ($/M input, $/M output) | Provider official pricing pages / APIs | OpenRouter models feed | Automated fetch |
| Context window | Provider documentation | OpenRouter metadata | Automated fetch |
| Intelligence / capability | Public benchmark aggregates (e.g. Artificial Analysis intelligence index, LMArena Elo) | Provider-published benchmarks (flagged as self-reported) | Automated fetch |
| Speed (tokens/s, TTFT) | Public throughput measurements | Own burn-in probes (`burn_in.py`) where a key is available | Automated + probe |
| Availability / uptime | Provider status pages, OpenRouter provider health | Failover telemetry from `aichaind` (opt-in, local only) | Automated fetch |
| Free access paths | Provider announcements, OpenRouter `:free` variants | Manual review before publish | Semi-automated |
| License / openness | Model cards, provider docs | — | Manual on model addition |

**Freshness:** the catalog is regenerated every 12 hours from live fetches, so published values are at most one cycle old. A per-input `fetched_at`/staleness downgrade rule is **planned, not yet implemented** (see §9); today a source that fails to fetch simply keeps the pipeline's mid-range defaults and the failure is recorded in `source_health`.

---

## 3. Normalization

All dimensions are expressed on a 0–100 scale in `normalized_metrics`. Exact rules, as implemented in `tools/catalog_pipeline/rank/scoring.py`:

- **Intelligence:** the **median** of available benchmark sources (curated benchmark map, Artificial Analysis quality, LMArena Elo normalized to 0–100) — median is robust to a single outlier source. `source_attribution.intelligence` labels the highest-priority source present (benchmark → Artificial Analysis → LMArena → helper → heuristic). No source at all → heuristic estimate; missing everywhere → default **70**. Models on an active verified promotion ("promo kings") receive **+8** (capped at 99), recorded as `helper_metadata.promo_boost`.
- **Speed:** `35 + 65 × (tokens_per_second / max_tokens_per_second_in_snapshot)`, clamped to 0–100. Source: Artificial Analysis speed, else the OpenRouter speed hint. Unknown speed → default **62**.
- **Context:** log-ratio normalization with a 4096-token floor: `100 × (ln(1+ctx) − ln(1+4096)) / (ln(1+max_ctx) − ln(1+4096))`, where `max_ctx` is the largest context window in the snapshot.
- **Stability:** provider stability hint (0–100) as merged from sources; unknown → default **72**.
- **Availability:** provider availability hint; unknown → default **75**.
- **Task fit:** `coverage_score` = arithmetic mean of the model's `quality_by_task` scores across the 8 task dimensions; unknown → default **60**.
- **Taxonomy scores:** each entry additionally publishes `task_metadata.taxonomy_scores` — the 10-type routing taxonomy (DYNAMIC_AUTO §2) derived deterministically from the 8 dimensions (e.g. `math_logic` = reasoning; `vision_ocr` = 0.8·vision + 0.2·extraction; `legal_formal` = 0.7·reasoning + 0.3·extraction; full table in `tools/catalog_pipeline/rank/tasks.py`). The local router matches its classifier output against these directly.
- **Cost:** raw cost is the plain average of list prices, `(input_price + output_price) / 2` per token. Cost efficiency is inverse log-scale against the most expensive model in the snapshot: `100 − 100 × log10(1 + 9 × cost / max_cost)`, clamped to 0–100. Genuinely free (`cost ≤ 0`) → **100**.

Defaults are deliberately mid-range so that a model missing a signal is neither buried nor promoted by the gap; every default is visible in `raw_metrics` as an absent source.

## 4. Value score, tiers, and rank

Every model gets one composite **value score** (`value_score`, also echoed in the manifest's `scoring` block):

```
Score = 0.30·Intelligence + 0.14·Speed + 0.16·Stability + 0.18·CostEfficiency
      + 0.10·Availability + 0.06·Context + 0.06·TaskFit
```

Weights live in `tools/catalog_pipeline/constants.py` (`SCORING_WEIGHTS`, version `2026.03-control-plane-v1`) and each entry's `score_breakdown` shows the per-dimension contribution (`normalized × weight`), so the published score is reproducible by summation.

**Tiers** partition the ranking before scores are compared: `OAUTH_BRIDGE` (subscription-bridged access) → `FREE_FRONTIER` (zero list cost) → `HEAVY_HITTER` (paid). Rank is assigned by `(tier, −value_score, −intelligence, model_id)` — a free model always outranks a paid one *within the published hierarchy ordering*; the sidecar re-ranks per user anyway.

Each entry also carries `task_label` (its strongest task dimension), and `geopolitical_risk` (LOW/MEDIUM/HIGH by provider origin — informational only, never a scoring input).

**Role derivation** (`tools/build_catalog_manifest.py::derive_roles`) selects the v5 contract's `fast`/`heavy`/`visual` defaults heuristically:

- `heavy` = the model with the highest normalized intelligence in the snapshot.
- `fast` = among zero-marginal-cost candidates (free tier or OAuth bridge), the max of `4×speed + 2×stability + intelligence` plus token bonuses (e.g. "flash", "mini") and tier bonuses.
- `visual` = among models whose id signals vision ("gpt-4o", "vision", "gemini", "-vl"), the max of `3×intelligence + 2×stability + speed` plus token bonuses; falls back to `fast`.

> Weights and role heuristics are a policy decision, not a fact. Any change to `SCORING_WEIGHTS` or the role formulas increments the methodology version and is noted in the changelog. Users who disagree can re-run the arbitrator with their own weights — the local plane exists precisely so global weights are only a default.

## 5. Free-path scoring

A "free path" entry must satisfy all of:

1. Zero marginal cost to the end user (free tier, promotional credit, `:free` routing variant, or local open-weight execution).
2. Officially offered by the provider — access methods that violate a provider's terms of service are **not listed**.
3. Verified reachable within the last catalog cycle.

Each free path records: quota (requests/day or tokens/day where published), signup friction (none / account / card-on-file), and data-use caveats (e.g. "prompts may be used for training").

## 6. What the catalog does NOT claim

- It does not claim benchmark scores are a complete measure of quality; they are the best available public proxy.
- It does not measure your latency — network position matters; the local sidecar's own probes always override catalog speed data for routing decisions.
- It does not rank models it cannot source at least pricing + context + one capability signal for.

## 7. Reproducing the catalog

```bash
git clone https://github.com/filokloi/AIchain && cd AIchain
pip install -r requirements.txt
python tools/arbitrator.py               # fetch + score
python tools/build_catalog_manifest.py   # emit catalog_manifest.json
python -m pytest tests -q                # contract validation
```

## 8. Changelog

| Date | Methodology version | Change |
|---|---|---|
| 2026-07 | 1.0 | Initial public methodology draft: sources, proposed role weights, free-path criteria. |
| 2026-07 | 1.1 | Aligned with the shipped pipeline (`rank/scoring.py`): documented the actual 7-dimension value score, per-dimension normalization with defaults, promo boost, tiers/rank ordering, and heuristic role derivation. Moved unimplemented v1.0 ideas to §9. |
| 2026-07 | 1.2 | Intelligence merge switched from 3/2/1 weighted mean to **median of available sources** (implements the v1.0 robustness intent); moved out of §9 Planned. |
| 2026-07 | 1.3 | Added `taxonomy_scores`: per-entry scores for the 10-type routing taxonomy, derived deterministically from the 8 catalog dimensions. |

## 9. Planned (documented but not yet implemented)

These v1.0 proposals remain goals; the pipeline does not do them yet. Until a row moves to the changelog as implemented, no published number depends on it.

- **Output-weighted cost blend** `0.3×input + 0.7×output` (today: plain average of input and output price).
- **Per-input freshness rule** — `fetched_at` older than 7 days lowers `confidence`, older than 30 days marks the score `stale` and excludes it from role rankings.
- **Measured stability** — rolling 30-day availability estimate mapped 99.9%→100, <95%→0 (today: source-provided hints with mid-range defaults).
- **Per-role composite scores** with role-specific weight tables (today: single global value score + heuristic role picks).
