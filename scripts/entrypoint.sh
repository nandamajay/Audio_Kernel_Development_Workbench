#!/usr/bin/env bash
# REUSED FROM (PATTERN): Q-Build-Manager/entrypoint.sh
set -euo pipefail

export PYTHONUNBUFFERED=1

python - <<'PY'
from app import create_app
from app.models import db

app = create_app()
with app.app_context():
    db.create_all()
print("AKDW DB initialized")
PY

exec python -m flask --app "app:create_app" run --host="0.0.0.0" --port="5001"
