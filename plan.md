# Implementation Plan

## Multi-Model Used-Car Listing Analyzer

### Grok for Text + Research, Gemini 2.5 for Vision, Backend for Scoring, Final Synthesis for Buyer Report

---

# 1. Goal

Build an API-based used-car listing analyzer that produces a careful, buyer-focused Slovak report.

The system should:

* analyze listing text,
* analyze vehicle photos,
* optionally perform web/research checks,
* identify missing or risky information,
* prevent hallucinations,
* calculate a consistent risk score,
* generate a final buyer-facing recommendation.

The system must not invent:

* VIN history,
* accident history,
* service history,
* ownership history,
* market comparisons,
* prices,
* citations,
* visual damage,
* technical issues.

If information is missing or uncertain, the system must clearly mark it as missing, uncertain, unverified, or requiring manual verification.

---

# 2. Recommended Architecture

```text
car_info.md + listing text
        ↓
Grok Call 1: Text + Research Analyzer
        ↓
grok_research.json


photos / photo collages
        ↓
Gemini 2.5 Vision Call
        ↓
gemini_vision.json


grok_research.json
gemini_vision.json
original listing data
knowledge_base data
        ↓
Backend Deterministic Scoring
        ↓
risk_score.json


all structured inputs
risk_score.json
allowed final verdict
        ↓
Grok Call 2: Final Synthesis, tools OFF
        ↓
final Slovak buyer report
```

---

# 3. Model Responsibilities

## 3.1 Grok Call 1: Text + Research Analyzer

Grok Call 1 handles:

* listing fact extraction,
* missing-data detection,
* consistency checks,
* VIN format check,
* knowledge-base matching,
* web/research findings if tools are available,
* market assessment if reliable web results are available,
* text-based risk flags.

Grok Call 1 must not:

* analyze photos,
* invent visual condition,
* invent VIN results,
* invent online comparisons,
* invent service history,
* invent citations,
* make final buying verdict.

Output must be JSON only.

---

## 3.2 Gemini 2.5: Vision Analyzer

Gemini handles only:

* visible exterior condition,
* visible interior condition,
* dashboard/warning lights if readable,
* tyres, wheels, body panels, rust, scratches, dents,
* visual photo limitations,
* visual red flags,
* visible wear vs. claimed mileage.

Gemini must not:

* make final buying verdict,
* estimate market value,
* claim accident history,
* claim hidden defects,
* claim odometer fraud,
* claim service history,
* claim mechanical faults not visible.

Output must be JSON only.

---

## 3.3 Backend: Deterministic Scoring

Backend code calculates:

* risk points,
* final risk score,
* allowed final verdict,
* hard warning flags.

The LLM should not decide the risk score.

The final model may explain the verdict, but may not change it.

---

## 3.4 Grok Call 2: Final Synthesis

Grok Call 2 receives:

* original listing facts,
* Grok research JSON,
* Gemini vision JSON,
* backend risk score,
* backend final verdict,
* missing-data flags,
* knowledge-base findings,
* web/research findings.

Grok Call 2 must:

* write the final Slovak buyer-facing report,
* explain risks clearly,
* preserve uncertainty,
* use only provided information,
* not perform new research,
* not change the backend verdict.

Tools should be disabled for this call.

---

# 4. Folder / File Structure

Recommended project structure:

```text
used-car-analyzer/
│
├── prompts/
│   ├── grok_text_research_system.md
│   ├── gemini_vision_system.md
│   ├── final_synthesis_system.md
│
├── schemas/
│   ├── grok_research.schema.json
│   ├── gemini_vision.schema.json
│   ├── risk_score.schema.json
│   ├── final_report.schema.json
│
├── src/
│   ├── analyzeListing.ts
│   ├── callGrokResearch.ts
│   ├── callGeminiVision.ts
│   ├── calculateRiskScore.ts
│   ├── callFinalSynthesis.ts
│   ├── validateJson.ts
│   ├── normalizeInputs.ts
│   ├── logging.ts
│
├── knowledge_base/
│   ├── engines/
│   ├── transmissions/
│   ├── models/
│   ├── ev_hybrid/
│
├── tests/
│   ├── fixtures/
│   ├── grok_research.test.ts
│   ├── gemini_vision.test.ts
│   ├── risk_score.test.ts
│   ├── final_report.test.ts
│
└── README.md
```

