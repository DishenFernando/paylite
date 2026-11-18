#!/usr/bin/env bash
# Run on the server from the repository root. Backup contains sensitive user data.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a
# shellcheck disable=SC1091
source ./.env
set +a
mkdir -p ./backups
chmod 700 ./backups
file="./backups/paylite-$(date -u +%Y%m%dT%H%M%SZ).sql"
umask 077
docker compose -f compose.prod.yaml exec -T db \
  pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$file"
echo "Database backup written to $file"
