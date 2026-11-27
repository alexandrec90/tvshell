"""Home with the game behind a TV app: the game comes to the front (``SteamMenu``).

Steam opens its menu in the most recently started tile that still runs, whatever has focus.
With the game started from its tile and Kodi in front of it, the menu opened in the hidden game
and took every press. The decision runs through ``steam-menu <from tile> <on tv> <top> <game>``.
"""

from __future__ import annotations

import subprocess

import pytest

COVERS = ("SteamMenu.RaiseGame", "SteamMenu.Cli", "mode:steam-menu")

RAISE, LEAVE, USAGE = 1, 0, 254
GAME, KODI = "11", "12"


def steam_menu(exe, *args: str) -> int:
    return subprocess.run([str(exe), "steam-menu", *args], check=False, timeout=30).returncode  # noqa: S603 - our own build


def test_home_with_the_tile_game_behind_kodi_raises_the_game(exe):
    # Regression (2026-11-26 22:33): the menu opened in the game behind Kodi, unseen.
    assert steam_menu(exe, "1", "1", KODI, GAME) == RAISE


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("1", "1", GAME, GAME), "the game is in front already"),
        (("0", "1", KODI, GAME), "a game not started by its tile gets no Steam menu"),
        (("1", "0", KODI, GAME), "the game is on the desk"),
        (("1", "1", KODI, "0"), "no game window"),
    ],
)
def test_home_leaves_the_windows_alone(exe, args, why):
    assert steam_menu(exe, *args) == LEAVE, why


@pytest.mark.parametrize(
    "args", [(), ("1", "1", KODI), ("2", "1", KODI, GAME), ("1", "1", "x", GAME)]
)
def test_steam_menu_usage(exe, args):
    assert steam_menu(exe, *args) == USAGE
