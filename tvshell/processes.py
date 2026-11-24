"""Which running ``TvShell.exe`` is the tray helper and which is a launcher.

One exe runs in several modes: with no argument it is the helper, with one it is a
launcher a Steam tile started (a show or a game in progress). The deploy stops only the
helper, and the health checks ask whether one is running.
"""

from __future__ import annotations

import json
import subprocess

LAUNCHER_ARGS = (
    "launch",
    "launch-desk",
    "launch-tv",
    "kodi",
    "youtube",
    "backwhen-app",
)


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


def running_helpers() -> list[int]:
    """PIDs of the tray helpers running on this PC right now."""
    return helper_pids(running_tvshell())
