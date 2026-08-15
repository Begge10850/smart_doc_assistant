import json
from pathlib import Path


POLICY_FILE = Path(__file__).resolve().parent / "policies" / "carrier_policies.json"


class PolicyStoreError(RuntimeError):
    """Raised when the local fictional policy store cannot be read."""


def _normalize(value):
    return " ".join(str(value or "").lower().replace("_", " ").split())


def load_carrier_policies():
    """Load the small, version-controlled fictional carrier-policy catalogue."""
    try:
        with POLICY_FILE.open("r", encoding="utf-8") as policy_stream:
            policies = json.load(policy_stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise PolicyStoreError("The local carrier-policy store could not be read.") from exc

    if not isinstance(policies, list):
        raise PolicyStoreError("The local carrier-policy store has an invalid format.")

    return policies


def search_carrier_policies(carrier, country, incident_type):
    """Return policies matching explicit incident facts supplied by the agent."""
    normalized_carrier = _normalize(carrier)
    normalized_country = _normalize(country)
    normalized_incident_type = _normalize(incident_type)
    matches = []

    for policy in load_carrier_policies():
        policy_countries = {
            _normalize(policy_country)
            for policy_country in policy.get("countries", [])
        }
        if (
            _normalize(policy.get("carrier")) == normalized_carrier
            and normalized_country in policy_countries
            and _normalize(policy.get("incident_type"))
            == normalized_incident_type
        ):
            matches.append(policy)

    return {
        "carrier": carrier,
        "country": country,
        "incident_type": incident_type,
        "match_count": len(matches),
        "policies": matches,
        "notice": (
            "These are fictional evaluation policies stored with the Saidia project, "
            "not verified real-world carrier terms."
        ),
    }
