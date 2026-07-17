"""Customer-safe presentation model assembled from canonical job artifacts."""

from __future__ import annotations

import re
import unicodedata
from typing import Any
from urllib.parse import urlparse

from scrapper_demo.storage import ListingJobRepository
from scrapper_demo.verdicts import STATUS_RANK, status_for_label


VERDICT_LABELS = {
    "sk": {
        "WORTH_INSPECTING": "Stojí za obhliadku",
        "INSPECT_WITH_RESERVATIONS": "Najprv preveriť",
        "RESOLVE_BEFORE_PROCEEDING": "Riešiť len s výhradami",
        "HIGH_RISK": "Skôr neriešiť",
        "DO_NOT_PROCEED": "Ruky preč",
    },
    "cs": {
        "WORTH_INSPECTING": "Stojí za prohlídku",
        "INSPECT_WITH_RESERVATIONS": "Nejprve prověřit",
        "RESOLVE_BEFORE_PROCEEDING": "Řešit jen s výhradami",
        "HIGH_RISK": "Spíše neřešit",
        "DO_NOT_PROCEED": "Ruce pryč",
    },
    "en": {
        "WORTH_INSPECTING": "Worth checking out",
        "INSPECT_WITH_RESERVATIONS": "Verify first",
        "RESOLVE_BEFORE_PROCEEDING": "Proceed with reservations",
        "HIGH_RISK": "Probably skip",
        "DO_NOT_PROCEED": "Walk away",
    },
}

VERDICT_SUMMARIES = {
    "sk": {
        "WORTH_INSPECTING": "Inzerát má dobrý základ. Pred kúpou stále odporúčame nezávislú kontrolu.",
        "INSPECT_WITH_RESERVATIONS": "Auto môže byť zaujímavé, no pred cestou treba doplniť alebo overiť dôležité údaje.",
        "RESOLVE_BEFORE_PROCEEDING": "Pokračovať má zmysel až po vyriešení významných neistôt a cielenej kontrole.",
        "HIGH_RISK": "Zistené riziká výrazne znižujú zmysel pokračovať bez silných dôkazov a odbornej kontroly.",
        "DO_NOT_PROCEED": "Dostupné dôkazy nepodporujú ďalší záväzok voči tomuto vozidlu.",
    },
    "cs": {
        "WORTH_INSPECTING": "Inzerát má dobrý základ. Před koupí stále doporučujeme nezávislou kontrolu.",
        "INSPECT_WITH_RESERVATIONS": "Auto může být zajímavé, ale před cestou je třeba doplnit nebo ověřit důležité údaje.",
        "RESOLVE_BEFORE_PROCEEDING": "Pokračovat má smysl až po vyřešení významných nejistot a cílené kontrole.",
        "HIGH_RISK": "Zjištěná rizika výrazně snižují smysl pokračovat bez silných důkazů a odborné kontroly.",
        "DO_NOT_PROCEED": "Dostupné důkazy nepodporují další závazek vůči tomuto vozidlu.",
    },
    "en": {
        "WORTH_INSPECTING": "The listing has a good foundation. An independent inspection is still recommended.",
        "INSPECT_WITH_RESERVATIONS": "The car may be interesting, but important facts should be verified before travelling.",
        "RESOLVE_BEFORE_PROCEEDING": "Proceed only after the material uncertainties are resolved and checked.",
        "HIGH_RISK": "The identified risks make proceeding difficult to justify without strong evidence and inspection.",
        "DO_NOT_PROCEED": "The available evidence does not support making a further commitment to this vehicle.",
    },
}

VERDICT_TONES = {
    "WORTH_INSPECTING": "good",
    "INSPECT_WITH_RESERVATIONS": "warn",
    "RESOLVE_BEFORE_PROCEEDING": "warn",
    "HIGH_RISK": "risk",
    "DO_NOT_PROCEED": "risk",
}

_PRIVATE_SCORE_SECTION = re.compile(
    r"(?ims)^#{2,4}\s+(?:Sk[oó]re anal[yý]zy|Analysis score)\s*$.*?(?=^#{1,4}\s+|\Z)"
)


_INTERNAL_FAILURE_TEXT = re.compile(
    r"(?:research v2 returned invalid json(?: twice)?\.?|research model output was unavailable|"
    r"research output did not satisfy the structured contract|automatic technical research)",
    re.I,
)


def _localized(language: str, *, sk: str, cs: str, en: str) -> str:
    return {"sk": sk, "cs": cs, "en": en}.get(language, sk)


def _research_unavailable_text(language: str) -> str:
    return _localized(
        language,
        sk="Automatické technické overenie nebolo dostupné. Pred rozhodnutím ho doplňte manuálne alebo v nezávislom servise.",
        cs="Automatické technické ověření nebylo dostupné. Před rozhodnutím ho doplňte ručně nebo v nezávislém servisu.",
        en="Automatic technical research was unavailable. Complete it manually or through an independent workshop before deciding.",
    )


