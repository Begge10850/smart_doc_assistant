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

Before deploying the customer complaint lifecycle, apply migrations in filename
order to the same PostgreSQL database:

```text
migrations/001_customer_cases.sql
migrations/002_case_processing_metrics.sql
migrations/003_northstar_complaint_policies.sql
migrations/004_customer_case_schema_cleanup.sql
```

These create durable customer-case, evidence, grounded-analysis, lifecycle,
processing-metric, and fictional NorthStar policy records. Original file bodies remain in private S3;
PostgreSQL stores case data, private S3 object keys, document relationships,
processing status, grounded case analysis, and privacy-safe stage timings.

Migration 004 makes `customer_cases` the single operational case source,
connects `workflow_results` directly to it, removes the obsolete empty
`incident_cases` table, and removes the unused customer-photo observation
column. It refuses to drop `incident_cases` or detach unmatched workflow rows
when legacy data is present, so that data must be reviewed first.

After applying migration 003, run `python index_policies.py` once in the project
environment. This chunks and embeds the five policy texts so semantic policy
retrieval can find them. Structured policy matching remains deterministic by
carrier, country, and complaint type.

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
