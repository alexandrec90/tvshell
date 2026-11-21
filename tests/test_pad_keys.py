"""Controller buttons as keys for a keyboard-only web app (``PadKeys`` in helper/TvShell.cs).

BackWhenTV's web player reads only the keyboard, and Steam Input's keyboard bindings never
reach its Chrome window, so the helper presses the keys itself. The cases run through the
exe's ``pad-keys`` mode: it prints the logical buttons in a Switch Pro report and the keys
they become (virtual-key codes, a ``+`` joining keys pressed together).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.local

UP, DOWN, LEFT, RIGHT, A, B, MINUS = 1, 2, 4, 8, 16, 32, 64
ENTER, ESC, ARROW_UP, ARROW_DOWN, ARROW_LEFT, ARROW_RIGHT = "0D", "1B", "26", "28", "25", "27"
ALT_F4 = "12+73"


def full(right: int = 0, shared: int = 0, left: int = 0) -> str:
    """A 0x30 full report: timer, battery, the three button bytes, then neutral sticks."""
    return bytes([0x30, 0, 0x90, right, shared, left, 0, 0x08, 0x80, 0, 0x08, 0x80]).hex()


def simple(face: int = 0, shared: int = 0, hat: int = 8) -> str:
    """A 0x3F simple report: face buttons, shared buttons, the D-pad as a hat (8 = centred)."""
    return bytes([0x3F, face, shared, hat]).hex()


def pad_keys(exe: Path, report: str) -> tuple[int, str]:
    run = subprocess.run(  # noqa: S603 - our own build
        [str(exe), "pad-keys", report], check=False, capture_output=True, text=True
    )
    return run.returncode, run.stdout.strip()


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (full(), "0"),
        (full(right=0x08), f"{A} {ENTER}"),
        (full(right=0x04), f"{B} {ESC}"),
        (full(shared=0x01), f"{MINUS} {ALT_F4}"),
        (full(left=0x02), f"{UP} {ARROW_UP}"),
        (full(left=0x01), f"{DOWN} {ARROW_DOWN}"),
        (full(left=0x08), f"{LEFT} {ARROW_LEFT}"),
        (full(left=0x04), f"{RIGHT} {ARROW_RIGHT}"),
        # Home and Capture are Steam's (its menu, a screenshot); X, Y and Plus stay unmapped
        (full(right=0x03, shared=0x32), "0"),
        (simple(), "0"),
        (simple(face=0x02), f"{A} {ENTER}"),
        (simple(face=0x01), f"{B} {ESC}"),
        (simple(shared=0x01), f"{MINUS} {ALT_F4}"),
        (simple(hat=0), f"{UP} {ARROW_UP}"),
        (simple(hat=2), f"{RIGHT} {ARROW_RIGHT}"),
        (simple(hat=4), f"{DOWN} {ARROW_DOWN}"),
        (simple(hat=6), f"{LEFT} {ARROW_LEFT}"),
        # a diagonal is both directions
        (simple(hat=1), f"{UP | RIGHT} {ARROW_UP} {ARROW_RIGHT}"),
        (simple(hat=5), f"{DOWN | LEFT} {ARROW_DOWN} {ARROW_LEFT}"),
        (simple(face=0x0C, shared=0x30), "0"),
    ],
)
def test_pad_keys(exe, report, expected):
    assert pad_keys(exe, report) == (0, expected)


@pytest.mark.parametrize(
    "report",
    [
        "21" + "00" * 11,  # a reply to a subcommand, not input
        "3000",  # a full report cut short
        "3f00",  # a simple report cut short
    ],
)
def test_unknown_report_maps_nothing(exe, report):
    assert pad_keys(exe, report) == (2, "unknown")


def test_pad_keys_without_a_report_is_an_error(exe):
    assert subprocess.run([str(exe), "pad-keys"], check=False).returncode == 254  # noqa: S603 - our own build
