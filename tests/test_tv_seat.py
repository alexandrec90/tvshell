"""Which windows may stay on the TV (``TvSeat`` in helper/TvShell.cs).

After the displays came back in a different layout, Windows left the person's own Chrome
maximized on the TV; Chrome saved that and reopened there every time, out of reach of the
mouse (confined to the desk). The helper now sends an ordinary desk window found on the TV
back to the desk. The YouTube tile is Chrome too, so it is told apart by its profile on the
command line, never by a PID file alone: a killed launcher leaves its PID behind, and Windows
may hand that PID to the person's own Chrome. The cases run through the exe's ``tv-seat``
mode, whose exit code is the answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tvshell import deploy

PROFILE = r"C:\Users\someone\TvShell\yt-profile"
STAYS, GOES = 1, 0
BAD = 254

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


def seat(exe: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(exe), "tv-seat", *args], check=False, capture_output=True, text=True)  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("process", "youtube", "game", "launcher", "expected"),
    [
        # what the TV runs stays there
        ("kodi", "0", "-", "-", STAYS),
        ("Kodi", "0", "-", "-", STAYS),  # case-insensitive
        ("steamwebhelper", "0", "-", "-", STAYS),  # Big Picture
        ("steam", "0", "-", "-", STAYS),
        ("gameoverlayui64", "0", "-", "-", STAYS),  # Steam's in-game menu
        ("chrome", "1", "-", "-", STAYS),  # the YouTube browser
        ("MyGame", "0", "MyGame", "Store", STAYS),
        ("store", "0", "MyGame", "Store", STAYS),  # the game's store launcher
        # regression: the person's own Chrome reopened on the TV and stayed there
        ("chrome", "0", "-", "-", GOES),
        ("notepad", "0", "-", "-", GOES),
        ("explorer", "0", "-", "-", GOES),
        # no game configured: nothing is let through for it, not even an empty name
        ("MyGame", "0", "-", "-", GOES),
        ("", "0", "-", "-", GOES),
        # a name merely containing a TV process is not one
        ("kodihelper", "0", "-", "-", GOES),
    ],
)
def test_belongs_on_tv(exe, process, youtube, game, launcher, expected):
    assert seat(exe, "belongs", process, youtube, game, launcher).returncode == expected


@pytest.mark.parametrize(
    ("cmd", "expected"),
    [
        (rf'"chrome.exe" --user-data-dir="{PROFILE}" --kiosk https://www.youtube.com/tv', 1),
        (rf'"chrome.exe" --user-data-dir="{PROFILE.upper()}" --kiosk', 1),  # case-insensitive path
        # regression: a reused PID pointing at the person's own Chrome
        ('"chrome.exe" --source-shortcut-location=taskbar', 0),
        # a renderer or GPU process of the YouTube browser owns no top-level window
        (rf'"chrome.exe" --type=renderer --user-data-dir="{PROFILE}"', 0),
        ("", 0),
    ],
)
def test_is_youtube_browser(exe, cmd, expected):
    assert seat(exe, "youtube", cmd, PROFILE).returncode == expected


def test_is_youtube_browser_needs_a_profile(exe):
    assert seat(exe, "youtube", rf'"chrome.exe" --user-data-dir="{PROFILE}"', "").returncode == 0


TV = "1920,0,1280,768"
DESK = "0,0,1920,1160"  # a working area: the taskbar is not part of it


@pytest.mark.parametrize(
    ("win", "tv", "desk", "expected"),
    [
        # the same offset from the corner, on the desk
        ("2020,50,800,600", TV, DESK, "100,50,800,600"),
        # regression: Chrome maximized on the TV (a maximized frame overhangs by 8 px)
        ("1912,-8,1296,784", TV, DESK, "0,0,1296,784"),
        # larger than the desk: shrunk to fit
        ("1920,0,2500,1300", TV, DESK, "0,0,1920,1160"),
        # near the far edge: pulled in so it is wholly on the desk
        ("3100,700,800,600", TV, DESK, "1120,560,800,600"),
        # a desk that does not start at the origin
        ("2020,50,800,600", TV, "-1920,0,1920,1080", "-1820,50,800,600"),
    ],
)
def test_desk_rect(exe, win, tv, desk, expected):
    r = seat(exe, "desk-rect", win, tv, desk)
    assert (r.returncode, r.stdout.strip()) == (0, expected)


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["belongs", "kodi", "2", "-", "-"],
        ["belongs", "kodi", "0", "-"],
        ["youtube", "cmd"],
        ["desk-rect", "1,2,3", TV, DESK],
        ["desk-rect", "a,b,c,d", TV, DESK],
        ["nope"],
    ],
)
def test_bad_arguments(exe, args):
    assert seat(exe, *args).returncode == BAD
