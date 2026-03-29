""""Research Agent - Standalone script for Microsoft agent-framework deployment.

This module creates a deep research agent using Microsoft agent-framework for 
orchestration, natively using Agent.as_tool for delegation.
"""

import asyncio
import os
import json
import uuid
from datetime import datetime

import dotenv
from dotenv import load_dotenv

from textual import events, work, on
from textual.app import App, ComposeResult
from textual.containers import VerticalScroll, Horizontal, Vertical
from textual.widgets import Input, Static, Header, Footer, Collapsible, RichLog, OptionList, Label, Button, Checkbox, Switch, TabbedContent, TabPane, Select
from textual.widgets.option_list import Option
from textual.screen import ModalScreen

from agent_framework.openai import OpenAIChatClient
from agent_framework import AgentResponseUpdate
from prompts import (
    RESEARCHER_INSTRUCTIONS,
    RESEARCH_WORKFLOW_INSTRUCTIONS,
    SUBAGENT_DELEGATION_INSTRUCTIONS,
)
from tools import web_search, analyze_webpage, get_analyze_webpage_dynamic_tool, think_tool, write_todos, write_file, read_todos, read_file
import config as app_config

load_dotenv(override=True)
# load_config() is called in __main__ after --config is parsed; also called
# here as a fallback so imports that happen at module level still work.
app_config.load_config()

_session_events = []
_current_call_by_source = {}
_current_text_by_source = {}

def _write_log():
    run_dir = os.environ.get("CURRENT_RUN_DIR")
    if not run_dir: return
    log_file = os.path.join(run_dir, "session_log.json")
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            json.dump(_session_events, f, indent=2)
    except Exception:
        pass

def log_prompt(prompt: str):
    global _session_events, _current_call_by_source, _current_text_by_source
    _session_events = [{
        "timestamp": datetime.now().isoformat(),
        "source": "User",
        "type": "prompt",
        "data": {"text": prompt}
    }]
    _current_call_by_source.clear()
    _current_text_by_source.clear()
    _write_log()

def log_stream_content(source: str, content):
    global _session_events, _current_call_by_source, _current_text_by_source
    
    if content.type == "text":
        if not content.text: return
        _current_call_by_source[source] = None
        
        idx = _current_text_by_source.get(source)
        if idx is not None and idx < len(_session_events) and _session_events[idx]["type"] == "text":
            _session_events[idx]["data"]["text"] += content.text
        else:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "type": "text",
                "data": {"text": content.text}
            }
            _session_events.append(entry)
            _current_text_by_source[source] = len(_session_events) - 1
            
    elif content.type == "function_call":
        _current_text_by_source[source] = None
        
        call_id = getattr(content, "call_id", None)
        name = getattr(content, "name", None)
        arguments = getattr(content, "arguments", "") or ""
        
        if call_id:
            entry = {
                "timestamp": datetime.now().isoformat(),
                "source": source,
                "type": "function_call",
                "data": {
                    "call_id": call_id,
                    "name": name,
                    "arguments": arguments
                }
            }
            _session_events.append(entry)
            _current_call_by_source[source] = len(_session_events) - 1
        else:
            idx = _current_call_by_source.get(source)
            if idx is not None and idx < len(_session_events):
                if arguments:
                    _session_events[idx]["data"]["arguments"] += arguments
            
    elif content.type == "function_result":
        _current_text_by_source[source] = None
        _current_call_by_source[source] = None
        
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "type": "function_result",
            "data": {
                "call_id": getattr(content, "call_id", None),
                "result": str(getattr(content, "result", ""))
            }
        }
        _session_events.append(entry)
        
    else:
        _current_text_by_source[source] = None
        _current_call_by_source[source] = None
        data = {}
        if hasattr(content, "model_dump"):
            data = content.model_dump()
            data.pop("type", None)
            
        entry = {
            "timestamp": datetime.now().isoformat(),
            "source": source,
            "type": content.type,
            "data": data
        }
        _session_events.append(entry)
        
    _write_log()

# Limits
max_concurrent_research_units = 3
max_researcher_iterations = 3

current_date = datetime.now().strftime("%Y-%m-%d")

INSTRUCTIONS = (
    RESEARCH_WORKFLOW_INSTRUCTIONS
    + "\n\n"
    + "=" * 80
    + "\n\n"
    + SUBAGENT_DELEGATION_INSTRUCTIONS.format(
        max_concurrent_research_units=max_concurrent_research_units,
        max_researcher_iterations=max_researcher_iterations,
    )
)

