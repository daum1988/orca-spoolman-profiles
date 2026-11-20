<p align="center"><img src="assets/icon.png" alt="" width="96"></p>

# Spoolman Profiles for OrcaSlicer

[Deutsche Version → README.de.md](README.de.md)

An OrcaSlicer 2.5 plugin that creates filament profiles from the filaments in your
[Spoolman](https://github.com/Donkie/Spoolman) server, so that OrcaSlicer's filament
sync picks the right profile for every tool, and shows filament statistics from
your printer's history.

> This is my first project of this kind. So far it has only been tested on my own
> printer (Voron Trident 350 with Bondtech INDX, Klipper/Kalico, OrcaSlicer 2.5.1).
> The plugin was developed with the help of AI (Anthropic's Claude). Bug reports
> and hints are welcome as issues.

## Features

- **Filament profiles from Spoolman:** one *standalone* profile per Spoolman
  filament with an active spool and per nozzle variant of your template, named
  `<Vendor> <Material> <Name> (SM<id>) @<printer> <nozzle> nozzle` (format configurable), filament ID `PSM<id>`.
  Colour, nozzle and bed temperature, density, diameter and price come from
  Spoolman; everything else from your template.
- **Per-filament tuning in Spoolman:** extra fields named like the Orca setting
  (`filament_flow_ratio`, `filament_max_volumetric_speed`, `pressure_advance`, …),
  optionally per nozzle size (`_d025`, `_d04`, `_d06`). Spoolio's field names are
  read as a fallback.
- **No restart:** the profiles are a local preset bundle that the plugin reloads
  through Orca's plugin API; optional update when OrcaSlicer starts.
- **Filament sync:** the profiles do not inherit from another preset, so Orca
  matches them by filament ID when the printer reports `PSM<id>` per tool (see
  [Filament sync](#filament-sync-printer-side)).
- **Statistics** (read only) from Moonraker's print history and Spoolman:
  filament used (period, all time, since a date), prints, cost, tool changes,
  use per week by colour, materials, most used filaments, grams per tool,
  spools running low, print history with timelapse videos.
- **Spoolman web UI** in its own tab.

## Requirements

- OrcaSlicer **2.5.0** or newer (plugin system).
- A Spoolman server reachable from the PC.
- For the statistics: a Klipper printer with Moonraker (job history enabled, as
  by default). Timelapse links need
  [moonraker-timelapse](https://github.com/mainsail-crew/moonraker-timelapse).
- For the automatic filament sync: the printer side described below.

## Install

**From Orca Cloud:** subscribe to *Spoolman Profiles*, then in OrcaSlicer
*File → Plugins*, *Refresh*, tick *Activate*.

**Manually:** download `spoolman_profiles.py` (or the file for your system from
a release) and put it into its own folder:

| OS | Folder |
|---|---|
| Windows | `%APPDATA%\OrcaSlicer\orca_plugins\spoolman_profiles\` |
| macOS | `~/Library/Application Support/OrcaSlicer/orca_plugins/spoolman_profiles/` |
| Linux | `~/.config/OrcaSlicer/orca_plugins/spoolman_profiles/` |

Restart OrcaSlicer, *File → Plugins*, tick *Activate* for *Spoolman Profiles*.
Allow the prompts for network access (Spoolman, printer) and threads.

## Setup

1. **Templates (once):** in Orca create one *standalone* filament per material
   (*Create filament*, not a copy of a system profile), compatible with your
   printer, named `SM-Vorlage <Material>` – e.g. `SM-Vorlage PLA`,
   `SM-Vorlage PETG`. Orca makes one variant per nozzle size
   (`… @<printer> 0.4 nozzle`); each variant is used. The prefix is configurable.
2. **Settings:** *File → Plugins* → *Spoolman Profiles* → *Config* → entry
   **Update profiles from Spoolman** (the first one; the tab entries have no
   settings of their own):

   | Setting | |
   |---|---|
   | Spoolman address | e.g. `http://spoolman.local:7912` |
   | Printer address (Moonraker) | for the statistics, e.g. `http://printer.local` |
   | Low stock below (g) | threshold for "running low", default 100 |
   | Show per-tool statistics and tool changes | off for single-tool printers |
   | Tool statistics since | date your tool changer was installed (older prints are left out of the per-tool data) |
   | Extra link: label / address | optional button that opens a page, e.g. `XY calibration` / `{printer}/nozzle_cam.html` (`{printer}`, `{spoolman}` = the addresses above) |
   | Profile name | default `{vendor} {material} {name}`; the material is left out if the name already contains it |
   | Template prefix | default `SM-Vorlage` |
   | Update automatically when OrcaSlicer starts | default on |
   | Spoolio fallback | read Spoolio's field names too |
   | Also archived filaments … | default off |
   | Layout | two tabs (default), only the Spoolman tab, or only the Spools & Stats tab |

   Settings are stored locally (`<Orca data folder>/spoolman_profiles/settings.json`).
   The layout and the Spoolman tab address apply after restarting OrcaSlicer.
3. **Generate profiles:** tab **Spools & Stats** → **Update profiles from
   Spoolman**. The status line shows how many profiles were written and loaded.
   In the filament list they are under *Bundle presets → Spoolman Profiles*.

## Per-filament settings in Spoolman

Create extra fields for filaments in Spoolman (*Settings → Extra fields →
Filament*) whose key is exactly the Orca setting:

| Extra field | Orca setting |
|---|---|
| `filament_flow_ratio` | flow ratio |
| `filament_max_volumetric_speed` | max volumetric speed |
| `filament_retraction_length`, `filament_retraction_speed` | retraction |
| `fan_min_speed`, `fan_max_speed` | cooling fan |
| `filament_shrink`, `filament_shrinkage_compensation_z` | shrinkage in % (e.g. `99.5`) |
| `pressure_advance` | pressure advance (also turns on `enable_pressure_advance`) |

- Empty = template value. Unknown keys are reported as a warning.
- A suffix `_d025` / `_d04` / `_d06` applies only to that nozzle size and wins
  over the field without suffix.
- Spoolio fallback: `flow_ratio`, `max_volumetric_speed`, `retraction_length`,
  `retraction_speed` are used when the Orca field is missing.
- Do not edit the generated profiles in Orca; they are rewritten on every update.

## Filament sync (printer side)

OrcaSlicer's Moonraker printer agent reads the loaded filaments from Moonraker's
database namespace **`lane_data`**: one key per tool, `lane0` = T0 …,
for example

```json
{"lane": "0", "material": "PLA", "color": "#F9FDFF", "filament_id": "PSM11", "spool_id": 11}
```

When `filament_id` is `PSM<Spoolman filament id>`, the sync button in Orca's
*Filament* area picks exactly the profile created by this plugin. Orca only
matches standalone profiles (without "inherits") – that is why the plugin
creates them standalone.

Writing `lane_data` from the spool assigned to each tool is the printer's job.
A ready-made script (`orca_lanes.py`) with Klipper macros for one spool per
tool is in [daum1988/indx-trident-350](https://github.com/daum1988/indx-trident-350)
(`klipper/scripts/orca_lanes/`, docs in `docs/ORCASLICER.md`). Without it the
plugin still creates the profiles; you then pick them by hand.

In Orca, the printer preset needs *Printer agent = Moonraker*; details in the
docs linked above.

## Statistics

Section *Statistics* in the **Spools & Stats** tab, read from Moonraker's job
history and Spoolman; nothing is booked or changed:

- filament used in the period, all time and since the *Tool statistics since*
  date, plus Moonraker's lifetime counter (m);
- print time in the period, all time and since that date, average per finished print;
- prints and cancelled prints, cost from Spoolman prices;
- tool changes per period from the print files and, on Bondtech INDX, the
  printer's `toolchange_count`;
- use per week by colour, materials, most used filaments, spools running low;
- per tool T0–T8 (grams, prints, spool assigned now);
- print history with timelapse video (thumbnail, plays in the tab).

Grams per tool and tool changes are the slicer's values from the print files;
cancelled prints count with the printed share. Prints with profiles without
`(SM<id>)` count with material and colour from the print file, without cost.
The history is cached in `<Orca data folder>/spoolman_profiles/history_cache.json`.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Profiles not in the filament list | look under *Bundle presets → Spoolman Profiles* |
| Sync leaves a filament generic | the printer must report `PSM<id>`; delete older profiles with the same filament ID (two matches make the sync ambiguous) |
| "No template found" | create `SM-Vorlage <Material>` filaments (standalone) |
| Statistics: "Not reachable" | check the printer address (as in Mainsail/Fluidd) |
| Spoolman tab shows the old address | restart OrcaSlicer |

Log: `<Orca data folder>/spoolman_profiles/spoolman_profiles.log`.

## Using it with Spoolio

[Spoolio](https://github.com/danm1989/spoolio-orcaslicer) can run alongside, e.g.
to create spools. Keep *Configure OrcaSlicer Presets* off there, and if
Moonraker books the usage, set *When a Print Finishes* to *Do Not Track*.

## Releasing (maintainer)

Bump `# version` and `PLUGIN_VERSION` in `spoolman_profiles.py`, add a section
to `CHANGELOG.md`, commit, then publish a GitHub release with the tag
`v<version>`. The workflow `.github/workflows/publish-orcacloud.yml` builds the
files per OS (`python build.py`) and uploads them to Orca Cloud (needs the repo
variable `ORCACLOUD_PUBLISH=true` and the repo allowed in Orca Cloud). If Orca
Cloud answers `EDIT_CONFLICT` (plugin being edited or reviewed), it retries twice.
Orca Cloud can take several minutes to answer; the workflow waits up to 20 min.
If there is no answer at all, check the version on the plugin page before re-running.

A release only carries the files, the version and the changelog. The plugin
description in Orca Cloud is kept in [`orcacloud/description.md`](orcacloud/description.md);
when it changes, paste it on the plugin page in Orca Cloud by hand. Orca Cloud turns
every line break into a new line, so that file keeps each paragraph and list item
on one line. The changelog is sent as plain text (`build.py` strips the Markdown).

## License

GPL-3.0, see [LICENSE](LICENSE).
