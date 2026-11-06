"""Code-only verification of the provisioned stack (no console).

Run: python -m infra.verify

Checks:
  1. AWS identity (sts:GetCallerIdentity).
  2. Gateway MCP tools list (programmatic MCPClient — no MCP Inspector UI).
  3. Knowledge Base Retrieve probe ("return policy for electronics" -> 15-day result).
  4. Memory resource is ACTIVE with its two strategies.

Exits non-zero if any check fails. Reads the live config via main.py (config.yaml).
"""

from __future__ import annotations

import sys

from .common import REGION, logger, session


def _check_identity() -> bool:
    ident = session().client("sts").get_caller_identity()
    logger.info("Identity OK: account=%s arn=%s", ident["Account"], ident["Arn"])
    return True


def _check_gateway() -> bool:
    import main

    if not main._is_configured(main.GATEWAY_URL):
        logger.warning("GATEWAY_URL not configured; skipping gateway check.")
        return False
    client = main._gateway_client()
    with client:
        tools = client.list_tools_sync()
    names = sorted(getattr(t, "tool_name", "?") for t in tools)
    logger.info("Gateway OK: %d MCP tools: %s", len(names), ", ".join(names))
    return len(names) >= 2


def _check_knowledge_base() -> bool:
    import main

    if not main._is_configured(main.KB_ID):
        logger.warning("KB_ID not configured; skipping KB check.")
        return False
    result = main.search_knowledge_base("What is the return policy for electronics?")
    ok = "15" in result and "day" in result.lower()
    logger.info("KB Retrieve OK: 15-day result found = %s", ok)
    return ok


def _check_memory() -> bool:
    import main

    if not main._is_configured(main.MEMORY_ID):
        logger.warning("MEMORY_ID not configured; skipping memory check.")
        return False
    client = main.get_memory_client()
    status = client.get_memory_status(main.MEMORY_ID)
    namespaces = main.get_namespaces(client, main.MEMORY_ID)
    logger.info("Memory OK: status=%s strategies=%s", status, list(namespaces))
    return str(status).upper() == "ACTIVE" and len(namespaces) >= 2


def main() -> int:
    """Run all checks; return 0 if all pass, 1 otherwise."""
    logger.info("=== Verifying provisioned stack in %s ===", REGION)
    checks = [
        ("identity", _check_identity),
        ("gateway", _check_gateway),
        ("knowledge_base", _check_knowledge_base),
        ("memory", _check_memory),
    ]
    results: dict[str, bool] = {}
    for name, fn in checks:
        try:
            results[name] = fn()
        except Exception as error:  # noqa: BLE001 - report, don't abort other checks
            logger.warning("Check '%s' failed: %s", name, error)
            results[name] = False
    passed = sum(1 for ok in results.values() if ok)
    logger.info("=== Verify: %d/%d passed: %s ===", passed, len(results), results)
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