def setup_agents(subagent_callback=None):
    """Initialize and return the client and orchestrator agent."""
    base_url = app_config.cfg["api"].get("openai_base_url") or "https://api.openai.com/v1"
    api_key = os.environ.get("OPENAI_API_KEY") or "dummy"
    model_id = app_config.cfg["api"].get("openai_model", "local-model") or "local-model"
        
    client = OpenAIChatClient(
        base_url=base_url,
        api_key=api_key,
        model_id=model_id,
        function_invocation_configuration={"max_iterations": 20}
    )

    use_dynamic = app_config.cfg["settings"]["use_dynamic_webpage_analysis"]
    webpage_tool = get_analyze_webpage_dynamic_tool(stream_callback=subagent_callback) if use_dynamic else analyze_webpage

    q = app_config.q
    _, profile_description, _ = app_config.get_profile_info()
    q_res_search = q("researcher", "web_search")
    q_res_analyze = q("researcher", "analyze_webpage")
    q_res_think = q("researcher", "think_tool")
    q_res_max_tokens = int(q("researcher", "max_tokens") or 5000)
    res_word_limit = int(q_res_max_tokens * 0.6)

    q_orch_delegate = q("orchestrator", "delegate_research_task")
    q_orch_todos = q("orchestrator", "write_todos")
    q_orch_files = q("orchestrator", "write_file")
    q_orch_max_tokens = int(q("orchestrator", "max_tokens") or 5000)
    orch_word_limit = int(q_orch_max_tokens * 0.6)

    research_agent = client.as_agent(
        name="research_agent",
        description="A specialized sub-agent for executing deep research operations given a specific topic or instruction.",
        instructions=RESEARCHER_INSTRUCTIONS.format(
            date=current_date,
            search_quota=q_res_search,
            analyze_quota=q_res_analyze,
            think_quota=q_res_think,
            profile_description=profile_description,
            word_limit=res_word_limit,
        ),
        tools=[web_search, webpage_tool, think_tool],
        default_options={"temperature": 0.0, "max_tokens": q_res_max_tokens},
    )

    from agent_framework import tool
    from tools import tool_quotas_ctx, check_quota

    @tool(name="delegate-research-task", description="Delegate a research task to a specialized research sub-agent that can search the internet and analyze webpages.")
    async def delegate_research_task(instructions: str) -> str:
        quota_error = check_quota("delegate-research-task")
        if quota_error: return quota_error
            
        sub_quotas = {
            "web_search": {"used": 0, "limit": int(q_res_search)},
            "analyze_webpage": {"used": 0, "limit": int(q_res_analyze)},
            "think_tool": {"used": 0, "limit": int(q_res_think)},
        }
        token = tool_quotas_ctx.set(sub_quotas)
        try:
            final_text = ""
            stream = research_agent.run(instructions, stream=True)
            async for update in stream:
                if subagent_callback:
                    await subagent_callback(update)
                for c in update.contents:
                    if c.type == "text" and c.text:
                        final_text += c.text
            return final_text
        finally:
            tool_quotas_ctx.reset(token)

    orchestrator = client.as_agent(
        name="orchestrator",
        instructions=INSTRUCTIONS.format(
            orchestrator_quota=q_orch_delegate,
            orchestrator_todos_quota=q_orch_todos,
            orchestrator_files_quota=q_orch_files,
            profile_description=profile_description,
            word_limit=orch_word_limit,
        ),
        tools=[delegate_research_task, write_todos, write_file, read_todos, read_file],
        default_options={"temperature": 0.0, "max_tokens": q_orch_max_tokens},
    )
    
    return client, orchestrator

