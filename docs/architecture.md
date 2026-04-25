# AKDW Architecture

## High-Level View

```mermaid
flowchart LR
  U[Browser UI\nJinja + JS + Monaco + xterm] --> W[Flask App]
  W --> R[Routes\napp/routes/*]
  R --> S[Services\napp/services/*]
  S --> DB[(SQLite\nakdw.db)]
  S --> FS[/Workspace + Kernel FS/]
  S --> Q[QGenie Provider API]
  S --> L[lore.kernel.org / patchwork]
```

## Module Map

```mermaid
flowchart TD
  D[Dashboard] --> A[QGenie Agent]
  D --> E[Code Editor]
  D --> P[Patch Workshop]
  D --> U[Upstream Tracker]
  D --> T[Triage]
  D --> M[Target Manager]

  E --> API1[/api/editor/file]
  E --> API2[/api/terminal/*]
  A --> API3[/api/agent/chat + stream]
  P --> API4[/api/patchwise/*]
  U --> API5[/api/upstream/*]
  M --> API6[/target-manager/api/*]
```

## Agent Request Lifecycle

```mermaid
sequenceDiagram
  participant UI as Agent UI
  participant API as /api/agent/chat
  participant AS as AgentService
  participant QG as QGenie API
  participant DB as SQLite Session Bus

  UI->>API: POST message + attachments
  API->>AS: stream_chat(...)
  AS->>AS: tokenize + chunk large files
  AS->>QG: model request
  QG-->>AS: response
  AS->>DB: append user/assistant steps
  AS-->>API: normalized payload
  API-->>UI: friendly response + token usage
```

## Terminal-IDE Flow

```mermaid
sequenceDiagram
  participant UI as Editor/Terminal Panel
  participant TR as /api/terminal/session
  participant TS as TerminalService
  participant SH as PTY Bash

  UI->>TR: create terminal session
  TR->>TS: create_session(cwd)
  TS->>SH: spawn /bin/bash in PTY
  SH-->>TS: stdout/stderr stream
  TS-->>UI: SocketIO terminal:output
  UI->>TS: terminal:input / terminal:resize
  TS->>SH: write stdin / resize tty
```

## Data Model (Core)

- `Session` / `Message`: shared session bus for Agent/Editor history and replay.
- `UpstreamPatch`: external patch tracking and dashboard patch health.
- `Target` / `ValidationRun`: target registration, validation output, replayable history.
- `ActivityLog`: dashboard recent activity and operational traceability.

## Security and Guardrails

- Filesystem path access is constrained via `safe_path()` allowed roots.
- Terminal command filtering blocks destructive commands (`rm -rf /`, `shutdown`, `reboot`).
- Agent token guard chunks oversized attachments before model calls.
- TLS verification/CA bundle handling is configurable for enterprise endpoints.

## Known Integration Points

- QGenie provider URL and model config from environment/settings.
- lore/patchwork metadata fetch paths in Upstream Tracker.
- Optional ADB/Fastboot integration in Target Manager depending on runtime availability.
