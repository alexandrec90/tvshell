import subprocess
from pathlib import Path

import pytest

from tvshell import deploy


@pytest.mark.parametrize(
    ("command_line", "launcher"),
    [
        ('"C:\\Users\\u\\TvShell\\TvShell.exe"', False),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" ', False),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" launch', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" kodi tv', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe"  youtube', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" --unknown', False),
        ("something.exe launch", False),
    ],
)
def test_is_launcher(command_line, launcher):
    assert deploy.is_launcher(command_line) is launcher


def test_helper_pids_keeps_only_helpers():
    processes = [
        {"ProcessId": 1, "CommandLine": '"C:\\t\\TvShell.exe"'},
        {"ProcessId": 2, "CommandLine": '"C:\\t\\TvShell.exe" kodi radio'},
        {"ProcessId": 3, "CommandLine": None},  # access denied: never kill blind
    ]
    assert deploy.helper_pids(processes) == [1]


def test_csc_command_builds_windowless_exe_with_references():
    cmd = deploy.csc_command(Path("csc.exe"), Path("src.cs"), Path("out.exe"))
    assert cmd[0] == "csc.exe" and cmd[-1] == "src.cs"
    assert "/target:winexe" in cmd and "/out:out.exe" in cmd
    assert {f"/r:{r}" for r in deploy.REFERENCES} <= set(cmd)


def test_find_csc_missing_returns_none(tmp_path):
    assert deploy.find_csc(str(tmp_path)) is None


def test_find_csc_found(tmp_path):
    csc = tmp_path / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    csc.parent.mkdir(parents=True)
    csc.write_bytes(b"")
    assert deploy.find_csc(str(tmp_path)) == csc


def test_install_swaps_exe_and_removes_old(tmp_path):
    (tmp_path / "TvShell.exe").write_bytes(b"old")
    staged = tmp_path / "TvShell.new.exe"
    staged.write_bytes(b"new")
    target = deploy.install(staged, tmp_path)
    assert target.read_bytes() == b"new"
    assert not staged.exists()
    assert not (tmp_path / "TvShell.prev.exe").exists()


def locked(monkeypatch, *names: str) -> None:
    """Make these files undeletable, as a running exe is (it can still be renamed)."""
    real = Path.unlink

    def unlink(self, missing_ok=False):
        if self.name in names and self.exists():
            raise PermissionError(13, "in use", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)


def test_install_with_old_exe_still_running(monkeypatch, tmp_path):
    # regression: a launcher running from the exe set aside by the previous deploy made
    # the next deploy crash after it had already stopped the helper
    (tmp_path / "TvShell.prev.exe").write_bytes(b"older, still running")
    (tmp_path / "TvShell.exe").write_bytes(b"old")
    staged = tmp_path / "TvShell.new.exe"
    staged.write_bytes(b"new")
    locked(monkeypatch, "TvShell.prev.exe")
    assert deploy.install(staged, tmp_path).read_bytes() == b"new"
    assert (tmp_path / "TvShell.prev.exe").read_bytes() == b"older, still running"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TvShell.exe", "TvShell.prev.exe"]


def test_install_keeps_a_running_old_exe_aside_and_sweeps_it_later(monkeypatch, tmp_path):
    (tmp_path / "TvShell.prev.exe").write_bytes(b"older, still running")
    (tmp_path / "TvShell.exe").write_bytes(b"old, still running")
    staged = tmp_path / "TvShell.new.exe"
    staged.write_bytes(b"new")
    locked(monkeypatch, "TvShell.prev.exe", "TvShell.prev-1.exe")
    deploy.install(staged, tmp_path)
    assert (tmp_path / "TvShell.prev-1.exe").read_bytes() == b"old, still running"
    monkeypatch.undo()  # both launchers have exited
    staged.write_bytes(b"newer")
    deploy.install(staged, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TvShell.exe"]
    assert (tmp_path / "TvShell.exe").read_bytes() == b"newer"


def test_install_into_empty_dir(tmp_path):
    staged = tmp_path / "TvShell.new.exe"
    staged.write_bytes(b"new")
    assert deploy.install(staged, tmp_path).read_bytes() == b"new"


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [
        ('[{"ProcessId":1,"CommandLine":"a"},{"ProcessId":2,"CommandLine":"b"}]', [1, 2]),
        ('{"ProcessId":7,"CommandLine":"a"}', [7]),  # PowerShell unwraps a single result
        ("", []),
    ],
)
def test_running_tvshell_parses_powershell_json(monkeypatch, stdout, expected):
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=stdout, stderr=""),
    )
    assert [p["ProcessId"] for p in deploy.running_tvshell()] == expected


