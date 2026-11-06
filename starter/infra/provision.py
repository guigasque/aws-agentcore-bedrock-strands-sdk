"""Provision ALL infrastructure as code, in dependency order (idempotent).

Run: python -m infra.provision

Layers (each is describe-or-create; re-running is a no-op):
  1. IAM roles            (lambda exec, gateway, KB service)
  2. Lambdas              (order-tracker, refund-processor)
  3. REST API + stage prod
  4. S3 source bucket + product_catalog.txt
  5. S3 Vectors bucket + index
  6. Knowledge Base + data source + ingestion   -> KB_ID
  7. AgentCore Gateway (NONE auth) + 2 targets   -> GATEWAY_URL
  8. AgentCore Memory + 2 strategies             -> MEMORY_ID
Then writes GATEWAY_URL/KB_ID/MEMORY_ID into config.yaml and syncs main.py literals.

The agent Runtime itself is deployed separately with `agentcore deploy` (the sanctioned
toolkit path), and its runtime-role permissions are granted with `setup_permissions.py`
(KB/Memory/Browser) + `python -m infra.agent_permissions` (Code Interpreter).
"""

from __future__ import annotations

import sys

import botocore.exceptions

from . import apigw, gateway, iam, knowledge_base, lambdas, memory, outputs, s3_catalog, s3vectors
from .common import logger, source_bucket_name, vector_bucket_name, wait_until


def _provision_knowledge_base() -> None:
    """Layers 4-6: S3 source + vectors + KB + ingestion (with role propagation wait)."""
    source = s3_catalog.ensure_source()
    vectors = s3vectors.ensure_vectors()
    kb_role = iam.ensure_kb_role(source_bucket_name(), vector_bucket_name())

    def role_ready() -> bool:
        try:
            knowledge_base.ensure_knowledge_base(kb_role, vectors["index_arn"])
            return True
        except botocore.exceptions.ClientError as error:
            message = str(error).lower()
            if "assume" in message or "accessdenied" in message:
                return False
            raise

    wait_until(role_ready, what="KB role propagation", timeout=300, interval=15)
    kb_id = knowledge_base.ensure_knowledge_base(kb_role, vectors["index_arn"])
    ds_id = knowledge_base.ensure_data_source(kb_id, source["arn"])
    knowledge_base.ingest(kb_id, ds_id)


def _provision_gateway() -> None:
    """Layers 1-3 + 7: lambda/gateway roles, Lambdas, REST API, Gateway + targets."""
    lambda_role = iam.ensure_lambda_role()
    gateway_role = iam.ensure_gateway_role()
    arns = lambdas.ensure_lambdas(lambda_role)
    api = apigw.ensure_api(arns["order_arn"])
    gw = gateway.ensure_gateway(gateway_role)
    gateway.ensure_targets(gw["gateway_id"], api["rest_api_id"], api["stage"])


def main() -> int:
    """Provision every layer, then write config and sync main.py."""
    try:
        logger.info("=== Provisioning: Knowledge Base layer ===")
        _provision_knowledge_base()
        logger.info("=== Provisioning: Gateway layer (Lambdas, API, Gateway) ===")
        _provision_gateway()
        logger.info("=== Provisioning: Memory layer ===")
        memory.ensure_memory()
        logger.info("=== Writing config + syncing main.py ===")
        outputs.apply()
        logger.info("Provisioning complete. Next: `agentcore deploy`, `python setup_permissions.py`, "
                    "`python -m infra.agent_permissions`, then `python -m infra.verify`.")
        return 0
    except Exception as error:  # surface a clear message; `infra.destroy` is available
        logger.exception("Provisioning failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
