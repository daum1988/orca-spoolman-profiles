# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Spoolman Profiles"
# description = "Creates standalone OrcaSlicer filament profiles (filament ID PSM<id>) from Spoolman and shows the Spoolman web UI in a tab."
# author = "indx-trident-350"
# version = "1.2.0"
# ///

"""Spoolman Profiles - OrcaSlicer 2.5 plugin for the Voron Trident 350 INDX setup.

Replaces orca/orca_spoolman_profiles.ps1. For every Spoolman filament with a
non-archived spool it creates one filament profile per nozzle variant of the
matching template "<prefix> <Material> ... @<printer> <size> nozzle":

    name         "<Vendor> <Name> (SM<id>) @<printer> <size> nozzle"
    filament_id  "PSM<id>"   (the printer writes the same ID into Moonraker's
                              lane_data, so Orca's filament sync picks this profile)

The profiles are root presets (no "inherits"), written as a local preset bundle
into the active Orca user folder (user/<account>/_local/spoolman-profiles/) and
reloaded without restarting Orca.

Spoolman extra fields named like an Orca setting override the template, with an
optional nozzle suffix (_d025/_d04/_d06) that wins for the matching variant.
Spoolio's field names (flow_ratio, max_volumetric_speed, retraction_length,
retraction_speed) are read as a fallback when enabled.

Developed with the help of AI (Anthropic's Claude) as part of the author's first
project; so far only tested on the author's printer (Voron Trident 350 INDX,
OrcaSlicer 2.5.1). Repository: https://github.com/daum1988/indx-trident-350

Install: put this file into %APPDATA%\\OrcaSlicer\\orca_plugins\\spoolman_profiles\\,
restart Orca, File > Plugins, tick Activate.
"""

import copy
import json
import logging
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:  # The pure functions below are also used by the offline test, outside Orca.
    import orca
    import orca.pages
    import orca.script
except ImportError:  # pragma: no cover
    orca = None

PLUGIN_NAME = "Spoolman Profiles"
PLUGIN_VERSION = "1.2.0"
BUNDLE_ID = "spoolman-profiles"
ID_PREFIX = "PSM"
TIMEOUT = 15

DEFAULT_SETTINGS = {
    "spoolman_url": "",
    "template_prefix": "SM-Vorlage",
    "auto_sync": True,
    "include_all": False,
    "layout": "two_tabs",
    "spoolio_fallback": True,
}

# Spoolio's extra field name -> Orca setting (used only when the Orca name is not set).
SPOOLIO_FIELDS = {
    "flow_ratio": "filament_flow_ratio",
    "max_volumetric_speed": "filament_max_volumetric_speed",
    "retraction_length": "filament_retraction_length",
    "retraction_speed": "filament_retraction_speed",
}
PLATES = ("hot_plate", "textured_plate", "cool_plate", "eng_plate", "supertack_plate", "textured_cool_plate")
# Tabs shown in OrcaSlicer (read when Orca starts, tabs are only created then).
LAYOUTS = ("two_tabs", "spoolman_tab", "profiles_tab")
INVALID_FILENAME_CHARS = '<>:"/\\|?*' + "".join(chr(i) for i in range(32))


# --------------------------------------------------------------------------- paths, settings, log

