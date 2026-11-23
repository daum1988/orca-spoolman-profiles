# /// script
# requires-python = ">=3.12"
# dependencies = []
#
# [tool.orcaslicer.plugin]
# name = "Spoolman Profiles"
# description = "Creates standalone OrcaSlicer filament profiles (filament ID PSM<id>) from Spoolman and shows the Spoolman web UI in a tab."
# author = "indx-trident-350"
# version = "1.10.1"
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
OrcaSlicer 2.5.1). Repository: https://github.com/daum1988/orca-spoolman-profiles
Printer side (lane_data for the filament sync, INDX setup): https://github.com/daum1988/indx-trident-350

Install: subscribe in Orca Cloud, or put this file into
%APPDATA%\\OrcaSlicer\\orca_plugins\\spoolman_profiles\\, restart Orca, File > Plugins, tick Activate.
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
import urllib.parse
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
PLUGIN_VERSION = "1.10.1"
BUNDLE_ID = "spoolman-profiles"
ID_PREFIX = "PSM"
TIMEOUT = 15

DEFAULT_SETTINGS = {
    "spoolman_url": "",
    "moonraker_url": "",
    "low_stock_g": 100,
    "tools_since": "",
    "show_tools": True,
    "template_prefix": "SM-Vorlage",
    "name_format": "{vendor} {material} {name}",
    "link_label": "",
    "link_url": "",
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


# The settings can be edited in the Spools & Stats tab/window and in the config of the
# command capability in File > Plugins (Orca stores that itself and has no change callback).
# Every copy carries "saved_at"; the newest one wins and is copied to the other.
_caps: list = []  # capability instances, for get_config()/save_config()


def _normalize(data: dict) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update({k: data[k] for k in DEFAULT_SETTINGS if k in data})
    s["spoolman_url"] = str(s["spoolman_url"] or "").strip().rstrip("/")
    mr = str(s["moonraker_url"] or "").strip().rstrip("/")
    if mr and not mr.startswith(("http://", "https://")):
        mr = "http://" + mr
    s["moonraker_url"] = mr
    ts = str(s["tools_since"] or "").strip()
    s["tools_since"] = ts if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ts) else ""
    try:
        s["low_stock_g"] = max(0, min(100000, int(float(s["low_stock_g"]))))
    except (TypeError, ValueError):
        s["low_stock_g"] = DEFAULT_SETTINGS["low_stock_g"]
    s["template_prefix"] = str(s["template_prefix"] or "").strip() or DEFAULT_SETTINGS["template_prefix"]
    nf = str(s["name_format"] or "").strip()
    s["link_label"] = str(s["link_label"] or "").strip()[:40]
    s["link_url"] = str(s["link_url"] or "").strip()
    s["name_format"] = nf if any(k in nf for k in ("{vendor}", "{material}", "{name}")) else DEFAULT_SETTINGS["name_format"]
    if s["layout"] not in LAYOUTS:
        s["layout"] = DEFAULT_SETTINGS["layout"]
    for k in ("auto_sync", "include_all", "spoolio_fallback", "show_tools"):
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


def profile_label(fmt: str, vendor: str, material: str, name: str) -> str:
    """Profile name from the format, e.g. "{vendor} {material} {name}" -> "ELEGOO PLA Matte White".

    The material is left out when vendor or name already contain it as a word, also
    with a suffix like "+" or "-CF" ("RAPID PLA+ Black" stays as it is).
    """
    if material and re.search(rf"(?<![A-Za-z0-9]){re.escape(material)}(?![A-Za-z0-9])", f"{vendor} {name}", re.I):
        material = ""
    label = fmt.replace("{vendor}", vendor).replace("{material}", material).replace("{name}", name)
    return re.sub(r"\s+", " ", label).strip()


def build_profiles(filaments: list, spools: list, templates: dict, include_all: bool = False,
                   spoolio_fallback: bool = False, name_format: str = "{vendor} {name}") -> dict:
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
        label = (profile_label(name_format, vendor, str(fil.get("material") or ""), str(fil.get("name") or ""))
                 or str(fil.get("material") or ""))

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


# --------------------------------------------------------------------------- statistics

# Read-only: Moonraker's job history (per print: status, time, grams per tool from the
# slicer, filament names with "(SM<id>)") plus Spoolman for colour, material, price and
# remaining weight. Nothing is booked.
HISTORY_FILE = STATE_DIR / "history_cache.json"
UI_FILE = STATE_DIR / "ui_state.json"
PERIODS = (7, 30, 90, 365, 0)  # days, 0 = all
WEEKS = 8
CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£", "CHF": "CHF", "JPY": "¥"}
_hist_lock = threading.Lock()


