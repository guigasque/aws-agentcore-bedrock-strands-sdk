"""Tear down ALL infrastructure as code, in reverse dependency order (idempotent).

Run: python -m infra.destroy

Deletes (safe if a resource is already gone or was never created):
  Memory -> Gateway (+targets) -> Knowledge Base (+data source) -> S3 Vectors ->
  S3 source bucket -> REST API -> Lambdas -> IAM roles -> runtime-role add-on policy.

Note: the agent Runtime is torn down separately with `agentcore destroy` (the toolkit
owns it). This script removes everything `infra.provision` created. The runtime
execution role itself is managed by the toolkit; we only remove the inline policy
that `infra.agent_permissions` added to it.
"""

from __future__ import annotations

import sys

from . import (
    agent_permissions,
    apigw,
    gateway,
    iam,
    knowledge_base,
    lambdas,
    memory,
    s3_catalog,
    s3vectors,
)
from .common import logger


def _safe(step, label: str) -> None:
    """Run a teardown step, logging and swallowing errors so destroy always proceeds."""
    try:
        step()
    except Exception as error:  # noqa: BLE001 - destroy must be best-effort and complete
        logger.warning("Teardown step '%s' had an issue (%s); continuing.", label, error)


def main() -> int:
    """Delete every provisioned resource in reverse order (best-effort)."""
    logger.info("=== Teardown (reverse order) ===")
    _safe(memory.delete_memory, "memory")
    _safe(gateway.delete_gateway, "gateway + targets")
    _safe(knowledge_base.delete_knowledge_base, "knowledge base + data source")
    _safe(s3vectors.delete_vectors, "s3 vectors bucket + index")
    _safe(s3_catalog.delete_source, "s3 source bucket")
    _safe(apigw.delete_api, "rest api")
    _safe(lambdas.delete_lambdas, "lambdas")
    _safe(iam.delete_gateway_role, "gateway role")
    _safe(iam.delete_lambda_role, "lambda role")
    _safe(iam.delete_kb_role, "kb role")
    # Remove the inline policy we added to the toolkit-owned runtime role (if present).
    _safe(agent_permissions.delete, "runtime-role add-on policy")
    logger.info("Teardown complete. Also run `agentcore destroy` to remove the agent Runtime.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
