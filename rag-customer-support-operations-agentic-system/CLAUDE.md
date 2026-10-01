# Coding Agent Guide

## Prerequisites

Install the CLI (one-time):
```bash
uv tool install google-agents-cli
```

---

## Development Phases

### Phase 1: Understand Requirements
Before writing any code, understand the project's requirements, constraints, and success criteria.

### Phase 2: Build and Implement
Implement agent logic in `app/`. Use `make playground` for interactive agent testing with the configured SQLite session and local memory services. Use `make chat-gateway` and `make chat-ui` for the customer frontend connected to the deployed runtime. Iterate based on user feedback; preserve the existing models unless a model change is requested.

### Phase 3: The Evaluation Loop (Main Iteration Phase)
Use [the project evaluation guide](docs/evaluation.md). The existing two-case baseline uses `agents-cli` and the custom Gemini judge; Arize AX receives automatic ADK traces. Hosted Arize evaluators are not configured. Agree measurable acceptance criteria, a run budget and held-out cases before a new experiment. Bare `make eval` does not isolate Firestore, and an already registered ADK server can bypass new environment overrides; use the guide's controlled-server baseline. Inspect per-case errors and judge rationales, and compare saved result files after a change. Do not interpret two high scores as broad product quality. Run prompt optimisation only when explicitly requested.

### Phase 4: Pre-Deployment Tests
Run `uv run pytest tests/unit tests/integration`. Fix issues until all tests pass.

### Phase 5: Deploy to Dev
**Requires explicit human approval.** Run `agents-cli deploy` only after user confirms. See [the deployment guide](docs/deployment.md) for details.

### Phase 6: Production Deployment
Follow [the current deployment runbook](docs/deployment.md). The existing Agent Runtime and private Cloud Run dashboard are already deployed; documentation changes do not require redeployment. Preserve the runtime ID and both secret bindings during approved updates. Existing manually provisioned resources must be reconciled/imported before adopting Terraform. CI/CD and a public customer frontend remain future work.

## Development Commands

| Command | Purpose |
|---------|---------|
| `make playground` | Interactive local agent testing with session and memory services |
| `make chat-gateway` / `make chat-ui` | Customer chat against the deployed runtime |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios for your agent |
| `make eval` | Corpus preflight, then generate and grade the eval dataset |
| `uv run python scripts/arize_trace_smoke.py` | Verify automatic Arize transport without a model bill |
| `agents-cli eval generate` / `agents-cli eval grade` | Decoupled form: produce traces, then grade them |
| `agents-cli eval compare` | Compare two grade-results files (regression check) |
| `agents-cli eval analyze` | Cluster failure modes from grade results |
| `agents-cli eval metric list` | List built-in metrics available in the SDK |
| `agents-cli eval optimize` | Auto-tune agent prompts using eval data |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Apply Terraform only after reconciling existing resource ownership |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` | Add deployment target or CI/CD to project |
| `agents-cli scaffold upgrade` | Upgrade project to latest version |

---

## Operational Guidelines for Coding Agents

- **Code preservation**: Only modify code directly targeted by the user's request. Preserve all surrounding code, config values (e.g., `model`), comments, and formatting.
- **NEVER change the model** unless explicitly asked.
- **Model 404 errors**: Fix `GOOGLE_CLOUD_LOCATION` (e.g., `global` instead of `us-east1`), not the model name.
- **ADK tool imports**: Import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`
- **Run Python with `uv`**: `uv run python script.py`. Run `agents-cli install` first.
- **Stop on repeated errors**: If the same error appears 3+ times, fix the root cause instead of retrying.
- **Terraform conflicts** (Error 409): Use `terraform import` instead of retrying creation.
- **Product documentation**: README follows User → Problem → Why AI → Success criteria → UX → System design → Evaluation → Observability → Iteration → Safety → Production/ops → Portfolio evidence. Separate measured outcomes from proposed targets and implemented capabilities from roadmap work.
- **Observability**: Use the existing automatic Arize AX integration. Do not add manual traces around model/tool calls or replace the shared Cloud tracer provider. Keep secrets server-side and content capture off unless explicitly selected for a controlled workflow.
