# Saidia Learning Guide

This guide explains the project from the beginning. It intentionally uses plain
language first. Technical names are introduced only after the idea is clear.

## 1. What Saidia does

Saidia helps prepare delivery complaints for a human reviewer.

A customer chooses what went wrong, fills in the relevant form fields, and may
upload supporting evidence. The form is the main source of claim details. The
customer does not need to upload a separate claim document.

Saidia then:

1. checks the form;
2. stores the case and evidence;
3. finds the matching fictional NorthStar policy;
4. calculates dates and missing evidence with ordinary Python rules;
5. uses AI to explain the prepared information;
6. sends the case through Make to Jira; and
7. leaves the final decision to a person.

## 2. What RAG means here

RAG means **retrieval-augmented generation**.

In simple terms, the AI does not receive every stored document and guess. Saidia
first searches supporting text and gives the AI only the passages most closely
related to the question.

For example:

```text
receipt.pdf
-> extract its text
-> divide the text into smaller pieces
-> turn each piece into a list of 768 numbers
-> store the pieces and numbers in PostgreSQL
-> search those numbers when a reviewer asks about the receipt
-> give the best matching text to the AI
```

The lists of numbers are called **embeddings**. Similar text tends to have similar
embeddings. PostgreSQL uses the `pgvector` extension to compare them.

Saidia uses `sentence-transformers/all-mpnet-base-v2`. It produces 768 numbers,
so the database columns use `vector(768)`.

## 3. What is authoritative

The database table `carrier_policies` contains structured policy fields such as:

- complaint type;
- supported country;
- reporting-window length;
- required evidence; and
- reviewer guidance.

Those fields are authoritative for matching and calculations. Vector search is
supporting technology for finding explanatory text. It must not replace exact
policy matching.

The project contains five fictional NorthStar policies:

1. damaged parcel;
2. lost parcel;
3. late delivery;
4. partial loss; and
5. delivered but not received.

They are demonstration rules, not the terms of a real carrier.

## 4. Why Vision is not an agent tool

Vision is used only while preparing evidence that contains text, such as a
scanned receipt. It is not allowed to judge how badly an item is damaged.

```text
digital PDF -> normal text extraction
scanned receipt -> Vision text transcription
damage photograph -> private storage for human inspection
```

Keeping Vision in evidence preparation reduces cost and prevents the case
assistant from making unsupported visual conclusions.

## 5. The four case-assistant tools

The case assistant now has four separate read-only tools.

### `inspect_case`

Reads the customer form facts and the list of supplied files.

### `search_case_evidence`

Uses vector search over text extracted from supporting documents. It does not
interpret photographs.

### `inspect_policy_assessment`

Reads the exact policy match and the results calculated by Python: deadline,
timeliness, required evidence, missing evidence, and review guidance.

### `inspect_workflow_status`

Reads the safe Make/Jira receipt, including the returned Jira issue key.

The AI must select a tool before producing its first case-specific answer. The
tools can only read; they cannot change a case or Jira ticket.

After an answer, the interface shows an **Agent activity** section. This records
which tool was selected and gives a short source label, such as a receipt filename,
policy ID, or Jira issue key. The full customer record is not copied into this
trace.

## 6. Why Saidia does not decide payouts

A policy calculation can say that listed conditions appear satisfied. That is
not the same as approving money.

Saidia does not verify every external fact, inspect every photograph, determine
liability, or apply undocumented exceptions. Its answer must therefore remain:

```text
The structured conditions appear satisfied for review.
A human reviewer makes the final decision.
```

## 7. What was evaluated

The small automated evaluation contains five fictional cases. It checks:

- exact policy selection;
- deadline calculation;
- whether a report is on time;
- missing-evidence detection;
- safe handling of an unsupported carrier; and
- the human-decision boundary.

The first run passed three cases and failed two. Both failures revealed that the
code recognized `delivery date` but not the more specific policy phrase `actual
delivery date`. The mapping was repaired and protected with a regression test.
The recorded final result is five out of five.

This result does **not** prove that every AI answer is correct. Live embedding
retrieval, real model tool selection, generated-answer quality, Supabase, S3,
Make, and Jira still require separate checks in the configured environment.

## 8. How to run the checks

Install the declared packages in an isolated Python environment, then run:

```bash
python3 -m pytest -q
python3 evaluations/run_core_evaluation.py
```

The automated suite currently contains 55 passing tests. The measured core
business-rule and handoff modules have 81% statement coverage. This is not a
whole-application coverage claim. The deterministic evaluation result is saved
in `evaluations/results/core_results.json`, and the scoped coverage data is saved
in `evaluations/results/core_coverage.json`.

## 9. How to explain Saidia in an interview

Start with the problem:

> Saidia turns a structured delivery complaint and its supporting evidence into
> a grounded Jira case for human review.

Then explain the safety choice:

> Structured policy fields and Python calculations control deadlines and missing
> evidence. RAG helps retrieve supporting document text, while the language model
> explains the result and selects read-only tools. A person remains responsible
> for the final claim decision.

Finally, explain the evidence:

> I ran 55 automated tests and a five-case deterministic evaluation. The first
> evaluation exposed a date-label mapping bug, which I fixed and covered with a
> regression test. The final five cases passed. Live model and external-service
> behavior are documented as separate validation work rather than claimed without
> evidence.

## 10. What the small live evaluation found

The five saved case questions were run once against one fictional deployed case.
The assistant selected the expected first tool in 5/5 questions, showed the
expected source trace in 5/5, produced 4/5 strictly grounded answers, and
respected the payout boundary in 1/1 applicable question.

The failed answer mentioned an image even though no evidence file existed. This
is exactly why evaluation matters: unit tests confirmed the tool wiring, while a
real model answer exposed an ambiguous empty-evidence message. The failure was
recorded, the message was corrected, and a regression test was added. Five runs
are useful evidence for a portfolio, but they are not a production accuracy rate.

## 11. Important current limitation

The case Q&A is displayed on the case confirmation experience. The repository
does not yet implement employee login and role-based access. It must not be
described as a secure employee portal until authentication is added. For this
portfolio version, the Q&A demonstrates the read-only case-assistant design using
only the current submitter's in-memory case context.
