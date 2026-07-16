# Gemini optimization plan for Scrapper - DEMO

## 1. Cieľ

Znížiť cenu, tokeny a opakovanie v Gemini pipeline bez straty technickej
kvality, slovenského výstupu, evidence trailu, vision pokrytia, risk scorera a
prísnej market/link policy.

Plán je určený pre aktuálny Python/Flask projekt s priamymi Gemini REST
requestmi, dočasnými `Auta/<slug>/` priečinkami a bez perzistentnej databázy.
Normálna pipeline má jeden `text_research` request; druhý je iba jednorazový
recovery pri neplatnom JSON.

## 2. Rozsah

| Implementovať teraz | Odložiť |
|---|---|
| Presný usage a cost audit | Databázový component cache |
| Centrálne phase policies a budgety | Background queue/object storage |
| Menší research packet | Distribuovanú idempotenciu |
| Lacnejší JSON recovery | Úplný frontendový report renderer |
| Podmienený Mobile.de grounding | Druhý high-resolution vision pass |
| Kratší final prompt/report | Prechod na Google SDK |
| Image dedup a attachment cap | Zmenu risk/market pravidiel |
| Cost/quality evaluation a rollback | Viac samostatných feature flags |

Cache nemá zmysel implementovať pred perzistentnou DB a concurrency lockom;
Render filesystem je dočasný.

## 3. Overený baseline

Projekt už má environment-configured modely, phase output settings, obmedzené
thinking, JSON schémy, maximálne jeden text/vision recovery, bounded API-key
retry, compact final context, image collages/dedup, rollback profil a
calibration CLI. Tieto časti sa nemajú implementovať druhýkrát.

Posledný Tiguan debug run ukázal:

- vision: 5 986 actual input a 2 051 actual output tokenov,
- final report: približne 2 564 output tokenov,
- vision nepotreboval recovery,
- 89 market kandidátov v `grok_research.json` pridal backend, takže veľkosť
  súboru nereprezentuje modelový research output,
- text initial/recovery usage nie je v debug balíku dostatočne oddelený.

Preto musí byť prvým krokom observability bez zmeny výstupu.

## 4. Ciele

- Každý AI request má run ID, phase, model, attempt, status, retry reason,
  duration a usage source.
- Provider input, visible output, thinking, cached a total tokens sa ukladajú,
  ak ich response poskytne.
- Estimate a actual usage sú jasne oddelené.
- Bežný run má jeden text-research request.
- Research V2: medián `<= 2 500`, po kalibrácii cieľ `<= 2 200` output tokenov.
- Final: bežne `<= 9 000` actual input; mäkký output cieľ je `<= 3 500` tokenov.
- Vision: medián `<= 1 800` output tokenov bez straty relevantného pokrytia.
- Mobile grounding sa nespustí pri troch strict eligible lokálnych ponukách.
- Medián ceny cache-miss analýzy klesne aspoň o 35 % bez quality regression.

Absolútny cieľ `<= 0,12 EUR` sa potvrdí až po 20 presne zmeraných behoch s
model-specific cenami a grounding charges. Cache-hit cieľ sa odkladá s DB.

## 5. Cieľová pipeline

```text
listing facts + VIN light decode
  -> component identity grounding
  -> reliability grounding
  -> direct SK/CZ market search
  -> strict local eligibility precheck
     -> Mobile.de fallback iba ak treba
  -> compact research normalization JSON
  -> vision JSON
  -> deterministic benchmark + risk score
  -> compact final context
  -> final Markdown report
```

## 6. Fáza 1 — Usage a cost observability

Táto fáza nemení prompt, model ani report.

### Zmeny

Rozšíriť `token_tracker.py` o spätne kompatibilné polia:

- `analysis_run_id`, `phase`, `attempt`, `retry_reason`,
- `visible_output_tokens`, `thinking_tokens`, `cached_input_tokens`,
- `total_tokens`, `usage_source`, `thinking_mode`,
- `max_output_tokens`, `grounding_enabled`, `provider_request_id`,
- model-specific `estimated_cost`.

Pravidlá:

- provider hodnota `0` sa nesmie cez `or` nahradiť odhadom,
- failed request bez usage nie je potvrdený billable request,
- thinking patrí do billable output estimate,
- cached tokens sa nepočítajú dvakrát,
- neznámy model používa označený fallback rate,
- staré `token_usage.json` záznamy zostanú čitateľné.

V `providers/gemini.py` zachovať parsing existujúcich token fields. Cached usage,
request ID a Interactions/grounding usage mapovať iba podľa reálneho REST
payloadu; nevymýšľať SDK fields.

Pridať:

- `text_research_provider_attempts.json` s initial/recovery usage, finish reason,
  output chars, schema status a sanitized error,
- `ai_usage_summary.json` s call countom, usage/cost podľa phase, retries,
  recoveries, grounding countom, actual coverage a duration.

Raw research outputs patria iba do admin debugging bundle, nie do blind
calibration bundle.

