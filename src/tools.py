import os
import re
import httpx
import hashlib
import asyncio
from rank_bm25 import BM25Okapi as _BM25Okapi
from datetime import datetime
from urllib.parse import urlparse
from agent_framework import tool
from markdownify import markdownify
from markitdown import MarkItDown
from tavily import TavilyClient
from ddgs import DDGS
from agent_framework.openai import OpenAIChatClient

from prompts import URL_ANALYZER_INSTRUCTIONS, DYNAMIC_URL_ANALYZER_INSTRUCTIONS
import contextvars
import config as app_config

# Context variable mapping tool name -> {"used": int, "limit": int}
tool_quotas_ctx = contextvars.ContextVar('tool_quotas', default=None)

def check_quota(tool_name: str) -> str | None:
    """Check if the specific tool has exceeded its per-invocation quota."""
    ctx = tool_quotas_ctx.get()
    if ctx and tool_name in ctx:
        if ctx[tool_name]["used"] >= ctx[tool_name]["limit"]:
            return (
                f"Error: Quota reached. You have used the '{tool_name}' tool "
                f"{ctx[tool_name]['limit']} times out of your limit of {ctx[tool_name]['limit']}. "
                f"You MUST summarize what you've done and found so far, state clearly that you "
                f"had to stop due to quota limits, and return that summary."
            )
        ctx[tool_name]["used"] += 1
    return None

# Global client map or instantiator
_tavily_client = None

def get_tavily_client():
    global _tavily_client
    if _tavily_client is None:
        _tavily_client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", "dummy-if-mocked"))
    return _tavily_client

# Shared MarkItDown instance (stateless, safe to reuse)
_markitdown = MarkItDown()


def fetch_webpage_content(url: str, timeout: float = 10.0) -> str:
    """Fetch and convert webpage/document content to markdown.

    Supports HTML pages, PDFs, DOCX, PPTX, XLSX, and other formats via
    markitdown.  Falls back to httpx + markdownify for plain HTML if
    markitdown fails.

    Args:
        url: URL to fetch
        timeout: Request timeout in seconds

    Returns:
        Document content as markdown
    """
    # Primary path: markitdown handles HTML, PDF, DOCX, PPTX, XLSX, etc.
    try:
        result = _markitdown.convert(url)
        if result and result.text_content and result.text_content.strip():
            return result.text_content
    except Exception:
        pass  # fall through to legacy path

    # Fallback: plain HTTP fetch + HTML-to-markdown (original behaviour)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    try:
        response = httpx.get(url, headers=headers, timeout=timeout)
        response.raise_for_status()
        
        content_type = response.headers.get("Content-Type", "").lower()
        if "text/html" not in content_type and "text/plain" not in content_type:
            return f"Error: Fetched content is not text/html (Content-Type: {content_type}). Note: MarkItDown may have failed to parse this non-HTML file."
            
        return markdownify(response.text)
    except Exception as e:
        return f"Error fetching content from {url}: {str(e)}"


def _create_llm_client() -> OpenAIChatClient:
    """Create an OpenAI chat client using values from config."""
    return OpenAIChatClient(
        base_url=app_config.cfg["api"]["openai_base_url"] or "http://localhost:8080/v1",
        api_key=app_config.cfg["api"]["openai_api_key"] or "dummy",
        model_id="local-model"
    )


