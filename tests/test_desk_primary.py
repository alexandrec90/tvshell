"""The desk display is the primary one (``DeskPrimary.Plan`` in helper/TvShell.cs).

The plasma reports its own identity when it is already on at boot, and the adapter's when
it is not. Windows keeps a separate layout for each set of monitors, and the one it kept for
the plasma's own identity had the TV as the primary display: taskbar, sign-in and every new
window on a screen that cannot be seen from the desk. The helper found the TV primary and,
by design, did nothing.

It now makes the other display primary, and only when that is not a guess: tv.cfg names the
TV, exactly two displays are connected, exactly one of them is the TV, and the TV is the
primary one. The cases run through the exe's ``plan-primary`` mode, whose exit code is the
index of the display to make primary (255 = leave the displays alone).

The last test is the same question asked of this PC's real displays, through the installed
helper's read-only ``desk-primary check``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tvshell.tiles import INSTALL_DIR

TV = r"\\?\DISPLAY#TVX1234#5&1&0&UID4354#{e6f07b5f}"
# The same set on another boot: the adapter answers for the TV with its own identity.
TV_ADAPTER = r"\\?\DISPLAY#ADP0001#5&1&0&UID4354#{e6f07b5f}"
DESK = r"\\?\DISPLAY#DSK0001#5&1&0&UID4359#{e6f07b5f}"
OTHER = r"\\?\DISPLAY#OTH9999#5&1&0&UID4360#{e6f07b5f}"
BOTH = "ADP0001,TVX1234"
LEAVE = 255
DESK_IS_PRIMARY, TV_IS_PRIMARY = 0, 3

pytestmark = pytest.mark.local

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
# The last four are reached by the read-only check of this PC's real displays.
COVERS = (
    "DeskPrimary.Plan",
    "DeskPrimary.PlanCli",
    "mode:plan-primary",
    "Program.Main",
    "DeskPrimary.Cli",
    "DeskPrimary.PlanNow",
    "TvDisplay.List",
    "mode:desk-primary",
)


def plan(exe: Path, wanted: str, *displays: str) -> int:
    return subprocess.run([str(exe), "plan-primary", wanted, *displays], check=False).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("wanted", "displays", "expected"),
    [
        # regression: the boot where the TV came up primary, listed first, under its own identity
        (BOTH, [TV + "*", DESK], 1),
        (BOTH, [TV_ADAPTER + "*", DESK], 1),
        (BOTH, [DESK, TV + "*"], 0),
        ("tvx1234", [TV + "*", DESK], 1),  # case-insensitive, like the TV's own match
        # the desk is already primary: nothing to do
        (BOTH, [DESK + "*", TV], LEAVE),
        (BOTH, [TV, DESK + "*"], LEAVE),
        # no tv.cfg: which display is the TV would be a guess, so the displays are left alone
        ("-", [TV + "*", DESK], LEAVE),
        # anything but exactly two displays: which one is the desk would be a guess
        (BOTH, [TV + "*", DESK, OTHER], LEAVE),
        (BOTH, [TV + "*"], LEAVE),
        # the primary display is not the TV, and the TV is absent
        (BOTH, [OTHER + "*", DESK], LEAVE),
        # both displays are named as the TV: no desk to choose
        (BOTH, [TV + "*", TV_ADAPTER], LEAVE),
        # a code must match a whole segment, not a substring
        ("TVX12", [TV + "*", DESK], LEAVE),
        # no display reports itself primary (displays still settling)
        (BOTH, [TV, DESK], LEAVE),
    ],
)
def test_plan_primary(exe, wanted, displays, expected):
    assert plan(exe, wanted, *displays) == expected


def test_plan_primary_without_arguments_is_an_error(exe):
    assert subprocess.run([str(exe), "plan-primary"], check=False).returncode == 254  # noqa: S603 - our own build


def test_an_unknown_mode_starts_nothing(exe):
    # An unknown mode used to fall through and start a second tray helper, so a test for a
    # mode that did not exist yet would have left one running from a temporary directory.
    done = subprocess.run([str(exe), "no-such-mode"], check=False, timeout=30)  # noqa: S603 - our own build
    assert done.returncode == 253


def test_the_tv_is_not_this_pcs_primary_display():
    """This PC's real displays, read (never changed) by the installed helper."""
    installed = INSTALL_DIR / "TvShell.exe"
    if not installed.exists():
        pytest.fail(f"no installed helper at {installed}: run python -m tvshell.deploy")
    done = subprocess.run(  # noqa: S603 - the installed helper
        [str(installed), "desk-primary", "check"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode != TV_IS_PRIMARY, f"the TV is the primary display: {done.stdout}"
    assert done.returncode == DESK_IS_PRIMARY, (
        f"the installed helper answered {done.returncode}: {done.stdout}"
    )
