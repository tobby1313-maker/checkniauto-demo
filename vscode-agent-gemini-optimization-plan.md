# VS Code Agent Task: Reduce Gemini Cost and Token Usage

## Project context

This project analyzes used-car advertisements. The current Gemini pipeline has several phases:

- `component_identity_grounding`
- `grounding`
- two `text_research` calls
- `vision`
- `final_synthesis`

A recent analysis cost about **€0.275**. The two research calls generated almost **13,000 output tokens** and represented roughly **55% of total cost**. The final synthesis received about **15,000 input tokens**.

The goal is to keep Gemini, preserve or improve report quality, and reduce average cost substantially.

## Primary targets

Implement the following measurable targets:

- Cache miss analysis: **≤ €0.12**
- Cache hit analysis: **≤ €0.07**
- Research output: **≤ 2,000 tokens combined**
- Final synthesis input: **≤ 9,000 tokens**
- Final synthesis output: **≤ 2,400 tokens**
- No duplicate research calls for the same vehicle components
- Correct logging of input, visible output, thinking, cached and total tokens
- No double billing or duplicate jobs caused by retries
- Existing user-facing report must remain functional

Do not silently remove useful report content. Render repeated headings, labels and boilerplate in the frontend instead of asking the model to generate them.

---

# Implementation instructions

## 1. Audit the existing pipeline first

Before changing code:

1. Locate every Gemini call.
2. Create a table in `docs/gemini-cost-audit.md` containing:
   - phase name
   - model
   - prompt source
   - input data
   - output destination
   - max output tokens
   - thinking configuration
   - retry behavior
   - whether grounding is enabled
   - whether the result is cached
3. Identify:
   - duplicated prompts
   - duplicated advertisement data
   - duplicated research data
   - full raw outputs passed into later calls
   - calls that can use deterministic code instead of AI
4. Preserve current behavior until the optimized pipeline is covered by tests.

Do not guess the framework or database. Inspect the repository and follow its existing conventions.

---

## 2. Add centralized Gemini configuration

Create one central module for all Gemini calls, for example:

```text
src/lib/ai/gemini/
  client.ts
  models.ts
  budgets.ts
  usage.ts
  schemas.ts
  errors.ts
```

Adapt the paths to the existing project structure.

Define phase-level configuration in one place:

```ts
export const AI_PHASES = {
  componentIdentity: {
    model: process.env.GEMINI_EXTRACTION_MODEL,
    maxInputTokens: 5_000,
    maxOutputTokens: 500,
    thinkingMode: "off",
  },
  researchNormalization: {
    model: process.env.GEMINI_RESEARCH_MODEL,
    maxInputTokens: 12_000,
    maxOutputTokens: 1_800,
    thinkingMode: "off",
  },
  vision: {
    model: process.env.GEMINI_VISION_MODEL,
    maxInputTokens: 5_000,
    maxOutputTokens: 900,
    thinkingMode: "off",
  },
  finalSynthesis: {
    model: process.env.GEMINI_FINAL_MODEL,
    maxInputTokens: 9_000,
    maxOutputTokens: 2_200,
    thinkingMode: "minimal",
  },
} as const;
```

Use environment variables for model names. Do not hard-code preview model names throughout the application.

Add example values to `.env.example`, but never expose API keys.

---

## 3. Correct token and cost logging

The current dashboard appears to report zero thinking tokens. Fix usage logging.

Store, when available:

- prompt/input tokens
- visible candidate/output tokens
- thinking tokens
- cached input tokens
- total tokens
- grounding/search charges
- model name
- phase
- request duration
- retry count
- cache hit or miss
- estimated cost
- provider request ID
- status and error category

Create a normalized type:

```ts
type AiUsage = {
  inputTokens: number;
  visibleOutputTokens: number;
  thinkingTokens: number;
  cachedInputTokens: number;
  totalTokens: number;
};
```

Read the actual fields returned by the installed Google SDK version. Do not assume field names without checking SDK types and runtime responses.

Update cost calculation so billable output includes thinking tokens when applicable.

Add tests for:

- missing usage metadata
- zero thinking
- non-zero thinking
- cached tokens
- retries
- failed calls
- rate-limited calls

Do not count a failed request as a completed analysis, but record any real provider cost if the API reports usage.

---

## 4. Add hard token budgets before every call

