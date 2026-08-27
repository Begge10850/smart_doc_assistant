alter table customer_cases
    add column if not exists carrier text,
    add column if not exists country text,
    add column if not exists delivery_date date,
    add column if not exists declared_value text,
    add column if not exists ready_for_review_at timestamptz,
    add column if not exists handoff_accepted_at timestamptz,
    add column if not exists analysis_status text not null default 'pending',
    add column if not exists case_analysis jsonb;

create table if not exists case_processing_events (
    id bigserial primary key,
    customer_case_id bigint not null references customer_cases(id) on delete cascade,
    evidence_id bigint references customer_case_evidence(id) on delete cascade,
    stage text not null,
    duration_ms numeric(12, 2) not null check (duration_ms >= 0),
    status text not null check (status in ('completed', 'failed')),
    error_category text,
    created_at timestamptz not null default now()
);

create index if not exists case_processing_events_case_id_idx
    on case_processing_events(customer_case_id, created_at);

create index if not exists case_processing_events_stage_idx
    on case_processing_events(stage, created_at);
