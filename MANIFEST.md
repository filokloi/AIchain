# The AIchain Manifest

**Routers optimize for providers. AIchain optimizes for you.**

---

Every AI router on the market answers the question: *"Which model should serve this request?"*
Almost none of them answer the question that actually matters to a person paying out of pocket:

> *"Given everything I already have — my subscriptions, my free tiers, my local hardware, my promotional credits — what is the smartest way to answer this request at the lowest real cost to me?"*

Commercial routers can't answer it. Their business model depends on you paying per token. Maximizing your free options is, structurally, against their interest.

AIchain exists to answer exactly that question. Openly, locally, and for free.

## The three layers

**1 — Global truth.** A public catalog of every reachable model: real intelligence scores, real prices, real speed, real free paths. Rebuilt every 12 hours by an open pipeline, published as a static manifest anyone can read, audit, or fork. No account. No API key. No server that can be shut down — it's a JSON file on GitHub Pages. Every number is traceable to its source ([methodology](./docs/METHODOLOGY.md)).

**2 — The router.** A local sidecar (`aichaind`) that merges global truth with *your* truth and makes the routing decision on your machine. It doesn't need a frontier model to route — a deterministic policy over honest data beats an expensive orchestrator you have to pay to consult. Cheap, fast, inspectable, offline-capable.

**3 — Your truth.** A passive local config: which subscriptions you hold, which free quotas you haven't burned today, what your budget ceiling is, which requests must never leave your machine, whether you want maximum intelligence or maximum economy right now. This layer never leaves your computer. It is the one input no cloud router will ever have — and it's the one that changes the answer.

## Principles

1. **The user's interest is the objective function.** Not provider revenue, not our revenue — there is none.
2. **Facts and preferences never mix.** The global catalog contains only verifiable facts. Your preferences stay local. The router is where they meet.
3. **Local-first.** Routing decisions, keys, history, and your configuration live on your machine. The cloud part of AIchain is a static file.
4. **Graceful degradation.** Providers fail, quotas run out, feeds go stale. The chain continues. An interrupted conversation is a bug, not an inconvenience.
5. **Legitimate free, not gray free.** Every free path we list is one the provider actually offers. Economy through information, not through terms-of-service violations.
6. **Harness-agnostic.** One OpenAI-compatible endpoint on localhost. Point any client, agent framework, or coding harness at it and the whole chain works. Integration is a base URL, not a plugin.
7. **Boring where it counts.** Deterministic scoring, versioned methodology, contract tests. The exciting part should be what you build on top — not debugging your router.

## What AIchain is not

- Not a cloud gateway. Nothing proxies through our servers; there are no servers.
- Not a benchmark authority. We aggregate public evidence and show our work.
- Not a subscription. If AIchain ever costs money, this manifest has failed.

## Why now

Model prices span three orders of magnitude for overlapping capability. Free tiers, open weights, and promotional access have never been richer — and never harder to track by hand. The gap between what people *pay* for AI and what they *could* pay, with perfect information, has never been wider.

AIchain is an attempt to close that gap with a JSON file, a small daemon, and a refusal to have a business model.

---

*Maximum intelligence, maximum speed, maximum stability, minimum cost — in that order of appearance, and in exactly the balance you choose.*
