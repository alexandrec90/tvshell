"""Which display the helper treats as the TV (``TvDisplay.Pick`` in helper/TvShell.cs).

The helper used to take "the first non-primary display". After a boot where the graphics
driver came up with a different layout, that put Big Picture on the wrong screen. Pick
now names the TV by its monitor identity (tv.cfg), and with no match it answers "no TV"
so nothing gets moved. The cases run through the exe's ``pick-tv`` mode, whose exit code
is Pick's index (255 = no TV).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tvshell import deploy

TV = r"\\?\DISPLAY#TVX1234#5&1&0&UID4354#{e6f07b5f}"
DESK = r"\\?\DISPLAY#DSK0001#5&1&0&UID4359#{e6f07b5f}"
OTHER = r"\\?\DISPLAY#OTH9999#5&1&0&UID4360#{e6f07b5f}"
NONE = 255

pytestmark = pytest.mark.local


@pytest.fixture(scope="module")
def exe(tmp_path_factory) -> Path:
    if deploy.find_csc() is None:
        pytest.fail("the .NET Framework C# compiler is not on this PC")
    out = tmp_path_factory.mktemp("tvshell")
    (out / "logs").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deploy, "REPO", out)  # the build log goes under REPO/logs
        deploy.build(out / "TvShell.exe")
    return out / "TvShell.exe"


def pick(exe: Path, wanted: str, *displays: str) -> int:
    return subprocess.run([str(exe), "pick-tv", wanted, *displays], check=False).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("wanted", "displays", "expected"),
    [
        # configured: found by identity, wherever it sits in the list
        ("TVX1234", [DESK + "*", TV], 1),
        ("TVX1234", [TV, DESK + "*"], 0),
        ("tvx1234", [DESK + "*", TV], 1),  # case-insensitive
        ("NOPE,TVX1234", [DESK + "*", OTHER, TV], 2),  # any of several codes
        # regression: an extra display listed first used to become "the TV"
        ("TVX1234", [DESK + "*", OTHER, TV], 2),
        # configured TV absent -> no TV, even though a non-primary display exists
        ("TVX1234", [DESK + "*", OTHER], NONE),
        ("TVX1234", [DESK + "*"], NONE),
        # the TV made primary is never treated as the TV
        ("TVX1234", [TV + "*", DESK], NONE),
        # a code must match a whole segment, not a substring
        ("TVX12", [DESK + "*", TV], NONE),
        # unconfigured: only an unambiguous two-display layout has a TV
        ("-", [DESK + "*", TV], 1),
        ("-", [TV, DESK + "*"], 0),
        ("-", [DESK + "*", OTHER, TV], NONE),
        ("-", [DESK + "*"], NONE),
        ("-", [DESK, TV], NONE),  # no primary reported
    ],
)
def test_pick_tv(exe, wanted, displays, expected):
    assert pick(exe, wanted, *displays) == expected


def test_pick_tv_without_arguments_is_an_error(exe):
    assert subprocess.run([str(exe), "pick-tv"], check=False).returncode == 254  # noqa: S603 - our own build
