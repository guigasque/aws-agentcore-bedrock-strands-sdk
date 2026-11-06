"""Provision ONLY the Memory layer (idempotent). Run: python -m infra.provision_memory.

Creates the AgentCore Memory resource with the two strategies, then writes MEMORY_ID
into config.yaml and syncs main.py. Focused subset of infra.provision so T4 can be
tested as Wave 6 lands.
"""

from __future__ import annotations

import sys

from . import memory, outputs
from .common import logger


def main() -> int:
    """Provision the memory layer end to end."""
    try:
        memory_id = memory.ensure_memory()
        outputs.apply()
        logger.info("Memory provisioning complete. MEMORY_ID=%s", memory_id)
        return 0
    except Exception as error:
        logger.exception("Memory provisioning failed: %s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
