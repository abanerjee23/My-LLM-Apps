# SourceLens — Product Brief

**Status:** Working MVP; focused release hardening remains.
**Created:** 24 September 2026  
**Working name:** SourceLens. Final naming and availability remain open.  
**Direction:** A standalone agent-led research product for evidence-backed business investigation.

## 1. Vision

SourceLens is an agent-led customer-feedback research workspace. Users connect data, provide an investigation brief, and work with an agent that discovers patterns, presents evidence, proposes useful investigative directions, and builds a visual research brief throughout the conversation.

> Connect your customer feedback. The agent leads the investigation and turns messy data into findings you can understand, verify, review, and keep.

The product reduces time from raw data to useful analysis by combining ingestion, cleaning, structuring, investigation, visualisation, and human review in one workspace.

The long-term ingestion architecture should accommodate other information sources and domains. The initial product focuses on customer feedback so that user value and analysis quality can be evaluated concretely.

## 2. User and job to be done

**Primary user:** A product manager investigating customer experiences across products, sources, and time periods.

**Job to be done:** When faced with a large collection of customer feedback, help me identify what deserves attention, understand the evidence and limitations, and retain defensible conclusions without manually reading and organising every comment.

The user may know the product or period they want to investigate without knowing which themes, comparisons, or follow-up questions will be useful.

An initial brief could be: “Analyse feedback for this headphone range during this period.” It can also span hundreds of products. The product must not require the user to narrow the collection to one product merely to accommodate model context limits.

## 3. Problem

Customer feedback is scattered, inconsistently structured, repetitive, and sometimes contradictory. Understanding it involves collecting exports, cleaning records, reading comments, grouping themes, comparing segments, finding representative evidence, and assembling a report.

Manual work is slow and difficult to reproduce. A one-shot model summary can sound convincing while omitting important themes, using unrepresentative examples, or presenting conclusions that are difficult to verify. Ordinary chat also leaves users responsible for directing each analytical step and keeping charts, evidence, and conclusions consistent.

SourceLens must reduce both analysis effort and verification effort.

## 4. Why AI, and where deterministic software belongs

Model intelligence is central to interpreting varied language, recognising related complaints, discovering useful relationships, testing interpretations against counterevidence, choosing investigative directions, and explaining findings.

Deterministic software handles filtering, counting, calculations, schema validation, persistence, provenance, and versioning. It computes measurements from the structured observations; the underlying theme and sentiment labels remain model-derived judgments that users can inspect and correct.

The agent decides which supported investigative tools to use and which visual forms clarify the findings. The application validates tool inputs and outputs and renders charts from computed data.

## 5. Product differentiation

Uploading reviews and producing a summary is insufficient differentiation from general-purpose chat. The intended advantage is the quality and independence of the investigation, supported by:

- Reusable source connections and a maintained, auditable dataset.
- Systematic coverage rather than conclusions based only on retrieved examples.
- Proactive, evidence-driven follow-up investigations.
- Synchronised conversation, visual artifacts, and source evidence.
- Human corrections and decisions preserved in a notebook.
- Measured improvements in usefulness, accuracy, review effort, and time to value.

Model intelligence enables this experience. A durable advantage must be demonstrated through dependable investigation and accumulated, curated evaluation evidence; autonomy alone is not proof of a moat.

## 6. Core experience

> Surface a pattern → show evidence → propose a useful direction → incorporate the user's judgment → update the artifacts.

### Connect and inspect data

The user uploads data or selects an available connector. The system preserves originals and metadata, extracts content, normalises fields, identifies duplicates, and exposes processing gaps. Derived content remains traceable to the original records.

### Establish the investigation scope

The user provides a trigger describing their interest. The agent resolves product names and aliases, dates, source selection, and relevant filters. It shows the interpreted scope, matching record counts, and available coverage.

Ask for clarification only when ambiguity would materially change the investigation. Where safe, proceed with a visible assumption. If the dataset cannot answer the brief, explain the specific coverage gap.