---

# 5. Main API Flow

## Step 1: Receive Input

Input may contain:

```json
{
  "listing_id": "string",
  "car_info_md": "string",
  "listing_url": "string | null",
  "photos": [
    {
      "label": "Foto 01",
      "image_url": "string"
    }
  ],
  "knowledge_base": {
    "engine": {},
    "transmission": {},
    "model": {},
    "ev_hybrid": {}
  },
  "enable_research": true
}
```

---

## Step 2: Normalize Input

Backend should normalize:

* price,
* mileage,
* year,
* VIN,
* fuel type,
* transmission,
* engine string,
* seller type,
* photo labels.

Do not “guess” missing values. Keep unknowns as `null`.

Example normalized object:

```json
{
  "title": "Toyota Corolla 1.8 Hybrid",
  "price_eur": 16500,
  "year": 2020,
  "mileage_km": 98000,
  "engine": "1.8 Hybrid",
  "fuel": "hybrid",
  "transmission": "automatic",
  "drive": null,
  "vin": null,
  "seller": "dealer",
  "service_history": null
}
```

---

# 6. Grok Call 1: Text + Research Prompt

Use this as the system prompt for Grok Call 1.

```text
You are the Text and Research Analyzer for a used-car buyer advisory system.

Your task is to analyze only the listing text, structured car data, knowledge-base data, and available research results.

Do not analyze photos.

Return JSON only.

Your responsibilities:
- Extract listing facts.
- Identify missing or uncertain information.
- Check consistency of listing data.
- Check VIN format if VIN is provided.
- Use knowledge-base data only when it reasonably matches the vehicle.
- Use web/research results only if research tools or research data are actually provided.
- Separate facts, assumptions, estimates, and manual verification items.
- Identify buyer-relevant risks.

Important rules:
- Do not invent information.
- Do not invent VIN history.
- Do not invent market comparisons.
- Do not invent service history.
- Do not invent ownership history.
- Do not invent accident history.
- Do not invent URLs or citations.
- Do not claim online verification unless real research results were returned.
- Do not make the final buying verdict.
- If something is missing, set it to null or mark it as Neuvedené.
- If something requires verification, say Vyžaduje manuálne online overenie.

Return JSON matching the required schema.
```

---

# 7. Grok Research JSON Contract

```json
{
  "source_role": "text_research",
  "listing_facts": {
    "title": "",
    "price": "",
    "year": "",
    "mileage": "",
    "engine": "",
    "power": "",
    "fuel": "",
    "transmission": "",
    "drive": "",
    "vin": "",
    "seller": "",
    "origin_or_country": "",
    "service_history": "",
    "equipment": []
  },
  "missing_or_uncertain_data": [
    {
      "item": "",
      "why_it_matters": "",
      "severity": "low | medium | high"
    }
  ],
  "consistency_checks": [
    {
      "check": "",
      "result": "ok | concern | unknown",
      "explanation": ""
    }
  ],
  "vin_check": {
    "vin_present": false,
    "format_check": "ok | problem | skipped",
    "decoded_information": "",
    "online_history": "verified | not_available | requires_manual_verification",
    "notes": ""
  },
  "knowledge_base_findings": [
    {
      "component": "",
      "risk": "",
      "evidence": "Knowledge base | Všeobecná znalosť | Odhad",
      "confidence": "Vysoká | Stredná | Nízka",
      "notes": ""
    }
  ],
  "web_research_findings": [
    {
      "claim": "",
      "source_url": "",
      "source_type": "market | reliability | VIN | recall | other",
      "confidence": "Vysoká | Stredná | Nízka",
      "notes": ""
    }
  ],
  "market_assessment": {
    "available": false,
    "summary": "Aktuálne porovnanie trhu vyžaduje manuálne online overenie.",
    "limitations": "",
    "price_view": "fair | rather_expensive | rather_cheap | unclear | requires_manual_verification"
  },
  "text_research_risk_flags": [
    {
      "risk": "",
      "why_it_matters_to_buyer": "",
      "evidence": "Inzerát | Knowledge base | Web / Google Search | Všeobecná znalosť | Odhad | Manuálne overiť",
      "confidence": "Vysoká | Stredná | Nízka"
    }
  ]
}
```