def _data_dir() -> Path:
    """OrcaSlicer's data folder (the parent of orca_plugins)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "orca_plugins":
            return parent.parent
    root = os.environ.get("APPDATA") if sys.platform == "win32" else None
    if root:
        return Path(root) / "OrcaSlicer"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "OrcaSlicer"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "OrcaSlicer"


DATA_DIR = _data_dir()
STATE_DIR = DATA_DIR / "spoolman_profiles"  # not the plugin folder: updates replace that
SETTINGS_FILE = STATE_DIR / "settings.json"
LOG_FILE = STATE_DIR / "spoolman_profiles.log"
ICON_FILE = STATE_DIR / "tab_icon.svg"
ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="2"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/>'
            '<path d="M12 3v6M12 15v6M3 12h6M15 12h6"/></svg>')

PANEL_ICON_FILE = STATE_DIR / "panel_icon.svg"
PANEL_ICON_SVG = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
                  'stroke-width="2"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></svg>')

log = logging.getLogger("spoolman_profiles")
log.setLevel(logging.INFO)
log.propagate = False


def _setup_log() -> None:
    if log.handlers:
        return
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        h = RotatingFileHandler(LOG_FILE, maxBytes=256 * 1024, backupCount=1, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(h)
    except OSError:
        log.addHandler(logging.NullHandler())


# The settings can be edited in the Spoolman Profiles tab/window and in the config of the
# command capability in File > Plugins (Orca stores that itself and has no change callback).
# Every copy carries "saved_at"; the newest one wins and is copied to the other.
_caps: list = []  # capability instances, for get_config()/save_config()


def _normalize(data: dict) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: data[k] for k in DEFAULT_SETTINGS if k in data})
    s["spoolman_url"] = str(s["spoolman_url"] or "").strip().rstrip("/")
    s["template_prefix"] = str(s["template_prefix"] or "").strip() or DEFAULT_SETTINGS["template_prefix"]
    if s["layout"] not in LAYOUTS:
        s["layout"] = DEFAULT_SETTINGS["layout"]
    for k in ("auto_sync", "include_all", "spoolio_fallback"):
        v = s[k]
        s[k] = v.strip().lower() in ("1", "true", "yes", "on") if isinstance(v, str) else bool(v)
    try:
        s["saved_at"] = float(data.get("saved_at") or 0)
    except (TypeError, ValueError):
        s["saved_at"] = 0.0
    return s


def _cap_config(cap) -> dict | None:
    try:
        data = json.loads(cap.get_config())
    except Exception:  # noqa: BLE001 - outside a host callback or never saved
        return None
    return data if isinstance(data, dict) and any(k in data for k in DEFAULT_SETTINGS) else None


def _write_file(s: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")


def _push_to_caps(s: dict) -> None:
    for cap in list(_caps):
        try:
            if _cap_config(cap) != s:
                cap.save_config(json.dumps(s))
        except Exception:  # noqa: BLE001
            log.debug("save_config failed", exc_info=True)


def load_settings() -> dict:
    try:
        data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        file_s = _normalize(data if isinstance(data, dict) else {})
    except (OSError, ValueError):
        file_s = _normalize({})
    best = file_s
    for cap in list(_caps):
        data = _cap_config(cap)
        if data is not None:
            c = _normalize(data)
            if c["saved_at"] > best["saved_at"]:
                best = c
    if best is not file_s:  # changed in File > Plugins: copy to the file and the other capability
        try:
            _write_file(best)
        except OSError:
            log.warning("Could not write %s", SETTINGS_FILE)
        _push_to_caps(best)
    return best


def save_settings(new: dict) -> dict:
    s = load_settings()
    s.update({k: new[k] for k in DEFAULT_SETTINGS if k in new})
    s["saved_at"] = time.time()
    s = _normalize(s)
    _write_file(s)
    _push_to_caps(s)
    return s


# --------------------------------------------------------------------------- profile generation (1:1 port of the .ps1)

def _fmt(value, places: int) -> str:
    """Like .NET ToString("0.###"): rounded half away from zero, no trailing zeros."""
    try:
        d = Decimal(str(value)).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return str(value)
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def num(v) -> str:
    return _fmt(float(v), 3)


def _as_list(v) -> list:
    return list(v) if isinstance(v, list) else [v]


def set_prop(obj: dict, name: str, value, only_if_exists: bool = False) -> None:
    if name in obj:
        # Orca 2.5 keeps some values as one entry per extruder variant; a single new
        # value then applies to all entries of the template.
        old = _as_list(obj[name])
        if isinstance(value, list) and len(value) == 1 and len(old) > 1:
            value = [value[0]] * len(old)
        obj[name] = value
    elif not only_if_exists:
        obj[name] = value


def get_first(obj: dict, name: str):
    v = obj.get(name)
    if v is None:
        return None
    v = _as_list(v)
    return v[0] if v else None


def _int(v) -> int:
    return int(round(float(v)))  # [int] in PowerShell rounds half to even, so does round()


def nozzle_key(suffix: str) -> str:
    """"@Voron Trident 350 0.4 nozzle" -> "d04", "0.25" -> "d025", none -> ""."""
    m = re.search(r"(\d+(?:\.\d+)?)\s*nozzle", suffix or "")
    if not m:
        return ""
    return "d" + _fmt(float(m.group(1)), 3).replace(".", "")


def _extra_text(raw) -> str | None:
    """A Spoolman extra value (JSON text) as Orca text, or None when empty."""
    raw = "" if raw is None else str(raw)
    if raw in ("", "null"):
        return None
    try:
        v = json.loads(raw)
    except ValueError:
        v = raw
    if v is None:
        return None
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, float):
        return _fmt(v, 5)
    v = str(v)
    return v or None


def extra_values(extra: dict | None, nz: str, spoolio_fallback: bool = False) -> dict:
    """Spoolman extra fields -> {orca key: text}; a field with the matching nozzle suffix wins."""
    out: dict = {}
    if not isinstance(extra, dict):
        return out
    for pass_no in (0, 1):
        for key, raw in extra.items():
            m = re.match(r"^(.+)_(d\d+)$", key)
            if (pass_no == 0) == bool(m):
                continue
            if m:
                if m.group(2) != nz:
                    continue
                key = m.group(1)
            v = _extra_text(raw)
            if v is not None:
                out[key] = v
    if spoolio_fallback:
        for sk, ok in SPOOLIO_FIELDS.items():
            v = out.pop(sk, None)
            if v is not None and ok not in out:
                out[ok] = v
    return out


def safe_filename(s: str) -> str:
    return "".join("-" if c in INVALID_FILENAME_CHARS else c for c in s).strip()


def load_templates(template_dir: Path, prefix: str) -> tuple[dict, list]:
    """{MATERIAL: [template, ...]} from the user's "<prefix> <Material>" filaments."""
    templates: dict = {}
    warnings: list = []
    want = prefix.lower() + " "
    for f in sorted(Path(template_dir).glob("*.json"), key=lambda p: p.name.lower()):
        try:
            j = json.loads(f.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        name = str(j.get("name") or "")
        if not name.lower().startswith(want):
            continue
        if j.get("inherits"):
            warnings.append(f"Template '{name}' inherits from '{j['inherits']}' - create it as a standalone filament. Skipped.")
            continue
        typ = str(get_first(j, "filament_type") or "") or name[len(prefix):].strip()
        gc = "\n".join(str(x) for x in _as_list(j.get("filament_start_gcode") or ""))
        if "SET_ACTIVE_SPOOL" in gc:
            warnings.append(f"Template '{name}' sets SET_ACTIVE_SPOOL in the filament start G-code. "
                            "That breaks the per-tool spool assignment - please remove it.")
        at = name.rfind(" @")
        suffix = name[at:] if at >= 0 else ""
        templates.setdefault(typ.upper(), []).append({"type": typ, "path": f, "name": name, "suffix": suffix, "json": j})
    return templates, warnings


def find_template(templates: dict, material) -> list | None:
    m = str(material or "").strip().upper()
    if m in templates:
        return templates[m]
    best = None
    for k in templates:  # longest type the material starts with (PLA+ -> PLA, PETG-CF -> PETG)
        if m.startswith(k) and (best is None or len(k) > len(best)):
            best = k
    return templates[best] if best else None


def build_profiles(filaments: list, spools: list, templates: dict, include_all: bool = False,
                   spoolio_fallback: bool = False) -> dict:
    """Returns {"profiles": {filename: (name, json)}, "skipped": n, "warnings": [...], "active": n}."""
    active = {int(s["filament"]["id"]) for s in spools if not s.get("archived") and s.get("filament")}
    profiles: dict = {}
    warnings: list = []
    warned_keys: set = set()
    skipped = 0
    for fil in filaments:
        fid = int(fil["id"])
        if not include_all and fid not in active:
            continue
        tpl_list = find_template(templates, fil.get("material"))
        if not tpl_list:
            warnings.append(f"SM{fid} '{fil.get('name')}': no template for material '{fil.get('material')}'")
            skipped += 1
            continue
        vendor = str((fil.get("vendor") or {}).get("name") or "")
        label = " ".join(x for x in (vendor, str(fil.get("name") or "")) if x) or str(fil.get("material") or "")

        for tpl in tpl_list:  # one profile per nozzle variant, all with the same filament ID
            p = copy.deepcopy(tpl["json"])
            name = f"{label} (SM{fid}){tpl['suffix']}"
            set_prop(p, "name", name)
            set_prop(p, "inherits", "")
            set_prop(p, "from", "User")
            set_prop(p, "filament_id", f"{ID_PREFIX}{fid}")
            set_prop(p, "filament_settings_id", [name])
            set_prop(p, "filament_vendor", [vendor or "Generic"])

            hexc = str(fil.get("color_hex") or "")
            if not hexc and fil.get("multi_color_hexes"):
                hexc = str(fil["multi_color_hexes"]).split(",")[0]
            hexc = hexc.strip().lstrip("#")
            if len(hexc) >= 6:
                set_prop(p, "default_filament_colour", ["#" + hexc[:6].upper()])
            if fil.get("density"):
                set_prop(p, "filament_density", [num(fil["density"])])
            if fil.get("diameter"):
                set_prop(p, "filament_diameter", [num(fil["diameter"])])
            if fil.get("price") and fil.get("weight"):
                # Spoolman: price per spool of 'weight' grams, Orca: price per kg
                set_prop(p, "filament_cost", [num(float(fil["price"]) / float(fil["weight"]) * 1000.0)])
            if fil.get("spool_weight"):
                set_prop(p, "filament_spool_weight", [num(fil["spool_weight"])], only_if_exists=True)

            # Nozzle temperature: keep the first-layer offset and range from the template
            if fil.get("settings_extruder_temp"):
                t = _int(fil["settings_extruder_temp"])
                t0 = _int(get_first(p, "nozzle_temperature") or 0)
                ti = get_first(p, "nozzle_temperature_initial_layer")
                set_prop(p, "nozzle_temperature", [str(t)])
                if ti is not None:
                    set_prop(p, "nozzle_temperature_initial_layer", [str(t + (_int(ti) - t0))])
                lo, hi = get_first(p, "nozzle_temperature_range_low"), get_first(p, "nozzle_temperature_range_high")
                if lo is not None and t < _int(lo):
                    set_prop(p, "nozzle_temperature_range_low", [str(t)])
                if hi is not None and t > _int(hi):
                    set_prop(p, "nozzle_temperature_range_high", [str(t)])
            # Bed temperature for all plate types the template knows
            if fil.get("settings_bed_temp"):
                b = _int(fil["settings_bed_temp"])
                for plate in PLATES:
                    k, ki = f"{plate}_temp", f"{plate}_temp_initial_layer"
                    old = get_first(p, k)
                    if old is None:
                        continue
                    oldi = get_first(p, ki)
                    set_prop(p, k, [str(b)])
                    if oldi is not None:
                        set_prop(p, ki, [str(b + (_int(oldi) - _int(old)))])

            # Extra fields: Orca setting name, optional nozzle suffix. pressure_advance
            # (measured on the printer with the load cell) also turns on enable_pressure_advance.
            vals = extra_values(fil.get("extra"), nozzle_key(tpl["suffix"]), spoolio_fallback)
            if "pressure_advance" in vals:
                vals["enable_pressure_advance"] = "1"
            for k in ("filament_shrink", "filament_shrinkage_compensation_z"):
                if k in vals and not vals[k].endswith("%"):
                    vals[k] += "%"  # Orca stores shrinkage as percent text
            for k, v in vals.items():
                if k not in p:
                    if k not in warned_keys:
                        warnings.append(f"Extra field '{k}' does not exist in the Orca template - ignored.")
                        warned_keys.add(k)
                    continue
                set_prop(p, k, [v])

            profiles[safe_filename(name) + ".json"] = (name, p)
    return {"profiles": profiles, "skipped": skipped, "warnings": warnings, "active": len(active)}


def profile_text(p: dict) -> str:
    return json.dumps(p, indent=4, ensure_ascii=False) + "\n"


# --------------------------------------------------------------------------- Spoolman

def fetch_spoolman(url: str) -> tuple[list, list]:
    api = url.rstrip("/") + "/api/v1"
    out = []
    for path in ("filament", "spool"):
        req = urllib.request.Request(f"{api}/{path}", headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))  # Spoolman sends UTF-8 without a charset
        if not isinstance(data, list):
            raise ValueError(f"Unexpected answer from {api}/{path}")
        out.append(data)
    return out[0], out[1]


