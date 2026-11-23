"""The Kodi launcher ends a Kodi that hangs after it finished shutting down (``KodiLauncher.WaitOut``).

Kodi 21.3 can stay alive for hours after logging its last line, and Steam counts the tile
as running for as long as it does. The cases run through the exe's ``wait-out-kodi``
mode against a stand-in process and a stand-in ``kodi.log`` in ``tmp_path``, so no real
Kodi is touched.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.local

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = (
    "KodiLauncher.WaitOut",
    "KodiLauncher.WaitOutCli",
    "Cfg.LogEndsWith",
    "mode:wait-out-kodi",
)

EXITED = "Exiting the application..."  # Cfg.KodiExitedLine
ENDED, WENT_BY_ITSELF = 2, 0


def stand_in(seconds: int = 120) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep({seconds})"])  # noqa: S603 - our own interpreter


def wait_out(exe: Path, pid: int, log: Path, grace: int) -> subprocess.Popen:
    return subprocess.Popen([str(exe), "wait-out-kodi", str(pid), str(log), str(grace)])  # noqa: S603 - our own build


def write_log(log: Path, last: str) -> None:
    log.write_text(
        f"2026-11-22 09:14:42.678 T:1 info <general>: unload sections\n2026-11-22 09:14:42.696 T:1 info <general>: {last}\n",
        encoding="utf-8",
    )


def test_a_kodi_hung_after_its_exited_line_is_ended(exe, tmp_path):
    log = tmp_path / "kodi.log"
    write_log(log, EXITED)
    kodi = stand_in()
    try:
        started = time.monotonic()
        assert wait_out(exe, kodi.pid, log, grace=2).wait(timeout=30) == ENDED
        assert kodi.wait(timeout=10) is not None
        assert time.monotonic() - started >= 2  # not before the grace period
    finally:
        kodi.kill()


def test_a_kodi_still_working_is_left_alone(exe, tmp_path):
    log = tmp_path / "kodi.log"
    write_log(log, "CVideoPlayerVideo::OutputPicture - timeout waiting for buffer")
    kodi = stand_in()
    try:
        waiter = wait_out(exe, kodi.pid, log, grace=1)
        time.sleep(4)
        assert kodi.poll() is None
        kodi.kill()
        assert waiter.wait(timeout=10) == WENT_BY_ITSELF
    finally:
        kodi.kill()


def test_no_kodi_log_ends_nothing(exe, tmp_path):
    kodi = stand_in()
    try:
        waiter = wait_out(exe, kodi.pid, tmp_path / "missing.log", grace=1)
        time.sleep(3)
        assert kodi.poll() is None
        kodi.kill()
        assert waiter.wait(timeout=10) == WENT_BY_ITSELF
    finally:
        kodi.kill()


def test_a_kodi_that_exits_by_itself_is_not_ended(exe, tmp_path):
    log = tmp_path / "kodi.log"
    write_log(log, EXITED)
    kodi = stand_in(seconds=1)
    assert wait_out(exe, kodi.pid, log, grace=30).wait(timeout=20) == WENT_BY_ITSELF


def test_a_line_that_only_reaches_the_end_later_starts_the_grace_then(exe, tmp_path):
    log = tmp_path / "kodi.log"
    write_log(log, "unload skin")
    kodi = stand_in()
    try:
        waiter = wait_out(exe, kodi.pid, log, grace=2)
        time.sleep(3)
        assert kodi.poll() is None
        write_log(log, EXITED)
        assert waiter.wait(timeout=20) == ENDED
    finally:
        kodi.kill()