### Investigate independently

After the trigger, the agent should complete a meaningful investigation before requiring further input. It explores themes, chooses relevant comparisons, checks contradictory evidence, and prepares findings and visuals. These steps should follow the evidence rather than a rigid checklist of identical analyses for every brief.

The agent progresses between questions. It asks when a missing business preference or consequential ambiguity limits the next useful step. Users can interrupt, redirect, narrow the scope, or correct an interpretation at any time.

### Surface findings

Present up to five findings worth investigating, with fewer when evidence is insufficient. Selection may consider prevalence, breadth across products, concentration, changes over time, divided sentiment, and reported severity.

“Top” must have a visible meaning. Do not default to raw volume alone or invent an unexplained importance score. Explain why each selected finding deserves attention with concrete evidence.

Each finding distinguishes:

- **Observation:** What the available sources and computed measurements show.
- **Interpretation:** What the agent infers, including uncertainty.
- **Suggested next step:** What would be useful to investigate or consider next.

### Review and save

The user accepts, rejects, or edits findings and the overall brief. The investigation maintains those judgments and updates its artifacts. A decision on the overall brief creates a notebook snapshot containing the complete analysis and its review outcome.

## 7. Data strategy and initial source

The connected reference business is stored in a real BigQuery dataset. It contains four products, 124,747 sales lines, 1,084 feedback records, and aligned monthly sales, inventory, and return aggregates. Its known but agent-hidden outcomes support controlled evaluation without presenting the records as a real company's results.

The original SQLite source and provenance manifest are retained in Cloud Storage. The same data is available locally so deterministic validation and product demonstrations do not depend on live cloud access. Real public feedback remains a later connector demonstration after provenance and redistribution terms are checked.

### Raw-data audit trail

Keep original uploaded files and captured connector responses in object storage before transformation. Record source identity, capture time, available source event time, content hash, ingestion batch, and source version. New captures create new versions rather than overwrite the evidence used by an existing brief.

Store cleaned text, extracted observations, and analytical tables separately. Preserve lineage from each finding through its supporting records and transformations to the raw source snapshot. For quantitative findings, retain the query/filter definition, metric definition, and input versions as well as the computed result. Notebook snapshots reference the exact evidence and analysis versions reviewed.

Retention and deletion policies must account for raw evidence and saved briefs together; if retained evidence is deleted, explicitly mark affected citations unavailable. This is an application audit trail, not a claim of certified compliance or tamper-proof storage.

### Connector principle

Amazon is the first source, not the product boundary. Users should eventually be able to connect sources of their choice through supported connectors, such as review feeds, support exports, surveys, and interview notes.

Each connector maps to a common source contract containing original content, source identifier, product/entity metadata when available, event date, ingestion time, version, and an original-record reference. Preserve source-specific fields and distinguish missing values from known values.

Adding a connector should not require rebuilding the investigation workflow. Support for every possible provider is not an initial-release promise.

### Cross-source investigation: from symptoms to possible explanations

Customer feedback is the initial entry point, but feedback alone may not explain a business outcome. The broader product direction is to combine qualitative evidence with relevant structured business data when available. For example, falling sentiment and falling revenue could warrant examining prices, unit sales, returns, availability, promotions, and product changes.

The agent should maintain an evidence-availability map: what sources are connected, which entities and periods they cover, what metrics they contain, and which questions remain unanswered. It uses available sources independently and requests additional data when a material hypothesis cannot otherwise be assessed. Missing data is an explicit gap, not permission to invent an explanation.

| Possible explanation | Useful additional evidence |
| --- | --- |
| Pricing or discount changes | Realised selling prices, units, promotions, and comparable price history. |
| Product quality deterioration | Return reasons, warranty claims, support issues, batch or release identifiers. |
| Availability or fulfilment issues | Stock levels, stockouts, channel availability, delivery performance. |
| Product or channel mix changes | Sales by SKU, geography, channel, and comparable period. |
| Seasonality or demand changes | Longer sales history and appropriately matched comparisons. |

