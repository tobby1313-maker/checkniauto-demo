import unittest
from pathlib import Path


class RedesignProgressTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        cls.html = (root / "web" / "index.html").read_text(encoding="utf-8")
        cls.css = (root / "web" / "assets" / "redesign.css").read_text(
            encoding="utf-8"
        )
        cls.javascript = (
            root / "web" / "assets" / "redesign-landing.js"
        ).read_text(encoding="utf-8")

    def test_hybrid_progress_uses_svg_car_and_classic_finish(self):
        self.assertIn('src="/assets/journey-car.png"', self.html)
        self.assertNotIn("journey-window", self.html)
        self.assertNotIn("journey-wheel", self.html)
        self.assertIn('class="journey-road-surface"', self.html)
        self.assertIn('class="journey-finish"', self.html)
        self.assertNotIn("🚗", self.html)
        self.assertNotIn("🏁", self.html)

    def test_completion_crosses_finish_before_navigation(self):
        completion = self.javascript.index("await completeJourney();")
        navigation = self.javascript.index("location.assign(", completion)

        self.assertLess(completion, navigation)
        self.assertIn('.classList.add("is-complete")', self.javascript)
        self.assertIn(".journey-road.is-complete .journey-car", self.css)

    def test_progress_keeps_one_customer_message_and_reduced_motion(self):
        self.assertEqual(self.html.count("data-progress-status"), 1)
        self.assertNotIn("journey-note", self.html)
        self.assertIn("@media (prefers-reduced-motion: reduce)", self.css)
        self.assertNotIn("journey-wheel-spin", self.css)
        self.assertIn("--journey-progress", self.css)

    def test_failed_admin_analysis_exposes_debug_bundle_button(self):
        self.assertIn("showDebuggingBundleLink", self.javascript)
        self.assertIn("/api/admin/debugging-bundles/", self.javascript)
        self.assertIn("/api/token-usage?limit=1", self.javascript)
        self.assertIn("error.slug = data.slug || slug", self.javascript)


if __name__ == "__main__":
    unittest.main()
