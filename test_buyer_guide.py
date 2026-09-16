"""Buyer-guide regression fixtures are synthetic, never claims about real cars."""
from __future__ import annotations
import copy
from datetime import date, timedelta
import json
import unittest
from unittest.mock import patch

from scrapper_demo.beta_contracts import HubError, REVIEW_INSTRUCTIONS, report_template, validate_report
from scrapper_demo.buyer_guide import (
    GUIDE_SCHEMA, IDENTITY_KEYS, SECTION_LABELS, extend_report_schema,
    guide_template, research_coverage, validate_shape,
)

JOB = "beta-" + "a"*32
MANIFEST = {"photos": [
    {"id": "p001", "number": 1, "status": "available"},
    {"id": "p002", "number": 2, "status": "download_failed"},
]}


def source(id, kind="MANUFACTURER", url=None):
    return {"id": id, "title": "Synthetic fixture "+id,
            "url": url or "https://evidence.example/"+id,
            "source_type": kind, "accessed_on": "2025-01-01",
            "applies_to": "Fictional generation, engine, gearbox and market for tests only."}


def point(*refs, **extra):
    return {"title": "Synthetic ownership point", "detail": "Fictional test statement, not vehicle advice.",
            "buyer_relevance": "Check the exact configuration before applying this fixture.",
            "confidence": "MEDIUM", "source_ids": list(refs), **extra}


def full_report(job=JOB, manifest=MANIFEST):
    r = report_template(job, manifest)
    r["summary"] = "Synthetic vehicle-specific report. No real vehicle has been assessed."
    r["sources"] = [source("m"), source("t", "TECHNICAL"), source("o1", "OWNER_ACCOUNT"),
                    source("o2", "OWNER_ACCOUNT"), source("c", "REPAIR_COST")]
    g = r["buyer_guide"]
    for key in SECTION_LABELS:
        g[key].update(status="RESEARCHED", summary="Synthetic researched "+key+"; no real-world claim.",
                      confidence="MEDIUM", source_ids=["m"], gaps=[])
    for key in IDENTITY_KEYS:
        g["identity"]["fields"][key] = {"value": "Fictional "+key, "basis": "SOURCE_SUPPORTED",
                                       "confidence": "MEDIUM", "source_ids": ["m"]}
    g["configuration_verdict"].update(rating="CONDITIONAL", suitable_for=[point("m")])
    for key in ("engine", "transmission", "drivetrain"):
        g[key].update(rating="CONDITIONAL", positives=[point("m")], concerns=[point("t")],
                      maintenance=[point("m", action="Check manufacturer-specific documents.", fixed_interval=True)])
    g["owners"].update(source_ids=["o1", "o2"], praise=[point("o1", "o2", pattern="REPEATED_ACCOUNTS", configuration_match="EXACT_VARIANT")])
    g["model_risks"]["items"] = [{"id": "r1", "component": "Fictional component", "title": "Fictional model weakness",
        "detail": "Synthetic risk used for validation, not a finding on this vehicle.", "applies_to": "Fictional variant only.",
        "priority": "HIGH", "basis": "DOCUMENTED_MODEL_ISSUE", "symptoms": ["Synthetic symptom"],
        "check": "Arrange a configuration-specific professional inspection.", "why_relevant": "Specific fixture configuration.",
        "on_this_car": "NOT_VERIFIED", "photo_ids": [], "source_ids": ["t"], "confidence": "MEDIUM",
        "repair_cost": {"low_eur": 100, "high_eur": 200, "scope": "Fictional total repair estimate for a test.",
                        "as_of": "2025-01-01", "source_ids": ["c"]}}]
    g["buying_checks"]["items"] = [{"when": "BEFORE_VISIT", "priority": "HIGH", "action": "Request the exact service invoice.",
        "why_relevant": "Relates to the fixture's specific configuration.", "red_flag": "A mismatch needs further verification.",
        "basis": "MODEL_SPECIFIC", "source_ids": ["m"], "related_risk_ids": ["r1"]}]
    g["useful_context"]["items"] = [point("m")]
    return r


