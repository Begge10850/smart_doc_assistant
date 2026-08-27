-- Fictional evaluation policies for the single-carrier NorthStar Parcel MVP.
-- Safe to run more than once: policy_id is used to update existing policies.

do $$
begin
    if not exists (
        select 1 from carriers
        where lower(name) = lower('NorthStar Parcel')
    ) then
        raise exception
            'NorthStar Parcel is missing from carriers. Add the configured carrier before applying this migration.';
    end if;
end
$$;

insert into carrier_policies (
    policy_id, title, carrier_id, countries, incident_type, effective_date,
    deadline_days, deadline_basis, additional_timing_rules,
    required_evidence, handling_guidance, policy_text,
    fictional_evaluation_policy
)
select
    source.policy_id, source.title, carrier.id, source.countries,
    source.incident_type, source.effective_date, source.deadline_days,
    source.deadline_basis, source.additional_timing_rules,
    source.required_evidence, source.handling_guidance, source.policy_text, true
from (
    values
        (
            'northstar-parcel-damage-eu-v1',
            'NorthStar Parcel EU Damage Claim Policy',
            '["France", "Germany"]'::jsonb,
            'parcel_damage', date '2026-01-01', 7,
            'calendar days after delivery', '[]'::jsonb,
            '["tracking number", "delivery date", "damage report date", "commercial invoice or other proof of value", "photograph of the damaged item", "photograph of the external packaging"]'::jsonb,
            '["Request any missing required evidence before the claim is submitted.", "Do not approve, deny, or assign carrier liability from the incident report alone.", "Require human review before submitting a claim to an external system."]'::jsonb,
            'A parcel-damage complaint must be reported within 7 calendar days after delivery. Required evidence includes the tracking number, delivery date, damage report date, proof of value, a photograph of the damaged item, and a photograph of the external packaging. Missing evidence must be requested. A human reviewer retains the final decision.'
        ),
        (
            'northstar-lost-parcel-eu-v1',
            'NorthStar Parcel EU Lost Parcel Claim Policy',
            '["France", "Germany"]'::jsonb,
            'lost_parcel', date '2026-01-01', 30,
            'calendar days after the expected delivery date', '[]'::jsonb,
            '["tracking number", "expected delivery date", "commercial invoice or other proof of value", "description of the parcel contents"]'::jsonb,
            '["Confirm that carrier tracking has not recorded delivery before treating the parcel as lost.", "Request missing proof of value or contents before external submission.", "Require human review before submitting, approving, or denying a claim."]'::jsonb,
            'A lost-parcel complaint must be reported within 30 calendar days after the expected delivery date. Confirm that tracking has not recorded delivery. Required evidence includes the tracking number, expected delivery date, proof of value, and a description of the contents. A human reviewer retains the final decision.'
        ),
        (
            'northstar-late-delivery-eu-v1',
            'NorthStar Parcel EU Late Delivery Claim Policy',
            '["France", "Germany"]'::jsonb,
            'late_delivery', date '2026-01-01', 14,
            'calendar days after actual delivery', '[]'::jsonb,
            '["tracking number", "actual delivery date", "evidence of the promised delivery date"]'::jsonb,
            '["Compare the promised and actual delivery dates using supplied records.", "Do not infer financial loss from delay without supporting evidence.", "Require human review before any remedy or external submission."]'::jsonb,
            'A late-delivery complaint must be reported within 14 calendar days after actual delivery. Required evidence includes the tracking number, actual delivery date, and evidence of the promised delivery date. Financial loss must not be inferred without evidence. A human reviewer retains the final decision.'
        ),
        (
            'northstar-partial-loss-eu-v1',
            'NorthStar Parcel EU Partial Loss Claim Policy',
            '["France", "Germany"]'::jsonb,
            'partial_loss', date '2026-01-01', 7,
            'calendar days after delivery', '[]'::jsonb,
            '["tracking number", "delivery date", "commercial invoice or other proof of value", "packing list or description of missing contents", "photograph of the external packaging"]'::jsonb,
            '["Compare the claimed missing contents with the invoice or packing list.", "Preserve uploaded photographs unchanged for human inspection.", "Require human review before submitting, approving, or denying a claim."]'::jsonb,
            'A partial-loss complaint must be reported within 7 calendar days after delivery. Required evidence includes the tracking number, delivery date, proof of value, a packing list or description of missing contents, and a photograph of the external packaging. Uploaded photographs are preserved for human inspection. A human reviewer retains the final decision.'
        ),
        (
            'northstar-non-delivery-eu-v1',
            'NorthStar Parcel EU Delivered-but-Not-Received Claim Policy',
            '["France", "Germany"]'::jsonb,
            'non_delivery', date '2026-01-01', 7,
            'calendar days after the carrier-recorded delivery date', '[]'::jsonb,
            '["tracking number", "carrier-recorded delivery date", "proof of carrier delivery status", "recipient statement of non-receipt"]'::jsonb,
            '["Check carrier delivery status and any available proof-of-delivery record.", "Do not infer theft, fraud, or recipient fault from tracking status alone.", "Require human review before submitting, approving, or denying a claim."]'::jsonb,
            'A delivered-but-not-received complaint must be reported within 7 calendar days after the carrier-recorded delivery date. Required evidence includes the tracking number, recorded delivery date, proof of delivery status, and a recipient statement of non-receipt. Tracking alone must not be used to infer theft, fraud, or recipient fault. A human reviewer retains the final decision.'
        )
) as source (
    policy_id, title, countries, incident_type, effective_date,
    deadline_days, deadline_basis, additional_timing_rules,
    required_evidence, handling_guidance, policy_text
)
cross join lateral (
    select id from carriers
    where lower(name) = lower('NorthStar Parcel')
    limit 1
) as carrier
on conflict (policy_id) do update set
    title = excluded.title,
    carrier_id = excluded.carrier_id,
    countries = excluded.countries,
    incident_type = excluded.incident_type,
    effective_date = excluded.effective_date,
    deadline_days = excluded.deadline_days,
    deadline_basis = excluded.deadline_basis,
    additional_timing_rules = excluded.additional_timing_rules,
    required_evidence = excluded.required_evidence,
    handling_guidance = excluded.handling_guidance,
    policy_text = excluded.policy_text,
    fictional_evaluation_policy = excluded.fictional_evaluation_policy;
