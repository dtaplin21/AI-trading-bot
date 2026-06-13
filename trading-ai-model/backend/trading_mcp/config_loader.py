from __future__ import annotations
import os
import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "agents.yaml"

def load_manifest(config_path=None):
    from config.agent_mcp_schema import AgentManifest
    path = Path(config_path or os.getenv("AGENT_CONFIG_PATH", str(DEFAULT_CONFIG_PATH)))
    if not path.exists():
        logger.warning("agents.yaml not found at %s", path)
        return AgentManifest()
    import yaml
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    manifest = AgentManifest(**raw)
    for agent_id, cfg in manifest.agents.items():
        prefix = f"AGENT_{agent_id.upper().replace('-','_')}_"
        enabled_env = os.getenv(f"{prefix}ENABLED")
        if enabled_env is not None:
            cfg.enabled = enabled_env.lower() not in ("false","0","no")
        cfg.agent_id = agent_id
    return manifest

@lru_cache(maxsize=1)
def get_manifest():
    return load_manifest()

def reload_manifest():
    get_manifest.cache_clear()
    return get_manifest()
