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
        self.assertIn('class="journey-car-svg"', self.html)
        self.assertIn('class="journey-wheel journey-wheel-rear"', self.html)
        self.assertIn('class="journey-wheel journey-wheel-front"', self.html)
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
        self.assertIn(".journey-wheel", self.css)
        self.assertIn("--journey-progress", self.css)


if __name__ == "__main__":
    unittest.main()
