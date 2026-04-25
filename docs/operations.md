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
