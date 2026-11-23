"""The store launcher is closed when the game session ends (``Launcher.CloseStoreLauncher``).

Steam counts a tile as running while anything it started is alive. The game goes through
its store launcher, which stays open after the game exits, so without this the tile is
stuck on "Resume" and clicking it does nothing. The cases run through the exe's
``close-store-launcher`` mode against a stand-in store launcher named in a ``game.cfg``
beside a fresh build, so no real launcher is touched.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tvshell import deploy

pytestmark = pytest.mark.local

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = (
    "Launcher.CloseStoreLauncher",
    "Launcher.CloseStoreLauncherCli",
    "Game.Load",
    "mode:close-store-launcher",
)

FAKE = "TvShellTestStoreLauncher"
SLEEPER = "class P { static void Main() { System.Threading.Thread.Sleep(120000); } }\n"


@pytest.fixture(scope="module")
def built(exe) -> Path:
    """The helper's directory, with a stand-in store launcher compiled beside it."""
    csc = deploy.find_csc()
    if csc is None:
        pytest.fail("the .NET Framework C# compiler is not on this PC")
    out = exe.parent
    src = out / "sleeper.cs"
    src.write_text(SLEEPER, encoding="utf-8")
    done = subprocess.run(  # noqa: S603 - the local compiler on our own source
        [str(csc), "/nologo", "/target:winexe", f"/out:{out / (FAKE + '.exe')}", str(src)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return out


def close(exe_dir: Path, cfg: str) -> int:
    (exe_dir / "game.cfg").write_text(cfg, encoding="utf-8")
    exe = exe_dir / "TvShell.exe"
    return subprocess.run([str(exe), "close-store-launcher"], check=False, timeout=30).returncode  # noqa: S603 - our own build


def alive(proc: subprocess.Popen) -> bool:
    for _ in range(20):
        if proc.poll() is not None:
            return False
        time.sleep(0.1)
    return True


def test_closes_every_instance_of_the_store_launcher(built):
    procs = [subprocess.Popen([str(built / (FAKE + ".exe"))]) for _ in range(2)]  # noqa: S603 - our own build
    try:
        assert close(built, f"process=NoSuchGame\nlauncher=x.exe\nlauncher_process={FAKE}\n") == 0
        assert not any(alive(p) for p in procs)
    finally:
        for p in procs:
            p.kill()


def test_nothing_running_is_success(built):
    assert close(built, f"process=NoSuchGame\nlauncher=x.exe\nlauncher_process={FAKE}\n") == 0


def test_no_launcher_process_configured_closes_nothing(built):
    proc = subprocess.Popen([str(built / (FAKE + ".exe"))])  # noqa: S603 - our own build
    try:
        assert close(built, "process=NoSuchGame\nlauncher=x.exe\n") == 0
        assert alive(proc)
    finally:
        proc.kill()
