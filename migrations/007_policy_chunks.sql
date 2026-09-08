-- Semantic policy text supports retrieval/explanation only. Structured columns
-- in carrier_policies remain authoritative for matching and calculations.

create extension if not exists vector;

create table if not exists policy_chunks (
    id bigserial primary key,
    policy_id bigint not null references carrier_policies(id) on delete cascade,
    chunk_index integer not null check (chunk_index >= 0),
    chunk_text text not null,
    character_count integer not null check (character_count >= 0),
    embedding vector(768) not null,
    embedding_model text not null,
    created_at timestamptz not null default now(),
    unique (policy_id, chunk_index)
);

create index if not exists policy_chunks_policy_id_idx
    on policy_chunks(policy_id);
create index if not exists policy_chunks_embedding_hnsw_idx
    on policy_chunks using hnsw (embedding vector_cosine_ops);

comment on table policy_chunks is
    'Semantic retrieval support only; carrier_policies structured fields are authoritative.';

