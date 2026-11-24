"""Build the TvShell helper from ``helper/TvShell.cs``, install it at once, and test it meanwhile.

A fix reaches the TV as soon as it compiles. Nothing but a build failure stops the
install; the tests run beside it and say afterwards what the fix broke.

1. **Build**, with the C# compiler that ships with .NET Framework (no SDK needed).
2. **Tests start**: every test of the helper (the ``helper`` marker) against that very
   binary, in the background. Their output goes to ``logs/deploy-tests.log``.
3. **Install**, without waiting for them: the new exe is swapped into the install
   directory and the background helper restarted. A running exe cannot be overwritten on
   Windows but can be renamed, so the old one is moved aside first; a launcher still
   running from it (a game or Kodi in progress) keeps running and is left alone -- only
   the helper itself is restarted.
4. **Verify** (``tvshell.verify``), once the tests are done: the installed helper is judged
   on this PC as it runs, including the display drill, which makes the TV the primary
   display for real and requires the helper to undo it. Both screens flicker for a few
   seconds; ``--no-drill`` leaves that one check out, for a deploy while someone is
   watching the TV. The evidence is in ``logs/verify.json``.

Red tests or a red verify exit 1 **with the new helper in place**: the exit code is a
report of what to fix next, not a refusal.

    python -m tvshell.deploy              # build, install, test, verify
    python -m tvshell.deploy --no-drill   # the same, without the display drill
    python -m tvshell.deploy --build      # compile only, into logs/
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

from tvshell import verify
from tvshell.processes import LAUNCHER_ARGS, helper_pids, is_launcher, running_tvshell
from tvshell.tiles import INSTALL_DIR

__all__ = ["LAUNCHER_ARGS", "helper_pids", "is_launcher", "running_tvshell"]

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "helper" / "TvShell.cs"
REFERENCES = ("System.Windows.Forms.dll", "System.Drawing.dll", "System.Management.dll")
# Names a built helper for the tests to judge instead of compiling their own
# (the `exe` fixture in tests/conftest.py).
TEST_EXE_ENV = "TVSHELL_TEST_EXE"
TESTS_MARKER = "helper"
TESTS_TIMEOUT = 600  # seconds; a hung test run is reported as red, never waited on forever


def find_csc(windir: str | None = None) -> Path | None:
    """The 64-bit .NET Framework 4 C# compiler, if present."""
    root = Path(windir or os.environ.get("WINDIR", "C:/Windows"))
    csc = root / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    return csc if csc.exists() else None


def csc_command(csc: Path, source: Path, output: Path) -> list[str]:
    """The compiler invocation for a windowless (tray) exe."""
    refs = [f"/r:{name}" for name in REFERENCES]
    return [
        str(csc),
        "/nologo",
        "/target:winexe",
        "/optimize",
        f"/out:{output}",
        *refs,
        str(source),
    ]


def tests_command(report: Path) -> list[str]:
    """The pytest run that judges a built helper. Pure.

    `-m` comes after `addopts`, so it replaces their `not local`. pytest exits 5 when it
    collected nothing, so a run of no test is red, not a pass.
    """
    return [
        sys.executable,
        "-m",
        "pytest",
        str(REPO / "tests"),
        "-m",
        TESTS_MARKER,
        "-q",
        "--tb=short",
        "-rfE",
        # This run must not rewrite the last-failed set `run-tests.py --changed` reads.
        "-p",
        "no:cacheprovider",
        f"--junitxml={report}",
    ]


def start_tests(built: Path) -> subprocess.Popen[bytes]:
    """Start every helper test against `built` and return at once; the install does not wait.

    The output goes to ``logs/deploy-tests.log`` and the junit report beside it, on a pass too.
    """
    logs = REPO / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    with (logs / "deploy-tests.log").open("wb") as out:
        # The child holds its own handle to the log, so closing ours here is safe.
        return subprocess.Popen(  # noqa: S603 - our own interpreter running our own tests
            tests_command(logs / "deploy-tests.xml"),
            cwd=REPO,
            env={**os.environ, TEST_EXE_ENV: str(built)},
            stdout=out,
            stderr=subprocess.STDOUT,
        )


