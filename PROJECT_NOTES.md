# Saidia Project Notes

## MVP release state (2026-09-08)

Release architecture:

`validated intake -> private case-scoped S3 evidence -> PostgreSQL metadata/text
-> 768d pgvector retrieval -> deterministic structured policy assessment ->
human review via idempotent Make/Jira handoff`

Hard boundaries:

- `carrier_policies` structured fields are authoritative for exact carrier,
  country, incident-type, evidence, and deadline logic.
- `policy_chunks` is semantic support for retrieval/explanation only.
- Original customer photographs are not interpreted automatically.
- Saidia prepares and routes cases; a human reviewer makes the final decision.
- Make responses are reduced to allow-listed Jira display fields before use.

Release verification:

- Apply `migrations/000_core_schema.sql` through
  `migrations/007_policy_chunks.sql` in filename order for a fresh database.
- The configured embedding model is
  `sentence-transformers/all-mpnet-base-v2` (`vector(768)`).
- Run `python index_policies.py` after policy changes; indexing refreshes stale
  chunks and removes obsolete trailing chunks transactionally.
- Run `python -m pytest -q` before deployment.

Known external prerequisites are private S3, PostgreSQL, OpenAI, and the mapped
Make/Jira scenario. They must be verified with non-sensitive demo data in the
deployment environment. Do not commit `.env` or Streamlit secrets.

## Deferred: employee Jira operations dashboard

Implement this only after the customer-case Make route is configured and Jira
returns real ticket data to Saidia.

The employee-only area should include a clean, sortable ticket table inspired
by the supplied reference design. It must use persisted `workflow_results` and
customer-case data rather than sample rows.

Suggested table fields:

- Jira issue key
- Case reference
- Complaint type
- Tracking number
- Jira title
- Status
- Priority or routing
- Date reported
- Jira link

Suggested operational measurements:

- total cases received
- cases awaiting review
- cases in progress and completed
- average submission-to-ready time
- average Jira handoff time
- failure and retry count
- case volume by complaint type and month

Implementation boundaries:

- Keep this dashboard separate from the customer-facing confirmation screen.
- Require employee/internal access before showing customer or Jira information.
- Read the dashboard directly from hosted PostgreSQL/Supabase records, including
  `customer_cases`, `workflow_results`, `case_processing_events`, and related
  evidence and update records.
- Treat Google Sheets as optional reporting/export only, not as the dashboard's
  source of truth.
- Start with a read-only table and case details; do not allow Jira editing until
  permissions, audit history, and update behaviour are designed explicitly.
- Jira remains the operational system of record for employee decisions and
  ticket status. Saidia displays the returned result and measured automation
  performance.
