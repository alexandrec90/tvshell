"""Read and write SlyGuy add-on settings (Pluto TV, Samsung TV Plus, Plex Live share them).

SlyGuy keeps its settings in a SQLite table ``settings(addon_id, key, value)`` with a
JSON value, in the shared module's profile. Settings owned by the shared module apply
to every SlyGuy add-on. **Close Kodi first**: the add-ons keep settings in memory and
write them back.

    python -m tvshell.slyguy set max_bandwidth 0.7     # Mbit/s cap on stream quality
    python -m tvshell.slyguy delete max_height
    python -m tvshell.slyguy list
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

COMMON = "script.module.slyguy"


def default_db() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Kodi/userdata/addon_data" / COMMON / "settings.db"


def set_value(db: Path, key: str, value: object, addon_id: str = COMMON) -> None:
    """Store ``value`` (any JSON-encodable value) under ``key``."""
    with closing(sqlite3.connect(db)) as conn, conn:
        conn.execute(
            "REPLACE INTO settings (addon_id, key, value) VALUES (?, ?, ?)",
            (addon_id, key, json.dumps(value)),
        )


def delete_value(db: Path, key: str, addon_id: str = COMMON) -> bool:
    """Remove ``key``; returns whether it existed."""
    with closing(sqlite3.connect(db)) as conn, conn:
        return (
            conn.execute(
                "DELETE FROM settings WHERE addon_id = ? AND key = ?", (addon_id, key)
            ).rowcount
            > 0
        )


def list_values(db: Path, addon_id: str = COMMON) -> dict[str, object]:
    """Every setting stored for ``addon_id``, decoded."""
    with closing(sqlite3.connect(db)) as conn:
        rows = conn.execute(
            "SELECT key, value FROM settings WHERE addon_id = ? ORDER BY key", (addon_id,)
        )
        return {key: json.loads(value) for key, value in rows}


def parse_value(text: str) -> object:
    """A command-line value as JSON when it is valid JSON (``0.7``, ``true``), else as text."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--db", type=Path, default=None, help="settings.db (default: Kodi's, under %%APPDATA%%)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_set = sub.add_parser("set")
    p_set.add_argument("key")
    p_set.add_argument("value")
    sub.add_parser("delete").add_argument("key")
    sub.add_parser("list")
    args = parser.parse_args(argv)

    db = args.db or default_db()
    if not db.exists():
        print(f"no SlyGuy settings database at {db}", file=sys.stderr)
        return 1
    if args.command == "set":
        set_value(db, args.key, parse_value(args.value))
    elif args.command == "delete" and not delete_value(db, args.key):
        print(f"{args.key} was not set")
    print(json.dumps(list_values(db), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