class ConfigureScreen(ModalScreen[dict | None]):
    """Screen for configuring API keys and settings."""
    
    CSS = """
    ConfigureScreen {
        align: center middle;
        background: $background 50%;
    }
    #dialog {
        padding: 1 2;
        width: 75;
        height: auto;
        border: thick $background 80%;
        background: $surface;
    }
    .config-label {
        padding-top: 1;
        text-style: bold;
    }
    .config-hint {
        color: $text-muted;
        text-style: italic;
        padding-bottom: 1;
        width: 100%;
        height: auto;
    }
    .switch-container {
        height: auto;
        align: left middle;
        padding-bottom: 1;
    }
    .switch-label {
        padding-right: 2;
        padding-top: 1;
    }
    #config-buttons {
        margin-top: 2;
        align: right middle;
        height: auto;
    }
    Button {
        margin-left: 1;
    }
    """
    
    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Configuration", classes="config-label", id="title")
            
            with TabbedContent():
                with TabPane("General / API", id="tab-api"):
                    yield Label("OpenAI API Base URL", classes="config-label")
                    yield Input(value=os.environ.get("OPENAI_API_BASE", app_config.cfg["api"].get("openai_base_url", "")), id="api_base")
                    
                    yield Label("OpenAI API Key", classes="config-label")
                    yield Input(value=os.environ.get("OPENAI_API_KEY", ""), password=True, id="api_key")
                    
                    yield Label("OpenAI Model", classes="config-label")
                    yield Label("Required for official OpenAI endpoints (e.g. gpt-4o).", classes="config-hint")
                    yield Input(value=os.environ.get("OPENAI_MODEL", app_config.cfg["api"].get("openai_model", "local-model") or "local-model"), id="api_model")
                    
                    yield Label("Tavily API Key", classes="config-label")
                    yield Input(value=os.environ.get("TAVILY_API_KEY", ""), password=True, id="tavily_key")
                    
                    yield Label("Search Provider", classes="config-label")
                    current_provider = app_config.cfg.get("settings", {}).get("search_provider", "duckduckgo")
                    yield Select(
                        (("DuckDuckGo", "duckduckgo"), ("Tavily", "tavily")),
                        value=current_provider,
                        id="search_provider"
                    )

                    use_dynamic = app_config.cfg["settings"]["use_dynamic_webpage_analysis"]
                    yield Label("Dynamic Web Page Analysis", classes="config-label")
                    yield Label("Reduces token usage with large pages, but might miss some information.", classes="config-hint")
                    with Horizontal(classes="switch-container"):
                        yield Label("Enable:", classes="switch-label")
                        yield Switch(value=use_dynamic, id="use_dynamic")

                    use_bm25 = app_config.cfg.get("settings", {}).get("use_bm25_hints", False)
                    yield Label("BM25 Line Hints", classes="config-label")
                    yield Label("Pre-score page lines with BM25 to guide the URL analyzer (requires rank-bm25).", classes="config-hint")
                    with Horizontal(classes="switch-container"):
                        yield Label("Enable:", classes="switch-label")
                        yield Switch(value=use_bm25, id="use_bm25", disabled=not use_dynamic)
                
                with TabPane("Search Profile", id="tab-profile"):
                    profiles = app_config.cfg.get("profiles", {})
                    profile_options = [(p, p) for p in profiles.keys()]
                    current_profile = app_config.cfg.get("search_profile", "default")
                    
                    yield Label("Select Profile", classes="config-label")
                    yield Select(profile_options, value=current_profile, id="search_profile_select")
                    
                    yield Label("Description", classes="config-label")
                    yield Label("", id="profile_description", classes="config-hint")
                    
                    yield Label("Quotas Summary", classes="config-label")
                    yield Label("", id="profile_summary", classes="config-hint")
            
            with Horizontal(id="config-buttons"):
                yield Button("Save for Session", variant="primary", id="save_session")
                yield Button("Save & Persist", id="save_persist")
                yield Button("Cancel", id="cancel")
                
    def on_mount(self) -> None:
        self._update_profile_info(app_config.cfg.get("search_profile", "default"))

    @on(Switch.Changed, "#use_dynamic")
    def on_use_dynamic_changed(self, event: Switch.Changed) -> None:
        bm25_switch = self.query_one("#use_bm25", Switch)
        bm25_switch.disabled = not event.value
        if not event.value:
            bm25_switch.value = False

    @on(Select.Changed, "#search_profile_select")
    def on_profile_select_changed(self, event: Select.Changed) -> None:
        self._update_profile_info(str(event.value))

    def _update_profile_info(self, profile_name: str) -> None:
        profiles = app_config.cfg.get("profiles", {})
        profile = profiles.get(profile_name, profiles.get("default", {}))
        
        desc_label = self.query_one("#profile_description", Label)
        summary_label = self.query_one("#profile_summary", Label)
        
        desc_label.update(profile.get("description", "No description provided."))
        
        quotas = profile.get("quotas", {})
        orch = quotas.get("orchestrator", {})
        res = quotas.get("researcher", {})
        url = quotas.get("url_analyzer", {})
        
        summary = (
            f"Orchestrator:  {orch.get('delegate_research_task', 0)} delegates, {orch.get('write_todos', 0)} todos, {orch.get('write_file', 0)} files\n"
            f"Researcher:    {res.get('web_search', 0)} searches, {res.get('analyze_webpage', 0)} analyses, {res.get('think_tool', 0)} thoughts\n"
            f"URL Analyzer:  {url.get('read_full_page', 0)} full reads, {url.get('grep_page', 0)} greps, {url.get('read_page_chunk', 0)} chunks, {url.get('think_tool', 0)} thoughts"
        )
        summary_label.update(summary)

    @on(Button.Pressed, "#save_session")
    def action_save_session(self) -> None:
        self._dismiss_with_result(persist=False)

    @on(Button.Pressed, "#save_persist")
    def action_save_persist(self) -> None:
        self._dismiss_with_result(persist=True)

    def _dismiss_with_result(self, persist: bool) -> None:
        result = {
            "api_base": self.query_one("#api_base", Input).value.strip(),
            "api_key": self.query_one("#api_key", Input).value.strip(),
            "api_model": self.query_one("#api_model", Input).value.strip(),
            "tavily_key": self.query_one("#tavily_key", Input).value.strip(),
            "use_dynamic": str(self.query_one("#use_dynamic", Switch).value).lower(),
            "search_provider": self.query_one("#search_provider", Select).value,
            "use_bm25": str(self.query_one("#use_bm25", Switch).value).lower(),
            "search_profile": self.query_one("#search_profile_select", Select).value,
            "persist": persist,
        }
        self.dismiss(result)

    @on(Button.Pressed, "#cancel")
    def action_cancel(self) -> None:
        self.dismiss(None)


class UserMessage(Static):
    """Widget to display user messages."""
    def __init__(self, text: str):
        super().__init__(f"[b]User:[/b] {text}", classes="user-message")

class AgentMessage(Static):
    """Widget to display agent messages."""
    def __init__(self, agent_name: str, text: str = ""):
        super().__init__(f"[b]{agent_name}:[/b] {text}", classes="agent-message")
        self.agent_name = agent_name
        self.text_content = text

    def append_text(self, new_text: str):
        self.text_content += new_text
        self.update(f"[b]{self.agent_name}:[/b] {self.text_content}")

