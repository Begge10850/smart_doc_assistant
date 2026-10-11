# Saidia Logistics Claims — Career Handover

## Document status and evidence basis

This handover describes the implementation currently contained in the `smart_doc_assistant` repository. It is based on the repository code, SQL migrations, policy fixtures, automated tests, `README.md`, `PROJECT_NOTES.md`, and verified Git configuration. It intentionally separates implemented technical results from business outcomes that were not measured.

Saidia is a portfolio prototype built around a fictional carrier, NorthStar Parcel. It has not been verified as a production service used by a real carrier or by external customers. The rules and example data are fictional and must not be presented as legal advice or as the policies of a real company.

## Project purpose and intended users

Saidia is an AI-assisted logistics claims workflow that converts a customer’s delivery complaint and supporting evidence into a structured case for human review.

The intended users are:

- Customers or claimants submitting a delivery problem and supporting files through a Streamlit interface.
- Internal claims or operations reviewers receiving structured Jira cases, evidence links, policy guidance, and case context.
- A portfolio case-Q&A demonstration that inspects the current case through read-only tools. A secure employee portal still requires authentication and role checks.

The system supports operational review. It does not approve or reject claims, assign liability, authorize refunds, or replace a human reviewer.

## What Treva built

Treva built an end-to-end prototype covering:

- A customer intake experience for five configured delivery-complaint types.
- Field, date, email, evidence, and upload-limit validation.
- PostgreSQL persistence for customer cases, uploaded-evidence metadata, workflow results, processing events, carriers, policies, policy chunks, documents, and document chunks.
- Private, case-scoped evidence storage in Amazon S3 with temporary download links.
- Deterministic policy matching, filing-deadline calculation, evidence-completeness checks, and non-binding review guidance.
- Shipment-level duplicate protection in both application logic and the database.
- Policy retrieval with sentence-transformer embeddings and pgvector.
- OpenAI-assisted, grounded explanations and read-only case Q&A.
- An HTTPS webhook integration with Make for idempotent Jira issue creation and attachment transfer.
- Failure-state and processing-event persistence to make workflow problems recoverable and inspectable.
- Automated tests across intake, policy logic, embeddings, duplicate handling, document extraction, Q&A constraints, and Make/Jira handoff behavior.

## Complete technology stack

| Area | Verified implementation |
|---|---|
| Language/runtime | Python 3.11+ |
| User interface | Streamlit |
| Database | PostgreSQL hosted/configured for Supabase |
| Vector retrieval | pgvector with cosine similarity and HNSW indexes |
| Embeddings | `sentence-transformers/all-mpnet-base-v2`, 768 dimensions |
| Generative AI | OpenAI Responses API; model is configurable, with `gpt-5.6-sol` as the current repository default |
| Object storage | Amazon S3 using `boto3` |
| Workflow automation | Make HTTPS webhook scenario |
| Review system | Jira issue and attachment workflow through Make modules |
| Document processing | `pdfplumber`, PyMuPDF, `python-docx`, and text extraction |
| Database client | `psycopg` with pgvector support |
| Configuration | Environment variables and Streamlit secrets |
| Version control | Git and GitHub |
| Testing | Python `unittest`-compatible tests; repository also contains pytest discovery metadata |

Docker, Swagger/OpenAPI, and a CI/CD pipeline are not implemented in this repository. Jira is integrated through Make rather than through a direct Jira REST API client.

## Data sources and scale

### Data sources

- Customer-entered form data, including contact and shipment/incident details.
- Customer-uploaded images and supported text documents.
- Five versioned fictional NorthStar Parcel policy records stored as repository fixtures and database data.
- Extracted text and chunks from supported documents.
- Make/Jira workflow responses reduced to approved fields before persistence or display.
- Synthetic and mocked data used by automated tests.

### Verified scope and scale

- Five configured complaint types: parcel damage, lost parcel, late delivery, partial loss, and delivered-but-not-received/non-delivery.
- Two configured countries: Germany and France.
- One fictional carrier: NorthStar Parcel.
- Five policy records, effective from 1 January 2026.
- Embeddings with 768 dimensions.
- Up to 10 evidence files per submission.
- Maximum individual image size: 10 MB.
- Maximum individual document size: 20 MB.
- Maximum combined upload size: 50 MB.
- General scanned-PDF processing is bounded to 20 pages in the repository’s document-processing path.
- Temporary evidence download links expire after one hour.

