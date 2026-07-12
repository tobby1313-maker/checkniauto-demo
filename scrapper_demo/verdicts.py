"""Stable screening statuses and localized customer-facing labels."""

from __future__ import annotations

from typing import Final


STATUS_ORDER: Final[tuple[str, ...]] = (
    "WORTH_INSPECTING",
    "INSPECT_WITH_RESERVATIONS",
    "RESOLVE_BEFORE_PROCEEDING",
    "HIGH_RISK",
    "DO_NOT_PROCEED",
)
STATUS_RANK: Final[dict[str, int]] = {
    status: index for index, status in enumerate(STATUS_ORDER)
}

VERDICT_LABELS: Final[dict[str, dict[str, str]]] = {
    "sk": {
        "WORTH_INSPECTING": "🟢 STOJÍ ZA OBHLIADKU",
        "INSPECT_WITH_RESERVATIONS": "🟡 NAJPRV PREVERIŤ",
        "RESOLVE_BEFORE_PROCEEDING": "🟠 RIEŠIŤ LEN S VÝHRADAMI",
        "HIGH_RISK": "🔴 SKÔR NERIEŠIŤ",
        "DO_NOT_PROCEED": "⛔ RUKY PREČ",
    },
    "en": {
        "WORTH_INSPECTING": "🟢 WORTH CHECKING OUT",
        "INSPECT_WITH_RESERVATIONS": "🟡 VERIFY FIRST",
        "RESOLVE_BEFORE_PROCEEDING": "🟠 PROCEED WITH RESERVATIONS",
        "HIGH_RISK": "🔴 PROBABLY SKIP",
        "DO_NOT_PROCEED": "⛔ WALK AWAY",
    },
}

LEGACY_LABEL_TO_STATUS: Final[dict[str, str]] = {
    "🟢 DOBRÁ KÚPA": "WORTH_INSPECTING",
    "🟡 PRIJATEĽNÁ KÚPA": "INSPECT_WITH_RESERVATIONS",
    "🟠 ZVÁŽIŤ": "RESOLVE_BEFORE_PROCEEDING",
    "🔴 RIZIKOVÁ KÚPA": "HIGH_RISK",
    "⛔ EXTRÉMNE RIZIKO": "DO_NOT_PROCEED",
}


def normalize_language(value: str | None) -> str:
    return "en" if str(value or "").strip().lower().startswith("en") else "sk"


def label_for_status(status: str, language: str | None = "sk") -> str:
    if status not in STATUS_RANK:
        raise ValueError(f"Unsupported screening status: {status}")
    return VERDICT_LABELS[normalize_language(language)][status]


def status_for_label(label: str) -> str | None:
    normalized = str(label or "").strip()
    if normalized in LEGACY_LABEL_TO_STATUS:
        return LEGACY_LABEL_TO_STATUS[normalized]
    for labels in VERDICT_LABELS.values():
        for status, candidate in labels.items():
            if normalized == candidate:
                return status
    return None
