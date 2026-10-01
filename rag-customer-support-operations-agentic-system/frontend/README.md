# Tarnfield Care customer chat

**Last verified:** 1 October 2026.

**[Open Tarnfield Care — the customer chat app](http://127.0.0.1:3010)**

This is the product frontend. It runs locally on this computer; start the UI and
gateway with [Run locally](#run-locally) if needed. A public product URL is not
deployed yet.

A responsive customer chat for Tarnfield Running Co., a fictional running retailer. It helps a
customer understand a policy answer, continue a conversation and check a filed human-review
request without learning ADK's development interface. The customer frontend runs locally on
port **3010**, through a gateway on **8081**, against the existing deployed Google Agent Runtime.

The product uses fixture orders and does not execute refunds, exchanges or commerce changes.
Public customer hosting and verified customer/order access are not implemented.
Internal support operations and authenticated reviewer access are documented in
[the deployment guide](../docs/deployment.md).

## Stack and rationale

- **Next.js App Router, React and TypeScript:** application structure and a same-origin streaming proxy.
- **Tailwind CSS and shadcn-style components backed by Radix UI:** editable styling, accessible dialogs and keyboard interactions.
- **Manrope and Barlow Semi Condensed:** locally bundled fonts for a quiet, athletic Tarnfield identity.
- **Python FastAPI gateway with the existing Agent Platform SDK:** reuses managed sessions and the deployed agents without a second model orchestrator.

The thin frontend/gateway boundary reuses the agents' existing judgement, tools, managed
sessions and memory. Only Python uses Google credentials. Building this UI does not add a
second model orchestrator or change agent models and prompts.

```text
Customer browser → Next.js same-origin API → FastAPI gateway → existing Agent Runtime
                                                          → managed conversations
Reviewer → private Cloud Run Support Operations → Firestore support requests
Agent Runtime → automatic ADK instrumentation → Arize AX
```

## Run locally

From the repository root:

```bash
uv sync --locked
npm --prefix frontend ci
```

In one terminal:

```bash
make chat-gateway
```

In another:

```bash
make chat-ui
```

Open **[Tarnfield Care](http://127.0.0.1:3010)**. This exact browser origin is the local default; `localhost` is a different origin. The gateway listens on `127.0.0.1:8081`. Port 3010 avoids taking over other development apps on port 3000.

The gateway reads `deployment_metadata.json`, or `DEPLOYED_AGENT_RUNTIME_ID` from the environment. Authenticate Google Application Default Credentials before using live chat. Live questions use the deployed models and consume normal model credits.

AI tracing runs inside the agent runtime through automatic OpenInference ADK instrumentation
and exports to Arize AX. The browser receives no Arize key. This integration is deployed and
verified; changing local instrumentation later still requires an approved runtime update to
affect this chat's backend. See [the tracing setup and verification guide](../docs/observability.md).

Optional frontend settings can be copied from `.env.example` to `.env.local`. Set `CHAT_PUBLIC_ORIGIN` and the gateway's `GATEWAY_ALLOWED_ORIGINS` to matching exact origins if changing the port or hostname. Secrets must stay server-side; do not use `NEXT_PUBLIC_` for credentials.

## Customer controls and trust

- Streaming customer-facing answers, Markdown, copy action and genuine tool-progress labels; no invented reasoning display.
- A multiline composer, Enter to send, Shift+Enter for a newline, and Ctrl/Cmd+K to focus.
- New conversation creates and selects a real managed session; its first message becomes the saved title. Search filters the saved conversation list. Unsent drafts survive switching between conversations during the browser session.
- Managed conversation history tied to a signed HttpOnly browser cookie. The development signing secret survives restarts in ignored `.sessions/` storage.
- Actual policy citations are retained in answers. Links inferred from explicitly named documents are labelled **Referenced policy**; they are not represented as verified retrieved excerpts. Rich evidence is displayed only if real metadata is present. The current deployed AgentTool hides child retrieval events, so exact excerpts are usually unavailable.
- Recorded support-request references survive an incomplete answer; reopening a conversation exposes a status-check action. A request reference does not imply approval or completion.
- Stop ends display of the response. The already-dispatched agent workflow may finish, and its concurrency guard remains active. The UI checks for completion and restores the saved answer when the conversation is known; otherwise history provides recovery. It does not automatically resend an ambiguous action request.
- Explicit portfolio-demo disclosure: fictional retailer, fixture orders, no commerce execution, anonymous browser continuity rather than verified customer accounts.
- Mobile history drawer, keyboard focus containment, reduced motion and polite status announcements.

No messages or credentials are saved in browser local storage. Only the signed visitor cookie associates the browser with its server-side managed sessions.

## Failure handling and privacy

The browser establishes its visitor cookie before loading or creating conversations. The proxy
and gateway both validate origin and input boundaries. The proxy allowlists supported routes,
rejects invalid UTF-8, limits request bodies to 40,000 bytes and does not forward browser
Authorization. The gateway limits a message to 6,000 characters and prevents overlapping turns
for a visitor within its process. Those controls protect conversation continuity; they do not
verify customer identity or ownership of a fixture order.

The stream parser reports malformed events and missing terminal events. A temporary metadata
failure can be retried; an interrupted dispatched chat is recovered from saved history before
repeating an action. Default gateway limits are 30 seconds for metadata/API work and 120 seconds
for a turn. An unusually slow model response can exceed that turn limit. Stop ends the browser
stream; it does not guarantee backend cancellation, and the active-turn guard remains until
completion or timeout.

Default Arize capture hides prompts, replies, tool payloads and content-bearing error details.
Only the exact OpenInference ADK scope is exported to Arize, avoiding duplicate native model
usage. Google Cloud retains its separate native telemetry configuration. This masking is not
a retention policy or proof that every telemetry destination is content-free; native capture,
consent, deletion and access rules remain production decisions.

## Validation and current evidence

```bash
npm --prefix frontend run typecheck
npm --prefix frontend test
npm --prefix frontend run build
env ARIZE_ENABLED=false TEST_SERVER_PORT=8088 uv run pytest tests/unit tests/integration -q
```

The integration server defaults to port 8000; use `TEST_SERVER_PORT` when another app already owns that port. The production build uses Next.js's supported Webpack compiler because this host blocks Turbopack compiler worker sockets. This does not change the application/API architecture.

Recorded acceptance on 1 October 2026:

| Scope | Evidence |
|---|---|
| Frontend logic | 30 parser, source and API-proxy checks passed |
| Frontend packaging | TypeScript, production build and production dependency audit passed; audit reported no vulnerabilities at that time |
| Browser interactions | New conversation from empty/populated chats, draft preservation, search reset, copy, citations, keyboard/multiline entry and mobile drawer focus checked |
| Failure/recovery | Temporary gateway outage and live Google Cloud chat/stop recovery checked |
| Readability | Main chat 16px, composer 16–17px; enabled text contrast exceeded 6:1 in the checked controls |
| Latest Python verification | 147 checks passed, one opt-in live check skipped; 58 focused trace checks included; Ruff passed |

These are dated acceptance results, not checks rerun by this prose update or a broad
accessibility certification. The Python total supersedes the earlier 88-check frontend-build
snapshot; frontend and tracing counts refer to different suites.

A normal frontend conversation was verified in Arize after the export correction: trace
`ee18fb93d34d639e485828b08573974e`, managed session `3156356794222116864`, status OK,
**10 automatic spans**, **35.14 seconds** and **11,424 tokens**, with masked model/tool content
and no native model duplicates. The assistant returned a sourced final-sale explanation and
filed no request. One successful turn does not establish p95 latency, task completion rate,
cost per conversation or general answer accuracy. Hosted Arize eval jobs and alerts remain
unconfigured; [the product plan](../BUILD_PLAN.md) separates proposed gates from this evidence.

## Product measurement and next iteration

The customer outcome to measure is a grounded answer or correctly filed review request without
repeated information. Establish task completion, citation preservation, interruption recovery,
first-token/completed-turn latency and cost across a representative set. Collect customer
confusion and reviewer overrides as labelled failure cases, then compare scoped fixes against
the baseline and holdouts. Feedback capture and these aggregate measures are proposed work.

The proposed completed-turn p95 gate is 15 seconds. The 35.14-second successful sample and a
separate 143.3-second runtime workflow show that latency remains a product risk. Genuine
progress labels improve visibility but do not demonstrate faster resolution. Repeated model
and architecture experiments belong after the current-model baseline, under an agreed budget.
Use [the cost guide](../docs/costs.md) to plan that budget; its scenarios are estimates rather
than observed per-customer bills.

## Public deployment boundary

This version runs locally and connects to the deployed backend. Before a public customer launch:

- Add verified customer identity and order ownership checks. A signed browser cookie only protects conversation continuity.
- Keep the Python gateway internal to the frontend deployment or behind service authentication.
- Set `GATEWAY_ENV=production`, a persistent `GATEWAY_COOKIE_SECRET` of at least 32 characters, and explicit HTTPS `GATEWAY_ALLOWED_ORIGINS`. Match `CHAT_PUBLIC_ORIGIN`.
- Run one gateway worker/instance until concurrent-turn coordination uses a shared store. Add per-customer usage quotas before wider traffic; current limits are not a distributed rate limiter.
- Define retention, consent and deletion for conversations/memory and native telemetry. Configure export failures, latency/cost and support-workflow alerts with owners and response steps.
- Run representative behavioural and recovery gates, then validate HTTPS hosting, monitoring and rollback before accepting customer traffic.

The production build has passed. Public hosting is a separate stage with the identity,
coordination, measurement and operational gates above; this document does not claim a public
launch. Detailed stage decisions are in [BUILD_PLAN.md](../BUILD_PLAN.md), and implementation
boundaries are in [ENGINEERING_NOTES.md](../ENGINEERING_NOTES.md).
