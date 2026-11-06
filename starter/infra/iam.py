"""IAM roles for the infrastructure (idempotent describe-or-create).

Currently provides the Bedrock Knowledge Base service role, which Bedrock assumes
to read the source S3 bucket, write/read the S3 vector index, and invoke the
embedding model. Lambda execution roles are added in a later layer.
"""

from __future__ import annotations

import json

from botocore.exceptions import ClientError

from .common import NAMES, REGION, TAGS, account_id, logger, session, update_state

_KB_TRUST = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}


def _kb_policy(source_bucket: str, vector_bucket: str, acct: str) -> dict:
    """Least-privilege policy for the KB service role."""
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "ReadSourceBucket",
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{source_bucket}",
                    f"arn:aws:s3:::{source_bucket}/*",
                ],
            },
            {
                "Sid": "S3Vectors",
                "Effect": "Allow",
                "Action": ["s3vectors:*"],
                "Resource": [
                    f"arn:aws:s3vectors:{REGION}:{acct}:bucket/{vector_bucket}",
                    f"arn:aws:s3vectors:{REGION}:{acct}:bucket/{vector_bucket}/*",
                ],
            },
            {
                "Sid": "InvokeEmbeddingModel",
                "Effect": "Allow",
                "Action": ["bedrock:InvokeModel"],
                "Resource": [f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"],
            },
        ],
    }


def ensure_kb_role(source_bucket: str, vector_bucket: str) -> str:
    """Create or update the KB service role; return its ARN (idempotent)."""
    iam = session().client("iam")
    name = NAMES["kb_role"]
    acct = account_id()

    try:
        role = iam.get_role(RoleName=name)["Role"]
        logger.info("KB role exists: %s", role["Arn"])
    except ClientError as error:
        if error.response["Error"]["Code"] != "NoSuchEntity":
            raise
        role = iam.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(_KB_TRUST),
            Description="Bedrock Knowledge Base service role (cs-support)",
            Tags=[{"Key": k, "Value": v} for k, v in TAGS.items()],
        )["Role"]
        logger.info("KB role created: %s", role["Arn"])

    # Inline policy (idempotent put).
    iam.put_role_policy(
        RoleName=name,
        PolicyName="cs-support-kb-access",
        PolicyDocument=json.dumps(_kb_policy(source_bucket, vector_bucket, acct)),
    )
    update_state(kb_role_arn=role["Arn"], kb_role_name=name)
    return role["Arn"]


def delete_kb_role() -> None:
    """Delete the KB service role and its inline policy (idempotent)."""
    iam = session().client("iam")
    name = NAMES["kb_role"]
    for step in (
        lambda: iam.delete_role_policy(RoleName=name, PolicyName="cs-support-kb-access"),
        lambda: iam.delete_role(RoleName=name),
    ):
        try:
            step()
        except ClientError as error:
            if error.response["Error"]["Code"] not in ("NoSuchEntity",):
                raise
    logger.info("KB role deleted (if it existed): %s", name)
