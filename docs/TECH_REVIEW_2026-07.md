# Tehnička revizija — jul 2026

Kritički presek stanja AIchain-a naspram aktuelnog stanja tehnike u LLM rutiranju,
sa listom usvojenih izmena (commit uz ovu reviziju) i svesno odbijenih pravaca.

## 1. Gde AIchain stoji naspram SOTA

Aktuelna literatura i praksa (2025–2026) konvergiraju oko tri ose rutiranja:
*kada* se odlučuje (pre zahteva / kaskadno posle prvog odgovora), *čime* se
odlučuje (feature-i upita, metapodaci modela, istorija performansi) i *kako*
(pravila, naučeni klasifikatori — matrix factorization / IRT, RL, kaskade).
Objavljeni rezultati: learned ruteri šalju ~14% upita na frontier model uz
~95% kvaliteta i do ~85% uštede.

AIchain-ova odluka "bez velikog LLM-a u ruteru" ostaje ODBRANJIVA: learned
ruteri treniraju na preference-podacima kojih lokalno nema, a determinizam je
uslov reproduktivnosti (METHODOLOGY §1). Lokalni analog learned rutera su
tier-2 embedding centroidi — sada implementirani (TF-IDF varijanta).

## 2. Nalazi revizije (kritično)

| # | Nalaz | Ozbiljnost | Status |
|---|---|---|---|
| 1 | Cascade je slao L1-izbor kao `sticky_model_id` u POM — histereza je štitila pogrešan model (nagađanje rutera umesto modela na kojem je razgovor) | VISOKA | ✅ ispravljeno: session_context iz CanonicalSession (poslednji uspešan run) |
| 2 | `transcript_tokens` se nigde nije prosleđivao — cache-aware formula iz pom.py bila je mrtvo slovo | VISOKA | ✅ povezano: zbir tokena iz provider_runs |
| 3 | Jedinstven `cached_rate_factor=0.1` za sve provajdere — realnost jul 2026: DeepSeek v4 ~0.02 (98% popust na hit), Anthropic/OpenAI/Google ~0.1, nepoznati provajderi često 1.0 (bez keša) | SREDNJA | ✅ PROVIDER_CACHE_FACTORS tabela; nepoznat provajder = 1.0 (konzervativno: ne izmišljaj popust) |
| 4 | Quota pacing (PROJECT_STATE §3) neimplementiran — lako pitanje je moglo da spali poslednji besplatni poziv | SREDNJA | ✅ donjih 20% dnevne kvote rezervisano za difficulty ≥ 40; record_usage telemetrijska kuka na uspešan poziv |
| 5 | Klasifikator samo tier-1 regex — neutralni tekstovi padali na default | SREDNJA | ✅ tier-2 TF-IDF centroidi (10 tipova, EN+SR, bez novih zavisnosti, referentni dokumenti disjunktni od test seta) |
| 6 | Dva paralelna "mozga" u ruteru: legacy CostOptimizer (1400+ linija) i POM — dupliran domen, dugoročno divergiraju | SREDNJA | ⏳ otvoreno: konsolidacija = zaseban zadatak; POM je sada primaran, legacy je fallback |
| 7 | `Budget.spent_today` = 0 (dnevna potrošnja se ne agregira preko sesija) | SREDNJA | ✅ SessionStore.spent_today_usd() — UTC-dnevni agregat preko svih sesija, povezan u PomRouter |
| 8 | Kaskadni "generate→verify→escalate" obrazac (FrugalGPT-stil) nije implementiran — roadmap #9 (ansambl) | NISKA | ⏳ planirano roadmap-om |
| 9 | `intelligence` = ponderisana sredina 3/2/1 (dokument je tvrdio drugačije) | NISKA | ✅ medijan dostupnih izvora (METHODOLOGY v1.2) |

## 3. Svesno ODBIJENO (uz obrazloženje)

- **Learned router (matrix factorization / IRT / RL):** zahteva preference-dataset
  i trening-petlju; krši princip determinizma i lokalnosti. Revizija: tier-2
  centroidi + per-task katalog skorovi daju većinu vrednosti bez treninga.
- **LLM-as-router (poziv malom LLM-u za klasifikaciju):** latencija + trošak +
  nedeterminizam; postojeći L4 cloud classifier ostaje feature-flagged OFF.
- **Semantic caching odgovora:** vredno, ali je odvojen proizvodni rizik
  (staleness, privatnost); kandidat za OPTIMIZATIONS.md listu, ne za jezgro.

## 4. Verifikacija

- 527 testova prolazi (12 novih za: cache faktore, histerezu na near-tie
  modelima, context hard-filter na dugim transkriptima, pacing, record_usage,
  session_context izvlačenje, tier-2 klasifikaciju).
- Prihvatni kriterijum roadmap #5 ("formula povezana na telemetriju") ispunjen:
  sticky + transcript_tokens + potrošnja teku iz CanonicalSession u build_chain().

## 5. Dopuna (isti dan, nastavak sesije)

Posle inicijalne revizije implementirano i:
- **Roadmap #7 — virtuelni modeli:** `GET /v1/models` (OpenAI format) +
  tumačenje `model` polja: `aichain/auto|economy|power|local`,
  `aichain/lock:<id>` (sesijski hard lock), `aichain/<id>` (one-shot pin).
  Eksplicitna `_aichain_control` i dalje pobeđuje.
- **Roadmap #6 — LLMLingua-2:** `aichaind/compression/lingua.py`, off by
  default, opciona zavisnost (bez nje inertno), guardrails (system/tool/kod/
  poslednjih k turnova/`@aichain nocompress`), uvezano lingua-first u
  `_maybe_compress_messages` sa summarizer fallback-om; `can_fit_by_compression`
  za tačku ubacivanja 3.
- **Review #7:** dnevni budžet — `SessionStore.spent_today_usd()`.
- **Review #9 / METHODOLOGY v1.2:** medijan benchmark izvora u merge-u.
Suite posle svega: 543 passed.
