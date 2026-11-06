"""Grant the deployed agent's runtime role the permissions Waves 2–3 need (idempotent).

The provided setup_permissions.py (which must not be modified) grants KB Retrieve,
Memory, and Browser — but it refuses to run until MEMORY_ID exists (Wave 6), and it
does not grant the AgentCore Code Interpreter the loyalty tool uses. This module
attaches a separate, least-privilege inline policy to the same runtime execution role
(read from .bedrock_agentcore.yaml) covering Code Interpreter and — while Memory is
not yet provisioned — KB Retrieve, so RAG and discount math work in the cloud now.
Once Memory lands, setup_permissions.py becomes the source of truth.

Run: python -m infra.agent_permissions   (after `agentcore deploy`)
Idempotent: re-running just re-puts the inline policy.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

from .common import REGION, account_id, logger, session

POLICY_NAME = "CustomerSupportAgentExtras"
_CONFIG = Path(__file__).resolve().parent.parent / ".bedrock_agentcore.yaml"


def _runtime_role_name(agent: str | None = None) -> str:
    """Read the runtime execution-role name from the deploy manifest."""
    config = yaml.safe_load(_CONFIG.read_text(encoding="utf-8"))
    name = agent or config.get("default_agent")
    aws = config.get("agents", {}).get(name, {}).get("aws", {})
    role_arn = aws.get("execution_role") or ""
    match = re.fullmatch(r"arn:[a-z0-9-]+:iam::\d{12}:role/(.+)", role_arn)
    if not match:
        raise ValueError("No execution_role in .bedrock_agentcore.yaml — run `agentcore deploy` first.")
    return match.group(1)


def _policy(acct: str, kb_id: str | None) -> dict:
    """Least-privilege policy: Code Interpreter (+ KB Retrieve when KB_ID is known).

    This complements the provided setup_permissions.py (KB/Memory/Browser), which
    cannot run until MEMORY_ID exists. Granting KB Retrieve here lets RAG work in the
    cloud before Memory is provisioned; setup_permissions.py remains the source of
    truth once Memory lands (its KB statement is equivalent).
    """
    statements = [
        {
            "Sid": "CodeInterpreter",
            "Effect": "Allow",
            "Action": [
                "bedrock-agentcore:CreateCodeInterpreter",
                "bedrock-agentcore:StartCodeInterpreterSession",
                "bedrock-agentcore:InvokeCodeInterpreter",
                "bedrock-agentcore:StopCodeInterpreterSession",
                "bedrock-agentcore:DeleteCodeInterpreter",
                "bedrock-agentcore:ListCodeInterpreters",
                "bedrock-agentcore:GetCodeInterpreter",
                "bedrock-agentcore:GetCodeInterpreterSession",
                "bedrock-agentcore:ListCodeInterpreterSessions",
            ],
            "Resource": [
                f"arn:aws:bedrock-agentcore:{REGION}:{acct}:code-interpreter/*",
                f"arn:aws:bedrock-agentcore:{REGION}:aws:code-interpreter/aws.codeinterpreter.v1",
            ],
        }
    ]
    if kb_id:
        statements.append(
            {
                "Sid": "KnowledgeBaseRetrieve",
                "Effect": "Allow",
                "Action": "bedrock:Retrieve",
                "Resource": f"arn:aws:bedrock:{REGION}:{acct}:knowledge-base/{kb_id}",
            }
        )
    return {"Version": "2012-10-17", "Statement": statements}


def _kb_id_from_config() -> str | None:
    """Read a real knowledge_base_id from config.yaml (None if still a placeholder)."""
    config = yaml.safe_load((Path(__file__).resolve().parent.parent / "config.yaml").read_text())
    value = (config or {}).get("knowledge_base_id")
    if isinstance(value, str) and value and not value.startswith("<"):
        return value
    return None


def apply(agent: str | None = None) -> int:
    """Attach the extras inline policy (Code Interpreter + KB Retrieve) to the role."""
    role_name = _runtime_role_name(agent)
    acct = account_id()
    kb_id = _kb_id_from_config()
    session().client("iam").put_role_policy(
        RoleName=role_name,
        PolicyName=POLICY_NAME,
        PolicyDocument=json.dumps(_policy(acct, kb_id)),
    )
    granted = "Code Interpreter" + (f" + KB Retrieve ({kb_id})" if kb_id else "")
    logger.info("Attached %s to role %s: %s.", POLICY_NAME, role_name, granted)
    return 0


def delete(agent: str | None = None) -> None:
    """Remove the extras inline policy (and the legacy name) — idempotent."""
    from botocore.exceptions import ClientError

    role_name = _runtime_role_name(agent)
    iam = session().client("iam")
    for policy_name in (POLICY_NAME, "CustomerSupportCodeInterpreter"):
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
            logger.info("Removed %s from role %s.", policy_name, role_name)
        except ClientError as error:
            if error.response["Error"]["Code"] != "NoSuchEntity":
                raise


if __name__ == "__main__":
    sys.exit(apply())