There is no verified dataset of real claims, no verified production case count, and no measured user population.

## Architecture and workflow

1. **Intake:** A customer selects a complaint type and enters the required incident, contact, shipment, and date information.
2. **Validation:** Python checks required fields, email format, date relationships, declared value, complaint-specific evidence requirements, file counts, file sizes, and total upload size.
3. **Secure persistence:** The case and evidence metadata are saved in PostgreSQL. Original files are stored under case-scoped keys in a private S3 bucket.
4. **Duplicate protection:** The workflow normalizes carrier and tracking-number values and checks for an active case. A database unique index provides concurrency-safe protection for active shipment cases.
5. **Deterministic assessment:** Structured policy records are matched to the complaint and country. Python/PostgreSQL logic calculates filing deadlines, identifies missing evidence, and produces non-binding guidance.
6. **Policy retrieval:** Policy text is chunked, embedded with `all-mpnet-base-v2`, stored in pgvector, and retrieved with cosine similarity.
7. **AI explanation and Q&A:** The OpenAI integration explains verified structured results and answers questions using allow-listed case facts, deterministic assessment data, and retrieved policy excerpts.
8. **Operational handoff:** When enabled, the app sends an HTTPS webhook to Make with an idempotency key. Make creates the Jira issue, transfers attachments, records the Jira reference, and returns a small allow-listed response.
9. **Human review:** A claims or operations reviewer makes the final decision in the operational workflow.
10. **Recovery and observability:** Processing stages, latency values, completion states, and failures can be recorded for diagnosis and operational recovery.

## Implemented features

- Complaint-specific forms and evidence prompts.
- Acceptance of incomplete submissions with an explicit list of missing policy evidence.
- Structured case IDs derived consistently from incident/document inputs.
- Private original-file preservation for human inspection.
- PDF, DOCX, and TXT extraction in the document-processing path.
- Versioned policy storage and retrieval.
- Deterministic deadline and evidence-completeness assessment.
- Semantic policy search using pgvector.
- Grounded case explanations and read-only Q&A.
- Duplicate-case detection that returns the existing case reference where available.
- Idempotent Make/Jira handoff.
- Allow-listed handling of returned Jira fields and validation of returned URLs.
- Processing acknowledgements, background work, completion states, and recoverable failure states.

## Business rules implemented

The repository implements fictional demonstration rules, including:

| Complaint type | Demonstration filing window |
|---|---|
| Parcel damage | Within 7 days after delivery |
| Lost parcel | Within 30 days after expected delivery |
| Late delivery | Within 14 days after actual delivery |
| Partial loss | Within 7 days after delivery |
| Delivered but not received | Within 7 days after carrier-recorded delivery |

Additional verified rules include:

- Complaint-specific required fields and evidence types.
- Incident and delivery dates cannot be in the future.
- For late delivery, actual delivery must be later than promised delivery.
- Declared values must be numeric where supplied.
- Fictional service commitments of five days for NorthStar Standard and three days for NorthStar Express.
- Non-binding late-delivery guidance distinguishes a one-day delay from delays of two or more days, while leaving exclusions and the final decision to a human reviewer.
- Active duplicates are determined from normalized carrier and tracking-number values; closed or cancelled cases do not remain within the active uniqueness rule.
- Database migrations refuse to add the active-case uniqueness constraint when conflicting active duplicates already exist, preventing silent data loss.

These rules are demonstration content, not verified carrier policy or legal guidance.

## AI tools and AI-assisted capabilities actually implemented

