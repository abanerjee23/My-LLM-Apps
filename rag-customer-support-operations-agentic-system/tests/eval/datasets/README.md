# Tarnfield evaluation datasets

This directory contains synthetic behavioural cases for the existing ADK agent.
The current runner is `agents-cli`; scoring uses the local Gemini judge in
[`response_quality.py`](../response_quality.py), selected by
[`eval_config.yaml`](../eval_config.yaml). Arize AX observes automatic ADK
execution when enabled. Hosted Arize evaluators are not configured by this repository.

See the [evaluation guide](../../../docs/evaluation.md) for commands, dated
evidence, coverage gaps and grading limitations. The
[observability guide](../../../docs/observability.md) explains the Galileo →
Arize AI migration; the [main README](../../../README.md) covers setup.

## Current cases

[`basic-dataset.json`](basic-dataset.json) contains two single-turn cases:

| Case ID | User journey | Expected behaviour |
|---|---|---|
| `final_sale_is_refused_not_filed` | Return Solstice Edition trainers, order `TF-88213` | Apply the catalogue override, cite it and avoid claiming a return was arranged. |
| `faulty_exchange_creates_pending_handoff` | Faulty trainers, order `TF-88455`, with explicit filing permission | Check policy, give the filed `REQ-` reference and state that human approval is still required. |

Both scored 5/5 in the saved 13 September 2026 grading result. This is a small
development baseline, not a holdout suite or broad quality evidence. Orders
come from `app/data/fixtures.json`; date arithmetic uses the fixed fixture date,
12 September 2026.

## Run the dataset

Run from the repository root with model credentials, ADC and a working corpus.
Generation and grading both spend model credits. The exchange case can file a
request, so this command selects the local in-memory support-action store:

```bash
env -u GOOGLE_CLOUD_AGENT_ENGINE_ID -u K_SERVICE -u SESSION_SERVICE_URI \
  SESSION_BACKEND=memory SUPPORT_ACTION_BACKEND=memory \
  GOOGLE_CLOUD_AGENT_ENGINE_ENABLE_TELEMETRY=false ARIZE_ENABLED=false \
  agents-cli eval run \
  --dataset tests/eval/datasets/basic-dataset.json \
  --config tests/eval/eval_config.yaml \
  --metrics custom_response_quality --concurrency 1 --qps 1
```

This checks response behaviour and logical filing, not Firestore durability.
The overrides apply to a newly started CLI server, not a reused server or
`--url` target. Use a fresh development server for the local baseline.
The CLI creates fresh sessions but uses one `eval-cli-user` identity, so memory
can carry between cases within a run. Keep `.env.local` limited to local secrets
rather than overrides of these isolation settings. See
[run a local baseline](../../../docs/evaluation.md#run-a-local-baseline) for prerequisites.

Use the guide's two-step commands to preserve an explicit trace file. A bare
`eval grade` reads every JSON file in the default trace directory, including
historical runs. Specify `--traces` when re-grading.

## Author a case

An inference dataset has an `eval_cases` array. Keep IDs unique and stable.
A single-turn case has a user `prompt` and an outcome-based reference:

```json
{
  "eval_cases": [{
    "eval_case_id": "final_sale_example",
    "prompt": {
      "role": "user",
      "parts": [{"text": "Can I return the Solstice Edition trainers from TF-88213?"}]
    },
    "reference": {
      "response": {
        "role": "model",
        "parts": [{"text": "Apply the catalogue final-sale restriction, cite it, and do not file or claim a completed return."}]
      }
    }
  }]
}
```

`reference.response` wraps a model `Content`; it is not a bare string.
Generated responses belong in trace output, not inference input. References
should describe correct policy outcomes rather than require exact phrasing.

For a continued conversation, use `agent_data.turns` with sequential
`turn_index` values and a final user event, instead of a top-level `prompt`.
Assistant content uses `role: "model"`; tools use `function_call` and
`function_response` parts. The current dataset has no multi-turn or cross-session cases.
See the [CLI evaluation guide](https://google.github.io/agents-cli/guide/evaluation/)
for the complete schema and check installed `agents-cli eval generate --help`.

## Extend coverage safely

Add billing, standard returns, policy gaps, unavailable retrieval, injection,
memory conflicts and action failures before claiming release coverage. Reserve
holdouts and repeat runs; see the
[coverage plan](../../../docs/evaluation.md#coverage-and-release-gaps).

Use synthetic identities and contact details. Local JSON/HTML reports contain
full prompts, replies, tool payloads and agent instructions. Arize's default
redaction does not redact these files or the judge input. `artifacts/` is ignored
by Git; review reports before sharing, and keep customer transcripts and secrets
out of datasets.
