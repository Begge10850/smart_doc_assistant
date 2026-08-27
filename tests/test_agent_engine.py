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
fake_qa_engine.get_embedding_model = lambda: None
sys.modules["qa_engine"] = fake_qa_engine

fake_database = types.ModuleType("database")
fake_database.search_document_chunks = lambda **_kwargs: []
fake_database.find_carrier_policies = lambda **_kwargs: []
sys.modules["database"] = fake_database

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

        class FakeEmbeddingModel:
            def encode(self, query):
                captured_search["query"] = query
                return [0.1, 0.2]

        def fake_search(**kwargs):
            captured_search.update(kwargs)
            return [
                (0, "First relevant excerpt", 0.9),
                (1, "Second relevant excerpt", 0.8),
            ]

        with patch.object(
            agent_engine,
            "get_embedding_model",
            return_value=FakeEmbeddingModel(),
        ), patch.object(
            agent_engine,
            "search_document_chunks",
            side_effect=fake_search,
        ):
            result = agent_engine.execute_document_tool(
                "search_document",
                {"query": "model accuracy", "max_results": 2},
                file_name="matrix.png",
                document_metadata={},
                document_id=42,
                chunks=["chunk one", "chunk two"],
            )

        self.assertEqual(captured_search["query"], "model accuracy")
        self.assertEqual(captured_search["document_id"], 42)
        self.assertEqual(captured_search["limit"], 2)
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
            document_id=42,
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
                document_id=42,
                chunks=["incident text"],
            )

        mocked_search.assert_called_once_with(
            "NorthStar Parcel",
            "France",
            "parcel_damage",
        )
        self.assertEqual(result["match_count"], 1)


class AgentLoopTests(unittest.TestCase):
    def test_structured_incident_facts_use_strict_json_schema(self):
        facts = {
            "is_logistics_incident": True,
            "relevance_reason": "A concrete damaged-parcel incident is described.",
            "incident_id": "INC-002",
            "tracking_number": "NSP-FR-20260809-7821",
            "carrier": "NorthStar Parcel",
            "country": "France",
            "incident_type": "parcel_damage",
            "delivery_date": "2026-08-09",
            "reported_date": "2026-08-14",
            "declared_value": "EUR 89.90",
            "evidence_supplied": ["Commercial invoice"],
            "factual_summary": "Two glass jars were broken on arrival.",
            "unresolved_fields": [],
        }
        captured_request = {}

        class FakeResponses:
            def create(self, **kwargs):
                captured_request.update(copy.deepcopy(kwargs))
                return types.SimpleNamespace(output_text=json.dumps(facts))

        fake_client = types.SimpleNamespace(responses=FakeResponses())
        with patch.object(
            agent_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-model"),
        ), patch.object(
            agent_engine,
            "OpenAI",
            return_value=fake_client,
        ):
            result = agent_engine.extract_incident_facts("Incident report text")

        self.assertEqual(result["incident_id"], "INC-002")
        response_format = captured_request["text"]["format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["strict"])
        self.assertFalse(response_format["schema"]["additionalProperties"])

    def test_explicit_evidence_available_is_merged_when_model_omits_it(self):
        facts = {
            "is_logistics_incident": True,
            "relevance_reason": "A concrete damaged-parcel incident is described.",
            "incident_id": "INC-013",
            "tracking_number": "NSP-FR-013",
            "carrier": "Northstar Parcel Co.",
            "country": "France",
            "incident_type": "parcel_damage",
            "delivery_date": "2026-08-09",
            "reported_date": "2026-08-10",
            "declared_value": "EUR 120.00",
            "evidence_supplied": [],
            "factual_summary": "A damaged parcel was reported.",
            "unresolved_fields": [],
        }

        class FakeResponses:
            def create(self, **_kwargs):
                return types.SimpleNamespace(output_text=json.dumps(facts))

        fake_client = types.SimpleNamespace(responses=FakeResponses())
        with patch.object(
            agent_engine,
            "_read_openai_settings",
            return_value=("test-key", "test-model"),
        ), patch.object(agent_engine, "OpenAI", return_value=fake_client):
            result = agent_engine.extract_incident_facts(
                "Evidence available: Commercial invoice, damage photographs"
            )

        self.assertEqual(
            result["evidence_supplied"],
            ["Commercial invoice", "damage photographs"],
        )

    def test_non_incident_document_is_rejected_before_policy_lookup(self):
        facts = {
            "is_logistics_incident": False,
            "relevance_reason": "This is a lunch menu, not an incident report.",
            "incident_id": None,
            "tracking_number": None,
            "carrier": None,
            "country": None,
            "incident_type": None,
            "delivery_date": None,
            "reported_date": None,
            "declared_value": None,
            "evidence_supplied": [],
            "factual_summary": "A lunch menu.",
            "unresolved_fields": [],
        }
        with patch.object(
            agent_engine,
            "extract_incident_facts",
            return_value=facts,
        ), patch.object(agent_engine, "search_carrier_policies") as policy_search:
            with self.assertRaises(agent_engine.NonIncidentDocumentError):
                agent_engine.prepare_incident_case(
                    "Vegetarian options available.",
                    source_file="menu.pdf",
                    source_document_hash="menu-hash",
                )

        policy_search.assert_not_called()

    def test_incident_label_without_core_logistics_facts_is_rejected(self):
        facts = {
            "is_logistics_incident": True,
            "relevance_reason": "The text vaguely mentions an incident.",
            "incident_id": None,
            "tracking_number": None,
            "carrier": None,
            "country": None,
            "incident_type": None,
            "delivery_date": None,
            "reported_date": None,
            "declared_value": None,
            "evidence_supplied": [],
            "factual_summary": "No concrete logistics facts are present.",
            "unresolved_fields": [],
        }
        with patch.object(
            agent_engine,
            "extract_incident_facts",
            return_value=facts,
        ):
            with self.assertRaises(agent_engine.NonIncidentDocumentError):
                agent_engine.prepare_incident_case(
                    "A vague incident reference.",
                    source_file="generic.pdf",
                    source_document_hash="generic-hash",
                )

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
            "get_embedding_model",
            return_value=types.SimpleNamespace(encode=lambda _query: [0.1, 0.2]),
        ), patch.object(
            agent_engine,
            "search_document_chunks",
            return_value=[(0, "Aggressive 63 0 25", 0.9)],
        ):
            result = agent_engine.run_document_agent(
                "What can you tell me about this document?",
                file_name="matrix.png",
                document_metadata={"extraction_method": "openai_vision"},
                document_id=42,
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
