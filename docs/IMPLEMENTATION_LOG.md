# Short Portfolio Update Log

## Scope

This update deliberately avoided a large production rebuild. It focused on
making existing portfolio claims accurate and testable.

## Changes made

1. Replaced automatic case-context injection with four explicit read-only case
   tools.
2. Required the model to choose a tool in the first answer round.
3. Kept supporting-document vector search separate from structured case facts,
   policy calculations, and Jira status.
4. Added five deterministic evaluation cases and a repeatable evaluation runner.
5. Added five saved case-assistant questions for later live model scoring.
6. Fixed the date-requirement mapping exposed by the first evaluation run.
7. Added a regression test for that date mapping.
8. Changed policy-store unit tests so normal test runs no longer contact the live
   database.
9. Added `pytest` to the declared development requirements because the README
   instructs users to run it.
10. Added beginner-oriented documentation explaining the system, evidence, and
    limitations.
11. Made tool activity and concise source labels visible beside case answers.
12. Added a single architecture diagram showing the form, evidence RAG, structured
    rules, Make/Jira handoff, and final human decision.

## Verification performed

- 54 automated tests passed locally in a temporary Python 3.12 environment.
- Core business-rule and handoff modules measured 81% statement coverage.
- The five-case deterministic evaluation passed after the documented repair.
- No live customer data was used.
- A read-only hosted PostgreSQL check confirmed all expected tables, two
  `vector(768)` columns, eight stored policies, and eight policy chunks.
- No live S3, PostgreSQL, OpenAI, Make, or Jira mutation was performed.

## Deliberately deferred

- employee authentication and role-based access;
- a separate employee dashboard;
- autonomous payout decisions;
- automated interpretation of damage photographs;
- a large RAG benchmark;
- durable external background workers; and
- production security or compliance claims.
