"""Provision the Gateway layer (Lambdas + REST API + Gateway + targets) — idempotent.

Run: python -m infra.provision_gateway
Deploys both Lambdas, the REST API (stage prod), the AgentCore Gateway (NONE auth),
and the two MCP targets; then writes GATEWAY_URL into config.yaml and syncs main.py.
Focused subset of the full infra.provision so T1/T2 can be tested as Wave 5 lands.
"""

from __future__ import annotations

import sys

from . import apigw, gateway, iam, lambdas, outputs
from .common import logger


def main() -> int:
    """Provision the gateway layer end to end."""
    try:
        lambda_role = iam.ensure_lambda_role()
        gateway_role = iam.ensure_gateway_role()
        arns = lambdas.ensure_lambdas(lambda_role)

        api = apigw.ensure_api(arns["order_arn"])

        gw = gateway.ensure_gateway(gateway_role)
        gateway.ensure_targets(gw["gateway_id"], api["rest_api_id"], api["stage"])

        outputs.apply()
        logger.info("Gateway provisioning complete. GATEWAY_URL=%s", gw["gateway_url"])
        return 0
    except Exception as error:  # surface a clear message; destroy is available
        logger.exception("Gateway provisioning failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
