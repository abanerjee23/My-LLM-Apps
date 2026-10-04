# Tarnfield Care customer preview

## User → problem → experience

A customer asks about returns/exchanges and receives a sourced policy explanation.
The UI offers policy links, conversation history and helpful uncertainty, without
claiming to execute refunds, exchanges or support requests.

Open [Tarnfield Care](http://127.0.0.1:3010). This is a local portfolio preview,
not a public customer release.

## Architecture

Next.js same-origin proxy → FastAPI gateway → current single-agent source.
The gateway defaults to managed Google sessions and Memory Bank; RAG also uses
Google Cloud. It does not invoke the old deployed agent's model workflow.
Credentials remain server-side.

Answers are buffered until the schema and current-turn citation IDs are
validated. The interface receives status/answer/source events via SSE. Source
dialogs show retrieved excerpts and original PDFs. Older historical answers may
show “Referenced policy” rather than imply their evidence was validated anew.

## Run locally

Install dependencies with `uv sync --locked` and `npm --prefix frontend ci`.
Run `make chat-gateway` and `make chat-ui` in separate terminals.
Use the exact origin `http://127.0.0.1:3010`; localhost is a different origin.

Default managed state reads `deployment_metadata.json` or
`DEPLOYED_AGENT_RUNTIME_ID`. Google Application Default Credentials and the
Gemini API key must be configured. Live questions consume model credits.
Set `CHAT_STATE_BACKEND=local` for standalone SQLite sessions and in-process
memory; local memory does not survive restart.

Secrets go in ignored local overrides, never browser-visible variables.
Match `CHAT_PUBLIC_ORIGIN` and `GATEWAY_ALLOWED_ORIGINS` when changing origin.

## Verification and release

30 frontend tests, TypeScript checks and the production build passed on
4 October 2026. See [build report](build-report.md) for end-to-end evidence.

Public hosting still requires identity/ownership decisions, privacy controls,
distributed request coordination, abuse limits, calibrated quality evaluations,
monitoring and approved deployment. No live cloud resources were retired.