# --------------------------------------------------------------------------- Orca side

_lock = threading.Lock()
_state = {"time": "", "text": "Not synced yet.", "ok": None}


def _bundle():
    h = orca.host
    pb = h.preset_bundle()
    b = h.preset.bundles
    return pb, b.reload_local, getattr(b, "list_ids", None)


def _all(c) -> list:
    return [c.preset(i) for i in range(c.size())]


def _user_root() -> Path:
    return DATA_DIR / "user"


def _account_dir(pb, prefix: str) -> Path:
    """The user folder Orca loaded presets from (user/<account> when signed in, else user/default).

    OrcaSlicer.conf (preset_folder) is off limits to plugins, so it is read from the
    files of the loaded presets, templates first.
    """
    root = _user_root().resolve()
    found = []
    want = prefix.lower() + " "
    for coll in (pb.filaments, pb.printers, pb.prints):
        for p in _all(coll):
            try:
                parts = Path(p.file or "").resolve().relative_to(root).parts
            except (ValueError, OSError):
                continue
            if len(parts) > 1 and not parts[0].startswith(("_", ".")):
                score = 0 if coll is pb.filaments and str(p.name).lower().startswith(want) else 1
                found.append((score, parts[0]))
    if found:
        return _user_root() / min(found)[1]
    return _user_root() / "default"


