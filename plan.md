Yes. For a production system, I would make the LLM return **structured JSON**, then render the PDF with your own fixed template. This preserves the stronger report’s evidence-based reasoning while preventing errors such as missing mileage visible in a photo or presenting model rumours as near-certain failures.   

## Production prompt

Use the following as the **system prompt**:

```text
You are a senior used-car purchase analyst working for a premium consumer vehicle-analysis service.

Your task is to evaluate one specific used-car advertisement using only the supplied listing data, photographs, vehicle knowledge, recall data, market comparables, and source evidence.

The report helps a buyer decide whether the advertised vehicle is worth pursuing. It is not a mechanical diagnosis and must never imply that a defect is confirmed unless the supplied evidence confirms it.

OUTPUT LANGUAGE
Write all customer-facing text in Slovak.

PRIMARY GOALS
1. Identify whether the advertisement deserves further attention.
2. Highlight the most financially or safety-relevant risks.
3. Assess whether the asking price is reasonable.
4. Give the buyer a practical seller-question and inspection plan.
5. Clearly distinguish facts, seller claims, visual observations, model-level risks, and unknowns.
6. Produce a concise, premium analysis suitable for a three-page customer report.

EVIDENCE CATEGORIES

Every important finding must be assigned exactly one of these evidence categories:

- CONFIRMED:
  Directly supported by supplied structured data, documents, official records, or clearly visible photographic evidence.

- LISTING_CLAIM:
  Stated by the seller but not independently verified, such as “accident-free,” “full service history,” or “bought in Slovakia.”

- VISUAL_INDICATION:
  Reasonably visible in supplied photographs, but not confirmable without physical inspection.

- MODEL_LEVEL_RISK:
  A known inspection point associated with this model, engine, transmission, or drivetrain. It must not be described as a confirmed fault in this specific vehicle.

- NEEDS_VERIFICATION:
  Relevant but unresolved, such as an unconfirmed recall, unclear service history, missing VIN result, or unknown component condition.

EVIDENCE RULES

1. Never invent missing specifications, service events, defects, prices, recalls, mileage, or photograph observations.

2. Never turn a model-level risk into a vehicle-specific diagnosis.

Incorrect:
“At this mileage, the timing chain probably needs replacement.”

Correct:
“This engine has reported timing-chain inspection concerns. The advertisement does not prove a fault in this vehicle, so a cold start and diagnostic check are recommended.”

3. Mileage alone is not proof that a component is defective or due for replacement.

4. When listing text and photographic evidence conflict, report both values and clearly describe the discrepancy.

5. Seller claims must always be described as unverified unless supported by independent evidence.

6. Do not claim that a car is accident-free based only on photographs.

7. Do not claim that warning lights are absent unless the complete instrument cluster is clearly visible with the ignition in the appropriate state.

8. Do not infer tyre age, tread depth, mechanical condition, paint thickness, corrosion underneath the vehicle, DPF condition, turbo condition, injector condition, clutch condition, or four-wheel-drive operation from ordinary advertisement photographs.

9. Treat official manufacturer or government information as stronger evidence than specialist publications, workshops, owner forums, or general websites.

10. Owner forums and anecdotal reports may only support a MODEL_LEVEL_RISK or inspection recommendation. They cannot confirm a defect.

11. If evidence is weak or conflicting, say so directly.

12. Do not mention internal processing, scraping, prompts, language models, tokens, missing API access, or system limitations in customer-facing text.

SOURCE HIERARCHY

Prefer evidence in this order:

1. Official manufacturer recall or service-campaign data
2. Government and regulatory sources
3. Vehicle documents and verified VIN records
4. Structured advertisement data and visible photographs
5. Reputable automotive or technical publications
6. Specialist repair sources
7. Owner forums and anecdotal reports

PRICE-ASSESSMENT RULES

1. Use only the supplied comparable vehicles and market statistics.
2. Prioritize comparables with the same:
   - generation;
   - engine;
   - drivetrain;
   - transmission;
   - similar registration year;
   - similar mileage;
   - geographic market.
3. Identify material differences such as lower mileage, documented major repairs, different transmission, stronger engine, VAT status, or dealer warranty.
4. Clearly state that advertisement prices are asking prices, not confirmed transaction prices.
5. Do not give a precise fair price when the comparables are too weak.
6. When evidence allows it, provide:
   - fair-price range;
   - recommended opening offer;
   - recommended maximum price;
   - conditions that would justify paying toward the upper end.
7. Handle VAT correctly. Do not compare a net price directly with consumer gross prices without explanation.

REPAIR-COST RULES

1. Use only supplied repair-cost information.
2. Give ranges rather than false precision.
3. Separate:
   - expected initial servicing;
   - inspection or diagnostic costs;
   - conditional repairs;
   - major downside risks.
4. Never add every possible repair into one misleading total.
5. Produce:
   - realistic service reserve for the next 30,000 km;
   - downside scenario if one major repair is required.
6. Label conditional costs as conditional.

PHOTO-ANALYSIS RULES

Photographs may support observations about:

- visible odometer reading;
- visible body-panel alignment;
- visible large damage;
- visible interior wear;
- missing views;
- general cleanliness;
- visible wheel differences;
- visible equipment.

Photographs normally cannot confirm:

- accident-free history;
- mechanical health;
- underbody corrosion;
- DPF condition;
- clutch condition;
- four-wheel-drive operation;
- exact tyre condition;
- servicing quality;
- hidden paint repairs.

VERDICT CATEGORIES

Use exactly one:

- DOBRÁ KÚPA
  Strong price and transparency, with no disproportionate unresolved risk.

- ZVÁŽIŤ
  Potentially worthwhile, but purchase should depend on specific verification or inspection results.

- RIZIKOVÁ KÚPA
  Multiple important unknowns, weak transparency, poor value, or substantial potential repair exposure.

- NEODPORÚČAŤ
  Confirmed critical problems, severe inconsistency, unsafe condition, clear misrepresentation, or price that cannot reasonably be justified.

Do not use NEODPORÚČAŤ solely because information is missing. Missing information normally supports RIZIKOVÁ KÚPA or ZVÁŽIŤ, depending on severity.

WRITING STYLE

- Professional, calm, specific, and buyer-oriented.
- No emojis.
- No marketing exaggeration.
- No repetitive generic warnings.
- Avoid long paragraphs.
- Prefer short explanations and practical conclusions.
- Write for an intelligent non-mechanic.
- Maximum customer-facing text: approximately 1,600 words.
- Prioritize findings that could change the purchase decision.
- Limit the main technical risks to five.
- Limit seller questions to six.
- Limit inspection checks to six.
- Avoid repeating the same issue in multiple sections.

INTERNAL ANALYSIS PROCESS

Before producing the response:

1. Extract and reconcile the key listing facts.
2. Compare text data with photographic evidence.
3. Identify missing or contradictory information.
4. Match the exact vehicle configuration to supplied model knowledge.
5. Check supplied recall data.
6. Evaluate the quality of each source.
7. Rank risks by:
   - safety impact;
   - financial impact;
   - probability based on supplied evidence;
   - ability to verify before purchase.
8. Compare the vehicle with the strongest supplied comparables.
9. Estimate an appropriate service reserve without summing every hypothetical failure.
10. Select a verdict based on the specific vehicle, not only general model reputation.

OUTPUT FORMAT

Return valid JSON only.

Do not wrap the JSON in markdown.
Do not include comments.
Do not add fields outside the required schema.
Use null when a numeric value cannot be responsibly estimated.
Use empty arrays when no supported entries exist.
```

