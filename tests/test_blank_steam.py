"""Steam's window black over the game: the helper gives the screen back (``BlankSteam``).

Run through the built exe: ``blank-steam <running> <steam in front> <seconds> <rrggbb>...``
exits 1 to focus what is running on the TV, 0 to leave Steam alone.
"""

from __future__ import annotations

import subprocess

import pytest

COVERS = (
    "BlankSteam.Plan",
    "BlankSteam.Dark",
    "BlankSteam.Sample",
    "BlankSteam.Cli",
    "mode:blank-steam",
)

BLACK = ["000000"] * 6
# The Home menu: a near-black blue background with white text somewhere on it.
MENU = ["0e141b", "0e141b", "ffffff", "0e141b", "1a2733", "0e141b"]
RETAKE, LEAVE, USAGE = 1, 0, 254


def blank_steam(exe, *args: str) -> int:
    done = subprocess.run([str(exe), "blank-steam", *args], check=False, timeout=30)  # noqa: S603 - our own build
    return done.returncode


def test_a_black_steam_window_over_the_game_gives_the_screen_back(exe):
    # regression: with a second tile ended, Big Picture sat focused and black over the
    # game, and no controller press could reach the game again
    assert blank_steam(exe, "1", "1", "2", *BLACK) == RETAKE


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("1", "1", "2", *MENU), "Steam's menu is on screen and in use"),
        (("1", "1", "2", "000000", "000000", "000900"), "one lit pixel is not a black screen"),
        (("1", "1", "1.5", *BLACK), "black for a moment between two menu pages"),
        (("0", "1", "60", *BLACK), "nothing is running on the TV to give it back to"),
        (("1", "0", "60", *BLACK), "Steam is not what is in front"),
        (("1", "1", "60"), "the screen could not be read"),
    ],
)
def test_steam_is_left_alone(exe, args, why):
    assert blank_steam(exe, *args) == LEAVE, why


def test_near_black_counts_as_black(exe):
    assert blank_steam(exe, "1", "1", "2.5", "080808", "000000") == RETAKE


def test_sampling_reads_this_pcs_screen(exe):
    # 0 black, 3 something on it, 4 unreadable (a runner with no desktop): never a crash
    assert blank_steam(exe, "sample", "0", "0", "320", "240") in (0, 3, 4)


def test_sampling_an_area_that_cannot_exist_reads_nothing(exe):
    assert blank_steam(exe, "sample", "0", "0", "0", "0") == 4


@pytest.mark.parametrize(
    "args",
    [(), ("1", "1"), ("1", "1", "soon"), ("1", "1", "2", "zz"), ("sample", "0", "0", "w", "9")],
)
def test_a_malformed_call_is_a_usage_error(exe, args):
    assert blank_steam(exe, *args) == USAGE