@tool(approval_mode="never_require")
def web_search(
    query: str,
    max_results: int = 5,
    topic: str = "general",
) -> str:
    """Search the web for information on a given query.

    Returns search results with titles, URLs, and snippets.

    Args:
        query: Search query to execute
        max_results: Maximum number of results to return (default: 5)
        topic: Topic filter - 'general', 'news', or 'finance' (default: 'general')

    Returns:
        Formatted search results with titles, URLs, and snippets
    """
    quota_error = check_quota("web_search")
    if quota_error:
        return quota_error
        
    try:
        def _sanitize_snippet(text: str) -> str:
            """Strip CSS, SVG, and HTML artifacts from search snippets."""
            # Remove SVG tags and their contents
            text = re.sub(r'<svg[\s\S]*?</svg>', '', text, flags=re.IGNORECASE)
            # Remove style tags and their contents
            text = re.sub(r'<style[\s\S]*?</style>', '', text, flags=re.IGNORECASE)
            # Remove any remaining HTML tags
            text = re.sub(r'<[^>]+>', '', text)
            # Remove CSS-like property blocks (e.g. gradientUnits=... stop-color=...)
            text = re.sub(r"(?:[\w-]+=(?:'[^']*'|\"[^\"]*\")[\s]*){3,}", '', text)
            # Remove URL-encoded SVG/CSS fragments
            text = re.sub(r'%3[CEce][^%\s]{10,}', '', text)
            # Collapse whitespace
            text = re.sub(r'\s+', ' ', text).strip()
            return text

        provider = app_config.cfg.get("settings", {}).get("search_provider", "duckduckgo")
        result_texts = []

        if provider == "duckduckgo" or provider not in ("duckduckgo", "tavily"):
            # Default/fallback: DuckDuckGo (free, no API key required)
            if topic == "news":
                search_results = DDGS().news(query, max_results=max_results)
                for result in search_results:
                    url = result.get("url", "")
                    title = result.get("title", "")
                    snippet = _sanitize_snippet(result.get("body", "No snippet available"))
                    result_texts.append(f"## {title}\n**URL:** {url}\n**Snippet:** {snippet}\n")
            else:
                search_results = DDGS().text(query, max_results=max_results)
                for result in search_results:
                    url = result.get("href", "")
                    title = result.get("title", "")
                    snippet = _sanitize_snippet(result.get("body", "No snippet available"))
                    result_texts.append(f"## {title}\n**URL:** {url}\n**Snippet:** {snippet}\n")
        elif provider == "tavily":
            # Tavily: requires TAVILY_API_KEY to be set
            search_results = get_tavily_client().search(
                query,
                max_results=max_results,
                topic=topic,
            )

            for result in search_results.get("results", []):
                url = result["url"]
                title = result["title"]
                snippet = _sanitize_snippet(result.get("content", "No snippet available"))
                result_texts.append(f"## {title}\n**URL:** {url}\n**Snippet:** {snippet}\n")

        # Format final response
        return f"🔍 Found {len(result_texts)} result(s) for '{query}':\n\n{chr(10).join(result_texts)}"
    except Exception as e:
        return f"Search failed: {str(e)}"


@tool(approval_mode="never_require")
async def analyze_webpage(
    upstream_query: str,
    specific_query: str,
    url: str,
) -> str:
    """Fetch a webpage and analyze its content to extract information related to the provided query.

    Args:
        upstream_query: The original query the orchestrator is working on
        specific_query: The specific query to look for in the page
        url: The URL to fetch content from

    Returns:
        A summary of the contents of the URL related to the provided query
    """
    quota_error = check_quota("analyze_webpage")
    if quota_error:
        return quota_error

    content = fetch_webpage_content(url)
    
    client = _create_llm_client()
    
    agent = client.as_agent(
        name="url_analyzer",
        instructions=URL_ANALYZER_INSTRUCTIONS.format(
            date=datetime.now().strftime("%Y-%m-%d"),
        ),
        default_options={"temperature": 0.0},
    )
    
    prompt = f"Upstream query: {upstream_query}\nSpecific query: {specific_query}\nURL: {url}\nPage Content:\n\n{content}\n"
    response = await agent.run(prompt)
    return response.text


def _slugify_url(url: str) -> str:
    """Turn a URL into a filesystem-safe slug like 'wikipedia_Artificial_intelligence'."""
    parsed = urlparse(url)
    domain = parsed.netloc.replace("www.", "").split(".")[0]
    path_part = parsed.path.strip("/").replace("/", "_")[:40]
    slug = f"{domain}_{path_part}" if path_part else domain
    return re.sub(r'[^a-zA-Z0-9_\-]', '', slug) or "page"


def _save_page_to_run_dir(url: str, content: str) -> None:
    """Save fetched page content to the run directory with URL metadata header."""
    run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
    url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
    filename = f"{_slugify_url(url)}_{url_hash}.md"
    metadata = (
        f"<!-- Source URL: {url} -->\n"
        f"<!-- Fetched: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} -->\n"
        f"<!-- Characters: {len(content)} | Lines: {len(content.splitlines())} -->\n\n"
    )
    try:
        with open(os.path.join(run_dir, filename), "w") as f:
            f.write(metadata + content)
    except Exception:
        pass


def _bm25_hint_lines(lines: list[str], query: str, top_n: int = 5, context: int = 4) -> str:
    """Return a formatted hint string with the top-N BM25-scored line numbers.

    Scores each line against the query tokens using BM25Okapi, then groups
    nearby hits into contiguous ranges and returns a one-line summary the
    url_analyzer prompt can include.

    Returns an empty string if rank_bm25 is not installed or no hits found.
    """
    if not lines:
        return ""

    tokenized = [re.findall(r'\w+', line.lower()) for line in lines]
    # BM25 needs non-empty rows; replace empty rows with a sentinel
    tokenized = [toks if toks else ["__empty__"] for toks in tokenized]
    bm25 = _BM25Okapi(tokenized)
    query_tokens = re.findall(r'\w+', query.lower())
    if not query_tokens:
        return ""

    scores = bm25.get_scores(query_tokens)
    # Pick top_n lines by score (1-indexed)
    top_indices = sorted(
        range(len(scores)), key=lambda i: scores[i], reverse=True
    )[:top_n]
    top_indices = [i for i in top_indices if scores[i] > 0]
    if not top_indices:
        return ""

    # Expand each hit by context lines and merge overlapping ranges
    ranges: list[tuple[int, int]] = []
    for idx in sorted(top_indices):
        lo = max(0, idx - context) + 1          # convert to 1-indexed
        hi = min(len(lines) - 1, idx + context) + 1
        if ranges and lo <= ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], max(ranges[-1][1], hi))
        else:
            ranges.append((lo, hi))

    hint_parts = ", ".join(f"lines {lo}-{hi}" for lo, hi in ranges)
    return f"**BM25 relevance hints** — focus first on: {hint_parts}"