class ProcessingWidget(Static):
    """Widget to display a processing indicator before the first response."""
    DOTS_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, agent_name: str = "Orchestrator"):
        super().__init__("", classes="agent-message")
        self.agent_name = agent_name
        self._frame = 0
        self._start_time = datetime.now()
        
    def on_mount(self) -> None:
        self._timer = self.set_interval(0.1, self._animate_dots)
        self._animate_dots()

    def _animate_dots(self) -> None:
        self._frame = (self._frame + 1) % len(self.DOTS_FRAMES)
        elapsed = datetime.now() - self._start_time
        self.update(f"[b]{self.agent_name}:[/b] {self.DOTS_FRAMES[self._frame]} ({elapsed.total_seconds():.1f}s)")

    def stop(self) -> None:
        self._timer.stop()
        self.remove()

class ToolCallWidget(Collapsible):
    """Widget to display a tool call and its result."""
    DOTS_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

    def __init__(self, tool_name: str, call_id: str, agent_name: str, is_subagent: bool = False):
        self.call_id = call_id
        self.tool_name = tool_name
        self.agent_name = agent_name
        self.args_text = ""
        self.result_text = ""
        self._done = False
        self._frame = 0
        self._start_time = datetime.now()
        
        self.args_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
        self.args_log.border_title = "Arguments"
        
        self.result_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
        self.result_log.border_title = "Result"
        
        title = f"\N{HAMMER AND WRENCH} \\[{agent_name}] {tool_name} {self.DOTS_FRAMES[0]}"
        level_class = "orchestrator-tool"
        if agent_name == "url_analyzer":
            level_class = "nested-subagent-tool"
        elif is_subagent:
            level_class = "subagent-tool"
            
        super().__init__(
            self.args_log,
            self.result_log,
            title=title,
            classes=f"tool-call {level_class}"
        )
        self._timer = self.set_interval(0.1, self._animate_dots)

    def _animate_dots(self) -> None:
        if self._done:
            self._timer.stop()
            return
        self._frame = (self._frame + 1) % len(self.DOTS_FRAMES)
        elapsed = datetime.now() - self._start_time
        self.title = f"\N{HAMMER AND WRENCH} \\[{self.agent_name}] {self.tool_name} {self.DOTS_FRAMES[self._frame]} ({elapsed.total_seconds():.1f}s)"

    def append_args(self, text: str):
        self.args_text += text
        self.args_log.clear()
        self.args_log.write(self.args_text)

    def set_result(self, text: str):
        self.result_text = text
        self.result_log.clear()
        self.result_log.write(self.result_text)
        self._done = True
        elapsed = datetime.now() - self._start_time
        self.title = f"\N{HAMMER AND WRENCH} \\[{self.agent_name}] {self.tool_name} \N{WHITE HEAVY CHECK MARK} ({elapsed.total_seconds():.1f}s)"

    def mark_stopped(self):
        """Mark the tool as stopped/cancelled."""
        self._done = True
        elapsed = datetime.now() - self._start_time
        self.title = f"\N{HAMMER AND WRENCH} \\[{self.agent_name}] {self.tool_name} \N{OCTAGONAL SIGN} ({elapsed.total_seconds():.1f}s)"


# ASCII art banner shown at startup
_BANNER = r"""
██╗      ██████╗  ██████╗ █████╗ ██╗                            
██║     ██╔═══██╗██╔════╝██╔══██╗██║                            
██║     ██║   ██║██║     ███████║██║                            
██║     ██║   ██║██║     ██╔══██║██║                            
███████╗╚██████╔╝╚██████╗██║  ██║███████╗                       
╚══════╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝╚══════╝                       
                                                                
██████╗ ███████╗███████╗███████╗ █████╗ ██████╗  ██████╗██╗  ██╗
██╔══██╗██╔════╝██╔════╝██╔════╝██╔══██╗██╔══██╗██╔════╝██║  ██║
██████╔╝█████╗  ███████╗█████╗  ███████║██████╔╝██║     ███████║
██╔══██╗██╔══╝  ╚════██║██╔══╝  ██╔══██║██╔══██╗██║     ██╔══██║
██║  ██║███████╗███████║███████╗██║  ██║██║  ██║╚██████╗██║  ██║
╚═╝  ╚═╝╚══════╝╚══════╝╚══════╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝
                                                                """

