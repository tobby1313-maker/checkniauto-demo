import io
import unittest

import main
import web_server
from scrapper_demo import logging as demo_logging
from scrapper_demo.providers import gemini, grok, openrouter


class SharedLoggingTests(unittest.TestCase):
    def test_all_runtime_surfaces_share_one_safe_log_function(self):
        self.assertIs(main.safe_print, demo_logging.safe_log)
        self.assertIs(web_server.safe_log, demo_logging.safe_log)
        self.assertIs(gemini.safe_log, demo_logging.safe_log)
        self.assertIs(grok.safe_log, demo_logging.safe_log)
        self.assertIs(openrouter.safe_log, demo_logging.safe_log)

    def test_safe_log_replaces_unencodable_console_characters(self):
        buffer = io.BytesIO()
        stream = io.TextIOWrapper(buffer, encoding="ascii", errors="strict")

        demo_logging.safe_log("vehicle 🚗", stream=stream)
        stream.flush()

        self.assertEqual(buffer.getvalue().decode("ascii").splitlines(), ["vehicle ?"])


if __name__ == "__main__":
    unittest.main()
