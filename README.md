# LocalResearch

```
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
```

> An experimental deep-research agent built to learn and explore how agentic search actually works — and how different design decisions affect quality, speed, and cost.

---

## Table of contents

- [Inspiration & motivation](#inspiration--motivation)
- [Architecture](#architecture)
- [Design decisions](#design-decisions-worth-knowing-about)
- [Quick start](#quick-start)
- [Configuration](#configuration-srcconfigyaml)
- [Slash commands](#slash-commands-tui)
- [Evaluation](#evaluation-eval)
- [Project structure](#project-structure)


## What is this?

LocalResearch is a simple multi-agent research system I built to teach myself how to:

- Use the [Microsoft agent-framework](https://github.com/microsoft/agent-framework) with **agent delegation and sub-agents**
- Understand how **orchestrator → researcher → URL analyzer** delegation patterns work in practice
- Compare design tradeoffs: full-page context vs. grep-based chunked reading, BM25 pre-scoring, static vs. dynamic webpage analysis, and different search providers
- Run all of this efficiently on **local LLMs** with no cloud dependency (other than search)

Everything is designed to keep context windows small so it runs well on models served locally via `llama.cpp` or similar.

---

## Inspiration & motivation

This started from the [LangChain DeepAgents deep-research example](https://github.com/langchain-ai/deepagents/tree/main/examples/deep_research) — a nice reference implementation for how a multi-step research agent looks. I saw a few things I wanted to explore differently:

- **Framework** — I wanted to understand how the same patterns work with the [Microsoft agent-framework](https://github.com/microsoft/agent-framework), specifically its native delegation and sub-agent model.
- **Better delegation** — rather than a flat agent loop, I wanted a proper orchestrator that never searches directly, and a separate URL analyzer with its own scoped context.
- **Context management** — instead of dumping full page content into the model, use grep and chunk-based tools so the URL analyzer only reads what it needs. This matters a lot for local models with limited context windows.
- **Pluggable search backends** — swap between DuckDuckGo (free) and Tavily without changing any code.
- **Document support** — handle PDFs, DOCX, PPTX and other formats via `markitdown`, not just HTML.
- **Per-run folders** — every search run saves its fetched pages and a full action trace (`session_log.json`) to a timestamped folder, making it easy to audit what the agent did and why.
- **Search Profiles & Quotas** — you can switch between profiles (`shallow`, `default`, `deep`, or your own custom profiles) designed for different research depths. Every tool has a configurable call limit per run, and the orchestrator modifies its expectations based on the profile's configuration. This helps prevent agents from looping or over-spending on a single step, giving you complete control over the depth vs. speed tradeoff right from the UI or `config.yaml`.


## Architecture

```
Orchestrator
  └── delegate_research_task  ──►  Research Agent
                                        ├── web_search  (DuckDuckGo or Tavily)
                                        └── analyze_webpage
                                              ├── [static]  full page → LLM summary
                                              └── [dynamic] read_page_chunk / grep_page → targeted extraction
```

- **Orchestrator** plans the research and delegates tasks; it never searches directly.
- **Research Agent** searches the web and decides which URLs to investigate.
- **URL Analyzer** fetches a page and extracts the relevant content — either by reading the whole page (simple, higher token cost) or by grepping and chunking (more efficient, slightly less recall).
- **BM25 hints** (optional) pre-score page lines against the query, giving the URL analyzer a head start on where to look.

---

## Design decisions worth knowing about

| Decision | Tradeoff |
|---|---|
| Sub-agent delegation | Each agent has its own scoped context; the orchestrator never sees raw search results |
| grep-based page reading | Much smaller context at the cost of potentially missing implicit matches |
| BM25 line hints | Pre-scores page lines before the LLM reads anything; negligible overhead |
| Search Profiles & Quotas | Tune depth vs. speed with configurable limits per profile in `config.yaml` |
| Local-first | No OpenAI key needed; any OpenAI-compatible server works |
| DuckDuckGo default | Free, no API key; Tavily available for better snippet quality |
| PDF / document support | `markitdown` handles PDFs, DOCX, PPTX alongside HTML |

---

## Quick start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the TUI
python src/main.py

# Or run a single query non-interactively
python src/main.py --prompt "What caused the 2008 financial crisis?"

# Load an alternative config file
python src/main.py --config my_experiment.yaml
```

Set your API keys in `.env` or directly in `src/config.yaml`:

```env
OPENAI_API_BASE=http://localhost:8080/v1
OPENAI_API_KEY=                     # leave blank for local servers
TAVILY_API_KEY=tvly-...             # only needed if search_provider = tavily
```

---

## Configuration (`src/config.yaml`)

```yaml
api:
  openai_base_url: http://localhost:8080/v1
  openai_api_key: ''
  tavily_api_key: ''

settings:
  search_provider: duckduckgo   # duckduckgo | tavily
  use_dynamic_webpage_analysis: true
  use_bm25_hints: true

search_profile: default   # choose between shallow, default, deep, or custom profiles

profiles:                 # limit tool calls per run to control cost / context size
  default:
    description: "Balanced search with moderate depth, expect 2-3 solid sources per claim."
    quotas:
      orchestrator:
        delegate_research_task: 3
      researcher:
        web_search: 5
        analyze_webpage: 7
      url_analyzer:
        read_full_page: 1
        grep_page: 15
        read_page_chunk: 10
```

---

## Slash commands (TUI)

| Command | Description |
|---|---|
| `/new` | Start a fresh session |
| `/stop` | Cancel the running search |
| `/configure` | Open the settings dialog |
| `/files` | Browse files from the current run |
| `/help` | Show available commands |
| `/exit` | Quit |

---

## Evaluation (`eval/`)

> ⚠️ **Evaluation suite is a work in progress.** Results below are stubs; a full benchmark run is pending.

The evaluator runs the agent against a JSONL dataset of questions with weighted criteria and scores each report using the LLM-as-judge pattern.

```bash
# Run with default config
python eval/evaluate.py --limit 5 --runs 1

# Compare all variant combinations (2 providers × 2 dynamic × 2 bm25 × 3 profiles = 24 runs per question)
python eval/evaluate.py --all-variants --limit 5 --runs 1

# Override individual settings
python eval/evaluate.py --search-provider tavily --dynamic true --bm25 false --profile deep
```

Results are appended to `eval/results.jsonl` with full config metadata, so you can compare variants side-by-side.

| Variant | Avg Score | Avg Time |
|---|---|---|
| duckduckgo / static / no BM25 | — | — |
| duckduckgo / dynamic / BM25 | — | — |
| tavily / dynamic / BM25 | — | — |

---

## Project structure

```
src/
  main.py        # Textual TUI + CLI entrypoint
  tools.py       # All agent tools (web_search, analyze_webpage, …)
  config.py      # Config loader / saver
  config.yaml    # Default configuration
  prompts.py     # System prompts for all agents

eval/
  evaluate.py    # Evaluation harness
  dataset.jsonl  # Question + criteria dataset

runs/            # Per-run output dirs (reports, session logs, fetched pages)
```
