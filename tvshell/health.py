"""Is the TV working? What the TV's programs left behind, read for problems.

The fix pass sends a fixer at what a gate says is red and nothing else, and no CI gate can
see a crash on this PC. So the local tier (`scripts/e2e.py`, nightly on this PC) runs
`tests/test_health.py`, which calls `check()`: every problem since the window opened
becomes a red test, and the evidence goes to `logs/runtime-health.json`, the artifact the
fixer starts from.

What counts as a problem:

- **crash**: a TV program (`WATCHED_APPS`) in the Windows Application log's crash events,
  or a Kodi crash dump. Each one carries the lines Kodi logged just before it, from
  `kodi.log`, `kodi.old.log` and the copies the helper keeps in `crashes/` (Kodi
  overwrites its log on the next start). A Kodi crash after Kodi's own last line
  (`KODI_EXITED`) is a note in the artifact, not a crash: Kodi 21.3 corrupts its heap in
  its final teardown on most exits on this PC (0xc0000374 in ntdll), after it has saved
  everything. The defect is Kodi's, and no change here can fix it.
- **error**: an `ERROR` line in the helper's log. The helper logs ERROR only for a defect
  someone has to fix (`Cfg.Error` in `helper/TvShell.cs`).
- **focus**: Windows refusing most of the helper's focus changes. A refused focus is a
  controller that talks to the wrong screen, which is what the person sees.
- **helper-down**: the tray helper is not running, so nothing above is being recorded.

The window is the last `WINDOW`, cut at the moment the installed helper was built: a
deployed fix makes what came before it history, not a finding.

    python -m tvshell.health            # check, write the artifact, exit 1 on any problem
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tvshell import processes
from tvshell.tiles import INSTALL_DIR

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = REPO / "logs" / "runtime-health.json"
LOCAL = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
ROAMING = Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming")))
LOG_DIR = LOCAL / "TvShell"
KODI_DIR = ROAMING / "Kodi"

WINDOW = _dt.timedelta(hours=24)
WATCHED_APPS = ("kodi.exe", "tvshell.exe")
# Refusals at or above this count, and at least this share of all focus attempts.
FOCUS_REFUSALS = 5
FOCUS_SHARE = 1 / 3
# How much of Kodi's log before a crash is evidence.
CONTEXT_BEFORE = _dt.timedelta(seconds=90)
CONTEXT_LINES = 60
# A dump this close to a logged crash is the same crash.
SAME_CRASH = _dt.timedelta(minutes=2)

CRASH = "crash"
ERROR = "error"
FOCUS = "focus"
HELPER_DOWN = "helper-down"
KINDS = (CRASH, ERROR, FOCUS, HELPER_DOWN)
# A note, not a kind: recorded in the artifact, never a red test.
CRASH_ON_EXIT = "crash-on-exit"
# Kodi's last log line, after it has saved its settings and the EPG and unloaded everything.
# Must match `Cfg.KodiExitedLine` in helper/TvShell.cs.
KODI_EXITED = "Exiting the application..."

STAMP = "%Y-%m-%d %H:%M:%S"
LINE = re.compile(
    r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?"
    r"(?: (INFO|WARN|ERROR) +(\S+\[\d+\]))? (.*)$"
)
KODI_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\.\d+ ")


@dataclass(frozen=True)
class Entry:
    """One line of the helper's log. Lines from before log levels existed read as INFO."""

    when: _dt.datetime
    level: str
    source: str
    message: str


@dataclass(frozen=True)
class Crash:
    when: _dt.datetime
    app: str
    module: str
    code: str
    seen_in: str


@dataclass
class Finding:
    kind: str
    when: str
    summary: str
    evidence: list[str] = field(default_factory=list)


def parse_log(lines: Iterable[str]) -> list[Entry]:
    """The helper's log as entries; a line that is not an entry is skipped. Pure."""
    found = []
    for line in lines:
        m = LINE.match(line.rstrip("\r\n"))
        if m:
            when = _dt.datetime.strptime(m.group(1), STAMP)
            found.append(Entry(when, m.group(2) or "INFO", m.group(3) or "", m.group(4)))
    return found


def parse_crash_events(text: str) -> list[Crash]:
    """Crash events from `read_crash_events`' JSON, kept only for `WATCHED_APPS`. Pure."""
    if not text.strip():
        return []
    data = json.loads(text)
    rows = data if isinstance(data, list) else [data]
    found = []
    for row in rows:
        message = row.get("Message") or ""
        app = _field(r"Faulting application name: ([^,\r\n]+)", message)
        if app.lower() not in WATCHED_APPS:
            continue
        when = _dt.datetime.strptime(row["When"], "%Y-%m-%dT%H:%M:%S")
        module = _field(r"Faulting module name: ([^,\r\n]+)", message)
        code = _field(r"Exception code: (0x[0-9a-fA-F]+)", message)
        found.append(Crash(when, app, module, code, "Windows Application log"))
    return found


