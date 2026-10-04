# Single policy agent — build evidence

4 October 2026. Local implementation, not a production release.

## User → problem → outcome

Customers need clear returns/exchanges guidance with inspectable policy support.
The source now uses one Gemini 3.8 Flash agent after fresh retrieval, instead of
serial routing and specialist model calls. It cannot perform commerce actions.

## What changed

| Decision | User value / constraint |
|---|---|
| One agent, pre-generation retrieval | Avoid serial model delegation and tool-selection calls |
| Returns policy + catalogue whitelist | Product exceptions remain available; billing is excluded |
| Schema and current-turn citation validation | Reject malformed answers and invented source IDs |
| Local scope/action redirects | Known unsupported requests need no model call |
| Managed sessions and selective Memory Bank | Keep history; recall order/item/issue without saving contact/payment details into memory |
| Memory latency budgets | Optional recall/write failure does not block policy help |
| No Firestore workflow or reviewer application | No simulated action promises or action-status UI |

Source-level code, direct Firestore dependency, Terraform definitions, billing
fixtures and redundant PDFs were removed. Git preserves recoverable history.
No deployment, Terraform apply, corpus deletion or live resource retirement ran.
The existing corpus may still contain legacy documents; retrieval filters them.

## Verification

| Check | Result |
|---|---|
| Backend unit/integration tests | 130 passed, 1 skipped |
| Frontend tests | 30 passed |
| TypeScript and production UI build | Passed |
| Python lint | Passed |
| Final-sale live smoke | Cited answer; one response, 5.7s, 3,893 total tokens |
| Faulty-exchange live smoke | Cited answer; one response, 4.4s, 3,949 total tokens |
| Billing/action live smoke | Helpful redirects; zero model responses |
| Local cross-session memory smoke | Recalled sample order and fault in a separate conversation |
| Customer UI / managed sessions | Live cited answer, inspectable excerpt/PDF and restored citations after reload |

Times are two standalone local smoke turns, including real retrieval. They are
not p95, matched baseline comparisons, semantic grounding scores or cost proofs.
The smoke caught and corrected three SDK integration issues: tool-wrapped output
and unsupported JSON Schema fields, plus a late memory-injection handoff. Native structured output now uses a
Gemini-compatible schema; stricter validation remains in application code.

## Trust boundaries and remaining work

Citation identity is checked; entailment needs evals. Scope rules are conservative
English patterns plus a model prompt, not a complete semantic firewall. Conflicts,
mixed intents, injection and misleading citations require held-out cases.

Session history preserves messages. Memory writes only selected details, but
existing legacy session/memory data may contain older content. No old data was
purged. New managed-path recall/isolation and trace privacy still need verification.
Local SQLite/in-process memory is a development option, not managed-memory proof.

Evals are next: rubric → human-scored good/poor/borderline answers → judge
calibration and separate holdout → scoring fresh agent outputs. Public deployment
also requires consent/deletion/retention, identity, abuse limits, monitoring,
representative latency/cost measurements and an approved release/rollback plan.
