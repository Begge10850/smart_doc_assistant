-- Support duplicate reporting signals and later evidence on an existing case.

alter table customer_cases
    add column if not exists duplicate_submission_count integer not null default 0
        check (duplicate_submission_count >= 0),
    add column if not exists last_duplicate_submission_at timestamptz,
    add column if not exists duplicate_of_customer_case_id bigint
        references customer_cases(id);

create index if not exists customer_cases_duplicate_of_idx
    on customer_cases(duplicate_of_customer_case_id);

do $$
begin
    if exists (
        select 1
        from customer_cases
        where status not in ('closed', 'cancelled')
        group by upper(tracking_number), complaint_type
        having count(*) > 1
    ) then
        raise exception
            'Active duplicate customer cases already exist. Review them before applying migration 005.';
    end if;
end
$$;

create unique index if not exists customer_cases_one_active_problem_uidx
    on customer_cases (upper(tracking_number), complaint_type)
    where status not in ('closed', 'cancelled');

create table if not exists customer_case_updates (
    id bigserial primary key,
    update_reference text not null unique,
    customer_case_id bigint not null references customer_cases(id) on delete cascade,
    update_type text not null check (
        update_type in ('additional_information', 'duplicate_submission_attempt')
    ),
    additional_information text not null default '',
    processing_status text not null default 'pending',
    processing_error text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists customer_case_updates_case_id_idx
    on customer_case_updates(customer_case_id, created_at);

alter table customer_case_evidence
    add column if not exists customer_case_update_id bigint
        references customer_case_updates(id) on delete cascade;

create index if not exists customer_case_evidence_update_id_idx
    on customer_case_evidence(customer_case_update_id);