def _field(pattern: str, text: str) -> str:
    m = re.search(pattern, text)
    return m.group(1).strip() if m else ""


def read_crash_events(
    since: _dt.datetime, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run
) -> list[Crash]:
    """Application crash events (Application Error, id 1000) since `since`."""
    query = (
        "Get-WinEvent -FilterHashtable @{LogName='Application'; ProviderName='Application Error';"
        f" Id=1000; StartTime=[datetime]'{since.strftime(STAMP)}'}} -ErrorAction SilentlyContinue"
        " | ForEach-Object { [pscustomobject]@{ When = $_.TimeCreated.ToString('yyyy-MM-ddTHH:mm:ss');"
        " Message = $_.Message } } | ConvertTo-Json -Compress"
    )
    done = runner(
        ["powershell", "-NoProfile", "-Command", query],
        capture_output=True,
        text=True,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return parse_crash_events(done.stdout or "")


def crash_dumps(kodi_dir: Path, since: _dt.datetime) -> list[Crash]:
    """Kodi's own crash dumps written since `since`."""
    found = []
    for dump in kodi_dir.glob("kodi_crashlog-*.dmp"):
        when = _dt.datetime.fromtimestamp(dump.stat().st_mtime).replace(microsecond=0)
        if when >= since:
            found.append(Crash(when, "kodi.exe", "", "", f"Kodi crash dump {dump.name}"))
    return found


def merge_crashes(events: Sequence[Crash], dumps: Sequence[Crash]) -> list[Crash]:
    """Every crash once: a dump beside an event for the same app is that event. Pure."""
    alone = [
        d
        for d in dumps
        if not any(
            e.app.lower() == d.app.lower() and abs(e.when - d.when) <= SAME_CRASH for e in events
        )
    ]
    return sorted([*events, *alone], key=lambda c: c.when)


def collapse(lines: Sequence[str]) -> list[str]:
    """Runs of lines that differ only in their numbers, folded into one with a count. Pure."""
    out: list[str] = []
    shape, run = None, 0
    for line in lines:
        this = re.sub(r"\d+", "#", line)
        if this == shape:
            run += 1
            continue
        if run:
            out.append(f"    (... {run} more like the line above)")
        out.append(line)
        shape, run = this, 0
    if run:
        out.append(f"    (... {run} more like the line above)")
    return out


def kodi_context(when: _dt.datetime, logs: Iterable[Path]) -> list[str]:
    """What Kodi logged in the `CONTEXT_BEFORE` up to a crash, from whichever log holds it."""
    start, end = when - CONTEXT_BEFORE, when + _dt.timedelta(seconds=5)
    seen: dict[str, None] = {}
    for log in logs:
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = KODI_LINE.match(line)
            if m and start <= _dt.datetime.strptime(m.group(1), STAMP) <= end:
                seen.setdefault(line, None)
    return collapse(sorted(seen))[-CONTEXT_LINES:]


def kodi_logs(kodi_dir: Path, log_dir: Path) -> list[Path]:
    return [
        kodi_dir / "kodi.log",
        kodi_dir / "kodi.old.log",
        *sorted((log_dir / "crashes").glob("kodi-*.log")),
    ]


def after_shutdown(context: Sequence[str]) -> bool:
    """Whether the last thing Kodi logged before a crash was its own final line. Pure."""
    lines = [line for line in context if KODI_LINE.match(line)]
    return bool(lines) and lines[-1].rstrip().endswith(KODI_EXITED)


def crash_findings(
    crashes: Sequence[Crash], logs: Sequence[Path]
) -> tuple[list[Finding], list[Finding]]:
    """The crashes as findings, and as notes those Kodi had after it finished shutting down."""
    found, notes = [], []
    for c in crashes:
        detail = ", ".join(
            x for x in (c.code and f"exception {c.code}", c.module and f"in {c.module}") if x
        )
        evidence = kodi_context(c.when, logs) if c.app.lower() == "kodi.exe" else []
        when = c.when.strftime(STAMP)
        if after_shutdown(evidence):
            summary = f"{c.app} crashed after it finished shutting down"
            summary += (f" ({detail})" if detail else "") + f", seen in the {c.seen_in}"
            notes.append(Finding(CRASH_ON_EXIT, when, summary, evidence))
            continue
        summary = (
            f"{c.app} crashed" + (f" ({detail})" if detail else "") + f", seen in the {c.seen_in}"
        )
        found.append(Finding(CRASH, when, summary, evidence))
    return found, notes


def error_findings(entries: Sequence[Entry]) -> list[Finding]:
    """One finding per distinct ERROR message (numbers aside), with its count. Pure."""
    groups: dict[str, list[Entry]] = {}
    for e in entries:
        if e.level == "ERROR":
            groups.setdefault(re.sub(r"\d+", "#", e.message), []).append(e)
    found = []
    for same in groups.values():
        first = same[0]
        times = (
            f" ({len(same)} times, last {same[-1].when.strftime(STAMP)})" if len(same) > 1 else ""
        )
        found.append(
            Finding(ERROR, first.when.strftime(STAMP), first.message[:300] + times, [first.message])
        )
    return found


def focus_finding(entries: Sequence[Entry]) -> Finding | None:
    """A finding when Windows refused most focus changes the helper asked for. Pure."""
    refused = [e for e in entries if "refused" in e.message and "focus" in e.message]
    granted = [e for e in entries if re.search(r"-> (focused|focus back to)", e.message)]
    tried = len(refused) + len(granted)
    if len(refused) < FOCUS_REFUSALS or len(refused) < tried * FOCUS_SHARE:
        return None
    summary = f"Windows refused {len(refused)} of {tried} focus changes the helper asked for"
    return Finding(
        FOCUS, refused[-1].when.strftime(STAMP), summary, [e.message for e in refused[-20:]]
    )


def window_start(now: _dt.datetime, installed: Path) -> _dt.datetime:
    """The last `WINDOW`, cut at the installed helper's build: what came before a fix is history."""
    start = now - WINDOW
    try:
        built = _dt.datetime.fromtimestamp(installed.stat().st_mtime)
    except OSError:
        return start
    return max(start, built)


def read_entries(log_dir: Path, since: _dt.datetime) -> list[Entry]:
    lines: list[str] = []
    for name in ("tvshell.old.log", "tvshell.log"):
        try:
            lines += (log_dir / name).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
    return [e for e in parse_log(lines) if e.when >= since]


@dataclass
class Report:
    since: str
    checked: str
    findings: list[Finding]
    notes: list[Finding] = field(default_factory=list)

    def of(self, kind: str) -> list[Finding]:
        return [f for f in self.findings if f.kind == kind]


def check(
    now: _dt.datetime | None = None,
    log_dir: Path = LOG_DIR,
    kodi_dir: Path = KODI_DIR,
    installed: Path = INSTALL_DIR / "TvShell.exe",
    events: Callable[[_dt.datetime], list[Crash]] = read_crash_events,
    helpers: Callable[[], list[int]] = processes.running_helpers,
) -> Report:
    now = now or _dt.datetime.now().replace(microsecond=0)
    since = window_start(now, installed)
    crashes = merge_crashes(events(since), crash_dumps(kodi_dir, since))
    entries = read_entries(log_dir, since)
    crashed, notes = crash_findings(crashes, kodi_logs(kodi_dir, log_dir))
    findings = crashed + error_findings(entries)
    focus = focus_finding(entries)
    if focus:
        findings.append(focus)
    if not helpers():
        findings.append(
            Finding(HELPER_DOWN, now.strftime(STAMP), "the TvShell tray helper is not running")
        )
    return Report(since.strftime(STAMP), now.strftime(STAMP), findings, notes)


def write_artifact(report: Report, path: Path = ARTIFACT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "since": report.since,
        "checked": report.checked,
        "logs": {"helper": str(LOG_DIR / "tvshell.log"), "kodi": str(KODI_DIR / "kodi.log")},
        "findings": [asdict(f) for f in report.findings],
        "notes": [asdict(f) for f in report.notes],
    }
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def describe(findings: Sequence[Finding], artifact: Path) -> str:
    """A failure message: each problem on a line, then where the evidence is. Pure."""
    lines = [f"{f.when} {f.summary}" for f in findings]
    return "\n".join([*lines, f"evidence: {artifact}"])


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    report = check()
    path = write_artifact(report)
    for f in report.findings:
        print(f"{f.kind}: {f.when} {f.summary}")
    for f in report.notes:
        print(f"note: {f.kind}: {f.when} {f.summary}")
    print(f"health: {len(report.findings)} problem(s) since {report.since}; details in {path}")
    return 1 if report.findings else 0


if __name__ == "__main__":
    sys.exit(main())