---

# 8. Gemini Vision Prompt

Use this as the system prompt for Gemini 2.5 Vision.

```text
You are the Vision Analyzer for a used-car buyer advisory system.

Your task is to analyze only the provided vehicle photos or photo collages.

Return JSON only.

Inspect only what is visible.

You may identify:
- visible exterior damage,
- visible rust or corrosion signs,
- visible paint mismatch,
- visible panel gap concerns,
- visible tyre, wheel, light, glass, bumper, or body-panel issues,
- visible interior wear,
- dashboard warnings if readable,
- whether visible wear appears roughly consistent with the claimed mileage,
- missing photo angles,
- photo quality limitations,
- visual red flags requiring physical inspection.

You must not:
- decide whether the car is a good buy overall,
- estimate market value,
- claim accident history,
- claim hidden corrosion,
- claim odometer fraud,
- claim mechanical faults that are not visible,
- claim service history,
- infer ownership history,
- infer country of origin,
- invent details not visible in the images.

If something is unclear, mark confidence as Nízka.

If photos are not sufficient, say Nedostatočné fotografie.

Return JSON matching the required schema.
```

---

# 9. Gemini Vision JSON Contract

```json
{
  "source_role": "vision",
  "photos_provided": true,
  "photo_limitations": [],
  "exterior_observations": [
    {
      "photo_label": "Foto 01",
      "observation": "",
      "severity": "minor | medium | serious | unknown",
      "confidence": "Vysoká | Stredná | Nízka",
      "buyer_relevance": ""
    }
  ],
  "interior_observations": [
    {
      "photo_label": "Foto 01",
      "observation": "",
      "severity": "minor | medium | serious | unknown",
      "confidence": "Vysoká | Stredná | Nízka",
      "buyer_relevance": ""
    }
  ],
  "dashboard_or_warning_lights": [
    {
      "photo_label": "Foto 01",
      "observation": "",
      "confidence": "Vysoká | Stredná | Nízka",
      "requires_verification": true
    }
  ],
  "visible_red_flags": [
    {
      "photo_label": "Foto 01",
      "red_flag": "",
      "why_it_matters": "",
      "confidence": "Vysoká | Stredná | Nízka"
    }
  ],
  "mileage_wear_consistency": {
    "assessment": "consistent | possibly_inconsistent | cannot_assess",
    "explanation": "",
    "confidence": "Vysoká | Stredná | Nízka"
  },
  "visual_verdict": "Vyzerá vizuálne dobre | Viditeľné drobné nedostatky | Viditeľné riziká | Nedostatočné fotografie | Neisté - vyžaduje fyzickú kontrolu",
  "must_not_infer": [
    "accident history",
    "service history",
    "hidden defects",
    "odometer fraud",
    "market price",
    "overall buying verdict"
  ]
}
```

---

# 10. Backend Risk Scoring

The backend should calculate risk points deterministically.

The model should not calculate or override the score.

## Risk Rules

```json
{
  "risk_rules": [
    {
      "condition": "vin_missing_or_unverifiable",
      "points": 2
    },
    {
      "condition": "obvious_listing_conflict",
      "points": 2
    },
    {
      "condition": "unclear_service_history_for_old_or_high_mileage_car",
      "points": 1
    },
    {
      "condition": "missing_or_weak_photos",
      "points": 1
    },
    {
      "condition": "visible_minor_damage",
      "points": 1
    },
    {
      "condition": "visible_serious_damage_or_red_flags",
      "points": 2
    },
    {
      "condition": "relevant_expensive_known_risk",
      "points": 1
    },
    {
      "condition": "high_confidence_expensive_known_risk",
      "points": 2
    },
    {
      "condition": "price_suspiciously_low_or_high",
      "points": 1
    },
    {
      "condition": "price_suspicious_and_other_risks_exist",
      "points": 2
    },
    {
      "condition": "high_age_or_mileage_expected_service",
      "points": 1
    },
    {
      "condition": "unclear_origin_or_seller_missing_key_info",
      "points": 1
    },
    {
      "condition": "good_documentation_clear_origin_vin_good_photos",
      "points": -1
    },
    {
      "condition": "excellent_documentation_and_low_risk_profile",
      "points": -2
    }
  ]
}
```

