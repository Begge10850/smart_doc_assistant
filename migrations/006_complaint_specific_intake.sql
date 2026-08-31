-- Preserve complaint-specific web intake facts without changing evidence storage
-- or the existing Make/Jira workflow contract.

alter table customer_cases
    add column if not exists complaint_details jsonb not null default '{}'::jsonb,
    add column if not exists evidence_types jsonb not null default '[]'::jsonb,
    add column if not exists intake_source text not null default 'email'
        check (intake_source in ('web_form', 'email')),
    add column if not exists intake_completeness text not null default 'incomplete'
        check (intake_completeness in ('complete', 'incomplete'));

create index if not exists customer_cases_intake_completeness_idx
    on customer_cases (intake_source, intake_completeness);

comment on column customer_cases.complaint_details is
    'Complaint-specific facts, including late-delivery dates, delay duration, exclusions, and non-binding fee-review guidance.';

comment on column customer_cases.intake_completeness is
    'Web submissions are validated as complete; email ingestion may remain incomplete for downstream review.';

update carrier_policies
set additional_timing_rules = '[
        {"service_type":"NorthStar Standard","expected_transit_days":5},
        {"service_type":"NorthStar Express","expected_transit_days":3}
    ]'::jsonb,
    handling_guidance = '[
        "Compare the service commitment or promised delivery date with actual delivery.",
        "One day late may be reviewed for partial delivery-fee reimbursement.",
        "Two or more days late may be reviewed for full delivery-fee reimbursement.",
        "Check severe weather, customs delay, customer-requested changes, and incomplete address exclusions.",
        "Any reimbursement recommendation requires a final human decision."
    ]'::jsonb,
    policy_text = 'NorthStar Standard has a fictional five-day expected transit duration and NorthStar Express has a fictional three-day duration. Compare the applicable commitment or documented promised delivery date with the actual delivery date. One day late may be reviewed for partial delivery-fee reimbursement; two or more days late may be reviewed for full delivery-fee reimbursement. Severe weather, customs delay, customer-requested delivery changes, and incomplete addresses are possible exclusions requiring investigation. This is guidance only: a human reviewer retains the final reimbursement decision.'
where policy_id = 'northstar-late-delivery-eu-v1';