def _build_url_analyzer_quotas() -> dict:
    """Build a fresh per-invocation quota dict for the dynamic URL analyzer."""
    return {
        "read_full_page": {"used": 0, "limit": app_config.q("url_analyzer", "read_full_page")},
        "grep_page": {"used": 0, "limit": app_config.q("url_analyzer", "grep_page")},
        "read_page_chunk": {"used": 0, "limit": app_config.q("url_analyzer", "read_page_chunk")},
        "think_tool": {"used": 0, "limit": app_config.q("url_analyzer", "think_tool")},
    }


async def _run_agent_with_quotas(agent, prompt: str, quotas: dict, stream_callback=None) -> str:
    """Run an agent within a quota-scoped context, optionally streaming updates."""
    token = tool_quotas_ctx.set(quotas)
    try:
        if stream_callback:
            final_text = ""
            stream = agent.run(prompt, stream=True)
            async for update in stream:
                if asyncio.iscoroutinefunction(stream_callback):
                    await stream_callback(update)
                else:
                    stream_callback(update)
                for c in update.contents:
                    if c.type == "text" and c.text:
                        final_text += c.text
            return final_text
        else:
            response = await agent.run(prompt)
            return response.text
    finally:
        tool_quotas_ctx.reset(token)


def get_analyze_webpage_dynamic_tool(stream_callback=None):
    @tool(approval_mode="never_require")
    async def analyze_webpage(
        upstream_query: str,
        specific_query: str,
        url: str,
    ) -> str:
        """Fetch a webpage and analyze its content dynamically using read/grep tools.
        
        Args:
            upstream_query: The original query the orchestrator is working on
            specific_query: The specific query to look for in the page
            url: The URL to fetch content from
            
        Returns:
            A summary of the contents of the URL related to the provided query
        """
        quota_error = check_quota("analyze_webpage")
        if quota_error:
            return quota_error
        content = fetch_webpage_content(url)
        _save_page_to_run_dir(url, content)
        lines = content.split('\n')
        
        @tool(approval_mode="never_require")
        def read_full_page() -> str:
            """Read the full raw markdown string of the page."""
            quota_error = check_quota("read_full_page")
            if quota_error: return quota_error
            return content
            
        @tool(approval_mode="never_require")
        def grep_page(pattern: str, context_lines: int = 2) -> str:
            """Search for a regex pattern across the page lines and return matching lines with context.
            
            Args:
                pattern: The regular expression string to search for
                context_lines: Number of lines to include before and after the match
            """
            quota_error = check_quota("grep_page")
            if quota_error: return quota_error
            
            try:
                regex = re.compile(pattern, re.IGNORECASE)
            except re.error as e:
                return f"Invalid regex pattern '{pattern}': {e}. Please fix the regex syntax."

            results = []
            for i, line in enumerate(lines):
                if regex.search(line):
                    start = max(0, i - context_lines)
                    end = min(len(lines), i + context_lines + 1)
                    chunk = []
                    for j in range(start, end):
                        chunk.append(f"{j + 1}: {lines[j]}")
                    results.append("\n".join(chunk))
                    results.append("-" * 40)
            
            if not results:
                return f"Pattern '{pattern}' not found."
            return "\n".join(results)
            
        @tool(approval_mode="never_require")
        def read_page_chunk(start_line: int, end_line: int) -> str:
            """Read a specific chunk of the page from start_line to end_line (1-indexed).
            
            Args:
                start_line: The starting line number (1-indexed)
                end_line: The ending line number (1-indexed)
            """
            quota_error = check_quota("read_page_chunk")
            if quota_error: return quota_error
            
            start = max(0, start_line - 1)
            end = min(len(lines), end_line)
            chunk = []
            for i in range(start, end):
                chunk.append(f"{i + 1}: {lines[i]}")
            return "\n".join(chunk)

        client = _create_llm_client()
        
        agent = client.as_agent(
            name="url_analyzer",
            instructions=DYNAMIC_URL_ANALYZER_INSTRUCTIONS.format(
                date=datetime.now().strftime("%Y-%m-%d"),
                read_full_page_quota=app_config.q("url_analyzer", "read_full_page"),
                grep_page_quota=app_config.q("url_analyzer", "grep_page"),
                read_page_chunk_quota=app_config.q("url_analyzer", "read_page_chunk"),
                think_quota=app_config.q("url_analyzer", "think_tool"),
            ),
            tools=[read_full_page, grep_page, read_page_chunk, think_tool],
            default_options={"temperature": 0.0},
        )
        
        bm25_hint = ""
        if app_config.cfg.get("settings", {}).get("use_bm25_hints", False):
            bm25_hint = _bm25_hint_lines(lines, specific_query)

        prompt = (
            f"Upstream query: {upstream_query}\n"
            f"Specific query: {specific_query}\n"
            f"URL: {url}\n\n"
            f"Page Properties:\n"
            f"- Total characters: {len(content)}\n"
            f"- Total lines: {len(lines)}\n"
        )
        if bm25_hint:
            prompt += f"\n{bm25_hint}\n"

        return await _run_agent_with_quotas(
            agent, prompt, _build_url_analyzer_quotas(), stream_callback
        )
            
    return analyze_webpage


