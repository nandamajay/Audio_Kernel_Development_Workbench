# AKDW Driver Converter v2 — Preflight (2026-04-29)

## Files inspected
- app/routes/converter.py
- app/templates/converter.html
- app/static/js/akdw_converter.js (not present)
- app/utils/driver_fetcher.py (not present)

## SQLite settings lookup
- Checked /local/mnt/workspace/AKDW/Audio_Kernel_Development_Workbench/akdw.db
- Table `settings` not found; SSL/CA settings not stored in SQLite.
- Will rely on runtime env (.env) via env_service/config for SSL verify + CA bundle.

## Notes
- Converter routes currently only render converter.html.
- Converter UI is placeholder; requires full redesign per prompt.
