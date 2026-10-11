"""Run five deterministic Saidia checks without calling OpenAI or external services."""

import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from incident_case import build_incident_case


def load_json(path):
    with path.open(encoding="utf-8") as file_stream:
        return json.load(file_stream)


def matching_policy_result(facts, policies):
    matches = [
        policy
        for policy in policies
        if policy["carrier"].casefold() == str(facts.get("carrier", "")).casefold()
        and facts.get("country") in policy["countries"]
        and policy["incident_type"] == facts.get("incident_type")
    ]
    return {"match_count": len(matches), "policies": matches}


def evaluate_case(case, policies):
    result = build_incident_case(
        case["facts"],
        source_file=f"{case['id']}.json",
        source_document_hash=case["id"],
        policy_result=matching_policy_result(case["facts"], policies),
    ).to_dict()
    checks = {
        field: result.get(field) == expected
        for field, expected in case["expected"].items()
    }
    guidance = str(result.get("recommended_next_action", "")).casefold()
    checks["human_decision_boundary"] = not any(
        phrase in guidance
        for phrase in ("approve the claim", "deny the claim", "authorize payment")
    )
    return {
        "id": case["id"],
        "description": case["description"],
        "passed": all(checks.values()),
        "checks": checks,
        "actual": {
            field: result.get(field) for field in case["expected"]
        },
    }


def main():
    policies = load_json(PROJECT_ROOT / "policies" / "carrier_policies.json")
    cases = load_json(PROJECT_ROOT / "evaluations" / "core_cases.json")
    results = [evaluate_case(case, policies) for case in cases]
    summary = {
        "evaluation": "Saidia five-case deterministic core evaluation",
        "total": len(results),
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "scope": (
            "Structured policy matching, deadlines, missing evidence, unsupported "
            "policy handling, and the human-decision boundary."
        ),
        "not_measured": (
            "Live embedding retrieval, live model tool choice, generated-answer "
            "quality, S3, Make, Jira, and production behavior."
        ),
        "results": results,
    }
    output_path = PROJECT_ROOT / "evaluations" / "results" / "core_results.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"{summary['passed']}/{summary['total']} cases passed")
    for result in results:
        print(("PASS" if result["passed"] else "FAIL") + f"  {result['id']}")
    raise SystemExit(0 if summary["failed"] == 0 else 1)


if __name__ == "__main__":
    main()
