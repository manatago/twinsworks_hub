#!/usr/bin/env bash
# 未適用のマイグレーションを番号順に適用する。
set -euo pipefail
cd "$(dirname "$0")/.."

set -a
[ -f .env ] && . ./.env
set +a
: "${POSTGRES_USER:?POSTGRES_USER is not set}"
: "${POSTGRES_DB:?POSTGRES_DB is not set}"

psql_exec() {
  docker compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"
}

psql_exec -c "CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now());" >/dev/null

for f in db/migrations/*.sql; do
  version="$(basename "$f")"
  applied="$(psql_exec -t -A -c "SELECT 1 FROM schema_migrations WHERE version = '$version';")"
  if [ "$applied" = "1" ]; then
    echo "skip  $version"
    continue
  fi
  echo "apply $version"
  psql_exec < "$f"
  psql_exec -c "INSERT INTO schema_migrations (version) VALUES ('$version');" >/dev/null
done

echo "migrations done"
