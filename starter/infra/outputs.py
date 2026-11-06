"""Write provisioned resource IDs into config.yaml, then sync main.py literals.

Closes the loop so nothing is hand-edited: reads infra/state.json, updates the
relevant keys in config.yaml (preserving comments is not required — PyYAML dump is
fine for the generated values), and runs sync_config.py to mirror the three
literals (region/knowledge_base_id/memory_id) into main.py.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from .common import logger, read_state

_STARTER = Path(__file__).resolve().parent.parent
_CONFIG = _STARTER / "config.yaml"
_SYNC = _STARTER / "sync_config.py"

# infra/state.json key -> config.yaml key.
_MAP = {
    "kb_id": "knowledge_base_id",
    "memory_id": "memory_id",
    "gateway_url": "gateway_url",
}


def write_outputs() -> dict[str, str]:
    """Merge provisioned IDs from state.json into config.yaml. Return what changed."""
    state = read_state()
    config = yaml.safe_load(_CONFIG.read_text(encoding="utf-8")) or {}
    changed: dict[str, str] = {}
    for state_key, cfg_key in _MAP.items():
        value = state.get(state_key)
        if value and config.get(cfg_key) != value:
            config[cfg_key] = value
            changed[cfg_key] = value
    if changed:
        _CONFIG.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
        logger.info("Updated config.yaml: %s", ", ".join(changed))
    else:
        logger.info("config.yaml already up to date.")
    return changed


def sync_main_literals() -> None:
    """Run sync_config.py to mirror the three literals into main.py."""
    result = subprocess.run(
        [sys.executable, str(_SYNC)],
        capture_output=True,
        text=True,
        check=False,
    )
    logger.info("sync_config: %s", result.stdout.strip() or result.stderr.strip())
    if result.returncode != 0:
        raise RuntimeError(f"sync_config.py failed: {result.stderr.strip()}")


def apply() -> None:
    """Write outputs then sync main.py literals."""
    write_outputs()
    sync_main_literals()
