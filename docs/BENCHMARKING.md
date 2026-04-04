# Benchmarking — DeepResearch Bench

This document describes how to run the [DeepResearch Bench](https://github.com/Ayanami0730/deep_research_bench) evaluation against the research-assistant backend using `scripts/benchmark.py`.

---

## Overview

DeepResearch Bench evaluates deep research agents on 100 PhD-level tasks (50 English, 50 Chinese) across 22 topic domains. It measures two dimensions:

- **RACE** — report quality: comprehensiveness, insight, instruction-following, readability (compared against reference reports).
- **FACT** — citation accuracy and effective citations per task.

The benchmark runner (`scripts/benchmark.py`) handles Phase 1: submitting queries to the backend and collecting reports. Phase 2 (scoring) is run using the benchmark repo's own scripts.

---

## Prerequisites

1. **Clone the benchmark repo** alongside this one:

   ```bash
   git clone https://github.com/Ayanami0730/deep_research_bench
   ```

2. **Start the backend stack:**

   ```bash
   make run-all   # starts backend + all MCP servers
   ```

3. **Activate the backend venv** (provides `websockets` and `tqdm`):

   ```bash
   source backend/venv/bin/activate
   ```

---

## Phase 1 — Generate Reports

```bash
python scripts/benchmark.py \
  --query-file /path/to/deep_research_bench/data/prompt_data/query.jsonl \
  --output-file /path/to/deep_research_bench/data/test_data/raw_data/research-assistant.jsonl
```

Each completed task is written to the output file immediately. If the run is interrupted, re-running the same command will skip already-completed tasks (`--resume` is on by default).

### Key Options

| Flag | Default | Description |
|------|---------|-------------|
| `--ws-url` | `ws://localhost:9999/ws/research` | Backend WebSocket URL |
| `--research-depth` | `shallow` | `shallow` / `moderate` / `deep` |
| `--concurrency` | `3` | Concurrent tasks (hard ceiling: `MAX_CONCURRENT_PIPELINES=5`) |
| `--task-timeout` | `1800` | Per-task timeout in seconds |
| `--lang` | `both` | `en`, `zh`, or `both` |
| `--limit N` | off | Run only the first N tasks (useful for smoke testing) |
| `--resume` / `--no-resume` | resume on | Skip tasks already in the output file |
| `--verbose` | off | Log every WebSocket event per task |

### Smoke test (5 English tasks)

```bash
python scripts/benchmark.py \
  --query-file .../query.jsonl \
  --output-file /tmp/ra-test.jsonl \
  --lang en --limit 5 --research-depth shallow --concurrency 1
```

---

## Phase 2 — Evaluate

From inside the benchmark repo:

```bash
cd /path/to/deep_research_bench
export GEMINI_API_KEY="..."
export JINA_API_KEY="..."

TARGET_MODELS=("research-assistant") bash run_benchmark.sh
```

Results are written to:

- `results/race/research-assistant/race_result.txt`
- `results/fact/research-assistant/fact_result.txt`

---

## Output Format

Each line of the output JSONL must match the benchmark's expected format:

```json
{"id": "task_id", "prompt": "original query text", "article": "full markdown report with citations"}
```

The script writes this format directly. Failed tasks write `{"id": ..., "prompt": ..., "error": "..."}` — these are excluded from scoring by the benchmark evaluator.

---

## Runtime Estimates (v1 baseline — 2026-03-20)

Based on log analysis of AWS Bedrock Sonnet 4.6 sessions. Dominant bottleneck is LLM latency (P90 ≈ 48s/call), not Python overhead.

| Depth | Per-task | 100 tasks @ concurrency=3 | Notes |
|-------|----------|--------------------------|-------|
| `shallow` | ~15–25 min | ~8–10 hours | Extrapolated — no full run observed yet |
| `moderate` | ~40–70 min | ~20–25 hours | Log evidence: 57 min still running; prior run ~98 min |
| `deep` | ~60–90 min | ~35–45 hours | Extrapolated from moderate + QA overhead |

**Update this table** when a complete run is observed (look for per-task wall time from the tqdm progress bar or log timestamps).

---

## Architecture Notes

The script connects a fresh WebSocket per task and follows the standard session flow:

1. Send `{"type": "query", "content": "<prompt>", "research_depth": "<depth>"}`
2. Receive `session_created` → receive `plan` → send `{"type": "approve_plan", "planId": "<id>"}`
3. Wait for `{"type": "report", "data": {"document": "..."}}`

All intermediate events (`status`, `step_start`, `step_complete`, `research_complete`, `section_draft`) are silently consumed. Errors, timeouts, and early `plan_denied` / `research_stopped` events are captured and written as failure records.

Concurrency is managed with `asyncio.Semaphore(--concurrency)`. The backend enforces its own `MAX_CONCURRENT_PIPELINES=5` limit independently.
