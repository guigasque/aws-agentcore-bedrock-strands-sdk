#!/usr/bin/env bash
# Package the project submission into submission.zip (reproducible, no secrets).
#
# Includes: completed starter/ (code, infra, config, prompt, lambdas), evidence/
# (six test screenshots + reflection), docs/, DEPLOYMENT.md, the spec, and READMEs.
# Excludes: secrets (.env), the virtualenv, build artifacts, caches, git internals,
# the toolkit's local state dir, and personal notes.
set -euo pipefail

cd "$(dirname "$0")"
OUT="submission.zip"
rm -f "$OUT"

zip -r "$OUT" \
  starter \
  evidence \
  docs \
  .kiro \
  DEPLOYMENT.md \
  README.md \
  LICENSE.txt \
  package_submission.sh \
  deploy.sh \
  teardown.sh \
  -x '*/.venv/*' \
  -x '*/.ruff_cache/*' \
  -x '*/__pycache__/*' \
  -x '*.pyc' \
  -x '*/build/*' \
  -x '*.egg-info/*' \
  -x 'starter/.bedrock_agentcore/*' \
  -x '*/.DS_Store' \
  -x '.DS_Store' \
  -x '*/.env' \
  -x '.env'

echo
echo "Created $OUT:"
unzip -l "$OUT" | tail -n +2 | awk 'NR>0{print}' | sed 's/^/  /'
