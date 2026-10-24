import os
import unittest
from unittest.mock import patch

import preflight


class ClassifyStatusTests(unittest.TestCase):
    def test_authentication_is_distinguished(self):
        self.assertIn("AUTHENTICATION_FAILED", preflight.classify_status(401))

    def test_rate_limit_is_distinguished(self):
        self.assertEqual(preflight.classify_status(429), "RATE_LIMITED_OR_QUOTA_EXHAUSTED")

    def test_server_error_is_retryable_category(self):
        self.assertEqual(preflight.classify_status(503), "PROVIDER_SERVER_ERROR")

    def test_model_not_found_is_distinguished(self):
        self.assertIn("MODEL_OR_ENDPOINT_NOT_FOUND", preflight.classify_status(404))

    def test_temporary_failure_is_retried_then_succeeds(self):
        class Response:
            def __init__(self, status):
                self.status_code = status
                self.headers = {}
            def json(self):
                return {}

        with patch.object(preflight.requests, "request", side_effect=[Response(503), Response(200)]) as request, \
             patch.object(preflight.time, "sleep"):
            response, detail = preflight.request_with_retry("GET", "https://example.invalid", provider="test")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(detail, "")
        self.assertEqual(request.call_count, 2)


class ProviderFallbackTests(unittest.TestCase):
    def test_configured_model_is_tried_before_fallback_and_fallback_selected(self):
        results = {
            "my-retired-model": preflight.ProbeResult(False, "MODEL_NOT_AVAILABLE", "rejected"),
            "fallback-model": preflight.ProbeResult(True, "OK", "minimal generation succeeded"),
        }
        with patch.dict(os.environ, {"TEST_KEY": "secret", "TEST_MODEL": "my-retired-model"}, clear=False):
            with patch.object(preflight, "_unique", side_effect=lambda values: list(dict.fromkeys(values))):
                ok, model, category = preflight.check_provider(
                    "test", "TEST_KEY", "TEST_MODEL", "default-model", ["fallback-model"],
                    lambda _key: [], lambda _key, candidate: results[candidate],
                )
        self.assertTrue(ok)
        self.assertEqual(model, "fallback-model")
        self.assertEqual(category, "OK")

    def test_missing_key_is_classified_without_network_call(self):
        with patch.dict(os.environ, {}, clear=True):
            ok, model, category = preflight.check_provider(
                "test", "MISSING_TEST_KEY", "MISSING_TEST_MODEL", "default-model", [],
                lambda _key: self.fail("catalog should not be queried without key"),
                lambda _key, _model: self.fail("model should not be probed without key"),
            )
        self.assertFalse(ok)
        self.assertEqual(model, "")
        self.assertEqual(category, "MISSING_API_KEY")

    def test_current_free_fallbacks_are_configured(self):
        self.assertEqual(preflight.GEMINI_DEFAULT, "gemini-3.8-flash")
        self.assertEqual(preflight.GROQ_DEFAULT, "qwen/qwen3.8-27b")
        self.assertIn("gemma-4-31b-it", preflight.GEMINI_FALLBACKS)
        self.assertIn("gemma-4-26b-a4b-it", preflight.GEMINI_FALLBACKS)
        self.assertIn("allam-2-7b", preflight.GROQ_FALLBACKS)
        self.assertTrue(all(model.endswith(":free") for model in preflight.OPENROUTER_FALLBACKS))
        for model in (
            "google/gemma-4-31b-it:free",
            "google/gemma-4-26b-a4b-it:free",
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "nvidia/nemotron-3.5-super-vl:free",
            "dots-studio/dots-3-note-preview:free",
            "thinkingmachines/inkling-large:free",
            "thinkingmachines/inkling-small:free",
            "poolside/laguna-s-2.1:free",
            "poolside/laguna-xs-2.1:free",
            "liquid/lfm-2.5-2.6b:free",
            "apodex/apodex-1-1-mini:free",
            "cohere/north-mini-code:free",
        ):
            self.assertIn(model, preflight.OPENROUTER_FALLBACKS)
        self.assertNotIn("nvidia/nemotron-3.5-content-safety:free", preflight.OPENROUTER_FALLBACKS)


if __name__ == "__main__":
    unittest.main()
