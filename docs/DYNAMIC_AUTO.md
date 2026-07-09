# Dynamic Auto Mode (`aichain/auto`)

Extension of [ROUTING_POLICY.md](./ROUTING_POLICY.md). Replaces the fixed presets with a continuous, per-request decision driven by two sliders and a task-type classification — with an optional ensemble tier for "superior answer regardless of cost".

## 1. The two sliders

Exposed in the companion UI and adjustable inline (`@aichain iq=80 cost=30`). Stored in `profile`:

```json
"profile": {
  "mode": "auto",
  "sliders": { "intelligence": 70, "cost_sensitivity": 40 },
  "ensemble": "auto"            // "off" | "auto" | "always"
}
```

- **intelligence (0–100):** how much capability headroom the user wants above the task's estimated difficulty. 0 = "cheapest thing that can do it", 100 = "best available".
- **cost_sensitivity (0–100):** how strongly effective cost penalizes a candidate. 100 = free paths practically mandatory; 0 = cost ignored (within budget guard, which still applies — sliders never override `budget.hard_stop`).

Sliders map to weights continuously instead of preset tables:

```
w_intelligence = 0.20 + 0.55 × (intelligence / 100)
w_cost         = 0.05 + 0.50 × (cost_sensitivity / 100)
w_speed, w_context, w_stability = remainder, split by task-type profile (below)
(all weights renormalized to sum 1.0)
```

## 2. Task-type taxonomy

Step 1 of the pipeline (classification) is upgraded from 3 roles to a task taxonomy. The catalog already carries per-task scores; the manifest formalizes these task dimensions:

| task_type | Signal examples | Dominant catalog dimensions | Difficulty prior |
|---|---|---|---|
| `chat_casual` | short prompt, no artifacts | speed, cost | low |
| `creative_writing` | "write a story/poem/scene", style cues | creative-writing score, long-output quality | medium |
| `knowledge` | factual/encyclopedic question | factual accuracy, knowledge benchmarks | low–medium |
| `legal_formal` | statutes, contracts, formal register | reasoning + factual, low-temperature reliability | high |
| `coding` | code blocks, file refs, stack traces | coding benchmarks, tool use | medium–high |
| `agentic_tool_use` | tool schema present in request | tool-calling reliability, instruction following | high |
| `vision_ocr` | image attachment + extract/read intent | OCR/vision score | medium |
| `vision_reasoning` | image + analytical question | vision + reasoning | high |
| `math_logic` | equations, proofs, puzzles | reasoning benchmarks | high |
| `translation_language` | cross-language intent | multilingual scores | low–medium |

**Classifier:** three tiers, all local and free:
1. **Deterministic signals** (attachments, tool schemas, code fences, length) — decide instantly when unambiguous.
2. **Local embedding classifier** — few-shot centroid match against the taxonomy; runs on any local model in ~ms.
3. **Fallback:** harness hint header or previous turn's type.

The classification produces `(task_type, difficulty_estimate 0–100)`. Difficulty adjusts the intelligence floor: `effective_floor = max(profile.min_intelligence, difficulty_estimate × 0.8)`. A hard question raises the bar even at low slider settings — this is what makes it *dynamic* rather than static preference.

**Candidate scoring** then uses the model's per-task catalog score (not the generic role score) as the intelligence dimension. A model can be #1 for coding and #7 for creative writing; the router sees that.

## 2b. Personal opportunity matrix & value density

Per-task scores are only half of the decision. The other half is that **the same catalog produces a different ranking for every user**, because availability is personal. The router materializes this as the *personal opportunity matrix*:

```
POM[user] : (model, path, task_type) → (task_score, effective_cost, availability_state)
```

built per request by crossing:

- **Global truth:** per-task scores and list prices from the catalog.
- **Personal access:** which paths exist *for this user right now* — active promotions, unburned daily quotas, prepaid credits (and their expiry), flat subscriptions with API access, local hardware. A path another user doesn't have simply doesn't exist in their matrix; a path whose quota resets in 20 minutes is `pending`, not `gone`.
- **Temporal state:** quota counters, credit balances, budget headroom, time-to-reset. Effective cost is a function of *time*, not a constant — the matrix is recomputed, never cached across requests.

The ranking metric that correlates intelligence with economics is **value density**:

```
value_density(path, task) = task_score(model, task)^γ / (effective_cost(path) + ε)
```

- `γ` (quality exponent) comes from the intelligence slider: low slider → γ≈1 (linear, cheap-and-good-enough wins); high slider → γ≈3 (quality dominates, cost matters only as tiebreaker).
- `ε` is a small constant so free paths (cost 0) don't divide by zero — among free paths, ranking collapses to pure task_score, which is exactly right: *when marginal cost is zero, always take the smartest thing you can reach.*
- Expiring assets get a use-it-or-lose-it bonus: prepaid credit or promo expiring within `T` days multiplies its path's value density by `1 + urgency`, so the router spends dying value before touching durable value.

Consequences the fixed-weight blend can't express, and this metric captures naturally:

1. A mid-tier model on an active promotion outranks a frontier model on a paid key for medium-difficulty tasks — and flips back the day the promotion ends.
2. Two users with identical sliders and the identical question get different chains, legitimately.
3. The router *plans across the day*: with 180 free frontier-requests remaining and historical usage suggesting ~60 more requests today, it spends free quota liberally; at 5 remaining it reserves them for high-difficulty tasks and routes casual chat to local/cheap paths (`quota_pacing`, on by default in `auto`).

