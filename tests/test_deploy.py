import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tvshell import deploy, verify


@pytest.mark.parametrize(
    ("command_line", "launcher"),
    [
        ('"C:\\Users\\u\\TvShell\\TvShell.exe"', False),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" ', False),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" launch', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" kodi tv', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe"  youtube', True),
        ('"C:\\Users\\u\\TvShell\\TvShell.exe" backwhen-app', True),
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


def test_install_clears_old_asides_once_nothing_runs_from_them(tmp_path):
    (tmp_path / "TvShell.exe").write_bytes(b"current")
    (tmp_path / "TvShell.prev.exe").write_bytes(b"older")
    (tmp_path / "TvShell.prev-1.exe").write_bytes(b"older still")
    staged = tmp_path / "TvShell.new.exe"
    staged.write_bytes(b"new")
    deploy.install(staged, tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["TvShell.exe"]


def test_free_aside_skips_names_in_use(tmp_path):
    assert deploy.free_aside(tmp_path) == tmp_path / "TvShell.prev.exe"
    (tmp_path / "TvShell.prev.exe").write_bytes(b"")
    (tmp_path / "TvShell.prev-1.exe").write_bytes(b"")
    assert deploy.free_aside(tmp_path) == tmp_path / "TvShell.prev-2.exe"


def test_remove_asides_keeps_the_held_one_and_the_installed_exe(monkeypatch, tmp_path):
    for name in ("TvShell.exe", "TvShell.prev.exe", "TvShell.prev-1.exe", "TvShell.new.exe"):
        (tmp_path / name).write_bytes(b"")
    locked(monkeypatch, "TvShell.prev-1.exe")
    deploy.remove_asides(tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "TvShell.exe",
        "TvShell.new.exe",
        "TvShell.prev-1.exe",
    ]


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


class FakeDeploy:
    """`deploy.main` with the compiler, the tests, the processes and the live checks faked.

    `calls` is what it did to the machine, in order; `verified` is what it asked the live
    checks for.
    """

    def __init__(self, monkeypatch, tmp_path, green=True, checks=(), running=()):
        self.calls: list = []
        self.verified: list[dict] = []
        self.install_dir = tmp_path / "install"
        self.install_dir.mkdir()
        monkeypatch.setattr(deploy, "REPO", tmp_path / "repo")

        def build(out: Path) -> None:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"new")

        def run_checks(install_dir, drill, since):
            self.verified.append({"install_dir": install_dir, "drill": drill, "since": since})
            return list(checks)

        monkeypatch.setattr(deploy, "build", build)

        def start_tests(built: Path) -> str:
            self.calls.append("tests started")
            return "the test run"

        def tests_passed(running: str) -> bool:
            assert running == "the test run"
            self.calls.append("tests awaited")
            return green

        monkeypatch.setattr(deploy, "start_tests", start_tests)
        monkeypatch.setattr(deploy, "tests_passed", tests_passed)
        monkeypatch.setattr(deploy, "running_tvshell", lambda: list(running))
        monkeypatch.setattr(deploy.subprocess, "run", lambda argv, **_k: self.calls.append(argv))
        monkeypatch.setattr(deploy.subprocess, "Popen", lambda argv, **_k: self.calls.append(argv))
        monkeypatch.setattr(deploy.verify, "run", run_checks)
        monkeypatch.setattr(
            deploy.verify, "write_artifact", lambda _checks: tmp_path / "verify.json"
        )

    def main(self, *args: str) -> int:
        return deploy.main(["--install-dir", str(self.install_dir), *args])

    @property
    def installed(self) -> Path:
        return self.install_dir / "TvShell.exe"


def test_main_installs_and_restarts_only_the_helper(monkeypatch, tmp_path):
    fake = FakeDeploy(
        monkeypatch,
        tmp_path,
        running=[
            {"ProcessId": 11, "CommandLine": "TvShell.exe"},
            {"ProcessId": 12, "CommandLine": "TvShell.exe kodi tv"},
        ],
    )
    assert fake.main() == 0
    # The install never waits for the tests: they start, the helper is swapped and
    # restarted, and only then is their verdict read.
    assert fake.calls == [
        "tests started",
        ["taskkill", "/PID", "11", "/F"],
        [str(fake.installed)],
        "tests awaited",
    ]
    assert fake.installed.read_bytes() == b"new"
    assert not (fake.install_dir / "TvShell.new.exe").exists()


def test_main_installs_even_when_the_helper_tests_fail(monkeypatch, tmp_path, capsys):
    # Red tests are a report, not a refusal: the fix is on the TV and the exit code says
    # there is something to repair.
    fake = FakeDeploy(
        monkeypatch,
        tmp_path,
        green=False,
        running=[{"ProcessId": 11, "CommandLine": "TvShell.exe"}],
    )
    fake.installed.write_bytes(b"old")
    assert fake.main() == 1
    assert fake.installed.read_bytes() == b"new"
    assert [str(fake.installed)] in fake.calls
    assert len(fake.verified) == 1
    out = capsys.readouterr().out
    assert "tests: FAILED on the build that is now installed" in out
    assert "deploy-tests.log" in out


def test_main_runs_the_tests_on_every_deploy():
    # The tests block nothing, so nothing needs a flag to skip them, and none exists:
    # every deploy says what it broke.
    with pytest.raises(SystemExit):
        deploy.main(["--skip-tests"])
    parser_flags = {"--build", "--install-dir", "--no-drill", "--if-stale"}
    source = Path(deploy.__file__).read_text(encoding="utf-8")
    assert set(re.findall(r'add_argument\(\s*"(--[\w-]+)"', source)) == parser_flags


def test_source_digest_ignores_line_endings(tmp_path):
    lf, crlf, other = (tmp_path / name for name in ("lf.cs", "crlf.cs", "other.cs"))
    lf.write_bytes(b"a\nb\n")
    crlf.write_bytes(b"a\r\nb\r\n")
    other.write_bytes(b"a\nc\n")
    assert deploy.source_digest(lf) == deploy.source_digest(crlf) != deploy.source_digest(other)


def test_is_stale(tmp_path):
    source = tmp_path / "TvShell.cs"
    source.write_bytes(b"one\n")
    assert deploy.is_stale(tmp_path, source), "nothing installed, or before the stamp"
    stamp = tmp_path / deploy.SOURCE_STAMP
    stamp.write_text(deploy.source_digest(source), encoding="utf-8")
    assert deploy.is_stale(tmp_path, source), "a stamp with no exe beside it"
    (tmp_path / "TvShell.exe").write_bytes(b"exe")
    assert not deploy.is_stale(tmp_path, source)
    source.write_bytes(b"two\n")
    assert deploy.is_stale(tmp_path, source)


def test_main_if_stale_installs_once(monkeypatch, tmp_path):
    fake = FakeDeploy(monkeypatch, tmp_path)
    assert fake.main("--if-stale", "--no-drill") == 0
    assert fake.installed.read_bytes() == b"new"
    done = list(fake.calls)
    assert fake.main("--if-stale", "--no-drill") == 0
    assert fake.calls == done, "the same source is not installed again"
    assert fake.main("--no-drill") == 0
    assert len(fake.calls) > len(done), "a plain deploy always installs"


def test_main_checks_the_installed_helper_at_work_and_drills_the_displays(monkeypatch, tmp_path):
    fake = FakeDeploy(monkeypatch, tmp_path, checks=[verify.Check("helper", True, "running")])
    before = dt.datetime.now().replace(microsecond=0)
    assert fake.main() == 0
    (asked,) = fake.verified
    assert asked["install_dir"] == fake.install_dir
    assert asked["drill"] is True
    # Only what is logged from the new helper's start on counts.
    assert before <= asked["since"] <= dt.datetime.now()


def test_main_no_drill_leaves_only_the_drill_out(monkeypatch, tmp_path):
    fake = FakeDeploy(monkeypatch, tmp_path)
    assert fake.main("--no-drill") == 0
    assert [asked["drill"] for asked in fake.verified] == [False]


def test_main_is_red_when_the_installed_helper_fails_a_live_check(monkeypatch, tmp_path, capsys):
    checks = [
        verify.Check("helper", True, "running"),
        verify.Check("displays", False, "the TV is the primary display"),
    ]
    fake = FakeDeploy(monkeypatch, tmp_path, checks=checks)
    assert fake.main() == 1
    assert fake.installed.read_bytes() == b"new"
    out = capsys.readouterr().out
    assert "FAIL displays: the TV is the primary display" in out
    assert "verify: FAILED (displays)" in out


def test_main_restarts_the_old_helper_when_the_swap_fails(monkeypatch, tmp_path):
    fake = FakeDeploy(
        monkeypatch, tmp_path, running=[{"ProcessId": 11, "CommandLine": "TvShell.exe"}]
    )
    fake.installed.write_bytes(b"old")

    def fail(*_a):
        raise OSError("disk full")

    monkeypatch.setattr(deploy, "install", fail)
    with pytest.raises(OSError, match="disk full"):
        fake.main()
    assert fake.calls == [
        "tests started",
        ["taskkill", "/PID", "11", "/F"],
        [str(fake.installed)],
    ]


def test_tests_command_selects_the_helper_tests_after_addopts(tmp_path):
    argv = deploy.tests_command(tmp_path / "r.xml")
    assert argv[:3] == [sys.executable, "-m", "pytest"]
    assert argv[argv.index("helper") - 1] == "-m"
    assert argv.index("helper") > argv.index("pytest")
    assert str(deploy.REPO / "tests") in argv
    assert argv[-1] == f"--junitxml={tmp_path / 'r.xml'}"


def test_start_tests_judges_the_built_exe_without_waiting(monkeypatch, tmp_path):
    monkeypatch.setattr(deploy, "REPO", tmp_path)
    seen = {}

    def popen(argv, **kwargs):
        seen.update(kwargs, argv=argv)
        kwargs["stdout"].write(b"1 failed\n")
        return "running"

    monkeypatch.setattr(deploy.subprocess, "Popen", popen)
    assert deploy.start_tests(tmp_path / "built" / "TvShell.exe") == "running"
    assert seen["argv"] == deploy.tests_command(tmp_path / "logs" / "deploy-tests.xml")
    assert seen["env"][deploy.TEST_EXE_ENV] == str(tmp_path / "built" / "TvShell.exe")
    assert seen["cwd"] == tmp_path
    assert seen["stderr"] == subprocess.STDOUT
    assert (tmp_path / "logs" / "deploy-tests.log").read_bytes() == b"1 failed\n"


class FakeRun:
    def __init__(self, code=0, hangs=False):
        self.code, self.hangs, self.killed = code, hangs, False

    def wait(self, timeout):
        if self.hangs:
            raise subprocess.TimeoutExpired("pytest", timeout)
        return self.code

    def kill(self):
        self.killed = True


@pytest.mark.parametrize(("code", "passed"), [(0, True), (1, False), (5, False)])
def test_tests_passed_reads_the_exit_code(code, passed):
    # 5 is pytest's "collected nothing": a run of no test is not a pass.
    assert deploy.tests_passed(FakeRun(code)) is passed


def test_tests_passed_calls_a_hung_run_red_and_ends_it():
    run = FakeRun(hangs=True)
    assert deploy.tests_passed(run, timeout=1) is False
    assert run.killed


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
