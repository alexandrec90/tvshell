"""Whether a Steam tile's launcher is waiting on the game (``Launcher.TileLauncherRunning``).

Steam Input follows only the process tree of the tile it launched, so a game with no such
launcher gets no controller, and the log says so. Run through ``tile-launcher``: with a
command line it judges that one, with none it looks at this PC's processes, where a
stand-in named ``TvShell.exe`` plays the launcher.
"""

from __future__ import annotations

import subprocess

import pytest

from tvshell import deploy

COVERS = (
    "Launcher.TileLauncherRunning",
    "Launcher.IsGameLaunch",
    "Launcher.TileLauncherCli",
    "mode:tile-launcher",
)

YES, NO = 1, 0
SLEEPER = "class P { static void Main() { System.Threading.Thread.Sleep(60000); } }\n"


def ask(exe, *args: str) -> int:
    return subprocess.run([str(exe), "tile-launcher", *args], check=False, timeout=60).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (r"C:\x\TvShell.exe launch", YES),
        (r"C:\x\tvshell.EXE LAUNCH", YES),
        ("'C:\\x y\\TvShell.exe' launch".replace("'", '"'), YES),
        (r"C:\x\TvShell.exe", NO),
        (r"C:\x\TvShell.exe kodi launch", NO),
        (r"C:\x\TvShell.exe launch-desk", NO),
        (r"C:\x\other.exe launch", NO),
        ("", NO),
    ],
)
def test_which_command_line_is_the_games_launcher(exe, line, expected):
    assert ask(exe, line) == expected


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory):
    csc = deploy.find_csc()
    if csc is None:
        pytest.fail("the .NET Framework C# compiler is not on this PC")
    out = tmp_path_factory.mktemp("stand-in")
    (out / "sleeper.cs").write_text(SLEEPER, encoding="utf-8")
    target = out / "TvShell.exe"
    done = subprocess.run(  # noqa: S603 - the local compiler on our own source
        [str(csc), "/nologo", "/target:winexe", f"/out:{target}", str(out / "sleeper.cs")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return target


def test_a_running_launcher_is_found_and_another_mode_is_not(exe, stand_in):
    # A game may really be running on this PC, so "none" is whatever it was before.
    before = ask(exe)
    for mode, expected in (("launch", YES), ("kodi", before)):
        proc = subprocess.Popen([str(stand_in), mode])  # noqa: S603 - our own build
        try:
            assert ask(exe) == expected, mode
        finally:
            proc.kill()
            proc.wait(timeout=10)
