# Saidia Logistics Claims

Saidia is an internal claims assistant for NorthStar Parcel. It turns a customer delivery report into a structured case, applies versioned policy rules, explains the assessment in plain language, and creates a Jira ticket for human review.

[Open the live application](https://smartdocassistant-ibk4wvbdysw7fqkfpkxb7q.streamlit.app/)

NorthStar Parcel and its policies are fictional. Saidia supports operational review only. It does not approve or deny claims, assign liability, or authorize refunds.

## What the system does

- Presents conditional forms for damaged, lost, late, partial-loss, delivered-but-not-received, and other delivery problems.
- Accepts incomplete submissions and identifies the evidence still required by the matching policy.
- Stores original uploads privately in Amazon S3 and keeps evidence metadata in PostgreSQL.
- Preserves images for human inspection. The application does not use computer vision or infer their contents.
- Extracts and indexes supported text documents when they are supplied, making their text searchable during case Q&A.
- Calculates filing deadlines and evidence completeness with deterministic Python and PostgreSQL logic.
- Retrieves relevant policy passages with semantic search and uses an LLM to explain verified results in plain language.
- Sends new cases and temporary evidence links to Make, which creates a Jira issue and attaches the evidence.
- Returns the Jira issue details to Streamlit and provides grounded case Q&A for employees.
- Detects an existing active case before another Jira issue is created and returns its case reference to the customer.

## How a claim moves through Saidia

1. A customer selects a delivery problem and submits the relevant case details and any available evidence.
2. Saidia creates the case in PostgreSQL and stores uploaded files in a private S3 bucket.
3. Structured policy logic matches the NorthStar policy by problem type and country, calculates the filing deadline, and compares required evidence with the customer's declaration.
4. Policy embeddings stored with pgvector retrieve relevant passages for the explanation and later Q&A.
5. Saidia sends the prepared case to a Make webhook. Make checks the shared normalized tracking key before entering the new-case route.
6. For a new claim, Make creates the Jira issue, transfers the evidence attachments, stores the Jira key, and returns the result to Streamlit.
7. For a duplicate, Make stops before Jira creation and returns the existing case reference.
8. An employee reviews the case and makes every final decision.

## Architecture

| Layer | Technology | Responsibility |
| --- | --- | --- |
| Customer and reviewer interface | Python, Streamlit | Conditional intake, progress states, results, duplicate response, and case Q&A |
| Relational data and constraints | PostgreSQL on Supabase | Cases, evidence metadata, policies, workflow results, transactions, and active-shipment uniqueness |
| Semantic retrieval | pgvector, `all-mpnet-base-v2` | 768-dimensional embeddings and similarity search over policy text and supported text evidence |
| Grounded language layer | OpenAI API | Plain-language policy explanations and concise case answers based on supplied context |
| Evidence storage | Private Amazon S3 | Original file storage and short-lived download links for the Jira handoff |
| Workflow automation | Make | Duplicate routing, Jira creation, attachment transfer, result recording, and webhook responses |
| Review queue | Jira | Operational ticket, case details, policy assessment, evidence status, attachments, and human ownership |

PostgreSQL was chosen because the workflow needs relational integrity, transactions, and database constraints, not only flexible object storage. pgvector keeps semantic retrieval beside the case and policy data instead of adding a separate vector database. The sentence-transformer model provides reproducible local embeddings suited to a small, focused policy library. The LLM is used only where language is useful. Authoritative deadlines, evidence requirements, and duplicate constraints remain deterministic.

## Reliability and safety controls

### Duplicate protection

The application and Make use the same `tracking_number_key`. Make checks that key before routing, sends `Exists = false` to the new-case path, and sends `Exists = true` to the duplicate response. The duplicate route terminates before Jira.

Migration `008_shipment_duplicate_lookup.sql` adds the database-level safeguard: only one active case may exist for a normalized carrier and tracking-number pair. This protects the workflow when requests arrive close together and an application-only check is not enough.

Make Data Store module 26 writes the processed record only after Jira creation and includes the Jira issue key. Older records keyed by event ID are legacy data and must not be treated as shipment lookup records.

### Evidence handling

- Original files remain private in S3.
- Make receives time-limited download links only for attachment transfer.
- Images remain unchanged and are reserved for human inspection.
- File declarations are compared with policy requirements, but a declaration is not treated as proof that an image or document says something.
- Claims can proceed with missing evidence so a reviewer can request it.

### Decision boundary

Structured rules are authoritative for policy matching, deadlines, and evidence requirements. Retrieval supplies relevant context to the LLM, but the model does not make the claim decision. A human reviewer always retains final authority.

## Local setup

### Requirements

- Python 3.11 or later
- PostgreSQL with the `vector` extension
- A private S3 bucket
- An OpenAI API key
- A Make webhook connected to Jira

Install the Python dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Provide secrets through environment variables or Streamlit secrets. Do not commit real credentials.

```toml
DATABASE_URL = "postgresql://..."

[aws]
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."

[openai]
OPENAI_API_KEY = "..."

[make]
WEBHOOK_URL = "https://hook.eu1.make.com/..."
ENABLE_CUSTOMER_CASE_HANDOFF = true
```

Apply the SQL migrations in `migrations/` in numerical order, then index the policy library and start Streamlit:

```bash
python3 index_policies.py
python3 -m streamlit run saidia_app.py
```

The default Q&A model can be changed with `OPENAI_QA_MODEL`. The application defaults to `gpt-5.6-sol` when no override is provided.

## Make scenario contract

The current new-claim scenario follows this order:

```text
Webhook 2
  -> Data Store 7: check tracking_number_key
  -> Router 8
      -> new claim: Jira 16 -> attachments 20/23/24/25 -> Data Store 26 -> Webhook 40
      -> duplicate: Webhook 30 and stop
```

The incoming payload includes `case.tracking_number_key`. Both Data Store modules 7 and 26 must map their key directly to that field. The successful response returns the Jira result and case assessment. The duplicate response returns the exact customer message and, when available, the existing case reference.

## Tests

Run the automated suite with:

```bash
python3 -m pytest -q
```

Production smoke testing should confirm two paths with a new tracking number:

1. The first submission creates one Jira issue, transfers its evidence, records the Jira key, and returns the result.
2. A second submission using the same normalized tracking key returns the duplicate message and original case reference without creating another Jira issue.

## Current scope

- Carrier: fictional NorthStar Parcel
- Countries: France and Germany
- Interface: Streamlit web application
- Human review: mandatory for every claim
- Image understanding: not implemented
- External tracking verification: not implemented

## License

This project is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International license. See [LICENSE](LICENSE).
