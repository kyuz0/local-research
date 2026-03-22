import os
import sys
import json
import time
import yaml
import argparse
import subprocess
import tempfile
import collections
from itertools import product
from datetime import datetime

# Add src to python path to import config and LLM client
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))
import config as app_config
from agent_framework.openai import OpenAIChatClient


def get_latest_run_dir(base_dir="runs"):
    if not os.path.exists(base_dir):
        return None
    runs = [os.path.join(base_dir, d) for d in os.listdir(base_dir)
            if os.path.isdir(os.path.join(base_dir, d)) and d.startswith('cli_')]
    if not runs:
        return None
    return max(runs, key=os.path.getmtime)


def evaluate_report(client, query, criteria, report_text):
    prompt = f"""You are an expert evaluator assessing a research agent's final report.
User Query: {query}
Criteria to check:
{json.dumps(criteria, indent=2)}

Final Report:
{report_text}

Task: Evaluate if the report meets the criteria. Based on the weights provided in the criteria, calculate a final float score between 0.0 and 1.0.
1.0 means all criteria points are perfectly answered.
Output ONLY a valid JSON object with a single key "score" mapping to the float value. No markdown, no explanations.
"""
    try:
        agent = client.as_agent(
            name="evaluator",
            instructions="You are an expert evaluator. Always output valid JSON with a single 'score' key.",
            default_options={"temperature": 0.0}
        )
        import asyncio
        response = asyncio.run(agent.run(prompt))
        text = response.text.strip()
        # Clean up in case the LLM returned Markdown blocks
        if text.startswith('```json'): text = text[7:]
        if text.startswith('```'): text = text[3:]
        if text.endswith('```'): text = text[:-3]
        text = text.strip()
        result = json.loads(text)
        return float(result.get("score", 0.0))
    except Exception as e:
        print(f"Error evaluating report: {e}")
        return 0.0


def _expand_option(flag_value, true_val, false_val):
    """Expand 'all' into [true_val, false_val], or a single-element list."""
    if flag_value == "all":
        return [true_val, false_val]
    return [flag_value]


def build_variants(args, base_cfg):
    """Return a list of variant dicts based on CLI flags and the base config."""
    # Determine the sets to iterate for each dimension
    if args.all_variants:
        providers    = ["duckduckgo", "tavily"]
        dynamics     = [True, False]
        bm25s        = [True, False]
    else:
        # search_provider
        if args.search_provider == "all":
            providers = ["duckduckgo", "tavily"]
        elif args.search_provider is not None:
            providers = [args.search_provider]
        else:
            providers = [base_cfg.get("settings", {}).get("search_provider", "duckduckgo")]

        # use_dynamic_webpage_analysis
        if args.dynamic == "all":
            dynamics = [True, False]
        elif args.dynamic is not None:
            dynamics = [args.dynamic.lower() == "true"]
        else:
            dynamics = [base_cfg.get("settings", {}).get("use_dynamic_webpage_analysis", False)]

        # use_bm25_hints
        if args.bm25 == "all":
            bm25s = [True, False]
        elif args.bm25 is not None:
            bm25s = [args.bm25.lower() == "true"]
        else:
            bm25s = [base_cfg.get("settings", {}).get("use_bm25_hints", False)]

    variants = []
    for provider, dynamic, bm25 in product(providers, dynamics, bm25s):
        variants.append({
            "search_provider": provider,
            "use_dynamic_webpage_analysis": dynamic,
            "use_bm25_hints": bm25,
        })
    return variants


def write_variant_config(base_cfg, variant, tmp_dir):
    """Write a temporary YAML config for this variant and return its path."""
    cfg_copy = json.loads(json.dumps(base_cfg))  # deep copy via JSON
    cfg_copy.setdefault("settings", {})
    cfg_copy["settings"]["search_provider"] = variant["search_provider"]
    cfg_copy["settings"]["use_dynamic_webpage_analysis"] = variant["use_dynamic_webpage_analysis"]
    cfg_copy["settings"]["use_bm25_hints"] = variant["use_bm25_hints"]
    path = os.path.join(tmp_dir, "eval_variant.yaml")
    with open(path, "w") as f:
        yaml.dump(cfg_copy, f, default_flow_style=False, sort_keys=False)
    return path


