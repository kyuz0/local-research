"""Configuration loader for the DeepResearch agent.

Loads defaults from config.yaml, overlays with .env for API keys,
and provides save/load helpers.
"""

import os
import yaml
import copy

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.yaml")

_DEFAULTS = {
    "api": {
        "openai_base_url": "http://localhost:8080/v1",
        "openai_api_key": "",
        "tavily_api_key": "",
    },
    "settings": {
        "use_dynamic_webpage_analysis": False,
        "search_provider": "duckduckgo",
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


def load_config() -> dict:
    """Load config from YAML file, falling back to defaults for missing keys."""
    global cfg
    file_cfg = {}
    if os.path.exists(_CONFIG_PATH):
        with open(_CONFIG_PATH, "r") as f:
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
    """Persist the current config dict to config.yaml."""
    with open(_CONFIG_PATH, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)


def q(agent: str, tool: str) -> int:
    """Shorthand to get a quota value: q('researcher', 'web_search') -> 5."""
    return int(cfg.get("quotas", {}).get(agent, {}).get(tool, 10))
