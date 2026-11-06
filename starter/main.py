"""
Customer Support AI Agent — Amazon Bedrock AgentCore + Strands
================================================================
A conversational customer support agent for an e-commerce platform. Capabilities:
  - Order tracking & refunds via the AgentCore Gateway (MCP → Lambda/API Gateway)
  - Grounded product/policy answers via a Bedrock Knowledge Base (RAG)
  - Cross-session memory via AgentCore Memory (facts + preferences)
  - Exact loyalty-discount math via the AgentCore Code Interpreter
  - Live web browsing via the AgentCore Browser tool

Configuration lives in config.yaml. REGION, KB_ID and
MEMORY_ID are mirrored into the literals below by sync_config.py because
setup_permissions.py AST-reads them. See ../docs/HOW_IT_WORKS.md for the full picture.

Local test (temporarily switch __main__ to main()):
  python main.py '{"prompt": "Hello", "customer_id": "CUST-123", "session_id": "s1"}'

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
from pydantic import BaseModel, ValidationError
from strands import Agent, tool
from strands.hooks import (
    AfterInvocationEvent,
    HookProvider,
    HookRegistry,
    MessageAddedEvent,
)
from strands.models import BedrockModel

# Browser, MCP, and code-interpreter are imported inside the functions that use them
# rather than here — a missing or mismatched optional dep then can't stop the module
# from loading (which has to finish inside the runtime's startup window).

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("CSAI_Agent")


# ── Configuration loader ──────────────────────────────────────────────────────
# config.yaml holds the real config. These defaults just cover the handful of values
# needed to boot if that file is missing (e.g. not bundled in the image); everything
# else falls back at its call site.

_MINIMAL_DEFAULTS: dict[str, Any] = {
    "model_id": "global.amazon.nova-2-lite-v1:0",
    "region": "us-east-1",
    "system_prompt_path": "system_prompt.txt",
}


def load_config(path: str | None = None) -> dict[str, Any]:
    """Load config.yaml over the minimal defaults. Returns defaults if the file is
    missing or unreadable, so startup doesn't depend on it."""
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
    """True unless the value is empty or still a `<placeholder>`."""
    return bool(value) and not str(value).startswith("<")


_CFG = load_config()

# ── TODO 1 — App Initialisation ───────────────────────────────────────────────
app = BedrockAgentCoreApp()

# Skip interactive tool-consent prompts (there's no TTY in the deployed runtime).
os.environ["BYPASS_TOOL_CONSENT"] = "true"


# ── TODO 2 — Configuration ────────────────────────────────────────────────────
# setup_permissions.py (which we can't change) reads REGION, KB_ID and MEMORY_ID by
# ast.literal_eval, so these three have to be plain string literals. They're kept in
# sync with config.yaml by sync_config.py — edit config.yaml, not these lines.
# GATEWAY_URL isn't read by that script, so it comes straight from config.

REGION    = "us-east-1"  # mirror of config.yaml:region (via sync_config.py)
KB_ID     = "YPXPQVRBJX"  # synced from config.yaml
MEMORY_ID = "CustomerSupportMemory-V31S5N77HY"  # synced from config.yaml

GATEWAY_URL = _CFG.get("gateway_url", "<gateway_url>")


# ── TODO 3 — Model and Clients ────────────────────────────────────────────────
# Built on first use, not at import, so loading the module stays fast and doesn't
# depend on AWS being reachable.
model_id = _CFG.get("model_id", "global.amazon.nova-2-lite-v1:0")

_model: BedrockModel | None = None
_memory_client: MemoryClient | None = None
_bedrock_runtime_client: Any = None


def get_model() -> BedrockModel:
    """The shared Nova 2 Lite model, built once."""
    global _model
    if _model is None:
        _model = BedrockModel(model_id=model_id)
    return _model


def get_memory_client() -> MemoryClient:
    """The shared AgentCore Memory client, built once."""
    global _memory_client
    if _memory_client is None:
        _memory_client = MemoryClient(region_name=REGION)
    return _memory_client


def get_bedrock_runtime() -> Any:
    """The shared bedrock-agent-runtime client (used for KB Retrieve), built once."""
    global _bedrock_runtime_client
    if _bedrock_runtime_client is None:
        _bedrock_runtime_client = boto3.client("bedrock-agent-runtime", region_name=REGION)
    return _bedrock_runtime_client


def _load_system_prompt() -> str:
    """Read the prompt from the file named in config; fall back to a one-liner."""
    prompt_file = _CFG.get("system_prompt_path", "system_prompt.txt")
    prompt_path = Path(__file__).resolve().parent / prompt_file
    try:
        return prompt_path.read_text(encoding="utf-8").strip()
    except OSError as error:
        logger.warning("Could not read system prompt (%s); using a minimal default.", error)
        return "You are a helpful Amazon store customer support assistant. Be concise and accurate."


