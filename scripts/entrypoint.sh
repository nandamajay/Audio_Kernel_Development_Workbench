#!/usr/bin/env bash
# REUSED FROM (PATTERN): Q-Build-Manager/entrypoint.sh
set -euo pipefail

export PYTHONUNBUFFERED=1

mkdir -p \
  "${WORKSPACE_PATH:-/app/workspace}" \
  "${KERNEL_SRC_PATH:-/app/kernel}" \
  "${PATCHES_PATH:-/app/patches}" \
  "$(dirname "${SESSIONS_DB_PATH:-/app/sessions/akdw_sessions.db}")" \
  "${LOGS_PATH:-/app/workspace/logs}" \
  "${WORKSPACE_PATH:-/app/workspace}/workspace"

python - <<'PY'
from app import create_app
from app.models import db

app = create_app()
with app.app_context():
    db.create_all()
print("AKDW DB initialized")
PY

if [[ "$#" -gt 0 ]]; then
  exec "$@"
fi

exec python -m flask --app "app:create_app" run --host="0.0.0.0" --port="${FLASK_PORT:-5000}"