class DeepResearchApp(App):
    """LocalResearch — agent-driven search for local LLMs."""

    TITLE = "🔍 LocalResearch"
    
    CSS = """
    #chat-container {
        height: 1fr;
    }
    .user-message {
        margin: 1 2;
        padding: 1;
        background: $boost;
        color: $text;
        border: round $primary;
        text-align: right;
    }
    .agent-message {
        margin: 1 2;
        padding: 1;
        color: $text;
    }
    .tool-call {
        margin: 0 2 1 2;
    }
    .orchestrator-tool {
        border-left: vkey $primary;
    }
    .subagent-tool {
        border-left: vkey $secondary;
        margin: 0 2 1 6;
    }
    .nested-subagent-tool {
        border-left: vkey $accent;
        margin: 0 2 1 10;
    }
    Horizontal {
        height: auto;
    }
    RichLog {
        height: auto;
        max-height: 20;
        margin: 0 1;
        border: solid $surface-lighten-1;
    }
    #command-list {
        height: auto;
        max-height: 15;
        padding: 0 1;
        background: $panel;
        color: $text;
        border: solid $accent;
    }
    #bottom-bar {
        dock: bottom;
        height: auto;
    }
    #prompt-input {
        margin: 1 0;
    }
    .banner {
        color: $success;
        text-style: bold;
        padding: 1 2 0 2;
    }
    .banner-sub {
        color: $text-muted;
        padding: 0 2 1 2;
    }
    .file-viewer-wrapper {
        height: auto;
    }
    .file-viewer-collapsible {
        width: 1fr;
    }
    .file-viewer-inner {
        height: auto;
    }
    .title-copy-btn {
        dock: right;
        width: auto;
        height: 1;
        min-width: 3;
        border: none;
        background: transparent;
        color: $text-muted;
        padding: 0;
        margin: 0 1 0 0;
    }
    .title-copy-btn:hover {
        color: $text;
        background: transparent;
    }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

    SLASH_COMMANDS = [
        ("/new", "Start fresh research"),
        ("/stop", "Stop current research"),
        ("/files", "Browse session files"),
        ("/configure", "Edit settings and API keys"),
        ("/help", "Show instructions"),
        ("/exit", "Quit application"),
    ]

    def __init__(self):
        super().__init__()
        self.client = None
        self.orchestrator = None
        self._has_run_research = False
        self._pending_new_query = None
        self._filtered_cmds: list[tuple[str, str]] = []
        self._cmd_index: int = 0
        self._file_picker_active: bool = False
        self._file_picker_files: list[str] = []
        self._is_searching: bool = False
        # State tracking for streams
        self.orchestrator_state = {"calls": {}, "current_call_id": None, "current_msg": None}
        self.subagent_state = {"calls": {}, "current_call_id": None, "current_msg": None}

    def _banner_widget(self) -> Static:
        """Build the startup banner with current config status."""
        cfg = app_config.cfg
        provider   = cfg.get("settings", {}).get("search_provider", "duckduckgo")
        dynamic    = cfg.get("settings", {}).get("use_dynamic_webpage_analysis", False)
        bm25       = cfg.get("settings", {}).get("use_bm25_hints", False)
        base_url   = cfg.get("api", {}).get("openai_base_url") or "https://api.openai.com/v1"
        model      = cfg.get("api", {}).get("openai_model", "local-model") or "local-model"
        profile    = cfg.get("search_profile", "default")
        lines = [
            _BANNER,
            "",
            "  Agent-driven search • optimized for local LLMs",
            "",
            f"  ✓ search profile  : {profile}",
            f"  ✓ search provider : {provider}",
            f"  ✓ dynamic analysis: {'on' if dynamic else 'off'}   "
            f"  bm25 hints: {'on' if bm25 else 'off'}",
            f"  ✓ LLM endpoint    : {base_url} ({model})",
            "",
            "  Ready! Type a query or /help for commands.",
        ]
        return Static("\n".join(lines), classes="banner", id="banner")

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="chat-container"):
            yield self._banner_widget()
        with Vertical(id="bottom-bar"):
            yield OptionList(id="command-list")
            yield Input(
                placeholder="Enter a research query... (or /help)",
                id="prompt-input"
            )
        yield Footer()

    def _generate_run_title(self, query: str) -> str:
        """Generate a short, filesystem-safe title from the query without any LLM call."""
        import re as _re
        title = _re.sub(r'[^a-z0-9\-]', '-', query[:60].lower().replace(' ', '-'))
        title = _re.sub(r'-+', '-', title).strip('-')
        return title[:60] if title else "research"

    def _initialize_agents(self) -> None:
        async def researcher_stream_callback_tui(update: AgentResponseUpdate):
            self.handle_agent_update(update, True)

        self.client, self.orchestrator = setup_agents(researcher_stream_callback_tui)

    def on_mount(self) -> None:
        self.query_one("#prompt-input").focus()
        self.query_one("#command-list").display = False
        self._initialize_agents()

    def _render_cmd_list(self) -> None:
        """Re-render the command list OptionList based on current filter/index."""
        panel = self.query_one("#command-list", OptionList)
        items = self._filtered_cmds
        if not items:
            panel.display = False
            return
        panel.clear_options()
        for i, (cmd, desc) in enumerate(items):
            panel.add_option(Option(f"{cmd}  {desc}", id=str(i)))
        panel.highlighted = self._cmd_index
        panel.display = True

    def _show_file_picker(self) -> None:
        """Show the file picker in the dropdown panel."""
        run_dir = os.environ.get("CURRENT_RUN_DIR")
        if not run_dir or not os.path.isdir(run_dir):
            self._file_picker_files = []
            self._file_picker_active = False
            return
        files = sorted(os.listdir(run_dir))
        if not files:
            self._file_picker_files = []
            self._file_picker_active = False
            return
        self._file_picker_files = files
        self._file_picker_active = True
        self._filtered_cmds = [
            (f, f"{os.path.getsize(os.path.join(run_dir, f))} bytes")
            for f in files
        ]
        self._cmd_index = 0
        self._render_cmd_list()

    def _display_file(self, filename: str, collapsed_by_default: bool = False) -> None:
        """Display a file's content in a collapsible widget within the chat."""
        run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
        filepath = os.path.join(run_dir, filename)
        if not os.path.exists(filepath):
            return
            
        chat_container = self.query_one("#chat-container", VerticalScroll)
        try:
            with open(filepath, "r") as f:
                content = f.read()
            from textual.containers import Vertical
            file_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
            copy_btn = Button("📋", id=f"copy-btn-{id(file_log)}", classes="title-copy-btn")
            copy_btn._file_content = content
            inner = Vertical(copy_btn, file_log, classes="file-viewer-inner")
            viewer = Collapsible(inner, title=f"\N{OPEN FILE FOLDER} {filename}", classes="file-viewer-collapsible")
            wrapper = Vertical(viewer, classes="tool-call file-viewer-wrapper")
            chat_container.mount(wrapper)
            viewer.collapsed = collapsed_by_default
            file_log.write(content)
        except Exception as e:
            chat_container.mount(AgentMessage("System", f"Error reading {filename}: {e}"))
        chat_container.scroll_end(animate=False)

    @on(Button.Pressed, ".title-copy-btn")
    def on_copy_button(self, event: Button.Pressed) -> None:
        if hasattr(event.button, "_file_content"):
            self.app.copy_to_clipboard(event.button._file_content)
            btn = event.button
            btn.label = "✅"
            def reset():
                btn.label = "📋"
            self.set_timer(2.0, reset)

    def _open_selected_file(self) -> None:
        """Open the currently selected file in the file picker."""
        if not self._file_picker_active or not self._file_picker_files:
            return
        filename = self._filtered_cmds[self._cmd_index][0]
        self._display_file(filename)
        
        # Close the picker
        self._file_picker_active = False
        self._filtered_cmds = []
        self._render_cmd_list()

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._file_picker_active:
            # While in file picker, ignore input changes
            return
        val = event.value
        if val.startswith("/"):
            self._filtered_cmds = [
                (cmd, desc) for cmd, desc in self.SLASH_COMMANDS
                if cmd.startswith(val.lower())
            ]
            self._cmd_index = 0
            self._render_cmd_list()
        else:
            self._filtered_cmds = []
            self._render_cmd_list()

    def on_key(self, event: events.Key) -> None:
        panel = self.query_one("#command-list", OptionList)
        prompt_input = self.query_one("#prompt-input", Input)
        if panel.display and self._filtered_cmds and prompt_input.has_focus:
            if event.key == "down":
                self._cmd_index = min(self._cmd_index + 1, len(self._filtered_cmds) - 1)
                panel.highlighted = self._cmd_index
                event.prevent_default()
            elif event.key == "up":
                self._cmd_index = max(self._cmd_index - 1, 0)
                panel.highlighted = self._cmd_index
                event.prevent_default()
            elif event.key == "tab" and not self._file_picker_active:
                cmd, _ = self._filtered_cmds[self._cmd_index]
                prompt_input.value = cmd
                prompt_input.cursor_position = len(cmd)
                event.prevent_default()
            elif event.key == "escape":
                self._file_picker_active = False
                self._filtered_cmds = []
                self._render_cmd_list()
                event.prevent_default()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._file_picker_active:
            self._open_selected_file()
            event.input.value = ""
            return

        panel = self.query_one("#command-list", OptionList)
        if panel.display and self._filtered_cmds:
            query = self._filtered_cmds[self._cmd_index][0]
        else:
            query = event.value.strip()
        # Hide the command list after submission
        self._filtered_cmds = []
        self._render_cmd_list()

        if not query:
            return
            
        event.input.value = ""
        
        if query.lower() == "/exit":
            self.exit()
            return
            
        chat_container = self.query_one("#chat-container", VerticalScroll)

        if self._pending_new_query is not None:
            if query.lower() in ("y", "yes"):
                query = self._pending_new_query
                self._pending_new_query = None
                self._has_run_research = False
                await chat_container.remove_children()
                await chat_container.mount(self._banner_widget())
                self.on_mount()
            else:
                self._pending_new_query = None
                await chat_container.mount(AgentMessage("System", "Cancelled new session."))
                chat_container.scroll_end(animate=False)
                return
        
        if query.lower() == "/new":
            self._has_run_research = False
            self._pending_new_query = None
            await chat_container.remove_children()
            await chat_container.mount(self._banner_widget())
            self.on_mount()
            return
            
        if query.lower() == "/stop":
            self.workers.cancel_all()
            # Stop all running tool spinners
            for widget in self.query("ToolCallWidget"):
                if not widget._done:
                    widget.mark_stopped()
            await chat_container.mount(AgentMessage("System", "Research stopped."))
            chat_container.scroll_end(animate=False)
            return

        if query.lower() == "/configure":
            def config_callback(result: dict | None) -> None:
                if result:
                    # Update API settings
                    app_config.cfg["api"]["openai_base_url"] = result["api_base"]
                    app_config.cfg["api"]["openai_model"] = result["api_model"] or "local-model"
                    app_config.cfg["settings"]["use_dynamic_webpage_analysis"] = result["use_dynamic"] == "true"
                    app_config.cfg["settings"]["search_provider"] = result.get("search_provider", "duckduckgo")
                    app_config.cfg["settings"]["use_bm25_hints"] = result.get("use_bm25", "false") == "true"
                    
                    # Update search profile in config
                    app_config.cfg["search_profile"] = result.get("search_profile", "default")
                    
                    # Also push API keys to env (for Tavily client etc.)
                    os.environ["OPENAI_API_BASE"] = result["api_base"]
                    os.environ["OPENAI_API_KEY"] = result["api_key"]
                    os.environ["OPENAI_MODEL"] = result["api_model"] or "local-model"
                    os.environ["TAVILY_API_KEY"] = result["tavily_key"]
                    
                    # Save to config.yaml if requested
                    if result.get("persist"):
                        app_config.save_config()
                        msg = "Configuration saved & persisted! Agents re-initialized."
                    else:
                        msg = "Configuration applied to current session! Agents re-initialized."
                    
                    self._initialize_agents()
                    
                    # Notify user
                    chat_container.mount(AgentMessage("System", msg))
                    chat_container.scroll_end(animate=False)

            self.push_screen(ConfigureScreen(), config_callback)
            return

        if query.lower() == "/files":
            self._show_file_picker()
            if not self._file_picker_active:
                await chat_container.mount(AgentMessage("System", "No active session or no files yet. Start a research query first."))
                chat_container.scroll_end(animate=False)
            return
            
        if query.lower() == "/help":
            await chat_container.mount(AgentMessage("System", "Available commands:\n/new - Start fresh research\n/stop - Stop current research\n/configure - Edit settings and API keys\n/files - Browse session files\n/exit - Quit application\n/help - Show this message\nAny other text will be processed as a research query."))
            chat_container.scroll_end(animate=False)
            return

        if self._is_searching:
            await chat_container.mount(AgentMessage(
                "System",
                "⚠️  A search is already in progress. "
                "Use [bold]/stop[/bold] to cancel it first, or wait for it to finish."
            ))
            chat_container.scroll_end(animate=False)
            return

        if self._has_run_research:
            self._pending_new_query = query
            await chat_container.mount(UserMessage(query))
            await chat_container.mount(AgentMessage("System", "Asking a new question will open a new session. Continue? (y/n)"))
            chat_container.scroll_end(animate=False)
            return

        self._has_run_research = True
            
        await chat_container.mount(UserMessage(query))
        chat_container.scroll_end(animate=False)
        
        # Generate a human-readable run folder name: timestamp-query-title
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_title = self._generate_run_title(query)
        run_dir = os.path.join("runs", f"{timestamp}-{query_title}")
        os.makedirs(run_dir, exist_ok=True)
        os.environ["CURRENT_RUN_DIR"] = run_dir
        log_prompt(query)
        
        # Reset state for a new run
        self.orchestrator_state = {"calls": {}, "current_call_id": None, "current_msg": None}
        self.subagent_state = {"calls": {}, "current_call_id": None, "current_msg": None}

        # Run the orchestrator in an async worker
        self._is_searching = True
        self.run_orchestrator(query)

    @work(exclusive=True)
    async def run_orchestrator(self, query: str) -> None:
        start_time = datetime.now()
        q = app_config.q

        quotas = {
            "delegate-research-task": {"used": 0, "limit": q("orchestrator", "delegate_research_task")},
            "write_todos": {"used": 0, "limit": q("orchestrator", "write_todos")},
            "read_todos": {"used": 0, "limit": q("orchestrator", "read_todos")},
            "write_file": {"used": 0, "limit": q("orchestrator", "write_file")},
            "read_file": {"used": 0, "limit": q("orchestrator", "read_file")},
        }
        
        from tools import tool_quotas_ctx
        token = tool_quotas_ctx.set(quotas)
        chat_container = self.query_one("#chat-container", VerticalScroll)
        processing_widget = ProcessingWidget("Orchestrator")
        await chat_container.mount(processing_widget)
        chat_container.scroll_end(animate=False)
        
        try:
            first_content = True
            stream = self.orchestrator.run(query, stream=True)
            await asyncio.sleep(0.1)
            async for update in stream:
                if first_content:
                    for c in update.contents:
                        if (c.type == "text" and c.text) or c.type == "function_call":
                            processing_widget.stop()
                            first_content = False
                            break
                # the generator is async and runs in the Textual event loop 
                # we can update the UI directly since we are on the main thread
                self.handle_agent_update(update, False)
            
            if first_content:
                processing_widget.stop()
            
            elapsed = datetime.now() - start_time
            chat_container = self.query_one("#chat-container", VerticalScroll)
            chat_container.mount(AgentMessage("System", f"Research completed in {elapsed.total_seconds():.1f} seconds."))
            chat_container.scroll_end(animate=False)
            
            self._display_file("final_report.md", collapsed_by_default=True)
        except Exception as e:
            self.log_error(str(e))
        finally:
            self._is_searching = False
            tool_quotas_ctx.reset(token)

    def handle_agent_update(self, update: AgentResponseUpdate, is_subagent: bool = False):
        state = self.subagent_state if is_subagent else self.orchestrator_state
        agent_name = getattr(update, "author_name", None) or ("Sub-Agent" if is_subagent else "Orchestrator")
        
        for content in update.contents:
            log_stream_content(agent_name, content)
            
        chat_container = self.query_one("#chat-container", VerticalScroll)
        # Only auto-scroll if user is already near the bottom
        near_bottom = chat_container.scroll_y >= (chat_container.max_scroll_y - 3)

        for content in update.contents:
            if content.type == "text" and content.text:
                if is_subagent:
                    # Suppress subagent text from pouring into the main chat console
                    continue
                    
                if not state["current_msg"]:
                    msg = AgentMessage(agent_name)
                    chat_container.mount(msg)
                    state["current_msg"] = msg
                state["current_msg"].append_text(content.text)
                chat_container.scroll_end(animate=False) if near_bottom else None
                
            elif content.type == "function_call":
                state["current_msg"] = None # Reset text msg so next text gets a new bubble
                if content.call_id:
                    state["current_call_id"] = content.call_id
                    widget = ToolCallWidget(content.name, content.call_id, agent_name, is_subagent)
                    state["calls"][content.call_id] = widget
                    chat_container.mount(widget)
                    if content.arguments:
                        widget.append_args(content.arguments)
                else:
                    call_id = state["current_call_id"]
                    if call_id and call_id in state["calls"] and content.arguments:
                        state["calls"][call_id].append_args(content.arguments)
                chat_container.scroll_end(animate=False) if near_bottom else None
                        
            elif content.type == "function_result":
                state["current_msg"] = None
                call_id = getattr(content, "call_id", None)
                if call_id and call_id in state["calls"]:
                    widget = state["calls"].pop(call_id)
                    widget.set_result(str(content.result))
                    # Note: We keep the tool widget mounted but removed from tracking dictionary
                chat_container.scroll_end(animate=False) if near_bottom else None

    def log_error(self, err_msg: str):
        chat_container = self.query_one("#chat-container", VerticalScroll)
        chat_container.mount(Static(f"[bold red]Error:[/bold red] {err_msg}", classes="agent-message"))
        chat_container.scroll_end(animate=False)