SYSTEM_PROMPT = _load_system_prompt()


# ── Memory namespaces ─────────────────────────────────────────────────────────

def get_namespaces(mem_client: MemoryClient, memory_id: str) -> dict:
    """Return a dict mapping strategy type → namespace template string.

    Reads each strategy's namespace from ``namespaceTemplates[0]`` (current API),
    falling back to ``namespaces[0]`` for older responses. Handles both the
    ``type`` and ``memoryStrategyType`` key variants.
    """
    namespaces: dict = {}
    for strategy in mem_client.get_memory_strategies(memory_id):
        strategy_type = strategy.get("type") or strategy.get("memoryStrategyType")
        templates = strategy.get("namespaceTemplates") or strategy.get("namespaces") or []
        if strategy_type and templates:
            namespaces[strategy_type] = templates[0]
    return namespaces


# ── Long-term memory hook ─────────────────────────────────────────────────────

class MemoryHook(HookProvider):
    """Long-term memory hook for the customer support agent."""

    def __init__(
        self,
        actor_id: str,
        session_id: str,
        memory_client: MemoryClient,
        memory_id: str,
    ):
        self.actor_id = actor_id
        self.session_id = session_id
        self.memory_client = memory_client
        self.memory_id = memory_id
        try:
            self.namespaces = get_namespaces(memory_client, memory_id)
        except Exception as error:  # memory may not be ready yet — skip it for now
            logger.warning("Could not load memory namespaces (%s); memory disabled this turn.", error)
            self.namespaces = {}

    @staticmethod
    def _plain_text(message: dict) -> str | None:
        """Return the text of a plain-text message, or None if it has tool blocks."""
        content = message.get("content") or []
        if any(isinstance(block, dict) and ("toolResult" in block or "toolUse" in block) for block in content):
            return None
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                return block["text"]
        return None

    def retrieve_customer_context(self, event: MessageAddedEvent):
        """Retrieve relevant memories and prepend them to the user message."""
        try:
            messages = event.agent.messages
            if not messages:
                return
            last = messages[-1]
            if last.get("role") != "user":
                return
            query = self._plain_text(last)
            if not query:
                return

            top_k = _CFG.get("memory", {}).get("retrieve_top_k", 5)
            collected: list[str] = []
            for strategy_type, namespace in self.namespaces.items():
                ns = namespace.format(actorId=self.actor_id)
                try:
                    memories = self.memory_client.retrieve_memories(
                        memory_id=self.memory_id, namespace=ns, query=query, top_k=top_k
                    )
                except Exception as error:
                    logger.warning("retrieve_memories failed for %s (%s).", strategy_type, error)
                    continue
                for record in memories or []:
                    text = (record.get("content") or {}).get("text") if isinstance(record, dict) else None
                    if text:
                        collected.append(f"[{strategy_type}] {text}")

            if collected:
                context = "\n".join(collected)
                for block in last.get("content", []):
                    if isinstance(block, dict) and isinstance(block.get("text"), str):
                        block["text"] = f"Customer Context:\n{context}\n\n{query}"
                        break
                logger.info("Injected %d memory item(s) for actor_id=%s", len(collected), self.actor_id)
        except Exception:
            logger.exception("retrieve_customer_context failed; continuing without memory.")

    def save_support_interaction(self, event: AfterInvocationEvent):
        """Save the completed turn to memory after the agent responds."""
        try:
            messages = event.agent.messages
            customer_query = None
            agent_response = None
            for message in reversed(messages):
                role = message.get("role")
                text = self._plain_text(message)
                if role == "assistant" and agent_response is None and text:
                    agent_response = text
                elif role == "user" and customer_query is None and text:
                    customer_query = text
                if customer_query and agent_response:
                    break
            if customer_query and agent_response:
                self.memory_client.create_event(
                    memory_id=self.memory_id,
                    actor_id=self.actor_id,
                    session_id=self.session_id,
                    messages=[(customer_query, "USER"), (agent_response, "ASSISTANT")],
                )
                logger.info("Saved interaction to memory for actor_id=%s", self.actor_id)
        except Exception:
            logger.exception("save_support_interaction failed; continuing.")

    def register_hooks(self, registry: HookRegistry) -> None:  # type: ignore
        """Register both memory callbacks."""
        registry.add_callback(MessageAddedEvent, self.retrieve_customer_context)
        registry.add_callback(AfterInvocationEvent, self.save_support_interaction)


