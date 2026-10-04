#!/usr/bin/env bash
# Warn when the Vertex AI RAG Engine tier is still provisioned and billing.
#
# Wired to the Stop hook in .claude/settings.json. Stop fires after every
# assistant turn, so this must be cheap:
#   * throttled -- the real check runs at most once per THROTTLE_SECONDS
#   * async in the hook config -- it never blocks the turn ending
#   * always exits 0 -- a cost reminder must never break the session
#
# Why this exists: the RAG Engine billing tier is a project+location singleton,
# not a property of the corpus. `make rag-down` deletes the corpus and leaves
# the meter running. See docs/build-plan.md 2.5.
#
# Run directly with --force to check now, ignoring the throttle.

set -uo pipefail

THROTTLE_SECONDS="${RAG_COST_CHECK_THROTTLE:-1800}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARKER="$ROOT/.claude/.rag-tier-last-check"

mtime() {
  stat -f %m "$1" 2>/dev/null || stat -c %Y "$1" 2>/dev/null || echo 0
}

if [ "${1:-}" != "--force" ] && [ -f "$MARKER" ]; then
  age=$(( $(date +%s) - $(mtime "$MARKER") ))
  [ "$age" -lt "$THROTTLE_SECONDS" ] && exit 0
fi
mkdir -p "$ROOT/.claude" && touch "$MARKER"

status="$(cd "$ROOT" && uv run python scripts/rag_corpus.py status 2>/dev/null)" || exit 0

tier="$(printf '%s\n' "$status" | awk -F': *' '/^Tier/ {print $2; exit}')"
location="$(printf '%s\n' "$status" | awk -F': *' '/^Location/ {print $2; exit}')"

# Unprovisioned is the goal state. Unknown means we could not reach the API --
# say nothing rather than cry wolf.
case "$tier" in
  Unprovisioned|""|unknown*) exit 0 ;;
esac

if printf '%s\n' "$status" | grep -q "does not exist"; then
  detail="no corpus exists, and the tier bills anyway"
else
  detail="corpus is up"
fi

python3 - "$tier" "$location" "$detail" <<'PY'
import json, sys
tier, location, detail = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({"systemMessage":
    f"RAG Engine is still billing: tier {tier} in {location} ({detail}). "
    f"Run `make rag-unprovision` when you are done for a while — "
    f"`make rag-down` does not stop the meter."}))
PY