def _old_script_presets(pb) -> list[str]:
    """Loaded PSM presets that are not ours (left over from orca_spoolman_profiles.ps1)."""
    # The Python Preset binding has no filament_id, so it is read from the preset's file.
    out = []
    for p in _all(pb.filaments):
        try:
            if p.is_system or p.is_default or p.is_from_bundle() or not str(p.file or "").endswith(".json"):
                continue
            j = json.loads(Path(p.file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, AttributeError):
            continue
        if str(j.get("filament_id") or "").startswith(ID_PREFIX):
            out.append(p.name)
    return out


def _reload_error(msg: str) -> str:
    if "modified" in msg:
        return "Save or discard your unsaved changes to a Spoolman profile, then update again."
    if "depends on it" in msg:
        return f"Another preset is based on a Spoolman profile ({msg.split('depends on it:', 1)[-1].strip()}). Save it without that parent, then update again."
    if "not initialized" in msg or "not available" in msg:
        return "OrcaSlicer is not ready yet. Select printer and filament presets, then update again."
    return f"OrcaSlicer did not reload the profiles: {msg}"


def sync(reason: str = "button") -> dict:
    """Generate the profiles, write the bundle, reload it. Returns a result dict."""
    if not _lock.acquire(blocking=False):
        return {"ok": False, "text": "An update is already running.", "busy": True}
    try:
        res = _sync(reason)
    except urllib.error.URLError as exc:
        res = {"ok": False, "text": f"Spoolman not reachable: {getattr(exc, 'reason', exc)}"}
    except Exception as exc:  # noqa: BLE001 - report everything to the user
        log.exception("Sync failed")
        res = {"ok": False, "text": f"Update failed: {exc}"}
    finally:
        _lock.release()
    _state.update(time=time.strftime("%H:%M:%S"), text=res["text"], ok=res["ok"])
    log.info("[%s] %s", reason, res["text"].replace("\n", " | "))
    _post_status()
    return res


def _sync(reason: str) -> dict:
    s = load_settings()
    if not s["spoolman_url"]:
        return {"ok": False, "text": "Enter the Spoolman address in the settings first."}
    pb, reload_local, list_ids = _bundle()
    acct = _account_dir(pb, s["template_prefix"])
    tdir = acct / "filament" / "base"
    templates, warnings = load_templates(tdir, s["template_prefix"])
    if not templates:
        return {"ok": False, "text": f"No template found. Create a filament '{s['template_prefix']} <Material>' "
                                     f"per material in Orca (searched user/{acct.name})."}
    filaments, spools = fetch_spoolman(s["spoolman_url"])
    built = build_profiles(filaments, spools, templates, s["include_all"], s["spoolio_fallback"])
    warnings += built["warnings"]
    files = {fn: profile_text(p) for fn, (_, p) in built["profiles"].items()}

    bdir = acct / "_local" / BUNDLE_ID
    fdir = bdir / "filament"
    fdir.mkdir(parents=True, exist_ok=True)
    meta = bdir / "bundle_metadata.json"
    meta_text = json.dumps({"id": BUNDLE_ID, "name": PLUGIN_NAME, "version": PLUGIN_VERSION,
                            "description": "Filament profiles generated from Spoolman", "author": ""}, indent=4)
    old = {}
    for f in fdir.glob("*.json"):
        try:
            old[f.name] = f.read_text(encoding="utf-8")
        except OSError:
            old[f.name] = None
    new = [n for n in files if n not in old]
    changed = [n for n in files if n in old and old[n] != files[n]]
    removed = [n for n in old if n not in files]
    meta_missing = not meta.is_file()
    if meta_missing:
        meta.write_text(meta_text, encoding="utf-8")
    for n in new + changed:
        (fdir / n).write_text(files[n], encoding="utf-8")
    for n in removed:
        (fdir / n).unlink(missing_ok=True)

    loaded = {p.name for p in _all(pb.filaments) if p.is_from_bundle()}
    needs_reload = bool(new or changed or removed or meta_missing) or (
        files and not any(n.startswith(f"_local/{BUNDLE_ID}/") for n in loaded))
    if needs_reload:
        if callable(list_ids):
            ids = list_ids() or []
            if BUNDLE_ID not in ids:
                return {"ok": False, "text": f"OrcaSlicer does not list the bundle in user/{acct.name}/_local. "
                                             "Restart OrcaSlicer and update again."}
        try:
            reload_local(BUNDLE_ID)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "retry": "not initialized" in str(exc) or "not available" in str(exc),
                    "text": _reload_error(str(exc))}

    lines = [f"{len(new)} new, {len(changed)} updated, {len(removed)} removed, "
             f"{len(files) - len(new) - len(changed)} unchanged ({len(files)} profiles for "
             f"{built['active'] if not s['include_all'] else len(filaments)} filaments), "
             f"{built['skipped']} without template. Folder: user/{acct.name}"]
    ours = [p for p in _all(pb.filaments) if getattr(p, "bundle_id", "") == BUNDLE_ID]
    lines.append(f"Loaded in OrcaSlicer: {len(ours)} profiles, {sum(1 for p in ours if p.is_compatible)} of them "
                 "compatible with the selected printer (filament list: Bundle presets > Spoolman Profiles).")
    old_presets = _old_script_presets(pb)
    if old_presets:
        warnings.insert(0, f"{len(old_presets)} old profiles with filament ID PSM... from the PowerShell script are "
                           "still loaded (e.g. '" + old_presets[0] + "'). They make the filament sync ambiguous - "
                           "close Orca and delete them from filament/base of your user folder.")
    lines += warnings
    return {"ok": not old_presets, "text": "\n".join(lines), "changed": bool(new or changed or removed),
            "warn": bool(warnings)}