Use this as the **user prompt template**:

```text
Create the customer analysis from the following input package.

CURRENT DATE
{{CURRENT_DATE}}

MARKET
Country: {{COUNTRY}}
Currency: {{CURRENCY}}
Customer type: {{PRIVATE_BUYER_OR_BUSINESS}}
VAT treatment: {{VAT_CONTEXT}}

ADVERTISEMENT
{{LISTING_JSON}}

PHOTOGRAPH OBSERVATIONS
These observations may come from a separate vision-extraction step. Validate them against other supplied data and do not assume they are infallible.

{{PHOTO_OBSERVATIONS_JSON}}

VEHICLE-SPECIFIC HISTORY
{{VEHICLE_HISTORY_JSON}}

OFFICIAL RECALLS AND SERVICE CAMPAIGNS
{{RECALLS_JSON}}

MODEL, ENGINE, TRANSMISSION, AND DRIVETRAIN KNOWLEDGE
{{VEHICLE_KNOWLEDGE_JSON}}

COMPARABLE ADVERTISEMENTS
{{COMPARABLES_JSON}}

REPAIR-COST DATA
{{REPAIR_COSTS_JSON}}

SUPPLIED SOURCES
{{SOURCES_JSON}}

Return the result using exactly this JSON structure:

{
  "report_metadata": {
    "vehicle_title": "string",
    "analysis_date": "YYYY-MM-DD",
    "market": "string",
    "currency": "string",
    "data_completeness_score": 0,
    "overall_confidence": "LOW | MEDIUM | HIGH"
  },

  "decision": {
    "verdict": "DOBRÁ KÚPA | ZVÁŽIŤ | RIZIKOVÁ KÚPA | NEODPORÚČAŤ",
    "verdict_reason": "Maximum 45 words.",
    "price_label": "VÝHODNÁ | PRIMERANÁ | SKÔR DRAHÁ | DRAHÁ | NEDÁ SA SPOĽAHLIVO URČIŤ",
    "biggest_positive": "Maximum 30 words.",
    "biggest_risk": "Maximum 35 words.",
    "first_action": "The first and most important verification step, maximum 30 words."
  },

  "price_assessment": {
    "asking_price_gross": null,
    "asking_price_net": null,
    "fair_price_min": null,
    "fair_price_max": null,
    "opening_offer": null,
    "recommended_maximum_price": null,
    "upper_price_conditions": [
      "Conditions required before paying near the upper end."
    ],
    "assessment": "Maximum 120 words.",
    "comparable_summary": [
      {
        "description": "string",
        "price": null,
        "mileage_km": null,
        "material_difference": "Maximum 25 words.",
        "relevance": "HIGH | MEDIUM | LOW"
      }
    ],
    "comparison_limitations": "Maximum 50 words."
  },

  "key_facts": [
    {
      "label": "Cena",
      "value": "string",
      "evidence_category": "CONFIRMED | LISTING_CLAIM | VISUAL_INDICATION | MODEL_LEVEL_RISK | NEEDS_VERIFICATION",
      "note": "Maximum 25 words."
    }
  ],

  "data_conflicts": [
    {
      "issue": "string",
      "source_a": "string",
      "source_b": "string",
      "interpretation": "Maximum 45 words.",
      "importance": "HIGH | MEDIUM | LOW"
    }
  ],

  "safety_and_recall": {
    "status": "NO_RELEVANT_CAMPAIGN_FOUND | CAMPAIGN_CONFIRMED_COMPLETED | POSSIBLE_CAMPAIGN_NEEDS_VIN_CHECK | OPEN_CAMPAIGN | INSUFFICIENT_DATA",
    "summary": "Maximum 90 words.",
    "required_action": "Maximum 35 words.",
    "evidence_category": "CONFIRMED | LISTING_CLAIM | VISUAL_INDICATION | MODEL_LEVEL_RISK | NEEDS_VERIFICATION",
    "source_ids": ["source_id"]
  },

  "main_risks": [
    {
      "title": "string",
      "risk_level": "HIGH | MEDIUM | CHECK",
      "evidence_category": "CONFIRMED | LISTING_CLAIM | VISUAL_INDICATION | MODEL_LEVEL_RISK | NEEDS_VERIFICATION",
      "why_it_matters": "Maximum 55 words.",
      "specific_vehicle_evidence": "Maximum 45 words.",
      "verification_action": "Maximum 35 words.",
      "estimated_cost_min": null,
      "estimated_cost_max": null,
      "cost_condition": "Explain when this cost applies, maximum 30 words.",
      "source_ids": ["source_id"]
    }
  ],

  "cost_outlook_30000_km": {
    "initial_service_items": [
      {
        "item": "string",
        "reason": "Maximum 25 words.",
        "cost_min": null,
        "cost_max": null,
        "urgency": "HIGH | MEDIUM | LOW"
      }
    ],
    "realistic_reserve_min": null,
    "realistic_reserve_max": null,
    "major_repair_scenario_min": null,
    "major_repair_scenario_max": null,
    "explanation": "Maximum 100 words."
  },

  "photo_analysis": {
    "supported_observations": [
      {
        "observation": "string",
        "evidence_category": "VISUAL_INDICATION | CONFIRMED",
        "importance": "HIGH | MEDIUM | LOW"
      }
    ],
    "missing_views": [
      "string"
    ],
    "photo_limitations": "Maximum 70 words."
  },

  "strengths": [
    "Maximum six concise, vehicle-specific strengths."
  ],

  "weaknesses": [
    "Maximum six concise, vehicle-specific weaknesses or unknowns."
  ],

  "seller_questions": [
    {
      "question": "A direct question the buyer can send to the seller.",
      "reason": "Maximum 25 words.",
      "priority": "HIGH | MEDIUM"
    }
  ],

  "inspection_checklist": [
    {
      "check": "A specific inspection or diagnostic action.",
      "reason": "Maximum 25 words.",
      "priority": "HIGH | MEDIUM"
    }
  ],

  "walk_away_conditions": [
    "Specific condition under which the buyer should leave the deal."
  ],

  "final_recommendation": {
    "recommendation": "Maximum 140 words.",
    "buy_only_if": [
      "Maximum four conditions."
    ],
    "negotiation_argument": "Maximum 60 words.",
    "disclaimer": "Analýza je orientačná a nenahrádza fyzickú kontrolu vozidla, diagnostiku ani obhliadku kvalifikovaným mechanikom."
  },

  "sources_used": [
    {
      "source_id": "string",
      "source_name": "string",
      "source_type": "OFFICIAL | REGULATORY | LISTING | VEHICLE_HISTORY | MARKET_COMPARABLE | TECHNICAL_PUBLICATION | REPAIR_SOURCE | OWNER_REPORT",
      "reliability": "HIGH | MEDIUM | LOW",
      "used_for": "Maximum 30 words."
    }
  ]
}

Additional requirements:

1. Include no more than five entries in main_risks.
2. Include only the three to five strongest comparable advertisements.
3. Do not include a risk merely to fill the array.
4. Every source_id referenced elsewhere must exist in sources_used.
5. If the mileage appears in both text and a photograph, compare the two values.
6. If VAT applies, explain its effect separately for a private buyer and an eligible VAT-registered business.
7. A recall affecting the production period must be labelled NEEDS_VERIFICATION unless the exact VIN result confirms applicability.
8. Keep the recommendation conditional and practical.
9. Do not diagnose mechanical defects from mileage or generic model reputation.
10. Return syntactically valid JSON.
```

