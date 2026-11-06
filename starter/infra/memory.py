"""AgentCore Memory resource + two strategies (idempotent).

Strategies (per the project brief):
  Semantic extraction  -> name customer_facts       namespace cs_agent/{actorId}/facts
  User preference       -> name customer_preferences  namespace cs_agent/{actorId}/preferences

Uses the high-level MemoryClient (create_or_get_memory + waiter). Produces MEMORY_ID
(written to state and, by outputs.py, into config.yaml -> main.py literal).
"""

from __future__ import annotations

from bedrock_agentcore.memory import MemoryClient

from .common import NAMES, REGION, logger, read_state, update_state

_STRATEGIES = [
    {
        "semanticMemoryStrategy": {
            "name": "customer_facts",
            "namespaces": ["cs_agent/{actorId}/facts"],
        }
    },
    {
        "userPreferenceMemoryStrategy": {
            "name": "customer_preferences",
            "namespaces": ["cs_agent/{actorId}/preferences"],
        }
    },
]


def _client() -> MemoryClient:
    return MemoryClient(region_name=REGION)


def ensure_memory() -> str:
    """Create or reuse the Memory resource with both strategies; return MEMORY_ID."""
    client = _client()
    name = NAMES["memory"]

    # Reuse by name if present (create_or_get_memory is idempotent but we also want the id).
    for mem in client.list_memories():
        if mem.get("id", "").startswith(name) or mem.get("name") == name:
            memory_id = mem["id"]
            logger.info("Memory exists: %s (%s)", name, memory_id)
            update_state(memory_id=memory_id)
            return memory_id

    logger.info("Creating memory %s (waiting for ACTIVE) ...", name)
    result = client.create_or_get_memory(
        name=name,
        strategies=_STRATEGIES,
        description="Customer support cross-session memory (facts + preferences).",
    )
    memory_id = result.get("id") or result.get("memoryId")
    logger.info("Memory ready: %s (%s)", name, memory_id)
    update_state(memory_id=memory_id)
    return memory_id


def delete_memory() -> None:
    """Delete the Memory resource."""
    client = _client()
    memory_id = read_state().get("memory_id")
    if not memory_id:
        for mem in client.list_memories():
            if mem.get("name") == NAMES["memory"]:
                memory_id = mem["id"]
                break
    if not memory_id:
        logger.info("Memory already absent: %s", NAMES["memory"])
        return
    try:
        client.delete_memory_and_wait(memory_id=memory_id)
        logger.info("Memory deleted: %s", memory_id)
    except Exception as error:  # already gone or mid-delete
        logger.warning("Memory delete issue (%s); continuing.", error)