## Verdict Mapping

```json
{
  "verdict_mapping": [
    {
      "min": 0,
      "max": 1,
      "verdict": "🟢 DOBRÁ KÚPA"
    },
    {
      "min": 2,
      "max": 3,
      "verdict": "🟡 PRIJATEĽNÁ KÚPA"
    },
    {
      "min": 4,
      "max": 6,
      "verdict": "🟠 ZVÁŽIŤ"
    },
    {
      "min": 7,
      "max": 9,
      "verdict": "🔴 RIZIKOVÁ KÚPA"
    },
    {
      "min": 10,
      "max": 999,
      "verdict": "⛔ EXTRÉMNE RIZIKO"
    }
  ]
}
```

## Conservative Override Rules

Apply these after point calculation:

```text
If VIN is missing and service history is missing, final verdict cannot be better than 🟠 ZVÁŽIŤ.

If VIN is missing and photos are missing or weak, final verdict cannot be better than 🟠 ZVÁŽIŤ.

If serious visible red flags exist, final verdict cannot be better than 🟠 ZVÁŽIŤ.

If there is a major listing contradiction, final verdict cannot be better than 🔴 RIZIKOVÁ KÚPA unless clearly resolved.

If the price is suspiciously low and VIN is missing, final verdict cannot be better than 🔴 RIZIKOVÁ KÚPA.

If no photos are provided, do not allow 🟢 DOBRÁ KÚPA.
```

---

# 11. Risk Score Output Contract

```json
{
  "risk_score": 0,
  "allowed_final_verdict": "🟠 ZVÁŽIŤ",
  "applied_rules": [
    {
      "rule": "vin_missing_or_unverifiable",
      "points": 2,
      "reason": "VIN nebol uvedený."
    }
  ],
  "override_rules_applied": [
    {
      "rule": "VIN missing + service history missing",
      "effect": "Final verdict cannot be better than 🟠 ZVÁŽIŤ."
    }
  ],
  "missing_data_flags": [
    "VIN",
    "service_history",
    "market_comparison"
  ],
  "buyer_priority_checks": [
    "Požiadať predajcu o VIN pred obhliadkou.",
    "Overiť servisnú históriu.",
    "Urobiť fyzickú kontrolu alebo diagnostiku."
  ]
}
```

---

# 12. Final Synthesis Prompt

Use this as the system prompt for Grok Call 2.

Important: tools should be disabled.

```text
You are the Final Synthesis Model for a used-car buyer advisory system.

Your task is to create the final Slovak buyer-facing report by combining only the provided structured inputs.

You will receive:
1. Original listing data
2. Grok text/research JSON
3. Gemini vision JSON
4. Backend-calculated risk score
5. Backend-calculated final verdict
6. Missing-data flags
7. Optional knowledge-base findings
8. Optional web/research findings

Important rules:
- Do not perform new research.
- Do not invent facts.
- Do not add information that is not present in the provided inputs.
- Do not change the backend-calculated final verdict.
- You may explain the verdict, but you may not override it.
- Treat listing data as seller claims, not verified facts.
- Treat Gemini findings as visual observations only.
- Do not turn visual suspicion into confirmed accident history.
- Do not turn general known issues into confirmed defects of this specific car.
- Do not turn an estimate into a market fact.
- If evidence is missing, uncertain, conflicting, or weak, say so clearly.
- Use only URLs already present in the provided research JSON.
- Do not create fake URLs, fake VIN results, fake market comparisons, fake service history, fake ownership history, fake accident history, or fake prices.
- Keep the tone customer-friendly, honest, clear, practical, and not pushy.

Your goal is to help the buyer understand:
- whether the car is worth pursuing,
- what the biggest risks are,
- what is missing,
- what must be verified,
- what to ask the seller,
- what to check during inspection,
- how to think about price and negotiation.

Before writing the final answer, internally check:
- Is every important claim supported by the provided inputs?
- Are estimates clearly marked as estimates?
- Are missing facts clearly marked?
- Is the final verdict consistent with the backend risk score?
- Did I avoid adding new facts?
- Is the answer useful for a real buyer?

Return the final report in Slovak.
```

---

# 13. Final Report Format

