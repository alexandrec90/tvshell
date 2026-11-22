import datetime as dt
import json
import os
import subprocess

import pytest

from tvshell import health

T = dt.datetime(2026, 11, 21, 11, 35, 44)


def at(seconds: int) -> dt.datetime:
    return T + dt.timedelta(seconds=seconds)


def entry(message: str, level: str = "INFO", seconds: int = 0) -> health.Entry:
    return health.Entry(at(seconds), level, "helper[1]", message)


# --- the helper's log ---------------------------------------------------------------------


def test_parse_log_reads_levelled_and_old_lines():
    found = health.parse_log(
        [
            "2026-11-21 11:35:50.123 ERROR helper[42] Kodi crashed (exit 0xC0000005)\n",
            "2026-11-21 11:35:50.200 WARN  kodi[7] kodi rpc x failed",
            "2026-11-20 19:05:41 kodi launcher: playing channel 146",  # before levels existed
            "not a log line",
            "",
        ]
    )
    assert [(e.level, e.source, e.message) for e in found] == [
        ("ERROR", "helper[42]", "Kodi crashed (exit 0xC0000005)"),
        ("WARN", "kodi[7]", "kodi rpc x failed"),
        ("INFO", "", "kodi launcher: playing channel 146"),
    ]
    assert found[0].when == dt.datetime(2026, 11, 21, 11, 35, 50)


def test_read_entries_spans_the_rotated_log_and_cuts_at_since(tmp_path):
    (tmp_path / "tvshell.old.log").write_text("2026-11-21 09:00:00.000 INFO  helper[1] old\n")
    (tmp_path / "tvshell.log").write_text(
        "2026-11-21 10:00:00.000 INFO  helper[1] before\n2026-11-21 12:00:00.000 INFO  helper[1] new\n"
    )
    found = health.read_entries(tmp_path, dt.datetime(2026, 11, 21, 8, 0))
    assert [e.message for e in found] == ["old", "before", "new"]
    assert [e.message for e in health.read_entries(tmp_path, dt.datetime(2026, 11, 21, 11, 0))] == [
        "new"
    ]


def test_read_entries_without_logs_is_empty(tmp_path):
    assert health.read_entries(tmp_path, T) == []


def test_error_findings_group_repeats_by_shape():
    found = health.error_findings(
        [
            entry("tick error (1x so far): boom", "ERROR"),
            entry("all fine"),
            entry("tick error (2x so far): boom", "ERROR", seconds=60),
            entry("refused", "WARN"),
            entry("add-on patch X: no longer contains the code it patches", "ERROR"),
        ]
    )
    assert [f.kind for f in found] == ["error", "error"]
    assert "2 times" in found[0].summary
    assert found[1].summary.startswith("add-on patch X")


def test_focus_finding_needs_many_refusals_and_a_large_share():
    refused = [entry("controller press -> Windows refused to focus game", "WARN")] * 5
    granted = [entry("controller press -> focused game")]
    found = health.focus_finding(refused + granted)
    assert found is not None and found.kind == "focus"
    assert "5 of 6" in found.summary
    # a few refusals is ordinary; many among very many successes is too
    assert health.focus_finding(refused[:4]) is None
    assert health.focus_finding(refused + granted * 20) is None


def test_focus_finding_counts_the_keyboard_too():
    keys = [entry("keyboard on desk -> Windows refused the focus change", "WARN")] * 5
    back = [entry("keyboard on desk -> focus back to the desk")] * 11
    assert health.focus_finding(keys + back) is None
    assert health.focus_finding(keys + back[:5]) is not None


# --- crashes ------------------------------------------------------------------------------

KODI_EVENT = (
    "Faulting application name: kodi.exe, version: 21.3.0.0, time stamp: 0x6922eafc\r\n"
    "Faulting module name: VCRUNTIME140.dll, version: 14.44.35211.0\r\n"
    "Exception code: 0xc0000005\r\n"
)


