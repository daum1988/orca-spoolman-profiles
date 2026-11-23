<p align="center"><img src="assets/icon.png" alt="" width="96"></p>

# Spoolman Profiles für OrcaSlicer

[English version → README.md](README.md)

Ein Plugin für OrcaSlicer 2.5, das aus den Filamenten in deinem
[Spoolman](https://github.com/Donkie/Spoolman) Filamentprofile erzeugt, damit Orcas
Filamentabgleich für jedes Tool das richtige Profil wählt, und eine Statistik aus
der Druckhistorie des Druckers zeigt.

> Das ist mein erstes eigenes Projekt dieser Art. Getestet ist es bisher nur an
> meinem eigenen Drucker (Voron Trident 350 mit Bondtech INDX, Klipper/Kalico,
> OrcaSlicer 2.5.1). Das Plugin ist mit Hilfe von KI (Claude von Anthropic)
> entstanden. Fehlermeldungen und Hinweise gern als Issue.

## Funktionen

- **Filamentprofile aus Spoolman:** pro Spoolman-Filament mit aktiver Spule und pro
  Düsen-Variante der Vorlage ein *eigenständiges* Profil
  `<Hersteller> <Material> <Name> (SM<id>) @<Drucker> <Düse> nozzle` (Format einstellbar), Filament-ID `PSM<id>`.
  Farbe, Düsen- und Betttemperatur, Dichte, Durchmesser und Preis kommen aus
  Spoolman, alles andere aus der Vorlage.
- **Einstellungen pro Filament in Spoolman:** Zusatzfelder mit dem Namen der
  Orca-Einstellung (`filament_flow_ratio`, `filament_max_volumetric_speed`,
  `pressure_advance`, …), optional pro Düse (`_d025`, `_d04`, `_d06`). Spoolios
  Feldnamen werden als Ersatz gelesen.
- **Ohne Neustart:** Die Profile sind ein lokales Preset-Bundle, das das Plugin
  über Orcas Plugin-API neu lädt; auf Wunsch automatisch beim Start von Orca.
- **Filamentabgleich:** Die Profile erben von keinem anderen Preset. Orca findet
  sie deshalb über die Filament-ID, wenn der Drucker pro Tool `PSM<id>` meldet
  (siehe [Filamentabgleich](#filamentabgleich-druckerseite)).
- **Statistik** (nur lesend) aus Moonrakers Druckhistorie und Spoolman:
  verbrauchtes Filament (Zeitraum, gesamt, seit einem Datum), Drucke, Kosten,
  Toolwechsel, Verbrauch pro Woche nach Farbe, Materialien, meistgenutzte
  Filamente, Gramm pro Tool, fast leere Spulen, Druckhistorie mit Timelapse-Videos.
- **Spoolman-Weboberfläche** in einem eigenen Reiter.

## Voraussetzungen

- OrcaSlicer **2.5.0** oder neuer (Plugin-System).
- Ein Spoolman-Server, den der PC erreicht.
- Für die Statistik: ein Klipper-Drucker mit Moonraker (Druckhistorie aktiv, wie
  standardmäßig). Timelapse-Links brauchen
  [moonraker-timelapse](https://github.com/mainsail-crew/moonraker-timelapse).
- Für den automatischen Filamentabgleich: die unten beschriebene Druckerseite.

## Installation

**Aus der Orca Cloud:** *Spoolman Profiles* abonnieren, dann in OrcaSlicer
*Datei → Plugins*, *Refresh*, *Aktivieren* anhaken.

**Von Hand:** `spoolman_profiles.py` (oder die Datei für dein System aus einem
Release) in einen eigenen Ordner legen:

| System | Ordner |
|---|---|
| Windows | `%APPDATA%\OrcaSlicer\orca_plugins\spoolman_profiles\` |
| macOS | `~/Library/Application Support/OrcaSlicer/orca_plugins/spoolman_profiles/` |
| Linux | `~/.config/OrcaSlicer/orca_plugins/spoolman_profiles/` |

OrcaSlicer neu starten, *Datei → Plugins*, bei *Spoolman Profiles* *Aktivieren*
anhaken. Die Rückfragen nach Netzwerk (Spoolman, Drucker) und Threads erlauben.

## Einrichtung

1. **Vorlagen (einmalig):** In Orca pro Material ein *eigenständiges* Filament
   anlegen (*Filament erstellen*, keine Kopie eines System-Profils), kompatibel
   mit dem Drucker, Name `SM-Vorlage <Material>`, z. B. `SM-Vorlage PLA`,
   `SM-Vorlage PETG`. Orca legt pro Düsengröße eine Variante an
   (`… @<Drucker> 0.4 nozzle`); jede Variante wird genutzt. Das Präfix ist
   einstellbar.
2. **Einstellungen:** *Datei → Plugins* → *Spoolman Profiles* → *Config* →
   Eintrag **Update profiles from Spoolman** (der erste; die Einträge der Reiter
   haben keine eigenen Einstellungen):

   | Einstellung | |
   |---|---|
   | Spoolman address | z. B. `http://spoolman.local:7912` |
   | Printer address (Moonraker) | für die Statistik, z. B. `http://drucker.local` |
   | Low stock below (g) | Schwelle für „fast leer“, Standard 100 |
   | Show per-tool statistics and tool changes | bei Druckern mit einem Tool aus |
   | Tool statistics since | Datum des Toolwechsler-Umbaus (ältere Drucke zählen nicht pro Tool) |
   | Extra link: label / address | optionaler Knopf, der eine Seite öffnet, z. B. `XY calibration` / `{printer}/nozzle_cam.html` (`{printer}`, `{spoolman}` = die Adressen oben) |
   | Profile name | Standard `{vendor} {material} {name}`; das Material entfällt, wenn der Name es schon enthält |
   | Template prefix | Standard `SM-Vorlage` |
   | Update automatically when OrcaSlicer starts | Standard an |
   | Spoolio fallback | Spoolios Feldnamen mitlesen |
   | Also archived filaments … | Standard aus |
   | Layout | zwei Reiter (Standard), nur der Spoolman-Reiter oder nur Spools & Stats |

   Die Einstellungen liegen lokal (`<Orca-Datenordner>/spoolman_profiles/settings.json`).
   Layout und Adresse des Spoolman-Reiters gelten nach einem Neustart von Orca.
3. **Profile erzeugen:** Reiter **Spools & Stats** → **Update profiles from
   Spoolman**. Die Statuszeile zeigt, wie viele Profile geschrieben und geladen
   sind. In der Filamentliste stehen sie unter *Bundle presets → Spoolman Profiles*.

## Einstellungen pro Filament in Spoolman

In Spoolman Zusatzfelder für Filamente anlegen (*Einstellungen → Zusatzfelder →
Filament*), deren Schlüssel genau der Orca-Einstellung entspricht:

| Zusatzfeld | Orca-Einstellung |
|---|---|
| `filament_flow_ratio` | Flussrate |
| `filament_max_volumetric_speed` | max. Volumenstrom |
| `filament_retraction_length`, `filament_retraction_speed` | Retract |
| `fan_min_speed`, `fan_max_speed` | Lüfter |
| `filament_shrink`, `filament_shrinkage_compensation_z` | Schrumpfung in % (z. B. `99.5`) |
| `pressure_advance` | Pressure Advance (schaltet auch `enable_pressure_advance` ein) |

- Leer = Wert der Vorlage. Unbekannte Schlüssel meldet das Plugin als Warnung.
- Endung `_d025` / `_d04` / `_d06`: gilt nur für diese Düse und hat dort Vorrang.
- Spoolio-Ersatz: `flow_ratio`, `max_volumetric_speed`, `retraction_length`,
  `retraction_speed`, wenn das Orca-Feld fehlt.
- Die erzeugten Profile nicht in Orca bearbeiten; sie werden bei jedem Abgleich
  neu geschrieben.

## Filamentabgleich (Druckerseite)

Orcas Moonraker-Drucker-Agent liest die geladenen Filamente aus Moonrakers
Datenbank-Namespace **`lane_data`**: ein Schlüssel pro Tool, `lane0` = T0 …,
zum Beispiel

```json
{"lane": "0", "material": "PLA", "color": "#F9FDFF", "filament_id": "PSM11", "spool_id": 11}
```

Ist `filament_id` = `PSM<Spoolman-Filament-ID>`, wählt der Sync-Knopf im Bereich
*Filament* genau das Profil dieses Plugins. Orca vergleicht nur mit
eigenständigen Profilen (ohne „erbt von“), deshalb legt das Plugin sie so an.

`lane_data` aus der Spule pro Tool zu schreiben, ist Sache des Druckers. Ein
fertiges Skript (`orca_lanes.py`) mit Klipper-Makros für eine Spule pro Tool liegt
in [daum1988/indx-trident-350](https://github.com/daum1988/indx-trident-350)
(`klipper/scripts/orca_lanes/`, Doku in `docs/ORCASLICER.de.md`). Ohne das erzeugt
das Plugin trotzdem die Profile; du wählst sie dann von Hand.

In Orca braucht das Druckerprofil *Drucker-Agent = Moonraker*; Details in der
oben verlinkten Doku.

## Statistik

Abschnitt *Statistics* im Reiter **Spools & Stats**, gelesen aus Moonrakers
Druckhistorie und Spoolman; gebucht oder geändert wird nichts:

- verbrauchtes Filament im Zeitraum, gesamt und seit dem Datum *Tool statistics
  since*, dazu Moonrakers Gesamtzähler (m);
- Druckzeit im Zeitraum, gesamt und seit diesem Datum, Schnitt pro fertigem Druck;
- Drucke und abgebrochene Drucke, Kosten aus den Spoolman-Preisen;
- Toolwechsel pro Zeitraum aus den Druckdateien und beim Bondtech INDX der
  Zähler `toolchange_count` des Druckers;
- Verbrauch pro Woche nach Farbe, Materialien, meistgenutzte Filamente, fast
  leere Spulen;
- pro Tool T0–T8 (Gramm, Drucke, aktuell zugeordnete Spule);
- Druckhistorie mit Timelapse-Video (Vorschaubild, spielt im Reiter).

Gramm pro Tool und Toolwechsel sind die Werte des Slicers aus den Druckdateien;
abgebrochene Drucke zählen anteilig. Drucke mit Profilen ohne `(SM<id>)` zählen
mit Material und Farbe aus der Druckdatei, ohne Kosten. Die Historie wird in
`<Orca-Datenordner>/spoolman_profiles/history_cache.json` zwischengespeichert.

## Fehlersuche

| Symptom | Lösung |
|---|---|
| Profile nicht in der Filamentliste | unter *Bundle presets → Spoolman Profiles* nachsehen |
| Abgleich nimmt ein generisches Filament | der Drucker muss `PSM<id>` melden; ältere Profile mit derselben Filament-ID entfernen (zwei Treffer machen den Abgleich mehrdeutig): Orca schließen, nur ihre `.json` löschen und die `.info` liegen lassen, dann löscht Orca beim nächsten Start auch die Kopie in der Orca Cloud |
| „No template found“ | Filamente `SM-Vorlage <Material>` (eigenständig) anlegen |
| Statistik: „Not reachable“ | Druckeradresse prüfen (wie in Mainsail/Fluidd) |
| Spoolman-Reiter zeigt die alte Adresse | OrcaSlicer neu starten |

Log: `<Orca-Datenordner>/spoolman_profiles/spoolman_profiles.log`.

## Zusammen mit Spoolio

[Spoolio](https://github.com/danm1989/spoolio-orcaslicer) kann parallel laufen,
z. B. um Spulen anzulegen. Dort *Configure OrcaSlicer Presets* aus lassen, und wenn
Moonraker den Verbrauch bucht, *When a Print Finishes* auf *Do Not Track*.

## Lizenz

GPL-3.0, siehe [LICENSE](LICENSE).
