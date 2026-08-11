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

fake_numpy = types.ModuleType("numpy")
fake_numpy.array = lambda value: value
sys.modules["numpy"] = fake_numpy

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


class FakeEmbeddingModel:
    def encode(self, _texts):
        return [[0.1, 0.2]]


class SearchIndexTests(unittest.TestCase):
    def test_one_available_chunk_is_returned_once(self):
        class FakeIndex:
            ntotal = 1

            def search(self, _embedding, result_count):
                self.result_count = result_count
                return [[0.0]], [[0]]

        fake_index = FakeIndex()
        with patch.object(
            qa_engine,
            "get_embedding_model",
            return_value=FakeEmbeddingModel(),
        ):
            result = qa_engine.search_index(
                "What readings can you get?",
                fake_index,
                ["Only document chunk"],
                top_k=3,
            )

        self.assertEqual(fake_index.result_count, 1)
        self.assertEqual(result, ["Only document chunk"])

    def test_invalid_faiss_indexes_are_ignored(self):
        class FakeIndex:
            ntotal = 2

            def search(self, _embedding, _result_count):
                return [[0.0, 1.0]], [[0, -1]]

        with patch.object(
            qa_engine,
            "get_embedding_model",
            return_value=FakeEmbeddingModel(),
        ):
            result = qa_engine.search_index(
                "Question",
                FakeIndex(),
                ["First chunk", "Second chunk"],
                top_k=2,
            )

        self.assertEqual(result, ["First chunk"])


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
