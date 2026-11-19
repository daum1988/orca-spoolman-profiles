#!/usr/bin/env python3
"""Build the Orca Cloud release files of the Spoolman Profiles plugin.

The plugin is one pure-Python file. Orca Cloud tells platform builds apart by the
file name suffix, so the same file is written once per target into dist/.

    python build.py                          # check version, build dist/
    python build.py --tag v1.7.0
    python build.py --changelog-out notes.md

--tag fails unless the tag matches the plugin version; --changelog-out writes the
CHANGELOG.md section of that version (the text Orca shows as the changelog).

Releasing: bump "# version = ..." and PLUGIN_VERSION in spoolman_profiles.py, add a
CHANGELOG.md section, commit, then publish a GitHub release with the tag
v<version>. .github/workflows/publish-orcacloud.yml uploads it to Orca Cloud.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN = HERE / "spoolman_profiles.py"
CHANGELOG = HERE / "CHANGELOG.md"
DIST = HERE / "dist"
TAG_PREFIX = "v"
TARGETS = ("win_x86_64", "win_arm64", "linux_x86_64", "linux_arm64", "macosx_arm64", "macosx_x86_64")


def plugin_version() -> str:
    text = PLUGIN.read_text(encoding="utf-8")
    header = re.findall(r'^# version = "([^"]+)"$', text, re.M)
    const = re.findall(r'^PLUGIN_VERSION = "([^"]+)"$', text, re.M)
    if len(header) != 1 or len(const) != 1:
        sys.exit("error: expected one '# version = ...' header line and one PLUGIN_VERSION")
    if header[0] != const[0]:
        sys.exit(f"error: header version {header[0]} != PLUGIN_VERSION {const[0]}")
    return header[0]


def changelog_section(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    if not m or not m.group(1).strip():
        sys.exit(f"error: CHANGELOG.md has no section for {version}")
    return m.group(1).strip() + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tag", help="release tag, must be spoolman-profiles-v<version>")
    ap.add_argument("--changelog-out", help="write this version's changelog section to the file")
    args = ap.parse_args()

    version = plugin_version()
    if args.tag is not None and args.tag != TAG_PREFIX + version:
        sys.exit(f"error: tag {args.tag!r} does not match plugin version {version} "
                 f"(expected {TAG_PREFIX}{version})")
    notes = changelog_section(version)
    if args.changelog_out:
        Path(args.changelog_out).write_text(notes, encoding="utf-8")

    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir()
    for target in TARGETS:
        out = DIST / f"spoolman_profiles_{target}.py"
        shutil.copyfile(PLUGIN, out)
        print(f"built {out.relative_to(HERE)}")
    print(f"version {version}")


if __name__ == "__main__":
    main()
