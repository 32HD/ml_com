#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

MODE="${1:-safe}"
ENV_FILE=".env.ngc"
COMPOSE_FILE="docker-compose.ngc.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  cp .env.ngc.example "$ENV_FILE"
  sed -i "s/^UID=.*/UID=$(id -u)/" "$ENV_FILE"
  sed -i "s/^GID=.*/GID=$(id -g)/" "$ENV_FILE"
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

docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "${PROFILE_ARGS[@]}" up -d "$SERVICE"

echo "Started service: $SERVICE"
echo "Open shell:"
echo "  docker compose --env-file $ENV_FILE -f $COMPOSE_FILE exec $SERVICE bash"