def load_ui() -> dict:
    ui = {"collapsed": {"profiles": False, "stats": False}, "period": 30}
    try:
        data = json.loads(UI_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            c = data.get("collapsed")
            if isinstance(c, dict):
                ui["collapsed"].update({k: bool(c[k]) for k in ui["collapsed"] if k in c})
            if data.get("period") in PERIODS:
                ui["period"] = data["period"]
    except (OSError, ValueError):
        pass
    return ui


def save_ui(data: dict) -> None:
    ui = load_ui()
    c = data.get("collapsed")
    if isinstance(c, dict):
        ui["collapsed"].update({k: bool(c[k]) for k in ui["collapsed"] if k in c})
    try:
        if int(data.get("period")) in PERIODS:
            ui["period"] = int(data["period"])
    except (TypeError, ValueError):
        pass
    try:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        UI_FILE.write_text(json.dumps(ui), encoding="utf-8")
    except OSError:
        log.warning("Could not write %s", UI_FILE)


def _http_json(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _jlist(v) -> list[str]:
    """Moonraker keeps some metadata lists as JSON text ('["PLA", "PETG"]') or ';'-separated."""
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    s = str(v).strip()
    if s.startswith("["):
        try:
            x = json.loads(s)
            if isinstance(x, list):
                return [str(i) for i in x]
        except ValueError:
            pass
    return [part.strip().strip('"') for part in s.split(";")] if s else []


def _floats(v) -> list[float]:
    out = []
    for x in (_jlist(v) if isinstance(v, str) else v or []):
        try:
            out.append(float(x))
        except (TypeError, ValueError):
            out.append(0.0)
    return out


def compact_job(j: dict) -> dict | None:
    status = str(j.get("status") or "")
    if status == "in_progress":
        return None
    m = j.get("metadata") or {}
    weights = _floats(m.get("filament_weights"))
    try:
        changes = int(m["filament_change_count"]) if m.get("filament_change_count") is not None else None
    except (TypeError, ValueError):
        changes = None
    if not any(weights):
        weights = _floats([m.get("filament_weight_total")]) if m.get("filament_weight_total") else []
    try:
        return {"id": str(j.get("job_id")), "name": str(j.get("filename") or ""),
                "start": float(j.get("start_time") or 0), "end": float(j.get("end_time") or 0), "status": status,
                "print_s": float(j.get("print_duration") or 0),
                "used_mm": float(j.get("filament_used") or 0), "total_mm": float(m.get("filament_total") or 0),
                "weights": weights, "changes": changes, "names": _jlist(m.get("filament_name")),
                "types": _jlist(m.get("filament_type")), "colors": _jlist(m.get("filament_colors"))}
    except (TypeError, ValueError):
        return None


def fetch_history(base: str) -> list[dict]:
    """All finished jobs; cached, so later calls only fetch what is new."""
    with _hist_lock:
        try:
            cache = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            jobs = cache.get("jobs", {}) if cache.get("base") == base else {}
        except (OSError, ValueError):
            jobs = {}
        start, limit = 0, 100
        while True:
            res = _http_json(f"{base}/server/history/list?limit={limit}&start={start}&order=desc")["result"]
            page = res.get("jobs") or []
            changed = False
            for j in page:
                c = compact_job(j)
                if c and jobs.get(c["id"]) != c:
                    jobs[c["id"]] = c
                    changed = True
            start += len(page)
            # "count" is the size of this page, not of the history: page until one comes back short.
            if len(page) < limit or not changed:
                break
        try:
            STATE_DIR.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps({"base": base, "jobs": jobs}), encoding="utf-8")
        except OSError:
            log.warning("Could not write %s", HISTORY_FILE)
        return list(jobs.values())


def printer_variables(base: str) -> dict:
    """Klipper save_variables (INDX toolchange_count, t<n>__spool_id); {} when Klipper is not ready."""
    try:
        res = _http_json(f"{base}/printer/objects/query?save_variables")["result"]
        return res["status"]["save_variables"]["variables"] or {}
    except Exception:  # noqa: BLE001 - optional
        return {}


def printer_totals(base: str) -> dict:
    """Moonraker's lifetime job totals (filament in mm, also jobs without weight metadata)."""
    try:
        return _http_json(f"{base}/server/history/totals")["result"]["job_totals"] or {}
    except Exception:  # noqa: BLE001 - optional
        return {}


TIMELAPSE_RE = re.compile(r"^timelapse_(.+)_(\d{8}_\d{4})\.(mp4|gif|jpg)$")


def fetch_timelapses(base: str) -> list[dict]:
    """Rendered timelapse videos on the printer; [] without moonraker-timelapse."""
    try:
        files = _http_json(f"{base}/server/files/list?root=timelapse")["result"]
    except Exception:  # noqa: BLE001 - optional
        return []
    out: dict = {}
    for f in files if isinstance(files, list) else []:
        name = str(f.get("path") or f.get("filename") or "")
        m = TIMELAPSE_RE.match(name)
        if not m:
            continue
        try:
            ts = time.mktime(time.strptime(m.group(2), "%Y%m%d_%H%M"))
        except ValueError:
            continue
        e = out.setdefault((m.group(1), m.group(2)), {"gcode": m.group(1), "ts": ts})
        e["thumb" if m.group(3) == "jpg" else "video"] = name
    return [e for e in out.values() if e.get("video")]


def attach_timelapses(hist: list, videos: list, base: str) -> None:
    """Link each print to the video rendered for it (same G-code file, rendered after its start)."""
    url = lambda n: f"{base}/server/files/timelapse/{urllib.parse.quote(n)}"  # noqa: E731
    by_file: dict = {}
    for v in videos:
        by_file.setdefault(v["gcode"], []).append(v)
    used: set = set()
    for h in sorted(hist, key=lambda x: x["t"]):
        end = h.get("end") or h["t"]
        cands = [v for v in by_file.get(h.get("file"), [])
                 if v["video"] not in used and h["t"] - 60 <= v["ts"] <= end + 3600]
        if not cands:
            continue
        v = min(cands, key=lambda v: abs(v["ts"] - end))
        used.add(v["video"])
        h["timelapse"] = url(v["video"])
        if v.get("thumb"):
            h["thumb"] = url(v["thumb"])


def spoolman_currency(url: str) -> str:
    try:
        v = _http_json(url.rstrip("/") + "/api/v1/setting/currency").get("value")
        code = json.loads(v) if isinstance(v, str) else v
        code = str(code or "EUR")
    except Exception:  # noqa: BLE001 - optional
        code = "EUR"
    return CURRENCY_SYMBOLS.get(code.upper(), code)


def _hex(v) -> str:
    s = str(v or "").strip().lstrip("#").split(",")[0]
    return "#" + s[:6].upper() if re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", s) else "#888888"


def _week_start(ts: float) -> float:
    lt = time.localtime(ts)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    return midnight - lt.tm_wday * 86400


def compute_stats(jobs: list, filaments: list, spools: list, days: int, low_g: int, currency: str,
                  now: float | None = None, variables: dict | None = None, tools_since: str = "",
                  totals: dict | None = None) -> dict:
    now = now or time.time()
    since = now - days * 86400 if days else 0
    # Per-tool data and tool changes only from the day the tool changer was installed:
    # before that, "tool 1, 2, ..." were filament slots of another toolhead.
    try:
        tsince = time.mktime(time.strptime(tools_since, "%Y-%m-%d")) if tools_since else 0.0
    except ValueError:
        tsince = 0.0
    fils = {int(f["id"]): f for f in filaments if f.get("id") is not None}
    w0 = _week_start(now) - (WEEKS - 1) * 7 * 86400
    weeks = [{} for _ in range(WEEKS)]
    used, mats, hist = {}, {}, []
    total = cost = 0.0
    prints = cancelled = unmatched = 0
    tools: dict = {}  # tool index -> {"g", "prints"}
    total_all = total_tools = 0.0  # independent of the period
    time_period = time_all = time_since = 0.0  # print time in seconds, all finished jobs
    time_done = 0.0  # print time of completed prints in the period (for the average)
    n_done = 0
    changes = 0.0
    change_jobs = 0

    for job in sorted(jobs, key=lambda j: j["start"], reverse=True):
        if job["status"] == "completed":
            frac = 1.0
        else:
            frac = min(1.0, job["used_mm"] / job["total_mm"]) if job["total_mm"] > 0 else 0.0
        ps = float(job.get("print_s") or 0.0)
        time_all += ps
        if tsince and job["start"] >= tsince:
            time_since += ps
        if job["start"] >= since:
            time_period += ps
            if job["status"] == "completed" and ps > 0:
                time_done += ps
                n_done += 1
        rows = []
        for i, w in enumerate(job["weights"]):
            gr = w * frac
            if gr < 0.01:
                continue
            name = job["names"][i] if i < len(job["names"]) else ""
            m = re.search(r"\(SM(\d+)\)", name)
            f = fils.get(int(m.group(1))) if m else None
            if f:
                vendor = str((f.get("vendor") or {}).get("name") or "")
                label = " ".join(x for x in (vendor, str(f.get("name") or "")) if x) or str(f.get("material") or "")
                colour = _hex(f.get("color_hex") or f.get("multi_color_hexes"))
                material = str(f.get("material") or "?")
                ppg = float(f["price"]) / float(f["weight"]) if f.get("price") and f.get("weight") else None
                key = f"SM{f['id']}"
            else:
                label = re.sub(r"^_(local|subscribed)/[^/]+/", "", name).split(" @")[0].strip() or f"Tool {i}"
                colour = _hex(job["colors"][i] if i < len(job["colors"]) else "")
                material = job["types"][i] if i < len(job["types"]) else "?"
                ppg, key = None, "N:" + label
            rows.append({"tool": i, "key": key, "label": label, "colour": colour, "material": material or "?",
                         "g": gr, "cost": gr * ppg if ppg else None})
        if not rows:
            continue
        total_all += sum(r["g"] for r in rows)
        if tsince and job["start"] >= tsince:
            total_tools += sum(r["g"] for r in rows)
        if job["start"] >= w0:
            idx = min(WEEKS - 1, int((job["start"] - w0) // (7 * 86400)))
            for r in rows:
                weeks[idx][r["colour"]] = weeks[idx].get(r["colour"], 0.0) + r["g"]
        if job["start"] < since:
            continue
        if job["status"] == "completed":
            prints += 1
        else:
            cancelled += 1
        if any(not r["key"].startswith("SM") for r in rows):
            unmatched += 1
        if job["start"] >= tsince:
            for r in rows:
                tl = tools.setdefault(r["tool"], {"g": 0.0, "prints": 0})
                tl["g"] += r["g"]
                tl["prints"] += 1
            if job.get("changes") is not None:
                changes += job["changes"] * frac
                change_jobs += 1
        jg = sum(r["g"] for r in rows)
        jc = [r["cost"] for r in rows if r["cost"] is not None]
        total += jg
        cost += sum(jc)
        for r in rows:
            u = used.setdefault(r["key"], {"label": r["label"], "colour": r["colour"], "g": 0.0, "cost": 0.0})
            u["g"] += r["g"]
            u["cost"] += r["cost"] or 0.0
            mats[r["material"]] = mats.get(r["material"], 0.0) + r["g"]
        hist.append({"t": job["start"], "end": job.get("end") or 0.0, "file": job["name"].split("/")[-1],
                     "name": re.sub(r"\.gcode$", "", job["name"].split("/")[-1]),
                     "status": job["status"], "g": jg, "cost": sum(jc) if jc else None,
                     "colours": [r["colour"] for r in rows], "print_s": ps})

    low = []
    for s in spools:
        if s.get("archived") or s.get("remaining_weight") is None:
            continue
        rem = float(s["remaining_weight"])
        if rem >= low_g:
            continue
        f = s.get("filament") or {}
        vendor = str((f.get("vendor") or {}).get("name") or "")
        init = float(s.get("initial_weight") or f.get("weight") or 0) or 1000.0
        low.append({"label": " ".join(x for x in (vendor, str(f.get("name") or "")) if x) or f"Spool {s.get('id')}",
                    "colour": _hex(f.get("color_hex") or f.get("multi_color_hexes")), "g": rem,
                    "pct": max(0.0, min(1.0, rem / init)), "id": s.get("id")})
    low.sort(key=lambda x: x["g"])

    variables = variables or {}
    by_spool = {str(s.get("id")): s for s in spools}
    tool_rows = []
    n_tools = max([9] + [k + 1 for k in tools])
    for i in range(n_tools):
        sp = by_spool.get(str(variables.get(f"t{i}__spool_id")))
        f = (sp or {}).get("filament") or {}
        vendor = str((f.get("vendor") or {}).get("name") or "")
        spool_label = " ".join(x for x in (vendor, str(f.get("name") or "")) if x)
        tl = tools.get(i, {"g": 0.0, "prints": 0})
        tool_rows.append({"label": f"T{i}" + (f" \u00b7 {spool_label}" if spool_label else ""),
                          "colour": _hex(f.get("color_hex") or f.get("multi_color_hexes")) if sp else "",
                          "g": tl["g"], "prints": tl["prints"]})
    try:
        indx_count = int(variables["toolchange_count"]) if "toolchange_count" in variables else None
    except (TypeError, ValueError):
        indx_count = None

    week_rows = []
    for i, parts in enumerate(weeks):
        ws = w0 + i * 7 * 86400
        week_rows.append({"label": time.strftime("%d.%m.", time.localtime(ws)), "total": sum(parts.values()),
                          "parts": [{"c": c, "g": g} for c, g in sorted(parts.items(), key=lambda x: -x[1])]})
    return {"total_g": total, "prints": prints, "cancelled": cancelled, "cost": cost, "currency": currency,
            "low": low, "low_g": low_g, "weeks": week_rows,
            "materials": [{"name": k, "g": v} for k, v in sorted(mats.items(), key=lambda x: -x[1])],
            "most": sorted(used.values(), key=lambda x: -x["g"])[:8],
            "history": hist[:200], "history_count": len(hist), "unmatched": unmatched, "jobs_total": len(jobs),
            "tools": tool_rows, "changes": round(changes), "change_jobs": change_jobs, "indx_changes": indx_count,
            "tools_since": tools_since if tsince > since else "",
            "total_all": total_all, "since_date": tools_since if tsince else "", "total_since": total_tools,
            "printer_m": float((totals or {}).get("total_filament_used") or 0) / 1000.0,
            "time_period": time_period, "time_all": time_all, "time_since": time_since,
            "time_avg": time_done / n_done if n_done else 0.0,
            "printer_time": float((totals or {}).get("total_print_time") or 0)}


def _stats_and_post(period) -> None:
    try:
        period = int(period)
    except (TypeError, ValueError):
        period = 30
    s = load_settings()
    try:
        if not s["moonraker_url"]:
            raise ValueError("Enter the printer address (Moonraker) in File > Plugins > Config > "
                             "Update profiles from Spoolman.")
        if not s["spoolman_url"]:
            raise ValueError("Enter the Spoolman address in File > Plugins > Config > Update profiles from Spoolman.")
        jobs = fetch_history(s["moonraker_url"])
        filaments, spools = fetch_spoolman(s["spoolman_url"])
        d = compute_stats(jobs, filaments, spools, period, s["low_stock_g"], spoolman_currency(s["spoolman_url"]),
                          variables=printer_variables(s["moonraker_url"]), tools_since=s["tools_since"],
                          totals=printer_totals(s["moonraker_url"]))
        attach_timelapses(d["history"], fetch_timelapses(s["moonraker_url"]), s["moonraker_url"])
        for h in d["history"]:
            h.pop("file", None)
            h.pop("end", None)
        d["timelapses"] = sum(1 for h in d["history"] if h.get("timelapse"))
        d.update(type="stats", period=period, time=time.strftime("%H:%M"), show_tools=s["show_tools"])
    except urllib.error.URLError as exc:
        d = {"type": "stats", "error": f"Not reachable: {getattr(exc, 'reason', exc)}"}
    except ValueError as exc:
        d = {"type": "stats", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        log.exception("Statistics failed")
        d = {"type": "stats", "error": f"Statistics failed: {exc}"}
    _post(d)


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
    built = build_profiles(filaments, spools, templates, s["include_all"], s["spoolio_fallback"], s["name_format"])
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
                           "close Orca and delete only their .json files in filament/base of your user "
                           "folder. Keep the .info files: on the next start Orca deletes the copies in "
                           "Orca Cloud too (with the .info gone, they come back from the cloud).")
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


def extra_link(s: dict) -> str:
    """The configured extra link with {printer} replaced; "" when unset or not http(s)."""
    u = s["link_url"].replace("{printer}", s["moonraker_url"]).replace("{spoolman}", s["spoolman_url"])
    return u if u.startswith(("http://", "https://")) else ""


def _handle_ui_message(data) -> None:
    """Messages from the Spools & Stats page (tab or window)."""
    try:
        if isinstance(data, str):
            data = json.loads(data)
        if not isinstance(data, dict):
            return
        t = data.get("type")
        if t == "ready":
            _post({"type": "ui", **load_ui()})
            _post_status()
        elif t == "sync":
            _bg(_sync_and_notify, "button")
        elif t == "stats":
            _bg(_stats_and_post, data.get("period"))
        elif t == "ui":
            save_ui(data)
        elif t == "open":
            u, base = str(data.get("url") or ""), load_settings()["moonraker_url"]
            if base and u.startswith(base + "/server/files/timelapse/"):
                _bg(webbrowser.open, u)
        elif t == "link":
            u = extra_link(load_settings())
            if u:
                _bg(webbrowser.open, u)
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
:root { color-scheme: light dark;
  --line: rgba(127,127,127,.22); --surf: rgba(127,127,127,.07); --surf2: rgba(127,127,127,.12);
  --muted: .66; --acc: #009688; --acc-soft: rgba(0,150,136,.16);
  --ok: #2e9d57; --bad: #d64545; --warn: #e8890c; --blue: #3b82f6; --cyan: #06b6d4; --amber: #d9a400; --violet: #8b5cf6;
  --r: 10px; }
* { box-sizing: border-box; }
html, body { margin: 0; font: 13px/1.45 "Segoe UI", system-ui, sans-serif; }
body { padding: 14px 16px 24px; max-width: 1400px; }
svg.i { width: 16px; height: 16px; flex: none; fill: none; stroke: currentColor; stroke-width: 2;
  stroke-linecap: round; stroke-linejoin: round; }
.muted { opacity: var(--muted); }

/* sections */
.seg { border: 1px solid var(--line); border-radius: var(--r); margin: 0 0 14px; overflow: hidden; }
.seg > header { display: flex; align-items: center; gap: 10px; padding: 11px 14px; cursor: pointer;
  user-select: none; background: var(--surf); }
.seg > header:hover { background: var(--surf2); }
.seg > header .ico { width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  background: var(--acc-soft); color: var(--acc); }
.seg > header h2 { margin: 0; font-size: 14px; font-weight: 600; }
.seg > header .chip { margin-left: auto; }
.seg > header .chev { transition: transform .18s ease; opacity: .7; }
.seg.closed > header .chev { transform: rotate(-90deg); }
.seg.closed > .body { display: none; }
.seg > .body { padding: 14px; border-top: 1px solid var(--line); }
.chip { display: inline-flex; align-items: center; gap: 6px; padding: 2px 10px; border-radius: 999px;
  font-size: 12px; background: var(--surf2); white-space: nowrap; }
.chip.ok { background: rgba(46,157,87,.16); color: var(--ok); }
.chip.bad { background: rgba(214,69,69,.16); color: var(--bad); }
.chip .pip { width: 7px; height: 7px; border-radius: 50%; background: currentColor; }

/* controls */
.row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
button { font: inherit; display: inline-flex; align-items: center; gap: 7px; padding: 7px 14px;
  border-radius: 8px; border: 1px solid var(--line); background: transparent; color: inherit; cursor: pointer;
  transition: background .15s, border-color .15s, opacity .15s; }
button:hover { background: var(--surf2); }
button.primary { background: var(--acc); border-color: var(--acc); color: #fff; font-weight: 600; }
button.primary:hover { filter: brightness(1.08); background: var(--acc); }
button:disabled { opacity: .5; cursor: default; }
button.icon { padding: 7px 9px; }
.seg-ctl { display: inline-flex; border: 1px solid var(--line); border-radius: 8px; overflow: hidden; }
.seg-ctl button { border: 0; border-radius: 0; padding: 6px 12px; }
.seg-ctl button + button { border-left: 1px solid var(--line); }
.seg-ctl button.on { background: var(--acc); color: #fff; }
.spin svg { animation: spin 1s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

/* status */
.status { margin-top: 12px; padding: 10px 12px; border-radius: 8px; background: var(--surf);
  border-left: 3px solid var(--line); white-space: pre-wrap; }
.status.ok { border-left-color: var(--ok); }
.status.bad { border-left-color: var(--bad); }
.status .t { font-size: 12px; opacity: var(--muted); margin-bottom: 2px; }

/* KPI cards */
.kpis { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 10px; margin: 14px 0; }
.kpi { position: relative; padding: 12px 14px; border-radius: var(--r); background: var(--surf);
  border: 1px solid var(--line); overflow: hidden; }
.kpi::before { content: ""; position: absolute; inset: 0 0 auto 0; height: 3px; background: var(--c, var(--acc)); }
.kpi .h { display: flex; align-items: center; gap: 8px; font-size: 12px; opacity: .8; }
.kpi .h .ic { width: 24px; height: 24px; border-radius: 7px; display: grid; place-items: center;
  color: var(--c, var(--acc)); background: color-mix(in srgb, var(--c, var(--acc)) 16%, transparent); }
.kpi .v { font-size: 24px; font-weight: 650; margin-top: 8px; letter-spacing: -.01em; }
.kpi .v.warn { color: var(--warn); }
.kpi .s { font-size: 11.5px; opacity: var(--muted); margin-top: 2px; }

/* panels */
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 10px; margin-bottom: 10px; }
.panel { padding: 12px 14px; border-radius: var(--r); background: var(--surf); border: 1px solid var(--line); min-width: 0; }
.panel h3 { margin: 0 0 10px; font-size: 13px; font-weight: 600; display: flex; align-items: baseline; gap: 8px; }
.panel h3 small { font-weight: 400; opacity: var(--muted); font-size: 11.5px; }
.empty { opacity: var(--muted); padding: 6px 0; }
.err { color: var(--bad); white-space: pre-wrap; margin-top: 12px; padding: 10px 12px; border-radius: 8px;
  background: rgba(214,69,69,.1); }

/* bar rows */
.br { display: grid; grid-template-columns: minmax(130px, 42%) 1fr auto; gap: 10px; align-items: center; padding: 4px 0; }
.br .n { display: flex; align-items: center; gap: 8px; min-width: 0; }
.br .n .lbl { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sw { width: 12px; height: 12px; border-radius: 4px; flex: none; box-shadow: inset 0 0 0 1px rgba(127,127,127,.45); }
.tk { font-size: 11px; font-weight: 650; padding: 1px 6px; border-radius: 5px; background: var(--surf2); flex: none; }
.track { height: 9px; border-radius: 99px; background: var(--surf2); overflow: hidden; }
.fill { height: 100%; border-radius: 99px; background: var(--acc); transition: width .4s ease; }
.val { text-align: right; white-space: nowrap; font-variant-numeric: tabular-nums; }
.val small { opacity: var(--muted); margin-left: 4px; }

/* week chart */
.chart { display: grid; grid-template-columns: auto 1fr; gap: 6px; }
.yax { display: flex; flex-direction: column; justify-content: space-between; font-size: 10.5px;
  opacity: var(--muted); text-align: right; height: 160px; padding-bottom: 0; }
.plot { position: relative; height: 160px; display: flex; align-items: flex-end; gap: 8px;
  background: repeating-linear-gradient(to top, var(--line) 0 1px, transparent 1px 25%); }
.wk { flex: 1; height: 100%; display: flex; flex-direction: column-reverse; min-width: 0; border-radius: 6px 6px 0 0;
  overflow: hidden; }
.wk:hover { filter: brightness(1.1); }
.wk i { display: block; width: 100%; box-shadow: inset 0 1px 0 rgba(0,0,0,.18); }
.xax { grid-column: 2; display: flex; gap: 8px; font-size: 10.5px; opacity: var(--muted); }
.xax span { flex: 1; text-align: center; white-space: nowrap; }

/* donut */
.donut-wrap { display: flex; gap: 18px; align-items: center; flex-wrap: wrap; }
.donut-box { width: 132px; height: 132px; flex: none; position: relative; }
.donut { position: absolute; inset: 0; border-radius: 50%;
  -webkit-mask: radial-gradient(farthest-side, transparent calc(100% - 22px), #000 calc(100% - 21px));
          mask: radial-gradient(farthest-side, transparent calc(100% - 22px), #000 calc(100% - 21px)); }
.donut-box .c { position: absolute; inset: 0; display: grid; place-items: center; z-index: 1; text-align: center;
  font-weight: 650; font-size: 15px; line-height: 1.2; }
.donut-box .c small { display: block; font-weight: 400; font-size: 11px; opacity: var(--muted); }
.legend { flex: 1; min-width: 160px; }
.legend .li { display: grid; grid-template-columns: auto 1fr auto auto; gap: 8px; align-items: center; padding: 3px 0; }
.legend .pct { opacity: var(--muted); min-width: 3.2em; text-align: right; font-variant-numeric: tabular-nums; }

/* history */
details.hist > summary { cursor: pointer; list-style: none; display: flex; align-items: center; gap: 8px; font-weight: 600; }
details.hist > summary::-webkit-details-marker { display: none; }
details.hist > summary .chev { transition: transform .18s; transform: rotate(-90deg); opacity: .7; }
details.hist[open] > summary .chev { transform: none; }
.tbl { max-height: 420px; overflow: auto; margin-top: 10px; border-radius: 8px; border: 1px solid var(--line); }
table { width: 100%; border-collapse: collapse; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--line); }
th { position: sticky; top: 0; background: var(--bg, Canvas); font-weight: 600; font-size: 12px; opacity: .9; z-index: 1; }
tr:nth-child(even) td { background: var(--surf); }
td.r, th.r { text-align: right; white-space: nowrap; }
td .sws { display: flex; gap: 3px; }
a.tl { display: inline-flex; align-items: center; gap: 6px; color: var(--acc); text-decoration: none; cursor: pointer;
  white-space: nowrap; }
a.tl:hover { text-decoration: underline; }
a.tl img { width: 44px; height: 25px; object-fit: cover; border-radius: 4px; box-shadow: 0 0 0 1px var(--line); }
a.tl .play { width: 14px; height: 14px; }
.modal { position: fixed; inset: 0; background: rgba(0,0,0,.72); display: none; align-items: center; justify-content: center;
  z-index: 50; padding: 24px; }
.modal.on { display: flex; }
.modal .box { width: min(1100px, 100%); background: var(--bg, Canvas); border-radius: 12px; overflow: hidden;
  box-shadow: 0 20px 60px rgba(0,0,0,.5); }
.modal .bar { display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--line); }
.modal .bar b { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.modal video { display: block; width: 100%; max-height: 75vh; background: #000; }
.st { font-size: 11.5px; padding: 1px 8px; border-radius: 99px; background: var(--surf2); white-space: nowrap; }
.st.completed { background: rgba(46,157,87,.16); color: var(--ok); }
.st.error, .st.klippy_shutdown, .st.klippy_disconnect { background: rgba(214,69,69,.16); color: var(--bad); }
.foot { font-size: 11.5px; opacity: var(--muted); margin-top: 10px; }
</style></head><body>

<svg width="0" height="0" style="position:absolute">
  <symbol id="i-spool" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3"/></symbol>
  <symbol id="i-chart" viewBox="0 0 24 24"><path d="M3 3v18h18"/><path d="M7 15v3M12 10v8M17 6v12"/></symbol>
  <symbol id="i-sync" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-3-6.7L21 8"/><path d="M21 3v5h-5"/></symbol>
  <symbol id="i-ext" viewBox="0 0 24 24"><path d="M14 4h6v6"/><path d="M20 4l-9 9"/><path d="M19 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1h5"/></symbol>
  <symbol id="i-chev" viewBox="0 0 24 24"><path d="M6 9l6 6 6-6"/></symbol>
  <symbol id="i-weight" viewBox="0 0 24 24"><path d="M6 8h12l2 12H4z"/><circle cx="12" cy="5" r="2"/></symbol>
  <symbol id="i-print" viewBox="0 0 24 24"><path d="M4 20h16M6 20V9l6-5 6 5v11"/><path d="M10 20v-6h4v6"/></symbol>
  <symbol id="i-coin" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M15 9.5a3 3 0 1 0 0 5M8 11h5M8 13h5"/></symbol>
  <symbol id="i-swap" viewBox="0 0 24 24"><path d="M7 7h11l-3-3M17 17H6l3 3"/></symbol>
  <symbol id="i-clock" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></symbol>
  <symbol id="i-play" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M10 9l5 3-5 3z"/></symbol>
  <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 3l9 16H3z"/><path d="M12 10v4M12 17h.01"/></symbol>
</svg>

<section class="seg" id="seg-profiles">
  <header><span class="ico"><svg class="i"><use href="#i-spool"/></svg></span><h2>Profiles</h2>
    <span class="chip" id="psub">not synced yet</span><svg class="i chev"><use href="#i-chev"/></svg></header>
  <div class="body">
    <div class="row">
      <button class="primary" id="sync"><svg class="i"><use href="#i-sync"/></svg>Update profiles from Spoolman</button>
      <button id="browser"><svg class="i"><use href="#i-ext"/></svg>Open Spoolman in browser</button>
      <button id="xlink" style="display:none"><svg class="i"><use href="#i-ext"/></svg><span></span></button>
    </div>
    <div class="status" id="status"><div class="t">Status</div><div id="stext">Not synced yet.</div></div>
  </div>
</section>

<section class="seg" id="seg-stats">
  <header><span class="ico"><svg class="i"><use href="#i-chart"/></svg></span><h2>Statistics</h2>
    <span class="chip" id="ssub">–</span><svg class="i chev"><use href="#i-chev"/></svg></header>
  <div class="body">
    <div class="row">
      <div class="seg-ctl" id="period">
        <button data-p="7">7 days</button><button data-p="30">30 days</button><button data-p="90">90 days</button>
        <button data-p="365">12 months</button><button data-p="0">All</button>
      </div>
      <button class="icon" id="reload" title="Refresh"><svg class="i"><use href="#i-sync"/></svg></button>
      <span class="muted" id="stime"></span>
    </div>
    <div id="stats"><div class="empty" style="margin-top:12px">Loading …</div></div>
  </div>
</section>

<div class="modal" id="modal"><div class="box"><div class="bar"><b id="mtitle"></b>
  <button id="mopen"><svg class="i"><use href="#i-ext"/></svg>Open in browser</button><button id="mclose">Close</button></div>
  <video id="mvideo" controls playsinline></video></div></div>

<div class="foot">Settings: File › Plugins › Config › Update profiles from Spoolman.</div>

<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  // Sticky table header needs an opaque colour: take the page's own background.
  (function () {
    var bg = getComputedStyle(document.body).backgroundColor;
    if (!bg || bg === "transparent" || bg === "rgba(0, 0, 0, 0)") bg = getComputedStyle(document.documentElement).backgroundColor;
    if (bg && bg !== "transparent" && bg !== "rgba(0, 0, 0, 0)") document.documentElement.style.setProperty("--bg", bg);
  })();
  var collapsed = { profiles: false, stats: false }, period = 30, loaded = false;
  var MAT = ["#009688", "#3b82f6", "#e8890c", "#8b5cf6", "#d64545", "#d9a400", "#14b8a6", "#ec4899", "#64748b"];
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]; }); }
  function hours(s) {
    s = Math.round(s / 60);  // minutes
    if (s < 60) return s + " min";
    var h = Math.floor(s / 60), m = s % 60;
    return h >= 100 ? h.toLocaleString() + " h" : h + " h" + (m ? " " + m + " min" : "");
  }
  function grams(x) { return x >= 1000 ? (x / 1000).toFixed(2).replace(".", ",") + " kg" : Math.round(x) + " g"; }
  function money(x, cur) { return x == null ? "–" : x.toFixed(2).replace(".", ",") + " " + cur; }
  function sw(c) { return '<span class="sw" style="background:' + esc(c) + '"></span>'; }
  function ico(id) { return '<svg class="i"><use href="#i-' + id + '"/></svg>'; }
  function apply() {
    ["profiles", "stats"].forEach(function (k) { $("seg-" + k).classList.toggle("closed", !!collapsed[k]); });
    Array.prototype.forEach.call($("period").children, function (b) { b.classList.toggle("on", +b.dataset.p === period); });
  }
  function saveUi() { orca.postMessage({ type: "ui", collapsed: collapsed, period: period }); }
  function loadStats() {
    loaded = true;
    $("reload").classList.add("spin");
    $("stime").textContent = "Loading …";
    orca.postMessage({ type: "stats", period: period });
  }
  ["profiles", "stats"].forEach(function (k) {
    $("seg-" + k).querySelector("header").onclick = function () {
      collapsed[k] = !collapsed[k]; apply(); saveUi();
      if (k === "stats" && !collapsed.stats && !loaded) loadStats();
    };
  });
  function bar(label, g, max, colour, extra, prefix) {
    var w = max > 0 ? Math.max(g > 0 ? 1.5 : 0, 100 * g / max) : 0;
    return '<div class="br"><div class="n">' + (prefix || "") + (colour ? sw(colour) : "") +
      '<span class="lbl" title="' + esc(label) + '">' + esc(label) + '</span></div>' +
      '<div class="track"><div class="fill" style="width:' + w.toFixed(1) + '%' + (colour ? ';background:' + esc(colour) : '') +
      '"></div></div><div class="val">' + grams(g) + (extra ? '<small>' + extra + '</small>' : '') + '</div></div>';
  }
  function kpi(icon, colour, label, value, sub, cls) {
    return '<div class="kpi" style="--c:' + colour + '"><div class="h"><span class="ic">' + ico(icon) + '</span>' + label +
      '</div><div class="v' + (cls ? " " + cls : "") + '">' + value + '</div><div class="s">' + (sub || "&nbsp;") + '</div></div>';
  }
  function weeks(d) {
    var max = Math.max.apply(null, d.weeks.map(function (w) { return w.total; }).concat([0]));
    if (max <= 0) return '<div class="empty">Nothing printed in the last 8 weeks.</div>';
    var step = Math.pow(10, Math.floor(Math.log10(max))), top = Math.ceil(max / step) * step;
    if (top / step > 6) { step *= 2; top = Math.ceil(max / step) * step; }
    var ticks = [top, top * .75, top * .5, top * .25, 0].map(function (v) { return v >= 1000 ? (v / 1000).toFixed(1) + " kg" : Math.round(v) + " g"; });
    return '<div class="chart"><div class="yax">' + ticks.map(function (t) { return '<span>' + t + '</span>'; }).join("") + '</div>' +
      '<div class="plot">' + d.weeks.map(function (w) {
        return '<div class="wk" title="Week of ' + esc(w.label) + ': ' + grams(w.total) + '">' + w.parts.map(function (p) {
          return '<i style="height:' + (100 * p.g / top).toFixed(2) + '%;background:' + esc(p.c) + '"></i>'; }).join("") + '</div>';
      }).join("") + '</div><div class="xax">' + d.weeks.map(function (w) { return '<span>' + esc(w.label) + '</span>'; }).join("") +
      '</div></div>';
  }
  function donut(d) {
    if (!d.materials.length) return '<div class="empty">Nothing printed in this period.</div>';
    var acc = 0, stops = [], items = d.materials.slice(0, 8);
    var rest = d.materials.slice(8).reduce(function (s, m) { return s + m.g; }, 0);
    if (rest > 0) items.push({ name: "Other", g: rest });
    items.forEach(function (m, i) {
      var a = 100 * acc / d.total_g; acc += m.g; var b = 100 * acc / d.total_g;
      stops.push(MAT[i % MAT.length] + " " + a.toFixed(2) + "% " + b.toFixed(2) + "%");
    });
    return '<div class="donut-wrap"><div class="donut-box"><div class="donut" style="background:conic-gradient(' + stops.join(",") + ')"></div>' +
      '<div class="c"><span>' + grams(d.total_g) + '<small>' + items.length + (items.length === 1 ? ' material' : ' materials') + '</small></span></div></div>' +
      '<div class="legend">' + items.map(function (m, i) {
        return '<div class="li">' + sw(MAT[i % MAT.length]) + '<span>' + esc(m.name) + '</span><span class="pct">' +
          Math.round(100 * m.g / d.total_g) + ' %</span><span class="val">' + grams(m.g) + '</span></div>'; }).join("") + '</div></div>';
  }
  function render(d) {
    $("reload").classList.remove("spin");
    if (d.error) { $("stats").innerHTML = '<div class="err">' + esc(d.error) + '</div>'; $("stime").textContent = ""; return; }
    $("stime").textContent = "Updated " + d.time + " · " + d.jobs_total + " jobs in the printer history";
    $("ssub").textContent = grams(d.total_g) + " · " + d.prints + " prints" +
      (d.since_date && d.show_tools !== false ? " · since " + d.since_date + ": " + grams(d.total_since) : "") +
      (d.show_tools !== false && d.indx_changes != null ? " · " + d.indx_changes.toLocaleString() + " tool changes (INDX)" : "");
    var cur = d.currency, h = '<div class="kpis">';
    h += kpi("weight", "var(--acc)", "Filament used", grams(d.total_g),
      "All time: <b>" + grams(d.total_all) + "</b>" +
      (d.since_date ? "<br>Since " + esc(d.since_date) + (d.show_tools !== false ? " (tool changer)" : "") + ": <b>" + grams(d.total_since) + "</b>" : "") +
      (d.printer_m ? "<br>Printer counter: " + Math.round(d.printer_m).toLocaleString() + " m" : ""));
    h += kpi("clock", "var(--cyan)", "Print time", hours(d.time_period),
      "All time: <b>" + hours(d.printer_time || d.time_all) + "</b>" +
      (d.since_date ? "<br>Since " + esc(d.since_date) + ": <b>" + hours(d.time_since) + "</b>" : "") +
      (d.time_avg ? "<br>Ø " + hours(d.time_avg) + " per finished print" : ""));
    h += kpi("print", "var(--blue)", "Prints", d.prints, d.cancelled ? d.cancelled + " cancelled / failed (counted partly)" : "none cancelled");
    h += kpi("coin", "var(--amber)", "Filament cost", d.cost > 0 ? money(d.cost, cur) : "–", "from Spoolman prices");
    if (d.show_tools !== false)
      h += kpi("swap", "var(--violet)", "Tool changes" + (d.tools_since ? ' <span class="muted">since ' + esc(d.tools_since) + '</span>' : ''),
        d.changes.toLocaleString(), (d.change_jobs ? "Ø " + Math.round(d.changes / d.change_jobs) + " per print · " : "") +
        (d.indx_changes != null ? "INDX counter " + d.indx_changes.toLocaleString() : "from the print files"));
    h += kpi("alert", d.low.length ? "var(--warn)" : "var(--ok)", "Low stock", d.low.length, "spools under " + d.low_g + " g", d.low.length ? "warn" : "");
    h += '</div><div class="grid"><div class="panel"><h3>Used per week <small>last 8 weeks, by filament colour</small></h3>' + weeks(d) +
      '</div><div class="panel"><h3>Materials</h3>' + donut(d) + '</div></div>';
    h += '<div class="grid"><div class="panel"><h3>Most used</h3>' + (d.most.length ? d.most.map(function (m) {
      return bar(m.label, m.g, d.most[0].g, m.colour, m.cost ? money(m.cost, cur) : ""); }).join("") : '<div class="empty">Nothing printed in this period.</div>') + '</div>';
    h += '<div class="panel"><h3>Running low <small>under ' + d.low_g + ' g</small></h3>' + (d.low.length ? d.low.map(function (s) {
      var c = s.pct < .25 ? "var(--bad)" : "var(--warn)";
      return '<div class="br"><div class="n">' + sw(s.colour) + '<span class="lbl" title="' + esc(s.label) + '">' + esc(s.label) +
        '</span></div><div class="track"><div class="fill" style="width:' + Math.max(2, 100 * s.pct).toFixed(1) + '%;background:' + c +
        '"></div></div><div class="val">' + grams(s.g) + '</div></div>'; }).join("") : '<div class="empty">No spool under ' + d.low_g + ' g.</div>') + '</div></div>';
    if (d.show_tools !== false) {
      var tmax = Math.max.apply(null, d.tools.map(function (x) { return x.g; }).concat([0]));
      h += '<div class="panel" style="margin-bottom:10px"><h3>Per tool <small>colour and name: spool assigned now' +
        (d.tools_since ? ' · prints since ' + esc(d.tools_since) : '') + '</small></h3>' + (tmax > 0 ? d.tools.map(function (x) {
          var m = /^T(\d+)(?: · (.*))?$/.exec(x.label) || [], tk = '<span class="tk">T' + (m[1] || "?") + '</span>';
          return bar(m[2] || "no spool assigned", x.g, tmax, x.colour, x.prints + "×", tk); }).join("")
        : '<div class="empty">Nothing printed in this period.</div>') + '</div>';
    }
    var st = { completed: "done", cancelled: "cancelled", error: "error", klippy_shutdown: "shutdown", klippy_disconnect: "disconnected", interrupted: "interrupted" };
    h += '<div class="panel"><details class="hist"><summary><svg class="i chev"><use href="#i-chev"/></svg>Print history' +
      '<span class="chip">' + d.history_count + ' prints · ' + grams(d.total_g) + ' · ' + hours(d.time_period) + '</span>' +
      (d.timelapses ? '<span class="chip">' + d.timelapses + ' timelapses</span>' : '') + '</summary><div class="tbl"><table>' +
      '<tr><th>Date</th><th>Name</th><th>Filament</th><th>Status</th><th class="r">Duration</th><th class="r">Used</th><th class="r">Cost</th>' +
      (d.timelapses ? '<th>Timelapse</th>' : '') + '</tr>' +
      d.history.map(function (j) {
        var dt = new Date(j.t * 1000);
        return '<tr><td>' + dt.toLocaleDateString() + ' ' + dt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) +
          '</td><td>' + esc(j.name) + '</td><td><span class="sws">' + j.colours.map(sw).join("") + '</span></td><td><span class="st ' +
          esc(j.status) + '">' + esc(st[j.status] || j.status) + '</span></td><td class="r">' + (j.print_s ? hours(j.print_s) : '–') +
          '</td><td class="r">' + grams(j.g) + '</td><td class="r">' +
          money(j.cost, cur) + '</td>' + (d.timelapses ? '<td>' + (j.timelapse ? '<a class="tl" data-url="' + esc(j.timelapse) + '" data-name="' + esc(j.name) +
          '" title="Play the timelapse">' + (j.thumb ? '<img src="' + esc(j.thumb) + '" alt="">' : '') +
          '<svg class="i play"><use href="#i-play"/></svg>Video</a>' : '') + '</td>' : '') + '</tr>'; }).join("") + '</table></div>' +
      (d.history_count > d.history.length ? '<div class="foot">Showing the newest ' + d.history.length + '.</div>' : '') + '</details></div>';
    var notes = ["Grams per tool and tool changes per print are the slicer’s values; cancelled prints count with the printed share."];
    if (d.unmatched) notes.push(d.unmatched + " prints used filament profiles without a Spoolman ID (SM<id>): counted with material and colour from the print file, without cost.");
    if (d.show_tools !== false && d.indx_changes != null) notes.push("The INDX counter counts every real tool change since it was last reset.");
    h += '<div class="foot">' + notes.map(esc).join("<br>") + '</div>';
    $("stats").innerHTML = h;
  }
  orca.onMessage(function (m) {
    if (!m) return;
    if (m.type === "settings") {
      var s = m.settings || {}, b = $("xlink"), on = !!(s.link_url && s.link_label);
      b.style.display = on ? "" : "none";
      b.querySelector("span").textContent = s.link_label || "";
      b.title = (s.link_url || "").replace("{printer}", s.moonraker_url || "").replace("{spoolman}", s.spoolman_url || "");
    } else if (m.type === "ui") {
      collapsed = m.collapsed || collapsed; period = m.period; apply();
      if (!collapsed.stats && !loaded) loadStats();
    } else if (m.type === "status") {
      var box = $("status"), chip = $("psub");
      $("stext").textContent = m.text;
      box.className = "status" + (m.ok === true ? " ok" : m.ok === false ? " bad" : "");
      box.querySelector(".t").textContent = m.time ? "Last update " + m.time : "Status";
      chip.className = "chip" + (m.ok === true ? " ok" : m.ok === false ? " bad" : "");
      chip.innerHTML = m.busy ? "updating …" : m.time ? '<span class="pip"></span>' + (m.ok === false ? "error " : "up to date · ") + esc(m.time) : "not synced yet";
      $("sync").disabled = !!m.busy;
    } else if (m.type === "stats") {
      render(m);
    }
  });
  $("sync").onclick = function () { $("sync").disabled = true; $("stext").textContent = "Updating …"; $("status").className = "status"; orca.postMessage({ type: "sync" }); };
  $("browser").onclick = function () { orca.postMessage({ type: "browser" }); };
  $("xlink").onclick = function () { orca.postMessage({ type: "link" }); };
  Array.prototype.forEach.call($("period").children, function (b) {
    b.onclick = function () { period = +b.dataset.p; apply(); saveUi(); loadStats(); };
  });
  $("reload").onclick = loadStats;
  $("stats").addEventListener("click", function (e) {
    var a = e.target.closest ? e.target.closest("a.tl") : null;
    if (!a) return;
    e.preventDefault();
    var v = $("mvideo");
    $("mtitle").textContent = a.getAttribute("data-name") || "Timelapse";
    $("mopen").setAttribute("data-url", a.getAttribute("data-url"));
    v.src = a.getAttribute("data-url"); $("modal").classList.add("on"); v.play().catch(function () {});
  });
  function closeModal() { var v = $("mvideo"); v.pause(); v.removeAttribute("src"); v.load(); $("modal").classList.remove("on"); }
  $("mclose").onclick = closeModal;
  $("modal").addEventListener("click", function (e) { if (e.target === $("modal")) closeModal(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeModal(); });
  $("mopen").onclick = function () { orca.postMessage({ type: "open", url: $("mopen").getAttribute("data-url") }); };
  apply();
  orca.postMessage({ type: "ready" });
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


NOADDR_HTML = r"""<!doctype html><html><head><meta charset="utf-8"><style>
:root { color-scheme: light dark; } body { margin: 24px; font: 13px "Segoe UI", system-ui, sans-serif; }
</style></head><body>
<p>No Spoolman address set.</p>
<p>Enter it in File &gt; Plugins &gt; Config &gt; Update profiles from Spoolman, then restart OrcaSlicer.</p>
</body></html>"""


def _tab_html() -> str:
    url = load_settings()["spoolman_url"]
    if url.startswith(("http://", "https://")):
        return REDIRECT_HTML.replace("__URL__", json.dumps(url).replace("</", "<\\/"))
    return NOADDR_HTML


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
<label class="t"><span>Printer address (Moonraker, for the statistics)</span><input type="text" name="moonraker_url" placeholder="http://printer.local"></label>
<label class="t"><span>Low stock below (g)</span><input type="text" name="low_stock_g" placeholder="100"></label>
<label><input type="checkbox" name="show_tools"> Show per-tool statistics and tool changes (for tool changers)</label>
<label class="t"><span>Tool statistics since (YYYY-MM-DD)</span><input type="text" name="tools_since" placeholder="e.g. 2026-11-09">
<small>Date the tool changer was installed. Per-tool grams and tool changes ignore older prints; empty = all.</small></label>
<label class="t"><span>Extra link: button label</span><input type="text" name="link_label" placeholder="e.g. XY calibration">
<small>Optional button in the Spools &amp; Stats tab that opens a page in the browser.</small></label>
<label class="t"><span>Extra link: address</span><input type="text" name="link_url" placeholder="e.g. {printer}/nozzle_cam.html">
<small>{printer} = printer address, {spoolman} = Spoolman address. Empty = no button.</small></label>
<label class="t"><span>Profile name</span><input type="text" name="name_format" placeholder="{vendor} {material} {name}">
<small>Placeholders {vendor}, {material}, {name}; " (SM&lt;id&gt;) @&lt;printer&gt; &lt;size&gt; nozzle" is added. The material is left out if the name already contains it.</small></label>
<label class="t"><span>Template prefix</span><input type="text" name="template_prefix" placeholder="SM-Vorlage">
<small>Filament templates in Orca: "&lt;prefix&gt; &lt;Material&gt; ... @&lt;printer&gt; &lt;size&gt; nozzle".</small></label>
<label><input type="checkbox" name="auto_sync"> Update automatically when OrcaSlicer starts</label>
<label><input type="checkbox" name="spoolio_fallback"> Also read Spoolio's fields (flow_ratio, max_volumetric_speed, retraction_length, retraction_speed) as a fallback</label>
<label><input type="checkbox" name="include_all"> Also archived filaments and filaments without a spool</label>
<label>Layout (after restarting OrcaSlicer)<br><select name="layout">
    <option value="two_tabs">Two tabs: Spoolman + Spools &amp; Stats</option>
    <option value="spoolman_tab">Spoolman tab only (buttons: command "Update profiles from Spoolman")</option>
    <option value="profiles_tab">Spools &amp; Stats tab only (Spoolman opens in the browser)</option>
  </select></label>
<button type="button" id="save">Save</button><span id="msg"></span>
<p><small>Changes apply to the next update or refresh; the layout and the Spoolman tab address after restarting OrcaSlicer.</small></p>
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
(and in the Spools &amp; Stats tab).</p>
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
        """Tab "Spools & Stats": profile update and statistics."""

        def get_name(self):
            return "Spools & Stats"

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
                return orca.ExecutionResult.success("Spools & Stats is already open.")
            self.window = orca.host.ui.create_window(
                html=PANEL_HTML,
                title="Spools & Stats",
                width=720,
                height=560,
                on_message=_handle_ui_message,
                on_close=self._on_close,
            )
            _ui_targets["window"] = self.window.post
            return orca.ExecutionResult.success("Spools & Stats opened.")

        def _on_close(self, *args):
            _ui_targets.pop("window", None)
            self.window = None

    @orca.plugin
    class SpoolmanProfilesPlugin(orca.base):
        def register_capabilities(self):
            s = load_settings()
            urls = [u for u in (s["spoolman_url"], s["moonraker_url"]) if u.startswith(("http://", "https://"))]
            try:
                orca.request_permissions(threading=True, network=urls)
            except Exception:  # noqa: BLE001
                log.debug("request_permissions failed", exc_info=True)
            # Registration order is the order in File > Plugins > Config: settings entry first.
            orca.register_capability(SpoolmanProfilesScript)  # always: settings, startup update, window
            layout = load_settings()["layout"]
            if layout in ("two_tabs", "spoolman_tab"):
                orca.register_capability(SpoolmanPage)
            if layout in ("two_tabs", "profiles_tab"):
                orca.register_capability(SpoolmanProfilesPage)
