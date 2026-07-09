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

**Freshness rule:** every raw input carries a `fetched_at` timestamp. Inputs older than 7 days lower the model's `confidence` field; inputs older than 30 days mark the affected score as `stale` and it is excluded from role rankings until refreshed.

---

## 3. Normalization

All raw values are normalized to a 0–100 scale per dimension across the current catalog snapshot:

- **Intelligence:** min–max normalization of the benchmark aggregate over all catalog models. Where multiple benchmark sources exist, the median of available sources is used (median is robust to a single outlier source).
- **Speed:** log-scale min–max of measured tokens/s (log scale because the perceptual difference between 20 and 40 tok/s matters more than between 200 and 220).
- **Cost:** inverse log-scale of blended price per million tokens, computed as `0.3 × input_price + 0.7 × output_price` (output-weighted, reflecting typical chat/agent usage). Models with a genuinely free path receive `cost_score = 100` on that path, listed separately from their paid score.
- **Context:** log2 of context window, min–max normalized.
- **Stability:** rolling 30-day availability estimate, mapped linearly (99.9%+ → 100, <95% → 0).

## 4. Role scores

The v5 contract assigns each model role-specific composite scores. Weights per role:

| Role | Intelligence | Speed | Cost | Context | Stability |
|---|---|---|---|---|---|
| `heavy` (complex reasoning) | 0.50 | 0.05 | 0.15 | 0.20 | 0.10 |
| `fast` (interactive / everyday) | 0.25 | 0.35 | 0.20 | 0.05 | 0.15 |
| `visual` (image/video input) | 0.45 | 0.15 | 0.15 | 0.10 | 0.15 |

`role_score = Σ (weight_i × normalized_dimension_i)`, rounded to one decimal. Models missing a required dimension for a role (e.g. no vision support for `visual`) are excluded from that role rather than scored at zero.

> Weights are a policy decision, not a fact. They are versioned: any change to this table increments the methodology version and is noted in the changelog below. Users who disagree with the weights can re-run the arbitrator with their own — the local plane exists precisely so global weights are only a default.

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
| 2026-07 | 1.0 | Initial public methodology: sources, normalization, role weights, free-path criteria. |