# ── Knowledge Base search (RAG) ───────────────────────────────────────────────

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
    except Exception as error:  # a KB hiccup shouldn't sink the whole reply
        logger.warning("Knowledge base retrieve failed: %s", error)
        return f"Knowledge base search failed: {error}"

    results = resp.get("retrievalResults", [])
    if not results:
        return "No relevant information found in the knowledge base."

    chunks = [r.get("content", {}).get("text", "") for r in results]
    return separator.join(chunk for chunk in chunks if chunk)


# ── Structured output validation (stand-out: Pydantic) ───────────────────────
# Validate the discount breakdown before returning it, so a malformed Code
# Interpreter result is caught rather than passed to the model as-is.
class DiscountBreakdown(BaseModel):
    """Validated shape of a loyalty discount result."""

    order_total: float
    tier: str
    tier_discount_rate: float
    tier_discount_pct: float
    points_redeemed: int
    tier_discount: float
    final_total: float
    total_savings: float
    points_earned: int
    remaining_points: int
    points_value_applied: float | None = None
    note: str | None = None


def _validated_breakdown(data: dict) -> str:
    """Return `data` as JSON after checking it against DiscountBreakdown.

    If it doesn't validate, log the reason and return the data anyway — we'd rather
    show the numbers than fail the request over a schema quibble.
    """
    try:
        return DiscountBreakdown(**data).model_dump_json()
    except ValidationError as error:
        logger.warning("Discount breakdown failed validation (%s); returning raw result.", error)
        return json.dumps(data)


# ── Loyalty discount (Code Interpreter) ───────────────────────────────────────

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
    loyalty_cfg = _CFG.get("loyalty", {})
    earn_rates = loyalty_cfg.get("earn_rates", {"standard": 1, "device": 2, "fresh": 5})
    tier_rates = loyalty_cfg.get("tier_rates", {"Silver": 0.0, "Gold": 0.10, "Platinum": 0.15})
    points_per_dollar = loyalty_cfg.get("points_per_dollar", 100)
    min_redemption = loyalty_cfg.get("min_redemption", 500)
    max_redeem_fraction = loyalty_cfg.get("max_redeem_fraction", 0.5)

    logger.info(
        "calculate_loyalty_discount points=%s tier=%s total=%s category=%s",
        loyalty_points, tier, order_total, product_category,
    )

    # Self-contained snippet executed in the sandbox. Config-driven values are
    # injected as literals so the code needs no external state.
    code = f"""
        import json

        earn_rates = {earn_rates!r}
        tier_rates = {tier_rates!r}
        points_per_dollar = {points_per_dollar}
        min_redemption = {min_redemption}
        max_redeem_fraction = {max_redeem_fraction}

        loyalty_points = {int(loyalty_points)}
        tier = {tier!r}
        order_total = float({float(order_total)})
        product_category = {product_category!r}

        # Points redeemed: floor to a multiple of min_redemption, capped so their dollar
        # value never exceeds max_redeem_fraction of the order.
        usable_points = (loyalty_points // min_redemption) * min_redemption
        max_point_value = order_total * max_redeem_fraction
        point_value = min(usable_points / points_per_dollar, max_point_value)
        points_redeemed = int(round(point_value * points_per_dollar))

        subtotal_after_points = order_total - point_value
        tier_rate = tier_rates.get(tier, 0.0)
        tier_discount = round(subtotal_after_points * tier_rate, 2)
        final_total = round(subtotal_after_points - tier_discount, 2)
        total_savings = round(order_total - final_total, 2)
        points_earned = int(final_total * earn_rates.get(product_category, 1))
        remaining_points = loyalty_points - points_redeemed + points_earned

        print(json.dumps({{
            "order_total": round(order_total, 2),
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "tier_discount_pct": round(tier_rate * 100, 2),
            "points_redeemed": points_redeemed,
            "points_value_applied": round(point_value, 2),
            "tier_discount": tier_discount,
            "final_total": final_total,
            "total_savings": total_savings,
            "points_earned": points_earned,
            "remaining_points": remaining_points,
        }}))
    """

    try:
        # Import here so the module still loads if this package isn't present.
        from bedrock_agentcore.tools.code_interpreter_client import code_session

        with code_session(REGION) as sandbox:
            response = sandbox.invoke(
                "executeCode",
                {"language": "python", "clearContext": True, "code": code},
            )
            for event in response["stream"]:
                result = event["result"]
                # The breakdown is the stdout JSON printed by the sandbox code.
                stdout = (result.get("structuredContent") or {}).get("stdout", "")
                if not stdout:
                    content = result.get("content") or [{}]
                    stdout = content[0].get("text", "") if content else ""
                try:
                    breakdown = json.loads(stdout)
                    return _validated_breakdown(breakdown)
                except (json.JSONDecodeError, TypeError):
                    # Could not parse the printed JSON — return the raw result event.
                    return json.dumps(result)
        return json.dumps({"error": "Code Interpreter returned no result."})
    except Exception as error:  # fall back to a tier-only calculation; never raise
        logger.warning("Code Interpreter unavailable (%s); using tier-only fallback.", error)
        tier_rate = tier_rates.get(tier, 0.0)
        tier_discount = round(float(order_total) * tier_rate, 2)
        final_total = round(float(order_total) - tier_discount, 2)
        return _validated_breakdown({
            "order_total": round(float(order_total), 2),
            "tier": tier,
            "tier_discount_rate": tier_rate,
            "tier_discount_pct": round(tier_rate * 100, 2),
            "points_redeemed": 0,
            "tier_discount": tier_discount,
            "final_total": final_total,
            "total_savings": tier_discount,
            "points_earned": int(final_total * earn_rates.get(product_category, 1)),
            "remaining_points": int(loyalty_points),
            "note": "Code Interpreter unavailable; tier discount only.",
        })


