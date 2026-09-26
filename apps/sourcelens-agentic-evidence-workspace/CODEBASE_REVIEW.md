# SourceLens codebase and product review

**Reviewed:** 26 September 2026
**Scope:** product framing, documentation, authentication, investigation workflow, observability, evaluation, deployment, frontend, tests, and operating controls.

## Release outcome

The first two release actions from this review are complete. `README.md` now describes the current one-user product and direct Google-session architecture. Cloud Run revision `sourcelens-00010-47l` is serving 100% of traffic. The Galileo secret was replaced with the locally validated credential, and the deployed health check now reports `galileo: true`. The remaining findings below are retained as the review baseline and prioritized follow-up work.

## Verdict

The implementation still supports the central product idea: a user asks a business question, SourceLens performs bounded analysis, keeps calculations and citations deterministic, uses distinct agent roles for judgment, exposes evidence, and lets the user review the result before saving it.

The code is currently more advanced than the documentation. The repository does not yet provide one reliable account of what is local, what is deployed, what is working, and what is planned. Authentication documentation still describes Firebase browser sessions; the current frontend uses Google Identity Services and exchanges the Google credential for a revocable SourceLens session in an HttpOnly cookie. The product is currently restricted to one allowed email, while the storage model retains owner isolation so multiple accounts can be supported later.

The current release candidate should not be described as fully production-ready yet. Galileo is now configured and enabled in the deployed service, but an end-to-end trace arrival test remains. Investigations run in FastAPI background tasks and can be lost if the Cloud Run instance stops. The historical eval artifacts are valid records of the 24 September run, but the current eval runner has drifted from the owner-aware persistence API and is not runnable as written.

## What is aligned with the latest product thinking

- The current deployment and local configuration use `gpt-5.6-sol` for the Research Planner, Evidence Analyst, and Lead Investigator.
- SQL execution, arithmetic, evidence IDs, notebook snapshots, source ownership, and cost accounting remain outside model control.
- BigQuery connections are verified before being saved and are presented as a three-step, read-only setup flow.
- Google identity is verified server-side and exchanged for an opaque, revocable application session. Mutating cookie-authenticated requests require a CSRF token.
- The deployed app is restricted to the owner email. The data model still applies owner filters throughout the API, which preserves a credible path to multiple users without presenting the current app as multi-user.
- One Galileo session can contain multiple workflow traces, and each workflow trace contains the three role spans. Token counts, estimated model cost, and model-call duration are also stored in Cloud SQL.
- The frontend now presents a focused chat workspace and keeps sources, investigations, and reviewed notebook entries as distinct product concepts.

## Findings

### P0 — correct before the next deployment

1. **Galileo credential issue — resolved in revision `00010`.** At review time, the deployed health endpoint reported `galileo: false` and Cloud Run logs showed HTTP 401 `Invalid credentials`. The secret has since been replaced and initialization succeeds. One live investigation should still confirm that the session, trace, and all role spans arrive correctly.

2. **Authentication documentation is materially stale.** `README.md`, `AUTH_PLAN.md`, `GOOGLE_SERV.md`, and `LOG.md` describe Firebase Web SDK tokens on every request. The current browser uses Google Identity Services once, then relies on a SourceLens session cookie. The deployment script and backend still contain legacy Firebase configuration and bearer-token support, which obscures the intended architecture.

3. **Deployment documentation was stale — README corrected.** The review found documentation naming revision `sourcelens-00008-jd8` while Cloud Run served `sourcelens-00009-vht`. The current release is `sourcelens-00010-47l`, with the direct Google-session authentication and redesigned frontend deployed.

4. **The eval runner has drifted from the application API.** `evals/run_portfolio_eval.py` still constructs `AppStore` from a SQLite path and calls investigation persistence methods without an owner. The current store requires a SQLAlchemy engine and owner-scoped operations. The two JSON eval reports should be labelled historical results until the runner is repaired and rerun.

5. **The documents disagree on the product boundary.** `AUTH_PLAN.md` frames a multi-user product; the latest decision is a one-user product with production-quality authentication. The correct statement is: one allowed account today, tenant-aware storage and authorization retained for future expansion.

### P1 — address as product hardening

6. **Investigation execution is not durable.** `BackgroundTasks` runs the workflow inside the web process. A Cloud Run restart, timeout, or scale-down can leave an investigation in `running` without automatic resume. The old build plan mentions Cloud Tasks, but that architecture was not implemented.

7. **Usage tracking exists but is not yet a product surface.** Cloud SQL records input tokens, output tokens, estimated cost, and duration for successful role calls. The app has no owner-facing run summary or operational dashboard. The public health endpoint exposes aggregate run count and spend, which would be better moved behind authentication.

