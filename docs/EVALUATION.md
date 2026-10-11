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

The live language model was not scored during this local run. Therefore the
project must not claim a measured hallucination rate, retrieval Hit@K score, or
live tool-selection accuracy. A later live evaluation should run the five saved
questions against a non-sensitive demonstration case and record:

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