class BuyerGuideTests(unittest.TestCase):
    def setUp(self):
        self.report = full_report()

    def reject(self, r=None, contains=None):
        with self.assertRaises(HubError) as error:
            validate_report(self.report if r is None else r, JOB, MANIFEST)
        self.assertEqual(error.exception.status, 400)
        if contains:
            self.assertIn(contains, str(error.exception))

    def test_complete_guide_is_valid_and_separates_model_from_individual(self):
        self.report["buyer_guide"]["configuration_verdict"]["rating"] = "GOOD_CHOICE"
        self.report["verdict"] = "AVOID"
        with patch("socket.getaddrinfo", side_effect=AssertionError("Validation must stay offline")):
            validate_report(self.report, JOB, MANIFEST)
        self.assertEqual(research_coverage(self.report)["status"], "COMPLETE")
        self.assertEqual(self.report["verdict"], "AVOID")
        self.assertEqual(self.report["buyer_guide"]["model_risks"]["items"][0]["on_this_car"], "NOT_VERIFIED")

    def test_template_requires_real_summary_and_never_claims_research(self):
        r = report_template(JOB, MANIFEST)
        self.assertEqual(r["schema_version"], 2)
        self.reject(r, "real summary")
        r["summary"] = "Research could not be performed with the available tools."
        validate_report(r, JOB, MANIFEST)
        self.assertEqual(research_coverage(r)["status"], "UNAVAILABLE")

    def test_legacy_import_keeps_original_shape_and_label(self):
        r = report_template(JOB, MANIFEST, version=1)
        r["summary"] = "A legacy listing-only report, not a researched buyer guide."
        r["sources"] = [{"id": "a", "title": "Legacy", "url": "https://evidence.example/a"}]
        before = copy.deepcopy(r)
        validate_report(r, JOB, MANIFEST)
        self.assertEqual(r, before)
        self.assertEqual(research_coverage(r)["status"], "LEGACY")
        self.assertNotIn("buyer_guide", r)

    def test_all_sections_are_required_and_extra_fields_rejected(self):
        for key in SECTION_LABELS:
            with self.subTest(missing=key):
                r = full_report();del r["buyer_guide"][key];self.reject(r)
        r = full_report();r["buyer_guide"]["engine"]["invented_score"] = 99;self.reject(r)

    def test_missing_guide_wrong_version_or_forged_coverage_rejected(self):
        for value in (None, [], "text"):
            r = full_report();r["buyer_guide"] = value;self.reject(r)
        for version in (True, 3, "2"):
            r = full_report();r["schema_version"] = version;self.reject(r)
        r = full_report();r["coverage"] = "COMPLETE";self.reject(r)
        r = full_report();r["schema_version"] = 1;self.reject(r)

    def test_incomplete_sections_need_explicit_gaps(self):
        block = self.report["buyer_guide"]["engine"]
        block["status"] = "LIMITED";self.reject(contains="gap")
        block["gaps"] = ["Exact emissions variant not verified."]
        validate_report(self.report, JOB, MANIFEST)
        self.assertEqual(research_coverage(self.report)["status"], "PARTIAL")
        self.assertEqual(research_coverage(self.report)["researched"], 8)

    def test_unavailable_section_cannot_hide_factual_content(self):
        block = self.report["buyer_guide"]["engine"]
        block.update(status="UNAVAILABLE", confidence="LOW", rating="UNKNOWN", gaps=["No applicable evidence."])
        self.reject(contains="LIMITED")

    def test_listing_only_is_not_researched_model_evidence(self):
        self.report["sources"][0]["source_type"] = "LISTING"
        self.reject(contains="listing-only")

    def test_researched_component_needs_content(self):
        self.report["buyer_guide"]["engine"].update(positives=[], concerns=[], maintenance=[])
        self.reject(contains="useful content")

    def test_refs_must_be_real_at_every_depth(self):
        self.report["buyer_guide"]["engine"]["positives"][0]["source_ids"] = ["nonexistent"]
        self.reject(contains="unknown")
        self.report["buyer_guide"]["engine"]["positives"][0]["source_ids"] = []
        self.reject(contains="sources")

    def test_unknown_engine_code_stays_null(self):
        field = self.report["buyer_guide"]["identity"]["fields"]["engine_code"]
        field.update(value=None, basis="UNKNOWN", confidence="LOW", source_ids=[])
        validate_report(self.report, JOB, MANIFEST)
        field["value"] = "An invented exact code";self.reject(contains="must be null")

    def test_unknown_configuration_cannot_be_marked_fully_identified(self):
        self.report["buyer_guide"]["identity"]["fields"]["engine"].update(value=None, basis="UNKNOWN", confidence="LOW", source_ids=[])
        self.reject(contains="configuration")

    def test_single_owner_does_not_become_consensus(self):
        owner = self.report["buyer_guide"]["owners"]["praise"][0]
        owner["source_ids"] = ["o1"];self.reject(contains="two independent")
        owner["pattern"] = "SINGLE_ACCOUNT";validate_report(self.report, JOB, MANIFEST)

    def test_non_owner_source_cannot_support_owner_account(self):
        self.report["buyer_guide"]["owners"]["praise"][0].update(source_ids=["m"], pattern="SINGLE_ACCOUNT")
        self.reject(contains="source type")

    def test_duplicate_urls_and_tracking_do_not_count_as_independent(self):
        for url in ("https://evidence.example/o1#reply", "https://evidence.example/o1?utm_source=other"):
            r = full_report();r["sources"][3]["url"] = url;self.reject(r, "duplicated URLs")

    def test_other_variant_owner_claims_remain_limited(self):
        owners = self.report["buyer_guide"]["owners"]
        owners["praise"][0]["configuration_match"] = "OTHER_VARIANT";self.reject(contains="variant mismatch")
        owners.update(status="LIMITED", gaps=["Different powertrain; do not generalize."])
        validate_report(self.report, JOB, MANIFEST)

    def test_survey_requires_survey_evidence(self):
        self.report["buyer_guide"]["owners"]["praise"][0]["pattern"] = "SURVEY"
        self.reject(contains="source type")
        self.report["sources"][2]["source_type"] = "OWNER_SURVEY"
        validate_report(self.report, JOB, MANIFEST)

    def test_fixed_interval_requires_primary_source(self):
        service = self.report["buyer_guide"]["transmission"]["maintenance"][0]
        service["source_ids"] = ["t"];self.reject(contains="source type")
        service["fixed_interval"] = False;validate_report(self.report, JOB, MANIFEST)

    def test_cost_ranges_are_optional_and_must_be_sourced(self):
        cost = self.report["buyer_guide"]["model_risks"]["items"][0]["repair_cost"]
        cost["source_ids"] = [];self.reject(contains="sources")
        cost["source_ids"] = ["o1"];self.reject(contains="source type")
        cost["source_ids"] = ["c"];cost["low_eur"] = 300;self.reject(contains="range")
        cost["low_eur"] = True;self.reject(contains="type")
        self.report["buyer_guide"]["model_risks"]["items"][0]["repair_cost"] = None
        validate_report(self.report, JOB, MANIFEST)

    def test_extreme_cost_numbers_fail_cleanly(self):
        for number in (10**400, float("inf"), float("nan"), -1):
            r = full_report()
            r["buyer_guide"]["model_risks"]["items"][0]["repair_cost"]["high_eur"] = number
            self.reject(r)

    def test_source_metadata_missing_or_fake_future_dates_rejected(self):
        r = full_report();del r["sources"][0]["source_type"];self.reject(r)
        for value in ("not-a-date", "2025-02-31", (date.today()+timedelta(days=2)).isoformat()):
            r = full_report();r["sources"][0]["accessed_on"] = value;self.reject(r)

    def test_model_risk_photos_require_actual_inspection(self):
        risk = self.report["buyer_guide"]["model_risks"]["items"][0]
        risk.update(on_this_car="PHOTO_INDICATION", photo_ids=[]);self.reject(contains="inspected photo")
        risk["photo_ids"] = ["p001"];self.reject(contains="uninspected")
        self.report["photo_review"][0]["level"] = "detail"
        validate_report(self.report, JOB, MANIFEST)
        risk["photo_ids"] = ["p002"];self.reject(contains="uninspected")

    def test_owner_reports_are_not_documented_technical_faults(self):
        self.report["buyer_guide"]["model_risks"]["items"][0]["source_ids"] = ["o1"]
        self.reject(contains="source type")
        self.report["buyer_guide"]["model_risks"]["items"][0]["basis"] = "OWNER_REPORTS"
        validate_report(self.report, JOB, MANIFEST)

    def test_generic_checklist_does_not_count_as_model_research(self):
        self.report["buyer_guide"]["buying_checks"]["items"][0]["basis"] = "GENERAL"
        self.reject(contains="generic checks")

    def test_checks_reference_real_risks(self):
        self.report["buyer_guide"]["buying_checks"]["items"][0]["related_risk_ids"] = ["invented"]
        self.reject(contains="unknown related risk")

    def test_empty_no_fault_search_can_be_honest(self):
        self.report["buyer_guide"]["model_risks"]["items"] = []
        self.report["buyer_guide"]["model_risks"]["summary"] = "No applicable issue found in these synthetic sources; this is not proof of absence."
        self.report["buyer_guide"]["buying_checks"]["items"][0]["related_risk_ids"] = []
        validate_report(self.report, JOB, MANIFEST)

    def test_independent_templates_and_schema_do_not_mutate_shared_state(self):
        a = guide_template();a["engine"]["gaps"].clear()
        self.assertTrue(guide_template()["engine"]["gaps"])
        base = {"type": "object", "properties": {"schema_version": {"type": "integer", "enum": [1]}}, "required": ["schema_version"]}
        new = extend_report_schema(base)
        self.assertNotIn("buyer_guide", base["properties"])
        self.assertIn("buyer_guide", new["required"])
        validate_shape(self.report["buyer_guide"], GUIDE_SCHEMA)

    def test_instructions_demand_research_not_just_advertisement_audit(self):
        for term in ("BUYER GUIDE V2 IS REQUIRED", "first-hand owner", "paid API fallback", "nine buyer_guide sections", "model-level risk"):
            self.assertIn(term, REVIEW_INSTRUCTIONS)

    def test_report_size_and_template_are_practical(self):
        self.assertLess(len(json.dumps(self.report, ensure_ascii=False).encode()), 30000)
        self.assertEqual(len(guide_template()), 9)


if __name__ == "__main__":
    unittest.main()
