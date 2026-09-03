from __future__ import annotations

from typing import Any

from v2_ai import _list_of_dicts, _list_of_strings
from v2_normalize import _number


def build_fallback_report(
    listing: dict[str, Any],
    photo: dict[str, Any],
    research: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    quality = listing.get("data_quality", {})
    score = int(quality.get("score", 0))
    findings: list[dict[str, Any]] = []

    for item in _list_of_dicts(photo.get("findings"))[:4]:
        findings.append(
            {
                "id": f"photo-{len(findings) + 1}",
                "category": "Fotografie",
                "severity": item.get("severity", "watch"),
                "title": item.get("title", "Vizuálne zistenie"),
                "summary": item.get("observation") or item.get("interpretation") or "",
                "evidence_type": "photo",
                "evidence_refs": _list_of_strings(item.get("photo_refs")),
                "confidence": item.get("confidence", "low"),
                "action": item.get("action", "Overiť pri fyzickej obhliadke."),
                "cost_min_eur": _number(item.get("cost_min_eur")),
                "cost_max_eur": _number(item.get("cost_max_eur")),
            }
        )

    for item in _list_of_dicts(research.get("known_risks"))[:3]:
        findings.append(
            {
                "id": f"web-{len(findings) + 1}",
                "category": item.get("component", "Technické riziko"),
                "severity": "risk" if item.get("confidence") == "high" else "watch",
                "title": item.get("issue", "Modelovo špecifické riziko"),
                "summary": item.get("applicability", ""),
                "evidence_type": "web",
                "evidence_refs": _list_of_strings(item.get("source_urls")),
                "confidence": item.get("confidence", "low"),
                "action": (
                    "Pri obhliadke preveriť: "
                    f"{item.get('typical_trigger', 'stav a servis komponentu')}."
                ),
                "cost_min_eur": _number(item.get("cost_min_eur")),
                "cost_max_eur": _number(item.get("cost_max_eur")),
            }
        )

    for missing in quality.get("missing_critical", [])[:3]:
        findings.append(
            {
                "id": f"missing-{len(findings) + 1}",
                "category": "Transparentnosť",
                "severity": "watch",
                "title": f"Chýba údaj: {missing}",
                "summary": "Bez tohto údaja je rozhodnutie menej spoľahlivé.",
                "evidence_type": "listing",
                "evidence_refs": ["Inzerát"],
                "confidence": "high",
                "action": f"Vyžiadať od predajcu: {missing}.",
                "cost_min_eur": 0,
                "cost_max_eur": 0,
            }
        )

    critical = any(item.get("severity") == "critical" for item in findings)
    risky = sum(item.get("severity") == "risk" for item in findings)
    if critical:
        level, safety = "red", 28
    elif risky >= 2 or score < 45:
        level, safety = "orange", 48
    elif score < 70 or risky:
        level, safety = "yellow", 64
    else:
        level, safety = "yellow", 72

    market = research.get("market") if isinstance(research.get("market"), dict) else {}
    market_status = market.get("status", "unavailable")
    position_map = {"low": "low", "fair": "fair", "high": "high", "unknown": "unknown"}
    price_status = position_map.get(market.get("position"), "unknown")

    questions = [
        {
            "question": "Pošlete mi VIN ešte pred obhliadkou?",
            "why_it_matters": "Umožní základné overenie identity a dostupnej histórie.",
            "red_flag_answer": "Predajca VIN odmieta poskytnúť bez rozumného dôvodu.",
        },
        {
            "question": "Máte faktúry alebo záznamy k poslednému servisu a väčším opravám?",
            "why_it_matters": "Doklady sú hodnotnejšie než všeobecné tvrdenie o servise.",
            "red_flag_answer": "Servis je deklarovaný, ale nie je možné doložiť ani základné úkony.",
        },
        {
            "question": "Môžem auto skontrolovať v nezávislom servise a za studena naštartovať?",
            "why_it_matters": "Nezávislá kontrola znižuje riziko skrytých závad.",
            "red_flag_answer": "Predajca nezávislú kontrolu alebo studený štart odmieta.",
        },
    ]

    return {
        "headline": f"Predbežná kontrola: {listing.get('title', 'vozidlo')}",
        "executive_summary": (
            "Automatická záložná analýza z dostupných údajov. Pred rozhodnutím treba "
            "doplniť chýbajúce informácie a vykonať fyzickú kontrolu."
        ),
        "verdict": {
            "level": level,
            "safety_score": safety,
            "confidence": min(75, max(25, score)),
            "one_sentence": "Pokračujte iba po doplnení podkladov a nezávislej kontrole.",
            "recommendation": (
                "Kontaktovať predajcu má zmysel len vtedy, ak poskytne VIN, servisné "
                "doklady a umožní kontrolu v servise."
            ),
        },
        "top_findings": findings[:8],
        "price_assessment": {
            "status": price_status,
            "summary": market.get("summary")
            or "Cena sa bez dostatočných porovnateľných ponúk nedá spoľahlivo vyhodnotiť.",
            "evidence_quality": "medium" if market_status == "supported" else "unavailable",
            "market_min": _number(market.get("range_min")),
            "market_max": _number(market.get("range_max")),
            "recommended_max": _number(market.get("recommended_max")),
            "currency": market.get("currency")
            or listing.get("price", {}).get("currency", "EUR"),
            "negotiation_points": [
                "Chýbajúce servisné doklady",
                "Náklady zistené pri nezávislej kontrole",
            ],
        },
        "ownership_costs": {
            "summary": (
                "Bez presnej motorizácie a servisnej histórie ide len o rezervu na "
                "vstupný servis."
            ),
            "total_min_eur": 300,
            "total_max_eur": 1200,
            "items": [
                {
                    "item": "Vstupný servis po kúpe",
                    "reason": (
                        "Kvapaliny, filtre a kontrola základných opotrebiteľných dielov, "
                        "ak nie sú doložené."
                    ),
                    "min_eur": 300,
                    "max_eur": 1200,
                    "urgency": "soon",
                    "evidence_type": "estimate",
                }
            ],
        },
        "positives": _list_of_strings(photo.get("positive_signals"))[:5],
        "seller_questions": questions,
        "inspection_checklist": [
            {
                "group": "Doklady",
                "items": [
                    "Porovnať VIN na aute a v dokladoch",
                    "Skontrolovať faktúry a servisné záznamy",
                ],
            },
            {
                "group": "Karoséria",
                "items": [
                    "Zmerať lak",
                    "Skontrolovať prahy, podvozok a medzery panelov",
                ],
            },
            {
                "group": "Jazda",
                "items": [
                    "Studený štart",
                    "Diagnostika",
                    "Skúšobná jazda vrátane brzdenia a radenia",
                ],
            },
        ],
        "limitations": [
            "Záverečný AI syntetizačný modul nebol dostupný; report vznikol z deterministických pravidiel.",
            *_list_of_strings(research.get("limitations")),
            *_list_of_strings(photo.get("limitations")),
        ],
        "disclaimer": (
            "Analýza je orientačný prvý filter a nenahrádza VIN report, meranie laku, "
            "diagnostiku ani fyzickú kontrolu kvalifikovaným mechanikom."
        ),
    }