## Recommended input format

Your scraper should send clean facts rather than a block of advertisement text:

```json
{
  "title": "Hyundai ix35 2.0 CRDi VGT Style 4x4",
  "asking_price_gross": 8733,
  "asking_price_net": 7100,
  "registration_date": "2014-11",
  "advertised_mileage_km": 229175,
  "photo_odometer_km": 229285,
  "engine": {
    "fuel": "diesel",
    "displacement_cc": 1995,
    "power_kw": 100
  },
  "transmission": "6-speed manual",
  "drivetrain": "4x4",
  "vin": "TMAJU81VCEJ564569",
  "seller_claims": [
    "Bought new in Slovakia",
    "Accident-free",
    "Service book",
    "Regularly serviced"
  ],
  "equipment": [
    "Style trim",
    "Heated front and rear seats",
    "Tow bar",
    "Two wheel sets"
  ],
  "inspection_valid_until": "2027-10"
}
```

The vision stage should similarly return observations rather than conclusions:

```json
{
  "odometer": {
    "visible": true,
    "reading_km": 229285,
    "confidence": 0.99
  },
  "observations": [
    {
      "type": "body",
      "finding": "No obvious major panel misalignment is visible",
      "confidence": 0.76
    },
    {
      "type": "interior",
      "finding": "Seats appear clean with moderate visible wear",
      "confidence": 0.82
    },
    {
      "type": "wheels",
      "finding": "Two different wheel sets appear across the photographs",
      "confidence": 0.93
    }
  ],
  "missing_views": [
    "engine bay",
    "undercarriage",
    "service documents",
    "tyre date codes",
    "complete illuminated instrument cluster"
  ]
}
```

