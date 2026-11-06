"""
Customer Support AI Agent — Starter Code
==========================================
Your task is to complete this file by implementing all sections marked
with # TODO comments.

Reference the project instructions and rubric for guidance.
Work through each section yourself.

Run locally (after filling in config values):
  uv run main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

Deploy to AgentCore:
  agentcore deploy

Invoke deployed agent:
  agentcore invoke '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'
"""

# ── Imports ───────────────────────────────────────────────────────────────────
# These imports are provided. Do not remove them.
import argparse
import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import boto3
import yaml
from bedrock_agentcore.memory import MemoryClient
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from strands.models import BedrockModel

# NOTE: heavier / version-sensitive integrations are imported lazily inside the
# functions that use them, so a slow or incompatible optional dependency can never
# break module import (which must complete within the runtime's init window):
#   - bedrock_agentcore.tools.code_interpreter_client.code_session  (Wave 3)
#   - mcp.client.streamable_http.streamable_http_client             (Wave 5)
#   - strands.tools.mcp.mcp_client.MCPClient                        (Wave 5)
#   - the AgentCore browser tool                                    (Wave 4)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Agent")


# ── Configuration loader ──────────────────────────────────────────────────────
# config.yaml is the single source of truth for every tunable value. load_config()
# reads it and returns its contents. _MINIMAL_DEFAULTS holds ONLY the few values
# needed to start the process if config.yaml is somehow missing/invalid at runtime
# (e.g. not bundled into the deployed image) — it is a safety net, not a second
# source of truth. Each value is otherwise read at its point of use with its own
# `.get(..., fallback)` so the full config tree is never duplicated here.

