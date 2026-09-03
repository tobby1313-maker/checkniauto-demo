from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from v2_ai_client import call_generate_content_json
from v2_config import (
    MAX_DETAIL_IMAGES,
    VISION_FALLBACK_MODELS,
    VISION_MODEL,
    _model_candidates,
    _unique,
)
from v2_images import mark_detail_reviewed, prepare_detail_images, public_gallery
from v2_schemas import PHOTO_OVERVIEW_SCHEMA, PHOTO_SCHEMA


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _photo_ref(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"(?:foto|photo)[\s_-]*0*(\d{1,3})", text, flags=re.I)
    if not match:
        return ""
    number = int(match.group(1))
    return f"Foto {number:02d}" if number < 100 else f"Foto {number:03d}"


def _finding_refs(finding: dict[str, Any]) -> list[str]:
    refs = []
    for value in _list_of_strings(finding.get("photo_refs")):
        normalized = _photo_ref(value)
        if normalized and normalized not in refs:
            refs.append(normalized)
    return refs


def _candidate_refs(overview: dict[str, Any]) -> list[str]:
    priority_rank = {"high": 0, "medium": 1, "low": 2}
    candidates = sorted(
        _list_of_dicts(overview.get("detail_candidates")),
        key=lambda item: priority_rank.get(str(item.get("priority") or "low"), 2),
    )
    refs: list[str] = []
    for candidate in candidates:
        normalized = _photo_ref(candidate.get("photo_ref"))
        if normalized and normalized not in refs:
            refs.append(normalized)

    # Risk findings always deserve the opportunity for an individual-photo verification.
    for finding in _list_of_dicts(overview.get("findings")):
        if finding.get("severity") not in {"risk", "critical"}:
            continue
        for normalized in _finding_refs(finding):
            if normalized not in refs:
                refs.append(normalized)
    return refs[: max(MAX_DETAIL_IMAGES * 2, MAX_DETAIL_IMAGES)]


