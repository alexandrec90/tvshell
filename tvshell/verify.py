"""Is the TV side right on this PC, right now? Asked of the real thing after every deploy.

The helper's tests judge its decisions; this judges the installed helper at work. Each
check reads live state and is one line of the answer:

- ``helper``: a tray helper is running.
- ``displays``: the desk is the primary display, not the TV (``TvShell.exe desk-primary
  check``, read-only).
- ``errors``: the helper has logged no ERROR since it started.
- ``drill`` (with ``--drill``; the deploy runs it): the fault for real. The TV is made
  the primary display, as a bad boot leaves it, and the running helper must put the desk
  back within 15 seconds, leaving the displays exactly as they were. Both screens flicker.

The evidence is in ``logs/verify.json``, written on a pass too.

    python -m tvshell.verify            # read-only
    python -m tvshell.verify --drill    # and the display drill
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from tvshell import health, processes
from tvshell.tiles import INSTALL_DIR

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = REPO / "logs" / "verify.json"

# `TvShell.exe desk-primary ...` exit codes (DeskPrimary.Cli in helper/TvShell.cs).
DESK_IS_PRIMARY = 0
TV_IS_PRIMARY = 3
DRILL_PASSED = 0
# The drill found nothing to drill: tv.cfg names no TV among exactly two displays.
DRILL_NO_TV = 4

HELPER_WAIT = 10  # seconds for a helper the deploy has just started to appear
SETTLE = 2  # seconds for Windows to finish a display change before it is read back

Run = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def run_exe(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the installed helper
        list(argv), capture_output=True, text=True, check=False, timeout=60
    )


def helper_check(
    helpers: Callable[[], list[int]],
    wait: float = HELPER_WAIT,
    sleep: Callable[[float], None] = time.sleep,
) -> Check:
    """A helper is running; one the deploy started a moment ago gets `wait` seconds."""
    waited = 0.0
    while True:
        pids = helpers()
        if pids:
            return Check("helper", True, f"running, pid {', '.join(map(str, pids))}")
        if waited >= wait:
            return Check("helper", False, "the TvShell tray helper is not running")
        sleep(1)
        waited += 1


def displays_check(exe: Path, run: Run = run_exe) -> Check:
    """The desk is the primary display. Read-only."""
    done = run([str(exe), "desk-primary", "check"])
    layout = done.stdout.strip()
    if done.returncode == DESK_IS_PRIMARY:
        return Check("displays", True, layout)
    if done.returncode == TV_IS_PRIMARY:
        return Check("displays", False, f"the TV is the primary display: {layout}")
    return Check(
        "displays", False, f"desk-primary check answered {done.returncode}: {layout}".rstrip(": ")
    )


def errors_check(entries: Sequence[health.Entry]) -> Check:
    """No ERROR line among `entries` (the log since the helper started). Pure."""
    errors = [e for e in entries if e.level == "ERROR"]
    if not errors:
        return Check("errors", True, "no ERROR in the log since the helper started")
    lines = "; ".join(f"{e.when.strftime(health.STAMP)} {e.source} {e.message}" for e in errors)
    return Check("errors", False, f"{len(errors)} ERROR line(s): {lines}")


def drill_check(
    exe: Path,
    run: Run = run_exe,
    sleep: Callable[[float], None] = time.sleep,
) -> Check:
    """Make the TV primary for real; the running helper must restore the same layout."""
    before = run([str(exe), "desk-primary", "check"]).stdout.strip()
    done = run([str(exe), "desk-primary", "drill"])
    said = done.stdout.strip()
    if done.returncode == DRILL_NO_TV:
        return Check("drill", True, f"nothing to drill ({said}): {before}")
    sleep(SETTLE)
    after = run([str(exe), "desk-primary", "check"]).stdout.strip()
    if done.returncode != DRILL_PASSED:
        return Check("drill", False, f"drill answered {done.returncode}: {said}; now {after}")
    if after != before:
        return Check(
            "drill", False, f"the displays did not come back as they were: {before} -> {after}"
        )
    return Check("drill", True, f"{said}; displays as before: {after}")


def log_since(start: _dt.datetime) -> Sequence[health.Entry]:
    """The helper's own log from `start` on."""
    return health.read_entries(health.LOG_DIR, start)


@dataclass(frozen=True)
class Pc:
    """How the checks reach this PC: its processes, the helper exe, the log, the clock.

    The tests put fakes here.
    """

    helpers: Callable[[], list[int]] = processes.running_helpers
    runner: Run = run_exe
    entries: Callable[[_dt.datetime], Sequence[health.Entry]] = log_since
    sleep: Callable[[float], None] = time.sleep


def run(
    install_dir: Path = INSTALL_DIR,
    drill: bool = False,
    since: _dt.datetime | None = None,
    pc: Pc | None = None,
) -> list[Check]:
    """Every check, in order. `since` is when the helper under judgement started."""
    pc = pc or Pc()
    exe = install_dir / "TvShell.exe"
    since = since or health.window_start(_dt.datetime.now(), exe)
    checks = [helper_check(pc.helpers, sleep=pc.sleep), displays_check(exe, pc.runner)]
    # Only with a helper to do the undoing, and from a layout that is right to begin with.
    if drill and all(c.ok for c in checks):
        checks.append(drill_check(exe, pc.runner, pc.sleep))
    # Last, so an ERROR the drill provoked is counted.
    checks.append(errors_check(pc.entries(since)))
    return checks


def write_artifact(checks: Sequence[Check], path: Path = ARTIFACT) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "checked": _dt.datetime.now().strftime(health.STAMP),
        "ok": all(c.ok for c in checks),
        "checks": [asdict(c) for c in checks],
    }
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def report(checks: Sequence[Check], artifact: Path) -> str:
    """The terminal's lines: one per check, then the verdict and where the evidence is. Pure."""
    lines = [f"{'ok  ' if c.ok else 'FAIL'} {c.name}: {c.detail}" for c in checks]
    failed = [c.name for c in checks if not c.ok]
    verdict = f"verify: FAILED ({', '.join(failed)})" if failed else "verify: all checks passed"
    return "\n".join([*lines, f"{verdict}; details in {artifact}"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument(
        "--drill",
        action="store_true",
        help="also make the TV the primary display for real and require the helper to undo it",
    )
    parser.add_argument("--install-dir", type=Path, default=INSTALL_DIR)
    args = parser.parse_args(argv)
    checks = run(args.install_dir, drill=args.drill)
    print(report(checks, write_artifact(checks)))
    return 0 if all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
