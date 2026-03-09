#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-safe}"
ENV_FILE=".env.ngc"
COMPOSE_FILE="docker-compose.ngc.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing $ENV_FILE. Run scripts/ngc-up.sh first."
  exit 1
fi

case "$MODE" in
  safe)
    PROFILE_ARGS=()
    SERVICE="ngc-safe"
    ;;
  rdma)
    PROFILE_ARGS=(--profile rdma)
    SERVICE="ngc-rdma"
    ;;
  *)
    echo "Usage: $0 [safe|rdma]"
    exit 1
    ;;
esac

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]}" exec -T "$SERVICE" bash -lc '
set -e
echo "== GPU =="
nvidia-smi -L
echo
echo "== DPU/RDMA device nodes =="
ls -l /dev/infiniband || true
echo
echo "== RDMA tools =="
if command -v ibv_devinfo >/dev/null 2>&1; then
  ibv_devinfo -l
else
  echo "ibv_devinfo not found in image (install rdma-core if needed)."
fi
'
