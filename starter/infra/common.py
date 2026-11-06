"""Shared infrastructure helpers: session, naming, tagging, state, logging.

All infra modules import from here so resource naming, tagging, the boto3 session
(built from the ambient AWS credentials / .env) and the small JSON state file are
consistent and idempotent.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Infra")

# Deterministic resource names → re-running provision never creates duplicates.
PROJECT = "cs-support"
REGION = "us-east-1"

NAMES: dict[str, str] = {
    "lambda_role": "cs-support-lambda-exec-role",
    "kb_role": "cs-support-kb-service-role",
    "gateway_role": "cs-support-gateway-role",
    "order_lambda": "order-tracker",
    "refund_lambda": "refund-processor",
    "rest_api": "cs-support-api",
    "source_bucket": None,  # set at runtime (needs account id for global uniqueness)
    "vector_bucket": None,  # set at runtime
    "vector_index": "cs-support-index",
    "knowledge_base": "CustomerSupportKB",
    "data_source": "cs-support-catalog",
    "gateway": "CustomerSupportGateway",
    "memory": "CustomerSupportMemory",
}

TAGS = {"Project": PROJECT, "ManagedBy": "infra-as-code"}

_STATE_PATH = Path(__file__).resolve().parent / "state.json"


def session() -> boto3.Session:
    """boto3 Session in the project region using ambient credentials (.env)."""
    return boto3.Session(region_name=REGION)


def account_id() -> str:
    """Current AWS account id."""
    return session().client("sts").get_caller_identity()["Account"]


def source_bucket_name() -> str:
    """Globally-unique name for the KB source-documents bucket."""
    return f"cs-support-kb-src-{account_id()}-{REGION}"


def vector_bucket_name() -> str:
    """Globally-unique name for the S3 vectors bucket."""
    return f"cs-support-vectors-{account_id()}-{REGION}"


# ── state.json (records created IDs/ARNs for teardown + config output) ─────────

def read_state() -> dict[str, Any]:
    """Return the persisted infra state (empty dict if none)."""
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(state: dict[str, Any]) -> None:
    """Persist the infra state."""
    _STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def update_state(**kwargs: Any) -> dict[str, Any]:
    """Merge keys into state.json and return the updated state."""
    state = read_state()
    state.update({k: v for k, v in kwargs.items() if v is not None})
    write_state(state)
    return state


# ── polling waiter ─────────────────────────────────────────────────────────────

def wait_until(
    check: Callable[[], bool],
    *,
    what: str,
    timeout: int = 900,
    interval: int = 10,
) -> None:
    """Poll `check` until it returns True or `timeout` seconds elapse.

    Raises TimeoutError if the condition is never met. Used instead of fixed sleeps
    for async resource states (ingestion COMPLETE, memory ACTIVE, etc.).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if check():
            logger.info("%s is ready.", what)
            return
        logger.info("Waiting for %s ...", what)
        time.sleep(interval)
    raise TimeoutError(f"Timed out waiting for {what} after {timeout}s.")
