-- Speed up active-case checks by carrier and normalized tracking number.
-- Existing cases are intentionally not modified or merged by this migration.

create index if not exists customer_cases_active_shipment_lookup_idx
    on customer_cases (
        upper(trim(carrier)),
        upper(trim(tracking_number)),
        reported_at
    )
    where status not in ('closed', 'cancelled');
