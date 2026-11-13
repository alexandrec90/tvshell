"""The Big Picture tiles this project installs, and where the helper lives.

The helper (``TvShell.exe``) is installed outside the repository, in ``INSTALL_DIR``:
Windows startup, the Steam tiles and the desktop shortcut point there, so a checkout
can move or be deleted without breaking the TV.

An optional game tile comes from ``game.cfg`` in the install directory -- the same
key=value file the helper reads -- so which game it is never lives in this repository.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

INSTALL_DIR = Path.home() / "TvShell"
GAME_CFG = "game.cfg"
KODI_EXE = Path("C:/Program Files/Kodi/kodi.exe")
CHROME_EXE = Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe")

# (tile name, icon, TvShell.exe arguments)
TILE_SPECS: tuple[tuple[str, Path, str], ...] = (
    ("Live TV", KODI_EXE, "kodi tv"),
    ("Radio", KODI_EXE, "kodi radio"),
    ("CBC Radio", KODI_EXE, "kodi cbcradio"),
    ("CBC Gem", KODI_EXE, "kodi gem"),
    ("YouTube", CHROME_EXE, "youtube"),
)


@dataclass(frozen=True)
class Tile:
    """One Big Picture tile: what Steam shows and what it launches."""

    name: str
    exe: str
    start_dir: str
    icon: str
    options: str


def windows_path(path: Path) -> str:
    """``path`` with Windows separators, as Steam stores it."""
    return str(path).replace("/", "\\")


def parse_cfg(text: str) -> dict[str, str]:
    """``key=value`` lines as the helper reads them: keys lower-cased, ``#`` lines and blanks skipped."""
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        key, sep, value = line.partition("=")
        if not line or line.startswith("#") or not sep or not key.strip():
            continue
        values[key.strip().lower()] = value.strip()
    return values


def game_spec(install_dir: Path) -> tuple[str, Path, str] | None:
    """The game tile's (name, icon, arguments), or None without a ``game.cfg`` naming the game."""
    cfg_path = install_dir / GAME_CFG
    if not cfg_path.exists():
        return None
    cfg = parse_cfg(cfg_path.read_text(encoding="utf-8"))
    if not cfg.get("name") or not cfg.get("process"):
        return None
    return cfg["name"], Path(cfg.get("icon") or install_dir / "TvShell.exe"), "launch"


def tiles(install_dir: Path = INSTALL_DIR) -> list[Tile]:
    """Every tile, launching the helper installed in ``install_dir``."""
    exe = '"' + windows_path(install_dir / "TvShell.exe") + '"'
    start_dir = '"' + windows_path(install_dir) + '\\"'
    game = game_spec(install_dir)
    specs = ((game,) if game else ()) + TILE_SPECS
    return [
        Tile(name, exe, start_dir, windows_path(icon), options) for name, icon, options in specs
    ]