def test_main_build_only(monkeypatch, capsys):
    built = []
    monkeypatch.setattr(deploy, "build", built.append)
    assert deploy.main(["--build"]) == 0
    assert built == [deploy.REPO / "logs" / "TvShell.exe"]


def test_main_installs_and_restarts_only_the_helper(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(deploy, "build", lambda out: out.write_bytes(b"new"))
    monkeypatch.setattr(
        deploy,
        "running_tvshell",
        lambda: [
            {"ProcessId": 11, "CommandLine": "TvShell.exe"},
            {"ProcessId": 12, "CommandLine": "TvShell.exe kodi tv"},
        ],
    )
    monkeypatch.setattr(deploy.subprocess, "run", lambda argv, **_k: calls.append(argv))
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda argv, **_k: calls.append(argv))
    assert deploy.main(["--install-dir", str(tmp_path)]) == 0
    assert calls == [["taskkill", "/PID", "11", "/F"], [str(tmp_path / "TvShell.exe")]]
    assert (tmp_path / "TvShell.exe").read_bytes() == b"new"


def test_main_restarts_the_old_helper_when_the_swap_fails(monkeypatch, tmp_path):
    (tmp_path / "TvShell.exe").write_bytes(b"old")
    calls = []
    monkeypatch.setattr(deploy, "build", lambda out: out.write_bytes(b"new"))
    monkeypatch.setattr(
        deploy, "running_tvshell", lambda: [{"ProcessId": 11, "CommandLine": "TvShell.exe"}]
    )
    monkeypatch.setattr(deploy.subprocess, "run", lambda argv, **_k: calls.append(argv))
    monkeypatch.setattr(deploy.subprocess, "Popen", lambda argv, **_k: calls.append(argv))

    def fail(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(deploy, "install", fail)
    with pytest.raises(OSError, match="disk full"):
        deploy.main(["--install-dir", str(tmp_path)])
    assert calls == [["taskkill", "/PID", "11", "/F"], [str(tmp_path / "TvShell.exe")]]


def test_build_reports_compiler_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    (tmp_path / "logs").mkdir()
    monkeypatch.setattr(deploy, "find_csc", lambda: Path("csc.exe"))
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, stdout="error CS1002", stderr=""),
    )
    with pytest.raises(SystemExit, match="build failed"):
        deploy.build(tmp_path / "out.exe")
    assert "CS1002" in (tmp_path / "logs" / "build-helper.log").read_text()


def test_build_in_a_checkout_without_logs_dir(monkeypatch, tmp_path):
    # regression: a fresh worktree has no logs/ (it is ignored), and the build crashed
    # writing its compiler log there instead of building
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    monkeypatch.setattr(deploy, "find_csc", lambda: Path("csc.exe"))
    monkeypatch.setattr(
        deploy.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout="ok", stderr=""),
    )
    deploy.build(tmp_path / "logs" / "TvShell.exe")
    assert (tmp_path / "logs" / "build-helper.log").read_text() == "ok"


def test_build_without_compiler(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "find_csc", lambda: None)
    with pytest.raises(SystemExit, match="compiler not found"):
        deploy.build(tmp_path / "out.exe")


@pytest.mark.local
def test_helper_source_compiles(tmp_path, monkeypatch):
    if deploy.find_csc() is None:
        pytest.fail(
            "the .NET Framework C# compiler (Framework64/v4.0.30319/csc.exe) is not on this PC"
        )
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    (tmp_path / "logs").mkdir()
    deploy.build(tmp_path / "TvShell.exe")
    assert (tmp_path / "TvShell.exe").stat().st_size > 0
