"""REST API for the order-tracker Lambda (idempotent), deployed to stage `prod`.

Creates three GET resources as Lambda-proxy integrations:
  GET /orders/{order_id}                 operation get_order
  GET /customers/{customer_id}/orders    operation get_customer_orders
  GET /customers/{customer_id}           operation get_customer

These operation names become the MCP tool names when the stage is added to the
AgentCore Gateway as an API Gateway target. Idempotent: reuses the API if a REST API
with the project name already exists.
"""

from __future__ import annotations

from botocore.exceptions import ClientError

from .common import NAMES, REGION, account_id, logger, session, update_state

STAGE = "prod"

# path template -> (operationName, [path part chain])
_ROUTES = [
    ("get_order", ["orders", "{order_id}"]),
    ("get_customer_orders", ["customers", "{customer_id}", "orders"]),
    ("get_customer", ["customers", "{customer_id}"]),
]


def _find_api(client) -> str | None:
    """Return the REST API id matching the project name, else None."""
    for api in client.get_rest_apis(limit=500).get("items", []):
        if api.get("name") == NAMES["rest_api"]:
            return api["id"]
    return None


def _ensure_resource(client, api_id: str, parent_id: str, part: str, cache: dict) -> str:
    """Return the resource id for `part` under parent, creating it if needed."""
    key = (parent_id, part)
    if key in cache:
        return cache[key]
    # Look for an existing child.
    for res in client.get_resources(restApiId=api_id, limit=500).get("items", []):
        if res.get("parentId") == parent_id and res.get("pathPart") == part:
            cache[key] = res["id"]
            return res["id"]
    created = client.create_resource(restApiId=api_id, parentId=parent_id, pathPart=part)
    cache[key] = created["id"]
    return created["id"]


def ensure_api(order_lambda_arn: str) -> dict[str, str]:
    """Create/reuse the REST API, wire the 3 GET proxy routes, deploy stage. Return ids."""
    client = session().client("apigateway")
    acct = account_id()

    api_id = _find_api(client)
    if api_id:
        logger.info("REST API exists: %s (%s)", NAMES["rest_api"], api_id)
    else:
        api_id = client.create_rest_api(
            name=NAMES["rest_api"],
            description="cs-support order-tracker API",
            endpointConfiguration={"types": ["REGIONAL"]},
            tags=dict(_tags()),
        )["id"]
        logger.info("REST API created: %s (%s)", NAMES["rest_api"], api_id)

    root_id = next(
        r["id"] for r in client.get_resources(restApiId=api_id).get("items", []) if r["path"] == "/"
    )
    lambda_uri = (
        f"arn:aws:apigateway:{REGION}:lambda:path/2015-03-31/functions/"
        f"{order_lambda_arn}/invocations"
    )

    cache: dict = {}
    for operation, parts in _ROUTES:
        parent = root_id
        for part in parts:
            parent = _ensure_resource(client, api_id, parent, part, cache)
        resource_id = parent
        # GET method.
        try:
            client.put_method(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod="GET",
                authorizationType="NONE",
                operationName=operation,
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConflictException":
                raise
        # Lambda-proxy integration.
        client.put_integration(
            restApiId=api_id,
            resourceId=resource_id,
            httpMethod="GET",
            type="AWS_PROXY",
            integrationHttpMethod="POST",
            uri=lambda_uri,
        )
        # Method + integration responses — required so the Gateway can parse the
        # generated OpenAPI spec (it rejects operations with no `responses`).
        try:
            client.put_method_response(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod="GET",
                statusCode="200",
                responseModels={"application/json": "Empty"},
            )
        except ClientError as error:
            if error.response["Error"]["Code"] != "ConflictException":
                raise
        try:
            client.put_integration_response(
                restApiId=api_id,
                resourceId=resource_id,
                httpMethod="GET",
                statusCode="200",
                responseTemplates={"application/json": ""},
            )
        except ClientError as error:
            # AWS_PROXY integrations may reject explicit integration responses; the
            # method response alone is enough for the OpenAPI spec. Ignore those.
            if error.response["Error"]["Code"] not in ("ConflictException", "BadRequestException"):
                raise
        logger.info("Wired GET /%s -> %s", "/".join(parts), operation)

    # Allow API Gateway to invoke the order Lambda.
    from . import lambdas

    lambdas.allow_invoke(
        NAMES["order_lambda"],
        principal="apigateway.amazonaws.com",
        source_arn=f"arn:aws:execute-api:{REGION}:{acct}:{api_id}/*/GET/*",
        statement_id="apigw-invoke-order-tracker",
    )

    client.create_deployment(restApiId=api_id, stageName=STAGE)
    logger.info("Deployed REST API %s to stage %s", api_id, STAGE)

    update_state(rest_api_id=api_id, rest_api_stage=STAGE)
    return {"rest_api_id": api_id, "stage": STAGE}


def _tags() -> dict:
    from .common import TAGS

    return TAGS


def delete_api() -> None:
    """Delete the REST API."""
    client = session().client("apigateway")
    api_id = _find_api(client)
    if not api_id:
        logger.info("REST API already absent: %s", NAMES["rest_api"])
        return
    try:
        client.delete_rest_api(restApiId=api_id)
        logger.info("REST API deleted: %s (%s)", NAMES["rest_api"], api_id)
    except ClientError as error:
        if error.response["Error"]["Code"] != "NotFoundException":
            raise