- **Semantic policy retrieval:** Sentence-transformer embeddings and pgvector retrieve relevant policy passages.
- **Grounded explanation:** The OpenAI Responses API turns deterministic assessment results and retrieved passages into a clearer explanation.
- **Read-only case Q&A:** A constrained assistant must choose between four narrow tools: `inspect_case`, `search_case_evidence`, `inspect_policy_assessment`, and `inspect_workflow_status`. It is instructed not to make claim decisions, assign liability, or promise refunds.
- **Visible grounding evidence:** The interface shows which read-only tools were used and concise source labels such as the customer form, an evidence filename, a policy ID, or a Jira/workflow reference.
- **Structured extraction controls:** The agent path uses strict structured schemas, a bounded number of tool rounds, and limited search-result counts.
- **General document ingestion:** The repository contains text extraction and a vision-capable path for general/scanned document processing.

Customer evidence images in the claims workflow are deliberately preserved for human inspection and are not interpreted to infer damage or other claim facts. The project therefore must not be described as automated visual damage assessment.

## Security and privacy considerations

Implemented safeguards and boundaries include:

- Original evidence is stored in a private S3 bucket under case-scoped object keys.
- Evidence is shared through time-limited presigned links with a one-hour expiry.
- Secrets are expected in environment variables or Streamlit secrets; `.env` and `.streamlit/secrets.toml` are excluded from version control.
- The Make endpoint must use HTTPS.
- Make requests use a 60-second timeout and an idempotency key.
- Make responses are limited to 64 KB and reduced to an allow-list of expected Jira fields.
- Returned URLs are validated before use.
- Relational constraints, foreign keys, unique identifiers, and active-case uniqueness protect database integrity.
- Customer images are not sent for automated interpretation in the claims flow.
- Human review is mandatory for the final operational decision.
- Test and deployment guidance calls for non-sensitive demonstration data.

Limitations: the repository does not establish GDPR certification, a formal data-retention schedule, enterprise identity and access management, encryption-key governance, penetration-test results, or a completed privacy-impact assessment. These must not be implied.

## Testing, evaluation, and validation

### What is covered

The test suite covers areas including:

- Intake validation, complaint-specific requirements, dates, files, and limits.
- Duplicate detection and concurrent duplicate submissions.
- Deterministic policy matching, deadlines, and evidence assessment.
- Policy fixtures and 768-dimensional embedding configuration.
- Agent tools, strict schemas, and rejection of unsupported/non-incident claims.
- Grounded Q&A prompt constraints.
- Make/Jira handoff, idempotency, timeouts, duplicate routing, allow-listed responses, and the no-image-AI boundary.
- DOCX extraction and vision configuration in the general document path.

### Current verification result

- A dependency-equipped Python 3.12 run completed 54 automated tests successfully.
- A five-case deterministic evaluation initially passed 3/5 cases, exposed a date-label mapping defect, and passed 5/5 after the repair and regression test.
- The deliberately scoped core business-rule and handoff modules measured 81% statement coverage (453 statements, 86 missed). This is not a whole-application coverage figure.
- The live language model, hosted database, S3, Make, and Jira were not exercised by this local verification.

The project still has no verified hallucination rate, live retrieval Hit@K score, production latency benchmark, throughput benchmark, or A/B evaluation. The saved case-assistant evaluation questions define how those checks can be performed later with fictional data.

## Verified results and findings

### Implementation results verified in the repository

- The application models an end-to-end path from customer intake to a structured Jira handoff for human review.
- Five complaint types and five fictional policies are represented consistently in application configuration, rules, and migrations.
- Deterministic logic, rather than the LLM, controls filing deadlines and evidence-completeness results.
- The database and application both protect against active shipment duplicates, including concurrent submissions.
- Policy retrieval uses a fixed 768-dimensional embedding configuration supported by database indexes.
- Customer evidence can be stored privately and exposed through temporary links rather than public objects.
- Make/Jira responses are constrained before being trusted by the application.
- Failures and workflow state can be persisted instead of silently losing a received case.
- The loaded automated tests passed in the current partial verification described above.

### Measured business impact

No production business impact has been measured. There is no verified evidence of reduced handling time, fewer support tickets, improved approval accuracy, lower fraud, higher recovery value, cost savings, revenue impact, customer satisfaction, or adoption by a real operations team.

The value demonstrated is technical and product capability: structuring an intake process, encoding review rules, protecting evidence, grounding AI output, preventing duplicate work, and handing a case to a human-controlled operational system.

## Decisions, trade-offs, and limitations

