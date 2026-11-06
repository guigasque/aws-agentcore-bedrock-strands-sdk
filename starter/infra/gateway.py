"""AgentCore Gateway (NONE authorizer) + two MCP targets (idempotent).

Targets:
  order_tracker    — API Gateway target (restApiId + stage) exposing the 3 GET operations
  refund_processor — Lambda target (refund-processor) with the tool schema from lambda/lambda_schema

Produces GATEWAY_URL (…/mcp). The NONE authorizer matches the starter's unsigned MCP
client and is sandbox-only — removed by destroy.
"""

from __future__ import annotations

import json
from pathlib import Path

from botocore.exceptions import ClientError

from .common import NAMES, logger, read_state, session, update_state, wait_until

_SCHEMA_FILE = Path(__file__).resolve().parent.parent / "lambda" / "lambda_schema"


def _find_gateway(client) -> str | None:
    """Return the gateway id matching the project name, else None."""
    for gw in client.list_gateways(maxResults=100).get("items", []):
        if gw.get("name") == NAMES["gateway"]:
            return gw["gatewayId"]
    return None


def _gateway_ready(client, gateway_id: str) -> bool:
    status = client.get_gateway(gatewayIdentifier=gateway_id)["status"]
    if status in ("FAILED",):
        raise RuntimeError(f"Gateway {gateway_id} status {status}.")
    return status == "READY"


def ensure_gateway(gateway_role_arn: str) -> dict[str, str]:
    """Create/reuse the Gateway (NONE auth, MCP). Return id + url."""
    client = session().client("bedrock-agentcore-control")
    gateway_id = _find_gateway(client)
    if gateway_id:
        logger.info("Gateway exists: %s (%s)", NAMES["gateway"], gateway_id)
    else:
        resp = client.create_gateway(
            name=NAMES["gateway"],
            description="Customer support MCP gateway (sandbox, NONE auth).",
            roleArn=gateway_role_arn,
            protocolType="MCP",
            authorizerType="NONE",
        )
        gateway_id = resp["gatewayId"]
        logger.info("Gateway created: %s (%s)", NAMES["gateway"], gateway_id)

    wait_until(lambda: _gateway_ready(client, gateway_id), what="gateway READY", timeout=300, interval=10)
    gw = client.get_gateway(gatewayIdentifier=gateway_id)
    url = gw.get("gatewayUrl")
    update_state(gateway_id=gateway_id, gateway_url=url)
    return {"gateway_id": gateway_id, "gateway_url": url}


def _target_status(client, gateway_id: str, name: str) -> str | None:
    """Return the status of the named target, or None if it does not exist."""
    for t in client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100).get("items", []):
        if t.get("name") == name:
            return t.get("status")
    return None


def _delete_target(client, gateway_id: str, name: str) -> None:
    """Delete the named target if present (used to recreate FAILED targets)."""
    for t in client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100).get("items", []):
        if t.get("name") == name:
            client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            logger.info("Deleted target for recreation: %s", name)


def _target_exists(client, gateway_id: str, name: str) -> bool:
    status = _target_status(client, gateway_id, name)
    if status == "FAILED":
        # A failed target can never recover; delete and wait for it to clear so it
        # can be recreated cleanly (deletion is asynchronous).
        _delete_target(client, gateway_id, name)
        wait_until(
            lambda: _target_status(client, gateway_id, name) is None,
            what=f"target {name} deletion",
            timeout=120,
            interval=5,
        )
        return False
    return status is not None


def ensure_targets(gateway_id: str, rest_api_id: str, stage: str) -> None:
    """Create the order_tracker (API GW) and refund_processor (Lambda) targets."""
    client = session().client("bedrock-agentcore-control")
    cred = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    if _target_exists(client, gateway_id, "order-tracker"):
        logger.info("Target exists: order-tracker")
    else:
        # toolFilters selects which API routes become MCP tools (required).
        tool_filters = [
            {"filterPath": "/orders/{order_id}", "methods": ["GET"]},
            {"filterPath": "/customers/{customer_id}/orders", "methods": ["GET"]},
            {"filterPath": "/customers/{customer_id}", "methods": ["GET"]},
        ]
        client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="order-tracker",
            description="Order & customer lookups via API Gateway.",
            targetConfiguration={
                "mcp": {
                    "apiGateway": {
                        "restApiId": rest_api_id,
                        "stage": stage,
                        "apiGatewayToolConfiguration": {"toolFilters": tool_filters},
                    }
                }
            },
            credentialProviderConfigurations=cred,
        )
        logger.info("Target created: order-tracker (API Gateway %s/%s)", rest_api_id, stage)

    if _target_exists(client, gateway_id, "refund-processor"):
        logger.info("Target exists: refund-processor")
    else:
        schema = json.loads(_SCHEMA_FILE.read_text(encoding="utf-8"))
        refund_arn = read_state().get("refund_lambda_arn")
        client.create_gateway_target(
            gatewayIdentifier=gateway_id,
            name="refund-processor",
            description="Refunds, refund status, return labels via Lambda.",
            targetConfiguration={
                "mcp": {"lambda": {"lambdaArn": refund_arn, "toolSchema": {"inlinePayload": schema}}}
            },
            credentialProviderConfigurations=cred,
        )
        logger.info("Target created: refund-processor (Lambda)")


def delete_gateway() -> None:
    """Delete targets then the gateway (idempotent)."""
    client = session().client("bedrock-agentcore-control")
    gateway_id = read_state().get("gateway_id") or _find_gateway(client)
    if not gateway_id:
        logger.info("Gateway already absent: %s", NAMES["gateway"])
        return
    try:
        for t in client.list_gateway_targets(gatewayIdentifier=gateway_id, maxResults=100).get("items", []):
            client.delete_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
            logger.info("Deleted target: %s", t.get("name"))
        client.delete_gateway(gatewayIdentifier=gateway_id)
        logger.info("Gateway deleted: %s (%s)", NAMES["gateway"], gateway_id)
    except ClientError as error:
        if error.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
