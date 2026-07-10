# Show HN draft (roadmap #10)

> Status: DRAFT — objaviti tek posle ručne provere linkova i live dashboarda.
> Ciljna mesta: Show HN, r/LocalLLaMA. Ton: tehnički, bez marketinga, prva
> osoba, konkretan problem → konkretno rešenje.

---

**Title:** Show HN: AIchain – a local, free router that optimizes LLM cost for you, not for providers

**Body:**

Routers optimize for providers. I wanted one that optimizes for me.

Every commercial router answers "which model should serve this request?"
None of them answer the question I actually have as someone paying out of
pocket: *given everything I already have — subscriptions, free tiers, local
models, promo credits — what's the smartest way to answer this request at
the lowest real cost to me?* Their business model depends on per-token
billing, so maximizing my free options is structurally against their
interest.

AIchain is three layers:

1. **Global truth** — a public catalog of ~336 models (intelligence, price,
   speed, free paths), rebuilt every 12h by an open pipeline, published as a
   static JSON on GitHub Pages. No account, no server. Every number is
   traceable: the methodology doc states every formula and contract tests
   bind the doc to the code — if they diverge, CI fails.
2. **The router** — `aichaind`, a local OpenAI-compatible sidecar. Fully
   deterministic: a 10-type task classifier (regex signals + local TF-IDF
   centroids, no LLM calls), then value-density ranking
   `task_score^γ / effective_cost` over *paths*, not models — the same model
   via free quota, prepaid credit, subscription or pay-as-you-go is four
   different paths with four different real costs. Cache-aware: switching
   models mid-conversation re-bills your whole transcript at full price
   (DeepSeek bills cache hits at 2% of miss price — sticky sessions are real
   money), so switch hysteresis grows with transcript length.
3. **Your truth** — a local JSON: your subscriptions, unburned quotas,
   budget ceiling, privacy rules (e.g. "anything mentioning my clients never
   leaves this machine" → hard filter, overrides everything including manual
   locks). This file never leaves your computer, and it's the input no cloud
   router will ever have.

Nice properties that fell out of the design: free quotas get paced through
the day (an easy question won't burn your last free calls — those are saved
for hard ones); expiring credits get a use-it-or-lose-it ranking bonus;
harness integration is just a base URL (`model: "aichain/auto"`, or
`aichain/economy`, `aichain/power`, `aichain/lock:<id>`).

Everything is MIT. The catalog is a static file you can fork. There's no
revenue model on purpose — if it ever costs money, the manifest says the
project has failed.

Live dashboard: https://filokloi.github.io/AIchain/
Repo: https://github.com/filokloi/AIchain
Manifest (the pitch, 5 min read): MANIFEST.md
Methodology (every number derivable): docs/METHODOLOGY.md

---

**Checklist pre objave:**
- [ ] Live dashboard radi i pokazuje svež manifest (< 12h)
- [ ] README About polje + topics podešeni na GitHubu
- [ ] `pip install -r requirements.txt && python -m pytest tests -q` prolazi na čistom klonu
- [ ] free stranica (ai-chain repo) linkovana i funkcionalna
- [ ] Pripremljeni odgovori na očekivana pitanja: "zašto ne RouterLLM/learned router?"
      (determinizam + nema preference-dataseta + reproducibilnost),
      "kako verujem skorovima?" (metodologija + contract testovi),
      "ToS sivа zona?" (principijelno ne — 'legitimate free, not gray free')