Connectors alone do not solve integration. Preserve each source's grain and establish explicit entity mappings, compatible periods, currencies, metric definitions, and join rules. A product-month comparison must not imply that a reviewer is linked to a specific transaction. Prevent joins from multiplying sales totals when many reviews map to the same product. Report unmatched records and incomparable populations.

Use structured queries for business metrics and retrieval for narrative evidence, bringing both into the same investigation. Maintain hypotheses with supporting evidence, contradictory evidence, missing evidence, and a next test. Distinguish observed associations from possible explanations and validated causal conclusions; temporal coincidence alone is insufficient to prove causation.

An illustrative investigation might detect a sentiment decline, separate revenue changes into units and realised price, compare relevant segments and periods, inspect quality-related comments and returns, then propose the best-supported next test. Visuals could include aligned timelines, revenue decompositions where mathematically valid, and a hypothesis/evidence table. User review and notebook capture apply to these artifacts too.

The Amazon dataset does not provide verified internal revenue, transaction, or returns history. A future joined demonstration requires a compatible real source or an explicitly labelled reference business dataset; generated measurements must never be presented as actual Amazon business outcomes. The initial release can demonstrate useful feedback analysis and explicit evidence gaps before adding this broader capability.

## 8. Processing, retrieval, and scale

Conceptual pipeline:

**Capture → Parse → Normalise → Extract → Reconcile themes → Index and aggregate → Investigate → Validate → Human review**

Raw sources, derived observations, and analysis outputs have separate versions. Record content hashes, processing versions, and source relationships so findings can be traced and reproduced against a dataset snapshot.

The context window limits an individual model call, not the dataset the product can analyse. Keep the collection in storage, process manageable batches, persist structured observations, and retrieve original evidence when needed.

Token budgeting is an internal safeguard before every model call. It accounts for instructions, conversation, data, tool material, reserved output, and a safety margin. Do not silently truncate data or routinely ask users to split uploads into prompts. Report coverage, progress, and incomplete analysis when operational limits are reached.

For prevalence claims, process the entire matching collection, reconcile overlapping theme labels across batches, and use explicit counting units and denominators. A review may contribute to several themes but should not be counted repeatedly within the same theme. Distinguish distinct reviews from distinct people where identity is unavailable.

RAG supports finding and inspecting evidence. A few retrieved comments cannot establish the most common problem. Corpus-wide processing and deterministic aggregation establish measurements; model interpretation makes them useful.

Scalability includes connector extensibility, datasets spanning hundreds of products, reusable prior processing, and durable jobs with progress, retries, and restart-safe state. Final capacity, latency, and cost limits remain to be measured.

## 9. Hybrid workspace and visual artifacts

The desktop direction is a modern chat interface beside a live analysis canvas, with a personal notebook underneath. Keep the design clean and restrained: neutral surfaces, readable typography, generous spacing, and limited accent colour.

```text
┌────────────────────────────────────────────────────────┐
│ SourceLens       Sources · Investigation scope          │
├──────────────────────┬─────────────────────────────────┤
│ Conversation         │ Live analysis                    │
│                      │                                 │
│ Initial brief        │ Findings · Graphs · Research brief│
│ Agent progress       │                                 │
│ Findings & questions │ Source evidence on citation click│
│ User input           │                                 │
├──────────────────────┴─────────────────────────────────┤
│ Notebook                                      View all │
│ [Title / Date / Decision / Preview] [Brief tile] [...]  │
└────────────────────────────────────────────────────────┘
```

The conversation occupies roughly one-third of the main workspace as an initial layout direction. The remaining space supports charts, the brief, and inspectable evidence. Responsive details remain a design task.

### Visible agent activity

