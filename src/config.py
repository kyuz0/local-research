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
        "openai_model": "local-model",
    },
    "settings": {
        "use_dynamic_webpage_analysis": False,
        "search_provider": "duckduckgo",
        "use_bm25_hints": False,
    },
    "search_profile": "default",
    "profiles": {
        "shallow": {
            "description": "Fast and lightweight search, suitable for quick facts. Expect 1-2 solid sources.",
            "quotas": {
                "orchestrator": {"delegate_research_task": 2, "write_todos": 10, "read_todos": 10, "write_file": 3, "read_file": 3, "max_tokens": 5000},
                "researcher": {"web_search": 2, "analyze_webpage": 3, "think_tool": 8, "max_tokens": 5000},
                "url_analyzer": {"read_full_page": 1, "grep_page": 5, "read_page_chunk": 5, "think_tool": 5, "max_tokens": 5000, "max_chunk_lines": 150, "max_grep_matches": 10, "max_static_chars": 30000}
            }
        },
        "default": {
            "description": "Balanced search with moderate depth. Expect 2-3 solid sources per claim.",
            "quotas": {
                "orchestrator": {"delegate_research_task": 3, "write_todos": 15, "read_todos": 15, "write_file": 5, "read_file": 5, "max_tokens": 5000},
                "researcher": {"web_search": 5, "analyze_webpage": 7, "think_tool": 15, "max_tokens": 5000},
                "url_analyzer": {"read_full_page": 2, "grep_page": 10, "read_page_chunk": 10, "think_tool": 8, "max_tokens": 5000, "max_chunk_lines": 150, "max_grep_matches": 10, "max_static_chars": 30000}
            }
        },
        "deep": {
            "description": "Extensive research, slower but covers more sources. Expect 4+ solid sources per claim, exploring multiple angles.",
            "quotas": {
                "orchestrator": {"delegate_research_task": 8, "write_todos": 30, "read_todos": 30, "write_file": 10, "read_file": 10, "max_tokens": 5000},
                "researcher": {"web_search": 10, "analyze_webpage": 15, "think_tool": 25, "max_tokens": 5000},
                "url_analyzer": {"read_full_page": 4, "grep_page": 20, "read_page_chunk": 20, "think_tool": 15, "max_tokens": 5000, "max_chunk_lines": 150, "max_grep_matches": 10, "max_static_chars": 30000}
            }
        }
    }
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
    if os.environ.get("OPENAI_MODEL"):
        cfg["api"]["openai_model"] = os.environ["OPENAI_MODEL"]

    return cfg


def save_config() -> None:
    """Persist the current config dict to the active config file."""
    # Create a copy to prevent mutating the live config and avoid saving secrets
    save_data = copy.deepcopy(cfg)
    
    # Strip out sensitive API keys before writing
    if "api" in save_data:
        save_data["api"].pop("openai_api_key", None)
        save_data["api"].pop("tavily_api_key", None)
        
    with open(_active_config_path, "w") as f:
        yaml.dump(save_data, f, default_flow_style=False, sort_keys=False)


def get_active_profile() -> dict:
    """Retrieves the active profile configuration."""
    profile_name = cfg.get("search_profile", "default")
    profiles = cfg.get("profiles", {})
    return profiles.get(profile_name, profiles.get("default", {}))

def q(agent: str, tool: str) -> int:
    """Shorthand to get a quota value: q('researcher', 'web_search') -> 5."""
    profile = get_active_profile()
    return int(profile.get("quotas", {}).get(agent, {}).get(tool, 10))

def get_profile_info() -> tuple[str, str, str]:
    """Returns (profile_name, description, summary_text)."""
    profile_name = cfg.get("search_profile", "default")
    profile = get_active_profile()
    desc = profile.get("description", "No description provided.")
    
    quotas = profile.get("quotas", {})
    orch = quotas.get("orchestrator", {})
    res = quotas.get("researcher", {})
    url = quotas.get("url_analyzer", {})
    
    summary = (
        f"Orchestrator: {orch.get('delegate_research_task', 0)} delegates, {orch.get('write_todos', 0)} todos/reads, {orch.get('write_file', 0)} files\n"
        f"Researcher: {res.get('web_search', 0)} searches, {res.get('analyze_webpage', 0)} analyses, {res.get('think_tool', 0)} thoughts\n"
        f"URL Analyzer: {url.get('read_full_page', 0)} full reads, {url.get('grep_page', 0)} greps, {url.get('read_page_chunk', 0)} chunks, {url.get('think_tool', 0)} thoughts"
    )
    return profile_name, desc, summary
