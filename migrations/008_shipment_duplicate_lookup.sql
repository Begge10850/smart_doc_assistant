-- Enforce one active case per normalized carrier shipment.
-- Existing cases are intentionally not modified or merged by this migration.

do $$
begin
    if exists (
        select 1
        from customer_cases
        where status not in ('closed', 'cancelled')
        group by lower(trim(carrier)), lower(trim(tracking_number))
        having count(*) > 1
    ) then
        raise exception
            'Active duplicate shipments already exist. Review them before applying migration 008.';
    end if;
end
$$;

drop index if exists customer_cases_one_active_problem_uidx;
drop index if exists customer_cases_active_shipment_lookup_idx;

create unique index if not exists customer_cases_one_active_shipment_uidx
    on customer_cases (
        lower(trim(carrier)),
        lower(trim(tracking_number))
    )
    where status not in ('closed', 'cancelled');
