#!/usr/bin/env bash
# DB のダンプを backups/ に取る（backups/ は Git 管理外）。
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
[ -f .env ] && . ./.env
set +a
: "${POSTGRES_USER:?POSTGRES_USER is not set}"
: "${POSTGRES_DB:?POSTGRES_DB is not set}"

mkdir -p backups
out="backups/backup-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" > "$out"
echo "backup written: $out"