def test_parse_crash_events_keeps_watched_apps_only():
    text = json.dumps(
        [
            {"When": "2026-11-21T11:35:45", "Message": KODI_EVENT},
            {"When": "2026-11-21T11:00:00", "Message": "Faulting application name: other.exe, x"},
        ]
    )
    assert health.parse_crash_events(text) == [
        health.Crash(
            dt.datetime(2026, 11, 21, 11, 35, 45),
            "kodi.exe",
            "VCRUNTIME140.dll",
            "0xc0000005",
            "Windows Application log",
        )
    ]


def test_parse_crash_events_single_object_and_empty():
    # PowerShell unwraps a single result, and prints nothing for none
    one = json.dumps({"When": "2026-11-21T11:35:45", "Message": KODI_EVENT})
    assert len(health.parse_crash_events(one)) == 1
    assert health.parse_crash_events("") == []


def test_read_crash_events_asks_for_the_window(monkeypatch):
    seen = {}

    def run(argv, **kwargs):
        seen["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    assert health.read_crash_events(T, runner=run) == []
    assert "StartTime=[datetime]'2026-11-21 11:35:44'" in seen["argv"][-1]


def test_crash_dumps_since(tmp_path):
    old, new = tmp_path / "kodi_crashlog-a.dmp", tmp_path / "kodi_crashlog-b.dmp"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    os.utime(old, (at(-7200).timestamp(),) * 2)
    os.utime(new, (T.timestamp(),) * 2)
    found = health.crash_dumps(tmp_path, at(-60))
    assert [c.seen_in for c in found] == ["Kodi crash dump kodi_crashlog-b.dmp"]


def test_merge_crashes_drops_a_dump_beside_its_event():
    event = health.Crash(T, "kodi.exe", "m", "0xc0000005", "log")
    same = health.Crash(at(30), "kodi.exe", "", "", "dump")
    other = health.Crash(at(3600), "kodi.exe", "", "", "dump")
    assert health.merge_crashes([event], [same, other]) == [event, other]


def test_collapse_folds_runs_that_differ_in_numbers():
    lines = ["sync error: 5006.1", "sync error: 5007.2", "sync error: 5008.3", "other", "x 1"]
    assert health.collapse(lines) == [
        "sync error: 5006.1",
        "    (... 2 more like the line above)",
        "other",
        "x 1",
    ]
    assert health.collapse(["a 1", "a 2"]) == ["a 1", "    (... 1 more like the line above)"]


def test_kodi_context_takes_the_lines_before_the_crash_from_any_copy(tmp_path):
    log, copy = tmp_path / "kodi.log", tmp_path / "kodi-copy.log"
    log.write_text(
        "2026-11-21 11:30:00.000 T:1 info <general>: long before\n"
        "2026-11-21 11:35:23.700 T:2 info <general>: Opening stream: 50001 source: 256\n"
        "                                                   \n"
        "2026-11-21 11:35:44.698 T:3 warning <general>: timeout waiting for buffer\n"
    )
    copy.write_text("2026-11-21 11:35:44.698 T:3 warning <general>: timeout waiting for buffer\n")
    found = health.kodi_context(T, [log, copy, tmp_path / "missing.log"])
    assert found == [
        "2026-11-21 11:35:23.700 T:2 info <general>: Opening stream: 50001 source: 256",
        "2026-11-21 11:35:44.698 T:3 warning <general>: timeout waiting for buffer",
    ]


def test_kodi_logs_lists_both_logs_then_the_saved_crash_copies(tmp_path):
    kodi_dir, log_dir = tmp_path / "kodi", tmp_path / "tvshell"
    (log_dir / "crashes").mkdir(parents=True)
    for name in ("kodi-2026-11-21b.log", "kodi-2026-11-20.log", "other.log"):
        (log_dir / "crashes" / name).write_text("")
    assert health.kodi_logs(kodi_dir, log_dir) == [
        kodi_dir / "kodi.log",
        kodi_dir / "kodi.old.log",
        log_dir / "crashes" / "kodi-2026-11-20.log",
        log_dir / "crashes" / "kodi-2026-11-21b.log",
    ]
    assert health.kodi_logs(kodi_dir, tmp_path / "none") == [
        kodi_dir / "kodi.log",
        kodi_dir / "kodi.old.log",
    ]


def test_crash_findings_attach_kodi_context_only_to_kodi(tmp_path):
    log = tmp_path / "kodi.log"
    log.write_text("2026-11-21 11:35:40.000 T:1 info <general>: just before\n")
    crashes = [
        health.Crash(T, "kodi.exe", "VCRUNTIME140.dll", "0xc0000005", "Windows Application log"),
        health.Crash(T, "TvShell.exe", "", "", "Windows Application log"),
    ]
    (kodi, helper), _ = health.crash_findings(crashes, [log])
    assert kodi.summary == (
        "kodi.exe crashed (exception 0xc0000005, in VCRUNTIME140.dll), seen in the Windows Application log"
    )
    assert kodi.evidence == ["2026-11-21 11:35:40.000 T:1 info <general>: just before"]
    assert helper.summary == "TvShell.exe crashed, seen in the Windows Application log"
    assert helper.evidence == []


# --- the window and the whole check -------------------------------------------------------


def test_window_start_cuts_at_the_installed_build(tmp_path):
    exe = tmp_path / "TvShell.exe"
    assert health.window_start(T, exe) == T - health.WINDOW  # nothing installed
    exe.write_bytes(b"x")
    os.utime(exe, (at(-3600).timestamp(),) * 2)
    assert health.window_start(T, exe) == at(-3600)
    os.utime(exe, (at(-90000).timestamp(),) * 2)
    assert health.window_start(T, exe) == T - health.WINDOW


def make_check(tmp_path, log: str = "", crashes=(), helpers=(1,), kodi_log: str = ""):
    log_dir, kodi_dir = tmp_path / "TvShell", tmp_path / "Kodi"
    log_dir.mkdir()
    kodi_dir.mkdir()
    (log_dir / "tvshell.log").write_text(log)
    (kodi_dir / "kodi.log").write_text(kodi_log)
    return health.check(
        now=T,
        log_dir=log_dir,
        kodi_dir=kodi_dir,
        installed=tmp_path / "none.exe",
        events=lambda since: list(crashes),
        helpers=lambda: list(helpers),
    )


def test_check_healthy(tmp_path):
    report = make_check(tmp_path, "2026-11-21 11:00:00.000 INFO  helper[1] helper started\n")
    assert report.findings == []
    assert report.since == "2026-11-20 11:35:44"


def test_check_finds_each_kind(tmp_path):
    log = "2026-11-21 11:35:50.000 ERROR helper[1] Kodi crashed (exit 0xC0000005)\n"
    crash = health.Crash(T, "kodi.exe", "", "", "Windows Application log")
    report = make_check(tmp_path, log, crashes=[crash], helpers=())
    assert [f.kind for f in report.findings] == ["crash", "error", "helper-down"]
    assert report.of("crash")[0].when == "2026-11-21 11:35:44"


SHUTDOWN = (
    "2026-11-21 11:35:40.000 T:1 info <general>: Stopping the application...\n"
    "2026-11-21 11:35:43.900 T:1 info <general>: unload sections\n"
    "2026-11-21 11:35:44.157 T:1 info <general>: Exiting the application...\n"
)


def test_after_shutdown_reads_kodis_last_line():
    lines = SHUTDOWN.splitlines()
    assert health.after_shutdown(lines)
    assert health.after_shutdown([*lines, "    (... 1 more like the line above)"])
    # Kodi went on after a line like it, or never got there
    assert not health.after_shutdown([*lines, "2026-11-21 11:35:44.200 T:1 info <general>: x"])
    assert not health.after_shutdown(lines[:2])
    assert not health.after_shutdown([])


def test_the_helper_reads_the_same_last_line():
    source = (health.REPO / "helper" / "TvShell.cs").read_text(encoding="utf-8")
    assert f'KodiExitedLine = "{health.KODI_EXITED}";' in source


def test_check_notes_a_kodi_crash_after_it_finished_shutting_down(tmp_path):
    # Kodi 21.3 corrupts its heap in its last teardown on this PC: settings and EPG are saved
    # by then, the fault is Kodi's, and nothing here can fix it. Recorded, but not red.
    crash = health.Crash(T, "kodi.exe", "ntdll.dll", "0xc0000374", "Windows Application log")
    report = make_check(tmp_path, crashes=[crash], kodi_log=SHUTDOWN)
    assert report.findings == []
    (note,) = report.notes
    assert note.kind == "crash-on-exit"
    assert note.summary.startswith("kodi.exe crashed after it finished shutting down")
    assert note.evidence[-1].endswith("Exiting the application...")


def test_check_still_finds_a_kodi_crash_while_it_runs(tmp_path):
    playing = "2026-11-21 11:35:43.000 T:3 warning <general>: timeout waiting for buffer\n"
    crash = health.Crash(T, "kodi.exe", "VCRUNTIME140.dll", "0xc0000005", "Windows Application log")
    report = make_check(tmp_path, crashes=[crash], kodi_log=playing)
    assert [f.kind for f in report.findings] == ["crash"]
    assert report.notes == []


def test_crash_findings_never_note_a_helper_crash(tmp_path):
    log = tmp_path / "kodi.log"
    log.write_text(SHUTDOWN)
    found, notes = health.crash_findings([health.Crash(T, "TvShell.exe", "", "", "x")], [log])
    assert [f.kind for f in found] == ["crash"]
    assert notes == []


def test_write_artifact_and_describe(tmp_path):
    report = health.Report("a", "b", [health.Finding("crash", "t", "kodi.exe crashed", ["line"])])
    report.notes.append(health.Finding("crash-on-exit", "t", "kodi.exe crashed after", []))
    path = health.write_artifact(report, tmp_path / "logs" / "runtime-health.json")
    body = json.loads(path.read_text())
    assert body["findings"] == [
        {"kind": "crash", "when": "t", "summary": "kodi.exe crashed", "evidence": ["line"]}
    ]
    assert body["notes"] == [
        {"kind": "crash-on-exit", "when": "t", "summary": "kodi.exe crashed after", "evidence": []}
    ]
    assert health.describe(report.findings, path) == f"t kodi.exe crashed\nevidence: {path}"


def test_main_exit_code_follows_findings(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(health, "ARTIFACT", tmp_path / "h.json")
    monkeypatch.setattr(health, "check", lambda: health.Report("a", "b", []))
    assert health.main([]) == 0
    noted = health.Report("a", "b", [], [health.Finding("crash-on-exit", "t", "after")])
    monkeypatch.setattr(health, "check", lambda: noted)
    assert health.main([]) == 0
    assert "note: crash-on-exit: t after" in capsys.readouterr().out
    monkeypatch.setattr(
        health, "check", lambda: health.Report("a", "b", [health.Finding("error", "t", "boom")])
    )
    assert health.main([]) == 1
    assert "error: t boom" in capsys.readouterr().out


# --- this PC: run nightly by scripts/e2e.py -----------------------------------------------
# One test per kind, so each problem is its own signature for the fix pass.


@pytest.fixture(scope="module")
def live():
    report = health.check()
    return report, health.write_artifact(report)


@pytest.mark.local
@pytest.mark.parametrize("kind", health.KINDS)
def test_the_tv_is_healthy(live, kind):
    report, artifact = live
    problems = report.of(kind)
    assert not problems, health.describe(problems, artifact)
