import copy
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


fake_openai = types.ModuleType("openai")
fake_openai.OpenAI = object
sys.modules["openai"] = fake_openai

fake_qa_engine = types.ModuleType("qa_engine")
fake_qa_engine._read_openai_settings = lambda: ("test-key", "test-model")
fake_qa_engine.search_index = lambda *_args, **_kwargs: []
sys.modules["qa_engine"] = fake_qa_engine

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "agent_engine_under_test",
    PROJECT_ROOT / "agent_engine.py",
)
agent_engine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(agent_engine)


class ToolSchemaTests(unittest.TestCase):
    def test_all_document_tools_use_strict_closed_schemas(self):
        for tool in agent_engine.DOCUMENT_TOOLS:
            self.assertTrue(tool["strict"])
            self.assertFalse(tool["parameters"]["additionalProperties"])
            self.assertIn("required", tool["parameters"])


class ToolExecutionTests(unittest.TestCase):
    def test_search_document_returns_ranked_excerpts(self):
        captured_search = {}

        def fake_search(query, index, chunks, top_k):
            captured_search.update(
                {
                    "query": query,
                    "index": index,
                    "chunks": chunks,
                    "top_k": top_k,
                }
            )
            return ["First relevant excerpt", "Second relevant excerpt"]

        with patch.object(agent_engine, "search_index", side_effect=fake_search):
            result = agent_engine.execute_document_tool(
                "search_document",
                {"query": "model accuracy", "max_results": 2},
                file_name="matrix.png",
                document_metadata={},
                vector_index="index-object",
                chunks=["chunk one", "chunk two"],
            )

        self.assertEqual(captured_search["top_k"], 2)
        self.assertEqual(result["result_count"], 2)
        self.assertEqual(result["excerpts"][0]["rank"], 1)

    def test_inspect_document_returns_processing_metadata(self):
        result = agent_engine.execute_document_tool(
            "inspect_document",
            {},
            file_name="report.pdf",
            document_metadata={
                "extension": ".pdf",
                "extraction_method": "native_pdf",
                "used_vision": False,
            },
            vector_index="index-object",
            chunks=["one", "two"],
        )

        self.assertEqual(result["file_name"], "report.pdf")
        self.assertEqual(result["extraction_method"], "native_pdf")
        self.assertEqual(result["searchable_chunk_count"], 2)

    def test_search_carrier_policy_returns_matching_policy(self):
        policy_result = {
            "match_count": 1,
            "policies": [{"policy_id": "northstar-parcel-damage-eu-v1"}],
        }
        with patch.object(
            agent_engine,
            "search_carrier_policies",
            return_value=policy_result,
        ) as mocked_search:
            result = agent_engine.execute_document_tool(
                "search_carrier_policy",
                {
                    "carrier": "NorthStar Parcel",
                    "country": "France",
                    "incident_type": "parcel_damage",
                },
                file_name="INC-002.pdf",
                document_metadata={},
                vector_index="index-object",
                chunks=["incident text"],
            )

        mocked_search.assert_called_once_with(
            "NorthStar Parcel",
            "France",
            "parcel_damage",
        )
        self.assertEqual(result["match_count"], 1)


class AgentLoopTests(unittest.TestCase):
    def test_model_tool_call_is_executed_before_final_answer(self):
        tool_call = types.SimpleNamespace(
            type="function_call",
            name="search_document",
            arguments=json.dumps(
                {"query": "confusion matrix performance", "max_results": 3}
            ),
            call_id="call-search-1",
        )
        responses_to_return = [
            types.SimpleNamespace(output=[tool_call], output_text=""),
            types.SimpleNamespace(
                output=[types.SimpleNamespace(type="message")],
                output_text="The model accuracy is 93.5%.",
            ),
        ]
        captured_requests = []

        class FakeResponses:
            def create(self, **kwargs):
                captured_requests.append(copy.deepcopy(kwargs))
                return responses_to_return.pop(0)

        fake_client = types.SimpleNamespace(responses=FakeResponses())

        with patch.object(
            agent_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-model"),
        ), patch.object(
            agent_engine,
            "OpenAI",
            return_value=fake_client,
        ), patch.object(
            agent_engine,
            "search_index",
            return_value=["Aggressive 63 0 25"],
        ):
            result = agent_engine.run_document_agent(
                "What can you tell me about this document?",
                file_name="matrix.png",
                document_metadata={"extraction_method": "openai_vision"},
                vector_index="index-object",
                chunks=["Aggressive 63 0 25"],
            )

        self.assertEqual(result["answer"], "The model accuracy is 93.5%.")
        self.assertEqual(result["tool_trace"][0]["tool"], "search_document")
        self.assertEqual(len(captured_requests), 2)
        second_input = captured_requests[1]["input"]
        tool_outputs = [
            item
            for item in second_input
            if isinstance(item, dict) and item.get("type") == "function_call_output"
        ]
        self.assertEqual(tool_outputs[0]["call_id"], "call-search-1")
        self.assertIn("Aggressive 63 0 25", tool_outputs[0]["output"])


if __name__ == "__main__":
    unittest.main()