@tool(approval_mode="never_require")
def think_tool(reflection: str) -> str:
    """Tool for SHORT strategic reflection on research progress and decision-making.

    Use this tool after each search to analyze extracted results and plan next steps systematically.
    This creates a deliberate pause in the research workflow for quality decision-making.

    This short reflection should briefly address:
    1. Analysis of current findings - What concrete information have I gathered?
    2. Gap assessment - What crucial information is still missing?
    3. Quality evaluation - Do I have sufficient evidence/examples for a good answer?
    4. Strategic decision - Should I continue searching or provide my answer?

    Ensure this short reflection doesn't end up being a full repetition and list of all the links and references, just the key items and insights to support decision making and evaluate the research progress.

    Args:
        reflection: Your SHORT reflection on research progress, findings, gaps, and next steps

    Returns:
        Confirmation that reflection was recorded for decision-making
    """
    quota_error = check_quota("think_tool")
    if quota_error: return quota_error
    
    return f"Reflection recorded: {reflection}"


@tool(approval_mode="never_require")
def write_todos(todos: str) -> str:
    """Write or update a todo list for the orchestrator task.

    Use this to track your plan and mark items as completed.
    Use markdown checkboxes so you can see progress at a glance:

        - [x] Completed task
        - [ ] Pending task
        - [ ] Another pending task

    Call read_todos() first to see the current list, then rewrite the
    full list with updated checkboxes when items are done.

    Args:
        todos: The full todo list string with checkboxes to save.
    """
    quota_error = check_quota("write_todos")
    if quota_error: return quota_error
    
    run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
    path = os.path.join(run_dir, "research_todos.md")
    with open(path, "w") as f:
        f.write(todos)
    return "Todos saved successfully to research_todos.md"


@tool(approval_mode="never_require")
def write_file(filename: str, content: str) -> str:
    """Write content to a file.

    Args:
        filename: The path to the file to write to
        content: The content to write
    """
    quota_error = check_quota("write_file")
    if quota_error: return quota_error
    
    # ensure it doesn't write out of boundaries
    safe_filename = filename.replace("../", "").split("/")[-1]
    run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
    path = os.path.join(run_dir, safe_filename)
    with open(path, "w") as f:
        f.write(content)
    return f"Content successfully written to {safe_filename}"

@tool(approval_mode="never_require")
def read_todos() -> str:
    """Read the current todo list to review progress.

    Use this before continuing work to see which tasks are done ([x])
    and which are still pending ([ ]).

    Returns:
        The contents of the research_todos.md file, or a message if it doesn't exist.
    """
    quota_error = check_quota("read_todos")
    if quota_error: return quota_error
    
    run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
    path = os.path.join(run_dir, "research_todos.md")
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()
    return "No todos have been saved yet."

@tool(approval_mode="never_require")
def read_file(filename: str) -> str:
    """Read content from a file in the current run directory.

    Args:
        filename: The path to the file to read from
    
    Returns:
        The content of the file, or an error message if it doesn't exist.
    """
    quota_error = check_quota("read_file")
    if quota_error: return quota_error
    
    safe_filename = filename.replace("../", "").split("/")[-1]
    run_dir = os.environ.get("CURRENT_RUN_DIR", ".")
    path = os.path.join(run_dir, safe_filename)
    if os.path.exists(path):
        with open(path, "r") as f:
            return f.read()
    return f"File {safe_filename} does not exist."
