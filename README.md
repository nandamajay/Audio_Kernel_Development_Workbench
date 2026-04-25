# Audio Kernel Driver Workbench (AKDW)

AKDW is a Flask-based kernel development cockpit for patch review, triage, conversion, agent-assisted coding, upstream tracking, and target validation workflows in one UI.

## Documentation

- Docs Hub: [`docs/README.md`](./docs/README.md)
- Architecture: [`docs/architecture.md`](./docs/architecture.md)
- Operations: [`docs/operations.md`](./docs/operations.md)
- Diagrams: [`docs/diagrams/akdw-overview.mmd`](./docs/diagrams/akdw-overview.mmd)
- Screenshots: [`docs/screenshots/README.md`](./docs/screenshots/README.md)

## UI Preview

### Dashboard

![AKDW Dashboard](./docs/screenshots/dashboard-glassmorphism-2026-04-25.png)

### Code Editor (Terminal-IDE)

![AKDW Editor](./docs/screenshots/editor-terminal-ide-2026-04-25.png)

### QGenie Agent

![AKDW Agent](./docs/screenshots/agent-thinking-stream-2026-04-25.png)

### Upstream Tracker

![AKDW Upstream Tracker](./docs/screenshots/upstream-tracker-cards-2026-04-25.png)

## What You Get

- Terminal-IDE style `Code Editor` with Monaco + xterm + Agent mode
- `QGenie Agent` with session history, replay, token budget guard, and copy-response UX
- `Patch Workshop` for patch analysis and guided review flow
- `Upstream Tracker` for patch status tracking (lore/patchwork workflows)
- `Triage` and `Converter` modules for issue diagnosis and driver transformation
- `Target Manager` for device connection, validation runs, SSE logs, and replay
- Glassmorphic dashboard with live operational stats and recent activity resume

## Tech Stack

- Backend: Flask, Flask-SocketIO, SQLAlchemy, SQLite
- Frontend: Jinja2 templates, vanilla JS, Monaco, xterm.js, Split layouts
- Runtime: Docker / docker-compose

## Module Routes

- Dashboard: `/`
- Code Editor: `/editor/`
- Patch Workshop: `/patchwise/`
- Upstream Tracker: `/upstream/`
- Triage: `/triage/`
- QGenie Agent: `/agent/`
- Dual Agent: `/dual-agent/`
- Target Manager: `/target-manager/`
- Settings: `/settings/`
- Health: `/health`

## Quick Start

1. Clone repository.
2. Configure environment:

```bash
cp .env.example .env
```

3. Start AKDW:

```bash
./run.sh
```

4. Open:

```text
http://localhost:5001
```

Alternative launch:

```bash
docker compose up -d --build akdw
```

## Configuration

Common `.env` keys:

```bash
# QGenie
QGENIE_API_KEY=...
QGENIE_PROVIDER_URL=https://qgenie-chat.qualcomm.com/v1
QGENIE_DEFAULT_MODEL=claude-sonnet-4

# TLS / enterprise CA
QGENIE_SSL_VERIFY=true
QGENIE_CA_BUNDLE=/app/certs/qcom-ca-chain.crt

# Paths
KERNEL_SRC_PATH=/app/kernel
WORKSPACE_PATH=/app/workspace
```

## QGenie SSL (Internal CA)

If you hit `CERTIFICATE_VERIFY_FAILED`:

1. Place Qualcomm CA chain at `certs/qcom-ca-chain.crt`
2. Set:

```bash
QGENIE_SSL_VERIFY=true
QGENIE_CA_BUNDLE=/app/certs/qcom-ca-chain.crt
```

Temporary fallback only:

```bash
QGENIE_SSL_VERIFY=false
```

## Health and Validation

Service health:

```bash
curl -s http://localhost:5001/health
```

Representative API checks:

```bash
curl -s http://localhost:5001/api/dashboard/stats
curl -s -X POST http://localhost:5001/api/terminal/session
curl -s http://localhost:5001/api/editor/file?path=/app/kernel
```

## Data and Persistence

- Main DB: `akdw.db` (SQLite)
- Session persistence for Agent/Editor workflows
- Target validation history and replay entries
- Upstream patch records and dashboard activity logs

## Development Notes

- Frontend styles are centralized in `app/static/css/theme.css`
- Shared split-layout logic is in `app/static/js/resizable.js`
- Core route blueprints are registered in `app/routes/__init__.py`
- App factory is in `app/__init__.py`
- Architecture and runbooks live under `docs/`

## Troubleshooting

- `Target Manager` not visible in sidebar:
  - Hard refresh browser (`Ctrl+Shift+R`)
  - Confirm running image is up to date (`docker compose up -d --build akdw`)
- `Path not allowed` errors:
  - Verify mounted paths and `KERNEL_SRC_PATH`
- Agent empty/oversized request behavior:
  - Large attachments are chunked automatically with token headroom safeguards

## License

Internal project repository. Follow your organization policy for distribution and third-party dependency usage.
