# Tailoring Plan Analysis: Multi-Model Architecture for `Scrapper - DEMO/`

After reviewing both `plan.md` and the current `Scrapper - DEMO/` codebase, here's my analysis of the gaps and how to adapt the plan to your existing project.

## Current State vs. Plan.md

| Aspect | plan.md (Proposed) | `Scrapper - DEMO/` (Current) |
|---|---|---|
| **Language** | TypeScript (Node.js) | **Python** (Flask web server) |
| **Text Analysis** | Grok Call 1 → `grok_research.json` | Single Gemini call does everything: text + vision + scoring + report |
| **Vision Analysis** | Gemini 2.5 → `gemini_vision.json` | Same Gemini call handles images inline |
| **Risk Scoring** | Backend deterministic code | LLM calculates its own risk score internally |
| **Final Report** | Grok Call 2 (tools OFF) | Same Gemini call generates the report |
| **JSON Schemas** | Separate schema files + validation | No schema files; markdown output only |
| **Overrides** | Conservative override rules in backend | No overrides; LLM decides final verdict |
| **Forbidden phrases** | Backend checks after generation | Not implemented |
| **Error handling** | Graceful degradation per model | Single point of failure |
| **Output format** | Structured JSON pipeline → Final MD report | Direct markdown output from single LLM call |

## What You Already Have (Reusable)

- **Scrapers**: `Autobazar_eu.py`, `Autobazar_sk.py`, `Bazos.py` — can stay as-is
- **`main.py`**: Orchestration + `build_analysis_request()` — can be extended
- **`llm_client.py`**: `_call_gemini()` with streaming, `run_grounded_web_research()` — reusable core
- **`vin_utils.py`**: VIN validation — already done
- **`analysis_normalizer.py`**: Post-processing clean-up — can be extended for validation
- **`token_tracker.py`**: Token tracking — already reusable
- **`web_server.py`**: REST API + SSE streaming — the orchestration layer lives here
- **Knowledge base**: Already has the KB matching workflow in `main.py`

## Tailored Architecture for `Scrapper - DEMO/` (Python)

Instead of the TypeScript architecture in plan.md, here's how it maps to your existing Python codebase:

```text
┌─────────────────────────────────────────────────────────┐
│                   web_server.py                          │
│  Receives URL → Runs scraper → Triggers analysis         │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               new: text_analyzer.py                       │
│  Grok Call 1 (or Gemini) — Text + Research only           │
│  Output: structured_json/text_research.json                │
│  Prompt: prompts/grok_text_research_system.md              │
│  Schema: schemas/grok_research.schema.json                 │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               new: vision_analyzer.py                     │
│  Gemini 2.5 Flash — Vision only                           │
│  Output: structured_json/vision_results.json               │
│  Prompt: prompts/gemini_vision_system.md                   │
│  Schema: schemas/gemini_vision.schema.json                 │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               new: risk_scorer.py                         │
│  Backend deterministic scoring                            │
│  No LLM calls — pure Python logic                         │
│  Input: both JSON outputs + listing data                  │
│  Output: structured_json/risk_score.json                  │
│  Implements: all risk rules + conservative overrides      │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               new: final_synthesis.py                     │
│  Grok Call 2 (or Gemini) — tools OFF                     │
│  Input: all 3 JSON files + original data                  │
│  Output: Final Slovak buyer report (markdown)             │
│  Prompt: prompts/final_synthesis_system.md                 │
│  Must NOT change backend verdict                          │
└─────────────────────┬───────────────────────────────────┘
                      │
                      ▼
┌─────────────────────────────────────────────────────────┐
│               new: output_validator.py                    │
│  Forbidden phrase check                                   │
│  Verdict lock check                                       │
│  Schema validation                                        │
└─────────────────────────────────────────────────────────┘
```

## New Files to Create

### 1. `prompts/grok_text_research_system.md`
Adapt from §6 of plan.md — Grok Call 1 system prompt. Since you use Gemini, this maps to a Gemini call with the "text + research only" prompt.

### 2. `prompts/gemini_vision_system.md`
Adapt from §8 of plan.md — already Gemini, just need to separate vision from text. This would be a textless Gemini call with only image attachments.