def _finding_key(finding: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    title = re.sub(r"\s+", " ", str(finding.get("title") or "")).strip().lower()
    return title, tuple(sorted(_finding_refs(finding)))


def _normalize_finding(
    finding: dict[str, Any],
    inspection_level: str,
) -> dict[str, Any]:
    normalized = dict(finding)
    normalized["photo_refs"] = _finding_refs(normalized)
    normalized["inspection_level"] = inspection_level
    minimum = max(0, int(normalized.get("cost_min_eur") or 0))
    maximum = max(minimum, int(normalized.get("cost_max_eur") or 0))
    normalized["cost_min_eur"] = minimum
    normalized["cost_max_eur"] = maximum
    if normalized.get("severity") not in {"info", "watch", "risk", "critical"}:
        normalized["severity"] = "watch"
    if normalized.get("confidence") not in {"high", "medium", "low"}:
        normalized["confidence"] = "low"
    return normalized


def _merge_findings(
    overview_findings: list[dict[str, Any]],
    detail_findings: list[dict[str, Any]],
    detail_refs: set[str],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()

    for raw in detail_findings:
        finding = _normalize_finding(raw, "detail")
        key = _finding_key(finding)
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)

    for raw in overview_findings:
        finding = _normalize_finding(raw, "overview")
        refs = set(_finding_refs(finding))
        # The individual-photo pass is authoritative for every selected reference.
        # Its findings replace (or dismiss) the coarser collage observation.
        if refs & detail_refs:
            continue
        key = _finding_key(finding)
        if key in seen:
            continue
        seen.add(key)
        merged.append(finding)

    severity_rank = {"critical": 0, "risk": 1, "watch": 2, "info": 3}
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    merged.sort(
        key=lambda item: (
            severity_rank.get(str(item.get("severity")), 2),
            confidence_rank.get(str(item.get("confidence")), 2),
            0 if item.get("inspection_level") == "detail" else 1,
        )
    )
    return merged[:10]


def _coverage_summary(
    manifest: dict[str, Any],
    detail_count: int,
    language: str,
) -> str:
    total = int(manifest.get("gallery_total", 0) or 0)
    unique = int(manifest.get("gallery_unique", 0) or 0)
    duplicates = int(manifest.get("duplicate_count", 0) or 0)
    sheets = int(manifest.get("overview_sheet_count", 0) or 0)
    if language == "cs":
        return (
            f"Galerie obsahuje {total} fotografií. {unique} odlišných záběrů prošlo "
            f"přehledovou kontrolou v {sheets} kolážích, {detail_count} záběrů také "
            f"detailní kontrolou a {duplicates} téměř shodných fotografií bylo "
            "přiřazeno ke svému reprezentativnímu záběru."
        )
    return (
        f"Galéria obsahuje {total} fotografií. {unique} odlišných záberov prešlo "
        f"prehľadovou kontrolou v {sheets} kolážach, {detail_count} záberov aj "
        f"detailnou kontrolou a {duplicates} takmer zhodných fotografií bolo "
        "priradených k reprezentatívnemu záberu."
    )


def analyze_photos(
    listing: dict[str, Any],
    image_package: dict[str, Any],
    language: str,
) -> dict[str, Any]:
    """Run a two-tier visual review without losing any listing photo.

    Tier 1 reviews all unique views in labelled 2x2 contact sheets. Near duplicates
    remain in the final manifest but share the overview evidence of their cluster.
    Tier 2 individually inspects only model-selected risks plus a spread safety sample.
    """
    manifest = public_gallery(image_package)
    overview_images = [
        item for item in image_package.get("overview_images", []) if isinstance(item, dict)
    ]
    if not manifest["gallery_total"] or not overview_images:
        return {
            "available": False,
            "images_reviewed": 0,
            "gallery_total": manifest["gallery_total"],
            "gallery_unique": manifest["gallery_unique"],
            "duplicate_count": manifest["duplicate_count"],
            "overview_unique_count": 0,
            "overview_sheet_count": 0,
            "detail_count": 0,
            "visual_coverage_count": 0,
            "visual_coverage_percent": 0,
            "summary": "Fotografie neboli dostupné na analýzu.",
            "findings": [],
            "positive_signals": [],
            "coverage_gaps": [
                "Nie je možné vizuálne preveriť karosériu, interiér ani pneumatiky."
            ],
            "limitations": [
                "Bez fotografií nemožno posúdiť viditeľné poškodenia ani opotrebenie."
            ],
            "clusters": manifest["clusters"],
            "gallery": manifest["gallery"],
        }

    language_name = "češtine" if language == "cs" else "slovenčine"
    sheet_map = "\n".join(
        f"- {item['label']}: {', '.join(item.get('contains', []))}"
        for item in overview_images
    )
    duplicate_map = "\n".join(
        f"- {cluster['representative']} reprezentuje: {', '.join(cluster['members'])}"
        for cluster in manifest["clusters"]
        if len(cluster.get("members", [])) > 1
    ) or "- Žiadne takmer identické skupiny."

    overview_prompt = f"""
Si opatrný automobilový vizuálny inšpektor. Odpovedaj v {language_name}.

Dostávaš označené 2x2 prehľadové koláže. Každý odlišný záber z galérie je v jednej
z koláží. Takmer identické fotografie sa nezahodili: sú uvedené v mape skupín a
reprezentuje ich najkvalitnejší záber. Nepočítaj podobné fotografie ako viac dôkazov.

Vozidlo: {listing.get('title')}
Rok: {listing.get('year') or 'neuvedený'}
Najazdené km: {listing.get('mileage_km') or 'neuvedené'}

Koláže:
{sheet_map}

Skupiny podobných fotografií:
{duplicate_map}

Úloha:
1. Prezri všetky označené zábery a uveď iba viditeľné pozorovania.
2. Hľadaj rozdiely odtieňov laku, medzery panelov, poškodenia, hrdzu, pneumatiky,
   disky, svetlá, opotrebenie interiéru, kontrolky, vlhkosť a motorový priestor.
3. Nevyhlasuj haváriu, stočený nájazd ani poruchu ako fakt len z náznaku.
4. Vyber fotografie, ktoré majú ísť do individuálnej detailnej kontroly. Prioritu
   majú možné riziká, jemné detaily, kontrolky, poškodenia, pneumatiky a motor.
5. Používaj presné referencie vložené do koláže, napríklad "Foto 07".
6. Uveď najviac 8 významných zistení a najviac 12 detail_candidates.
""".strip()

    overview = call_generate_content_json(
        overview_prompt,
        PHOTO_OVERVIEW_SCHEMA,
        overview_images,
        _model_candidates(VISION_MODEL, VISION_FALLBACK_MODELS),
    )
    overview["available"] = True
    overview["findings"] = _list_of_dicts(overview.get("findings"))[:8]
    requested_refs = _candidate_refs(overview)
    detail_images = prepare_detail_images(image_package, requested_refs)
    detail_refs = {str(item.get("label") or "") for item in detail_images}

    detail: dict[str, Any] = {
        "available": False,
        "images_reviewed": 0,
        "summary": "",
        "findings": [],
        "positive_signals": [],
        "coverage_gaps": [],
        "limitations": [],
    }
    detail_error = ""
    if detail_images:
        detail_map = "\n".join(
            f"- {item['label']} = pôvodný súbor {item['original_name']}"
            for item in detail_images
        )
        detail_prompt = f"""
Si senior automobilový vizuálny inšpektor. Odpovedaj v {language_name}.
Dostávaš individuálne fotografie vo vyššom rozlíšení. Ide o zábery vybrané po
prehľadovej kontrole celej galérie a o rozloženú bezpečnostnú vzorku.

Vozidlo: {listing.get('title')}
Rok: {listing.get('year') or 'neuvedený'}
Najazdené km: {listing.get('mileage_km') or 'neuvedené'}

Mapovanie:
{detail_map}

Predbežné zistenia z koláží:
{json.dumps(overview.get('findings', []), ensure_ascii=False, indent=2)[:18000]}

Skontroluj jemné detaily. Predbežné podozrenie potvrď, spresni alebo ho jednoducho
neuvádzaj, keď detailná fotografia neposkytuje oporu. Nepoužívaj fotografiu ako
dôkaz mechanickej poruchy. Uveď najviac 8 hodnotných zistení. Referencie musia byť
presné, napríklad "Foto 07".
""".strip()
        try:
            detail = call_generate_content_json(
                detail_prompt,
                PHOTO_SCHEMA,
                detail_images,
                _model_candidates(VISION_MODEL, VISION_FALLBACK_MODELS),
            )
            detail["available"] = True
            detail["images_reviewed"] = len(detail_images)
        except Exception as exc:
            detail_error = str(exc)
            detail = {
                "available": False,
                "images_reviewed": 0,
                "summary": "",
                "findings": [],
                "positive_signals": [],
                "coverage_gaps": [],
                "limitations": [detail_error],
            }

    if detail.get("available"):
        mark_detail_reviewed(image_package, detail_refs)
    else:
        detail_images = []
        detail_refs = set()

    manifest = public_gallery(image_package)
    overview_findings = _list_of_dicts(overview.get("findings"))
    detail_findings = _list_of_dicts(detail.get("findings"))
    findings = _merge_findings(overview_findings, detail_findings, detail_refs)

    summary_parts = [_coverage_summary(manifest, len(detail_images), language)]
    model_summary = str(detail.get("summary") or overview.get("summary") or "").strip()
    if model_summary:
        summary_parts.append(model_summary)

    limitations = _unique(
        [
            *_list_of_strings(overview.get("limitations")),
            *_list_of_strings(detail.get("limitations")),
        ]
    )
    if manifest["overview_unique_count"] < manifest["gallery_unique"]:
        limitations.append(
            (
                "Galerie překročila bezpečnostní limit přehledové kontroly; "
                "část odlišných záběrů zůstala pouze v inventáři."
            )
            if language == "cs"
            else (
                "Galéria prekročila bezpečnostný limit pre prehľadovú kontrolu; "
                "časť odlišných záberov zostala iba v inventári."
            )
        )
    if detail_error:
        limitations.append(
            (
                "Detailní kontrola vybraných záběrů selhala; zůstala pouze zjištění z koláží."
            )
            if language == "cs"
            else (
                "Detailná kontrola vybraných záberov zlyhala; zostali iba zistenia z koláží."
            )
        )

    result = {
        "available": True,
        # Legacy metric: how many original listing photos are covered by either a
        # reviewed representative or its near-duplicate cluster.
        "images_reviewed": manifest["visual_coverage_count"],
        "gallery_total": manifest["gallery_total"],
        "gallery_unique": manifest["gallery_unique"],
        "duplicate_count": manifest["duplicate_count"],
        "overview_unique_count": manifest["overview_unique_count"],
        "overview_sheet_count": manifest["overview_sheet_count"],
        "detail_count": len(detail_images),
        "visual_coverage_count": manifest["visual_coverage_count"],
        "visual_coverage_percent": manifest["visual_coverage_percent"],
        "summary": " ".join(summary_parts),
        "findings": findings,
        "positive_signals": _unique(
            [
                *_list_of_strings(detail.get("positive_signals")),
                *_list_of_strings(overview.get("positive_signals")),
            ]
        )[:8],
        "coverage_gaps": _unique(
            [
                *_list_of_strings(overview.get("coverage_gaps")),
                *_list_of_strings(detail.get("coverage_gaps")),
            ]
        )[:10],
        "limitations": limitations[:10],
        "clusters": manifest["clusters"],
        "gallery": manifest["gallery"],
    }

    job_dir = Path(str(image_package.get("job_dir") or "."))
    (job_dir / "gallery_manifest.json").write_text(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key
                in {
                    "gallery_total",
                    "gallery_unique",
                    "duplicate_count",
                    "overview_unique_count",
                    "overview_sheet_count",
                    "detail_count",
                    "visual_coverage_count",
                    "visual_coverage_percent",
                    "clusters",
                    "gallery",
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result