- **Deterministic rules over LLM decisions:** Deadlines and evidence completeness are calculated in code/database logic for consistency and auditability. The LLM explains results but does not decide outcomes.
- **Human review over full automation:** This reduces the risk of unsupported claim decisions but means the system is an assistant, not an autonomous claims processor.
- **Private storage and temporary links:** This supports controlled evidence access but requires correct S3 permissions and operational secret management.
- **Make for Jira orchestration:** This speeds integration and makes the workflow visual, but introduces an external automation dependency. There is no direct Jira REST client in the repository.
- **Shared duplicate key across layers:** Application, database, and Make use normalized tracking identifiers. This improves resilience but requires the normalization contract to stay aligned.
- **Semantic retrieval as supporting evidence:** Vector search improves policy discovery, while structured policy fields remain authoritative. Retrieval quality was not benchmarked quantitatively.
- **No customer-photo interpretation:** Preserving images for humans avoids unsupported visual conclusions but limits automation.
- **Narrow demonstration scope:** The configured carrier, policies, two-country scope, and service commitments are fictional and not a general carrier-rules engine.
- **No external tracking verification:** The system relies on submitted information and does not verify status against a carrier API.
- **Deferred operations dashboard:** The notes describe a potential employee Jira operations dashboard, but it is not implemented and no operational KPI results are available.
- **Deployment maturity:** A README-listed Streamlit deployment exists, but there is no verified CI/CD pipeline, production SLA, monitoring platform, or security certification.

## Deployment, repository, and portfolio links

- Git repository root: `/Users/trevaogwang/Documents/mobile-projects/saidia/smart_doc_assistant`
- GitHub remote: <https://github.com/Begge10850/smart_doc_assistant>
- README-listed Streamlit application: <https://smartdocassistant-ibk4wvbdysw7fqkfpkxb7q.streamlit.app/>
- Portfolio page used in Treva’s CV: <https://trevaogwang.com/projects/saidia-logistics-claims>

The Git remote was verified locally. The Streamlit and portfolio URLs are recorded from the repository/CV context; their current public availability was not independently verified during this handover.

## Skills demonstrated

- Python application development and modular workflow design.
- Streamlit interface development.
- PostgreSQL schema design, migrations, constraints, and transaction-aware duplicate prevention.
- SQL-based operational data modelling.
- Amazon S3 object storage, private evidence handling, and presigned URLs.
- Retrieval-augmented generation with sentence transformers, pgvector, and the OpenAI Responses API.
- Prompt and tool-boundary design for grounded, human-in-the-loop AI.
- API/webhook integration using HTTPS and structured JSON.
- Make workflow automation and Jira operational handoff.
- Input validation, error handling, idempotency, and recoverable workflow states.
- Automated testing and boundary-case design.
- Technical documentation, product scoping, and explicit limitation management.

## Relevance to target roles

| Role family | Evidence from Saidia |
|---|---|
| Data analyst | Defined structured operational fields, evidence statuses, policy outcomes, and workflow events that can support reporting; translated case data into review-ready information. No production KPI analysis has been completed. |
| Data engineer | Designed PostgreSQL schemas and migrations, ingestion/persistence paths, S3 evidence storage, document chunking, vector indexes, data constraints, and integration flows. |
| Data scientist | Applied embeddings and semantic similarity to policy retrieval and defined evaluation boundaries. The project does not contain a trained predictive model or measured model-performance study. |
| AI/ML or applied-AI | Built grounded RAG, constrained tool use, strict structured extraction, safety boundaries, and human-in-the-loop explanations/Q&A using OpenAI and pgvector. |
| Product/operations | Mapped a customer complaint journey into intake, validation, evidence, duplicate prevention, handoff, recovery, and human-review stages; documented trade-offs and deferred scope. |
| Software/technical roles | Implemented modular Python, database integrity controls, secure storage, webhook contracts, integration resilience, automated tests, and deployable Streamlit configuration. |

The project is strongest as evidence of applied AI, data engineering, workflow automation, and product-minded technical implementation. For an application, select and order the evidence to match the target role rather than presenting every capability with equal weight.

## Five evidence-based CV bullets

