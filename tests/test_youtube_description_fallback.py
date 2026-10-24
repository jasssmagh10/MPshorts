import os
import unittest
from unittest.mock import patch

import requests
import generate_youtube_description as desc


class YouTubeDescriptionFallbackTests(unittest.TestCase):
    def test_uses_next_model_when_first_model_fails(self):
        attempted = []

        def fake_call(provider, prompt, model):
            attempted.append((provider, model))
            if len(attempted) == 1:
                raise RuntimeError("model unavailable")
            return "#Shorts #Facts #History"

        env = {
            "LLM_PROVIDER": "gemini",
            "GEMINI_API_KEY": "fake-key",
            "GEMINI_MODEL": "configured-model",
            "GROQ_API_KEY": "",
            "OPENROUTER_API_KEY": "",
        }
        with patch.dict(os.environ, env, clear=False), \
             patch.object(desc, "model_candidates", return_value=["configured-model", "fallback-model"]), \
             patch.object(desc, "call_provider_model", side_effect=fake_call), \
             patch.object(desc.time, "sleep"):
            result = desc.call_llm("prompt")
        self.assertEqual(result, "#Shorts #Facts #History")
        self.assertEqual(attempted, [("gemini", "configured-model"), ("gemini", "fallback-model")])

    def test_bad_hashtag_output_moves_to_another_model(self):
        responses = iter(["No useful hashtags here", "#Shorts #Facts #Science"])
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "fake-key",
            "GROQ_API_KEY": "", "OPENROUTER_API_KEY": "",
        }, clear=False), \
             patch.object(desc, "model_candidates", return_value=["model-a", "model-b"]), \
             patch.object(desc, "call_provider_model", side_effect=lambda *_: next(responses)), \
             patch.object(desc.time, "sleep"):
            result = desc.call_llm("prompt")
        self.assertEqual(result, "#Shorts #Facts #Science")

    def test_missing_preferred_key_falls_through_to_available_provider(self):
        with patch.dict(os.environ, {
            "LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "",
            "GROQ_API_KEY": "fake-groq", "OPENROUTER_API_KEY": "",
        }, clear=False), \
             patch.object(desc, "model_candidates", side_effect=lambda provider: ["groq-fallback"]), \
             patch.object(desc, "call_provider_model", return_value="#Shorts #Facts #History") as call, \
             patch.object(desc.time, "sleep"):
            result = desc.call_llm("prompt")
        self.assertEqual(result, "#Shorts #Facts #History")
        call.assert_called_once_with("groq", "prompt", "groq-fallback")

    def test_transient_503_retried_then_success(self):
        response = requests.Response()
        response.status_code = 503
        err = requests.exceptions.HTTPError(response=response)
        with patch.object(desc.time, "sleep"):
            calls = {"n": 0}
            def fn():
                calls["n"] += 1
                if calls["n"] == 1:
                    raise err
                return "#Shorts #Facts #Science"
            self.assertEqual(desc.retry_call(fn, label="test", attempts=2), "#Shorts #Facts #Science")
            self.assertEqual(calls["n"], 2)

    def test_openrouter_candidates_are_free_only(self):
        candidates = desc.model_candidates("openrouter")
        self.assertTrue(candidates)
        self.assertTrue(all(model.endswith(":free") for model in candidates))


if __name__ == "__main__":
    unittest.main()
