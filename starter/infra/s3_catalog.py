"""Source-documents S3 bucket + product_catalog.txt upload (idempotent)."""

from __future__ import annotations

from pathlib import Path

from botocore.exceptions import ClientError

from .common import REGION, TAGS, logger, session, source_bucket_name, update_state

CATALOG_KEY = "product_catalog.txt"


def ensure_bucket(name: str) -> None:
    """Create the bucket if absent. us-east-1 needs no LocationConstraint."""
    s3 = session().client("s3")
    try:
        s3.head_bucket(Bucket=name)
        logger.info("Source bucket exists: %s", name)
        return
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("404", "NoSuchBucket", "403"):
            raise
    if REGION == "us-east-1":
        s3.create_bucket(Bucket=name)
    else:
        s3.create_bucket(
            Bucket=name,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
    s3.put_bucket_tagging(
        Bucket=name,
        Tagging={"TagSet": [{"Key": k, "Value": v} for k, v in TAGS.items()]},
    )
    logger.info("Source bucket created: %s", name)


def ensure_source() -> dict[str, str]:
    """Create the source bucket and upload the catalog. Return bucket/key/arn."""
    name = source_bucket_name()
    ensure_bucket(name)
    catalog = Path(__file__).resolve().parent.parent / "product_catalog.txt"
    session().client("s3").upload_file(str(catalog), name, CATALOG_KEY)
    logger.info("Uploaded %s to s3://%s/%s", catalog.name, name, CATALOG_KEY)
    arn = f"arn:aws:s3:::{name}"
    update_state(source_bucket=name, source_bucket_arn=arn, catalog_key=CATALOG_KEY)
    return {"bucket": name, "key": CATALOG_KEY, "arn": arn}


def delete_source() -> None:
    """Empty and delete the source bucket."""
    s3 = session().client("s3")
    name = source_bucket_name()
    try:
        objs = s3.list_objects_v2(Bucket=name).get("Contents", [])
        for obj in objs:
            s3.delete_object(Bucket=name, Key=obj["Key"])
        s3.delete_bucket(Bucket=name)
        logger.info("Source bucket deleted: %s", name)
    except ClientError as error:
        if error.response["Error"]["Code"] not in ("404", "NoSuchBucket"):
            raise
        logger.info("Source bucket already absent: %s", name)
