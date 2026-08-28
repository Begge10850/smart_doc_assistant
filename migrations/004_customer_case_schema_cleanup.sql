-- Consolidate operational persistence around customer_cases.
-- This migration deliberately refuses to remove a non-empty legacy
-- incident_cases table so historical data cannot be deleted accidentally.

do $$
declare
    legacy_incident_count bigint;
begin
    if to_regclass('public.incident_cases') is not null then
        execute 'select count(*) from public.incident_cases'
            into legacy_incident_count;
        if legacy_incident_count > 0 then
            raise exception
                'incident_cases contains % row(s). Archive or migrate them before applying migration 004.',
                legacy_incident_count;
        end if;
    end if;
end
$$;

alter table workflow_results
    add column if not exists customer_case_id bigint
        references customer_cases(id) on delete cascade;

do $$
declare
    legacy_workflow_count bigint;
begin
    select count(*)
    into legacy_workflow_count
    from workflow_results
    where customer_case_id is null;

    if legacy_workflow_count > 0 then
        raise exception
            'workflow_results contains % legacy incident result row(s). Archive them before applying migration 004.',
            legacy_workflow_count;
    end if;
end
$$;

alter table workflow_results
    alter column customer_case_id set not null;

create unique index if not exists workflow_results_event_id_uidx
    on workflow_results(event_id);

create index if not exists workflow_results_customer_case_id_idx
    on workflow_results(customer_case_id, created_at);

-- Removing case_id also removes its obsolete foreign-key relationship to
-- incident_cases. customer_case_id is now the explicit parent relationship.
alter table workflow_results
    drop column if exists case_id;

drop table if exists incident_cases;

-- Customer photographs are stored unchanged for human review. OCR metadata
-- for scanned textual documents remains in the documents table.
alter table customer_case_evidence
    drop column if exists vision_observations;