# ── Browser tool ──────────────────────────────────────────────────────────────
# AgentCoreBrowser lives in strands_tools.browser, which 0.9.0 dropped — pyproject
# pins <0.9.0. Imported here (not at the top) so a bad version doesn't block startup.
def _browser_tools() -> list:
    """The AgentCore browser tool, or [] if it isn't available."""
    try:
        from strands_tools.browser import AgentCoreBrowser

        timeout = _CFG.get("browser", {}).get("session_timeout", 3600)
        browser = AgentCoreBrowser(region=REGION, session_timeout=timeout)
        return [browser.browser]
    except Exception as error:
        logger.warning("Browser tool unavailable (%s); continuing without web browsing.", error)
        return []


# ── Agent entrypoint ──────────────────────────────────────────────────────────

@app.entrypoint
async def invoke(payload: dict[str, Any], context: Any = None) -> str:
    """Handle one request from AgentCore.

    payload keys:
      prompt      (str, required) — the customer's message
      customer_id (str, optional) — memory actor id
      session_id  (str, optional) — session id; generated if absent
    """
    try:
        user_input = (payload or {}).get("prompt")
        if not user_input or not str(user_input).strip():
            return "Error: 'prompt' is required."

        actor_id = (payload or {}).get("customer_id") or "anonymous"
        session_id = (payload or {}).get("session_id") or str(uuid.uuid4())
        logger.info("invoke actor_id=%s session_id=%s", actor_id, session_id)

        tools: list = [search_knowledge_base, calculate_loyalty_discount]
        tools += _browser_tools()

        hooks = _memory_hooks(actor_id, session_id)

        # Gateway tools only work while the MCP client is open, so build and run the
        # agent inside the `with`. No gateway configured → just use the local tools.
        mcp_client = _gateway_client()
        if mcp_client is None:
            agent = Agent(model=get_model(), tools=tools, hooks=hooks, system_prompt=SYSTEM_PROMPT)
            result = agent(user_input)
            return _response_text(result)

        with mcp_client:
            try:
                tools.extend(mcp_client.list_tools_sync())
            except Exception as error:
                logger.warning("Could not load gateway tools (%s); using local tools only.", error)
            agent = Agent(model=get_model(), tools=tools, hooks=hooks, system_prompt=SYSTEM_PROMPT)
            result = agent(user_input)
        return _response_text(result)
    except Exception as error:  # noqa: BLE001 - one bad request shouldn't take down the runtime
        logger.exception("invoke failed")
        return f"Error handling request: {error}"


def _memory_hooks(actor_id: str, session_id: str) -> list:
    """[MemoryHook] for this customer/session, or [] if Memory isn't configured."""
    if not _is_configured(MEMORY_ID):
        logger.info("Memory not configured; skipping memory hook.")
        return []
    try:
        return [MemoryHook(actor_id, session_id, get_memory_client(), MEMORY_ID)]
    except Exception as error:  # if memory's down, just answer without it
        logger.warning("Could not build memory hook (%s); continuing without memory.", error)
        return []


def _gateway_client():
    """An MCPClient pointed at the AgentCore Gateway, or None if it isn't set up."""
    if not _is_configured(GATEWAY_URL):
        logger.info("Gateway not configured; skipping MCP tools.")
        return None
    try:
        from mcp.client.streamable_http import streamable_http_client
        from strands.tools.mcp.mcp_client import MCPClient

        return MCPClient(lambda: streamable_http_client(GATEWAY_URL))
    except Exception as error:
        logger.warning("Could not create MCP client (%s); skipping gateway tools.", error)
        return None


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
