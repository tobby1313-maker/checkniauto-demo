"""Offline browser checks of the real report UI using synthetic report data."""
import io
import json
import os
import re
from pathlib import Path
import unittest
from urllib.parse import urlsplit

from test_buyer_guide import JOB, MANIFEST, full_report
from scrapper_demo.beta_contracts import report_template

ROOT = Path(__file__).parent
ORIGIN = "http://127.0.0.1"


@unittest.skipUnless(os.environ.get("CHECKNI_BROWSER_TESTS") == "1", "Enable browser tests explicitly")
class BuyerGuideBrowserTests(unittest.TestCase):
    def setUp(self):
        from playwright.sync_api import sync_playwright
        from PIL import Image
        data = io.BytesIO();Image.new("RGB", (10, 10)).save(data, "JPEG");self.photo = data.getvalue()
        self.pw = sync_playwright().start()
        browser_path = os.environ.get("CHECKNI_TEST_BROWSER")
        self.browser = self.pw.chromium.launch(headless=True, **({"executable_path":browser_path} if browser_path else {}))
        self.context = self.browser.new_context(viewport={"width":390,"height":844})
        self.context.set_default_timeout(10000)
        self.addCleanup(self.pw.stop);self.addCleanup(self.browser.close);self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.errors = [];self.page.on("pageerror", lambda error:self.errors.append(str(error)))
        self.report = full_report()
        self.job_status = "DONE"

    def open(self):
        # The browser sees real HTML/CSS/JS, but no navigation or outbound fetch.
        # Location/fetch are explicit test collaborators; production script is unmodified.
        self.page.close()
        self.page = self.context.new_page()
        self.errors = []
        self.page.on("pageerror", lambda error:self.errors.append(str(error)))
        html = (ROOT/"web"/"beta.html").read_text()
        html = re.sub(r'<script[^>]*>.*?</script>|<link[^>]*rel="stylesheet"[^>]*>', '', html)
        self.page.set_content(html)
        self.page.add_style_tag(content=(ROOT/"web"/"assets"/"beta.css").read_text())
        job = {"id":JOB,"title":"Synthetic test vehicle","status":self.job_status,
               "report":self.report,"photos":MANIFEST["photos"],"warnings":[]}
        self.page.evaluate("""({script, job}) => {
          const testLocation = {pathname:'/analysis/'+job.id};
          const testFetch = async path => {
            if(path !== '/_beta/jobs/'+job.id)throw new Error('Unexpected request: '+path);
            return {ok:true, json:async()=>structuredClone(job)};
          };
          new Function('location','fetch',script)(testLocation,testFetch);
        }""", {"script":(ROOT/"web"/"assets"/"beta.js").read_text(), "job":job})
        self.page.locator(".gallery").wait_for()
        self.assertEqual(self.errors, [])

    def test_sections_two_verdicts_source_metadata_and_mobile_layout(self):
        self.open()
        self.assertEqual(self.page.locator(".verdict-grid article").count(), 2)
        self.assertEqual(self.page.locator(".research-section").count(), 9)
        self.assertEqual(self.page.locator(".research-coverage").get_attribute("data-status"), "COMPLETE")
        self.assertIn("Tento konkrétny kus", self.page.locator(".verdict-grid").inner_text())
        self.assertIn("Motor", self.page.locator("#guide-engine").inner_text())
        self.assertIn("Skúsenosti majiteľov", self.page.locator("#guide-owners").inner_text())
        self.assertIn("nie sú reprezentatívna", self.page.locator("#guide-owners").inner_text())
        self.assertIn("Neoverené", self.page.locator("#risk-r1").inner_text())
        self.assertIn("2025-01-01", self.page.locator("#source-m").inner_text())
        self.assertEqual(self.page.locator(".gallery figure").count(), 2)
        self.assertIn("Neposúdená", self.page.locator("#p001").inner_text())
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        if os.environ.get("CHECKNI_SCREENSHOT_DIR"):
            self.page.screenshot(path=os.environ["CHECKNI_SCREENSHOT_DIR"]+"/buyer-guide-mobile.png")
        self.page.set_viewport_size({"width":1440,"height":1050})
        self.assertTrue(self.page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))
        if os.environ.get("CHECKNI_SCREENSHOT_DIR"):
            self.page.screenshot(path=os.environ["CHECKNI_SCREENSHOT_DIR"]+"/buyer-guide-desktop.png")

    def test_partial_research_is_visible_even_when_job_is_done(self):
        self.report["buyer_guide"]["engine"].update(status="LIMITED",gaps=["Missing exact engine variant evidence."])
        self.open()
        self.assertEqual(self.page.locator(".research-coverage").get_attribute("data-status"), "PARTIAL")
        self.assertIn("8/9", self.page.locator(".research-coverage").inner_text())
        self.assertIn("Missing exact", self.page.locator("#guide-engine .research-gaps").inner_text())

    def test_legacy_report_is_readable_without_fake_research(self):
        self.report = report_template(JOB,MANIFEST,version=1)
        self.report["summary"] = "Legacy listing-only report."
        self.open()
        self.assertEqual(self.page.locator(".research-section").count(), 0)
        self.assertEqual(self.page.locator(".research-coverage").get_attribute("data-status"), "LEGACY")
        self.assertIn("Legacy listing-only", self.page.locator("main").inner_text())

    def test_unavailable_research_and_prompt_do_not_claim_completed_work(self):
        self.report = report_template(JOB,MANIFEST)
        self.report["summary"] = "Research is unavailable, not a complete buyer guide."
        self.open()
        self.assertEqual(self.page.locator(".research-coverage").get_attribute("data-status"), "UNAVAILABLE")
        self.job_status="WAITING_FOR_AI";self.report=None
        self.open();self.page.get_by_text("Pokyn pre ChatGPT",exact=True).click()
        text = self.page.locator("main pre").inner_text()
        for term in ("modelový výskum", "skúsenosti majiteľov", "schema_version=2", "platené API"):
            self.assertIn(term,text)

    def test_model_text_cannot_inject_html_or_execute_script(self):
        attack = '<img src=x onerror="window.PWNED=1">'
        self.report["buyer_guide"]["engine"]["positives"][0]["title"] = attack
        self.report["sources"][0]["title"] = '<script>window.PWNED=1</script>'
        self.open()
        self.assertIsNone(self.page.evaluate("window.PWNED"))
        self.assertEqual(self.page.locator("#guide-engine img").count(),0)
        self.assertIn(attack,self.page.locator("#guide-engine").inner_text())


if __name__ == "__main__":unittest.main()