# --------------------------------------------------------------------------- UI glue

def _notify(text: str, warning: bool = False) -> None:
    try:
        ui = orca.host.ui
        level = getattr(ui, "WarningNotificationLevel" if warning else "RegularNotificationLevel", None)
        if level is None:
            nl = getattr(ui, "NotificationLevel", None)
            level = getattr(nl, "WarningNotificationLevel" if warning else "RegularNotificationLevel", None)
        ui.push_notification(level, text[:400])
    except Exception:  # noqa: BLE001
        log.info("Notification not shown: %s", text)


_ui_targets: dict = {}  # name -> post(dict) of an open plugin page or window


def _post(msg: dict) -> None:
    for name, post in list(_ui_targets.items()):
        try:
            post(msg)
        except Exception:  # noqa: BLE001
            log.debug("post to %s failed", name, exc_info=True)


def _post_status() -> None:
    if not _ui_targets:
        return
    _post({"type": "settings", "settings": load_settings()})  # may have changed in File > Plugins
    _post({"type": "status", "time": _state["time"], "text": _state["text"], "ok": _state["ok"],
           "busy": _lock.locked()})


def _handle_ui_message(data) -> None:
    """Messages from the control panel (window or tab)."""
    try:
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return
        t = data.get("type")
        if t == "ready":
            _post_status()
        elif t == "sync":
            _bg(_sync_and_notify, "button")
        elif t == "save":
            save_settings(data.get("settings") or {})
            _post_status()
        elif t == "browser":
            u = load_settings()["spoolman_url"]
            if u.startswith(("http://", "https://")):
                _bg(webbrowser.open, u)
    except Exception:  # noqa: BLE001
        log.exception("UI message failed")


