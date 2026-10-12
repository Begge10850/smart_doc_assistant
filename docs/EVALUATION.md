# Evaluation Record

## Why the project has two kinds of checks

Automated tests check individual code behaviours. The five-case evaluation
checks whether several deterministic behaviours work together on representative
fictional claims.

## Recorded results

| Check | Result |
| --- | ---: |
| Automated Python tests | 54 passed |
| Five-case deterministic evaluation, first run | 3 passed, 2 failed |
| Five-case deterministic evaluation, after repair | 5 passed, 0 failed |
| Core business-rule and handoff statement coverage | 81% |

The 81% figure is deliberately scoped to `customer_intake`, `incident_case`,
`policy_store`, `embedding_config`, and `case_handoff`. It is not whole-project
coverage. The machine-readable report is in
`evaluations/results/core_coverage.json`.

The first-run failures were retained in this explanation because they show that
the evaluation found a real issue. The policy phrase `actual delivery date` was
not being matched to the normalized `delivery_date` field. The same normalized
field is now accepted for delivery, actual-delivery, expected-delivery, and
carrier-recorded-delivery requirements.

## The five deterministic scenarios

1. Complete and on-time damaged-parcel report.
2. On-time late-delivery report.
3. Damage report missing its packaging photograph.
4. Unsupported carrier that must not receive a NorthStar policy.
5. Complete late-delivery report submitted after its calculated deadline.

The inputs are in `evaluations/core_cases.json`. The machine-readable result is
in `evaluations/results/core_results.json`.

## Case-assistant evaluation design

`evaluations/case_assistant_cases.json` defines five questions covering all four
read-only tools and the no-payout-decision boundary. Unit tests verify the tool
schemas, tool execution, forced first-round tool use, and evidence-search wiring.

The deterministic local run did not score the language model. A separate small
live evaluation was therefore run against one non-sensitive fictional case.

## Small live case-assistant evaluation

| Live measure | Result |
| --- | ---: |
| Correct first tool | 5/5 (100%) |
| Correct displayed source trace | 5/5 (100%) |
| Strictly grounded answers | 4/5 (80%) |
| Strict task success | 4/5 (80%) |
| Unsupported-claim rate | 1/5 (20%) |
| Payout-boundary compliance | 1/1 (100%) |
| Median observed response latency | 9.672 seconds |

The failed answer correctly said a receipt amount was unknown and selected
`search_case_evidence`, but referred to an image even though no file had been
uploaded. This was scored as unsupported. The empty-evidence tool response was
corrected after the run so it now distinguishes “no file exists” from “a file
exists but has no searchable text,” and a regression test protects that behaviour.

The same fictional submission created Supabase case
`CASE-20261012-BBD132345D`, received HTTP 200 from Make, and returned Jira issue
`KAN-57`. No file was uploaded, so S3 evidence transfer and successful document
retrieval were not tested. Token usage and cost were not exposed by the UI.

The observations are saved in
`evaluations/results/live_case_assistant_results.json`. Since every question was
run only once against one case, these percentages are descriptive portfolio
results, not production accuracy or reliability estimates.

The live rubric recorded:

- selected first tool;
- whether the expected source was retrieved;
- whether every factual statement is supported;
- whether missing information is acknowledged; and
- whether the human-decision boundary is preserved.

## Read-only hosted database check

The configured PostgreSQL database was inspected without changing data. All nine
expected tables were present. Both `document_chunks.embedding` and
`policy_chunks.embedding` were confirmed as `vector(768)`. Eight policy rows and
eight policy chunks exist in the database.

Five policy rows are the configured NorthStar France/Germany set used by the
customer form. Three older/demo records also remain. One older NorthStar loss
record uses the country label `EU destinations`; the exact PostgreSQL country
match means it does not also match the configured `France` or `Germany` cases.
It should still be reviewed before any future broadening of country matching.

## External systems not revalidated here

- applying migrations to a new empty hosted database;
- S3 upload and temporary download links;
- the live OpenAI model;
- the Make scenario; and
- Jira creation and attachment transfer.

These require a controlled smoke test with fictional data. Passing local tests
and a read-only database inspection do not prove that the whole deployed flow is
correct.
