# 📄 Saidia Smart Document Assistant

Saidia is a GPT-powered complaint-preparation assistant for a fictional parcel carrier. It evaluates logistics incidents against carrier policies and supports document-grounded questions in a private web app.

Saidia is intentionally a single-organisation, single-carrier MVP. The carrier is not a customer-selectable tenant or workspace.

The customer-facing MVP is intentionally configured for the fictional
`NorthStar Parcel` carrier in Germany and France. The underlying data model can
support additional carriers, but they must not be exposed until their policies
are added and evaluated.

👉 **Try the live app here:** [Launch Saidia Smart Assistant](https://smartdocassistant-ibk4wvbdysw7fqkfpkxb7q.streamlit.app/)

## ⚠️ Warning

- Please do not use documents that have sensitive data when trying to use the application as the documents uploaded in the application are store in my AWS S3 bucket.

![Saidia Smart Assistant Home Page](Images/home_page.PNG)

![Saidia Smart Assistant App in action](Images/app_in_action.PNG)

![Saidia Smart Assistant App results](Images/result.PNG)

---

## 🚀 Features

- 🔒 **Secure Document Upload** — Files are stored in AWS S3 bucket
- 🧠 **AI-Powered Q&A** — Uses OpenAI's GPT to answer questions about uploaded documents
- 📄 **Supported File Types** — PDF, DOCX, TXT, JPG, JPEG, and PNG
- 🧾 **Adaptive Text Extraction** — Uses local extraction for digital documents and OpenAI Vision only as an OCR fallback for scanned text documents
- ⚡ **Concurrent First-Pass Processing** — Extracts directly from the original upload while S3 storage runs in parallel, avoiding an immediate S3 re-download
- 🧠 **Semantic Chunking & Embedding** — Text is chunked and embedded using `all-mpnet-base-v2`
- 🔍 **Vector Search** — Uses PostgreSQL with pgvector to retrieve relevant context for question answering
- 🧰 **Agentic Tool Selection** — An OpenAI function-calling controller chooses when to inspect document metadata or search indexed content
- 📚 **Carrier Policy Retrieval** — The agent can compare incidents with a small, clearly labelled fictional evaluation-policy store
- 📋 **Structured Incident Cases** — Converts document facts into a validated case contract and applies deterministic policy, evidence, and deadline checks
- 🛡️ **Incident Relevance Gate** — Keeps unrelated documents available for preview and chat without creating Make, Sheets, or Jira records
- 🔗 **One-Click Automatic Case Handoff** — Processing a document also analyzes its incident and sends the validated, versioned case to Make.com; operational human handling belongs in Jira
- 🗄️ **PostgreSQL Persistence** — Persists real processed-document metadata in hosted Supabase PostgreSQL via `DATABASE_URL`
- ☁️ **Streamlit Cloud Ready** — Fully deployed on Streamlit

---

## 💼 Product Scope

Saidia's MVP supports one internal organisation handling:

- many logistics incidents;
- one configured fictional carrier, `NorthStar Parcel`;
- policies by supported country and incident type;
- structured case handoff for human operational review.

The data model can be extended to additional carriers later, but the customer
form does not expose a carrier selector while only NorthStar policies have been
implemented and tested.

---

## 🔧 Tech Stack

| Tool                  | Purpose                             |
|-----------------------|-------------------------------------|
| `streamlit`           | Frontend UI                         |
| `boto3`               | AWS S3 storage                      |
| `pdfplumber`, `docx`, `PyMuPDF` | Text, annotation, image, and scanned-PDF preparation |
| `sentence-transformers` | Text embeddings                   |
| `pgvector`            | PostgreSQL vector search            |
| `openai`              | Document Q&A, agent tool selection, and automatic vision transcription |
| `psycopg`             | PostgreSQL document persistence        |
| `python-dotenv`       | Local environment setup (optional)  |

---

## 📦 Folder Structure
.
| saidia_app.py         | Main Streamlit app                   |
|-----------------------|--------------------------------------|
| customer_intake.py    | Pure complaint validation and normalization |
| rag_pipeline.py       | Inspects files and selects local or vision extraction |
| s3_upload.py          | Uploads file to AWS S3               |
| vector_store.py       | Document chunking and embedding      |
| qa_engine.py          | GPT Q&A engine                       |
| agent_engine.py       | Bounded read-only document agent and tool controller |
| policy_store.py       | Read-only fictional carrier-policy lookup |
| vision_engine.py      | Automatic OpenAI image transcription |
| incident_case.py      | Structured case validation and deterministic policy analysis |
| case_handoff.py       | Versioned processed-case handoff to Make |
| database.py           | PostgreSQL document persistence       |
| requirements.txt      | .streamlit/-secrets.toml-Private Keys|

📌 Notes
- For digital documents, only retrieved document chunks are sent to OpenAI when answering questions.

- Customer evidence photographs are stored unchanged in private S3 for human review and are not interpreted by AI. Rendered pages from scanned text documents may be sent to OpenAI Vision only when usable native text is unavailable; the app displays this OCR routing clearly.

- Original uploads are stored in a private AWS S3 bucket; selected document content is processed by OpenAI as described above.

- The agent can inspect metadata, search already-processed content, and read fictional evaluation policies. Its tools cannot modify files, delete objects, send messages, or perform external actions.

- Selecting **Process Document** performs extraction, PostgreSQL persistence, incident analysis, and Make handoff in one workflow. Extraction uses the original uploaded bytes while S3 upload runs concurrently; semantic embeddings are deferred until the first chat question so they do not delay the Jira result. Streamlit leads with the Jira result, keeps detailed case analysis collapsed for inspection, and does not approve or reject cases locally.

- Make may return a JSON `jira_result` containing `issue_key`, `title`, `routing`, `status`, `recommended_action`, and optional `jira_url`. Streamlit displays these recruiter-friendly fields without requiring Jira access. Until the external Make scenario returns that JSON, the app displays a successful handoff receipt only.

## 🔑 API Access Keys Required for the application.

##  [aws]
- AWS_ACCESS_KEY_ID = "your_aws_access_key"
- AWS_SECRET_ACCESS_KEY = "your_aws_secret"

## [openai]
- OPENAI_API_KEY = "your_openai_api_key"
- QA_MODEL = "gpt-5.6-sol" # optional override
- VISION_MODEL = "gpt-5.6-sol" # optional override

## [make]
- WEBHOOK_URL = "https://hook.example.make.com/your_private_webhook" # keep private
- ENABLE_CUSTOMER_CASE_HANDOFF = "false" # enable only after mapping the v1 customer event in Make

## Database
- DATABASE_URL = "postgresql://..." # hosted PostgreSQL connection string; keep private

Use Python 3.10 or newer. Create an isolated environment, install dependencies,
then apply every SQL file in `migrations/` in filename order to the same database.
The complete order is:

```text
migrations/000_core_schema.sql
migrations/001_customer_cases.sql
migrations/002_case_processing_metrics.sql
migrations/003_northstar_complaint_policies.sql
migrations/004_customer_case_schema_cleanup.sql
migrations/005_customer_case_updates.sql
migrations/006_complaint_specific_intake.sql
migrations/007_policy_chunks.sql
migrations/008_shipment_duplicate_lookup.sql
```

Then refresh the semantic policy index and start the app:

```bash
python index_policies.py
streamlit run saidia_app.py
```

`all-mpnet-base-v2` produces 768-dimensional embeddings. Both
`document_chunks.embedding` and `policy_chunks.embedding` are therefore
`vector(768)`; application writes and searches reject any other dimension.

These create durable customer-case, evidence, grounded-analysis, lifecycle,
processing-metric, and fictional NorthStar policy records. Original file bodies remain in private S3;
PostgreSQL stores case data, private S3 object keys, document relationships,
processing status, grounded case analysis, and privacy-safe stage timings.

Migration 004 makes `customer_cases` the single operational case source,
connects `workflow_results` directly to it, removes the obsolete empty
`incident_cases` table, and removes the unused customer-photo observation
column. It refuses to drop `incident_cases` or detach unmatched workflow rows
when legacy data is present, so that data must be reviewed first.

Migration 005 adds durable duplicate-attempt records. Migration 008 adds the
shipment-level unique index used to detect an active case by carrier and the
normalized tracking-number key `lower(trim(tracking_number))`.

The Make scenario must use that same normalized tracking-number value as its
Data Store key for both lookup and save. The new-case route is the lookup's
false route; the duplicate route is its true route and must terminate before
Jira. Data Store 26 belongs after successful Jira creation and must save the
normalized key together with the Jira issue key. Any older Data Store records
keyed by `event_id` must be migrated or removed in Make before this contract is
reliable; that remote cleanup is not performed by this repository.

After applying migration 003, run `python index_policies.py` once in the project
environment, and repeat it whenever policy text changes. It refreshes policy
chunks so semantic retrieval can support explanations. Structured fields in
`carrier_policies` remain authoritative: matching is deterministic by explicit
carrier (including an allow-listed alias), country, and complaint type, and
deadline/evidence calculations never depend on vector similarity.

## MVP architecture and demo

Customer intake validates complaint-specific facts and blocks duplicate active
cases. Original evidence is uploaded under a private, case-scoped S3 key; only
metadata and object keys are stored in PostgreSQL. Text documents are extracted,
persisted, and embedded lazily for grounded Q&A, while photographs remain
unchanged for human inspection. Saidia prepares a non-binding analysis from
structured policies, then sends an idempotent event and short-lived evidence
links to Make for Jira. Jira/human review owns the final decision.

Demo checklist:

1. Submit a supported NorthStar complaint with required fields and evidence.
2. Confirm a case reference and private evidence processing status appear.
3. Confirm structured policy guidance, missing evidence, and timing are shown as
   preparation—not approval or denial.
4. Confirm Make returns an accepted receipt and, when configured, an allow-listed
   Jira result with an issue key/link.
5. Ask a question about an uploaded text document to trigger grounded vector Q&A.
6. Resubmit the same active tracking number with different casing or surrounding
   spaces and confirm it terminates as a duplicate before Jira creation.

Known MVP limitations: the carrier policies are fictional; only NorthStar is
exposed in customer intake; external S3/OpenAI/Make/Jira behavior requires valid
private credentials and a mapped Make scenario; there is no employee dashboard;
and automated tests mock external writes rather than creating real Jira issues.

Example performance query:

```sql
select
    c.case_reference,
    e.stage,
    e.duration_ms,
    e.status,
    e.created_at
from case_processing_events e
join customer_cases c on c.id = e.customer_case_id
order by e.created_at desc;
```

Customer evidence limits are 10 files, 10 MB per image, 20 MB per document,
and 50 MB combined per complaint.

## 🙌 Credits
- Created by Treva Ogwang
- Powered by OpenAI + Streamlit + AWS

## ⚖️ License
This project is licensed under the Creative Commons Attribution-NonCommercial-NoDerivatives 4.0 International (CC BY-NC-ND 4.0).

You may not sell, alter, or use this work commercially without explicit permission from the author.