Show concise activity and decision summaries grounded in actual processing: “Matched products and date range,” “Comparing connectivity complaints,” or “Checking contradictory comments.” Expand completed steps to expose scope, counts, and results where helpful.

These are user-facing progress explanations, not private internal chain-of-thought or fabricated thinking animations.

### Artifact vocabulary

| Artifact | Purpose |
| --- | --- |
| Theme breakdown | Show complaints, praise, and requests with counts and proportions. |
| Product × theme heatmap | Reveal concentrated issues and useful comparisons. |
| Sentiment timeline | Show expressed sentiment over time alongside review volumes. |
| Phrase map | Group meaningful customer phrases by theme and sentiment, linking to source comments. |
| Evidence board | Display supporting and conflicting quotations for a finding. |
| Conclusion challenge card | Explain counterexamples, small samples, or missing information that could change a conclusion. |
| Living research brief | Maintain findings, limitations, unresolved questions, and suggested next steps. |

A phrase map is preferred to a decorative word cloud because isolated words lose context. The agent selects artifacts that answer the current question; it need not generate every chart for every investigation.

Conversation, charts, counts, summaries, and citations share a versioned analysis state. Scope changes update relevant artifacts together. Show artifact scope and dataset version; indicate stale or recomputing results rather than displaying mismatched conclusions as current.

## 10. Human judgment, ratings, and notebook

### Decisions

Support **Accept**, **Reject/Deny**, and **Edit** for findings and the overall brief. Preserve originals and edits. Editing does not imply acceptance; the user explicitly accepts an edited brief when satisfied.

Finding-level decisions update the working brief. A whole-brief decision saves a notebook entry, avoiding a tile for every small interaction. Rejected briefs are saved as useful investigation history, including a rejection reason when supplied.

### Two independent ratings

| Rating | Scale | Meaning of 5 |
| --- | --- | --- |
| Source accuracy | 1–5 | The reviewer judges the summary fully accurate and faithful to the source information. |
| Relevance, completeness, and usefulness | 1–5 | The reviewer judges the output fully relevant, complete, and useful for the investigation brief. |

The intended top rating expresses the user's assessment of 100% accuracy or usefulness, not a system-certified guarantee. Ratings attach to the exact reviewed version. Optional feedback tags such as unsupported claim, missed theme, or wrong emphasis make scores actionable. Lower-score rubric anchors remain to be defined.

### Notebook

Present a clean tile grid below the main workspace. Each tile includes a title, saved date and time, decision badge (**Accepted**, **Edited & accepted**, or **Rejected**), short preview, and submitted ratings when present.

Opening a tile reveals the full analysis: original brief, scope and coverage, summary, findings, graphs, citations, supporting comments, limitations, user edits, and decision notes.

Saved entries are fixed snapshots. Continuing an investigation creates a new revision; later data changes do not silently rewrite saved evidence or conclusions. Flag accepted findings for review when subsequent analysis undermines them.

Authentication is explicitly excluded initially. The first notebook is a shared demo workspace, without claims of verified reviewer identity or separate account-based private notebooks.

## 11. Trust, reliability, and safety

- Preserve raw records and trace derived observations and conclusions back to them.
- Validate that citations resolve to saved source evidence; assess whether the evidence supports the claim separately.
- Display processing gaps, missing periods, insufficient evidence, and contradictory feedback.
- Keep exact calculations in code and make denominators visible.
- Do not equate review frequency with customer-wide defect rates or infer causation from comments.
- Treat uploaded and retrieved content as evidence, never as instructions that can override agent behaviour.
- Bound tool execution, retries, investigation depth, and per-run spend; expose partial completion clearly.
- Make malformed model outputs and extraction failures recoverable and observable.
- Keep source retention, deletion, and trace exposure explicit when implementing storage and Galileo integration.
- Human acceptance records a judgment; it does not independently certify a claim as true.

## 12. Evaluation and success measures

Primary outcome: **time to a reviewed, evidence-backed conclusion**, assessed together with quality so speed does not reward shallow analysis.

