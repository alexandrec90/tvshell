"""Add or update the TV tiles in Steam's non-Steam shortcuts (``shortcuts.vdf``).

Steam keeps non-Steam shortcuts in a binary KeyValues file. A tile that already exists
keeps its ``appid`` -- Steam keys the tile's controller layout and artwork on it -- and
every other entry is written back unchanged. Steam must be closed while the file is
written, or it overwrites the change on exit.

    python -m tvshell.steam_shortcuts --vdf "C:/Program Files (x86)/Steam/userdata/<id>/config/shortcuts.vdf"
"""

from __future__ import annotations

import argparse
import shutil
import struct
import sys
import zlib
from pathlib import Path

from tvshell.tiles import Tile, tiles

MAP, STRING, INT32, END = 0, 1, 2, 8


def parse(data: bytes, i: int = 0) -> tuple[dict, int]:
    """Parse one binary KeyValues map starting at ``i``; return it and the index after it."""
    out: dict = {}
    while True:
        kind = data[i]
        i += 1
        if kind == END:
            return out, i
        j = data.index(0, i)
        key = data[i:j].decode("utf-8")
        i = j + 1
        if kind == MAP:
            out[key], i = parse(data, i)
        elif kind == STRING:
            j = data.index(0, i)
            out[key] = data[i:j].decode("utf-8")
            i = j + 1
        elif kind == INT32:
            out[key] = struct.unpack("<I", data[i : i + 4])[0]
            i += 4
        else:
            raise ValueError(f"unsupported KeyValues type {kind} at byte {i - 1}")


def dump(values: dict) -> bytes:
    """Serialise a map produced by :func:`parse` back to binary KeyValues."""
    out = b""
    for key, value in values.items():
        k = key.encode("utf-8") + b"\0"
        if isinstance(value, dict):
            out += bytes([MAP]) + k + dump(value)
        elif isinstance(value, str):
            out += bytes([STRING]) + k + value.encode("utf-8") + b"\0"
        elif isinstance(value, int):
            out += bytes([INT32]) + k + struct.pack("<I", value)
        else:
            raise TypeError(f"cannot store {type(value).__name__} under {key!r}")
    return out + bytes([END])


def shortcut_appid(exe: str, name: str) -> int:
    """The appid Steam derives for a new non-Steam shortcut."""
    return (zlib.crc32((exe + name).encode("utf-8")) & 0xFFFFFFFF) | 0x80000000


def game_id(appid: int) -> int:
    """The 64-bit id ``steam://rungameid/<id>`` takes for a non-Steam shortcut."""
    return (appid << 32) | 0x02000000


def new_entry(tile: Tile) -> dict:
    """A shortcut entry with Steam's default fields for ``tile``."""
    return {
        "appid": shortcut_appid(tile.exe, tile.name),
        "AppName": tile.name,
        "Exe": tile.exe,
        "StartDir": tile.start_dir,
        "icon": tile.icon,
        "ShortcutPath": "",
        "LaunchOptions": tile.options,
        "IsHidden": 0,
        "AllowDesktopConfig": 1,
        "AllowOverlay": 1,
        "OpenVR": 0,
        "Devkit": 0,
        "DevkitGameID": "",
        "DevkitOverrideAppID": 0,
        "LastPlayTime": 0,
        "FlatpakAppID": "",
        "tags": {},
    }


def upsert(shortcuts: dict, tile: Tile) -> tuple[dict, bool]:
    """Add ``tile`` to ``shortcuts`` or update the entry of the same name in place.

    Returns the entry and whether it was added. An existing entry keeps its appid.
    """
    for entry in shortcuts.values():
        if entry.get("AppName") == tile.name:
            entry.update(
                Exe=tile.exe, StartDir=tile.start_dir, icon=tile.icon, LaunchOptions=tile.options
            )
            return entry, False
    entry = new_entry(tile)
    shortcuts[str(len(shortcuts))] = entry
    return entry, True


def apply(data: bytes, wanted: list[Tile]) -> tuple[bytes, list[str]]:
    """Return ``data`` with every tile in ``wanted`` present, plus one report line per tile."""
    top, end = parse(data)
    shortcuts = top.setdefault("shortcuts", {})
    report = []
    for tile in wanted:
        entry, added = upsert(shortcuts, tile)
        verb = "added" if added else "updated"
        report.append(
            f"{verb} {tile.name}: appid {entry['appid']} gameid {game_id(entry['appid'])}"
        )
    return dump(top) + data[end:], report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--vdf", type=Path, required=True, help="Steam's userdata/<id>/config/shortcuts.vdf"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print what would change, write nothing"
    )
    args = parser.parse_args(argv)

    data = args.vdf.read_bytes() if args.vdf.exists() else dump({"shortcuts": {}})
    new, report = apply(data, tiles())
    print("\n".join(report))
    if args.dry_run:
        return 0
    if args.vdf.exists():
        shutil.copy(args.vdf, args.vdf.with_name(args.vdf.name + ".bak-tvshell"))
    args.vdf.write_bytes(new)
    return 0


if __name__ == "__main__":
    sys.exit(main())
