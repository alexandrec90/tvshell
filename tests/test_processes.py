"""Telling the tray helper from a launcher (``tvshell/processes.py``).

``is_launcher``, ``helper_pids`` and ``running_tvshell`` are exercised in
``tests/test_deploy.py``, through the names ``deploy`` re-exports.
"""

from tvshell import deploy, processes


def test_deploy_uses_the_same_functions():
    assert deploy.is_launcher is processes.is_launcher
    assert deploy.helper_pids is processes.helper_pids
    assert deploy.running_tvshell is processes.running_tvshell
    assert deploy.LAUNCHER_ARGS is processes.LAUNCHER_ARGS


def test_running_helpers_lists_only_the_helpers(monkeypatch):
    monkeypatch.setattr(
        processes,
        "running_tvshell",
        lambda: [
            {"ProcessId": 1, "CommandLine": '"C:\\t\\TvShell.exe"'},
            {"ProcessId": 2, "CommandLine": '"C:\\t\\TvShell.exe" kodi tv'},
        ],
    )
    assert processes.running_helpers() == [1]


def test_running_helpers_with_nothing_running(monkeypatch):
    monkeypatch.setattr(processes, "running_tvshell", list)
    assert processes.running_helpers() == []