def _bg(fn, *args) -> None:
    def run():
        try:
            fn(*args)
        except Exception:  # noqa: BLE001
            log.exception("Background task failed")
    threading.Thread(target=run, daemon=True).start()


def _sync_and_notify(reason: str) -> None:
    res = sync(reason)
    if res.get("busy"):
        return
    if not res["ok"] or res.get("warn"):
        _notify("Spoolman Profiles: " + res["text"], warning=True)
    elif res.get("changed") or reason != "startup":
        _notify("Spoolman Profiles: " + res["text"].split("\n", 1)[0])


def _startup_sync() -> None:
    """At start: wait until Orca has loaded its presets and main window, then sync once."""
    if not load_settings()["auto_sync"] or not load_settings()["spoolman_url"]:
        return
    time.sleep(8)
    for _ in range(24):  # up to ~2 minutes
        try:
            pb = orca.host.preset_bundle()
            ready = pb is not None and pb.filaments.size() > 1
        except Exception:  # noqa: BLE001
            ready = False
        if ready:
            res = sync("startup")
            if not res.get("retry"):
                if not res["ok"] or res.get("warn"):
                    _notify("Spoolman Profiles: " + res["text"], warning=True)
                elif res.get("changed"):
                    _notify("Spoolman Profiles: " + res["text"].split("\n", 1)[0])
                return
        time.sleep(5)
    log.warning("Startup sync gave up: OrcaSlicer was not ready")


PANEL_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; --b: rgba(127,127,127,.35); --ok: #2e9d57; --bad: #c0392b; --acc: #009688; }
html, body { margin: 0; font: 13px "Segoe UI", system-ui, sans-serif; }
#bar { display: flex; gap: 8px; align-items: center; padding: 10px; border-bottom: 1px solid var(--b); flex-wrap: wrap; }
button { font: inherit; padding: 5px 12px; border-radius: 4px; border: 1px solid var(--b); background: transparent; color: inherit; cursor: pointer; }
button.main { background: var(--acc); border-color: var(--acc); color: #fff; font-weight: 600; }
button:disabled { opacity: .5; cursor: default; }
#status { padding: 10px; white-space: pre-wrap; border-bottom: 1px solid var(--b); min-height: 3em; }
#status.ok { color: var(--ok); } #status.bad { color: var(--bad); }
#settings { padding: 10px; }
#settings h3 { margin: 0 0 10px; font-size: 13px; }
#settings label { display: block; margin: 0 0 8px; }
#settings input[type=text], #settings select { width: 100%; box-sizing: border-box; padding: 4px; font: inherit; }
small, #msg { opacity: .75; }
</style></head><body>
<div id="bar">
  <button class="main" id="sync">Update profiles from Spoolman</button>
  <button id="browser">Open Spoolman in browser</button>
</div>
<div id="status">Not synced yet.</div>
<form id="settings">
  <h3>Settings</h3>
  <label>Spoolman address<br><input type="text" name="spoolman_url" placeholder="http://spoolman.local:7912"></label>
  <label>Template prefix<br><input type="text" name="template_prefix"><br>
    <small>Filament templates in Orca are named "&lt;prefix&gt; &lt;Material&gt; ... @&lt;printer&gt; &lt;size&gt; nozzle".</small></label>
  <label><input type="checkbox" name="auto_sync"> Update automatically when OrcaSlicer starts</label>
  <label><input type="checkbox" name="spoolio_fallback"> Also read Spoolio's fields (flow_ratio, max_volumetric_speed, retraction_length, retraction_speed) as a fallback</label>
  <label><input type="checkbox" name="include_all"> Also archived filaments and filaments without a spool</label>
  <label>Layout (after restarting OrcaSlicer)<br><select name="layout">
    <option value="two_tabs">Two tabs: Spoolman + Spoolman Profiles</option>
    <option value="spoolman_tab">Spoolman tab only (buttons: command "Update profiles from Spoolman")</option>
    <option value="profiles_tab">Spoolman Profiles tab only (Spoolman opens in the browser)</option>
  </select></label>
  <button class="main" type="button" id="save">Save</button> <span id="msg"></span>
  <p><small>Same settings as in File &gt; Plugins &gt; Config &gt; Update profiles from Spoolman. After changing the address,
  restart OrcaSlicer so the Spoolman tab loads it.</small></p>
</form>
<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var f = $("settings");
  orca.onMessage(function (m) {
    if (!m) return;
    if (m.type === "settings") {
      if (document.activeElement && document.activeElement.form === f) return;  // do not overwrite typing
      var s = m.settings;
      for (var i = 0; i < f.elements.length; i++) {
        var el = f.elements[i];
        if (!el.name) continue;
        if (el.type === "checkbox") el.checked = !!s[el.name]; else el.value = s[el.name] || "";
      }
    } else if (m.type === "status") {
      var st = $("status");
      st.textContent = (m.time ? m.time + "  " : "") + m.text;
      st.className = m.ok === true ? "ok" : (m.ok === false ? "bad" : "");
      $("sync").disabled = !!m.busy;
    }
  });
  $("sync").onclick = function () { $("sync").disabled = true; $("status").className = ""; $("status").textContent = "Updating ..."; orca.postMessage({type: "sync"}); };
  $("browser").onclick = function () { orca.postMessage({type: "browser"}); };
  f.addEventListener("submit", function (e) { e.preventDefault(); });
  $("save").onclick = function () {
    var s = {};
    for (var i = 0; i < f.elements.length; i++) {
      var el = f.elements[i];
      if (el.name) s[el.name] = el.type === "checkbox" ? el.checked : el.value.trim();
    }
    if (document.activeElement) document.activeElement.blur();
    orca.postMessage({type: "save", settings: s});
    $("msg").textContent = "Saved.";
  };
  orca.postMessage({type: "ready"});
})();
</script></body></html>"""

# The tab navigates to Spoolman itself instead of framing it: inside an iframe under Orca's
# file:// page, Spoolman counts as third party, its localStorage access throws and SvelteKit
# shows "500 Internal Error".
REDIRECT_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; } body { margin: 24px; font: 13px "Segoe UI", system-ui, sans-serif; }
small { opacity: .75; }
</style></head><body>
<p>Loading Spoolman from <a id="u"></a> ...</p>
<p><small>If nothing appears, check the address: File &gt; Plugins &gt; Spoolman Profiles &gt; Config,
or run the command "Update profiles from Spoolman".</small></p>
<script>var u = __URL__; var a = document.getElementById("u"); a.textContent = u; a.href = u; location.replace(u);</script>
</body></html>"""


