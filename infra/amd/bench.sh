#!/usr/bin/env bash
# Throughput / latency benchmark on the AMD GPU — numbers for the pitch and the in-app System tab.
# Run inside the droplet's vLLM container while start_vllm.sh is serving.
# Afterwards copy /tmp/bench/*.json into the repo's docs/benchmarks/ (scp root@<ip>:/tmp/bench/*.json docs/benchmarks/).
set -euo pipefail
MODEL="${MODEL:-Qwen/Qwen2.5-VL-7B-Instruct}"
: "${VLLM_API_KEY:?export VLLM_API_KEY first}"
mkdir -p /tmp/bench
for C in 1 8 32 64; do
  vllm bench serve \
    --backend openai-chat --endpoint /v1/chat/completions \
    --model "$MODEL" --served-model-name factorypilot-vl \
    --dataset-name random --random-input-len 1024 --random-output-len 256 \
    --num-prompts $((C * 8)) --max-concurrency "$C" \
    --header "Authorization=Bearer ${VLLM_API_KEY}" \
    --save-result --result-dir /tmp/bench --result-filename "c${C}.json" \
    | tee "/tmp/bench/c${C}.txt"
done
echo "Results in /tmp/bench — copy the .json files into docs/benchmarks/"
