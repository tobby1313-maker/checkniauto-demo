import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from scrapper_demo.services.analysis_pipeline import (
    AnalysisPipelineDependencies,
    multi_model_analysis_events,
)
from scrapper_demo.storage import ListingJobRepository


def _dependencies(repository, prompt_dir):
    empty_text = lambda *args, **kwargs: ""
    return AnalysisPipelineDependencies(
        repository=repository,
        prompt_dir=prompt_dir,
        build_final_synthesis_context=empty_text,
        build_text_research_context=empty_text,
        compact_json_for_prompt=lambda value: json.dumps(value),
        output_language=lambda value: value,
        inject_photo_vin=empty_text,
        listing_context_text=lambda value, **kwargs: value,
        model_display_name=lambda provider: provider.title(),
        no_photos_vision_result=lambda *args: "{}",
        normalize_report_headings=lambda value: value,
        public_analysis_markdown=lambda value, slug_dir: value,
        replace_photo_analysis_section=lambda value, *args: value,
        replace_quick_summary_scorecard=lambda value, *args: value,
        move_pros_cons_after_quick_summary=lambda value: value,
        save_kb_blocks=lambda blocks: [],
        safe_model_json=lambda value: json.loads(value),
        strip_kb_section=lambda value: value,
        prepare_images=lambda slug_dir: ([], {}),
        stream_text_model=lambda *args, **kwargs: iter(()),
        log=lambda message: None,
    )


class AnalysisPipelineBoundaryTests(unittest.TestCase):
    def test_missing_job_is_reported_without_flask_context(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ListingJobRepository(Path(temp_dir) / "jobs")
            dependencies = _dependencies(repository, Path(temp_dir) / "prompts")

            events = list(
                multi_model_analysis_events(
                    "missing",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )

        self.assertEqual(events, ['data: {"error": "Listing job not found."}\n\n'])

    def test_missing_keys_is_reported_before_provider_or_prompt_access(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = ListingJobRepository(Path(temp_dir) / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample")
            dependencies = _dependencies(repository, Path(temp_dir) / "missing-prompts")

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    [],
                    dependencies=dependencies,
                )
            )

        self.assertEqual(
            events,
            ['data: {"error": "Gemini API keys are not configured on the server."}\n\n'],
        )

    def test_injected_pipeline_preserves_phase_order_and_artifacts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            repository = ListingJobRepository(root / "jobs")
            repository.job_dir("sample", create=True)
            repository.write_text("sample", "car_info.md", "# Sample listing")
            prompt_dir = root / "prompts"
            prompt_dir.mkdir()
            for filename in (
                "grok_text_research_system.md",
                "gemini_vision_system.md",
                "grok_final_synthesis_system.md",
            ):
                (prompt_dir / filename).write_text("system", encoding="utf-8")

            collected_phases = []

            def collect_gemini(entries, phase, factory, **kwargs):
                yield from ()
                collected_phases.append(phase)
                if phase == "web research":
                    return "### Orientacna cena / trh\n- Bez priamych URL.", entries[0]
                if phase == "market comparable research":
                    return (
                        "## Priamo overitelne porovnatelne inzeraty\n"
                        "- [Sample car (2015, 150 000 km)]"
                        "(https://auto.bazos.sk/inzerat/123456/sample.php) — 8 900 EUR — podobny najazd.",
                        entries[0],
                    )
                if phase == "text/research analysis":
                    return '{"listing_facts": {}, "vin_check": {}}', entries[0]
                raise AssertionError(f"Unexpected collected phase: {phase}")

            dependencies = replace(
                _dependencies(repository, prompt_dir),
                normalize_gemini_keys=lambda keys: [
                    {"key": key, "label": f"key-{index}"}
                    for index, key in enumerate(keys)
                ],
                collect_gemini=collect_gemini,
                call_gemini=lambda *args, **kwargs: iter(("# Final report",)),
                calculate_risk_score=lambda *args, **kwargs: {
                    "risk_score": 0,
                    "allowed_final_verdict": "ZVAZIT",
                },
                estimate_request_tokens=lambda *args: 10,
                estimate_output_tokens=lambda chunk: 3,
                validate_json_contract=lambda *args: [],
                validate_final_report=lambda *args: [],
                ensure_end_analysis_marker=lambda value: value + "\n\n<!-- END_ANALYSIS -->",
                write_validation_warnings=lambda *args, **kwargs: None,
                extract_kb_blocks=lambda value: [],
            )

            events = list(
                multi_model_analysis_events(
                    "sample",
                    "",
                    ["gemini-key"],
                    dependencies=dependencies,
                )
            )

            phase_positions = [
                next(index for index, event in enumerate(events) if marker in event)
                for marker in ("Phase 1/4", "Phase 2/4", "Phase 3/4", "Phase 4/4")
            ]
            self.assertEqual(phase_positions, sorted(phase_positions))
            self.assertIn("https://auto.bazos.sk/inzerat/123456/sample.php", repository.read_text("sample", "web_research.md"))
            self.assertEqual(
                collected_phases[:3],
                ["web research", "market comparable research", "text/research analysis"],
            )
            self.assertTrue(repository.read_text("sample", "grok_research.json"))
            self.assertTrue(repository.read_text("sample", "gemini_vision.json"))
            self.assertTrue(repository.read_text("sample", "risk_score.json"))
            self.assertIn(
                "buyer_scorecard",
                json.loads(repository.read_text("sample", "risk_score.json")),
            )
            self.assertEqual(repository.read_text("sample", "analysis_result_raw.md"), "# Final report")
            self.assertIn("<!-- END_ANALYSIS -->", repository.read_text("sample", "analysis_result.md"))
            self.assertIn('"done": true', events[-1])


if __name__ == "__main__":
    unittest.main()
