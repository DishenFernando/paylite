#!/usr/bin/env bash
# Run on the server from a clone of your GitHub repository.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only origin main
docker compose -f compose.prod.yaml up -d --build
docker compose -f compose.prod.yaml ps