def variant_key(variant):
    """A stable string key used for de-duplication in the results file."""
    return (
        variant["search_provider"],
        variant["use_dynamic_webpage_analysis"],
        variant["use_bm25_hints"],
    )


def main():
    parser = argparse.ArgumentParser(description="Evaluate deep research agent against a dataset")
    parser.add_argument("--dataset", type=str, default="eval/dataset.jsonl", help="Path to the JSONL dataset")
    parser.add_argument("--output",  type=str, default="eval/results.jsonl",  help="Path to output JSONL file")
    parser.add_argument("--model",   type=str, default="unknown",             help="Model name to include in results")
    parser.add_argument("--limit",   type=int, default=0,                     help="Limit number of queries to test")
    parser.add_argument("--runs",    type=int, default=3,                     help="Number of times to run each query per variant")
    parser.add_argument("--config",  "-c", type=str, default=None, metavar="PATH",
                        help="Base config YAML to use (default: src/config.yaml)")

    # Variant-selection flags
    parser.add_argument(
        "--search-provider",
        choices=["duckduckgo", "tavily", "all"],
        default=None,
        help="Search provider to use. 'all' runs both duckduckgo and tavily.",
    )
    parser.add_argument(
        "--dynamic",
        choices=["true", "false", "all"],
        default=None,
        help="use_dynamic_webpage_analysis setting. 'all' runs both True and False.",
    )
    parser.add_argument(
        "--bm25",
        choices=["true", "false", "all"],
        default=None,
        help="use_bm25_hints setting. 'all' runs both True and False.",
    )
    parser.add_argument(
        "--all-variants",
        action="store_true",
        help="Shorthand for --search-provider all --dynamic all --bm25 all (8 combinations).",
    )
    args = parser.parse_args()

    # Load base config (respects --config if given)
    app_config.load_config(path=args.config)
    base_cfg = app_config.cfg

    # Setup LLM client
    base_url = base_cfg.get("api", {}).get("openai_base_url") or "http://localhost:8080/v1"
    api_key  = base_cfg.get("api", {}).get("openai_api_key")  or "dummy"
    client   = OpenAIChatClient(base_url=base_url, api_key=api_key, model_id="local-model")

    model_name = args.model
    if model_name == "unknown":
        try:
            import urllib.request
            req = urllib.request.Request(f"{base_url}/models")
            if api_key and api_key != "dummy":
                req.add_header("Authorization", f"Bearer {api_key}")
            with urllib.request.urlopen(req, timeout=5) as response:
                models_data = json.loads(response.read().decode())
                if "data" in models_data and len(models_data["data"]) > 0:
                    model_name = models_data["data"][0].get("id", "unknown")
        except Exception as e:
            print(f"Warning: Could not auto-detect model from {base_url}/models: {e}")

    import re
    model_name = re.sub(r'-\d+-of-\d+\.gguf$', '', model_name)

    # Build variant list
    variants = build_variants(args, base_cfg)
    print(f"Variants to evaluate ({len(variants)} total):")
    for i, v in enumerate(variants, 1):
        print(f"  {i}. search_provider={v['search_provider']} | "
              f"dynamic={v['use_dynamic_webpage_analysis']} | "
              f"bm25={v['use_bm25_hints']}")

    # Load dataset
    dataset = []
    with open(args.dataset, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                dataset.append(json.loads(line))
    if args.limit > 0:
        dataset = dataset[:args.limit]
    print(f"\nLoaded {len(dataset)} items from {args.dataset}, model={model_name}\n")

    # Load existing run counts keyed by (prompt, model, search_provider, dynamic, bm25)
    existing_runs = collections.Counter()
    if os.path.exists(args.output):
        with open(args.output, 'r', encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try:
                        res = json.loads(line)
                        p   = res.get("prompt")
                        rc  = res.get("config", {})
                        m   = rc.get("model")
                        sp  = rc.get("search_provider")
                        dyn = rc.get("use_dynamic_webpage_analysis")
                        bm  = rc.get("use_bm25_hints")
                        if p and m:
                            existing_runs[(p, m, sp, dyn, bm)] += 1
                    except Exception:
                        pass

    # Main evaluation loop: variant → item → run
    with tempfile.TemporaryDirectory() as tmp_dir:
        for v_idx, variant in enumerate(variants):
            vk = variant_key(variant)
            print(
                f"\n{'='*70}\n"
                f"=== Variant {v_idx+1}/{len(variants)}: "
                f"search_provider={vk[0]} | dynamic={vk[1]} | bm25={vk[2]} ===\n"
                f"{'='*70}"
            )
            # Write temp config for this variant
            variant_cfg_path = write_variant_config(base_cfg, variant, tmp_dir)

            for idx, item in enumerate(dataset):
                query    = item.get("query")
                criteria = item.get("criteria", [])

                counter_key = (query, model_name, vk[0], vk[1], vk[2])
                runs_completed = existing_runs[counter_key]

                if runs_completed >= args.runs:
                    print(f"\n  [{idx+1}/{len(dataset)}] SKIP (already {runs_completed} runs): {query}")
                    continue

                for run_idx in range(runs_completed, args.runs):
                    print(f"\n  [{idx+1}/{len(dataset)}] Run {run_idx+1}/{args.runs}: {query}")

                    runs_before = set(os.listdir("runs")) if os.path.exists("runs") else set()
                    start_time  = time.time()

                    cmd = [
                        sys.executable, "src/main.py",
                        "--prompt", query,
                        "--config", variant_cfg_path,
                    ]
                    try:
                        subprocess.run(cmd, check=True, capture_output=True, text=True)
                    except subprocess.CalledProcessError as e:
                        print(f"  Agent run failed: {e.stderr[-500:] if e.stderr else e}")

                    end_time   = time.time()
                    time_taken = end_time - start_time

                    # Find new run directory
                    runs_after = set(os.listdir("runs")) if os.path.exists("runs") else set()
                    new_runs   = runs_after - runs_before
                    latest_run = None
                    if new_runs:
                        run_dirs = [os.path.join("runs", d) for d in new_runs
                                    if os.path.isdir(os.path.join("runs", d))]
                        if run_dirs:
                            latest_run = max(run_dirs, key=os.path.getmtime)
                    else:
                        latest_run = get_latest_run_dir("runs")

                    report_link = "Error: Run directory not found"
                    score = 0.0

                    if latest_run:
                        report_path = os.path.join(latest_run, "final_report.md")
                        if os.path.exists(report_path):
                            report_link = report_path
                            with open(report_path, 'r', encoding='utf-8') as rf:
                                report_text = rf.read()
                            print(f"  Found report at {report_path}, scoring...")
                            score = evaluate_report(client, query, criteria, report_text)
                        else:
                            report_link = f"Error: {report_path} not found"
                            print(f"  {report_link}")

                    print(f"  Score: {score:.2f} | Time: {time_taken:.1f}s")

                    result_entry = {
                        "timestamp":          datetime.now().isoformat(),
                        "prompt":             query,
                        "report_path":        report_link,
                        "score":              score,
                        "time_taken_seconds": round(time_taken, 2),
                        "run_index":          run_idx + 1,
                        "config": {
                            "use_dynamic_webpage_analysis": variant["use_dynamic_webpage_analysis"],
                            "use_bm25_hints":               variant["use_bm25_hints"],
                            "search_provider":              variant["search_provider"],
                            "model":                        model_name,
                        },
                    }

                    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
                    with open(args.output, 'a', encoding='utf-8') as out_f:
                        out_f.write(json.dumps(result_entry) + '\n')

                    existing_runs[counter_key] += 1

    print(f"\nEvaluation complete. Results saved to {args.output}")


if __name__ == "__main__":
    main()
