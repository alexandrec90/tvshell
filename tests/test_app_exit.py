"""How a TV app's end is judged (``AppExit.Judge``): closed, crashed, or crashed once done.

Run through the exe's ``app-exit`` mode, with a stand-in log in ``tmp_path``.
"""

from __future__ import annotations

import subprocess

import pytest

pytestmark = pytest.mark.ci_e2e

# What this module exercises in the helper (tests/test_helper_coverage.py holds it to that).
COVERS = ("AppExit.Judge", "AppExit.Cli", "Cfg.IsCrash", "mode:app-exit")

EXITED = "Exiting the application..."  # Cfg.KodiExitedLine
CLOSED, AFTER_SHUTDOWN, CRASHED, BAD_ARGUMENTS = 0, 1, 2, 254
ACCESS_VIOLATION = -1073741819  # 0xC0000005 as Windows' signed exit code
HEAP_CORRUPTION = -1073740940  # 0xC0000374
KILLED_BY_DOTNET = -1  # what Process.Kill leaves
CLR_EXCEPTION = -532462766  # 0xE0434352


def run(exe, *args: str) -> int:
    return subprocess.run([str(exe), "app-exit", *args], check=False, timeout=30).returncode  # noqa: S603 - our own build


@pytest.mark.parametrize(
    ("code", "last_line", "expected"),
    [
        (0, EXITED, CLOSED),
        (1, "unload skin", CLOSED),
        (KILLED_BY_DOTNET, EXITED, CLOSED),
        (KILLED_BY_DOTNET, "unload skin", CLOSED),
        (ACCESS_VIOLATION, EXITED, AFTER_SHUTDOWN),
        (HEAP_CORRUPTION, EXITED, AFTER_SHUTDOWN),
        (ACCESS_VIOLATION, "OutputPicture - timeout waiting for buffer", CRASHED),
        (CLR_EXCEPTION, "unload skin", CRASHED),
    ],
)
def test_judge(exe, tmp_path, code, last_line, expected):
    log = tmp_path / "kodi.log"
    log.write_text(f"first line\n2026-11-24 23:45:47.043 T:1 info: {last_line}\n", encoding="utf-8")
    assert run(exe, str(code), str(log), EXITED) == expected


def test_a_crash_with_no_log_is_a_crash(exe, tmp_path):
    assert run(exe, str(ACCESS_VIOLATION), str(tmp_path / "missing.log"), EXITED) == CRASHED


def test_bad_arguments(exe):
    assert run(exe) == BAD_ARGUMENTS
    assert run(exe, "not-a-number", "x.log", EXITED) == BAD_ARGUMENTS
