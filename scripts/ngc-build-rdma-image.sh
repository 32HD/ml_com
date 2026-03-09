#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

BASE_IMAGE="${1:-nvcr.io/nvidia/pytorch:25.02-py3}"
TARGET_IMAGE="${2:-local/ngc-comms:25.02}"

docker build -f Dockerfile.ngc --build-arg BASE_IMAGE="$BASE_IMAGE" -t "$TARGET_IMAGE" .

echo "Built image: $TARGET_IMAGE"
