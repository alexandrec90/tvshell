"""Whether the Kodi launcher restarts Kodi after it ends (``KodiLauncher.ShouldRestart``).

A crash mid-programme restarts it, up to three times in the window; a close, or Kodi's own
crash after it finished shutting down (a close too), ends the tile. Run through the exe's
``kodi-restart`` mode, with a stand-in log in ``tmp_path``.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.ci_e2e

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = ("KodiLauncher.ShouldRestart", "KodiLauncher.RestartCli", "mode:kodi-restart")

EXITED = "Exiting the application..."  # Cfg.KodiExitedLine
RESTART, LEAVE, BAD_ARGUMENTS = 1, 0, 254
ACCESS_VIOLATION = -1073741819  # 0xC0000005 as Windows' signed exit code
KILLED_BY_DOTNET = -1  # what Process.Kill leaves


def run(exe, *args: str) -> int:
    return subprocess.run([str(exe), "kodi-restart", *args], check=False, timeout=30).returncode  # noqa: S603 - our own build


def kodi_log(tmp_path, last_line: str):
    log = tmp_path / "kodi.log"
    log.write_text(f"first line\n2026-11-27 09:32:16.139 T:1 info: {last_line}\n", encoding="utf-8")
    return log


@pytest.mark.parametrize("restarts", [0, 1, 2])
def test_a_crash_mid_programme_restarts(exe, tmp_path, restarts):
    log = kodi_log(tmp_path, "OutputPicture - timeout waiting for buffer")
    assert run(exe, str(ACCESS_VIOLATION), str(log), str(restarts)) == RESTART


@pytest.mark.parametrize("restarts", [3, 10])
def test_a_kodi_crashing_over_and_over_is_left_down(exe, tmp_path, restarts):
    log = kodi_log(tmp_path, "OutputPicture - timeout waiting for buffer")
    assert run(exe, str(ACCESS_VIOLATION), str(log), str(restarts)) == LEAVE


@pytest.mark.parametrize(
    ("code", "last_line"),
    [
        (0, EXITED),
        (1, "unload skin"),
        (KILLED_BY_DOTNET, "unload skin"),
        (ACCESS_VIOLATION, EXITED),  # crashed after it finished shutting down: it was closed
    ],
)
def test_a_close_is_not_restarted(exe, tmp_path, code, last_line):
    assert run(exe, str(code), str(kodi_log(tmp_path, last_line)), "0") == LEAVE


def test_a_crash_with_no_log_restarts(exe, tmp_path):
    assert run(exe, str(ACCESS_VIOLATION), str(tmp_path / "missing.log"), "0") == RESTART


def test_bad_arguments(exe, tmp_path):
    log = str(kodi_log(tmp_path, "x"))
    assert run(exe) == BAD_ARGUMENTS
    assert run(exe, "not-a-number", log, "0") == BAD_ARGUMENTS
    assert run(exe, str(ACCESS_VIOLATION), log, "many") == BAD_ARGUMENTS
    assert run(exe, str(ACCESS_VIOLATION), log, "-1") == BAD_ARGUMENTS