```markdown
# 🚗 Analýza: {názov vozidla}

## 📋 Rýchle zhrnutie

- **Hodnotenie:** {backend allowed_final_verdict}
- **Cena:** {férová / skôr drahá / skôr lacná / nejasná} - {1 veta}
- **Najväčšie riziko:** {1 veta}
- **Najväčší plus:** {1 veta}
- **Čo overiť ako prvé:** {1 konkrétna vec}

## 🧾 Dáta z inzerátu

| Položka | Hodnota | Poznámka |
|---|---:|---|
| Cena | {hodnota alebo Neuvedené} | {krátka poznámka} |
| Rok | {hodnota alebo Neuvedené} | {krátka poznámka} |
| Nájazd | {hodnota alebo Neuvedené} | {krátka poznámka} |
| Motor | {hodnota alebo Neuvedené} | {krátka poznámka} |
| Prevodovka/pohon | {hodnota alebo Neuvedené} | {krátka poznámka} |
| VIN | {hodnota alebo Neuvedené} | {krátka poznámka} |

## 🔍 VIN a transparentnosť

{VIN analysis}

## 🌐 Webové overenie

{web research summary or manual verification warning}

## 🔧 Technické riziká modelu a komponentov

{3 to 6 main risks only}

## 💰 Cena a vyjednávanie

{price and negotiation guidance}

## 🛠️ Očakávané náklady na najbližších 30 000 km

| Položka | Prečo | Odhad EUR | Urgentnosť | Dôkaz | Istota |
|---|---|---:|---|---|---|
| ... | ... | ... | ... | ... | ... |

## 📸 Analýza fotografií

{Gemini visual findings only}

## ✅ Klady

- {klad 1}
- {klad 2}
- {klad 3}

## ❌ Zápory / riziká

- {riziko 1}
- {riziko 2}
- {riziko 3}

## ⚠️ Otázky pre predajcu a kontrola pri obhliadke

| Otázka / úkon | Prečo | Dôkaz | Istota |
|---|---|---|---|
| ... | ... | ... | ... |

## 🏁 Záverečné odporúčanie

**{backend allowed_final_verdict}**

{2 až 4 vety. Nepoužívaj nové fakty, ktoré neboli uvedené vyššie.}

<!-- END_ANALYSIS -->
```

---

# 14. Orchestration Pseudocode

```ts
async function analyzeUsedCar(input) {
  const normalizedInput = normalizeInputs(input);

  const grokResearch = await callGrokResearch({
    carInfoMd: input.car_info_md,
    normalizedInput,
    knowledgeBase: input.knowledge_base,
    enableResearch: input.enable_research
  });

  validateJson(grokResearch, "grok_research.schema.json");

  let geminiVision;

  if (input.photos && input.photos.length > 0) {
    geminiVision = await callGeminiVision({
      photos: input.photos,
      listingFacts: grokResearch.listing_facts
    });
  } else {
    geminiVision = createNoPhotosVisionResult();
  }

  validateJson(geminiVision, "gemini_vision.schema.json");

  const riskScore = calculateRiskScore({
    listingFacts: grokResearch.listing_facts,
    grokResearch,
    geminiVision
  });

  validateJson(riskScore, "risk_score.schema.json");

  const finalReport = await callFinalSynthesis({
    originalListingData: normalizedInput,
    grokResearch,
    geminiVision,
    riskScore,
    toolsEnabled: false
  });

  validateFinalReport(finalReport, {
    allowedVerdict: riskScore.allowed_final_verdict
  });

  return {
    listing_id: input.listing_id,
    grok_research: grokResearch,
    gemini_vision: geminiVision,
    risk_score: riskScore,
    final_report: finalReport
  };
}
```

---

# 15. Validation Rules

## JSON Validation

Every model output must pass schema validation.

If JSON is invalid:

1. retry once with a repair prompt,
2. if still invalid, fail gracefully,
3. return an internal error message,
4. do not generate final buyer report from invalid data.

---

## Final Report Validation

Before returning the final report, backend should check:

* final verdict matches backend verdict,
* report contains `<!-- END_ANALYSIS -->`,
* no unsupported URLs appear,
* no forbidden phrases appear,
* VIN section does not claim online verification unless web data exists,
* photo section does not claim hidden mechanical or accident history,
* price section does not claim market comparison unless research exists.

