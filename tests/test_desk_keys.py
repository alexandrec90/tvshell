"""Which keys send focus back to the desk display (``DeskKeys.Classify`` in helper/TvShell.cs).

The TV and the desk are separate seats: the controller drives the TV, the keyboard the desk.
A physical key pressed while the TV has focus returns focus to the desk and is held back so
it never types into Kodi or YouTube. Keys Steam Input injects for the controller must never
count, or using the controller on the TV would bounce focus to the desk; volume and media
keys act system-wide and must keep working; modifiers pass so the shortcut they start
survives. The cases run through the exe's ``desk-key`` mode, whose exit code is the answer.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tvshell import deploy

IGNORE, PASS, HOLD = 0, 1, 2
INJECTED, LOWER_IL_INJECTED, EXTENDED = 0x10, 0x02, 0x01

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


def classify(exe: Path, *args: str) -> int:
    return subprocess.run([str(exe), "desk-key", *args], check=False).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("vk", "flags", "expected"),
    [
        # typing keys from the real keyboard are held back
        (0x41, 0, HOLD),  # A
        (0x20, 0, HOLD),  # space: would pause Kodi
        (0x0D, 0, HOLD),  # Enter
        (0x25, EXTENDED, HOLD),  # Left arrow (an extended key is still physical)
        # regression: the controller's keys, injected by Steam Input, never count
        (0x0D, INJECTED, IGNORE),
        (0x25, INJECTED | EXTENDED, IGNORE),
        (0x41, LOWER_IL_INJECTED, IGNORE),
        # volume and media keys work wherever focus is
        (0xAF, 0, IGNORE),  # volume up
        (0xAD, 0, IGNORE),  # mute
        (0xB3, 0, IGNORE),  # play/pause
        (0xA6, 0, IGNORE),  # browser back
        # modifiers, Win and PrintScreen go through so their shortcut still works
        (0x10, 0, PASS),
        (0xA2, 0, PASS),  # left Ctrl
        (0xA5, 0, PASS),  # right Alt
        (0x5B, EXTENDED, PASS),  # left Win
        (0x2C, 0, PASS),  # PrintScreen
        # neighbours of the ranges above are ordinary keys
        (0x13, 0, HOLD),  # Pause, just past Alt
        (0xB8, 0, HOLD),  # just past the media range
        (0x5D, 0, HOLD),  # Apps (menu) key
    ],
)
def test_desk_key(exe, vk, flags, expected):
    assert classify(exe, str(vk), str(flags)) == expected


@pytest.mark.parametrize("args", [[], ["65"], ["x", "0"], ["65", "-1"], ["65", "0", "1"]])
def test_desk_key_bad_arguments_are_an_error(exe, args):
    assert classify(exe, *args) == 254
