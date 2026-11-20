## Spoolman Profiles

Creates OrcaSlicer filament profiles from the filaments in your
[Spoolman](https://github.com/Donkie/Spoolman) server, and shows Spoolman in a tab.

Every Spoolman filament with an active spool gets one **standalone** profile per
nozzle variant of your template, named `<Vendor> <Name> (SM<id>) @<printer> <nozzle> nozzle`
with the filament ID `PSM<id>`. Because the profiles do not inherit from another
preset, OrcaSlicer's filament sync can match them by filament ID: if your printer
writes the same ID into Moonraker's `lane_data`, *Sync filaments* picks exactly the
right profile for every tool.

### Features
- **Update profiles from Spoolman** – one button (or automatically when OrcaSlicer
  starts). Profiles are written as a local preset bundle and reloaded without a restart.
- Values from Spoolman: colour, nozzle and bed temperature, density, diameter, price
  per kg.
- Per-filament tuning from Spoolman extra fields named like the Orca setting
  (`filament_flow_ratio`, `filament_max_volumetric_speed`, `pressure_advance`, …),
  optionally per nozzle size with the suffix `_d025`, `_d04`, `_d06`. Spoolio's
  field names (`flow_ratio`, `max_volumetric_speed`, `retraction_length`,
  `retraction_speed`) are read as a fallback.
- **Statistics** from Moonraker's print history and Spoolman (read only): filament
  used, print time, prints, cost, tool changes (incl. the INDX counter), use per week by colour,
  materials, most used filaments, grams per tool, spools running low and the print
  history. Period selectable.
- **Spoolman tab** with the Spoolman web UI, and a **Spools & Stats** tab with
  collapsible sections for the profiles and the statistics. Layout selectable;
  all settings in File → Plugins → Config.

### Setup
1. In OrcaSlicer create one standalone filament per material as a template, named
   `SM-Vorlage <Material>` (e.g. `SM-Vorlage PLA`; the prefix is configurable),
   compatible with your printer, one variant per nozzle size.
2. *File → Plugins → Config → Update profiles from Spoolman*: enter your Spoolman
   address (e.g. `http://spoolman.local:7912`) and save.
3. Press **Update profiles from Spoolman**. The profiles appear in the filament list
   under *Bundle presets → Spoolman Profiles*.

### Filament sync with Klipper / Moonraker
The matching by filament ID needs the printer side to write `lane_data` with
`filament_id = PSM<id>` per tool. A ready-made script (`orca_lanes.py`) and the
full setup for a Voron Trident with Bondtech INDX are in
[daum1988/indx-trident-350](https://github.com/daum1988/indx-trident-350)
(docs/ORCASLICER.md). Without it, the plugin still creates the profiles; you then
pick them by hand.

Source, documentation and issues:
[daum1988/orca-spoolman-profiles](https://github.com/daum1988/orca-spoolman-profiles).

### More from this project
The plugin comes from
[daum1988/indx-trident-350](https://github.com/daum1988/indx-trident-350), a
Kalico/Klipper configuration for a Voron Trident 350 with a Bondtech INDX 9-tool
changer. Besides the OrcaSlicer integration it contains:
- **Spoolman, one spool per tool:** the active spool follows the tool, usage is
  booked to the right spool.
- **Pressure advance with the load cell:** `PA_CAL_LOADCELL` measures PA per tool
  without a test print and stores it in Spoolman, where this plugin picks it up.
- **Load/unload dialog** in Mainsail and KlipperScreen (tool, material, spool).
- **KlipperScreen add-on** with all tools and their spools.
- **Nozzle camera page** for the XY tool offsets.
- An OrcaSlicer **printer profile** for the machine.

It is a personal machine config shared as a reference, not a drop-in config;
parts of it (Spoolman per tool, `lane_data` for OrcaSlicer, the load dialog) can
be adapted to other multi-tool Klipper printers. Docs in English and German.
Questions about the printer side: on that repository.

Requires OrcaSlicer 2.5.0 or newer. License: GPL-3.0.

*This is my first project of this kind. The plugin was developed with the help of
AI (Anthropic's Claude) and has so far only been tested on my own printer with
OrcaSlicer 2.5.1. Bug reports are welcome on GitHub (orca-spoolman-profiles).*