def _tab_html() -> str:
    url = load_settings()["spoolman_url"]
    if url.startswith(("http://", "https://")):
        return REDIRECT_HTML.replace("__URL__", json.dumps(url).replace("</", "<\\/"))
    return PANEL_HTML  # no address yet: show the settings right in the tab


CONFIG_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; font: 13px "Segoe UI", system-ui, sans-serif; }
body { margin: 0; padding: 14px; }
label { display: block; margin: 0 0 10px; }
label.t span { display: block; margin-bottom: 3px; font-weight: 600; }
input[type=text], select { width: 100%; box-sizing: border-box; padding: 5px; font: inherit; }
small { opacity: .7; }
#msg { margin-left: 8px; opacity: .8; }
</style></head><body>
<form id="f">
<label class="t"><span>Spoolman address</span><input type="text" name="spoolman_url" placeholder="http://spoolman.local:7912"></label>
<label class="t"><span>Template prefix</span><input type="text" name="template_prefix" placeholder="SM-Vorlage">
<small>Filament templates in Orca: "&lt;prefix&gt; &lt;Material&gt; ... @&lt;printer&gt; &lt;size&gt; nozzle".</small></label>
<label><input type="checkbox" name="auto_sync"> Update automatically when OrcaSlicer starts</label>
<label><input type="checkbox" name="spoolio_fallback"> Also read Spoolio's fields (flow_ratio, max_volumetric_speed, retraction_length, retraction_speed) as a fallback</label>
<label><input type="checkbox" name="include_all"> Also archived filaments and filaments without a spool</label>
<label>Layout (after restarting OrcaSlicer)<br><select name="layout">
    <option value="two_tabs">Two tabs: Spoolman + Spoolman Profiles</option>
    <option value="spoolman_tab">Spoolman tab only (buttons: command "Update profiles from Spoolman")</option>
    <option value="profiles_tab">Spoolman Profiles tab only (Spoolman opens in the browser)</option>
  </select></label>
<button type="button" id="save">Save</button><span id="msg"></span>
<p><small>The same settings are in the Spoolman Profiles tab. Changes apply to the next update; the layout and the Spoolman tab address after restarting OrcaSlicer.</small></p>
</form><script>
(function () {
  var f = document.getElementById("f");
  function fill(cfg) {
    if (typeof cfg === "string") { try { cfg = JSON.parse(cfg); } catch (e) { cfg = {}; } }
    cfg = cfg || {};
    for (var i = 0; i < f.elements.length; i++) {
      var el = f.elements[i];
      if (!el.name || !(el.name in cfg)) continue;
      if (el.type === "checkbox") el.checked = !!cfg[el.name]; else el.value = cfg[el.name] || "";
    }
  }
  if (window.orca && typeof orca.onConfig === "function") orca.onConfig(fill);
  if (window.orca && typeof orca.getConfig === "function") {
    try { var r = orca.getConfig(); if (r && typeof r.then === "function") r.then(fill); else if (r) fill(r); } catch (e) {}
  }
  // The Plugins dialog sandboxes this page without allow-forms: a form submit never fires,
  // so Save is a plain button.
  f.addEventListener("submit", function (e) { e.preventDefault(); });
  document.getElementById("save").addEventListener("click", function () {
    var cfg = {};
    for (var i = 0; i < f.elements.length; i++) {
      var el = f.elements[i];
      if (el.name) cfg[el.name] = el.type === "checkbox" ? el.checked : el.value.trim();
    }
    cfg.saved_at = Date.now() / 1000;
    orca.saveConfig(cfg);
    document.getElementById("msg").textContent = "Saved.";
  });
})();
</script></body></html>"""


# Orca lists every capability in File > Plugins > Config and cannot hide one; only the
# command capability carries the settings, the tabs point there.
INFO_CONFIG_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; font: 13px "Segoe UI", system-ui, sans-serif; } body { margin: 0; padding: 14px; }
</style></head><body>
<p>This entry has no settings of its own.</p>
<p>All settings of Spoolman Profiles are under the entry <b>Update profiles from Spoolman</b>
(and in the Spoolman Profiles tab).</p>
</body></html>"""


