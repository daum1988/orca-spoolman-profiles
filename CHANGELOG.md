# Changelog – Spoolman Profiles (OrcaSlicer plugin)

Each section is the text OrcaSlicer shows as the release's changelog.
The newest version is at the top; keep the heading format `## [x.y.z] - YYYY-MM-DD`.

## [1.2.0] - 2026-11-19

### Added
- Setting **Layout**: two tabs (Spoolman + Spoolman Profiles), only the
  Spoolman tab (buttons through the command "Update profiles from Spoolman"
  as a window) or only the Spoolman Profiles tab (Spoolman opens in the
  browser). Applies after restarting OrcaSlicer.

### Changed
- File > Plugins > Config: only the entry "Update profiles from Spoolman"
  holds the settings and is listed first. The tab entries point to it.

## [1.1.0] - 2026-11-19

### Added
- Tab **Spoolman Profiles** with the update button, status line and all
  settings. The command "Update profiles from Spoolman" opens the same as a
  window.
- All settings can be edited in File > Plugins > Config; tab, window and
  config stay in sync.

### Fixed
- The Spoolman tab showed "500 Internal Error": inside a frame, OrcaSlicer's
  web view blocked Spoolman's browser storage. The tab now loads Spoolman
  directly.
- Save in File > Plugins > Config did nothing (OrcaSlicer blocks form
  submits there).

## [1.0.0] - 2026-11-19

### Added
- Creates standalone OrcaSlicer filament profiles from the filaments in
  Spoolman: one per nozzle variant of the template
  "SM-Vorlage <Material> … @<printer> <size> nozzle", named
  "<Vendor> <Name> (SM<id>) …", filament ID `PSM<id>`. OrcaSlicer's filament
  sync finds them when the printer writes the same ID into Moonraker's
  `lane_data` (orca_lanes.py from the indx-trident-350 repo).
- Values from Spoolman: colour, temperatures, density, diameter, price, and
  extra fields named like an Orca setting, with optional nozzle suffix
  (`_d025`, `_d04`, `_d06`). Spoolio's tuning field names as a fallback.
- Profiles are written as a local preset bundle and reloaded without a
  restart; optional update when OrcaSlicer starts.
- Spoolman web UI in a tab.