---

# 16. Forbidden Output Checks

Backend should flag or reject final output containing phrases like:

```text
VIN bol overený online
vozidlo nebolo havarované
vozidlo bolo havarované
kilometre sú určite pravé
kilometre sú stočené
servisná história je potvrdená
najlacnejšie na trhu
najlepšia ponuka na trhu
garantovaná kúpa
bez rizika
určite odporúčam kúpiť
```

These may be allowed only if directly supported by verified input, but safest production behavior is to block or rewrite them.

---

# 17. Error Handling

## If Grok research fails

Return:

```json
{
  "status": "partial_analysis",
  "message": "Textová alebo výskumná analýza zlyhala. Nie je možné vytvoriť spoľahlivé finálne odporúčanie.",
  "available_outputs": {
    "gemini_vision": {}
  }
}
```

## If Gemini vision fails

Continue without photo analysis.

Set:

```json
{
  "photos_provided": false,
  "visual_verdict": "Nedostatočné fotografie",
  "photo_limitations": ["Fotografie sa nepodarilo spoľahlivo analyzovať."]
}
```

Final verdict should not be better than:

```text
🟡 PRIJATEĽNÁ KÚPA
```

or, if other data is also missing:

```text
🟠 ZVÁŽIŤ
```

## If research is unavailable

Continue, but mark:

```text
Aktuálne porovnanie trhu vyžaduje manuálne online overenie.
```

Do not generate fake market data.

---

# 18. Logging

Log for each analysis:

```json
{
  "listing_id": "",
  "timestamp": "",
  "models_used": {
    "text_research": "grok",
    "vision": "gemini-2.5",
    "final_synthesis": "grok"
  },
  "research_enabled": true,
  "photos_count": 0,
  "vin_present": false,
  "risk_score": 0,
  "final_verdict": "",
  "schema_validation_passed": true,
  "final_report_validation_passed": true,
  "errors": []
}
```

Do not log sensitive user data unless necessary.

---

# 19. Testing Plan

Create test fixtures for:

## Low-risk listing

* VIN present,
* service history present,
* good photos,
* reasonable price,
* no major risks.

Expected verdict:

```text
🟢 DOBRÁ KÚPA
```

or

```text
🟡 PRIJATEĽNÁ KÚPA
```

## Missing VIN

* VIN missing,
* service history unclear.

Expected verdict cannot be better than:

```text
🟠 ZVÁŽIŤ
```

## No photos

* listing text present,
* no photos.

Expected:

```text
Fotografie neboli poskytnuté.
```

Verdict cannot be:

```text
🟢 DOBRÁ KÚPA
```

## Suspicious cheap price

* low price,
* missing VIN,
* unclear history.

Expected verdict:

```text
🔴 RIZIKOVÁ KÚPA
```

or worse.

## Visual damage

* Gemini reports serious visible damage.

Expected verdict cannot be better than:

```text
🟠 ZVÁŽIŤ
```

## Invalid research

* research unavailable,
* no market comparison.

Expected phrase:

```text
Aktuálne porovnanie trhu vyžaduje manuálne online overenie.
```

---

# 20. Production Checklist

Before launch:

* [ ] Grok research prompt created
* [ ] Gemini vision prompt created
* [ ] Final synthesis prompt created
* [ ] JSON schemas created
* [ ] Backend risk scoring implemented
* [ ] Conservative override rules implemented
* [ ] Output validation implemented
* [ ] Retry-on-invalid-JSON implemented
* [ ] No-photos fallback implemented
* [ ] No-research fallback implemented
* [ ] Final verdict lock implemented
* [ ] Forbidden phrase detection implemented
* [ ] Test fixtures created
* [ ] Logs added
* [ ] Error states handled
* [ ] Final report ends with `<!-- END_ANALYSIS -->`

---

# 21. Best Practice Summary

Use this rule as the core design principle:

```text
Models collect and explain evidence.
Backend decides the score.
Final model writes the report.
No model is allowed to invent missing evidence.
```

The safest production architecture is:

```text
Grok = text and research evidence
Gemini = visual evidence
Backend = deterministic scoring and verdict
Grok tools OFF = final Slovak buyer report
```