if orca is not None:

    class _SettingsConfig:
        """The plugin's settings as this capability's config in File > Plugins."""

        def has_config_ui(self):
            return True

        def get_config_ui(self):
            return CONFIG_HTML

        def get_default_config(self):
            return {**DEFAULT_SETTINGS, "saved_at": time.time()}

        def _register_config(self):
            if self not in _caps:
                _caps.append(self)

        def _sync_config(self):
            try:
                _push_to_caps(load_settings())
            except Exception:  # noqa: BLE001
                log.debug("config sync failed", exc_info=True)

    class _InfoConfig:
        """Config entry of a tab: only a pointer to the real settings."""

        def has_config_ui(self):
            return True

        def get_config_ui(self):
            return INFO_CONFIG_HTML

    class SpoolmanPage(_InfoConfig, orca.pages.PagesPluginCapabilityBase):
        """Tab "Spoolman": the Spoolman web UI (or the settings while no address is set)."""

        def get_name(self):
            return "Spoolman"

        def get_type(self):
            return orca.PluginType.Pages

        def get_icon(self):
            try:
                if not ICON_FILE.is_file():
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    ICON_FILE.write_text(ICON_SVG, encoding="utf-8")
                return str(ICON_FILE)
            except OSError:
                return ""

        def get_ui(self):
            html = _tab_html()
            if html is PANEL_HTML:
                _ui_targets["tab"] = self.post_message
            else:
                _ui_targets.pop("tab", None)
            return html

        def on_message(self, arg0):
            _handle_ui_message(arg0)

        def on_load(self):
            _setup_log()

        def on_unload(self):
            _ui_targets.pop("tab", None)

    class SpoolmanProfilesPage(_InfoConfig, orca.pages.PagesPluginCapabilityBase):
        """Tab "Spoolman Profiles": update button, status and settings."""

        def get_name(self):
            return "Spoolman Profiles"

        def get_type(self):
            return orca.PluginType.Pages

        def get_icon(self):
            try:
                if not PANEL_ICON_FILE.is_file():
                    STATE_DIR.mkdir(parents=True, exist_ok=True)
                    PANEL_ICON_FILE.write_text(PANEL_ICON_SVG, encoding="utf-8")
                return str(PANEL_ICON_FILE)
            except OSError:
                return ""

        def get_ui(self):
            _ui_targets["panel_tab"] = self.post_message
            return PANEL_HTML

        def on_message(self, arg0):
            _handle_ui_message(arg0)

        def on_load(self):
            _setup_log()

        def on_unload(self):
            _ui_targets.pop("panel_tab", None)

    class SpoolmanProfilesScript(_SettingsConfig, orca.script.ScriptPluginCapabilityBase):
        """Command "Update profiles from Spoolman" (Plugins dialog / command palette) and startup sync."""

        def __init__(self):
            super().__init__()
            self._register_config()

        def get_name(self):
            return "Update profiles from Spoolman"

        def on_load(self):
            _setup_log()
            log.info("%s %s loaded", PLUGIN_NAME, PLUGIN_VERSION)
            self._sync_config()
            _bg(_startup_sync)

        def on_unload(self):
            # The layout is read before any capability exists at the next start, i.e. from
            # settings.json only: take over a change made in File > Plugins now.
            try:
                load_settings()
            except Exception:  # noqa: BLE001
                log.debug("settings sync on unload failed", exc_info=True)

        window = None

        def execute(self):
            if self.window is not None and self.window.is_open():
                _post_status()
                return orca.ExecutionResult.success("Spoolman Profiles is already open.")
            self.window = orca.host.ui.create_window(
                html=PANEL_HTML,
                title="Spoolman Profiles",
                width=720,
                height=560,
                on_message=_handle_ui_message,
                on_close=self._on_close,
            )
            _ui_targets["window"] = self.window.post
            return orca.ExecutionResult.success("Spoolman Profiles opened.")

        def _on_close(self, *args):
            _ui_targets.pop("window", None)
            self.window = None

    @orca.plugin
    class SpoolmanProfilesPlugin(orca.base):
        def register_capabilities(self):
            url = load_settings()["spoolman_url"]
            try:
                if url.startswith(("http://", "https://")):
                    orca.request_permissions(threading=True, network=[url])
                else:
                    orca.request_permissions(threading=True)
            except Exception:  # noqa: BLE001
                log.debug("request_permissions failed", exc_info=True)
            # Registration order is the order in File > Plugins > Config: settings entry first.
            orca.register_capability(SpoolmanProfilesScript)  # always: settings, startup update, window
            layout = load_settings()["layout"]
            if layout in ("two_tabs", "spoolman_tab"):
                orca.register_capability(SpoolmanPage)
            if layout in ("two_tabs", "profiles_tab"):
                orca.register_capability(SpoolmanProfilesPage)
