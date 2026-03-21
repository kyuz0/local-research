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
app_config.load_config()

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
                    yield Input(value=os.environ.get("OPENAI_API_BASE", "http://localhost:8080/v1"), id="api_base")
                    
                    yield Label("OpenAI API Key", classes="config-label")
                    yield Input(value=os.environ.get("OPENAI_API_KEY", ""), password=True, id="api_key")
                    
                    yield Label("Tavily API Key", classes="config-label")
                    yield Input(value=os.environ.get("TAVILY_API_KEY", ""), password=True, id="tavily_key")
                    
                    use_dynamic = app_config.cfg["settings"]["use_dynamic_webpage_analysis"]
                    yield Label("Dynamic Web Page Analysis", classes="config-label")
                    yield Label("Reduces token usage with large pages, but might miss some information.", classes="config-hint")
                    with Horizontal(classes="switch-container"):
                        yield Label("Enable:", classes="switch-label")
                        yield Switch(value=use_dynamic, id="use_dynamic")
                        
                    yield Label("Search Provider", classes="config-label")
                    current_provider = app_config.cfg.get("settings", {}).get("search_provider", "duckduckgo")
                    yield Select(
                        (("DuckDuckGo", "duckduckgo"), ("Tavily", "tavily")),
                        value=current_provider,
                        id="search_provider"
                    )
                
                with TabPane("Orchestrator Quotas", id="tab-orch"):
                    yield Label("delegate-research-task", classes="config-hint")
                    yield Input(value=str(app_config.q("orchestrator", "delegate_research_task")), id="q_orch_delegate")
                    yield Label("write_todos / read_todos", classes="config-hint")
                    yield Input(value=str(app_config.q("orchestrator", "write_todos")), id="q_orch_todos")
                    yield Label("write_file / read_file", classes="config-hint")
                    yield Input(value=str(app_config.q("orchestrator", "write_file")), id="q_orch_files")
                
                with TabPane("Researcher Quotas", id="tab-res"):
                    yield Label("web_search", classes="config-hint")
                    yield Input(value=str(app_config.q("researcher", "web_search")), id="q_res_search")
                    yield Label("analyze_webpage", classes="config-hint")
                    yield Input(value=str(app_config.q("researcher", "analyze_webpage")), id="q_res_analyze")
                    yield Label("think_tool", classes="config-hint")
                    yield Input(value=str(app_config.q("researcher", "think_tool")), id="q_res_think")
                
                with TabPane("URL Analyzer Quotas", id="tab-url"):
                    yield Label("read_full_page", classes="config-hint")
                    yield Input(value=str(app_config.q("url_analyzer", "read_full_page")), id="q_url_readfull")
                    yield Label("grep_page", classes="config-hint")
                    yield Input(value=str(app_config.q("url_analyzer", "grep_page")), id="q_url_grep")
                    yield Label("read_page_chunk", classes="config-hint")
                    yield Input(value=str(app_config.q("url_analyzer", "read_page_chunk")), id="q_url_chunk")
                    yield Label("think_tool", classes="config-hint")
                    yield Input(value=str(app_config.q("url_analyzer", "think_tool")), id="q_url_think")
            
            with Horizontal(id="config-buttons"):
                yield Button("Save", variant="primary", id="save")
                yield Button("Cancel", id="cancel")
                
    @on(Button.Pressed, "#save")
    def action_save(self) -> None:
        result = {
            "api_base": self.query_one("#api_base", Input).value.strip(),
            "api_key": self.query_one("#api_key", Input).value.strip(),
            "tavily_key": self.query_one("#tavily_key", Input).value.strip(),
            "use_dynamic": str(self.query_one("#use_dynamic", Switch).value).lower(),
            "search_provider": self.query_one("#search_provider", Select).value,
            # Orchestrator
            "q_orch_delegate": self.query_one("#q_orch_delegate", Input).value.strip() or "3",
            "q_orch_todos": self.query_one("#q_orch_todos", Input).value.strip() or "15",
            "q_orch_files": self.query_one("#q_orch_files", Input).value.strip() or "5",
            # Researcher
            "q_res_search": self.query_one("#q_res_search", Input).value.strip() or "5",
            "q_res_analyze": self.query_one("#q_res_analyze", Input).value.strip() or "7",
            "q_res_think": self.query_one("#q_res_think", Input).value.strip() or "15",
            # URL Analyzer
            "q_url_readfull": self.query_one("#q_url_readfull", Input).value.strip() or "2",
            "q_url_grep": self.query_one("#q_url_grep", Input).value.strip() or "10",
            "q_url_chunk": self.query_one("#q_url_chunk", Input).value.strip() or "10",
            "q_url_think": self.query_one("#q_url_think", Input).value.strip() or "8",
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


class DeepResearchApp(App):
    """Modern TUI for the Deep Research Agent."""
    
    TITLE = "🕵️ DeepResearch CLI"
    
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
        self._filtered_cmds: list[tuple[str, str]] = []
        self._cmd_index: int = 0
        self._file_picker_active: bool = False
        self._file_picker_files: list[str] = []
        # State tracking for streams
        self.orchestrator_state = {"calls": {}, "current_call_id": None, "current_msg": None}
        self.subagent_state = {"calls": {}, "current_call_id": None, "current_msg": None}

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="chat-container"):
            yield Static("Welcome to DeepResearch CLI. Enter your query below.", classes="agent-message")
        with Vertical(id="bottom-bar"):
            yield OptionList(id="command-list")
            yield Input(
                placeholder="Send a message... (Press Enter to submit)", 
                id="prompt-input"
            )
        yield Footer()

    async def _generate_run_title(self, query: str) -> str:
        """Ask the LLM to generate a short, filesystem-safe title from the query."""
        import re as _re
        try:
            client = OpenAIChatClient(
                base_url=app_config.cfg["api"]["openai_base_url"] or "http://localhost:8080/v1",
                api_key=app_config.cfg["api"]["openai_api_key"] or "dummy",
                model_id="local-model",
            )
            agent = client.as_agent(
                name="title_gen",
                instructions=(
                    "Generate a very short filename-safe title (3-5 words, lowercase, "
                    "separated by hyphens) that a human would immediately associate with "
                    "the given research query. Output ONLY the title, nothing else. "
                    "Example: 'best-italian-restaurants-nyc' or 'quantum-computing-overview'"
                ),
            )
            response = await agent.run(query)
            title = response.text.strip().strip('"').strip("'").lower()
            # Sanitize: keep only alphanumerics and hyphens
            title = _re.sub(r'[^a-z0-9\-]', '-', title)
            title = _re.sub(r'-+', '-', title).strip('-')
            return title[:60] if title else "research"
        except Exception:
            # Fallback: sanitize the query itself
            fallback = _re.sub(r'[^a-z0-9\-]', '-', query[:40].lower())
            fallback = _re.sub(r'-+', '-', fallback).strip('-')
            return fallback or "research"

    def _initialize_agents(self) -> None:
        base_url = app_config.cfg["api"]["openai_base_url"] or "http://localhost:8080/v1"
        api_key = app_config.cfg["api"]["openai_api_key"] or "dummy"
            
        # Initialize client to the vLLM or provider
        self.client = OpenAIChatClient(
            base_url=base_url,
            api_key=api_key,
            model_id="local-model",
            function_invocation_configuration={"max_iterations": 20}
        )

        # Textual worker boundary needs call_from_thread if this is called on a different thread
        # The OpenAIChatClient async caller will trigger this in the same asyncio event loop
        # so we can just update the UI natively, but to be safe we use call_from_thread.
        async def researcher_stream_callback_tui(update: AgentResponseUpdate):
            self.handle_agent_update(update, True)

        use_dynamic = app_config.cfg["settings"]["use_dynamic_webpage_analysis"]
        webpage_tool = get_analyze_webpage_dynamic_tool(stream_callback=researcher_stream_callback_tui) if use_dynamic else analyze_webpage

        # Read per-tool quotas from config
        q = app_config.q
        q_res_search = q("researcher", "web_search")
        q_res_analyze = q("researcher", "analyze_webpage")
        q_res_think = q("researcher", "think_tool")
        q_orch_delegate = q("orchestrator", "delegate_research_task")
        q_orch_todos = q("orchestrator", "write_todos")
        q_orch_files = q("orchestrator", "write_file")

        research_agent = self.client.as_agent(
            name="research_agent",
            description="A specialized sub-agent for executing deep research operations given a specific topic or instruction.",
            instructions=RESEARCHER_INSTRUCTIONS.format(
                date=current_date,
                search_quota=q_res_search,
                analyze_quota=q_res_analyze,
                think_quota=q_res_think,
            ),
            tools=[web_search, webpage_tool, think_tool],
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
                    await researcher_stream_callback_tui(update)
                    for c in update.contents:
                        if c.type == "text" and c.text:
                            final_text += c.text
                return final_text
            finally:
                tool_quotas_ctx.reset(token)

        self.orchestrator = self.client.as_agent(
            name="orchestrator",
            instructions=INSTRUCTIONS.format(
                orchestrator_quota=q_orch_delegate,
                orchestrator_todos_quota=q_orch_todos,
                orchestrator_files_quota=q_orch_files,
            ),
            tools=[delegate_research_task, write_todos, write_file, read_todos, read_file],
        )

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

    def _open_selected_file(self) -> None:
        """Open the currently selected file in the file picker."""
        if not self._file_picker_active or not self._file_picker_files:
            return
        filename = self._filtered_cmds[self._cmd_index][0]
        run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
        filepath = os.path.join(run_dir, filename)
        chat_container = self.query_one("#chat-container", VerticalScroll)
        try:
            with open(filepath, "r") as f:
                content = f.read()
            file_log = RichLog(wrap=True, markup=True, highlight=True, min_width=20)
            viewer = Collapsible(file_log, title=f"\N{OPEN FILE FOLDER} {filename}", classes="tool-call")
            chat_container.mount(viewer)
            viewer.collapsed = False
            file_log.write(content)
        except Exception as e:
            chat_container.mount(AgentMessage("System", f"Error reading {filename}: {e}"))
        # Close the picker
        self._file_picker_active = False
        self._filtered_cmds = []
        self._render_cmd_list()
        chat_container.scroll_end(animate=False)

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
        
        if query.lower() == "/new":
            await chat_container.remove_children()
            await chat_container.mount(Static("Welcome to DeepResearch CLI. Enter your query below.", classes="agent-message"))
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
                    app_config.cfg["api"]["openai_api_key"] = result["api_key"]
                    app_config.cfg["api"]["tavily_api_key"] = result["tavily_key"]
                    app_config.cfg["settings"]["use_dynamic_webpage_analysis"] = result["use_dynamic"] == "true"
                    app_config.cfg["settings"]["search_provider"] = result.get("search_provider", "duckduckgo")
                    
                    # Update quotas in config
                    app_config.cfg["quotas"]["orchestrator"]["delegate_research_task"] = int(result["q_orch_delegate"])
                    app_config.cfg["quotas"]["orchestrator"]["write_todos"] = int(result["q_orch_todos"])
                    app_config.cfg["quotas"]["orchestrator"]["read_todos"] = int(result["q_orch_todos"])
                    app_config.cfg["quotas"]["orchestrator"]["write_file"] = int(result["q_orch_files"])
                    app_config.cfg["quotas"]["orchestrator"]["read_file"] = int(result["q_orch_files"])
                    app_config.cfg["quotas"]["researcher"]["web_search"] = int(result["q_res_search"])
                    app_config.cfg["quotas"]["researcher"]["analyze_webpage"] = int(result["q_res_analyze"])
                    app_config.cfg["quotas"]["researcher"]["think_tool"] = int(result["q_res_think"])
                    app_config.cfg["quotas"]["url_analyzer"]["read_full_page"] = int(result["q_url_readfull"])
                    app_config.cfg["quotas"]["url_analyzer"]["grep_page"] = int(result["q_url_grep"])
                    app_config.cfg["quotas"]["url_analyzer"]["read_page_chunk"] = int(result["q_url_chunk"])
                    app_config.cfg["quotas"]["url_analyzer"]["think_tool"] = int(result["q_url_think"])
                    
                    # Also push API keys to env (for Tavily client etc.)
                    os.environ["OPENAI_API_BASE"] = result["api_base"]
                    os.environ["OPENAI_API_KEY"] = result["api_key"]
                    os.environ["TAVILY_API_KEY"] = result["tavily_key"]
                    
                    # Save to config.yaml
                    app_config.save_config()
                    
                    self._initialize_agents()
                    
                    # Notify user
                    chat_container.mount(AgentMessage("System", "Configuration saved! Agents re-initialized."))
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
            
        await chat_container.mount(UserMessage(query))
        chat_container.scroll_end(animate=False)
        
        # Generate a human-readable run folder name: timestamp-query-title
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        query_title = await self._generate_run_title(query)
        run_dir = os.path.join("runs", f"{timestamp}-{query_title}")
        os.makedirs(run_dir, exist_ok=True)
        os.environ["CURRENT_RUN_DIR"] = run_dir
        
        # Reset state for a new run
        self.orchestrator_state = {"calls": {}, "current_call_id": None, "current_msg": None}
        self.subagent_state = {"calls": {}, "current_call_id": None, "current_msg": None}

        # Run the orchestrator in an async worker
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
        try:
            stream = self.orchestrator.run(query, stream=True)
            async for update in stream:
                # the generator is async and runs in the Textual event loop 
                # we can update the UI directly since we are on the main thread
                self.handle_agent_update(update, False)
            
            elapsed = datetime.now() - start_time
            chat_container = self.query_one("#chat-container", VerticalScroll)
            chat_container.mount(AgentMessage("System", f"Research completed in {elapsed.total_seconds():.1f} seconds."))
            chat_container.scroll_end(animate=False)
        except Exception as e:
            self.log_error(str(e))
        finally:
            tool_quotas_ctx.reset(token)

    def handle_agent_update(self, update: AgentResponseUpdate, is_subagent: bool = False):
        state = self.subagent_state if is_subagent else self.orchestrator_state
        agent_name = getattr(update, "author_name", None) or ("Sub-Agent" if is_subagent else "Orchestrator")
        
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


if __name__ == "__main__":
    app = DeepResearchApp()
    app.run()
