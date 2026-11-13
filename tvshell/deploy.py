"""Build the TvShell helper from ``helper/TvShell.cs`` and install it.

Compiles with the C# compiler that ships with .NET Framework (no SDK needed), then
swaps the new exe into the install directory and restarts the background helper.
A running exe cannot be overwritten on Windows but can be renamed, so the old one is
moved aside first; a launcher still running from it (a game or Kodi in progress) keeps
running and is left alone -- only the helper itself is restarted.

    python -m tvshell.deploy            # build, install, restart the helper
    python -m tvshell.deploy --build    # compile only, into logs/
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from tvshell.tiles import INSTALL_DIR

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "helper" / "TvShell.cs"
REFERENCES = ("System.Windows.Forms.dll", "System.Drawing.dll", "System.Management.dll")
LAUNCHER_ARGS = ("launch", "launch-desk", "launch-tv", "kodi", "youtube")


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


def is_launcher(command_line: str) -> bool:
    """Whether a TvShell.exe command line is a launcher (has a mode argument) rather than the helper."""
    parts = command_line.replace('"', " ").split()
    try:
        exe_at = next(i for i, part in enumerate(parts) if part.lower().endswith("tvshell.exe"))
    except StopIteration:
        return False
    return any(arg.lower() in LAUNCHER_ARGS for arg in parts[exe_at + 1 : exe_at + 2])


def helper_pids(processes: list[dict]) -> list[int]:
    """PIDs of running helpers, from ``[{"ProcessId": .., "CommandLine": ..}]``."""
    return [
        p["ProcessId"]
        for p in processes
        if p.get("CommandLine") and not is_launcher(p["CommandLine"])
    ]


def running_tvshell() -> list[dict]:
    query = (
        "Get-CimInstance Win32_Process -Filter \"Name='TvShell.exe'\" | "
        "Select-Object ProcessId, CommandLine | ConvertTo-Json -Compress"
    )
    cmd = ["powershell", "-NoProfile", "-Command", query]
    done = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - fixed query
    out = done.stdout.strip()
    if not out:
        return []
    data = json.loads(out)
    return data if isinstance(data, list) else [data]


def build(output: Path) -> None:
    csc = find_csc()
    if csc is None:
        raise SystemExit("C# compiler not found (.NET Framework 4 csc.exe)")
    cmd = csc_command(csc, SOURCE, output)
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)  # noqa: S603 - local compiler
    log = REPO / "logs" / "build-helper.log"
    log.write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode != 0:
        raise SystemExit(f"build failed; compiler output in {log}")


def install(new_exe: Path, install_dir: Path) -> Path:
    """Swap ``new_exe`` in as ``install_dir/TvShell.exe``; returns the installed path."""
    target = install_dir / "TvShell.exe"
    aside = install_dir / "TvShell.prev.exe"
    aside.unlink(missing_ok=True)
    if target.exists():
        target.rename(aside)
    new_exe.replace(target)
    try:
        aside.unlink(missing_ok=True)
    except PermissionError:
        pass  # a launcher is still running from it; the next deploy removes it
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--build", action="store_true", help="compile only, into logs/TvShell.exe")
    parser.add_argument("--install-dir", type=Path, default=INSTALL_DIR)
    args = parser.parse_args(argv)

    if args.build:
        build(REPO / "logs" / "TvShell.exe")
        print(f"built {REPO / 'logs' / 'TvShell.exe'}")
        return 0
    staged = args.install_dir / "TvShell.new.exe"
    build(staged)
    for pid in helper_pids(running_tvshell()):
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, check=False)  # noqa: S603, S607 - Windows' taskkill on a PID we just listed
    target = install(staged, args.install_dir)
    subprocess.Popen([str(target)], cwd=args.install_dir)  # noqa: S603 - the exe we just installed
    print(f"installed and restarted {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
