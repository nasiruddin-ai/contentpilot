#!/usr/bin/env bash
# Backs up the database and generated images into ./backups, keeping the last 14 days.
# Run from anywhere; schedule nightly with cron (see DEPLOY.md).
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f docker-compose.prod.yml)
STAMP="$(date +%Y%m%d-%H%M%S)"
mkdir -p backups
chmod 700 backups

# Custom-format dump: compressed, and restorable table by table if needed.
"${COMPOSE[@]}" exec -T postgres pg_dump -U contentpilot -Fc contentpilot > "backups/db-$STAMP.dump"
"${COMPOSE[@]}" exec -T api tar -czf - -C /data media > "backups/media-$STAMP.tar.gz"

find backups -type f -mtime +14 -delete
echo "Backup written: backups/db-$STAMP.dump and backups/media-$STAMP.tar.gz"