Use Gemini token counting, or the SDK-supported equivalent, before sending large requests.

Create a reusable helper:

```ts
assertWithinTokenBudget({
  phase,
  contents,
  maxInputTokens,
});
```

Behavior:

- If under budget, continue.
- If over budget, compact or truncate low-priority data.
- If still over budget, fail with a descriptive internal error.
- Never silently send an unbounded prompt.

Compaction priority:

1. Remove duplicated instructions.
2. Remove duplicate advertisement text.
3. Replace raw research pages with normalized facts.
4. Remove low-quality or repeated sources.
5. Remove duplicate images.
6. Shorten long seller descriptions while preserving claims and inconsistencies.
7. Preserve VIN, prices, mileage, dates, engine codes and evidence references.

Log both pre-compaction and post-compaction token counts.

---

## 5. Replace verbose intermediate prose with structured JSON

Intermediate calls must not produce buyer-facing reports.

Create strict schemas for:

### Component identity

```ts
type ComponentIdentity = {
  make: string;
  model: string;
  generation: string | null;
  year: number | null;
  engineFamily: string | null;
  engineCodeCandidates: Array<{
    code: string;
    confidence: number;
  }>;
  transmissionFamily: string | null;
  drivetrain: string | null;
  conflicts: string[];
  unknowns: string[];
  confidence: number;
};
```

### Research packet

```ts
type ResearchPacket = {
  identity: ComponentIdentity;
  engineRisks: RiskFact[];
  transmissionRisks: RiskFact[];
  recallFacts: RecallFact[];
  maintenanceFacts: MaintenanceFact[];
  inspectionChecks: InspectionCheck[];
  unknowns: string[];
  sources: SourceReference[];
};
```

### Vision packet

```ts
type VisionPacket = {
  findings: Array<{
    imageId: string;
    category: string;
    severity: "low" | "medium" | "high";
    observation: string;
    confidence: number;
    needsHighResolutionReview: boolean;
  }>;
  missingUsefulViews: string[];
  limitations: string[];
};
```

Use Gemini structured output / JSON schema support where available.

Reject invalid output, retry once with a repair prompt, and then fail gracefully. Do not repeatedly retry indefinitely.

---

## 6. Merge the two text research calls

Replace the two existing `text_research` calls with this flow:

1. Gather search and grounding results.
2. Deduplicate sources in application code.
3. Normalize all evidence in one model call.
4. Return one compact `ResearchPacket`.
5. Limit output to approximately 1,800 tokens.

The research prompt must explicitly state:

- Return facts, not a polished report.
- Maximum 8 major risks total unless evidence strongly requires more.
- Do not repeat the same problem under multiple headings.
- Preserve source IDs.
- Distinguish confirmed facts, probable identity and general model risk.
- Do not invent repair prices.
- Mark unsupported claims as unknown.
- Keep summaries concise.

Delete or disable the second research call after parity tests pass.

---

## 7. Introduce a component research cache

Create a database-backed cache for reusable engine, transmission and recall research.

Suggested entity:

```ts
type ComponentResearchCache = {
  id: string;
  componentKey: string;
  identityJson: unknown;
  researchJson: unknown;
  sourceJson: unknown;
  confidence: number;
  schemaVersion: number;
  createdAt: Date;
  updatedAt: Date;
  expiresAt: Date;
};
```

Generate a normalized key such as:

```text
make|model-generation|engine-family|engine-code-or-unknown|transmission|drivetrain
```

Requirements:

- Normalize whitespace, casing and punctuation.
- Include generation when known.
- Do not merge materially different engines.
- If exact engine code is unknown, use a lower-confidence generic key.
- Store schema version.
- Add expiry, initially 90 days.
- Allow manual invalidation.
- Record cache hit/miss in API-call logs.
- Do not cache listing-specific price, mileage, seller claims or photo findings.

Flow:

```text
identify components
→ calculate component key
→ check cache
→ cache hit: reuse research
→ cache miss: run grounding/research and save result
```

Prevent concurrent cache misses from producing duplicate work. Use a database lock, unique constraint, job deduplication key or equivalent mechanism.

---

## 8. Reduce grounding calls

Grounding should run only when:

- no valid component cache exists
- identity confidence is below the chosen threshold
- advertisement specifications conflict
- VIN-derived data conflicts with the listing
- cached research is stale
- a specific official recall check is required

