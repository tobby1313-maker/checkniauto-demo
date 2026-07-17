import json
import os
import tempfile
import unittest
from pathlib import Path

import web_server
from scrapper_demo import DemoServerConfig, create_app


class DemoServerConfigTest(unittest.TestCase):
    def test_defaults_match_the_public_demo_contract(self):
        config = DemoServerConfig.from_env(Path(__file__).parent, environ={})

        self.assertTrue(config.demo_mode)
        self.assertEqual(config.demo_prompt_file, "analyze_prompt_v4_koyeb.txt")
        self.assertEqual(config.demo_rate_limit_per_ip, "3/day")
        self.assertEqual(config.demo_max_concurrent_jobs, 1)
        self.assertEqual(config.demo_job_ttl_minutes, 60)
        self.assertEqual(config.demo_max_manual_images, 12)
        self.assertEqual(config.demo_max_scraped_images, 0)
        self.assertTrue(config.demo_skip_kb)
        self.assertEqual(config.demo_max_upload_mb, 24)
        self.assertEqual(config.max_upload_bytes, 24 * 1024 * 1024)
        self.assertTrue(config.auta_dir.endswith(os.path.join("scrapper-demo", "Auta")))

    def test_environment_values_are_parsed_once_and_paths_are_derived(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config = DemoServerConfig.from_env(
                Path(__file__).parent,
                environ={
                    "DEMO_MODE": "false",
                    "DEMO_SKIP_KB": "0",
                    "DEMO_MAX_CONCURRENT_JOBS": "0",
                    "DEMO_JOB_TTL_MINUTES": "2",
                    "DEMO_MAX_MANUAL_IMAGES": "-1",
                    "DEMO_MAX_SCRAPED_IMAGES": "-5",
                    "DEMO_MAX_UPLOAD_MB": "0",
                    "SCRAPPER_DATA_DIR": temp_dir,
                    "FLASK_SECRET_KEY": "configured-secret",
                    "GEMINI_PRIMARY_API_KEY": " primary ",
                    "GEMINI_SECOND_BACKUP_API_KEY": " second-backup ",
                },
            )

        self.assertFalse(config.demo_mode)
        self.assertFalse(config.demo_skip_kb)
        self.assertEqual(config.demo_max_concurrent_jobs, 1)
        self.assertEqual(config.demo_job_ttl_minutes, 5)
        self.assertEqual(config.demo_max_manual_images, 0)
        self.assertEqual(config.demo_max_scraped_images, 0)
        self.assertEqual(config.demo_max_upload_mb, 1)
        self.assertEqual(config.auta_dir, os.path.join(temp_dir, "Auta"))
        self.assertEqual(config.flask_secret_key, "configured-secret")
        self.assertEqual(config.gemini_primary_api_key, "primary")
        self.assertEqual(config.gemini_second_backup_api_key, "second-backup")

    def test_invalid_numeric_configuration_fails_with_variable_name(self):
        with self.assertRaisesRegex(ValueError, "DEMO_JOB_TTL_MINUTES"):
            DemoServerConfig.from_env(
                Path(__file__).parent,
                environ={"DEMO_JOB_TTL_MINUTES": "one-hour"},
            )

    def test_cost_optimized_profile_is_accepted_for_internal_evaluation(self):
        config = DemoServerConfig.from_env(
            Path(__file__).parent,
            environ={"DEMO_ANALYSIS_PROFILE": "cost_optimized"},
        )

        self.assertEqual(config.demo_analysis_profile, "cost_optimized")


class ApplicationFactoryTest(unittest.TestCase):
    def test_registered_http_views_are_owned_by_route_blueprints(self):
        app = create_app(
            {
                "TESTING": True,
                "DEMO_MODE": True,
                "DEMO_RATE_LIMIT_PER_IP": "0",
            }
        )

        blueprint_views = {
            endpoint: view
            for endpoint, view in app.view_functions.items()
            if endpoint != "static"
        }
        self.assertTrue(blueprint_views)
        self.assertTrue(all(endpoint.startswith("public.") for endpoint in blueprint_views))
        self.assertTrue(
            all(
                view.__module__ == "scrapper_demo.routes._registration"
                for view in blueprint_views.values()
            )
        )

    def test_factory_registers_public_and_private_blueprints_by_mode(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first_jobs = os.path.join(temp_dir, "first", "Auta")
            second_jobs = os.path.join(temp_dir, "second", "Auta")
            first = create_app(
                {
                    "TESTING": True,
                    "DEMO_MODE": True,
                    "DEMO_RATE_LIMIT_PER_IP": "0",
                    "SCRAPPER_AUTA_DIR": first_jobs,
                    "SECRET_KEY": "first-secret",
                }
            )
            second = create_app(
                {
                    "TESTING": True,
                    "DEMO_MODE": False,
                    "DEMO_RATE_LIMIT_PER_IP": "0",
                    "SCRAPPER_AUTA_DIR": second_jobs,
                    "SECRET_KEY": "second-secret",
                }
            )

            self.assertIsNot(first, second)
            self.assertTrue(Path(first_jobs).is_dir())
            self.assertTrue(Path(second_jobs).is_dir())
            self.assertEqual(first.secret_key, "first-secret")
            self.assertEqual(second.secret_key, "second-secret")

            first_health = first.test_client().get("/healthz")
            second_health = second.test_client().get("/healthz")
            first_private = first.test_client().get("/api/listings")
            second_private = second.test_client().get("/api/listings")

            self.assertEqual(first_health.get_json(), {"status": "ok", "demo_mode": True})
            self.assertEqual(second_health.get_json(), {"status": "ok", "demo_mode": False})
            self.assertEqual(first_private.status_code, 404)
            self.assertEqual(second_private.status_code, 200)
            self.assertEqual(second_private.get_json(), [])
            self.assertIn("public", first.blueprints)
            self.assertNotIn("private", first.blueprints)
            self.assertIn("public", second.blueprints)
            self.assertIn("private", second.blueprints)
            self.assertLess(len(first.url_map._rules), len(second.url_map._rules))
            self.assertEqual(
                {
                    (rule.rule, frozenset(rule.methods))
                    for rule in second.url_map.iter_rules()
                },
                {
                    (rule.rule, frozenset(rule.methods))
                    for rule in web_server.app.url_map.iter_rules()
                },
            )

    def test_legacy_entry_point_keeps_original_flask_application_name(self):
        self.assertEqual(web_server.app.name, "web_server")

    def test_data_dir_override_derives_job_dir_and_upload_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            app = create_app(
                {
                    "TESTING": True,
                    "SCRAPPER_DATA_DIR": temp_dir,
                    "DEMO_MAX_UPLOAD_MB": 7,
                }
            )

            self.assertEqual(app.config["SCRAPPER_AUTA_DIR"], os.path.join(temp_dir, "Auta"))
            self.assertEqual(app.config["MAX_CONTENT_LENGTH"], 7 * 1024 * 1024)
            self.assertTrue(Path(app.config["SCRAPPER_AUTA_DIR"]).is_dir())

    def test_factory_web_and_knowledge_base_paths_are_request_local(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            web_dir = Path(temp_dir) / "web"
            kb_dir = Path(temp_dir) / "knowledge_base"
            engine_dir = kb_dir / "engines"
            web_dir.mkdir()
            engine_dir.mkdir(parents=True)
            (web_dir / "index.html").write_text("factory frontend", encoding="utf-8")
            (engine_dir / "fixture.json").write_text(
                json.dumps({"aliases": ["fixture-engine"], "last_updated": "2026-07-11"}),
                encoding="utf-8",
            )
            app = create_app(
                {
                    "TESTING": True,
                    "DEMO_MODE": False,
                    "SCRAPPER_WEB_DIR": str(web_dir),
                    "SCRAPPER_KB_DIR": str(kb_dir),
                    "SCRAPPER_AUTA_DIR": str(Path(temp_dir) / "Auta"),
                }
            )

            index_response = app.test_client().get("/")
            kb_response = app.test_client().get("/api/kb")

            self.assertEqual(index_response.get_data(as_text=True), "factory frontend")
            self.assertEqual(kb_response.status_code, 200)
            self.assertEqual(kb_response.get_json()["engines"][0]["filename"], "fixture.json")
            index_response.close()

    def test_factory_api_key_overrides_are_visible_only_in_factory_context(self):
        app = create_app(
            {
                "TESTING": True,
                "GEMINI_PRIMARY_API_KEY": "factory-primary",
                "GEMINI_BACKUP_API_KEY": "factory-backup",
                "GEMINI_SECOND_BACKUP_API_KEY": "factory-second-backup",
                "GROK_API_KEY": "factory-grok",
                "OPENROUTER_API_KEY": "factory-openrouter",
            }
        )

        with app.test_request_context("/"):
            self.assertEqual(
                web_server._demo_api_keys(),
                [
                    "factory-primary",
                    "factory-backup",
                    "factory-second-backup",
                ],
            )
            self.assertEqual(web_server._demo_grok_api_key(), "factory-grok")
            self.assertEqual(web_server._demo_openrouter_api_key(), "factory-openrouter")


if __name__ == "__main__":
    unittest.main()