async def run_cli(prompt: str) -> None:
    from tools import tool_quotas_ctx
    import sys
    
    async def cli_subagent_callback(update: AgentResponseUpdate):
        agent_name = getattr(update, "author_name", None) or "Sub-Agent"
        for content in update.contents:
            log_stream_content(agent_name, content)
            if content.type == "function_call":
                if content.call_id:
                    sys.stdout.write(f"\n\033[93m[Subagent] Calling {content.name}...\033[0m\n")
            elif content.type == "function_result":
                sys.stdout.write(f"\033[92m[Subagent] Result -> {len(str(content.result))} chars\033[0m\n")

    client, orchestrator = setup_agents(cli_subagent_callback)

    q = app_config.q
    quotas = {
        "delegate-research-task": {"used": 0, "limit": q("orchestrator", "delegate_research_task")},
        "write_todos": {"used": 0, "limit": q("orchestrator", "write_todos")},
        "read_todos": {"used": 0, "limit": q("orchestrator", "read_todos")},
        "write_file": {"used": 0, "limit": q("orchestrator", "write_file")},
        "read_file": {"used": 0, "limit": q("orchestrator", "read_file")},
    }
    
    token = tool_quotas_ctx.set(quotas)
    print(f"\033[1mStarting research on:\033[0m {prompt}\n")
    start_time = datetime.now()
    try:
        stream = orchestrator.run(prompt, stream=True)
        async for update in stream:
            agent_name = getattr(update, "author_name", None) or "Orchestrator"
            for content in update.contents:
                log_stream_content(agent_name, content)
                if content.type == "text" and content.text:
                    sys.stdout.write(content.text)
                    sys.stdout.flush()
                elif content.type == "function_call":
                    if content.call_id:
                        sys.stdout.write(f"\n\033[96m[Orchestrator] Calling {content.name}...\033[0m\n")
                elif content.type == "function_result":
                    pass # Keep it clean
        elapsed = datetime.now() - start_time
        print(f"\n\n\033[1mResearch completed in {elapsed.total_seconds():.1f} seconds.\033[0m")
    except Exception as e:
        print(f"\n\033[91mError:\033[0m {e}")
    finally:
        tool_quotas_ctx.reset(token)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="DeepResearch Agent CLI / TUI")
    parser.add_argument("--prompt", "-p", type=str, help="Run non-interactively with a specific prompt", default=None)
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        metavar="PATH",
        help="Path to an alternative config YAML file (default: src/config.yaml)",
    )
    args, unknown = parser.parse_known_args()

    # Re-load config now that we know the path (overrides the module-level load)
    app_config.load_config(path=args.config)

    if args.prompt:
        import re
        run_name = re.sub(r'[^a-z0-9\-]', '', args.prompt[:40].lower().replace(" ", "-"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join("runs", f"cli_{timestamp}_{run_name}")
        os.makedirs(run_dir, exist_ok=True)
        os.environ["CURRENT_RUN_DIR"] = run_dir
        log_prompt(args.prompt)
        
        asyncio.run(run_cli(args.prompt))
    else:
        app = DeepResearchApp()
        app.run()

