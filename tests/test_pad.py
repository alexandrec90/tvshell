"""Which controller reports are a fresh press (``Pad.Pressed`` in helper/TvShell.cs).

A press focuses the app on the TV. Steam switches the Switch Pro controller between full
reports (0x30) and simple ones (0x3F) as focus moves, and the two lay the buttons out
differently, so comparing across them read every mode switch as a press: focus went to the
desk, Steam switched modes, the helper took focus back to the TV, and the desk keyboard typed
there. The cases run through the exe's ``pad-press`` mode, whose exit code is the answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

PRESS, NONE, BAD = 1, 0, 254
CENTRED = "000880" * 2  # both 12-bit sticks at 2048

pytestmark = pytest.mark.ci_e2e

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = ("Pad.Buttons", "Pad.Pressed", "Pad.Hex", "Pad.Cli", "mode:pad-press")


def full(right: int = 0, shared: int = 0, left: int = 0, sticks: str = CENTRED) -> str:
    """A 0x30 report: id, timer, battery, three button bytes, two sticks."""
    return f"30{0x5A:02X}80{right:02X}{shared:02X}{left:02X}{sticks}"


def simple(b1: int = 0, b2: int = 0, hat: int = 8) -> str:
    """A 0x3F report: id, two button bytes, the d-pad direction, then sticks."""
    return f"3F{b1:02X}{b2:02X}{hat:02X}" + "00" * 8


def pressed(exe: Path, *args: str) -> int:
    return subprocess.run([str(exe), "pad-press", *args], check=False).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("prev", "cur", "expected"),
    [
        # regression: Steam switching report mode is not a press, either way round
        (full(), simple(), NONE),
        (simple(), full(), NONE),
        (full(), simple(b1=0x02), NONE),  # a new baseline, even if a button is down in it
        # a button going down within one mode is a press; held or released is not
        (full(), full(right=0x08), PRESS),  # A
        (full(left=0x01), full(left=0x03), PRESS),  # a second d-pad direction
        (full(right=0x08), full(right=0x08), NONE),
        (full(right=0x08), full(), NONE),
        (simple(), simple(b1=0x01), PRESS),
        (simple(b1=0x01), simple(), NONE),
        # the simple d-pad is a direction, 8 when centred
        (simple(), simple(hat=0), PRESS),  # up: no new bit over 8, still a press
        (simple(hat=0), simple(hat=2), PRESS),
        (simple(hat=0), simple(), NONE),
        # Home (opens Steam's menu) and Capture never count
        (full(), full(shared=0x10), NONE),
        (full(), full(shared=0x20), NONE),
        (simple(), simple(b2=0x10), NONE),
        (full(), full(shared=0x01), PRESS),  # minus, beside them
        # a stick pushed far counts, drift does not
        (full(), full(sticks="FF0F80" + "000880"), PRESS),  # left stick hard right
        ("-", full(sticks="FF0F80" + "000880"), PRESS),  # a push needs no earlier report
        (full(), full(sticks="400880" + "000880"), NONE),  # 0x840: drift
        # nothing to compare with yet, and reports without buttons
        ("-", full(right=0x08), NONE),
        (full(), "21" + "00" * 11, NONE),
    ],
)
def test_pad_press(exe, prev, cur, expected):
    assert pressed(exe, prev, cur) == expected


@pytest.mark.parametrize(
    "args", [[], ["-"], ["-", ""], ["-", "3"], ["zz", full()], ["-", "30", "x"]]
)
def test_pad_press_bad_arguments_are_an_error(exe, args):
    assert pressed(exe, *args) == BAD
