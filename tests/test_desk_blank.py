"""The desk monitor's standby while the TV plays (``DeskBlank`` in helper/TvShell.cs).

Which app holds "display required" cannot be read without admin rights, so a hold is
inferred: the monitor timeout has passed by ``Grace`` (30 s) with no input and Windows still
reports the displays on. Regression: the first version read the system execution state,
which shows only the caller's own requests, so it never saw Kodi's hold and never acted.
The cases run through the exe's ``desk-blank`` mode, whose exit code is the answer.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

LEAVE, OFF, ON = 0, 1, 2
BAD = 254
TIMEOUT = 600  # the power plan's 10-minute monitor timeout
GRACE = 30

pytestmark = pytest.mark.local

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = (
    "DeskBlank.Plan",
    "DeskBlank.IsGame",
    "DeskBlank.IsOn",
    "DeskBlank.Step",
    "DeskBlank.Cli",
    "DeskBlank.Power",
    "DeskBlank.TimeoutSeconds",
    "DeskBlank.IdleSeconds",
    "mode:desk-blank",
)


def run(exe: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - our own build
        [str(exe), "desk-blank", *args], capture_output=True, text=True, check=False, timeout=30
    )


def plan(
    exe: Path,
    idle: float,
    blanked: bool,
    display_on: bool,
    game_on_desk: bool = False,
    timeout: int = TIMEOUT,
) -> int:
    flags = [str(int(b)) for b in (blanked, display_on, game_on_desk)]
    return run(exe, "plan", str(idle), str(timeout), *flags).returncode


@pytest.mark.parametrize(
    ("idle", "blanked", "display_on", "game_on_desk", "expected"),
    [
        # Windows turned the displays off itself: nothing is holding them, nothing to do
        (5000, False, False, False, LEAVE),
        # just past the timeout Windows may still be switching them off: wait out the grace
        (TIMEOUT, False, True, False, LEAVE),
        (TIMEOUT + GRACE - 1, False, True, False, LEAVE),
        # past timeout and grace and still lit: an app holds them (Kodi, a game)
        (TIMEOUT + GRACE, False, True, False, OFF),
        (5000, False, True, False, OFF),
        (5000, False, True, True, LEAVE),  # a game focused on the desk keeps it on
        (30, False, True, False, LEAVE),  # someone is at the desk
        # in standby
        (5000, True, True, False, LEAVE),  # still idle: nothing to send
        (5000, True, False, False, LEAVE),  # Windows cut the signal meanwhile: still nobody here
        (1, True, True, False, ON),  # input at the desk (keyboard, mouse or controller)
        (1, True, False, False, ON),  # input as Windows is bringing the displays back
    ],
)
def test_plan(exe, idle, blanked, display_on, game_on_desk, expected):
    assert plan(exe, idle, blanked, display_on, game_on_desk) == expected


def test_a_never_timeout_never_blanks_and_wakes_a_blanked_desk(exe):
    # The power plan set to never turn the monitor off, or the tray toggle off (which passes 0).
    assert plan(exe, 5000, blanked=False, display_on=True, timeout=0) == LEAVE
    assert plan(exe, 5000, blanked=True, display_on=True, timeout=0) == ON


@pytest.mark.parametrize(
    "args",
    [
        ("plan", "x", "600", "0", "1", "0"),
        ("plan", "5", "600", "0", "2", "0"),
        ("plan", "5", "600", "0", "1"),
        ("display-state", "x"),
        ("nope",),
        (),
    ],
)
def test_bad_arguments(exe, args):
    assert run(exe, *args).returncode == BAD


@pytest.mark.parametrize(("state", "on"), [("0", 0), ("1", 1), ("2", 0)])  # off, on, dimmed
def test_display_state(exe, state, on):
    assert run(exe, "display-state", state).returncode == on


@pytest.mark.parametrize(
    ("path", "name", "configured", "expected"),
    [
        (r"D:\SteamLibrary\steamapps\common\Some Game\somegame.exe", "somegame", "-", 1),
        (r"C:\Program Files (x86)\Steam\STEAMAPPS\common\Other Game\other.exe", "other", "-", 1),
        (r"C:\Games\Somewhere\thegame.exe", "thegame", "thegame", 1),  # game.cfg's process
        (r"C:\Games\Somewhere\thegame.exe", "TheGame", "thegame", 1),  # any case
        (r"C:\Program Files (x86)\Steam\steam.exe", "steam", "-", 0),  # the client is no game
        (r"C:\Program Files\Kodi\kodi.exe", "kodi", "thegame", 0),
        ("-", "explorer", "-", 0),  # a process whose path is unreadable
    ],
)
def test_is_game(exe, path, name, configured, expected):
    assert run(exe, "game", path, name, configured).returncode == expected


# Step against scripted DDC replies: Off sets standby (one reply); On sets on, then reads back
# (two replies: the set, the read). 1 = done / reads on, -1 = refused, 4 = standby.
@pytest.mark.parametrize(
    ("act", "blanked", "replies", "left_blanked"),
    [
        (OFF, 0, ["1"], 1),
        (OFF, 0, ["-1"], 0),  # a monitor without DDC/CI simply stays on
        (ON, 1, ["1", "1"], 0),
        (ON, 1, ["1", "-1"], 1),  # unconfirmed wake: still blanked, retried next tick
        (ON, 1, ["1", "4"], 1),  # reads back as standby: retried
        (LEAVE, 1, ["1"], 1),
        (LEAVE, 0, ["1"], 0),
    ],
)
def test_step(exe, act, blanked, replies, left_blanked):
    assert run(exe, "step", str(act), str(blanked), *replies).returncode == left_blanked


@pytest.mark.parametrize(
    "args",
    [("step", "3", "0", "1"), ("step", "1", "2", "1"), ("step", "1", "0", "x"), ("step", "1", "0")],
)
def test_step_bad_arguments(exe, args):
    assert run(exe, *args).returncode == BAD


def test_read_reports_the_live_inputs(exe):
    # Read-only: queries the desk monitor's power state, the power plan and the idle clock, and
    # sets nothing. The monitor answering DDC/CI depends on the monitor (and on Windows having
    # its signal on), so either outcome passes, but a reply must be a real power mode.
    done = run(exe, "read")
    assert done.returncode in (0, 255), done.stdout + done.stderr
    found = re.fullmatch(r"power=(-?\d+) timeout=(\d+) idle=(\d+)", done.stdout.strip())
    assert found, done.stdout
    assert (done.returncode == 0) == (1 <= int(found.group(1)) <= 5)
    query = subprocess.run(
        ["powercfg", "/q", "SCHEME_CURRENT", "SUB_VIDEO", "VIDEOIDLE"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    ac = re.search(r"Current AC Power Setting Index: 0x([0-9a-fA-F]+)", query.stdout)
    assert ac, query.stdout
    assert int(found.group(2)) == int(ac.group(1), 16)
