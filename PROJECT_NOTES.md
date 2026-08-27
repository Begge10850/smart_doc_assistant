# Saidia Project Notes

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
- Start with a read-only table and case details; do not allow Jira editing until
  permissions, audit history, and update behaviour are designed explicitly.
- Jira remains the operational system of record for employee decisions and
  ticket status. Saidia displays the returned result and measured automation
  performance.
