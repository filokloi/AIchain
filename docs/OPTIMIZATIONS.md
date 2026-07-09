# Applicable Optimizations (researched July 2026)

Honest applicability assessment: AIchain is a *router*, not an inference engine. Techniques inside a model's attention mechanism apply only to the **local plane** (models the user self-hosts) or indirectly (cheaper API prices). Techniques on the *prompt/transcript* level apply directly to `aichaind`.

## 1. DeepSeek's public efficiency work → catalog + local plane

- DeepSeek Sparse Attention (DSA) debuted publicly with V3.2-Exp: a lightning indexer selects top-k relevant tokens so attention runs over a sparse subset instead of the full context, cutting per-layer cost from O(L²) to O(Lk), with tech report and weights published openly.
- V4 (April 2026) ships a hybrid Compressed Sparse Attention + Heavily Compressed Attention architecture that cuts inference FLOPs to ~27% of V3.2's at 1M-token context and KV-cache memory to ~10%; weights are MIT-licensed with local-deployment instructions in the repo.

**Application to AIchain:**
- **Catalog:** these architectures translate into aggressive API pricing and 1M context at baseline tier — exactly what per-task value density rewards. Action: ensure the catalog tracks *effective long-context price* (price × context tier), not just headline $/M.
- **Local plane:** MIT-licensed V4 weights are far beyond consumer hardware (1.6T total params), but distilled/smaller DSA-based releases are prime candidates for the self-host index — sparse attention matters most on modest VRAM. Action: add an `attention_efficiency` tag to self-host catalog entries.
- **⚠ Operational (do now):** legacy `deepseek-chat` and `deepseek-reasoner` API aliases are being retired on July 24, 2026 — that's in two weeks. If the catalog or any routing table references those aliases, migrate them or those chain links will silently die. This is a perfect test case for the catalog's `stale`/availability machinery.

## 2. Prompt compression → directly applicable in aichaind (transcript layer)

- **LLMLingua-2** (Microsoft, MIT license): task-agnostic prompt compression formulated as token classification with a small encoder, 3–6× faster than LLMLingua, with the family claiming up to 20× compression with minimal performance loss.

**Application:** this is the strongest immediate win for the router. Three insertion points, all optional and off by default:
1. **Handoff summaries** (DYNAMIC_AUTO §3.3): compress old transcript segments when switching models with a long history — compression instead of, or before, summarization preserves more verbatim detail.
2. **Economy mode long chats:** when a conversation's transcript exceeds a threshold and the target path is paid per token, compress turns older than the last k at a conservative ratio (2–3×), keeping the recent window verbatim. Direct token-cost savings that compound with everything else.
3. **Fitting a better model:** when the top-scoring candidate's context window is slightly too small for the transcript, compression can make the better model reachable instead of falling to a worse one with a bigger window.

Guardrails: never compress the system prompt, tool schemas, code blocks, or the last k user/assistant turns; log compression ratio in routing metadata; a per-request `@aichain nocompress` override. Runs locally (small encoder model) — zero marginal cost, aligned with the free-first philosophy.

## 3. KV-cache compression → local plane only

Research is moving fast (SnapKV, KVQuant/KIVI quantization, 2026 work like CompressKV reporting ~97% of full-cache quality at 3% of KV storage on long-context QA). For AIchain this is **not router logic** — it's runtime configuration for the user's local models:
- llama.cpp / Ollama: quantized KV cache flags (e.g. q8_0/q4 KV) to fit longer contexts in the same VRAM.
- vLLM/SGLang class runtimes: paged attention and prefix caching by default.

**Application:** the self-host guide (`#/selfhost`) should include a "context vs VRAM" section recommending KV-cache quantization settings per hardware class, and `assets.local_models` may optionally record `effective_context` (what actually fits) instead of the model's theoretical maximum — the router should route against reality, not the spec sheet.

## 4. Provider prompt caching → router-awareness, big money

Major providers discount cached prefix tokens heavily. The router influences this more than any single optimization:
- **Sticky sessions** (already specified) are the mechanism: every model switch discards the provider-side prefix cache and re-bills the full transcript at full price. Action: add cache value into switch hysteresis — the score advantage required to justify a switch should grow with transcript length (`switch_threshold = 15 + f(transcript_tokens)`).
- **Effective cost must be cache-aware:** for the sticky path, price the request as `cached_prefix × cached_rate + new_tokens × full_rate`; for a switch candidate, the whole transcript at full rate. This makes the economics of staying vs switching explicit instead of accidental.

## 5. Priority order (impact × effort, economy-first)

| # | Item | Layer | Effort | Impact |
|---|---|---|---|---|
| 1 | DeepSeek endpoint-alias migration before Jul 24 | catalog | trivial | prevents breakage |
| 2 | Cache-aware effective cost + length-scaled switch hysteresis | aichaind | small | large, immediate savings |
| 3 | LLMLingua-2 compression at the three insertion points | aichaind | medium | large for long chats |
| 4 | KV-quantization guidance + `effective_context` in self-host | docs/schema | small | reliability on modest hardware |
| 5 | `attention_efficiency` / long-context-price fields in catalog | catalog | small | better rankings |