def _contains_internal_failure(value: Any) -> bool:
    return bool(_INTERNAL_FAILURE_TEXT.search(_text(value)))


def _public_report_markdown(value: str, language: str) -> str:
    """Remove the legacy numeric scorecard from otherwise public report prose."""
    cleaned = _PRIVATE_SCORE_SECTION.sub("", _text(value))
    cleaned = _INTERNAL_FAILURE_TEXT.sub(_research_unavailable_text(language), cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(*values: Any) -> str:
    for value in values:
        candidate = _text(value)
        if candidate:
            return candidate
    return ""


def _component_label(value: Any) -> str:
    if not isinstance(value, dict):
        return _text(value)
    return _first_text(
        value.get("marketing_name"),
        value.get("name"),
        value.get("type"),
        value.get("family"),
        value.get("code"),
    )


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    cleaned = re.sub(r"[^0-9,.-]", "", _text(value))
    if not cleaned:
        return None
    if "," in cleaned and "." in cleaned:
        if cleaned.rfind(",") > cleaned.rfind("."):
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
    elif cleaned.count(",") == 1:
        left, right = cleaned.split(",")
        cleaned = f"{left}.{right}" if len(right) <= 2 else left + right
    elif cleaned.count(".") == 1:
        left, right = cleaned.split(".")
        cleaned = left + right if len(right) == 3 else cleaned
    elif cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")
    try:
        number = float(cleaned)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _integer(value: Any) -> int | None:
    number = _number(value)
    return int(number) if number is not None else None


def _clean_string_list(values: Any, *, limit: int = 12) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        candidate = _text(value)
        if candidate and candidate not in result:
            result.append(candidate)
        if len(result) >= limit:
            break
    return result


def _fold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", _text(value))
    return " ".join(
        "".join(char for char in normalized if not unicodedata.combining(char))
        .casefold()
        .split()
    )


def _looks_wrong_language(value: Any, language: str) -> bool:
    text = _fold(value)
    if not text:
        return False
    words = set(re.findall(r"[a-z]+", text))
    english_markers = {
        "the", "this", "that", "with", "without", "was", "were", "is", "are",
        "requires", "verify", "check", "engine", "vehicle", "listing", "research",
        "timing", "chain", "replacement", "status", "associated", "failure", "price",
        "recall", "specific", "provided", "data",
    }
    slovak_markers = {
        "preverit", "overit", "vyziadat", "poziadat", "obhliadkou", "inzeratu",
        "najazde", "vozidla", "servisnu", "udajoch", "otazniky", "dokaz", "rozporu",
    }
    czech_markers = {
        "proverit", "overit", "vyzadat", "pozadat", "prohlidkou", "inzeratu",
        "najezdu", "vozidla", "servisni", "udajich", "dukaz", "rozporu",
    }
    if language in {"sk", "cs"}:
        return len(words & english_markers) >= 2
    return len(words & (slovak_markers | czech_markers)) >= 2


def _public_freeform(value: Any, language: str) -> str:
    text = _text(value)
    if _contains_internal_failure(text) or _looks_wrong_language(text, language):
        return ""
    return text


def _public_string_list(values: Any, language: str, *, limit: int = 12) -> list[str]:
    result: list[str] = []
    for value in _clean_string_list(values, limit=limit):
        text = _public_freeform(value, language)
        if text and text not in result:
            result.append(text)
    return result


def _public_observations(values: Any, language: str, *, limit: int = 24) -> list[str]:
    result: list[str] = []
    for value in _normalize_observations(values):
        text = _public_freeform(value, language)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _display_value(value: Any, language: str) -> str:
    text = _text(value)
    labels = {
        "all-wheel drive": {
            "sk": "Pohon všetkých kolies",
            "cs": "Pohon všech kol",
            "en": "All-wheel drive",
        },
        "requires_manual_verification": {
            "sk": "Vyžaduje manuálne overenie",
            "cs": "Vyžaduje ruční ověření",
            "en": "Requires manual verification",
        },
        "fair": {"sk": "V rámci trhu", "cs": "V rámci trhu", "en": "Within market range"},
        "rather_cheap": {"sk": "Skôr pod trhom", "cs": "Spíše pod trhem", "en": "Rather below market"},
        "rather_expensive": {"sk": "Skôr nad trhom", "cs": "Spíše nad trhem", "en": "Rather above market"},
        "skipped": {"sk": "Nevykonané", "cs": "Neprovedeno", "en": "Not performed"},
        "insufficient_data": {
            "sk": "Nedostatok údajov",
            "cs": "Nedostatek údajů",
            "en": "Insufficient data",
        },
        "probable": {"sk": "Pravdepodobné", "cs": "Pravděpodobné", "en": "Probable"},
        "high": {"sk": "Vysoká", "cs": "Vysoká", "en": "High"},
        "medium": {"sk": "Stredná", "cs": "Střední", "en": "Medium"},
        "low": {"sk": "Nízka", "cs": "Nízká", "en": "Low"},
        "needs_verification": {
            "sk": "Vyžaduje overenie",
            "cs": "Vyžaduje ověření",
            "en": "Needs verification",
        },
        "possible_campaign_needs_vin_check": {
            "sk": "Overiť zvolávacie akcie podľa VIN",
            "cs": "Ověřit svolávací akce podle VIN",
            "en": "Check recall campaigns by VIN",
        },
        "ok": {"sk": "V poriadku", "cs": "V pořádku", "en": "Valid"},
        "fwd": {"sk": "Pohon predných kolies", "cs": "Pohon předních kol", "en": "Front-wheel drive"},
        "rwd": {"sk": "Pohon zadných kolies", "cs": "Pohon zadních kol", "en": "Rear-wheel drive"},
        "awd": {"sk": "Pohon všetkých kolies", "cs": "Pohon všech kol", "en": "All-wheel drive"},
        "vin was not supplied in the listing.": {
            "sk": "VIN nebolo uvedené v inzeráte.",
            "cs": "VIN nebylo uvedeno v inzerátu.",
            "en": "VIN was not supplied in the listing.",
        },
        "verify campaigns manually with the vin.": {
            "sk": "Zvolávacie akcie overte manuálne podľa VIN.",
            "cs": "Svolávací akce ověřte ručně podle VIN.",
            "en": "Verify campaigns manually with the VIN.",
        },
        "automatic recall research was unavailable.": {
            "sk": "Automatické overenie zvolávacích akcií nebolo dostupné.",
            "cs": "Automatické ověření svolávacích akcí nebylo dostupné.",
            "en": "Automatic recall research was unavailable.",
        },
    }
    localized = labels.get(text.casefold(), {}).get(language)
    if localized:
        return localized
    if language in {"sk", "cs"}:
        generation_names = {
            "first generation": {"sk": "Prvá generácia", "cs": "První generace"},
            "second generation": {"sk": "Druhá generácia", "cs": "Druhá generace"},
            "third generation": {"sk": "Tretia generácia", "cs": "Třetí generace"},
            "fourth generation": {"sk": "Štvrtá generácia", "cs": "Čtvrtá generace"},
        }
        for prefix, translations in generation_names.items():
            if text.casefold().startswith(prefix):
                return translations[language] + text[len(prefix):]
    return text


def _usable_listing_value(value: Any) -> str:
    text = _text(value)
    if not text or text.casefold() in {"n/a", "na", "none", "null", "unknown", "neuvedené"}:
        return ""
    if re.fullmatch(r"[a-z]:?", text, re.I):
        return ""
    return text


def _title_identity(title: str) -> tuple[str, str]:
    words = title.split()
    if len(words) < 2:
        return (words[0], "") if words else ("", "")
    two_word_makes = {
        "alfa romeo", "aston martin", "land rover", "mercedes benz",
        "rolls royce",
    }
    first_two = " ".join(words[:2]).casefold().replace("-", " ")
    if first_two in two_word_makes and len(words) >= 3:
        return " ".join(words[:2]), words[2]
    return words[0], words[1]


def _language(metadata: dict[str, Any]) -> str:
    value = _text(metadata.get("output_language")).lower()
    if value.startswith("en"):
        return "en"
    if value.startswith(("cs", "cz")):
        return "cs"
    return "sk"


def _status(risk: dict[str, Any]) -> str:
    candidate = _text(risk.get("decision_status"))
    if candidate in STATUS_RANK:
        return candidate
    legacy = status_for_label(_text(risk.get("allowed_final_verdict")))
    return legacy or "RESOLVE_BEFORE_PROCEEDING"


def _source_name(source_url: str, raw: dict[str, Any]) -> str:
    explicit = _first_text(raw.get("source"), raw.get("portal"))
    if explicit and explicit.lower() != "manual":
        return explicit
    try:
        host = urlparse(source_url).hostname or ""
    except ValueError:
        host = ""
    return host.removeprefix("www.") or ("Manual input" if raw.get("source") == "manual" else "")


def _normalize_observations(values: Any) -> list[str]:
    result: list[str] = []
    for value in _list(values):
        if isinstance(value, dict):
            candidate = _first_text(
                value.get("observation"),
                value.get("red_flag"),
                value.get("finding"),
                value.get("description"),
                value.get("summary"),
            )
        else:
            candidate = _text(value)
        if candidate and candidate not in result:
            result.append(candidate)
    return result


def _finding(
    *,
    tone: str,
    title: Any,
    detail: Any = "",
    action: Any = "",
    category: str = "",
) -> dict[str, str] | None:
    clean_title = _text(title)
    if not clean_title:
        return None
    return {
        "tone": tone,
        "title": clean_title,
        "detail": _text(detail),
        "action": _text(action),
        "category": category,
    }


def _priority_findings(
    research: dict[str, Any],
    vision: dict[str, Any],
    language: str,
) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    for item in _list(research.get("missing_or_uncertain_data")):
        data = _dict(item)
        severity = _text(data.get("severity")).lower()
        internal_failure = _contains_internal_failure(data.get("item")) or _contains_internal_failure(
            data.get("why_it_matters")
        )
        clean_title = _public_freeform(data.get("item"), language)
        clean_detail = _public_freeform(data.get("why_it_matters"), language)
        wrong_language = not clean_title or not clean_detail
        found = _finding(
            tone="risk" if severity == "high" else "warn",
            title=(
                _localized(
                    language,
                    sk="Technické overenie modelu",
                    cs="Technické ověření modelu",
                    en="Model-specific technical research",
                )
                if internal_failure
                else (
                    _localized(
                        language,
                        sk="Technické údaje na overenie",
                        cs="Technické údaje k ověření",
                        en="Technical facts to verify",
                    )
                    if wrong_language
                    else clean_title
                )
            ),
            detail=(
                _research_unavailable_text(language)
                if internal_failure
                else (
                    _localized(
                        language,
                        sk="Túto informáciu treba overiť pred rozhodnutím.",
                        cs="Tuto informaci je třeba ověřit před rozhodnutím.",
                        en="Verify this information before deciding.",
                    )
                    if wrong_language
                    else clean_detail
                )
            ),
            category="missing_information",
        )
        if found:
            findings.append(found)

    for item in _list(research.get("data_conflicts")):
        data = _dict(item)
        issue = _public_freeform(data.get("issue"), language)
        interpretation = _public_freeform(data.get("interpretation"), language)
        if not issue or not interpretation:
            continue
        found = _finding(
            tone="risk" if _text(data.get("importance")).upper() == "HIGH" else "warn",
            title=issue,
            detail=interpretation,
            category="data_conflict",
        )
        if found:
            findings.append(found)

    for item in _list(research.get("technical_risks")):
        data = _dict(item)
        component = _public_freeform(data.get("component"), language)
        issue = _public_freeform(data.get("issue"), language)
        buyer_impact = _public_freeform(data.get("buyer_impact"), language)
        verification_action = _public_freeform(data.get("verification_action"), language)
        if not component or not issue or not buyer_impact:
            continue
        title = " — ".join(
            value for value in (component, issue) if value
        )
        found = _finding(
            tone="risk" if _text(data.get("risk_level")).upper() == "HIGH" else "warn",
            title=title,
            detail=buyer_impact,
            action=verification_action,
            category="technical_risk",
        )
        if found:
            findings.append(found)

    for observation in _normalize_observations(vision.get("visible_red_flags")):
        observation = _public_freeform(observation, language)
        if not observation:
            continue
        found = _finding(
            tone="risk",
            title=observation,
            category="visual_red_flag",
        )
        if found:
            findings.append(found)

    visual_verdict = _public_freeform(vision.get("visual_verdict"), language)
    if visual_verdict:
        found = _finding(
            tone="good",
            title=_localized(
                language,
                sk="Vizuálna kontrola",
                cs="Vizuální kontrola",
                en="Visual review",
            ),
            detail=visual_verdict,
            category="visual_review",
        )
        if found:
            findings.append(found)

    deduped: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in findings:
        key = item["title"].casefold()
        if key not in seen:
            seen.add(key)
            deduped.append(item)
        if len(deduped) >= 6:
            break
    return deduped


def _normalize_action(value: Any, language: str) -> str:
    action = _text(value)
    if not action or _contains_internal_failure(action):
        return ""
    known = {
        "poziadat predajcu o vin pred obhliadkou a overit ho mimo inzeratu.": {
            "sk": "Požiadať predajcu o VIN pred obhliadkou a overiť ho mimo inzerátu.",
            "cs": "Požádat prodejce o VIN před prohlídkou a ověřit ho mimo inzerát.",
            "en": "Ask the seller for the VIN before viewing and verify it independently.",
        },
        "vyziadat servisnu historiu, faktury a intervaly vymen.": {
            "sk": "Vyžiadať servisnú históriu, faktúry a intervaly výmen.",
            "cs": "Vyžádat servisní historii, faktury a intervaly výměn.",
            "en": "Request the service history, invoices, and replacement intervals.",
        },
        "cenu overit manualne na porovnatelnych vozidlach; neovereny odhad sam o sebe nehodnoti stav auta.": {
            "sk": "Cenu overiť manuálne na porovnateľných vozidlách; neoverený odhad sám o sebe nehodnotí stav auta.",
            "cs": "Cenu ověřit ručně na srovnatelných vozidlech; neověřený odhad sám o sobě nehodnotí stav auta.",
            "en": "Verify the price manually against comparable vehicles; an unverified estimate does not assess the car's condition.",
        },
        "pri veku alebo najazde preverit bezny servis a opotrebovatelne diely.": {
            "sk": "Pri danom veku a nájazde preveriť bežný servis a opotrebovateľné diely.",
            "cs": "S ohledem na věk a nájezd prověřit běžný servis a opotřebitelné díly.",
            "en": "Given the age and mileage, check routine servicing and wear items.",
        },
        "preverit modelom oznacene otazniky v udajoch; bez dvoch konkretnych zdrojov nejde o dokaz rozporu.": {
            "sk": "Preveriť modelom označené otázniky v údajoch; bez dvoch konkrétnych zdrojov nejde o dôkaz rozporu.",
            "cs": "Prověřit modelem označené nejasnosti v údajích; bez dvou konkrétních zdrojů nejde o důkaz rozporu.",
            "en": "Verify uncertainties identified in the data; without two concrete sources they are not evidence of a conflict.",
        },
    }
    localized = known.get(_fold(action), {}).get(language)
    if localized:
        return localized
    if _looks_wrong_language(action, language):
        return ""
    return action


def _buyer_actions(risk: dict[str, Any], research: dict[str, Any], language: str) -> list[str]:
    candidates = [
        normalized
        for item in _clean_string_list(risk.get("buyer_actions"), limit=10)
        if (normalized := _normalize_action(item, language))
    ]
    if not candidates:
        candidates = [
            normalized
            for item in _clean_string_list(risk.get("buyer_priority_checks"), limit=10)
            if (normalized := _normalize_action(item, language))
        ]
    for item in _list(research.get("technical_risks")):
        action = _normalize_action(_dict(item).get("verification_action"), language)
        if action and action not in candidates:
            candidates.append(action)
    for item in _list(research.get("missing_or_uncertain_data")):
        data = _dict(item)
        action = _normalize_action(
            _first_text(data.get("required_action"), data.get("why_it_matters")),
            language,
        )
        if action and action not in candidates:
            candidates.append(action)
    return candidates[:8]


def _seller_message(actions: list[str], language: str) -> str:
    selected = [action.rstrip(". ") for action in actions[:4] if action]
    if language == "en":
        if not selected:
            return "Hello, please send the VIN and available service documentation before the viewing. Thank you."
        return "Hello, before the viewing please help me verify: " + "; ".join(selected) + ". Thank you."
    if language == "cs":
        if not selected:
            return "Dobrý den, před prohlídkou prosím o VIN a dostupnou servisní dokumentaci. Děkuji."
        return "Dobrý den, před prohlídkou si prosím potřebuji ověřit: " + "; ".join(selected) + ". Děkuji."
    if not selected:
        return "Dobrý deň, pred obhliadkou prosím o VIN a dostupnú servisnú dokumentáciu. Ďakujem."
    return "Dobrý deň, pred obhliadkou si prosím potrebujem overiť: " + "; ".join(selected) + ". Ďakujem."


def _costs(research: dict[str, Any], language: str) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    initial_low = 0
    initial_high = 0
    conditional_low = 0
    conditional_high = 0
    initial_available = False
    conditional_available = False
    for raw_item in _list(research.get("expected_costs")):
        item = _dict(raw_item)
        item_label = _public_freeform(item.get("item"), language)
        why = _public_freeform(item.get("why"), language)
        if not item_label or (_text(item.get("why")) and not why):
            continue
        low = _number(item.get("estimated_cost_eur_low"))
        high = _number(item.get("estimated_cost_eur_high"))
        cost_type = _text(item.get("cost_type"))
        normalized = {
            "item": item_label,
            "why": why,
            "low_eur": low,
            "high_eur": high,
            "cost_type": cost_type,
            "urgency": _text(item.get("urgency")),
            "basis": _text(item.get("basis")),
        }
        if normalized["item"]:
            items.append(normalized)
        if low is None and high is None:
            continue
        use_low = int(low or 0)
        use_high = int(high if high is not None else use_low)
        if cost_type in {"initial_service", "diagnostic"}:
            initial_available = True
            initial_low += use_low
            initial_high += use_high
        else:
            conditional_available = True
            conditional_low += use_low
            conditional_high += use_high
    return {
        "items": items,
        "initial_service": {
            "available": initial_available,
            "low_eur": initial_low if initial_available else None,
            "high_eur": initial_high if initial_available else None,
        },
        "conditional_repairs": {
            "available": conditional_available,
            "low_eur": conditional_low if conditional_available else None,
            "high_eur": conditional_high if conditional_available else None,
        },
    }


def _technical_risks(research: dict[str, Any], language: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw_item in _list(research.get("technical_risks")):
        item = _dict(raw_item)
        component = _public_freeform(item.get("component"), language)
        issue = _public_freeform(item.get("issue"), language)
        if not component and not issue:
            continue
        buyer_impact = _public_freeform(item.get("buyer_impact"), language)
        vehicle_evidence = _public_freeform(item.get("specific_vehicle_evidence"), language)
        verification_action = _public_freeform(item.get("verification_action"), language)
        if any(
            _text(item.get(key)) and not clean
            for key, clean in (
                ("buyer_impact", buyer_impact),
                ("specific_vehicle_evidence", vehicle_evidence),
                ("verification_action", verification_action),
            )
        ):
            continue
        result.append(
            {
                "component": component,
                "issue": issue,
                "risk_level": _text(item.get("risk_level")),
                "evidence_category": _text(item.get("evidence_category")),
                "buyer_impact": buyer_impact,
                "specific_vehicle_evidence": vehicle_evidence,
                "verification_action": verification_action,
                "low_eur": _number(item.get("estimated_cost_eur_low")),
                "high_eur": _number(item.get("estimated_cost_eur_high")),
                "confidence": _text(item.get("confidence")),
            }
        )
    return result


def _market(benchmark: dict[str, Any], research: dict[str, Any], language: str) -> dict[str, Any]:
    assessment = _dict(research.get("market_assessment"))
    benchmark_comparables = _list(benchmark.get("accepted_comparables"))
    legacy_comparables = _list(research.get("market_comparables"))
    comparables: list[dict[str, Any]] = []
    for raw_item in benchmark_comparables or legacy_comparables:
        item = _dict(raw_item)
        url = _first_text(item.get("detail_url"), item.get("source_url"), item.get("url"))
        display = item.get("display_in_report") is True or item.get("customer_facing") is True
        verified = bool(benchmark_comparables) or item.get("verified_url") is True
        if not display or not verified or not url.startswith(("http://", "https://")):
            continue
        comparables.append(
            {
                "title": _first_text(
                    item.get("title"), item.get("description"), item.get("model"), item.get("portal")
                ),
                "price_eur": _number(
                    item.get("price_eur")
                    if item.get("price_eur") not in (None, "")
                    else item.get("normalized_price_eur")
                ),
                "year": _integer(item.get("year")),
                "mileage_km": _integer(item.get("mileage_km")),
                "portal": _text(item.get("portal")),
                "url": url,
            }
        )
    limitations = _clean_string_list(benchmark.get("limitations"), limit=8) or (
        [_text(assessment.get("limitations"))] if _text(assessment.get("limitations")) else []
    )
    if limitations and language != "en":
        limitations = [
            _localized(
                language,
                sk="Ponukové ceny nie sú realizačné ceny. Porovnanie nezohľadňuje presnú výbavu, stav, dovozné náklady ani záruku.",
                cs="Nabídkové ceny nejsou realizační ceny. Srovnání nezohledňuje přesnou výbavu, stav, dovozní náklady ani záruku.",
                en="Asking prices are not transaction prices. The comparison does not adjust for exact equipment, condition, import costs, or warranty.",
            )
        ]
    return {
        "available": benchmark.get("available") is True or assessment.get("benchmark_available") is True,
        "confidence": _first_text(benchmark.get("confidence"), assessment.get("benchmark_confidence")),
        "advertised_price_eur": _number(
            benchmark.get("advertised_price_eur") or assessment.get("advertised_price_eur")
        ),
        "median_eur": _number(benchmark.get("median_eur") or assessment.get("benchmark_median_eur")),
        "local_median_eur": _number(
            benchmark.get("local_market_median_eur") or assessment.get("local_market_median_eur")
        ),
        "price_delta_percent": _number(
            benchmark.get("price_delta_percent") or assessment.get("price_delta_percent")
        ),
        "price_view": _display_value(
            _first_text(benchmark.get("price_view"), assessment.get("price_view")),
            language,
        ),
        "scope": _first_text(benchmark.get("benchmark_scope"), assessment.get("benchmark_scope")),
        "comparables": comparables,
        "limitations": limitations,
    }


def _sources(research: dict[str, Any]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for raw_item in _list(research.get("sources_used")):
        item = _dict(raw_item)
        url = _text(item.get("source_url"))
        if item.get("verified_url") is not True or not url.startswith(("http://", "https://")):
            continue
        result.append(
            {
                "name": _first_text(item.get("source_name"), urlparse(url).hostname),
                "type": _text(item.get("source_type")),
                "reliability": _text(item.get("reliability")),
                "used_for": _text(item.get("used_for")),
                "url": url,
            }
        )
    for raw_item in _list(research.get("web_research_findings")):
        item = _dict(raw_item)
        url = _text(item.get("source_url"))
        if item.get("verified_url") is not True or not url.startswith(("http://", "https://")):
            continue
        if any(source["url"] == url for source in result):
            continue
        result.append(
            {
                "name": _first_text(item.get("source_name"), urlparse(url).hostname),
                "type": _text(item.get("source_type")),
                "reliability": _text(item.get("confidence")),
                "used_for": _text(item.get("claim")),
                "url": url,
            }
        )
    return result


def _pros(research: dict[str, Any], vision: dict[str, Any], language: str) -> list[str]:
    evidence = _dict(research.get("evidence_summary"))
    result = [
        text
        for value in _clean_string_list(evidence.get("strongest_evidence"), limit=4)
        if (text := _public_freeform(value, language))
    ]
    for claim in _list(research.get("seller_claims")):
        item = _dict(claim)
        if _text(item.get("verification_status")).lower() in {"verified", "confirmed", "supported"}:
            candidate = _public_freeform(item.get("claim"), language)
            if candidate and candidate not in result:
                result.append(candidate)
    if not result:
        result.extend(
            text
            for value in _normalize_observations(vision.get("exterior_observations"))[:2]
            if (text := _public_freeform(value, language))
        )
    return result[:6]


def build_presentation_payload(
    repository: ListingJobRepository,
    slug: str,
    *,
    parsed: dict[str, Any],
    images: list[dict[str, str]],
    report_markdown: str,
) -> dict[str, Any]:
    """Build the stable public view model without exposing internal diagnostics."""
    repository.job_dir(slug, require=True)
    raw = _dict(repository.read_json(slug, "raw_data.json", default={}))
    facts = _dict(repository.read_json(slug, "listing_facts.json", default={}))
    identity = _dict(repository.read_json(slug, "component_identity.json", default={}))
    risk = _dict(repository.read_json(slug, "risk_score.json", default={}))
    research = _dict(repository.read_json(slug, "grok_research.json", default={}))
    vision = _dict(repository.read_json(slug, "gemini_vision.json", default={}))
    benchmark = _dict(repository.read_json(slug, "market_benchmark.json", default={}))
    metadata = _dict(repository.read_json(slug, "analysis_metadata.json", default={}))

    if not identity:
        identity = _dict(research.get("component_identity"))
    if not facts:
        facts = _dict(research.get("listing_facts"))

    language = _language(metadata)
    status = _status(risk)
    specs = _dict(parsed.get("specs"))
    source_url = _first_text(parsed.get("source_url"), raw.get("source_url"), raw.get("url"))
    evidence = _dict(research.get("evidence_summary"))
    buyer_actions = _buyer_actions(risk, research, language)
    findings = _priority_findings(research, vision, language)
    listing_title = _first_text(facts.get("title"), parsed.get("title"), raw.get("title"), slug)
    derived_make, derived_model = _title_identity(listing_title)
    listing_engine = _first_text(
        _usable_listing_value(facts.get("engine")),
        _usable_listing_value(specs.get("Engine")),
        _usable_listing_value(raw.get("engine")),
        _component_label(identity.get("engine")),
    )

    listing = {
        "slug": slug,
        "title": listing_title,
        "price_eur": _number(_first_text(facts.get("price"), parsed.get("price"), raw.get("price"))),
        "year": _integer(_first_text(facts.get("year"), specs.get("Year"), raw.get("year"))),
        "mileage_km": _integer(
            facts.get("advertised_mileage_km")
            or _first_text(facts.get("mileage"), specs.get("Mileage"), raw.get("mileage"))
        ),
        "vin": _first_text(facts.get("vin"), parsed.get("vin"), specs.get("VIN"), raw.get("vin")),
        "engine": listing_engine,
        "fuel": _first_text(facts.get("fuel"), specs.get("Fuel"), raw.get("fuel")),
        "transmission": _first_text(
            facts.get("transmission"), specs.get("Transmission"), raw.get("transmission")
        ),
        "drivetrain": _display_value(
            _first_text(_component_label(identity.get("drivetrain")), specs.get("Drivetrain")),
            language,
        ),
        "location": _first_text(parsed.get("location"), raw.get("location")),
        "source_url": source_url,
        "source_name": _source_name(source_url, raw),
        "scraped_at": _text(parsed.get("scraped_at")),
        "photos_count": len(images),
        "images": images,
    }

    candidate_variants: list[dict[str, str]] = []
    for raw_variant in _list(identity.get("candidate_variants"))[:5]:
        variant = _dict(raw_variant)
        reason = _public_freeform(variant.get("reason"), language)
        engine_code = _text(variant.get("engine_code"))
        transmission_code = _text(variant.get("transmission_code"))
        if reason or engine_code or transmission_code:
            candidate_variants.append(
                {
                    "engine_code": engine_code,
                    "transmission_code": transmission_code,
                    "reason": reason,
                }
            )

    safe_identity = {
        "status": _text(identity.get("identification_status")),
        "make": _first_text(facts.get("make"), raw.get("make"), derived_make),
        "model": _first_text(facts.get("model"), raw.get("model"), derived_model),
        "generation": _dict(identity.get("generation")),
        "engine": _dict(identity.get("engine")),
        "transmission": _dict(identity.get("transmission")),
        "drivetrain": _dict(identity.get("drivetrain")),
        "notes": _public_string_list(identity.get("notes"), language, limit=8),
        "candidate_variants": candidate_variants,
        "confidence_label": _first_text(
            _dict(identity.get("generation")).get("confidence"),
            identity.get("identification_status"),
        ),
    }

    safe_safety: dict[str, Any] = {}
    for key, value in _dict(research.get("safety_and_recall")).items():
        if not isinstance(value, str):
            safe_safety[key] = value
            continue
        if key == "required_action" and _looks_wrong_language(value, language):
            safe_safety[key] = _localized(
                language,
                sk="Zvolávacie akcie overte v oficiálnom zdroji podľa VIN.",
                cs="Svolávací akce ověřte v oficiálním zdroji podle VIN.",
                en="Verify recall campaigns in an official source using the VIN.",
            )
        elif key == "summary" and _looks_wrong_language(value, language):
            safe_safety[key] = _localized(
                language,
                sk="Bez VIN nie je možné zvolávacie akcie spoľahlivo overiť.",
                cs="Bez VIN nelze svolávací akce spolehlivě ověřit.",
                en="Recall campaigns cannot be verified reliably without the VIN.",
            )
        else:
            safe_safety[key] = _display_value(value, language)

    return {
        "schema_version": 1,
        "language": language,
        "listing": listing,
        "verdict": {
            "status": status,
            "label": VERDICT_LABELS[language][status],
            "summary": VERDICT_SUMMARIES[language][status],
            "tone": VERDICT_TONES[status],
            "evidence_quality": _first_text(risk.get("evidence_quality"), evidence.get("overall_confidence"), "LOW").upper(),
        },
        "priority_findings": findings,
        "buyer_actions": buyer_actions,
        "seller_message": _seller_message(buyer_actions, language),
        "pros": _pros(research, vision, language),
        "technical_risks": _technical_risks(research, language),
        "costs": _costs(research, language),
        "market": _market(benchmark, research, language),
        "identity": safe_identity,
        "vin": {
            key: _display_value(value, language) if isinstance(value, str) else value
            for key, value in _dict(research.get("vin_check")).items()
        },
        "safety_and_recall": safe_safety,
        "research_findings": [
            {
                "claim": _public_freeform(_dict(item).get("claim"), language),
                "buyer_impact": _public_freeform(_dict(item).get("buyer_impact"), language),
                "confidence": _text(_dict(item).get("confidence")),
                "evidence_category": _text(_dict(item).get("evidence_category")),
            }
            for item in _list(research.get("web_research_findings"))
            if _public_freeform(_dict(item).get("claim"), language)
            and (
                not _text(_dict(item).get("buyer_impact"))
                or _public_freeform(_dict(item).get("buyer_impact"), language)
            )
        ],
        "vision": {
            "photos_provided": vision.get("photos_provided") is True,
            "visual_verdict": _public_freeform(vision.get("visual_verdict"), language),
            "photo_limitations": _public_string_list(vision.get("photo_limitations"), language, limit=8),
            "exterior_observations": _public_observations(vision.get("exterior_observations"), language),
            "interior_observations": _public_observations(vision.get("interior_observations"), language),
            "warning_lights": _public_observations(vision.get("dashboard_or_warning_lights"), language),
            "visible_red_flags": _public_observations(vision.get("visible_red_flags"), language),
            "supported_observations": _public_observations(vision.get("supported_observations"), language),
            "missing_views": _public_string_list(vision.get("missing_views"), language, limit=8),
            "mileage_wear_consistency": _dict(vision.get("mileage_wear_consistency")),
        },
        "sources": _sources(research),
        "report_markdown": _public_report_markdown(report_markdown, language),
    }


__all__ = ["build_presentation_payload"]
