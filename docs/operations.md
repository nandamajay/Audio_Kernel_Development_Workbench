# AKDW Operations Guide

## Start / Restart

```bash
./run.sh
```

```bash
docker compose up -d --build akdw
```

## Health + Smoke Checks

```bash
curl -s http://localhost:5001/health
curl -s http://localhost:5001/api/dashboard/stats
curl -s http://localhost:5001/agent/ | head -n 20
curl -s http://localhost:5001/api/agent/stream/metrics
curl -s http://localhost:5001/api/terminal/audit?limit=5
```

## Route Verification Set

```bash
for page in / /agent/ /editor/ /patchwise/ /triage/ /upstream/ /settings/ /target-manager/; do
  code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:5001${page}")
  echo "${page} -> ${code}"
done
```

## GitHub Push Workflow

```bash
git status -sb
git add -A
git commit -m "<message>"
GIT_SSH_COMMAND='ssh -x' git push -u origin main
GIT_SSH_COMMAND='ssh -x' git push origin --tags
```

## Report Workflow

- Build report path: `/local/mnt/workspace/AKDW/report_v6.html`
- Include:
  - release hash/tag
  - section-level PASS/FAIL
  - regression score
  - enhancement recommendations

## Phase 7 Observability Endpoints

- Stream metrics: `GET /api/agent/stream/metrics`
  - `active_streams`, `reconnects_total`, `stream_errors`
  - `streams_started/completed`, `avg_duration_sec`, `max_duration_sec`
- Terminal command audit: `GET /api/terminal/audit`
  - filter by `session_id`
  - includes `command`, `exit_code`, `allowed`, `blocked_reason`

## Troubleshooting

- Sidebar item missing after deploy:
  - hard refresh browser (`Ctrl+Shift+R`)
  - rebuild + restart container
- `/target-manager/` returns 404:
  - verify runtime container includes `app/routes/target_manager.py`
  - ensure blueprint is registered in `app/routes/__init__.py`
- Agent failures with large files:
  - check `MAX_TOKENS_FOR_FILE` behavior in `app/services/agent_service.py`

## Runtime Notes

- Default app port: `5001`
- Health endpoint: `/health`
- DB file: `akdw.db`