### 3. `prompts/final_synthesis_system.md`
Adapt from §12 of plan.md — final report generator with tools disabled.

### 4. `schemas/` folder with JSON schema files
- `grok_research.schema.json`
- `gemini_vision.schema.json`
- `risk_score.schema.json`
- `final_report.schema.json`

### 5. `src/text_analyzer.py` (or just flat at project root)
- `call_text_research()` — calls Gemini with text + research prompt, returns JSON
- Could optionally keep `run_grounded_web_research()` before it

### 6. `src/vision_analyzer.py`
- `call_vision_analysis()` — calls Gemini with vision prompt + collage images only, returns JSON

### 7. `src/risk_scorer.py`
- `calculate_risk_score()` — pure Python, no LLM
- Implements all risk rules from §10
- Implements conservative overrides from §10

### 8. `src/final_synthesis.py`
- `call_final_synthesis()` — calls Gemini with all 3 JSON inputs, no tools, no images

### 9. `src/output_validator.py`
- `validate_json()` — Pydantic or manual schema validation
- `validate_final_report()` — checks verdict lock, forbidden phrases, `<!-- END_ANALYSIS -->`

## Modifications to Existing Files

### `llm_client.py`
- Add a function variant that only sends text (no images) for text_analyzer
- Add a function variant that only sends images (minimal text) for vision_analyzer
- The `_call_gemini()` function is already reusable; just need different prompt + content combinations

### `main.py`
- Replace `build_analysis_request()` with the new 4-stage orchestration
- Add `analyze_with_pipeline()` function that runs all 4 stages
- Update `derive_slug()` to use `SCRAPPER_AUTA_DIR`

### `web_server.py`
- Update `/api/analyze/<slug>` endpoint to use the new 4-stage pipeline
- Update `/api/demo/analyze` and `/api/demo/analyze-manual` similarly
- Add streaming that yields progress from each stage
- Add endpoints for intermediate JSON outputs

### `analysis_normalizer.py`
- Add validation functions for forbidden phrases
- Add markdown-to-JSON extraction for intermediate outputs

## Pipeline Flow (adapted to your Python codebase)

```python
# New orchestration in web_server.py or a new analyze.py
async def analyze_used_car(slug_dir, api_key):
    # 1. Text + Research (Gemini, no images)
    research_result = call_text_research(api_key, slug_dir)
    # 2. Vision (Gemini, images only)
    vision_result = call_vision_analysis(api_key, slug_dir)
    # 3. Backend scoring (pure Python)
    risk_score = calculate_risk_score(research_result, vision_result)
    # 4. Final synthesis (Gemini, no tools, no images)
    final_report = call_final_synthesis(api_key, research_result, vision_result, risk_score)
    # 5. Validate output
    validate_final_report(final_report, risk_score)
    return final_report
```

## What Stays Unchanged

- **Scrapers** (`Autobazar_eu.py`, `Autobazar_sk.py`, `Bazos.py`) — no changes needed
- **`vin_utils.py`** + **`vin_data.py`** — already work
- **`token_tracker.py`** — already works
- **`requirements.txt`** — no new dependencies (all pure Python)
- **Knowledge base structure** — stays as-is

## Key Differences from plan.md (Adaptations)

1. **No Grok** — you use Gemini for everything, which is fine. The architecture is: **Gemini = text/research** → **Gemini = vision** → **Backend = scoring** → **Gemini = final report** (with different prompts for each call)

2. **No TypeScript** — everything stays Python. The orchestration lives in your Flask web server.

3. **Simpler schemas** — you can use Python's `dataclasses` + `json` for validation instead of JSON Schema files (or add `jsonschema` to requirements if desired)

4. **SSE streaming** — your existing streaming architecture can be extended to yield progress from each stage

---

Would you like me to proceed with implementing any specific part of this adaptation when you switch to ACT mode? I'd suggest starting with one of:

1. **`risk_scorer.py`** — the most impactful and lowest-risk change (pure Python, no API calls)
2. **`text_analyzer.py`** + the separated text-only prompt 
3. **The full pipeline integration** into `web_server.py`

Which approach would you prefer first?