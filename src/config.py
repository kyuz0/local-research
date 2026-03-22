"""Configuration loader for the DeepResearch agent.

Loads defaults from config.yaml, overlays with .env for API keys,
and provides save/load helpers.
"""

import os
import yaml
import copy

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")
_active_config_path: str = _CONFIG_PATH  # may be overridden by load_config(path=...)

_DEFAULTS = {
    "api": {
        "openai_base_url": "http://localhost:8080/v1",
        "openai_api_key": "",
        "tavily_api_key": "",
    },
    "settings": {
        "use_dynamic_webpage_analysis": False,
        "search_provider": "duckduckgo",
        "use_bm25_hints": False,
    },
    "quotas": {
        "orchestrator": {
            "delegate_research_task": 3,
            "write_todos": 15,
            "read_todos": 15,
            "write_file": 5,
            "read_file": 5,
        },
        "researcher": {
            "web_search": 5,
            "analyze_webpage": 7,
            "think_tool": 15,
        },
        "url_analyzer": {
            "read_full_page": 2,
            "grep_page": 10,
            "read_page_chunk": 10,
            "think_tool": 8,
        },
    },
}

# The live config dict, mutated by load/save
cfg: dict = {}


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Merge overlay into base, recursively for nested dicts."""
    result = copy.deepcopy(base)
    for k, v in overlay.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def load_config(path: str | None = None) -> dict:
    """Load config from YAML file, falling back to defaults for missing keys.

    Args:
        path: Optional path to an alternative config YAML file.  When given,
              this path is also used by :func:`save_config` so reads and writes
              stay in sync.  Defaults to the built-in ``src/config.yaml``.
    """
    global cfg, _active_config_path
    if path is not None:
        _active_config_path = os.path.abspath(path)
    else:
        _active_config_path = _CONFIG_PATH
    file_cfg = {}
    if os.path.exists(_active_config_path):
        with open(_active_config_path, "r") as f:
            file_cfg = yaml.safe_load(f) or {}
    cfg = _deep_merge(_DEFAULTS, file_cfg)

    # Overlay API keys from environment if set (env takes priority for secrets)
    if os.environ.get("OPENAI_API_BASE"):
        cfg["api"]["openai_base_url"] = os.environ["OPENAI_API_BASE"]
    if os.environ.get("OPENAI_API_KEY"):
        cfg["api"]["openai_api_key"] = os.environ["OPENAI_API_KEY"]
    if os.environ.get("TAVILY_API_KEY"):
        cfg["api"]["tavily_api_key"] = os.environ["TAVILY_API_KEY"]

    return cfg


def save_config() -> None:
    """Persist the current config dict to the active config file."""
    with open(_active_config_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def q(agent: str, tool: str) -> int:
    """Shorthand to get a quota value: q('researcher', 'web_search') -> 5."""
    return int(cfg.get("quotas", {}).get(agent, {}).get(tool, 10))
