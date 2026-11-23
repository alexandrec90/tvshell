"""The live checks a deploy ends with (``tvshell/verify.py``), with the PC faked.

The helper exe is a function from argv to (exit code, stdout); processes and the log are
callables. The real thing runs from ``python -m tvshell.deploy``.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
from pathlib import Path

from tvshell import health, verify

GOOD = r"\\.\DISPLAY2 desk {X=0,Y=0,Width=1920,Height=1200} primary | \\.\DISPLAY1 tv {X=1920,Y=0,Width=1280,Height=768}"
TV_FIRST = r"\\.\DISPLAY1 tv {X=0,Y=0,Width=852,Height=480} primary | \\.\DISPLAY2 desk {X=852,Y=0,Width=1920,Height=1200}"
SMALLER_TV = GOOD.replace("Width=1280,Height=768", "Width=720,Height=576")
EXE = Path("C:/install/TvShell.exe")
NOW = dt.datetime(2026, 11, 23, 9, 0, 0)


class FakeExe:
    """Answers `desk-primary check` from `layouts` in turn (the last one repeats) and
    `desk-primary drill` with `drill`; records every argv."""

    def __init__(self, layouts, check_code=0, drill=(0, "the helper made the desk primary again")):
        self.layouts = list(layouts)
        self.check_code = check_code
        self.drill = drill
        self.ran: list[list[str]] = []

    def __call__(self, argv):
        self.ran.append(list(argv)[1:])
        if list(argv)[1:] == ["desk-primary", "drill"]:
            return subprocess.CompletedProcess(argv, self.drill[0], stdout=self.drill[1] + "\n")
        layout = self.layouts.pop(0) if len(self.layouts) > 1 else self.layouts[0]
        return subprocess.CompletedProcess(argv, self.check_code, stdout=layout + "\n")


def entry(level: str, message: str = "m") -> health.Entry:
    return health.Entry(NOW, level, "helper[1]", message)


def no_sleep(_seconds: float) -> None:
    return None


def test_helper_check_passes_when_a_helper_runs():
    check = verify.helper_check(lambda: [42], sleep=no_sleep)
    assert check.ok and "42" in check.detail


def test_helper_check_waits_for_a_helper_that_was_just_started():
    answers = [[], [], [7]]
    slept: list[float] = []
    check = verify.helper_check(lambda: answers.pop(0), wait=10, sleep=slept.append)
    assert check.ok
    assert len(slept) == 2


def test_helper_check_fails_once_the_wait_is_over():
    slept: list[float] = []
    check = verify.helper_check(lambda: [], wait=3, sleep=slept.append)
    assert not check.ok
    assert "not running" in check.detail
    assert sum(slept) == 3


def test_displays_check_passes_with_the_desk_primary():
    exe = FakeExe([GOOD])
    check = verify.displays_check(EXE, exe)
    assert check == verify.Check("displays", True, GOOD)
    assert exe.ran == [["desk-primary", "check"]]


def test_displays_check_fails_with_the_tv_primary():
    # regression: the boot that left the TV as the primary display was seen by nothing
    check = verify.displays_check(EXE, FakeExe([TV_FIRST], check_code=verify.TV_IS_PRIMARY))
    assert not check.ok
    assert "the TV is the primary display" in check.detail and "852" in check.detail


def test_displays_check_fails_on_an_answer_it_does_not_know():
    # an installed helper too old to know the mode, say
    check = verify.displays_check(EXE, FakeExe([""], check_code=253))
    assert not check.ok
    assert "253" in check.detail


def test_errors_check_passes_without_an_error_line():
    assert verify.errors_check([entry("INFO"), entry("WARN")]).ok


def test_errors_check_names_every_error_line():
    check = verify.errors_check([entry("INFO"), entry("ERROR", "tick error"), entry("ERROR", "x")])
    assert not check.ok
    assert "2 ERROR line(s)" in check.detail and "tick error" in check.detail


def test_drill_passes_when_the_helper_restores_the_same_layout():
    exe = FakeExe([GOOD, GOOD])
    check = verify.drill_check(EXE, exe, no_sleep)
    assert check.ok
    assert exe.ran == [
        ["desk-primary", "check"],
        ["desk-primary", "drill"],
        ["desk-primary", "check"],
    ]


def test_drill_fails_when_the_helper_did_not_undo_it():
    check = verify.drill_check(
        EXE, FakeExe([GOOD, GOOD], drill=(1, "the helper did not")), no_sleep
    )
    assert not check.ok
    assert "drill answered 1" in check.detail


def test_drill_fails_when_the_displays_come_back_different():
    # the desk is primary again, but Windows gave the TV another resolution on the way
    check = verify.drill_check(EXE, FakeExe([GOOD, SMALLER_TV]), no_sleep)
    assert not check.ok
    assert "did not come back as they were" in check.detail


def test_drill_with_no_tv_to_drill_is_not_a_failure():
    exe = FakeExe([GOOD], drill=(verify.DRILL_NO_TV, "tv.cfg names no TV"))
    check = verify.drill_check(EXE, exe, no_sleep)
    assert check.ok and "nothing to drill" in check.detail


def run(fake, drill=True, helpers=(1,), log=()):
    seen: list[dt.datetime] = []

    def entries(since):
        seen.append(since)
        return list(log)

    checks = verify.run(
        Path("C:/install"),
        drill=drill,
        since=NOW,
        pc=verify.Pc(helpers=lambda: list(helpers), runner=fake, entries=entries, sleep=no_sleep),
    )
    assert seen == [NOW]
    return checks


def test_run_exe_runs_the_helper_and_captures_what_it_prints(monkeypatch):
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        return subprocess.CompletedProcess(argv, 3, stdout="layout\n")

    monkeypatch.setattr(verify.subprocess, "run", fake_run)
    done = verify.run_exe(("TvShell.exe", "desk-primary", "check"))
    assert (done.returncode, done.stdout) == (3, "layout\n")
    assert seen["argv"] == ["TvShell.exe", "desk-primary", "check"]
    assert seen["capture_output"] and seen["text"] and seen["timeout"] == 60


def test_log_since_reads_the_helpers_own_log(monkeypatch, tmp_path):
    (tmp_path / "tvshell.log").write_text(
        "2026-11-23 08:00:00.000 ERROR helper[1] before\n"
        "2026-11-23 09:30:00.000 ERROR helper[1] after\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(health, "LOG_DIR", tmp_path)
    assert [e.message for e in verify.log_since(NOW)] == ["after"]


def test_run_does_every_check_in_order():
    checks = run(FakeExe([GOOD]))
    assert [c.name for c in checks] == ["helper", "displays", "drill", "errors"]
    assert all(c.ok for c in checks)


def test_run_without_the_drill_changes_nothing_on_the_pc():
    exe = FakeExe([GOOD])
    checks = run(exe, drill=False)
    assert [c.name for c in checks] == ["helper", "displays", "errors"]
    assert exe.ran == [["desk-primary", "check"]]


def test_run_does_not_drill_without_a_helper_to_undo_it():
    exe = FakeExe([GOOD])
    checks = run(exe, helpers=())
    assert [c.name for c in checks] == ["helper", "displays", "errors"]
    assert ["desk-primary", "drill"] not in exe.ran


def test_run_does_not_drill_displays_that_are_already_wrong():
    exe = FakeExe([TV_FIRST], check_code=verify.TV_IS_PRIMARY)
    checks = run(exe)
    assert [c.name for c in checks if not c.ok] == ["displays"]
    assert ["desk-primary", "drill"] not in exe.ran


def test_run_counts_an_error_logged_during_the_drill():
    checks = run(FakeExe([GOOD]), log=[entry("ERROR", "drill: the helper did not")])
    assert [c.name for c in checks if not c.ok] == ["errors"]


def test_report_lists_each_check_and_the_verdict(tmp_path):
    checks = [verify.Check("helper", True, "running"), verify.Check("drill", False, "no")]
    text = verify.report(checks, tmp_path / "verify.json")
    assert text.splitlines()[:2] == ["ok   helper: running", "FAIL drill: no"]
    assert "verify: FAILED (drill)" in text and "verify.json" in text
    assert "all checks passed" in verify.report(checks[:1], tmp_path / "verify.json")


def test_write_artifact_is_written_on_a_pass_and_on_a_failure(tmp_path):
    path = tmp_path / "logs" / "verify.json"
    verify.write_artifact([verify.Check("helper", True, "running")], path)
    assert json.loads(path.read_text(encoding="utf-8"))["ok"] is True
    verify.write_artifact([verify.Check("helper", False, "down")], path)
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["ok"] is False
    assert body["checks"] == [{"name": "helper", "ok": False, "detail": "down"}]


def test_main_prints_the_verdict_and_exits_by_it(monkeypatch, tmp_path, capsys):
    asked = []

    def fake_run(install_dir, drill):
        asked.append((install_dir, drill))
        return [verify.Check("displays", not drill, "layout")]

    monkeypatch.setattr(verify, "run", fake_run)
    monkeypatch.setattr(verify, "ARTIFACT", tmp_path / "verify.json")
    monkeypatch.setattr(verify, "write_artifact", lambda checks: tmp_path / "verify.json")
    assert verify.main(["--install-dir", str(tmp_path)]) == 0
    assert verify.main(["--install-dir", str(tmp_path), "--drill"]) == 1
    assert asked == [(tmp_path, False), (tmp_path, True)]
    assert "verify: FAILED (displays)" in capsys.readouterr().out
