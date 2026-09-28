#!/usr/bin/env bash
# Creates .env for production from .env.production.example with fresh random secrets.
# Usage (from the contentpilot folder): ./deploy/init-env.sh contentpilot.example.com you@example.com
set -euo pipefail

cd "$(dirname "$0")/.."
DOMAIN="${1:?Usage: ./deploy/init-env.sh DOMAIN EMAIL}"
EMAIL="${2:?Usage: ./deploy/init-env.sh DOMAIN EMAIL}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN#http://}"
DOMAIN="${DOMAIN%/}"

if [ -e .env ]; then
  echo ".env already exists. Move it away first if you really want new secrets:" >&2
  echo "  (new secrets sign everyone out and disconnect every social account)" >&2
  exit 1
fi

random() { python3 -c "import secrets; print(secrets.token_$1($2))"; }
# A Fernet key is 32 random bytes, URL-safe base64 encoded.
fernet() { python3 -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

python3 - "$DOMAIN" "$EMAIL" "$(random hex 24)" "$(random urlsafe 48)" "$(fernet)" <<'PY'
import sys
from pathlib import Path

domain, email, db_password, jwt_secret, fernet_key = sys.argv[1:]
values = {
    "DOMAIN": domain,
    "ACME_EMAIL": email,
    "APP_URL": f"https://{domain}",
    "API_URL": f"https://{domain}",
    "POSTGRES_PASSWORD": db_password,
    "JWT_SECRET": jwt_secret,
    "TOKEN_ENCRYPTION_KEY": fernet_key,
}
lines = []
for line in Path(".env.production.example").read_text(encoding="utf-8").splitlines():
    key = line.split("=", 1)[0]
    if "=" in line and not line.startswith("#") and key in values:
        line = f"{key}={values[key]}"
    lines.append(line)
Path(".env").write_text("\n".join(lines) + "\n", encoding="utf-8")
PY
chmod 600 .env
echo "Created .env for https://$DOMAIN with new secrets."
echo "Next: nano .env  and fill in GOOGLE_AI_API_KEY and the FACEBOOK_* values."
