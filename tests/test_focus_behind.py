"""Focus that lands late, focus on a window hidden behind another of ours, and what a
controller press does to focus.

``N.Landed`` runs through the exe's ``focus-landed`` mode against a scripted foreground,
``Uncovered.Plan`` through ``uncovered`` and ``PadFocus.Plan`` through ``pad-focus``.
Window handles are plain numbers; 0 is no window.
"""

from __future__ import annotations

import subprocess

import pytest

COVERS = (
    "N.Landed",
    "N.LandedCli",
    "Uncovered.Plan",
    "Uncovered.Cli",
    "PadFocus.Plan",
    "PadFocus.Cli",
    "mode:focus-landed",
    "mode:uncovered",
    "mode:pad-focus",
)

YES, NO, USAGE = 1, 0, 254
TARGET, OTHER = "5", "7"
GAME, APP, STEAM = "11", "12", "13"


def run(exe, *args: str) -> int:
    return subprocess.run([str(exe), *args], check=False, timeout=30).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("readings", "expected"),
    [
        ((TARGET,), YES),
        (("0", "0", "0", "0", TARGET), YES),  # regression: mid-switch is not a refusal
        ((OTHER, TARGET), YES),  # the old window lingers for a moment
        ((OTHER,), NO),
        (("0", "0", "0", OTHER, TARGET), NO),  # settled elsewhere
        (("0",), NO),
        (("0",) * 15 + (TARGET,), YES),  # the last reading, once the wait is over
    ],
)
def test_focus_landed(exe, readings, expected):
    assert run(exe, "focus-landed", TARGET, *readings) == expected


@pytest.mark.parametrize("args", [(), ("5",), ("5", "x")])
def test_focus_landed_usage(exe, args):
    assert run(exe, "focus-landed", *args) == USAGE


@pytest.mark.parametrize(
    ("top", "focused", "expected", "why"),
    [
        (GAME, APP, YES, "the app has focus behind the game"),
        (APP, GAME, YES, "the game has focus behind the app"),
        (GAME, GAME, NO, "the focused window is the one showing"),
        ("0", APP, NO, "nothing is showing"),
        (STEAM, GAME, NO, "Steam's menu over the game"),
        (GAME, STEAM, NO, "never away from Steam's window"),
        (OTHER, APP, NO, "not one of ours on top"),
    ],
)
def test_a_press_moves_focus_to_the_window_on_top(exe, top, focused, expected, why):
    assert run(exe, "uncovered", top, focused, GAME, APP) == expected, why


@pytest.mark.parametrize("args", [(), ("1", "2", "3"), ("1", "2", "3", "x")])
def test_uncovered_usage(exe, args):
    assert run(exe, "uncovered", *args) == USAGE


def test_no_foreground_window_and_no_game_is_not_a_covered_game(exe):
    # With no game its handle is 0, and so is "no foreground window".
    assert run(exe, "uncovered", "0", "0", "0", APP) == NO


LEAVE, UNCOVER, TAKE_TO_TV = 0, 1, 2  # PadFocus.Act


@pytest.mark.parametrize(
    ("has_foreground", "steam", "on_tv", "on_desk", "expected", "why"),
    [
        # Regression: Steam's overlay switching from the game to Live TV leaves no window focused
        # for a moment, and no window reads as on the desk (its empty rectangle sits at 0,0).
        # Taking focus for the game then hid Steam's menu, which kept the controller, behind it.
        ("0", "0", "0", "1", LEAVE, "mid-switch, though its rectangle is on the desk"),
        ("0", "0", "0", "0", LEAVE, "mid-switch"),
        ("1", "0", "0", "1", TAKE_TO_TV, "a desk window has focus"),
        ("1", "0", "1", "0", UNCOVER, "something on the TV has focus"),
        ("1", "1", "1", "0", LEAVE, "Steam's menu on the TV is in use"),
        ("1", "1", "0", "0", LEAVE, "Steam's window off both displays"),
        ("1", "1", "0", "1", TAKE_TO_TV, "the ordinary Steam client on the desk"),
        ("1", "0", "0", "0", LEAVE, "a window off both displays is not the desk's to give"),
    ],
)
def test_a_press_takes_focus_only_from_the_desk(exe, has_foreground, steam, on_tv, on_desk, expected, why):
    assert run(exe, "pad-focus", has_foreground, steam, on_tv, on_desk) == expected, why


@pytest.mark.parametrize("args", [(), ("1", "0", "0"), ("1", "0", "0", "2"), ("1", "0", "0", "1", "1")])
def test_pad_focus_usage(exe, args):
    assert run(exe, "pad-focus", *args) == USAGE