Compare SourceLens with a general-purpose chat workflow using the same dataset and task. Measure whether it reduces analysis effort, verification effort, and required user direction.

| Dimension | Measures |
| --- | --- |
| User value | Time to first useful finding, time to reviewed brief, task completion. |
| Investigation quality | Important-theme coverage, usefulness of proposed directions, counterevidence handling. |
| Independence | Necessary versus avoidable clarification, user interventions, progress after the initial trigger. |
| Evidence quality | Citation correctness, claim support, unsupported-claim rate, aggregation accuracy. |
| Human feedback | Both 1–5 ratings, acceptance/rejection, edits and reasons. |
| Reliability and scale | Processing coverage, failure/retry rates, artifact consistency, connector integration effort. |
| Economics | Latency, token use, cost per investigation and reviewed brief, processing reuse. |

Use deterministic checks for calculations, citations, schemas, and state consistency. The implemented bounded Sol eval records model output, cost, citations, visible actions, causal caveats, and supported findings. Galileo is the selected external eval and trace sink once account credentials and plan availability are supplied.

Calibrate automated judging against human assessments. Curate reviewed corrections into candidate evaluation cases; do not automatically treat all accepted edits as truth or rewrite prompts from unreviewed feedback.

The first calibrated five-case run passed 5/5 after a baseline 4/5 exposed a false-negative evaluator. This small result demonstrates an evaluation iteration; it is not a production-quality claim.

## 13. Technology direction

The product requirements above take priority over implementation choices. The following stack is implemented for the MVP.

| Layer | Direction | Decision status |
| --- | --- | --- |
| Principal language | Python | User preference. |
| Backend | FastAPI and typed Pydantic schemas | Implemented. |
| Orchestration | OpenAI Agents SDK | User preference. |
| AI evaluation | Local bounded eval runner | The reproducible local report is implemented. |
| Observability | Galileo | Selected; connection pending API credentials. |
| Frontend | React, TypeScript, Vite | Implemented. |
| Raw sources | Google Cloud Storage | Provisioned with a versioned reference source. |
| Analytical store | BigQuery, with a local SQLite fixture | Provisioned and verified. |
| Application state | SQLite in the current deployment | Implemented; managed durability is required before horizontal scaling. |
| Retrieval | Qdrant-compatible rebuildable index with local fallback | Implemented and locally tested; cloud activation needs credentials. |
| Deployment | Cloud Run container and Cloud Build configuration | Packaged; deployment remains a gated release action. |
| Authentication | None initially | Explicit scope decision. |

Qdrant serves retrieval, not the sole raw-data archive or transactional system of record. Sol handles every model-backed investigation and preparation role; evaluation informs reasoning settings and routing refinements.

## 14. Initial scope and boundaries

The first complete experience should include one real dataset and connector, auditable ingestion, a user investigation trigger, independent analysis, evidence-linked findings, adaptive charts, follow-up interaction, decisions and ratings, and dated notebook snapshots.

The first release does not promise arbitrary provider integrations, every document format, real-time feeds, every domain, account-based collaboration, or external business actions. Procurement and publication analysis are possible future applications, not initial workflows.

SourceLens has its own application, domain model, investigation workflow, and evaluation criteria. The current build direction and budget are recorded in [BUILD.md](BUILD.md), including a coherent reference business in BigQuery as the primary integrated demonstration.

## 15. Open decisions and next step

- Activate the Qdrant Cloud collection once endpoint and API-key credentials exist.
- Connect Galileo once its project, log stream, and API key exist.
- Add arbitrary date parsing and map uploaded source fields into investigation-specific metrics.
- Move notebook and investigation state to a durable managed store before multi-instance hosting.
- Establish a human-reviewed chat baseline and rating anchors before making comparative product claims.
- Confirm final product-name availability.

**Immediate next step:** Review the measured eval result, then authorize the gated Cloud Run release if a hosted endpoint is desired.
