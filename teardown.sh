#!/usr/bin/env bash
# One-shot teardown: destroy the Runtime -> destroy all infra (reverse order).
# Run from the repo root.
#
#   ./teardown.sh              # tear everything down
#   ./teardown.sh --dry-run    # print what WOULD be deleted; delete nothing
#
# Both steps are idempotent and safe to re-run if something was already gone.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

cd "$ROOT/starter"
# shellcheck disable=SC1091
source .venv/bin/activate
set -a; source "$ROOT/.env"; set +a
export AGENTCORE_SUPPRESS_RECOMMENDATION=1

say()  { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }
plan() { printf '   \033[2m$ %s\033[0m\n' "$*"; }

if [[ "$DRY_RUN" == "1" ]]; then
  say "DRY RUN — teardown would run, in order (nothing deleted):"
  plan "agentcore destroy        # removes the AgentCore Runtime"
  plan "python -m infra.destroy  # Memory, Gateway+targets, KB, S3 Vectors, S3,"
  plan "                         # REST API, Lambdas, IAM roles, runtime add-on policy"
  say "Resources that currently exist (from infra/state.json):"
  if [[ -f infra/state.json ]]; then
    python -c "import json;s=json.load(open('infra/state.json'));[print(f'   - {k}: {v}') for k,v in s.items()]"
  else
    echo "   (no state.json found)"
  fi
  say "Dry run complete. Re-run without --dry-run to delete."
  exit 0
fi

say "1/2 Destroying the Runtime (agentcore destroy)"
agentcore destroy || echo "   (runtime already gone or destroy skipped)"

say "2/2 Destroying infrastructure (reverse order, best-effort)"
python -m infra.destroy

say "Teardown complete. Verify in the console or with: aws s3 ls | grep cs-support"
