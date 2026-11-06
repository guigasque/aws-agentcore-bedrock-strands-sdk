"""Deploy the two provided Lambda functions (idempotent create-or-update).

order-tracker   — from lambda/order_tracker.py   (API Gateway proxy integration)
refund-processor — from lambda/refund_processor.py (direct AgentCore Gateway Lambda target)

Both run Python 3.12 (per the project brief) with the shared execution role. The code
is zipped in-memory from the files under starter/lambda/ and never modified.
"""

from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

from botocore.exceptions import ClientError

from .common import NAMES, REGION, TAGS, logger, session, update_state

_LAMBDA_DIR = Path(__file__).resolve().parent.parent / "lambda"
_RUNTIME = "python3.12"
_HANDLER = "lambda_function.lambda_handler"  # we place the file as lambda_function.py in the zip


def _zip_single(source_file: Path) -> bytes:
    """Zip one source file as lambda_function.py (so the handler path is stable)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("lambda_function.py", source_file.read_text(encoding="utf-8"))
    return buffer.getvalue()


def _ensure_function(name: str, source_file: Path, role_arn: str) -> str:
    """Create or update one Lambda function; return its ARN (idempotent)."""
    client = session().client("lambda")
    code = _zip_single(source_file)
    try:
        client.get_function(FunctionName=name)
        client.update_function_code(FunctionName=name, ZipFile=code, Publish=True)
        logger.info("Lambda updated: %s", name)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
        # New roles can take a moment to be assumable by Lambda.
        for attempt in range(6):
            try:
                client.create_function(
                    FunctionName=name,
                    Runtime=_RUNTIME,
                    Role=role_arn,
                    Handler=_HANDLER,
                    Code={"ZipFile": code},
                    Timeout=30,
                    Publish=True,
                    Tags=TAGS,
                )
                break
            except ClientError as create_error:
                if create_error.response["Error"]["Code"] == "InvalidParameterValueException" and attempt < 5:
                    logger.info("Waiting for Lambda role to propagate (%s) ...", name)
                    time.sleep(10)
                    continue
                raise
        logger.info("Lambda created: %s", name)

    arn = client.get_function(FunctionName=name)["Configuration"]["FunctionArn"]
    return arn


def ensure_lambdas(role_arn: str) -> dict[str, str]:
    """Deploy both Lambdas. Return {order_arn, refund_arn}."""
    order_arn = _ensure_function(NAMES["order_lambda"], _LAMBDA_DIR / "order_tracker.py", role_arn)
    refund_arn = _ensure_function(NAMES["refund_lambda"], _LAMBDA_DIR / "refund_processor.py", role_arn)
    update_state(order_lambda_arn=order_arn, refund_lambda_arn=refund_arn)
    return {"order_arn": order_arn, "refund_arn": refund_arn}


def allow_invoke(function_name: str, principal: str, source_arn: str, statement_id: str) -> None:
    """Add a resource-based permission so `principal` may invoke the function (idempotent)."""
    client = session().client("lambda")
    try:
        client.add_permission(
            FunctionName=function_name,
            StatementId=statement_id,
            Action="lambda:InvokeFunction",
            Principal=principal,
            SourceArn=source_arn,
        )
        logger.info("Added invoke permission %s on %s", statement_id, function_name)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceConflictException":
            raise  # already present


def delete_lambdas() -> None:
    """Delete both Lambda functions (idempotent)."""
    client = session().client("lambda")
    for name in (NAMES["order_lambda"], NAMES["refund_lambda"]):
        try:
            client.delete_function(FunctionName=name)
            logger.info("Lambda deleted: %s", name)
        except ClientError as error:
            if error.response["Error"]["Code"] != "ResourceNotFoundException":
                raise


__all__ = ["ensure_lambdas", "allow_invoke", "delete_lambdas", "REGION"]