### Testy a exit gate

Rozšíriť token tracker, provider, grounding, pipeline, dashboard a bundle testy o
explicit zero, thinking, cached, missing usage, retries, legacy entries a
initial/recovery artefakty.

Fáza je hotová, keď jeden live interný run vysvetľuje každý request a rozdiel
medzi provider usage a dashboard cenou bez zmeny reportu.

## 7. Fáza 2 — Policies, budgety a Research V2

### Centrálna policy

Vytvoriť `scrapper_demo/ai_policy.py` s immutable policy: max input/output,
visible target, temperature, thinking mode a max attempts.

Počiatočné safety ceilings:

| Phase | Max input | Max output | Visible target |
|---|---:|---:|---:|
| identity grounding | 5 000 | API/prompt bound | 600 |
| reliability grounding | 8 000 | API/prompt bound | 2 500 |
| text research | 10 000 | 4 000 | 2 200 |
| text recovery | 8 000 | 3 200 | 1 800 |
| vision | 8 000 + images | 4 000 | 1 800 |
| vision recovery | 8 000 + images | 3 500 | 1 600 |
| final synthesis | 9 000 normal | 6 000 shared | 3 500 soft |

Sprísňovať ich jednotlivo až po Fáze 1. `legacy` profil zachová súčasné stropy.

### Token counting a compaction

Pridať `check_and_compact_input(...) -> BudgetResult` s pre/post countom,
counting method, vykonanými úpravami a final contents.

Pre text/final overiť Gemini REST `countTokens`. Pri výpadku použiť lokálny
odhad s warningom, nie automaticky zastaviť analýzu.

Poradie kompakcie: duplicitné inštrukcie; raw web pri normalized findings;
duplicitné listing/identity dáta; opakované claims/risks; low-priority source
prose; dlhý seller description; voliteľné low-impact položky.

Nikdy neodstrániť VIN/conflict, cenu/menu/DPH, rok, kilometre,
engine/transmission/drivetrain resolution, high-impact evidence, benchmark
limitations alebo backend verdict constraint.

### Research V2

Vytvoriť `schemas/research_model_output.schema.json`. Model vracia iba:

- evidence summary,
- seller claims, unknowns, conflicts a consistency checks,
- safety/recall,
- web findings a technical risks,
- expected costs a risk flags,
- source references.

Model už negeneruje canonical listing facts, component identity, local VIN
decode, market comparables/median, price view, risk score/verdict ani
buyer-facing report.

Pipeline z modelového packetu a backend listing/identity/VIN/market artefaktov
vytvorí kompatibilný canonical `grok_research.json` pre risk scorer a final.

Aktuálne kalibrované array limity: claims 3, unknowns 3, conflicts 2, checks 3,
web findings 3, technical risks 3, costs 3, flags 2 a sources 5. Rovnaký problém sa neopakuje vo
viacerých arrays; cost môže odkázať na risk ID.

### Recovery

Recovery je iba pri syntax/schema/truncation chybe, nerobí nový search, používa
menší packet a má maximum jeden pokus. Druhé zlyhanie vytvorí schema-valid
unavailable fallback; final report uvedie limitation a nepoužije unsupported
high-impact claims.

### Testy a exit gate

Pridať `test_ai_policy.py` a testy budget hraníc, compaction priority,
kritických hodnôt, count fallbacku, Research V2 merge, jedného recovery,
double-failure fallbacku a Tiguan-like fixture.

Gate: research medián `<= 2 500` a žiadne zhoršenie schema validity,
identity/risk/report testov.

## 8. Fáza 3 — Grounding, final a vision

### Conditional Mobile.de grounding

V `market_comparables.py` vytvoriť strict eligibility helper spoločný pre
precheck aj finálny benchmark:

1. direct SK/CZ search,
2. dedup a strict eligible count,
3. pri `>= 3` lokálnych ponukách Mobile grounding skipnúť,
4. pri `< 3` skúsiť Mobile direct HTTP,
5. pri stále nedostatočnej tight vzorke povoliť jeden grounded pass.

Foreign records zostávajú background-only. Diagnostika uloží reason, local
eligible count, direct/grounded Mobile count a skipped/needed status.

Identity a reliability grounding nezlučovať. Skipnú sa až po zavedení
bezpečného, versioned a perzistentného component cache.

### Final prompt/report

Skrátiť približne 23 600-znakový final prompt: odstrániť duplicitné pravidlá,
pevné minimálne počty a backendom vynucovaný boilerplate. Zachovať section
order, verdict lock, evidence categories, market link policy, limitations,
language a zákaz tvrdení o skrytom stave/histórii.

Summary má najviac tri rozhodujúce body; risk sa vysvetlí raz; cost naň stručne
odkáže; seller question sa pridá iba ak môže zmeniť rozhodnutie; conclusion
neopakuje celý report. Markdown generation zatiaľ zostáva.

### Vision payload

