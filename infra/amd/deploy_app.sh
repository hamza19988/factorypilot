#!/usr/bin/env bash
# Deploy the FactoryPilot web app on the same AMD droplet that serves the model,
# giving judges a live URL: http://<droplet-ip>:7860
#
#   ssh root@<droplet-ip>
#   git clone https://github.com/<you>/factorypilot && cd factorypilot
#   VLLM_API_KEY=<same key as start_vllm.sh> bash infra/amd/deploy_app.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
: "${VLLM_API_KEY:?export VLLM_API_KEY (the key vLLM was started with)}"

if [ ! -f .env ]; then
  cp .env.example .env
  sed -i "s|^VLLM_BASE_URL=.*|VLLM_BASE_URL=http://host.docker.internal:8000/v1|" .env
  sed -i "s|^VLLM_API_KEY=.*|VLLM_API_KEY=${VLLM_API_KEY}|" .env
  echo "Created .env (add your EVOLUS_* values, then re-run)."
fi

docker compose up -d --build app
sleep 4
curl -fsS localhost:7860/api/status | python3 -m json.tool | head -20
IP=$(curl -fsS ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
echo
echo "FactoryPilot is live at: http://${IP}:7860"
echo "Open port 7860 in the droplet firewall if it is not reachable."
