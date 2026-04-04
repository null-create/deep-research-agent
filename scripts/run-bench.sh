#!/bin/bash

set -e

echo "Running the benchmark script with the provided arguments..."

# Run the benchmark script with the provided arguments
python scripts/benchmark.py --research-depth shallow \
  --concurrency 10 \
  --query-file ./data/prompt_data/query.jsonl \
  --output-file ./data/benchmarks/benchmark_results.jsonl
