-- Base PostgreSQL schema required by the numbered Saidia MVP migrations.
-- Safe to apply to an existing installation: objects are only created when absent.

create extension if not exists vector;

create table if not exists documents (
    id bigserial primary key,
    document_hash text not null unique,
    original_file_name text not null,
    s3_object_key text not null,
    content_type text,
    size_bytes bigint check (size_bytes is null or size_bytes >= 0),
    document_kind text,
    extraction_method text,
    used_vision boolean not null default false,
    page_count integer check (page_count is null or page_count >= 0),
    extracted_word_count integer check (extracted_word_count is null or extracted_word_count >= 0),
    extracted_character_count integer check (extracted_character_count is null or extracted_character_count >= 0),
    extracted_text text,
    processing_status text not null default 'uploaded',
    processing_error text,
    processed_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists document_chunks (
    id bigserial primary key,
    document_id bigint not null references documents(id) on delete cascade,
    chunk_index integer not null check (chunk_index >= 0),
    chunk_text text not null,
    character_count integer not null check (character_count >= 0),
    embedding vector(768) not null,
    embedding_model text not null,
    created_at timestamptz not null default now(),
    unique (document_id, chunk_index)
);

create index if not exists document_chunks_document_id_idx
    on document_chunks(document_id);
create index if not exists document_chunks_embedding_hnsw_idx
    on document_chunks using hnsw (embedding vector_cosine_ops);

create table if not exists carriers (
    id bigserial primary key,
    name text not null,
    active boolean not null default true,
    created_at timestamptz not null default now()
);
create unique index if not exists carriers_name_lower_uidx
    on carriers(lower(name));

insert into carriers (name, active)
values ('NorthStar Parcel', true)
on conflict do nothing;

create table if not exists carrier_policies (
    id bigserial primary key,
    policy_id text not null unique,
    title text not null,
    carrier_id bigint not null references carriers(id),
    countries jsonb not null default '[]'::jsonb,
    incident_type text not null,
    effective_date date,
    deadline_days integer check (deadline_days is null or deadline_days >= 0),
    deadline_basis text,
    additional_timing_rules jsonb not null default '[]'::jsonb,
    required_evidence jsonb not null default '[]'::jsonb,
    handling_guidance jsonb not null default '[]'::jsonb,
    policy_text text not null,
    fictional_evaluation_policy boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists carrier_policies_match_idx
    on carrier_policies(carrier_id, incident_type);
create index if not exists carrier_policies_countries_gin_idx
    on carrier_policies using gin(countries);

-- Legacy parents are created only so migrations 001-004 can upgrade a fresh DB.
create table if not exists incident_cases (
    id bigserial primary key,
    case_id text not null unique,
    created_at timestamptz not null default now()
);

create table if not exists workflow_results (
    id bigserial primary key,
    case_id bigint references incident_cases(id) on delete cascade,
    event_id text not null,
    event_type text not null,
    event_version text not null,
    handoff_status text not null,
    jira_issue_key text,
    jira_title text,
    jira_routing text,
    jira_status text,
    jira_url text,
    error_message text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