Try to replace separate identity-grounding and general-grounding calls with one tightly scoped grounded task where possible.

Do not run grounding merely to confirm data already verified by VIN or a current cache entry.

Add a feature flag:

```text
AI_OPTIMIZED_GROUNDING=true
```

Keep the old path temporarily available for comparison.

---

## 9. Optimize image processing

Before sending images to Gemini:

1. Deduplicate identical or near-identical images.
2. Remove logos, financing banners, maps and irrelevant graphics.
3. Prefer:
   - front three-quarter
   - rear three-quarter
   - both sides
   - dashboard
   - steering wheel/interior
   - engine bay
   - visible damage
4. Limit the first pass to a configurable maximum, initially 8 images.
5. Use low or medium media resolution for the first pass.
6. Run high-resolution review only for images marked suspicious.

Add image metadata:

```ts
type AnalysisImage = {
  id: string;
  sourceUrl: string;
  hash: string;
  width: number;
  height: number;
  selectedForVision: boolean;
  selectionReason: string;
};
```

Do not let the model repeatedly analyze thumbnails and full-size copies of the same photo.

---

## 10. Build a compact final evidence packet

The final synthesis call must receive only normalized data.

Create:

```ts
type FinalEvidencePacket = {
  vehicle: NormalizedVehicle;
  listingFacts: ListingFacts;
  listingConflicts: ListingConflict[];
  research: ResearchPacket;
  vision: VisionPacket;
  marketData: MarketData | null;
  sourceIndex: SourceReference[];
  analysisLimitations: string[];
};
```

Do not include:

- complete raw web pages
- both raw and normalized research
- duplicate listing text
- full intermediate model conversations
- repeated system instructions
- HTML templates
- static Slovak labels

Before the final call:

- serialize the packet
- count tokens
- compact if necessary
- ensure the final input is no more than 9,000 tokens

The final model should generate structured report content, not HTML.

---

## 11. Move static report content into frontend templates

Render these in application code:

- section headings
- severity labels
- “verified from listing” labels
- generic disclaimers
- score explanations
- standard inspection instructions
- table layout
- buttons
- Slovak and English UI strings

The model should generate only vehicle-specific content.

Create frontend translation dictionaries for Slovak and English. Keep technical data language-neutral where possible.

The final report schema should contain fields such as:

```ts
type FinalReport = {
  score: number;
  verdict: "recommended" | "caution" | "avoid";
  confidence: number;
  verdictSummary: string;
  mainRisks: ReportRisk[];
  positiveSignals: string[];
  expectedCosts: CostEstimate[];
  sellerQuestions: string[];
  inspectionChecklist: string[];
  negotiationAdvice: string | null;
  limitations: string[];
};
```

Validate score ranges and required fields in application code.

---

## 12. Configure thinking conservatively

Use the installed Google SDK's supported configuration.

Desired behavior:

- component extraction: thinking off
- research normalization: thinking off
- vision first pass: thinking off
- final synthesis: minimal thinking
- escalation path: low thinking only for uncertain cases

Create an escalation condition, for example:

```ts
const needsEscalation =
  identity.confidence < 0.7 ||
  evidenceHasMaterialConflicts ||
  finalValidationFailed;
```

Do not use higher thinking automatically for every report.

Log the configured thinking mode and actual thinking-token usage.

---

## 13. Add robust retries and idempotency

Requirements:

- Exponential backoff for rate limits and temporary provider failures.
- Maximum retry count per phase.
- Do not retry validation or permanent input errors.
- Use a stable analysis job ID.
- Make each phase idempotent.
- Do not execute the same successful phase twice after a client refresh.
- Persist phase status:
  - pending
  - running
  - succeeded
  - failed
  - skipped_cache_hit
- Reuse completed phase output when retrying the overall job.
- Track rate-limit errors separately.

Do not charge or consume an additional user credit because of an internal retry.

---

## 14. Add feature flags and safe rollout

Add configurable flags:

```text
AI_OPTIMIZED_PIPELINE=false
AI_USE_COMPONENT_CACHE=false
AI_MERGED_RESEARCH=false
AI_LOW_RES_VISION=false
AI_FINAL_MINIMAL_THINKING=false
```

Allow old and new pipelines to run side-by-side in non-production.

Add shadow mode:

- User receives the existing report.
- New pipeline runs for selected internal test cases.
- Compare cost, latency and output quality.
- Do not double charge the user.

