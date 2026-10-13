#!/usr/bin/env bash
# Serve the FactoryPilot vision-language model on an AMD Instinct GPU (ROCm + vLLM).
#
# Run INSIDE the AMD Developer Cloud droplet's vLLM container (vLLM Quick Start image):
#   ssh root@<droplet-ip>
#   docker exec -it rocm /bin/bash
#   export VLLM_API_KEY=$(openssl rand -hex 24)   # keep this; it goes into the app's .env
#   bash /root/start_vllm.sh
#
# A single MI300X has 192 GB HBM3, so the 7B model leaves room for a large KV cache and high
# concurrency; set MODEL=Qwen/Qwen2.5-VL-72B-Instruct for the strongest reasoning on one GPU.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-VL-7B-Instruct}"
PORT="${PORT:-8000}"
MAX_LEN="${MAX_LEN:-32768}"
: "${VLLM_API_KEY:?Set VLLM_API_KEY first, e.g. export VLLM_API_KEY=\$(openssl rand -hex 24)}"

rocm-smi --showproductname 2>/dev/null | grep -iE "card|gpu" | head -3 || true

python -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --served-model-name factorypilot-vl \
  --host 0.0.0.0 \
  --port "$PORT" \
  --api-key "$VLLM_API_KEY" \
  --dtype bfloat16 \
  --max-model-len "$MAX_LEN" \
  --gpu-memory-utilization 0.90 \
  --limit-mm-per-prompt '{"image": 4}' \
  --enable-prefix-caching \
  --enable-auto-tool-choice --tool-call-parser hermes \
  > /tmp/vllm.log 2>&1 &

echo "vLLM starting (pid $!). Follow with: tail -f /tmp/vllm.log"
echo "Wait for 'Application startup complete', then from your laptop run:"
echo "  python scripts/check_endpoint.py data/samples/conveyor_c2_scratch.png"
