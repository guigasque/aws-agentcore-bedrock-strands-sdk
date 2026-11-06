"""Bedrock Knowledge Base (S3 Vectors backing) + data source + ingestion.

Idempotent: finds an existing KB/data source by name before creating. Waits for
the data source ingestion job to COMPLETE so search works immediately after.
Produces KB_ID (written to state and, by outputs.py, into config.yaml).
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from .common import NAMES, REGION, TAGS, logger, read_state, session, update_state, wait_until

EMBED_MODEL_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"


def _find_kb_by_name(client, name: str) -> str | None:
    """Return the KB id whose name matches, else None."""
    paginator = client.get_paginator("list_knowledge_bases")
    for page in paginator.paginate():
        for kb in page.get("knowledgeBaseSummaries", []):
            if kb["name"] == name:
                return kb["knowledgeBaseId"]
    return None


def ensure_knowledge_base(kb_role_arn: str, vector_index_arn: str) -> str:
    """Create the KB (S3 Vectors) if absent; return its id."""
    client = session().client("bedrock-agent")
    name = NAMES["knowledge_base"]

    kb_id = _find_kb_by_name(client, name)
    if kb_id:
        logger.info("Knowledge base exists: %s (%s)", name, kb_id)
    else:
        resp = client.create_knowledge_base(
            name=name,
            description="Customer support product catalog & policies.",
            roleArn=kb_role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {"embeddingModelArn": EMBED_MODEL_ARN},
            },
            storageConfiguration={
                "type": "S3_VECTORS",
                "s3VectorsConfiguration": {"indexArn": vector_index_arn},
            },
            tags=TAGS,
        )
        kb_id = resp["knowledgeBase"]["knowledgeBaseId"]
        logger.info("Knowledge base created: %s (%s)", name, kb_id)

    update_state(kb_id=kb_id)
    return kb_id


def ensure_data_source(kb_id: str, source_bucket_arn: str) -> str:
    """Create the S3 data source if absent; return its id."""
    client = session().client("bedrock-agent")
    name = NAMES["data_source"]

    for ds in client.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", []):
        if ds["name"] == name:
            logger.info("Data source exists: %s (%s)", name, ds["dataSourceId"])
            update_state(data_source_id=ds["dataSourceId"])
            return ds["dataSourceId"]

    resp = client.create_data_source(
        knowledgeBaseId=kb_id,
        name=name,
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {"bucketArn": source_bucket_arn},
        },
    )
    ds_id = resp["dataSource"]["dataSourceId"]
    logger.info("Data source created: %s (%s)", name, ds_id)
    update_state(data_source_id=ds_id)
    return ds_id


def ingest(kb_id: str, data_source_id: str) -> None:
    """Start an ingestion job and wait for COMPLETE."""
    client = session().client("bedrock-agent")
    job = client.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=data_source_id)
    job_id = job["ingestionJob"]["ingestionJobId"]
    logger.info("Ingestion job started: %s", job_id)

    def done() -> bool:
        status = client.get_ingestion_job(
            knowledgeBaseId=kb_id, dataSourceId=data_source_id, ingestionJobId=job_id
        )["ingestionJob"]["status"]
        if status == "FAILED":
            raise RuntimeError(f"Ingestion job {job_id} FAILED.")
        return status == "COMPLETE"

    wait_until(done, what=f"ingestion job {job_id}", timeout=600, interval=10)


def provision_kb(kb_role_arn: str, vector_index_arn: str, source_bucket_arn: str) -> str:
    """Full KB layer: KB + data source + ingestion. Return KB_ID."""
    kb_id = ensure_knowledge_base(kb_role_arn, vector_index_arn)
    ds_id = ensure_data_source(kb_id, source_bucket_arn)
    ingest(kb_id, ds_id)
    return kb_id


def delete_knowledge_base() -> None:
    """Delete the KB (and its data sources) by name."""
    client = session().client("bedrock-agent")
    name = NAMES["knowledge_base"]
    kb_id = read_state().get("kb_id") or _find_kb_by_name(client, name)
    if not kb_id:
        logger.info("Knowledge base already absent: %s", name)
        return
    try:
        for ds in client.list_data_sources(knowledgeBaseId=kb_id).get("dataSourceSummaries", []):
            client.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
        client.delete_knowledge_base(knowledgeBaseId=kb_id)
        logger.info("Knowledge base deleted: %s (%s)", name, kb_id)
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("ResourceNotFoundException",):
            raise
        logger.info("Knowledge base already absent: %s", name)
