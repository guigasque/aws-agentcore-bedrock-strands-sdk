"""S3 Vectors bucket + index for the Knowledge Base (idempotent).

S3 Vectors is used as the KB vector store instead of OpenSearch Serverless: far
simpler to provision as code and much cheaper (no always-on OCUs). The index is
sized for Amazon Titan Text Embeddings v2 (1024 dimensions, cosine distance).
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from .common import NAMES, REGION, account_id, logger, session, update_state, vector_bucket_name

EMBED_DIMENSION = 1024
DISTANCE_METRIC = "cosine"
DATA_TYPE = "float32"


def ensure_vectors() -> dict[str, str]:
    """Create the vector bucket + index if absent. Return names/ARNs (idempotent)."""
    sv = session().client("s3vectors")
    bucket = vector_bucket_name()
    index = NAMES["vector_index"]

    try:
        sv.get_vector_bucket(vectorBucketName=bucket)
        logger.info("Vector bucket exists: %s", bucket)
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("NotFoundException", "ResourceNotFoundException"):
            raise
        sv.create_vector_bucket(vectorBucketName=bucket)
        logger.info("Vector bucket created: %s", bucket)

    try:
        sv.get_index(vectorBucketName=bucket, indexName=index)
        logger.info("Vector index exists: %s", index)
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("NotFoundException", "ResourceNotFoundException"):
            raise
        sv.create_index(
            vectorBucketName=bucket,
            indexName=index,
            dataType=DATA_TYPE,
            dimension=EMBED_DIMENSION,
            distanceMetric=DISTANCE_METRIC,
        )
        logger.info("Vector index created: %s (dim=%s, %s)", index, EMBED_DIMENSION, DISTANCE_METRIC)

    acct = account_id()
    bucket_arn = f"arn:aws:s3vectors:{REGION}:{acct}:bucket/{bucket}"
    index_arn = f"{bucket_arn}/index/{index}"
    update_state(vector_bucket=bucket, vector_index=index, vector_index_arn=index_arn)
    return {"bucket": bucket, "index": index, "bucket_arn": bucket_arn, "index_arn": index_arn}


def delete_vectors() -> None:
    """Delete the vector index and bucket (idempotent)."""
    sv = session().client("s3vectors")
    bucket = vector_bucket_name()
    index = NAMES["vector_index"]
    for step in (
        lambda: sv.delete_index(vectorBucketName=bucket, indexName=index),
        lambda: sv.delete_vector_bucket(vectorBucketName=bucket),
    ):
        try:
            step()
        except ClientError as error:
            if error.response["Error"]["Code"] not in (
                "NotFoundException",
                "ResourceNotFoundException",
            ):
                raise
    logger.info("Vector bucket/index deleted (if they existed): %s", bucket)
