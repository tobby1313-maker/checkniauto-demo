# Candid review

This is a functioning research prototype, not a production-ready product. It tackles a real problem, but the current system produces conclusions with more confidence and precision than its evidence justifies. The two biggest blockers are trustworthiness and privacy—not UI polish.

I inspected the live Suzuki report, its public artifacts and token telemetry, the backend, scrapers, prompts, schemas, frontend, documentation, and tests. All 58 tests pass. No files were modified.

## Verified findings

### 1. User data and internal artifacts are publicly exposed

**Severity: Critical · Effort: Medium**

What is wrong:

- Every completed analysis is listed globally through `/api/demo/listings`.
- Anyone can access the listing text, uploaded/scraped photos, raw model output, research JSON, vision JSON, risk score, and legacy prompt input.
- The public token dashboard exposes model usage, latency, estimated costs, listing slugs, live progress, and the server filesystem path.
- `_check_demo_access()` provides no access control at all.

Evidence:

- [`_check_demo_access()` returns `None`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:800>).
- [`ANALYSIS_ARTIFACTS`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:424>) includes `raw_data.json`, `analysis_request.md`, raw model output, and all intermediate model artifacts.
- Public artifact routes are intentionally exposed in [`web_server.py`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:1105>).
- The frontend’s “previous analyses” drawer loads the global listing collection from [`index.html`](<D:/VS Projekty/Scrapper - DEMO/web/index.html:2729>).
- The live [token endpoint](https://checkniauto.onrender.com/api/token-usage?limit=5) exposes cost, model, latency, slug, and `/tmp/scrapper-demo/Auta/token_usage.json`.
- The live [artifact listing](https://checkniauto.onrender.com/api/demo/listings/suzuki-grand-vitara-24-benzin-facelift-model/artifacts) confirms the raw files are public.

Why it matters:

Manual input explicitly invites VINs, seller information, listing text, and user-supplied photos. A user would reasonably assume these are private unless they explicitly share them. Instead, they are discoverable through a global dashboard. This is a serious privacy, trust, and potentially regulatory problem. Publishing the prompt input and raw outputs also exposes internal implementation details.

Concrete improvement:

- Give each visitor an anonymous private session.
- Generate random, unguessable job IDs rather than human-readable slugs.
- Enforce ownership on every listing, photo, report, and artifact request.
- Make sharing opt-in through a separate expiring share token.
- Put token telemetry and raw artifacts behind administrator authentication.
- Publish a clear privacy and retention notice before manual uploads.
- Delete private jobs automatically after a stated period.

---

### 2. The report’s evidence chain is not trustworthy enough

**Severity: Critical · Effort: Large**

What is wrong:

The public report presents exact repair costs, failure intervals, market prices, and comparable vehicles while deliberately removing source names and links. The user cannot distinguish well-supported information from weak forum evidence or model-generated synthesis.

The live report says four comparable vehicles establish a €10,300–€11,500 market range. In the underlying JSON, all four “comparables” point to the same Bazoš category-search URL—not exact listings. That does not demonstrate that the individual vehicles existed with those specifications and prices.

Evidence:

- The final prompt explicitly says not to print URLs, source names, or citations in [`grok_final_synthesis_system.md`](<D:/VS Projekty/Scrapper - DEMO/prompts/grok_final_synthesis_system.md:28>).
- [`analysis_normalizer._remove_public_hyperlinks()`](<D:/VS Projekty/Scrapper - DEMO/analysis_normalizer.py:95>) strips links again.
- The live report gives exact failure thresholds such as “from 100,000 km” and precise repair ranges without public attribution.
- Underlying sources include owner forums and repair-service pages, but the final output flattens these into authoritative-looking statements.
- Competitors already provide record-backed mileage, damage, ownership, and market information from structured databases: [carVertical](https://www.carvertical.com/gb/features), [Cebia](https://sk.cebia.com/), and [autoDNA](https://www.autodna.com/).

Why it matters:

A buyer may negotiate, reject a vehicle, or spend money based on these claims. Hiding sources makes the output easier to read but materially less trustworthy. “Grounded search” is not equivalent to verified evidence.

Concrete improvement:

- Keep source links visible in a compact evidence drawer.
- Require every model-level risk, repair estimate, recall, and comparable to reference an exact source record.
- Accept market comparables only when an exact listing URL, capture timestamp, price, year, mileage, and drivetrain were extracted.
- Omit the market conclusion if fewer than three valid comparables exist.
- Label evidence explicitly as seller claim, photo observation, model-level inspection point, official data, or unverified estimate.
- Prefer official service schedules and recall databases over forums and generic repair pages.

---

### 3. The “deterministic” risk score is logically flawed and uncalibrated

**Severity: Critical · Effort: Large**

What is wrong:

The score is deterministic, but that does not make it correct. It translates loosely structured model output into an arbitrary point total without demonstrated calibration against real purchase outcomes or expert assessments.

There is a concrete production bug: Gemini described the Suzuki’s exterior and interior as being in very good condition but assigned those positive observations severity `minor`. The scorer treats every `minor` or `medium` observation as damage and added a `visible_minor_damage` penalty.

Evidence:

- [`minor_damage = _has_visual_severity(... {"minor", "medium"})`](<D:/VS Projekty/Scrapper - DEMO/risk_scorer.py:149>) does not inspect whether the observation is positive or negative.
- The live vision JSON described “very good visual condition” and “exceptionally clean” interior with `severity: "minor"`.
- The resulting live risk score added one point for “visible minor damage.”
- Any high-confidence expensive generic model risk adds two points in [`risk_scorer.py`](<D:/VS Projekty/Scrapper - DEMO/risk_scorer.py:165>).
- Age or mileage adds another generic point, and missing seller metadata adds another at [`risk_scorer.py`](<D:/VS Projekty/Scrapper - DEMO/risk_scorer.py:189>).
- Score thresholds in [`_verdict_for_score()`](<D:/VS Projekty/Scrapper - DEMO/risk_scorer.py:262>) are hand-authored rather than validated.

Why it matters:

The orange verdict appears objective because it is “backend-calculated,” but its inputs are model-generated, inconsistently typed, and occasionally misinterpreted. This can create false confidence and false negatives or positives.

Concrete improvement:

- Separate observation polarity from severity.
- Only vehicle-specific evidence should directly worsen the purchase verdict.
- Generic model risks should generate inspection actions, not automatically penalize the vehicle.
- Do not score missing parser fields such as seller type unless they are genuinely buyer-relevant.
- Build a reviewed evaluation set of real listings and expert assessments.
- Measure false-positive risk flags and verdict agreement before presenting a categorical recommendation.

Until that exists, replace the five-color verdict with something more honest, such as “insufficient evidence,” “worth inspecting,” or “do not proceed before resolving X.”

---

### 4. Validation looks stronger than it is

**Severity: Critical · Effort: Medium**

What is wrong:

Schemas exist, but they are not actually enforced. Validation checks only whether top-level required fields are present. It does not validate types, enum values, nested fields, evidence relationships, or factual consistency. All failures are non-blocking warnings, and the report is still published.

Evidence:

- [`_soft_validate_json_contract()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:2390>) reads only the schema’s `required` array.
- [`_soft_validate_final_report()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:2737>) checks headings, markers, a verdict substring, links, and a few regex patterns.
- Warnings are written to `validation_warnings.json`; they do not prevent publication or trigger regeneration.
- The live report says a 2011 vehicle is 13 years old in July 2026.
- The live API’s parsed specifications contain `"-----------": "-------"` because [`parse_car_info_md()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:262>) accepts the Markdown delimiter row as data.
- `vin_decoded.json` attempts to decode `"N/A"` as a VIN because [`main._run_vin_decoding()`](<D:/VS Projekty/Scrapper - DEMO/main.py:290>) treats placeholder text as a real value.
- The report calls Czech origin a positive indicator of better service history, which is unsupported.
- The report says visual wear is consistent with 98,000 km despite no odometer being visible.

Why it matters:

The project gives the impression that output contracts and deterministic controls protect users. In reality, they mainly protect formatting.

Concrete improvement:

- Use full JSON Schema validation.
- Reject or regenerate malformed model output.
- Add cross-field rules for age, VIN placeholders, mileage, source URLs, and observation polarity.
- Require provenance for every high-impact claim.
- Prevent publication when the final verdict conflicts with structured evidence.
- Add regression fixtures from actual production failures, including this Suzuki result.

---

### 5. The experience is too slow, expensive, and verbose for the value delivered

**Severity: Important · Effort: Large**

What is wrong:

The live analysis took roughly three minutes from scraping to final report. It made four Gemini calls, used approximately 55,000 estimated tokens, and cost about €0.42 at the configured rates. The resulting report is approximately 16.7 KB and repeats the same risks in the summary, technical risks, cons, questions, costs, and conclusion.

The landing copy promises a “concise analysis.”

Evidence:

- Live artifacts run from 12:04:23 to 12:07:29.
- The public [token endpoint](https://checkniauto.onrender.com/api/token-usage?limit=5) reports 55,483 estimated tokens and €0.41777.
- The pipeline serially performs grounded research, text extraction, vision, scoring, and final synthesis in [`_multi_model_analysis_events()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:3483>).
- The final prompt forces 4–6 main risks, 2–4 additional checks, 5–8 cons, 3–8 cost rows, and 5–7 inspection questions. That requirement itself creates repetition.
- The UI uses a simulated progress animation that advances toward 92%, rather than a meaningful ETA.

Why it matters:

Most users will tolerate waiting when the result is uniquely reliable. This result is primarily a long pre-purchase checklist with uncertain evidence. Three minutes and €0.42 per run are poor economics for public, anonymous usage.

Concrete improvement:

- Produce an immediate first-stage result: extracted facts, missing information, and three highest-value checks.
- Make deeper research optional.
- Remove forced section and bullet counts.
- Collapse repeated content into a decision summary with expandable evidence.
- Establish explicit latency and cost budgets.
- Avoid a second prose-generation pass when deterministic templates can render structured results.

---

### 6. The runtime architecture cannot support real usage

**Severity: Critical · Effort: Large**

What is wrong:

The deployment runs one Gunicorn worker and permits one global analysis job. Each analysis keeps an SSE request open for minutes. Rate limits, progress, concurrency, and job state are in process memory. Reports are stored on an ephemeral filesystem.

Cancellation is also incomplete: aborting the browser request closes the stream, but the scraper subprocess is not terminated on generator cancellation. The semaphore may be released while the child process continues writing files.

Evidence:

- The `Procfile` uses one worker.
- `_demo_job_lock` is a process-local semaphore in [`web_server.py`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:181>).
- New concurrent jobs receive an immediate 429 in [`_stream_with_demo_limits()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:3890>).
- Rate limiting is an in-memory dictionary in [`_check_demo_rate_limit()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:804>).
- The scraper subprocess is started at [`web_server.py`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:3942>) without a `finally` block that terminates it when the client disconnects.
- “Saved” analyses are temporary, with cleanup happening only when another analysis begins.

Why it matters:

The second simultaneous user gets rejected. Restarts reset limits and progress. Jobs disappear. Multiple workers would produce inconsistent state. Cancellation can waste tokens and compute.

Concrete improvement:

Use a real background-job model:

- Persistent job records in a database.
- Object storage for photos and reports.
- A queue with one or more workers.
- Polling or resumable event streams by job ID.
- Distributed rate limiting.
- Explicit job cancellation that terminates child work.
- Retry/idempotency controls per pipeline stage.

---

### 7. The codebase contains too many products and eras in one server

**Severity: Important · Effort: Large**

What is wrong:

`web_server.py` is 4,499 lines and combines:

- Public demo API.
- Private listing CRUD.
- Local folder operations.
- Knowledge-base management.
- Three LLM providers.
- Markdown rendering.
- Image processing and collages.
- Report normalization and validation.
- Token monitoring.
- Legacy analysis flows.

There is unreachable legacy pipeline code after an unconditional `return` in `api_analyze()`. `main.py` advertises `mobile.de`, but `Mobile_de.py` is absent. `DEMO_SKIP_KB` skips KB inclusion in one path, while final analysis still contains KB autosave code.

Why it matters:

The public/private route gate is compensating for an architecture boundary that should exist at build/deployment level. Every change risks breaking unrelated behavior. Dead paths rot without being noticed.

Concrete improvement:

- Create a public demo application containing only public routes.
- Move analysis orchestration into a service module.
- Give providers a small common interface.
- Move file/job storage behind a repository abstraction.
- Delete unreachable pipelines, local Explorer routes, KB autosave, and absent `mobile.de` support from the demo.
- Consolidate shared scraper behavior.

---

### 8. Scraping and ingestion remain fragile

**Severity: Important · Effort: Medium**

What is wrong:

There are three large standalone scraper scripts with duplicated network, image-download, and filesystem behavior. There are no scraper fixture tests. Dependencies are unpinned. Image uploads are checked by extension, not by actual content.

Evidence:

- `Autobazar_eu.py`, `Autobazar_sk.py`, and `Bazos.py` total roughly 1,500 lines.
- No tests exercise their selectors against stored HTML fixtures.
- [`requirements.txt`](<D:/VS Projekty/Scrapper - DEMO/requirements.txt:1>) contains no versions.
- Manual upload validation in [`_create_manual_listing_from_form()`](<D:/VS Projekty/Scrapper - DEMO/web_server.py:944>) trusts the filename extension.
- The live specification-parser delimiter bug shows ingestion defects propagate into the product.

Why it matters:

A marketplace markup change can silently degrade the report. Without fixtures, failures will be discovered by users. Unpinned dependencies make deployments non-reproducible.

Concrete improvement:

- Add captured HTML fixtures and expected normalized listing objects for every marketplace.
- Create a common scraper result model.
- Validate uploaded images by decoding them before storage.
- Set per-image byte and pixel limits.
- Pin dependencies and introduce automated CI.
- Monitor extraction completeness and reject suspiciously incomplete scrapes.

---

### 9. The differentiation is not yet compelling

**Severity: Important · Effort: Medium**

What is wrong:

The product does not verify vehicle history, legal status, real mileage, accident records, servicing, or the physical condition of the car. It repeatedly tells the user to purchase Cebia/carVertical and arrange a physical inspection. Those services already own the highest-value evidence.

Checkni Auto’s genuinely differentiated capability is narrower: localized listing triage that combines seller claims, model-specific inspection points, visible photo observations, and questions for the seller.

Why it matters:

Calling it an “AI car check before buying” suggests more certainty than it provides. Users may treat it as a substitute for history data or inspection when it is really a screening assistant.

Concrete improvement:

Position it as:

> “A pre-screening assistant that helps you decide whether a listing is worth a VIN report and physical inspection.”

Then integrate or hand off clearly to structured history and inspection services. Do not compete on claims they can verify and you cannot.

---

### 10. Important user expectations are not addressed in the interface

**Severity: Important · Effort: Medium**

What is wrong:

The landing page does not disclose:

- That uploaded content becomes publicly visible.
- How long it is retained.
- That AI output may be wrong.
- That the service is not a vehicle-history check or inspection.
- Which evidence is verified.
- Approximate analysis duration.
- Why the rate limit or global concurrency rejection exists.

The URL, price, and manual-text fields also lack useful client-side required validation. Some English UI strings remain Slovak, and several failure/cancellation messages are hard-coded outside the translation table.

Concrete improvement:

Add clear pre-submit expectations, evidence labels, privacy terms, retention, latency estimate, and a concise limitation statement. Make validation immediate and make the language implementation complete.

## Features to remove, simplify, or postpone

Remove from the public deployment:

- Token dashboard and raw artifacts.
- Global “previous analyses” list.
- Private CRUD and knowledge-base routes.
- KB autosave.
- Local folder-opening route.
- Legacy prompt artifact.
- Unsupported `mobile.de` claims and paths.

Simplify:

- Use one LLM provider until quality is measured.
- Reduce the report to summary, evidence, missing facts, and inspection checklist.
- Replace forced 4–8 item sections with only supported findings.
- Replace simulated percentage progress with named stages and elapsed time.

Postpone:

- PDF export.
- Public sharing beyond explicit private share links.
- Multiple provider routing.
- Knowledge-base generation.
- “Fair price” verdicts until exact comparable capture is reliable.
- Categorical buy/no-buy scoring until calibrated.

## Assumptions that appear wrong

- Grounded search results are equivalent to verified evidence.
- More report sections create more value.
- Users want every generic failure mode for a model.
- Photo wear can meaningfully validate mileage.
- A deterministic formula automatically makes a verdict objective.
- Missing seller metadata should worsen the vehicle score.
- Users will accept three-minute analyses and global single-job capacity.
- Temporary filesystem data can be presented as “saved analyses.”
- Users understand that manual uploads are public.
- Exact-looking cost estimates are useful even when their basis is weak.

## Things that look finished but are incomplete

- **Risk scoring:** deterministic but uncalibrated and currently misreads positive vision observations.
- **Validation:** schema-shaped, but not full schema enforcement and not blocking.
- **Cancellation:** stops the browser stream, not necessarily the scraper/model work.
- **Saved analyses:** temporary and globally public.
- **Bilingual UI:** incomplete translation coverage.
- **Market comparison:** exact-looking but not backed by exact comparable URLs.
- **VIN handling:** placeholder `"N/A"` is decoded as a malformed VIN.
- **Demo isolation:** route-gated, but private and legacy functionality remains in the same application.
- **Testing:** 58 passing tests, but no scraper fixtures, browser E2E tests, privacy tests, concurrency tests, or report-accuracy evaluation.

## Overengineering

The project is overengineered around managing LLM instability:

- Large regex-based report normalizer.
- Prompt constraints specifying exact section counts and shapes.
- Multiple provider fallbacks.
- Collage and overview-sheet machinery.
- Raw artifact renderer and public observability UI.
- Dual private/public product in one 4,499-line server.
- Post-generation rewriting of photo sections.

Some of this work is technically competent, but it is solving the wrong layer. The priority should be evidence integrity, user privacy, and a smaller report—not increasingly elaborate control over generated prose.

## Future problems created by the current implementation

- Markdown and folders acting as a database will become difficult to migrate and query.
- Human-readable slugs will collide and are unsuitable authorization identifiers.
- In-memory state prevents horizontal scaling.
- Provider-specific logic is intertwined with orchestration.
- Scraper drift can silently poison every downstream phase.
- Public raw artifacts create permanent pressure against changing prompts and schemas.
- Adding more scoring rules will create an opaque pseudo-scientific model.
- Regex-based normalization will accumulate exceptions instead of improving upstream contracts.

## Three strongest parts

1. **Pipeline traceability.** The separation of research, vision, scoring, raw synthesis, and public output makes failures inspectable. This is genuinely useful engineering, but the artifacts must be private.

2. **Intent to separate evidence from verdict.** A backend-controlled risk layer is the right instinct. The current implementation needs redesign and calibration, but the architectural goal is better than letting an LLM freely invent the final rating.

3. **Practical localized workflow.** Slovak/Czech marketplace ingestion, manual fallback, photo analysis, and seller questions address an actual regional buyer workflow rather than being a generic chatbot wrapper.

## Three weakest parts

1. Evidence quality and report trustworthiness.
2. Public-data/privacy architecture.
3. Runtime scalability and maintainability.

## Unverified assumptions and unanswered questions

I could not verify:

- The visual design, responsive behavior, keyboard flow, or real click interaction because the interactive browser was unavailable.
- Real user demand, retention, conversion, or willingness to pay; no analytics or research evidence is present.
- Accuracy against mechanic-reviewed vehicles or known post-purchase outcomes.
- The exact existence of the four market comparables at generation time; only a category URL is retained.
- Marketplace scraping terms, image-republishing rights, or data-protection compliance.
- Production environment variables beyond what public endpoints reveal.
- Behavior under real concurrent load or deployment restarts.

Important product questions still unanswered:

- Is this a free lead-generation tool, a paid report, or a companion to an inspection service?
- Who is the primary user: first-time buyer, enthusiast, dealer, or inspection professional?
- What outcome defines correctness: avoiding bad cars, saving inspection costs, improving negotiation, or increasing user confidence?
- Will you integrate licensed history data, or remain a pre-screening product?

## Honest overall assessment

| Area | Score | Assessment |
|---|---:|---|
| Product idea | 7/10 | Used-car buyers have a real information and confidence problem. Listing triage is useful, but the product must be positioned below VIN history and physical inspection. |
| Current execution | 4/10 | It completes an impressive end-to-end flow, but the live output exposes factual, scoring, privacy, and scope problems. |
| User experience | 4/10 | Input is simple and the summary is useful, but the process is slow, the report is repetitive, evidence is hidden, and privacy expectations are violated. |
| Technical quality | 3/10 | There are useful tests and careful artifact separation, but the monolithic server, soft validation, fragile storage, duplicated scrapers, dead code, and in-memory state dominate. |
| Production readiness | 2/10 | Public private-data exposure, one-job capacity, ephemeral storage, uncalibrated verdicts, and missing operational architecture prevent responsible production use. |

## Highest-priority improvements

1. **Make every analysis private by default.** Add ownership, random IDs, explicit share tokens, protected operations tooling, and clear retention rules.

2. **Build a verifiable evidence chain.** Exact source records, visible citations, source-quality labels, exact market comparables, and fail-closed behavior for unsupported claims.

3. **Replace or recalibrate the verdict system.** Fix observation semantics, stop scoring generic model risks as vehicle defects, and validate against expert-reviewed cases.

4. **Move analysis into durable background jobs.** Queue, database, object storage, resumable progress, reliable cancellation, and distributed limits.

5. **Narrow the product and codebase.** One provider, fewer supported marketplaces initially, a much shorter report, no public debug surface, and no private/legacy/KB code in the demo application.

## Recommended next iteration

The next iteration should be a focused private pre-screening tool.

It should contain:

- One or two well-tested marketplaces plus manual input.
- Private anonymous jobs with expiring explicit share links.
- A structured facts section separating seller claims from verified facts.
- At most three source-backed model inspection points.
- Photo observations that never imply hidden condition or mileage validation.
- A missing-information checklist.
- Exact comparable links or no price judgment.
- A concise seller/inspection checklist.
- Visible evidence and confidence for every meaningful conclusion.
- Background processing with accurate stage progress.
- Full schema enforcement and production-failure regression fixtures.
- A reviewed evaluation dataset with measurable accuracy, latency, and cost.

It should exclude:

- Public raw artifacts and token telemetry.
- Global saved-analysis browsing.
- KB generation/autosave.
- Multiple model providers.
- Long-form “premium” reports.
- Unsupported precise market ranges.
- PDF export until the content is trustworthy.
- A categorical buying verdict until it is calibrated.

Success should look like:

- Zero cross-user data exposure.
- At least 95% of high-impact claims linked to valid evidence.
- No invented or category-level market comparables.
- Median completion under 60 seconds and p95 under 120 seconds.
- Materially lower cost per analysis.
- Low false-positive red flags on an expert-reviewed listing set.
- Users can state the three next actions after reading the first screen.
- The product consistently helps users decide whether to reject the listing, request more information, purchase a history report, or arrange an inspection.

No code was changed. All 58 tests passed; Git still reports the pre-existing modification to `token_tracker.py`.