def tests_passed(running: subprocess.Popen[bytes], timeout: float = TESTS_TIMEOUT) -> bool:
    """Wait for the tests `start_tests` began; True when they all passed. A hang is red."""
    try:
        return running.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        running.kill()
        return False


def build(output: Path) -> None:
    csc = find_csc()
    if csc is None:
        raise SystemExit("C# compiler not found (.NET Framework 4 csc.exe)")
    log = REPO / "logs" / "build-helper.log"
    # logs/ is ignored, so a fresh checkout or worktree has none.
    log.parent.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = csc_command(csc, SOURCE, output)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - local compiler
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"build failed; compiler output in {log}")


def remove_asides(install_dir: Path) -> None:
    """Delete every set-aside ``TvShell.prev*.exe`` that nothing runs from any more.

    A launcher started from an older build (a show in progress) keeps its exe undeletable,
    possibly across several deploys; that one stays until a later deploy finds it free.
    """
    for aside in install_dir.glob("TvShell.prev*.exe"):
        try:
            aside.unlink()
        except PermissionError:
            pass


def free_aside(install_dir: Path) -> Path:
    """The first of ``TvShell.prev.exe``, ``TvShell.prev-1.exe``, ... that does not exist."""
    aside, n = install_dir / "TvShell.prev.exe", 0
    while aside.exists():
        n += 1
        aside = install_dir / f"TvShell.prev-{n}.exe"
    return aside


def install(new_exe: Path, install_dir: Path) -> Path:
    """Swap ``new_exe`` in as ``install_dir/TvShell.exe``; returns the installed path.

    A running exe cannot be deleted but can be renamed, so the current one is set aside
    under a free name rather than overwritten.
    """
    target = install_dir / "TvShell.exe"
    remove_asides(install_dir)
    if target.exists():
        target.rename(free_aside(install_dir))
    new_exe.replace(target)
    remove_asides(install_dir)
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--build", action="store_true", help="compile only, into logs/TvShell.exe")
    parser.add_argument("--install-dir", type=Path, default=INSTALL_DIR)
    parser.add_argument(
        "--no-drill",
        action="store_true",
        help="skip the display drill after installing (it flickers both screens for a few seconds)",
    )
    args = parser.parse_args(argv)

    if args.build:
        build(REPO / "logs" / "TvShell.exe")
        print(f"built {REPO / 'logs' / 'TvShell.exe'}")
        return 0
    # Built and judged away from the install directory, so the tests never run a helper
    # that has the real game.cfg and tv.cfg beside it.
    built = REPO / "logs" / "deploy" / "TvShell.exe"
    build(built)
    # Beside the install, never before it: a fix is on the TV while its tests still run.
    tests = start_tests(built)
    args.install_dir.mkdir(parents=True, exist_ok=True)
    staged = args.install_dir / "TvShell.new.exe"
    shutil.copy2(built, staged)
    started = _dt.datetime.now().replace(microsecond=0)
    for pid in helper_pids(running_tvshell()):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)  # noqa: S603, S607 - Windows' taskkill on a PID we just listed
    target = args.install_dir / "TvShell.exe"
    try:
        install(staged, args.install_dir)
    finally:
        # The helper is already stopped: whatever happened, start the exe that is in place,
        # so a failed swap never leaves the TV without it.
        if target.exists():
            subprocess.Popen([str(target)], cwd=args.install_dir)  # noqa: S603 - the installed exe
    print(f"installed and restarted {target}; its tests are still running", flush=True)
    # The drill makes the TV primary for a moment, which a test reading the live displays
    # would see as the fault, so the live checks wait for the tests to finish.
    green = tests_passed(tests)
    if not green:
        print(
            "tests: FAILED on the build that is now installed; details in "
            f"{REPO / 'logs' / 'deploy-tests.log'}"
        )
    else:
        print("tests: passed")
    checks = verify.run(args.install_dir, drill=not args.no_drill, since=started)
    print(verify.report(checks, verify.write_artifact(checks)))
    return 0 if green and all(c.ok for c in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