- Built an end-to-end Streamlit claims assistant for five logistics complaint types, converting customer submissions and evidence into structured cases for mandatory human review.
- Implemented versioned policy matching, filing-deadline calculation, evidence-completeness checks, and concurrency-safe duplicate prevention using Python and PostgreSQL.
- Developed a grounded RAG workflow using 768-dimensional sentence-transformer embeddings, pgvector semantic search, and the OpenAI Responses API for policy explanations and read-only case Q&A.
- Secured case evidence in private Amazon S3 storage with case-scoped object keys and one-hour presigned links, then automated idempotent Jira issue and attachment handoff through Make webhooks.
- Verified 54 automated tests, a 5/5 deterministic case evaluation, and 81% statement coverage across the explicitly scoped core business-rule and handoff modules.

## Concise LinkedIn project description

Built Saidia, an AI-assisted logistics claims workflow that turns customer delivery reports and uploaded evidence into structured, policy-grounded cases for human review. I implemented the Streamlit intake, Python/PostgreSQL rules for deadlines and evidence checks, private S3 evidence storage, duplicate prevention, pgvector policy retrieval, grounded OpenAI explanations and Q&A, and an idempotent Make-to-Jira handoff. The current portfolio version uses five fictional NorthStar Parcel policies across Germany and France and deliberately keeps final claim decisions with a human reviewer.

## Interview-ready STAR explanation

**Situation:** Delivery complaints often arrive as unstructured descriptions with different dates, evidence, and issue types. For a portfolio case study, I wanted to show how an operations team could receive more consistent, review-ready cases without letting AI make high-risk decisions.

**Task:** I set out to build an end-to-end prototype that could collect a complaint, validate it, apply transparent policy rules, protect the supporting files, prevent duplicate active cases, explain the result, and hand the case to Jira for human review.

**Action:** I built the customer flow in Streamlit and modelled cases, policies, evidence, workflow results, processing events, and vector chunks in PostgreSQL. I kept filing deadlines and evidence checks deterministic, then used `all-mpnet-base-v2` embeddings and pgvector to retrieve relevant policy passages. The OpenAI layer explains those verified results and supports constrained, read-only Q&A. I stored original evidence privately in S3 with temporary links and connected the app to Make through an HTTPS webhook for idempotent Jira creation and attachment handling. I added application- and database-level duplicate protection, allow-listed the returned Jira fields, persisted failures for recovery, and wrote tests for the main rules and integration boundaries.

**Result:** The repository demonstrates a working technical path from intake to a review-ready operational case across five configured complaint types, with human decision authority preserved. In the current verification, all 54 automated tests passed. A five-case deterministic evaluation passed 5/5 after exposing and guiding the repair of a date-label mapping defect, and the explicitly scoped core business-rule and handoff modules measured 81% statement coverage. The result is a validated portfolio implementation, not evidence of production adoption or measured business savings.

## Claims that must not be made

Do not claim that:

- NorthStar Parcel is a real client, carrier, or policy source.
- Saidia is used in production by real customers or a real claims team.
- The project processed a stated number of real claims or served a stated number of users.
- It reduced handling time, costs, fraud, errors, support tickets, or claim losses by a percentage or monetary amount.
- It improved customer satisfaction, acceptance rates, or reviewer productivity.
- The AI approves or rejects claims, assigns liability, authorizes refunds, or provides legal advice.
- Customer photographs are analysed to detect or classify damage.
- The system verifies shipment events with an external carrier/tracking API.
- Jira is integrated directly through its REST API; the verified implementation uses Make’s Jira modules.
- Swagger/OpenAPI documentation, Docker containerization, or CI/CD is implemented for this repository.
- The deferred employee Jira operations dashboard has been built.
- The system supports all carriers, countries, languages, or complaint categories.
- The project is GDPR-certified, security-certified, penetration-tested, or proven compliant with a specific regulatory framework.
- Model accuracy, retrieval accuracy, precision, recall, latency, throughput, or cost has been benchmarked unless a future evaluation produces those measurements.
- The 81% coverage figure represents whole-application coverage; it applies only to the named core business-rule and handoff modules.
- The “37 automated tests” previously associated with the RoutePulse project applies to Saidia.
