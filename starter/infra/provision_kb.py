"""Provision ONLY the Knowledge Base layer (IAM + S3 source + S3 Vectors + KB + ingest).

Run: python -m infra.provision_kb
Idempotent; safe to re-run. Writes KB_ID into config.yaml and syncs main.py.
This is a focused subset of the full infra.provision (added in Wave I) so RAG can
be tested as soon as Wave 2 lands.
"""

from __future__ import annotations

import sys

import botocore.exceptions

from . import iam, knowledge_base, outputs, s3_catalog, s3vectors
from .common import logger, source_bucket_name, vector_bucket_name, wait_until


def main() -> int:
    """Provision the KB layer end to end."""
    try:
        source = s3_catalog.ensure_source()
        vectors = s3vectors.ensure_vectors()
        kb_role_arn = iam.ensure_kb_role(source_bucket_name(), vector_bucket_name())

        # IAM can take 10-60s to propagate before Bedrock can assume a new role.
        def role_ready() -> bool:
            try:
                knowledge_base.ensure_knowledge_base(kb_role_arn, vectors["index_arn"])
                return True
            except botocore.exceptions.ClientError as error:
                message = str(error).lower()
                if "assume" in message or "accessdenied" in message:
                    return False
                raise

        wait_until(role_ready, what="KB role propagation", timeout=300, interval=15)

        kb_id = knowledge_base.ensure_knowledge_base(kb_role_arn, vectors["index_arn"])
        ds_id = knowledge_base.ensure_data_source(kb_id, source["arn"])
        knowledge_base.ingest(kb_id, ds_id)

        outputs.apply()
        logger.info("KB provisioning complete. KB_ID=%s", kb_id)
        return 0
    except Exception as error:  # surface a clear message; destroy is available
        logger.exception("KB provisioning failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
