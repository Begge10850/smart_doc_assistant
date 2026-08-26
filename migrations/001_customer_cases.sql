create table if not exists customer_cases (
    id bigserial primary key,
    case_reference text not null unique,
    reported_at timestamptz not null,
    status text not null,
    claimant_role text not null check (claimant_role in ('sender', 'recipient')),
    tracking_number text not null,
    complaint_type text not null,
    customer_email text not null,
    additional_information text not null default '',
    downstream_processing_status text not null,
    processing_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists customer_case_evidence (
    id bigserial primary key,
    customer_case_id bigint not null references customer_cases(id) on delete cascade,
    original_file_name text not null,
    content_type text,
    size_bytes bigint not null check (size_bytes >= 0),
    evidence_kind text check (evidence_kind in ('image', 'document')),
    s3_object_key text unique,
    upload_status text not null default 'pending',
    processing_status text not null default 'pending',
    document_id bigint references documents(id),
    vision_observations jsonb,
    processing_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists customer_case_evidence_case_id_idx
    on customer_case_evidence(customer_case_id);

create index if not exists customer_cases_tracking_number_idx
    on customer_cases(tracking_number);
