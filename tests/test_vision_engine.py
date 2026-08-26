import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


# Keep these unit tests independent of the deployed Streamlit environment.
fake_streamlit = types.ModuleType("streamlit")
fake_streamlit.secrets = {}
sys.modules["streamlit"] = fake_streamlit

fake_openai = types.ModuleType("openai")


class UnexpectedOpenAIClient:
    def __init__(self, *args, **kwargs):
        raise AssertionError("The OpenAI client should not be created in this test.")


fake_openai.OpenAI = UnexpectedOpenAIClient
sys.modules["openai"] = fake_openai

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "vision_engine_under_test",
    PROJECT_ROOT / "vision_engine.py",
)
vision_engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(vision_engine)


class VisionExtractionTests(unittest.TestCase):
    def test_empty_image_data_is_rejected_before_openai_is_called(self):
        with self.assertRaises(vision_engine.VisionProcessingError):
            vision_engine.extract_text_from_image_bytes(
                b"",
                "image/png",
            )

    def test_image_uses_the_configured_model_automatically(self):
        captured_request = {}

        class FakeResponses:
            def create(self, **kwargs):
                captured_request.update(kwargs)
                return types.SimpleNamespace(output_text="Transcribed document text")

        fake_client = types.SimpleNamespace(responses=FakeResponses())

        with patch.object(
            vision_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-vision-model"),
        ), patch.object(
            vision_engine,
            "OpenAI",
            return_value=fake_client,
        ):
            result = vision_engine.extract_text_from_image_bytes(
                b"not-a-real-image",
                "image/png",
            )

        self.assertEqual(result, "Transcribed document text")
        self.assertEqual(captured_request["model"], "test-vision-model")
        image_part = captured_request["input"][0]["content"][1]
        self.assertTrue(image_part["image_url"].startswith("data:image/png;base64,"))

    def test_evidence_inspection_keeps_human_review_as_the_decision(self):
        class FakeResponses:
            def create(self, **_kwargs):
                return types.SimpleNamespace(output_text=json.dumps({
                    "observations": ["A corner of the box appears crushed."],
                    "readable_text": ["TRACK-123"],
                    "limitations": ["The contents are not visible."],
                    "decision": "approve",
                }))

        with patch.object(
            vision_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-vision-model"),
        ), patch.object(
            vision_engine,
            "OpenAI",
            return_value=types.SimpleNamespace(responses=FakeResponses()),
        ):
            result = vision_engine.inspect_evidence_image_bytes(
                b"image-data", "image/jpeg", file_name="damage.jpg"
            )

        self.assertEqual(result["decision"], "human_review_required")
        self.assertEqual(
            result["observations"],
            ["A corner of the box appears crushed."],
        )


if __name__ == "__main__":
    unittest.main()