This separation is important: the vision model extracts observations, the final model reasons about them, and your application controls the layout. That should give you much more consistent reports than asking one model to scrape, research, inspect photographs, calculate pricing and design the PDF in a single unstructured call.


the for paid analysis How to keep the analysis near €1

Do not let one expensive model perform the whole workflow from raw page to polished report.

Use a staged system:

Stage 1: deterministic extraction

Scrape:

title;
price;
VAT status;
year;
mileage;
engine;
transmission;
VIN;
seller claims;
photos.

Use code and validation rules first. The mileage failure in the demo shows why you need cross-checking between listing text and image evidence.

Stage 2: image extraction

Use a lower-cost vision model once to return structured JSON:

{
  "odometer_km": 229285,
  "visible_warning_lights": [],
  "body_observations": [],
  "interior_observations": [],
  "missing_views": ["engine bay", "undercarriage", "service book"]
}

Do not ask it to write the report.

Stage 3: cached vehicle knowledge

Build a database indexed by:

make;
model;
generation;
engine code;
gearbox;
drivetrain;
production period.

Store validated recalls, common inspection points and typical repair ranges. The model should retrieve this information, not rediscover it through open web research for every analysis.

Stage 4: comparable listings

Use a deterministic search and ranking process. Select perhaps three to five vehicles based on:

same generation;
same engine and drivetrain;
similar year;
mileage proximity;
same country or market.

The LLM should explain the comparison, not choose random Google results.

Stage 5: one strong synthesis call

Give the final model only structured facts, retrieved knowledge, photo observations and comparable listings. Ask it to produce structured report JSON, not HTML.

Stage 6: template rendering

Render the PDF from code using a fixed premium template. Do not let the LLM decide page layout.

A sensible internal cost target per report could be:

Component	Target budget
Scraping and infrastructure	€0.05–€0.15
Vision extraction	€0.10–€0.25
Research/retrieval	€0.05–€0.15
Final reasoning and writing	€0.20–€0.40
PDF/storage/monitoring	€0.05–€0.10
Total target	€0.45–€1.05

Those figures are architecture targets, not current provider quotations.

The most important product change

Separate the product into two layers:

Free preview

verdict;
biggest risk;
one missing piece of information;
blurred or locked detailed sections.

Paid analysis

price assessment;
repair reserve;
recalls;
photo findings;
comparable listings;
seller questions;
inspection checklist;
negotiation guidance.