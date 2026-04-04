#!/usr/bin/env python3
"""
benchmark.py — DeepResearch Bench runner for the research-assistant agent.

Drives the research-assistant backend through its WebSocket API to produce
output files compatible with the DeepResearch Bench evaluation framework
(https://github.com/Ayanami0730/deep_research_bench).

Flow per task
-------------
1. Open a fresh WebSocket connection to the backend.
2. Send a ``query`` message with the benchmark prompt.
3. Wait for the ``plan`` event and immediately auto-approve it.
4. Wait for the ``report`` event (final Markdown document).
5. Write the result in the format expected by the benchmark evaluator:
   {"id": "<task_id>", "prompt": "<prompt>", "article": "<markdown>"}

Usage
-----
    # Basic: run all 100 tasks (both languages) at depth=deep
    python scripts/benchmark.py \\
        --query-file /path/to/deep_research_bench/data/prompt_data/query.jsonl \\
        --output-file /path/to/deep_research_bench/data/test_data/raw_data/research-assistant.jsonl

    # Faster smoke run: 5 English tasks only, shallow depth, no concurrency
    python scripts/benchmark.py \\
        --query-file /path/to/query.jsonl \\
        --output-file /tmp/test.jsonl \\
        --lang en --limit 5 --research-depth shallow --concurrency 1

    # Resume after interruption (existing results are preserved and skipped)
    python scripts/benchmark.py \\
        --query-file /path/to/query.jsonl \\
        --output-file /path/to/output.jsonl \\
        --resume

Requirements
------------
Install before running (or activate the backend venv which already has them):
    pip install websockets tqdm

The backend and all MCP servers must be running before executing this script.
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

# Maximum number of connection-level retries per task.  Retries only fire for
# transient connectivity errors (server restart, OOM recovery, etc.) — not for
# application-level failures such as backend error events.
_MAX_CONNECT_RETRIES = 5
_RETRY_BACKOFF_BASE = 15  # seconds; doubles each retry

try:
    import websockets
    from websockets.exceptions import ConnectionClosed, WebSocketException
except ImportError:
    print(
        "ERROR: 'websockets' package not found.\n"
        "Activate the backend venv or run: pip install websockets",
        file=sys.stderr,
    )
    sys.exit(1)

try:
    from tqdm.asyncio import tqdm
except ImportError:
    print(
        "ERROR: 'tqdm' package not found.\n"
        "Activate the backend venv or run: pip install tqdm",
        file=sys.stderr,
    )
    sys.exit(1)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Core per-task runner
# ---------------------------------------------------------------------------


async def _run_single_attempt(
    task_id: str,
    prompt: str,
    ws_url: str,
    research_depth: str,
    task_timeout: float,
) -> dict:
    """
    One attempt at running a benchmark task.  Connection-level retries are
    handled by the caller (``run_single_task``).
    """
    result_base = {"id": task_id, "prompt": prompt}

    async with websockets.connect(
        ws_url,
        open_timeout=30,
        ping_interval=30,
        ping_timeout=60,
        max_size=50 * 1024 * 1024,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "query",
                    "content": prompt,
                    "research_depth": research_depth,
                }
            )
        )

        plan_id: Optional[str] = None
        plan_approved = False
        deadline = time.monotonic() + task_timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    **result_base,
                    "error": f"Timeout after {task_timeout:.0f}s",
                }

            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=min(remaining, 60))
            except asyncio.TimeoutError:
                continue

            msg = json.loads(raw)
            msg_type = msg.get("type", "")
            logger.debug("[%s] received: %s", task_id, msg_type)

            if msg_type == "plan" and not plan_approved:
                plan_data = msg.get("plan", {})
                plan_id = plan_data.get("id")
                if not plan_id:
                    return {
                        **result_base,
                        "error": "Received plan event with no plan ID",
                    }
                await ws.send(json.dumps({"type": "approve_plan", "planId": plan_id}))
                plan_approved = True
                logger.debug("[%s] plan approved (planId=%s)", task_id, plan_id)

            elif msg_type == "report":
                data = msg.get("data", {})
                document = data.get("document", "")
                if not document:
                    return {
                        **result_base,
                        "error": "Received report event with empty document",
                    }
                return {**result_base, "article": document}

            elif msg_type == "error":
                err_msg = msg.get("message", "Unknown error from backend")
                return {**result_base, "error": f"Backend error: {err_msg}"}

            elif msg_type == "plan_denied":
                return {
                    **result_base,
                    "error": f"Unexpected terminal event: {msg_type}",
                }


async def run_single_task(
    task_id: str,
    prompt: str,
    ws_url: str,
    research_depth: str,
    task_timeout: float,
) -> dict:
    """
    Run a single benchmark task with automatic retries on transient
    connection errors (server restarts / OOM recovery).

    Application-level errors (backend error events, empty reports, plan
    denials) are NOT retried — only connectivity failures.
    """
    result_base = {"id": task_id, "prompt": prompt}

    for attempt in range(1, _MAX_CONNECT_RETRIES + 1):
        try:
            return await _run_single_attempt(
                task_id, prompt, ws_url, research_depth, task_timeout
            )
        except (ConnectionClosed, WebSocketException, OSError) as exc:
            if attempt == _MAX_CONNECT_RETRIES:
                return {
                    **result_base,
                    "error": (
                        f"Connection failed after {_MAX_CONNECT_RETRIES} attempts: {exc}"
                    ),
                }
            backoff = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            logger.warning(
                "[%s] attempt %d/%d failed (%s); retrying in %ds…",
                task_id,
                attempt,
                _MAX_CONNECT_RETRIES,
                exc,
                backoff,
            )
            await asyncio.sleep(backoff)

    # Should never reach here, but satisfy the type checker.
    return {**result_base, "error": "Exhausted retries"}


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run_benchmark(
    tasks: list[dict],
    ws_url: str,
    research_depth: str,
    concurrency: int,
    task_timeout: float,
    output_file: Path,
    existing_ids: set[str],
) -> list[dict]:
    """
    Run all *tasks* concurrently (up to *concurrency* at a time).
    Writes each result to *output_file* as it completes.
    Returns the full list of new results.
    """
    remaining = [t for t in tasks if t["id"] not in existing_ids]
    if not remaining:
        logger.info("All tasks already completed. Nothing to do.")
        return []

    logger.info(
        "Running %d task(s) [depth=%s, concurrency=%d, timeout=%ds]",
        len(remaining),
        research_depth,
        concurrency,
        int(task_timeout),
    )

    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict] = []
    write_lock = asyncio.Lock()

    async def _run_and_save(task: dict) -> None:
        async with semaphore:
            task_id = task["id"]
            prompt = task["prompt"]
            result = await run_single_task(
                task_id=task_id,
                prompt=prompt,
                ws_url=ws_url,
                research_depth=research_depth,
                task_timeout=task_timeout,
            )
            status = "ok" if "article" in result else f"FAILED: {result.get('error')}"
            logger.info("[%s] %s", task_id, status)

            async with write_lock:
                results.append(result)
                # Append to file immediately so progress survives interruption.
                with open(output_file, "a", encoding="utf-8") as f:
                    f.write(json.dumps(result, ensure_ascii=False) + "\n")

    tasks_coros = [_run_and_save(t) for t in remaining]
    await tqdm.gather(*tasks_coros, desc="Benchmark tasks")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run DeepResearch Bench queries against the research-assistant backend.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--query-file",
        required=True,
        metavar="PATH",
        help="Path to deep_research_bench/data/prompt_data/query.jsonl",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        metavar="PATH",
        help=(
            "Destination JSONL file for results. "
            "Should be deep_research_bench/data/test_data/raw_data/<model_name>.jsonl"
        ),
    )
    parser.add_argument(
        "--ws-url",
        default="ws://localhost:9999/ws/research",
        metavar="URL",
        help="WebSocket URL of the research-assistant backend.",
    )
    parser.add_argument(
        "--research-depth",
        choices=["shallow", "moderate", "deep"],
        default="shallow",
        help="Research depth passed to the orchestrator for each task.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of research tasks to run simultaneously.",
    )
    parser.add_argument(
        "--task-timeout",
        type=int,
        default=43200,
        metavar="SECONDS",
        help="Per-task timeout in seconds (default: 12 hours).",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "zh", "both"],
        default="both",
        help="Which language subset of the benchmark to run.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Process at most N tasks (useful for testing).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        default=True,
        help=(
            "Skip tasks whose ID already appears in the output file "
            "(enabled by default; use --no-resume to force re-run)."
        ),
    )
    parser.add_argument(
        "--no-resume",
        dest="resume",
        action="store_false",
        help="Re-run all tasks even if the output file already contains results.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging for per-task WebSocket trace.",
    )
    return parser.parse_args()


def load_queries(query_file: Path, lang: str, limit: Optional[int]) -> list[dict]:
    """Load and filter benchmark query tasks from *query_file*."""
    tasks = []
    with open(query_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            tasks.append(json.loads(line))

    if lang != "both":
        tasks = [t for t in tasks if t.get("language") == lang]

    if limit is not None and limit > 0:
        tasks = tasks[:limit]

    return tasks


def load_existing_results(output_file: Path) -> tuple[list[dict], set[str]]:
    """Return (successful_results, successful_ids) from file if it exists.

    Only *successful* results (those with an ``article`` field) are returned.
    Failed results are discarded so the tasks can be retried on the next run.
    The output file is rewritten to contain only successful entries.
    """
    if not output_file.exists():
        return [], set()
    successful: list[dict] = []
    with open(output_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                if "article" in r:
                    successful.append(r)
            except json.JSONDecodeError:
                pass
    # Rewrite the file to contain only successful results, dropping failures
    # so they can be retried.
    with open(output_file, "w", encoding="utf-8") as f:
        for r in successful:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    ids = {r["id"] for r in successful if "id" in r}
    return successful, ids


def print_summary(all_results: list[dict]) -> None:
    """Print a summary of the run to stdout."""
    successful = [r for r in all_results if "article" in r]
    failed = [r for r in all_results if "error" in r]

    print("\n=== Benchmark Run Summary ===")
    print(f"Total results : {len(all_results)}")
    print(f"  Successful  : {len(successful)}")
    print(f"  Failed      : {len(failed)}")
    if failed:
        print("\nFailed tasks:")
        for r in failed:
            print(f"  [{r.get('id', '?')}] {r.get('error', 'unknown error')}")
    print("=============================\n")


async def main() -> int:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
        logger.setLevel(logging.DEBUG)

    query_file = Path(args.query_file)
    output_file = Path(args.output_file)

    if not query_file.exists():
        print(f"ERROR: query file not found: {query_file}", file=sys.stderr)
        return 1

    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Load queries
    tasks = load_queries(query_file, args.lang, args.limit)
    if not tasks:
        print("No tasks to process (check --lang / --limit settings).")
        return 0
    logger.info("Loaded %d task(s) from %s", len(tasks), query_file)

    # Load existing results for resume
    existing_results, existing_ids = [], set()
    if args.resume:
        existing_results, existing_ids = load_existing_results(output_file)
        if existing_ids:
            logger.info(
                "Resume mode: %d task(s) already completed, will skip them.",
                len(existing_ids),
            )
    elif output_file.exists():
        # --no-resume: truncate the file before writing
        output_file.write_text("", encoding="utf-8")

    # Run
    new_results = await run_benchmark(
        tasks=tasks,
        ws_url=args.ws_url,
        research_depth=args.research_depth,
        concurrency=args.concurrency,
        task_timeout=float(args.task_timeout),
        output_file=output_file,
        existing_ids=existing_ids,
    )

    all_results = existing_results + new_results
    print_summary(all_results)

    print(f"Results written to: {output_file}")
    print(
        "\nNext step — run the benchmark evaluation (from the deep_research_bench directory):\n"
        f'  TARGET_MODELS=("{output_file.stem}") bash run_benchmark.sh'
    )

    # Exit non-zero if any task failed.
    failed_count = sum(1 for r in new_results if "error" in r)
    return 1 if failed_count > 0 else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