8. **Budget enforcement is coarse.** The budget and run count are global. The run ceiling counts distinct investigations, so refinements do not consume another run slot. Cost is checked before each call but can overshoot by the final call, and failed provider calls may incur cost without reaching the ledger.

9. **Legacy Firebase support should be retired after migration confidence.** `firebase-admin`, Firebase configuration, the `firebase_uid` field name, IAM roles, bearer-token tests, and a second authentication path remain. For a one-user application, this adds maintenance and security surface without current user value.

10. **The production observability test is too shallow.** Unit tests verify processor registration and trace grouping through mocks. The live model test checks database usage rows, but it does not verify that a Galileo session, top-level trace, and all role spans arrived in Galileo.

11. **Failure handling needs an operator path.** Errors are persisted on the investigation, but there is no retry/resume action, stale-run recovery, or alert when an investigation fails after the API has returned `202`.

### P2 — simplify or defer deliberately

12. **Qdrant is currently disabled in production.** Local lexical retrieval preserves the demo workflow. For the current one-user, small-dataset product, Qdrant should remain an explicit optional enhancement unless retrieval quality measurements show that it is needed.

13. **Documentation has become too fragmented.** Six substantial Markdown documents repeat architecture, status, costs, and next steps. Historical decisions and current operating truth are mixed together, which caused most of the contradictions in this review.

14. **Frontend verification is mostly functional.** Seven component tests and the TypeScript production build pass. Responsive layout and visual quality are currently checked manually; there is no repeatable browser smoke suite for sign-in, source connection, investigation completion, evidence inspection, and notebook review.

## Documentation reset

Use each document for one job:

| Document | Purpose | Required change |
| --- | --- | --- |
| `README.md` | Current product, architecture, setup, validation, and known boundaries | Rewrite around Google sessions, one-user access, Sol-only roles, actual local/deployed split, and current observability status. |
| `PRODUCT.md` | User, problem, value, experience, success measures, and product risks | Preserve the strong product thesis; update the modern chat experience, one-user scope, and measurable success metrics. |
| `BUILD.md` | As-built system and prioritized next work | Remove obsolete statements that auth/Cloud SQL/hosting are future work. Separate implemented architecture from later scale architecture. |
| `AUTH_PLAN.md` | Authentication decision record | Mark the Firebase plan as superseded and document Google Identity Services → SourceLens session → CSRF-protected API. |
| `GOOGLE_SERV.md` | Operations runbook | Update Cloud Run revision, live-agent state, secrets, Google OAuth origins, Cloud Storage layout, Galileo failure, and rollback steps. |
| `LOG.md` | Append-only development history | Keep the 24 September entry as history and add a 26 September entry describing the auth replacement, observability work, model switch, and frontend redesign. Do not leave old open items presented as current. |
| `.agents-cli-spec.md` | Compact machine-readable build context | Replace SQLite/future-hosting assumptions with the current stack and boundaries. |

Create one small `STATUS.md` as the canonical release snapshot: local candidate, deployed revision, enabled integrations, last test result, last live-model result, known blockers, and next deployment gate. Other documents should link to it instead of copying volatile status.

## Recommended update sequence

1. Repair the Galileo credential, run one bounded live investigation, and verify the session, workflow trace, three role spans, token counts, latency, and cost in both Galileo and Cloud SQL.
2. Repair the eval runner for owner-aware persistence, add a small regression set, and rerun it before making new quality claims.
3. Rewrite the documentation around a single current-state narrative and add `STATUS.md`.
4. Remove the legacy Firebase request path, dependency, configuration, IAM role, and tests after confirming existing Google users retain their records.
5. Add an authenticated run summary showing total latency, per-role latency, tokens, and estimated cost. Remove spend information from the public health response.
6. Add durable workflow execution or, for the portfolio release, implement stale-run detection plus a safe retry action and document the limitation explicitly.
7. Add one browser smoke test covering sign-in, a source, a real investigation, evidence, and notebook review.
8. Perform the deployment review only after the user approves the local frontend and documentation candidate.

## Validation performed

- Python lint: passed.
- Frontend component tests: 7 passed.
- Frontend TypeScript and production build: passed.
- Full backend integration suite against Cloud SQL, BigQuery, and Cloud Storage: 40 passed, 1 paid live-model test skipped, 1 local-Qdrant warning; completed in 9m 7s.
- Cloud Run post-release inspection: revision `sourcelens-00010-47l`, 100% traffic, live Sol enabled, OpenAI and Galileo secrets attached.
- Live health check: service and database healthy; BigQuery, live agent, authentication, and Galileo enabled; Qdrant disabled; two metered investigations; approximately $0.0472 recorded model cost.
- Authentication boundary check: `/api/me` returns 401 without a valid SourceLens session.
