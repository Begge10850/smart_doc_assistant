from database import find_carrier_policies


class PolicyStoreError(RuntimeError):
    """Raised when the PostgreSQL policy store cannot be read."""


def search_carrier_policies(carrier, country, incident_type):
    """Return policies matching explicit incident facts supplied by the agent."""

    try:
        rows = find_carrier_policies(
            carrier=carrier,
            country=country,
            incident_type=incident_type,
        )
    except Exception as exc:
        raise PolicyStoreError(
            "The PostgreSQL carrier-policy store could not be read."
        ) from exc

    policies = []

    for row in rows:
        (
            db_id,
            policy_id,
            title,
            canonical_carrier,
            countries,
            matched_incident_type,
            effective_date,
            deadline_days,
            deadline_basis,
            additional_timing_rules,
            required_evidence,
            handling_guidance,
            policy_text,
            fictional_evaluation_policy,
        ) = row

        policies.append(
            {
                "db_id": db_id,
                "policy_id": policy_id,
                "title": title,
                "carrier": canonical_carrier,
                "countries": countries,
                "incident_type": matched_incident_type,
                "effective_date": (
                    effective_date.isoformat()
                    if effective_date
                    else None
                ),
                "reporting_window_days": deadline_days,
                "deadline_basis": deadline_basis,
                "additional_timing_rules": additional_timing_rules,
                "required_evidence": required_evidence,
                "handling_guidance": handling_guidance,
                "policy_text": policy_text,
                "fictional_evaluation_policy": fictional_evaluation_policy,
            }
        )

    return {
        "carrier": carrier,
        "country": country,
        "incident_type": incident_type,
        "match_count": len(policies),
        "policies": policies,
        "notice": (
            "These are fictional evaluation policies stored with the Saidia project, "
            "not verified real-world carrier terms."
        ),
    }