After validation, gradually enable the optimized pipeline.

---

## 15. Build an evaluation harness

Use at least 20 existing completed advertisements.

Create a command such as:

```text
npm run eval:ai
```

or the project-equivalent command.

For each test case record:

- total cost
- total latency
- call count
- input tokens
- visible output tokens
- thinking tokens
- cache status
- valid JSON status
- detected vehicle identity
- number of unsupported claims
- final report completeness
- Slovak language quality
- source coverage

Produce a machine-readable JSON result and a Markdown summary.

Do not expose production customer data in fixtures. Anonymize test data.

---

## 16. Add automated tests

At minimum add tests for:

### Unit tests

- component-key normalization
- token-budget enforcement
- evidence compaction
- source deduplication
- usage parsing
- cost calculation
- report schema validation
- image deduplication
- cache expiry
- escalation logic

### Integration tests

- cache miss pipeline
- cache hit pipeline
- rate-limited Gemini call
- malformed structured output
- final synthesis retry
- vision disabled/no images
- conflicting listing facts
- failed analysis does not consume an extra credit

Mock Gemini in normal test runs. Keep live-provider tests optional and disabled by default.

---

## 17. Update the internal API-call dashboard

Add filters and summaries for:

- phase
- model
- date range
- cache hit/miss
- status
- optimized/legacy pipeline
- thinking mode

Show per-analysis totals:

- cost
- duration
- input
- visible output
- thinking
- cached tokens
- grounding cost
- retries
- number of calls

Add warnings when:

- research output exceeds 2,000 tokens
- final input exceeds 9,000 tokens
- thinking unexpectedly exceeds a threshold
- the same component is researched repeatedly
- a phase runs more than once for one analysis

---

# Suggested implementation order

Implement in small, reviewable stages.

## Stage 1 — Observability

- Audit calls
- Fix usage logging
- Add token counting
- Add phase budgets
- Add dashboard fields
- No behavior change yet

## Stage 2 — Output reduction

- Add structured schemas
- Limit intermediate outputs
- Merge research calls
- Build compact final evidence packet
- Move static report text to frontend

## Stage 3 — Caching

- Add component research cache
- Add concurrency protection
- Skip grounding/research on cache hit
- Add cache administration

## Stage 4 — Vision optimization

- Deduplicate images
- Select relevant images
- Low-resolution first pass
- High-resolution escalation

## Stage 5 — Thinking and model routing

- Disable thinking where unnecessary
- Minimal thinking for final synthesis
- Move low-complexity calls to the configured cheaper Gemini model
- Add uncertainty-based escalation

## Stage 6 — Evaluation and rollout

- Run the evaluation suite
- Compare legacy and optimized pipelines
- Fix quality regressions
- Enable feature flags gradually
- Remove obsolete code only after production validation

---

# Definition of done

The task is complete when:

1. All Gemini calls go through one centralized client.
2. Usage logging includes thinking and cached tokens when returned by the SDK.
3. Intermediate responses use validated structured JSON.
4. The two research calls are replaced by one compact normalization call.
5. Component research is reused through a database cache.
6. Final synthesis receives no more than 9,000 input tokens in normal cases.
7. Research output is no more than 2,000 tokens in normal cases.
8. Static report content is rendered by the frontend.
9. Image inputs are deduplicated and capped.
10. Retries are idempotent and do not consume extra user credits.
11. Legacy and optimized pipelines can be compared behind feature flags.
12. Evaluation results demonstrate:
    - no material loss in report quality
    - cache-miss cost at or below €0.12 for the test set median
    - cache-hit cost at or below €0.07 for the test set median
13. Documentation explains:
    - architecture
    - environment variables
    - database migration
    - cache invalidation
    - rollout
    - rollback
    - how to run evaluation

---

# Agent operating rules

- Inspect the repository before proposing file paths.
- Follow existing code style and architecture.
- Do not rewrite unrelated parts of the application.
- Do not expose secrets.
- Do not remove the legacy pipeline until the optimized path is verified.
- Do not trust model output without schema validation.
- Do not invent Gemini SDK fields; inspect installed types and official SDK behavior.
- Preserve Slovak report quality and technical values.
- Make migrations reversible where the project supports reversible migrations.
- After each stage, run linting, type checks and tests.
- Summarize changed files, migrations, environment variables and remaining risks.
