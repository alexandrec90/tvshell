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
    "PadFocus.Pick",
    "PadFocus.TargetCli",
    "PadFocus.UsedLast",
    "PadFocus.UsedLastCli",
    "mode:focus-landed",
    "mode:uncovered",
    "mode:pad-focus",
    "mode:pad-target",
    "mode:pad-used-last",
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
def test_a_press_moves_a_settled_focus_to_the_window_on_top(exe, top, focused, expected, why):
    assert run(exe, "uncovered", top, focused, GAME, APP) == expected, why


RAISE_FOCUSED = 2  # Uncovered.Act


@pytest.mark.parametrize(
    ("top", "focused", "expected", "why"),
    [
        # Regression: Steam's overlay switching from the game to Kodi focused Kodi a moment
        # before Kodi rose over the game, and the press that picked it handed focus back to the
        # game. The overlay froze over the game and no input reached anything.
        (GAME, APP, RAISE_FOCUSED, "Steam's switch to the app is still in flight"),
        (APP, GAME, RAISE_FOCUSED, "the game was just chosen over the app"),
        (GAME, GAME, NO, "the focused window is the one showing"),
        (STEAM, APP, NO, "Steam's menu over the app"),
        (GAME, STEAM, NO, "never away from Steam's window"),
        ("0", APP, NO, "nothing is showing"),
    ],
)
def test_a_press_keeps_a_focus_that_just_moved(exe, top, focused, expected, why):
    assert run(exe, "uncovered", top, focused, GAME, APP, "1") == expected, why


def test_a_focus_that_has_settled_behind_moves_to_the_window_on_top(exe):
    assert run(exe, "uncovered", GAME, APP, GAME, APP, "0") == YES


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("1", "2", "3"),
        ("1", "2", "3", "x"),
        ("1", "2", "3", "4", "2"),
        ("1", "2", "3", "4", "0", "0"),
    ],
)
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
def test_a_press_takes_focus_only_from_the_desk(
    exe, has_foreground, steam, on_tv, on_desk, expected, why
):
    assert run(exe, "pad-focus", has_foreground, steam, on_tv, on_desk) == expected, why


@pytest.mark.parametrize(
    "args", [(), ("1", "0", "0"), ("1", "0", "0", "2"), ("1", "0", "0", "1", "1")]
)
def test_pad_focus_usage(exe, args):
    assert run(exe, "pad-focus", *args) == USAGE


NO_TARGET, TO_GAME, TO_APP, TO_BIG_PICTURE = 0, 1, 2, 3  # PadFocus.Target


@pytest.mark.parametrize(
    ("game", "game_on_tv", "app", "app_used_last", "launcher_now", "expected", "why"),
    [
        # Regression: watching Kodi with the game on the TV behind it, focus wandered to the
        # desk (the game's store launcher, the taskbar) and the next press handed it to the game,
        # which covered Kodi; its covered-video guard then stopped the channel.
        ("1", "1", "1", "1", "0", TO_APP, "back to the TV app that was being used"),
        ("1", "1", "1", "0", "0", TO_GAME, "back to the game that was being played"),
        ("1", "1", "0", "1", "0", TO_GAME, "the TV app used last has closed"),
        ("0", "0", "1", "0", "0", TO_APP, "only a TV app"),
        ("1", "0", "1", "0", "0", TO_APP, "the game is on the desk"),
        ("1", "0", "0", "0", "0", NO_TARGET, "only the game, on the desk: focus stays"),
        ("0", "0", "0", "1", "0", TO_BIG_PICTURE, "nothing running"),
        # Regression (marks 20261128-000952 and -001532): picking the game in Steam's menu
        # over Kodi, Steam focused the store launcher's window on the desk; the press went to
        # Kodi, and the game's menu opened unseen behind it, taking every press.
        ("1", "1", "1", "1", "1", TO_GAME, "Steam switching to the game from its menu"),
        (
            "1",
            "0",
            "1",
            "1",
            "1",
            TO_APP,
            "the game is on the desk: Steam's switch is not the TV's",
        ),
        ("0", "0", "1", "1", "1", TO_APP, "the store launcher with no game running"),
    ],
)
def test_a_press_from_the_desk_returns_to_what_was_used_on_the_tv(
    exe, game, game_on_tv, app, app_used_last, launcher_now, expected, why
):
    args = (game, game_on_tv, app, app_used_last, launcher_now)
    assert run(exe, "pad-target", *args) == expected, why


@pytest.mark.parametrize(
    "args", [(), ("1", "1", "1"), ("1", "1", "1", "1"), ("1", "1", "1", "1", "x")]
)
def test_pad_target_usage(exe, args):
    assert run(exe, "pad-target", *args) == USAGE


@pytest.mark.parametrize(
    ("is_game", "is_app", "before", "expected", "why"),
    [
        ("1", "0", "1", NO, "the game took focus"),
        ("0", "1", "0", YES, "a TV app took focus"),
        ("0", "0", "1", YES, "a desk window (the store launcher, the taskbar) changes nothing"),
        ("0", "0", "0", NO, "nor here"),
    ],
)
def test_which_was_used_last(exe, is_game, is_app, before, expected, why):
    assert run(exe, "pad-used-last", is_game, is_app, before) == expected, why


@pytest.mark.parametrize("args", [(), ("1", "0"), ("1", "0", "2")])
def test_pad_used_last_usage(exe, args):
    assert run(exe, "pad-used-last", *args) == USAGE