_MINIMAL_DEFAULTS: dict[str, Any] = {
    "model_id": "global.amazon.nova-2-lite-v1:0",
    "region": "us-east-1",
    "system_prompt_path": "system_prompt.txt",
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config.yaml, falling back to minimal start-up defaults.

    Returns config.yaml merged over :data:`_MINIMAL_DEFAULTS`. Never raises: when the
    file is missing or invalid, the minimal defaults let the process start, and
    per-key callers supply their own fallbacks for anything else.
    """
    config_path = Path(path) if path else Path(__file__).resolve().parent / "config.yaml"
    merged: dict[str, Any] = {**_MINIMAL_DEFAULTS}
    try:
        loaded = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            merged.update(loaded)
        else:
            logger.warning("config.yaml is not a mapping; using minimal defaults.")
    except FileNotFoundError:
        logger.warning("config.yaml not found at %s; using minimal defaults.", config_path)
    except (OSError, yaml.YAMLError) as error:
        logger.warning("Could not read config.yaml (%s); using defaults.", error)
    return merged


def _is_configured(value: str | None) -> bool:
    """True when a config value is a real (non-placeholder, non-empty) string."""
    return bool(value) and not str(value).startswith("<")


_CFG = load_config()

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
# A single BedrockAgentCoreApp instance registers the ASGI server for AgentCore.
app = BedrockAgentCoreApp()


# Suppress interactive tool-consent prompts (required in headless deployments).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# config.yaml is the single source of truth (see load_config above). However, the
# provided setup_permissions.py (which must not be modified) reads REGION, KB_ID and
# MEMORY_ID by AST-parsing *literal string assignments* in this file — it calls
# ast.literal_eval on the right-hand side, so a `_CFG.get(...)` expression would make
# it fail. Those three therefore MUST stay as plain string literals here.
#
# To avoid two sources of truth, they are not hand-edited: sync_config.py mirrors
# them from config.yaml (region / knowledge_base_id / memory_id), and infra/outputs.py
# runs that sync automatically after provisioning. Treat the literals below like a
# generated lockfile — edit config.yaml, then run `python sync_config.py`.
#
# GATEWAY_URL is NOT read by setup_permissions.py, so it is sourced directly from
# config.yaml like every other value.

REGION    = "us-east-1"  # mirror of config.yaml:region (via sync_config.py)
KB_ID     = "YPXPQVRBJX"  # mirror of config.yaml:knowledge_base_id (via sync_config.py)
MEMORY_ID = "<mem_id>"  # mirror of config.yaml:memory_id (via sync_config.py)

GATEWAY_URL = _CFG.get("gateway_url", "<gateway_url>")


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Reasoning model (Nova 2 Lite) plus the Memory and Bedrock KB runtime clients.
# These are created lazily (cached singletons) rather than at import time, so module
# import always completes quickly within the runtime's initialization window and a
# transient AWS/client hiccup can never break startup.
model_id = _CFG.get("model_id", "global.amazon.nova-2-lite-v1:0")

_model: BedrockModel | None = None
_memory_client: MemoryClient | None = None
_bedrock_runtime_client: Any = None


def get_model() -> BedrockModel:
    """Return the shared BedrockModel, creating it on first use."""
    global _model
    if _model is None:
        _model = BedrockModel(model_id=model_id)
    return _model


def get_memory_client() -> MemoryClient:
    """Return the shared MemoryClient, creating it on first use."""
    global _memory_client
    if _memory_client is None:
        _memory_client = MemoryClient(region_name=REGION)
    return _memory_client


def get_bedrock_runtime() -> Any:
    """Return the shared bedrock-agent-runtime client, creating it on first use."""
    global _bedrock_runtime_client
    if _bedrock_runtime_client is None:
        _bedrock_runtime_client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    return _bedrock_runtime_client


# System prompt is externalised (config.yaml:system_prompt_path).
def _load_system_prompt() -> str:
    """Load the system prompt from the configured file, with a minimal fallback."""
    prompt_file = _CFG.get("system_prompt_path", "system_prompt.txt")
    prompt_path = Path(__file__).resolve().parent / prompt_file
    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        logger.warning("Could not read system prompt (%s); using a minimal default.", error)
        return "You are a helpful Amazon store customer support assistant. Be concise and accurate."


SYSTEM_PROMPT = _load_system_prompt()


# ── TODO 4 — Namespace Helper ─────────────────────────────────────────────────
# Implement get_namespaces() to return a dict mapping strategy type to
# namespace template string.
#
# Steps:
#   1. Call mem_client.get_memory_strategies(memory_id) to get strategy list
#   2. Read the namespace from strategy["namespaceTemplates"][0].
#      For compatibility with older AgentCore responses, fall back to
#      strategy["namespaces"][0] when namespaceTemplates is absent.
#
# Example output:
#   { "SEMANTIC": "cs_agent/{actorId}/facts",
#     "USER_PREFERENCE": "cs_agent/{actorId}/preferences" }

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> dict:
    """Return a dict mapping strategy type → namespace template string."""
    # TODO: Implemented in Wave 6.
    raise NotImplementedError("get_namespaces is implemented in Wave 6.")


# ── TODO 5 — Memory Hook ──────────────────────────────────────────────────────
# Implement MemoryHook, a HookProvider subclass that adds long-term memory.
#
# The class needs:
#   __init__(self, actor_id, session_id, memory_client, memory_id)
#     — store all four as instance attributes
#     — call get_namespaces() and store the result as self.namespaces
#
#   retrieve_customer_context(self, event: MessageAddedEvent)
#     — only runs for plain-text user messages (not tool results)
#     — for each strategy namespace, call memory_client.retrieve_memories(
#          memory_id, namespace (formatted with actorId), query, top_k=5)
#     — collect non-empty memory texts tagged with their strategy type
#     — if any memories found, prepend them to the user message as:
#          "Customer Context:\n<memories>\n\n<original_message>"
#
#   save_support_interaction(self, event: AfterInvocationEvent)
#     — walk the message list backwards to find the last plain-text user
#       query and the last assistant response
#     — call memory_client.create_event(memory_id, actor_id, session_id,
#          messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")])
#
#   register_hooks(self, registry: HookRegistry)
#     — register retrieve_customer_context on MessageAddedEvent
#     — register save_support_interaction on AfterInvocationEvent

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        # TODO: Store actor_id, session_id, memory_id, memory_client as attributes
        # TODO: Call get_namespaces() and store the result as self.namespaces
        pass

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        # TODO: Implement memory retrieval
        # Steps:
        #   1. Get the last message from event.agent.messages
        #   2. Check it is a user message and not a tool result
        #   3. Extract the user query text
        #   4. For each namespace in self.namespaces, call retrieve_memories()
        #   5. Collect non-empty memory texts with strategy type tags
        #   6. If any found, prepend them to the user message
        pass

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        # TODO: Implement memory saving
        # Steps:
        #   1. Get messages from event.agent.messages
        #   2. Walk backwards to find the last user query (plain text)
        #      and the last assistant response
        #   3. Call memory_client.create_event() with both messages
        pass

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        # TODO: Register retrieve_customer_context on MessageAddedEvent
        # TODO: Register save_support_interaction on AfterInvocationEvent
        pass


# ── TODO 6 — Knowledge Base Tool ─────────────────────────────────────────────
# Implement search_knowledge_base(query) using the @tool decorator.
#
# Steps:
#   1. Guard: if KB_ID is empty return "Knowledge base not configured."
#   2. Call _bedrock_runtime.retrieve(
#          knowledgeBaseId=KB_ID,
#          retrievalQuery={"text": query}
#      )
#   3. Extract resp["retrievalResults"]; return a message if empty
#   4. Join the text chunks with "\n---\n" and return the result
#
# The docstring is the tool description — the model uses it to decide when
# to call this tool, so keep it clear and accurate.

@tool
def search_knowledge_base(query: str) -> str:
    """
    Search the Amazon product catalog and support knowledge base.
    Use this for product specifications, return policies, warranty
    information, loyalty program details, and order status definitions.

    Args:
        query: The question or topic to search for

    Returns:
        Relevant information retrieved from the knowledge base
    """
    if not _is_configured(KB_ID):
        return "Knowledge base not configured."

    retrieval_cfg = _CFG.get("retrieval", {})
    top_k = retrieval_cfg.get("top_k", 5)
    separator = retrieval_cfg.get("chunk_separator", "\n---\n")

    logger.info("search_knowledge_base query=%r top_k=%s", query, top_k)
    try:
        resp = get_bedrock_runtime().retrieve(
            knowledgeBaseId=KB_ID,
            retrievalQuery={"text": query},
            retrievalConfiguration={
                "vectorSearchConfiguration": {"numberOfResults": top_k}
            },
        )
    except Exception as error:  # degrade gracefully; never crash the turn
        logger.warning("Knowledge base retrieve failed: %s", error)
        return f"Knowledge base search failed: {error}"

    results = resp.get("retrievalResults", [])
    if not results:
        return "No relevant information found in the knowledge base."

    chunks = [r.get("content", {}).get("text", "") for r in results]
    return separator.join(chunk for chunk in chunks if chunk)


# ── TODO 7 — Loyalty Discount Tool (Code Interpreter) ────────────────────────
# Implement calculate_loyalty_discount() using the @tool decorator.
#
# The tool must:
#   1. Build a self-contained Python code string that:
#        • Defines earn_rates: {"standard": 1, "device": 2, "fresh": 5}
#        • Defines tier_rates: {"Silver": 0.00, "Gold": 0.10, "Platinum": 0.15}
#        • Calculates points_redeemed (floor to nearest 500, cap at 50% of order)
#        • Calculates tier_discount (applied to subtotal after points)
#        • Calculates final_total, total_savings, points_earned, remaining_points
#        • Prints a JSON result dict
#   2. Execute the code with code_session(REGION).invoke("executeCode", {...})
#      using language="python" and clearContext=True
#   3. Return the first result event as a JSON string
#   4. Include a fallback that computes only the tier discount if the
#      Code Interpreter is unavailable

@tool
def calculate_loyalty_discount(
    loyalty_points: int,
    tier: str,
    order_total: float,
    product_category: str = "standard",
) -> str:
    """
    Calculate the loyalty discount for a customer order using the
    AgentCore Code Interpreter. Runs exact arithmetic in a secure sandbox.

    Args:
        loyalty_points:   Customer's current points balance
        tier:             Customer tier — Silver, Gold, or Platinum
        order_total:      Order total in USD
        product_category: standard, device, or fresh

    Returns:
        Full discount breakdown and final price
    """
    # TODO: Implemented in Wave 3 (Code Interpreter) with a tier-only fallback.
    raise NotImplementedError("calculate_loyalty_discount is implemented in Wave 3.")


# ── TODO 8 — Agent Entrypoint ─────────────────────────────────────────────────
# Implement the invoke() function decorated with @app.entrypoint.
#
# Steps:
#   1. Extract user_input, actor_id, and session_id from the payload
#      (generate a UUID if session_id is missing)
#   2. Instantiate MemoryHook for this actor/session
#   3. Instantiate AgentCoreBrowser(region=REGION)
#   4. Build the tools list: [search_knowledge_base, calculate_loyalty_discount,
#                              agent_core_browser.browser]
#   5. Connect to the Gateway via MCPClient, load gateway_tools, extend tools list
#   6. Create and invoke the Agent with all tools, hooks, and system_prompt
#   7. Return the text from the first content block of the response
#   8. Handle exceptions gracefully

@app.entrypoint
async def invoke(payload: dict[str, Any], context: Any = None) -> str:
    """Main handler called by AgentCore for every incoming request.

    Expected payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — unique customer identifier (memory actor id)
      session_id  (str, optional) — session identifier; generated if absent

    Wave 1 builds a tool-less agent (model only). Later waves add the KB, loyalty,
    browser, and gateway tools plus the memory hook.
    """
    try:
        user_input = (payload or {}).get("prompt")
        if not user_input or not str(user_input).strip():
            return "Error: 'prompt' is required."

        actor_id = (payload or {}).get("customer_id") or "anonymous"
        session_id = (payload or {}).get("session_id") or str(uuid.uuid4())
        logger.info("invoke actor_id=%s session_id=%s", actor_id, session_id)

        tools: list = [search_knowledge_base]  # Wave 2+ append tools here.

        agent = Agent(model=get_model(), tools=tools, system_prompt=SYSTEM_PROMPT)
        result = agent(user_input)
        return _response_text(result)
    except Exception as error:  # noqa: BLE001 - entrypoint must never crash the runtime
        logger.exception("invoke failed")
        return f"Error handling request: {error}"


def _response_text(result: Any) -> str:
    """Extract the first text content block from a Strands AgentResult."""
    try:
        return result.message["content"][0]["text"]
    except (AttributeError, KeyError, IndexError, TypeError):
        return str(result)


# ── CLI entry point (do not modify) ──────────────────────────────────────────
def main():
    """Run one invocation from the command line for local testing."""
    parser = argparse.ArgumentParser()
    parser.add_argument("payload", type=str)
    args = parser.parse_args()
    response = asyncio.run(invoke(json.loads(args.payload)))
    print(response)


if __name__ == "__main__":
    app.run()
    # Uncomment the line below and comment app.run() for local CLI testing:
    # main()
