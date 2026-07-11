# AIchain — Stanje projekta i kontekst za nastavak

> Za Cowork / bilo koju buduću Claude sesiju. Pročitaj OVO prvo, zatim dokumente po potrebi.
> Datum stanja: 2026-07-09. Vlasnik: filokloi. Jezik komunikacije sa vlasnikom: srpski (ekavica, latinica).

## 1. Vizija (jedna rečenica)
Ruteri optimizuju za provajdere — AIchain optimizuje za korisnika: lokalni, besplatni ruter koji ukršta
objektivni katalog svih LLM-ova (globalna istina) sa korisnikovom ličnom situacijom — pretplate, free
kvote, promocije, lokalni hardver (subjektivna istina) — i po zahtevu bira optimalan model ili ansambl.

## 2. Arhitektura (tri sloja)
1. **Globalna istina** — `filokloi/AIchain`: GitHub Actions pipeline, re-skoruje ~336 modela na 12h,
   objavljuje `catalog_manifest.json` (v5) na GitHub Pages. Deterministički; LLM sme samo da PREDLAŽE
   kandidate iz nestrukturiranih izvora, pipeline verifikuje pre upisa.
2. **Ruter** — `aichaind` lokalni sidecar: OpenAI-kompatibilni endpoint na localhost. SVI harnesi
   (OpenClaw, Hermes, Cline...) integrišu se promenom base_url — nula koda po harnesu. Virtuelni
   modeli: `aichain/auto`, `/economy`, `/power`, `aichain/<id>` (pin), `aichain/lock:<id>` (hard lock).
3. **Subjektivna istina** — `user_truth.json` (šema: `schemas/user_truth.schema.json`): profil sa
   2 slajdera (inteligencija, osetljivost na cenu), budžet, imovina (pretplate/ključevi/kvote/lokalni
   modeli), privacy pravila. Nikad ne napušta mašinu. API ključevi u keyring/env, NE u fajlu.

## 3. Ključne donete odluke (ne otvarati ponovo bez razloga)
- **Ruter NE koristi veliki LLM za rutiranje** — deterministička pravila + mali lokalni klasifikator.
- **Value density** je rang-metrika: `task_score^γ / (effective_cost·pressure + ε)`; γ iz slajdera
  inteligencije (1..3); free putanje → čist task_score ("kad je marginalni trošak 0, uzmi najpametnije").
- **Efektivna cena po PUTANJI, ne po modelu** (isti model: free kvota / kredit / pretplata / paid) i
  cache-aware (sticky = prefix po cached tarifi; switch = pun re-billing transkripta).
- **Privacy hard filter pre svakog skoringa; jedini filter koji ni lock ne pregazi.**
- **Redosled filtera:** privacy → capability (tools/vision/context) → dinamički floor
  (`max(min_intelligence, difficulty·0.8)`) → budžet.
- **Sticky histereza:** prag prebacivanja = 15 + transcript_tokens/10k (čuva prompt cache i koherentnost).
- **Ansambl** (best-of-N+judge / dekompozicija / generate+verify) samo za teške zadatke, uz prikaz
  procene troška i potvrdu.
- **"Legitimate free, not gray free":** ToS-kršeći pristupi se NE listaju; app-only pretplate se samo
  predlažu korisniku, nikad programski premošćavaju.
- **Quota pacing:** free kvota se raspoređuje kroz dan (čuva se za teška pitanja pri dnu kvote).
- **Use-it-or-lose-it:** kredit koji ističe dobija bonus u rangiranju.

## 4. Šta postoji (urađeno u ovoj fazi)
| Artefakt | Status | Ide u |
|---|---|---|
| MANIFEST.md (pozicioniranje) | gotovo | AIchain/ root |
| docs/METHODOLOGY.md (izvori, normalizacija, težine) | gotovo — **težine su predlog, uskladiti sa arbitrator.py!** | AIchain/docs |
| docs/ROUTING_POLICY.md (8-koračni pipeline, primer) | gotovo | AIchain/docs |
| docs/DYNAMIC_AUTO.md (slajderi, taksonomija 10 tipova, POM/value density §2b, lock, ansambl) | gotovo | AIchain/docs |
| docs/OPTIMIZATIONS.md (DeepSeek DSA/V4, LLMLingua-2, KV, prompt cache — prioriteti) | gotovo | AIchain/docs |
| schemas/user_truth.schema.json | gotovo (v1) | AIchain/schemas |
| aichaind/pom.py — matematičko jezgro | gotovo, **13/13 testova prolazi** | AIchain/aichaind |
| tests/test_pom.py | gotovo | AIchain/tests |
| CLEANUP.md checklista repoa | čeka izvršenje | ne komituje se, izvrši se |

Postojeći kod u repou koji NIJE diran: `tools/arbitrator.py`, `aichaind/` runtime, openclaw-skill,
GitHub Pages dashboard. `pom.py` je nov modul — još NIJE povezan na postojeći aichaind runtime.

## 5. Roadmap — sledeći ograničeni zadaci (redosled po prioritetu)
Svaki zadatak = jedna sesija, sa kriterijumom prihvatanja. Ne raditi više odjednom.

1. **DeepSeek alias migracija** (HITNO, rok 24.7.2026): grep `deepseek-chat|deepseek-reasoner` po
   repou, zameniti aktuelnim ID-jevima. Prihvatanje: nijedan legacy alias u routing tabelama.
2. **Integracija pom.py u aichaind runtime:** postojeća routing logika poziva `build_chain()`;
   `user_truth.json` se učitava i validira šemom (jsonschema). Prihvatanje: sidecar odgovara na
   zahtev kroz novi pipeline; postojeći testovi + test_pom prolaze.
3. **Klasifikator zadataka:** tier 1 deterministički (attachments/tool schema/code fence/dužina),
   tier 2 embedding centroidi (lokalni model, opciono). Izlaz `(task_type, difficulty)`.
   Prihvatanje: ≥85% tačnost na ručnom test setu od 50 primera (napraviti ga).
4. **Metodologija ↔ arbitrator usklađivanje:** ili kod prati METHODOLOGY.md ili se dokument
   koriguje. Prihvatanje: svaki broj u manifestu izvodljiv iz dokumenta.
5. **Cache-aware trošak + histereza u živom ruteru** (formula već u pom.py — samo povezati telemetriju).
6. **LLMLingua-2 kompresija** (3 tačke ubacivanja iz OPTIMIZATIONS §2, off by default).
7. **Virtuelni modeli u /v1/models** (`auto/economy/power/lock:`) + inline `@aichain` komande.
8. **Per-task skorovi u katalogu** (arbitrator da emituje task_scores po taksonomiji iz DYNAMIC_AUTO §2).
9. **Ansambl tier** (tek kad 1–8 stoji).
10. **Plasiranje:** free stranica kao udarni proizvod, Show HN / r/LocalLLaMA objava (MANIFEST je pitch).

## 6. Ograničenja i pravila rada
- Vlasnik je na ograničenom budžetu — zadaci moraju biti ograničeni i uvek ostaviti upotrebljiv rezultat.
- Ništa se ne push-uje bez prikaza diff-a vlasniku; destruktivne izmene uz potvrdu.
- Ne dirati: API ključeve, config sa tajnama, SubvencijeRadar (odvojen projekat, izolovan namerno).
- Srpsko tržište: bez Stripe-a; ako ikad zatreba naplata — Lemon Squeezy/Gumroad (ali v. MANIFEST:
  AIchain sam po sebi ostaje besplatan).
