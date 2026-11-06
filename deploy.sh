#!/usr/bin/env bash
# One-shot deploy: provision infra (as code) -> configure+deploy the Runtime ->
# grant permissions -> verify. Run from the repo root.
#
#   ./deploy.sh              # do it for real
#   ./deploy.sh --dry-run    # print the plan; run only read-only/dry-run checks
#
# Prereqs: starter/.venv exists, AWS creds in .env. Docker is NOT required
# (direct_code_deploy uploads to S3; --local would need Docker, which we don't use).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
AGENT_NAME="${AGENT_NAME:-cs_support_agent}"
DRY_RUN=0
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=1

cd "$ROOT/starter"
# shellcheck disable=SC1091
source .venv/bin/activate
set -a; source "$ROOT/.env"; set +a
export AGENTCORE_SUPPRESS_RECOMMENDATION=1

say()  { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
plan() { printf '   \033[2m$ %s\033[0m\n' "$*"; }

CONFIGURE=(agentcore configure --entrypoint main.py --name "$AGENT_NAME"
           --deployment-type direct_code_deploy --runtime PYTHON_3_13
           --disable-memory --region us-east-1 --non-interactive)

if [[ "$DRY_RUN" == "1" ]]; then
  say "DRY RUN — the following would run, in order (no resources created):"
  plan "python -m infra.provision"
  plan "${CONFIGURE[*]}"
  plan "agentcore deploy"
  plan "python setup_permissions.py        # + python -m infra.agent_permissions"
  plan "python -m infra.verify"
  say "Running the safe, read-only checks now:"
  echo "--- sts identity ---"; aws sts get-caller-identity
  echo "--- setup_permissions.py --dry-run (renders IAM policy, no changes) ---"
  python setup_permissions.py --dry-run
  echo "--- infra.verify (read-only: identity, MCP tools, KB probe, Memory) ---"
  python -m infra.verify || true
  say "Dry run complete. Re-run without --dry-run to apply."
  exit 0
fi

say "1/5 Provisioning infrastructure (idempotent)"
python -m infra.provision

say "2/5 Configuring the Runtime"
"${CONFIGURE[@]}"

say "3/5 Deploying the Runtime"
agentcore deploy

say "4/5 Granting runtime-role permissions"
python setup_permissions.py
python -m infra.agent_permissions

say "5/5 Verifying"
python -m infra.verify

say "Done. Try: agentcore invoke '{\"prompt\": \"Hello\", \"customer_id\": \"CUST-123\", \"session_id\": \"s1\"}'"
