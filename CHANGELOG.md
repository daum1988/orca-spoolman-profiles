# Changelog – Spoolman Profiles (OrcaSlicer plugin)

Each section is the text OrcaSlicer shows as the release's changelog.
The newest version is at the top; keep the heading format `## [x.y.z] - YYYY-MM-DD`.

## [1.9.0] - 2026-11-20

### Changed
- Profile names contain the **material**: `<Vendor> <Material> <Name> (SM<id>) …`,
  e.g. "ELEGOO PLA Matte White (SM3)". Left out when the name already contains it
  ("ELEGOO RAPID PLA+ Black"). New setting **Profile name** with the placeholders
  `{vendor}`, `{material}`, `{name}`.

## [1.8.0] - 2026-11-20

### Added
- **Print time** in the statistics: in the period, all time (Moonraker's lifetime
  counter) and since the *Tool statistics since* date, average per finished print, and a
  duration column in the print history.

## [1.7.0] - 2026-11-20

### Added
- Print history: link to the **timelapse** of a print (moonraker-timelapse),
  with thumbnail. The video plays right in the tab; "Open in browser" is in the
  player.

## [1.6.0] - 2026-11-19

### Changed
- The tab with the profile update and the statistics is now called
  **Spools & Stats** (was "Spoolman Profiles"; the plugin keeps its name).
- The Statistics header also shows the filament since the *Tool statistics
  since* date and the INDX tool change counter.

## [1.5.0] - 2026-11-19

### Added
- Setting **Tool statistics since** (date the tool changer was installed): the
  per-tool grams and the tool changes ignore older prints, e.g. those of a
  previous single-nozzle toolhead. All other statistics still count every print.
- Setting **Show per-tool statistics and tool changes** (default on): turn it
  off on printers with a single tool.
- Filament card: all-time total, total since the *Tool statistics since* date
  and Moonraker's lifetime filament counter (metres).

### Changed
- Redesigned Spoolman Profiles tab: section headers with icons and status
  chip, KPI cards, period buttons, week chart with axis, materials as a donut,
  tool badges, status chips in the print history; works in light and dark mode.

## [1.4.0] - 2026-11-19

### Added
- Statistics **per tool** (T0–T8): grams and number of prints, with the colour
  and name of the spool assigned to each tool now.
- **Tool changes**: per period from the print files, plus the INDX counter
  (`toolchange_count`) of the printer.

## [1.3.0] - 2026-11-19

### Added
- **Statistics** in the Spoolman Profiles tab, read from Moonraker's print
  history and Spoolman (nothing is booked): filament used, prints, cost, low
  stock, use per week by colour, materials, most used filaments, spools running
  low and the print history. Period selectable (7 days to all time).
- Settings **Printer address (Moonraker)** and **Low stock below (g)**.

### Changed
- The Spoolman Profiles tab has two collapsible sections, Profiles and
  Statistics. Settings are only in File > Plugins > Config.

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
