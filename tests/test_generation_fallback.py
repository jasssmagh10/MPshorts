import os
import unittest
from unittest.mock import patch

import generate_script


class ModelFallbackTests(unittest.TestCase):
    def test_configured_model_is_first_and_candidates_are_unique(self):
        with patch.dict(os.environ, {"GROQ_MODEL": "custom-model"}, clear=False):
            models = generate_script.model_candidates("groq")
        self.assertEqual(models[0], "custom-model")
        self.assertEqual(len(models), len(set(models)))
        self.assertIn("openai/gpt-oss-20b", models)

    def test_generation_moves_to_next_model_after_failure(self):
        attempted = []

        def fake_retry(fn, prompt, temperature, label, attempts=3):
            # The model is encoded in the label by call_with_model_fallback.
            attempted.append(label)
            if len(attempted) == 1:
                raise RuntimeError("first model unavailable")
            return "successful response"

        with patch.dict(os.environ, {"GEMINI_MODEL": "custom-model", "GEMINI_API_KEY": "fake-key"}, clear=False), \
             patch.object(generate_script, "call_with_retry", side_effect=fake_retry):
            result = generate_script.call_with_model_fallback(
                "gemini", lambda prompt, temperature, model=None: model,
                "prompt", 0.2, "test", attempts=2,
            )
        self.assertEqual(result, "successful response")
        self.assertEqual(len(attempted), 2)
        self.assertIn("custom-model", attempted[0])
        self.assertIn("gemini-3.8-flash", attempted[1])


if __name__ == "__main__":
    unittest.main()
