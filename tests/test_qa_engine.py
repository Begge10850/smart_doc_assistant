import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

fake_streamlit = types.ModuleType("streamlit")
fake_streamlit.secrets = {}
fake_streamlit.cache_resource = lambda **_kwargs: lambda function: function
sys.modules["streamlit"] = fake_streamlit

fake_openai = types.ModuleType("openai")
fake_openai.OpenAI = object
sys.modules["openai"] = fake_openai

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "qa_engine_under_test",
    PROJECT_ROOT / "qa_engine.py",
)
qa_engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa_engine)


class AnswerPromptTests(unittest.TestCase):
    def test_responses_request_requires_specific_numerical_analysis(self):
        captured_request = {}

        class FakeResponses:
            def create(self, **kwargs):
                captured_request.update(kwargs)
                return types.SimpleNamespace(output_text="The total is 952.")

        fake_client = types.SimpleNamespace(responses=FakeResponses())

        with patch.object(
            qa_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-model"),
        ), patch.object(
            qa_engine,
            "OpenAI",
            return_value=fake_client,
        ):
            answer = qa_engine.answer_question_with_gpt(
                "What readings can you get?",
                ["Aggressive | 63 | 0 | 25"],
            )

        self.assertEqual(answer, "The total is 952.")
        self.assertEqual(captured_request["model"], "test-model")
        self.assertIn("exact figures", captured_request["instructions"])
        self.assertIn("perform useful calculations", captured_request["instructions"])
        self.assertIn(
            "Aggressive | 63 | 0 | 25",
            captured_request["input"][-1]["content"],
        )


if __name__ == "__main__":
    unittest.main()