Presunúť perceptual dedup pred gallery-size vetvenie, aby fungoval aj nad 20
fotografií. Metadata rozšíriť o original/unique/duplicate/selected count,
selection reason a coverage.

Pridať `AI_MAX_VISION_ATTACHMENTS`, default 5; po evaloch skúsiť 4 (tri overview
+ jeden detail). Zachovať dashboard, odometer, visible damage a engine bay.
Nespúšťať automatický druhý high-resolution request.

### Testy a exit gates

- tri strict lokálne ponuky -> žiadny Mobile grounding,
- dve -> fallback povolený,
- nerelevantné cards sa nepočítajú,
- foreign links zostanú skryté a benchmark sa refaktorom nezmení,
- final zachová sections, verdict a SK/CZ link policy,
- final input bežne `<= 9 000`, mäkký output cieľ `<= 3 500`,
- image dedup funguje aj nad 20 obrázkov a rešpektuje cap/no-images/recovery.

## 9. Fáza 4 — Evaluation a rollout

Použiť najmenej 20 anonymizovaných calibration bundles: rôzne palivá,
prevodovky, vek, kilometre, VIN/no-VIN, fotografie a dostupnosť market
benchmarku; minimálne tri známe production regressions.

Rozšíriť `calibration_cli` o AI cost report. Pre case zaznamenať call count,
retries/recoveries, actual/estimated tokeny, grounding count, duration,
model-specific cost, schema validity, identity agreement, unsupported claims,
report completeness, market-link violations a expert rating slovenčiny.

Výstup: JSON a Markdown, legacy/optimized rozdiel, median, p90 a maximum. Unit
testy nikdy nerobia live provider calls.

Rozšíriť `DEMO_ANALYSIS_PROFILE`:

- `legacy` — núdzový rollback,
- `quality_optimized` — súčasná cesta,
- `cost_optimized` — nový packet, budgety a prompty.

Rollout: telemetry bez behavior change; offline eval; niekoľko interných live
porovnaní; nový profil iba pre interné jobs; po splnení gates nový default;
`quality_optimized` ponechať aspoň jedno release obdobie. Customer shadow run
sa nesmie spustiť ani účtovať dvakrát.

Quality gates: všetky testy prejdú; identity/schema quality neklesne; nepribudnú
unsupported high-impact claims; market policy a report completeness zostanú;
medián ceny klesne aspoň o 35 %; retry/recovery náklady sú viditeľné.

## 10. Súbory a test scope

| Súbor | Zodpovednosť |
|---|---|
| `scrapper_demo/ai_policy.py` | Phase policies a budget helper |
| `providers/gemini.py`, `providers/retry.py` | Usage, attempts, REST diagnostics |
| `token_tracker.py` | Model cost a run summary |
| `services/analysis_pipeline.py` | Research V2 a conditional grounding |
| `legacy_server.py` | Context compaction/final builder |
| `market_comparables.py` | Shared strict eligibility helper |
| `services/image_service.py` | Global dedup a attachment cap |
| `prompts/*.md`, `schemas/*.json` | Menšie kontrakty |
| `storage/listing_jobs.py`, `calibration*.py` | Artefakty a eval |
| `web/token-dashboard.html`, `README_DEMO.md` | Observability a dokumentácia |

Test scope: usage/cost; policy/budget/compaction; Research V2 merge;
initial/recovery/fallback; rate limit/backup key; Mobile required/skipped; final
context/report; image dedup/cap/no-images; bundle privacy a profile rollback.
Live tests sú iba manuálne alebo opt-in.

## 11. Implementačné poradie

1. `usage-observability`
2. `phase-policy`
3. `research-packet-v2`
4. `research-budgets`
5. `conditional-market-grounding`
6. `final-prompt`
7. `vision-payload`
8. `ai-evaluation`

Každý krok musí byť samostatne revertovateľný. Prompt, backend kontrakt a
dashboard redesign nemajú byť v jednom veľkom commite.

## 12. Definition of done

1. Každý request má run, phase a attempt.
2. Dashboard rozlišuje provider usage od estimate.
3. Thinking/cached/total tokens sa ukladajú, keď existujú.
4. Text model generuje iba research-owned packet a backend canonical JSON.
5. Normálny run nemá duplicitný text research; recovery je maximálne jeden.
6. Tokenové ciele platia na 20-case datasete.
7. Mobile grounding sa spúšťa iba pri nedostatočnej lokálnej vzorke.
8. Vision neposiela známe duplicity a rešpektuje cap.
9. Report, risk scorer, benchmark, link policy a validácia zostávajú funkčné.
10. Medián ceny klesne aspoň o 35 % bez material quality regression.
11. `quality_optimized` umožňuje okamžitý rollback.
12. README dokumentuje policies, artefakty, env vars, eval a rollback.

## 13. Prvý krok

Implementovať iba Fázu 1. Po jednom úplne zmeranom internom rune uložiť
baseline a až potom potvrdiť output ceilings Fázy 2. Bez tejto brány by sa
optimalizovalo podľa nepresných odhadov.
