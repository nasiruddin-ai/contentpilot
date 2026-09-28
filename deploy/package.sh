#!/usr/bin/env bash
# Packs the project for upload to the server, leaving out secrets, dependencies and build output.
# Run on your PC (Git Bash on Windows) from the contentpilot folder: ./deploy/package.sh
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="../contentpilot-deploy.tar.gz"
tar -czf "$OUT" \
  --exclude="./.env" \
  --exclude="./backend/.env" \
  --exclude="./frontend/.env*" \
  --exclude="node_modules" \
  --exclude=".venv" \
  --exclude=".next" \
  --exclude="__pycache__" \
  --exclude=".pytest_cache" \
  --exclude="./backend/media" \
  --exclude="./backups" \
  .
echo "Created $(cd .. && pwd)/contentpilot-deploy.tar.gz ($(du -h "$OUT" | cut -f1))"
echo "It contains no .env file: secrets are created on the server."