Final ranking in `auto` mode = value density, subject to the hard filters that always run first (privacy boundary, capability guard, effective intelligence floor, budget guard). The weight-blend of §1 remains as the tiebreaker among near-equal value densities.

## 3. Context preservation across model switches

Provider APIs are stateless — full history is sent on every request — so switching models mid-conversation loses no content by construction. What must be engineered:

1. **Canonical transcript.** `aichaind` owns the conversation state in a provider-neutral format (messages, tool calls, attachments) and renders it per provider's dialect at send time. The harness talks to the sidecar; the sidecar talks to whoever wins the routing.
2. **Switch hysteresis.** Within a conversation, re-route only when: task_type changes (e.g. chat → coding), the sticky model fails, quota for the sticky path is exhausted, or the new candidate beats the sticky model's score by > 15 points. Otherwise stay. This preserves prompt-cache discounts and stylistic coherence.
3. **Handoff summary (optional, long contexts).** When switching after N tokens of history, prepend a router-generated one-paragraph state summary (produced by a free/local model) so the incoming model orients faster; the full transcript still follows. Never summarize *instead of* the transcript unless the target's context window forces truncation — and then truncate oldest-first with the summary as replacement.
4. **Capability guard.** A switch target must support every feature present in the live context (tools, images). If the transcript contains images and the best text model doesn't accept them, images older than the last k turns are replaced by their cached OCR/description (produced when first seen) — degradation is explicit and logged in the routing metadata.

## 4. Ensemble tier — "superior answer regardless of cost"

Triggered when `ensemble: "always"`, when the user asks inline (`@aichain best`), or in `"auto"` when *all* of: difficulty ≥ 80, intelligence slider ≥ 80, cost_sensitivity ≤ 30, and budget headroom ≥ estimated ensemble cost. The router always prints the cost estimate before running an auto-triggered ensemble and requires one confirmation unless `ensemble.confirm=false`.

Three patterns, chosen by task_type:

- **Best-of-N + judge** (knowledge, legal_formal, creative_writing): send the same request to the top 2–3 *diverse* models (diversity = different provider families, to decorrelate errors); a judge model (can be one of the N, or a cheap strong model) selects or merges. Cost ≈ N+1 requests.
- **Decompose + specialists** (coding, agentic): a planner splits the task; per-subtask routing sends each piece to the model with the best per-task score (e.g. architecture → heavy reasoner, boilerplate → fast cheap coder); the planner integrates. This is where per-task catalog scores pay off most.
- **Generate + verify loop** (math_logic, OCR-critical): one model produces, a different-family model verifies; disagreement triggers one repair round, then surfaces both answers with the discrepancy flagged rather than silently picking.

Open-source prior art to reuse rather than reinvent (all permissively licensed ecosystems): RouteLLM (learned strong/weak routing), LiteLLM (provider abstraction + fallback plumbing), and the published multi-model inference-time search work (e.g. Sakana's AB-MCTS line) as the reference for when ensembles actually beat single frontier models — empirically: high-difficulty, verifiable tasks yes; casual chat no. `auto` encodes exactly that finding.

**Reporting:** an ensemble response's routing metadata lists every model consulted, each path's effective cost, and which pattern ran — the user sees precisely what "superior" cost.

## 5. Resulting virtual model lineup

| Virtual model | Behavior |
|---|---|
| `aichain/auto` | Full dynamic pipeline above (sliders + taxonomy + auto-ensemble) |
| `aichain/economy` | auto with sliders locked (iq=40, cost=90), ensemble off |
| `aichain/power` | auto with sliders locked (iq=95, cost=10), ensemble auto |
| `aichain/<model-id>` | Pin-through: user picked a concrete model; router still applies privacy guard and *does* fail over within the same model's alternative paths/providers |
| `aichain/lock:<model-id>` | **Hard lock**: this model, at any cost, no substitution ever |

**Lock semantics** (`lock:` prefix, inline `@aichain lock deepseek-v4-pro`, or the manual-lock toggle in the companion UI):

- No failover to a different model. If every path to the locked model is down/rate-limited, the router returns an explicit error with retry-after info — it never silently substitutes.
- Budget guard downgrades from *hard stop* to *warning*: the user said "at any cost", so the router warns ("this request will exceed your daily limit by $X") and proceeds on confirmation. `budget.hard_stop` can still be configured to override even lock — user safety default is configurable, silence is not.
- Privacy boundary is the one filter lock cannot override: a `local_only` request to a cloud-locked model returns a refusal with the reason, because privacy rules exist precisely to survive moments of convenience.
- Provider-level failover *within* the locked model (same model via a different provider/path) is allowed by default (`lock_scope: "model"`), or disabled with `lock_scope: "path"` for strict single-endpoint routing.
- Lock persists for the session (or until unlocked) and is always visible in routing metadata, so the user never wonders why economy logic stopped applying.

The last two rows answer the "DeepSeek V4 Pro" case: pin keeps safety nets, lock removes them on request — while `auto` hands the whole choice to the pipeline. All through the same single endpoint.
