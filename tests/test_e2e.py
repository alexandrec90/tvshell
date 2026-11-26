import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "e2e.py"
spec = importlib.util.spec_from_file_location("e2e", SCRIPT)
assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
e2e = importlib.util.module_from_spec(spec)
sys.modules["e2e"] = e2e  # dataclasses resolves annotations through sys.modules
spec.loader.exec_module(e2e)


@pytest.mark.parametrize(
    ("classname", "name", "expected"),
    [
        ("tests.test_deploy", "test_x", "tests/test_deploy.py::test_x"),
        ("tests.test_kodi.TestRpc", "test_y", "tests/test_kodi.py::TestRpc::test_y"),
        ("", "tests.test_broken", "tests.test_broken"),
    ],
)
def test_pytest_id(classname, name, expected):
    assert e2e.pytest_id(classname, name) == expected


def write_report(path: Path, body: str) -> Path:
    path.write_text(f'<?xml version="1.0"?><testsuites><testsuite>{body}</testsuite></testsuites>')
    return path


def test_cases_reads_outcomes(tmp_path):
    report = write_report(
        tmp_path / "r.xml",
        '<testcase classname="tests.test_a" name="test_ok"/>'
        '<testcase classname="tests.test_a" name="test_bad"><failure message="boom"/></testcase>'
        '<testcase classname="tests.test_a" name="test_err"><error message="fixture"/></testcase>'
        '<testcase classname="tests.test_a" name="test_skip"><skipped message="why"/></testcase>',
    )
    outcomes = {c.ident.split("::")[1]: c.outcome for c in e2e.cases(report)}
    assert outcomes == {
        "test_ok": "passed",
        "test_bad": "failed",
        "test_err": "failed",
        "test_skip": "skipped",
    }


def test_cases_unreadable_report_is_empty(tmp_path):
    assert e2e.cases(tmp_path / "missing.xml") == []
    assert e2e.cases(write_report(tmp_path / "bad.xml", "<unclosed")) == []


def test_verdict_green():
    assert e2e.verdict(0, [e2e.Case("t::a", e2e.PASSED)], strict=True) == (0, [])


def test_verdict_prints_failed_line_per_failure():
    code, lines = e2e.verdict(1, [e2e.Case("t::a", e2e.FAILED, "boom\nmore")], strict=False)
    assert code == 1
    assert lines == ["FAILED t::a - boom"]


def test_verdict_nothing_executed_is_red():
    code, lines = e2e.verdict(5, [], strict=False)
    assert code == 5
    assert "no local test executed" in lines[-1]
    code, _ = e2e.verdict(0, [e2e.Case("t::s", e2e.SKIPPED)], strict=False)
    assert code == 1


def test_verdict_skip_fails_only_under_scheduler():
    found = [e2e.Case("t::a", e2e.PASSED), e2e.Case("t::s", e2e.SKIPPED, "no csc")]
    assert e2e.verdict(0, found, strict=False) == (0, ["SKIPPED t::s - skipped: no csc"])
    code, lines = e2e.verdict(0, found, strict=True)
    assert code == 1
    assert lines[0] == "FAILED t::s - skipped: no csc"


def test_pytest_argv_selects_marker_after_addopts():
    argv = e2e.pytest_argv(Path("r.xml"), ["tests/test_deploy.py"])
    assert argv[:3] == ["uv", "run", "--frozen"]
    # pytest lives in the `dev` extra: a bare `uv run` in a tree with no `.venv` built one
    # without it, and the run ended `No module named pytest`.
    assert {"--all-extras", "--all-groups"} <= set(argv[: argv.index("python")])
    assert argv[argv.index("local") - 1] == "-m"
    assert argv.index("local") > argv.index("pytest")
    assert "tests/test_deploy.py" in argv and "tests" not in argv
    assert argv[-1] == "--junitxml=r.xml"


def test_pytest_argv_selects_the_marker_it_is_given():
    argv = e2e.pytest_argv(Path("r.xml"), marker="ci_e2e")
    assert argv[argv.index("ci_e2e") - 1] == "-m"
    assert "local" not in argv


@pytest.mark.parametrize(
    ("argv", "marker", "named"),
    [
        ([], "local", []),
        (["--ci"], "ci_e2e", []),
        (["--ci", "tests/test_x.py::test_y"], "ci_e2e", ["tests/test_x.py::test_y"]),
        (["tests/test_x.py::test_y", "--ci"], "ci_e2e", ["tests/test_x.py::test_y"]),
        (["tests/test_x.py::test_y"], "local", ["tests/test_x.py::test_y"]),
    ],
)
def test_split_flag_picks_the_tier_and_keeps_the_ids(argv, marker, named):
    assert e2e.split_flag(argv) == (marker, named)


def test_verdict_names_the_tier_that_executed_nothing():
    _, lines = e2e.verdict(5, [], strict=True, marker="ci_e2e")
    assert "no ci_e2e test executed" in lines[-1]


def test_a_tree_with_no_venv_runs_without_the_sessions_no_sync(tmp_path):
    """A devkit session's `UV_NO_SYNC` reached a run in a tree with no `.venv`: `uv run`
    made an empty one, installed nothing, and the run ended `No module named pytest`."""
    session = {e2e.NO_SYNC: "1", "KEPT": "x"}
    assert e2e.run_env(session, tmp_path) == {"KEPT": "x", "PYTHONUTF8": "1"}
    (tmp_path / ".venv").mkdir()
    assert e2e.run_env(session, tmp_path)[e2e.NO_SYNC] == "1", "a borrowed .venv is left alone"


class Runs:
    """A fake `subprocess.run`: pytest passes, and git and the deploy exit as told."""

    def __init__(self, helper_differs=0, deploy=0):
        self.calls: list[list[str]] = []
        self.codes = {"git": helper_differs, "deploy": deploy}

    def __call__(self, argv, **_kwargs):
        self.calls.append(list(argv))
        if argv[0] == "git":
            return subprocess.CompletedProcess(argv, self.codes["git"])
        if argv == e2e.INSTALL_ARGV:
            return subprocess.CompletedProcess(argv, self.codes["deploy"], "deployed\n", "")
        write_report(e2e.REPORT, '<testcase classname="tests.test_x" name="test_y"/>')
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(e2e, "REPORT", tmp_path / "logs" / "e2e-junit.xml")
    monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)
    (tmp_path / ".git").mkdir()
    return tmp_path


def test_the_nightly_run_installs_the_merged_helper_after_the_tests(checkout):
    runs = Runs()
    assert e2e.main([], runner=runs, env={e2e.ENV_FLAG: "1"}) == 0
    assert runs.calls[-1] == e2e.INSTALL_ARGV
    assert "pytest" in runs.calls[0]


def test_installs_merged_asks_git_about_the_helper_only(checkout):
    runs = Runs()
    assert e2e.installs_merged(checkout, [], True, runs)
    assert runs.calls == [["git", "diff", "--quiet", "origin/main", "--", "helper"]]


def test_a_red_deploy_is_a_named_failure(checkout, capsys):
    assert e2e.main([], runner=Runs(deploy=1), env={e2e.ENV_FLAG: "1"}) == 1
    assert "FAILED tvshell/deploy.py::install_merged" in capsys.readouterr().out


@pytest.mark.parametrize("why", ["by hand", "named tests", "worktree", "helper not as merged"])
def test_no_other_run_installs_anything(checkout, why):
    if why == "worktree":
        (checkout / ".git").rmdir()
        (checkout / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")
    runs = Runs(helper_differs=1 if why == "helper not as merged" else 0)
    named = ["tests/test_x.py"] if why == "named tests" else []
    env = {} if why == "by hand" else {e2e.ENV_FLAG: "1"}
    assert e2e.main(named, runner=runs, env=env) == 0
    assert e2e.INSTALL_ARGV not in runs.calls


def test_the_ci_tier_runs_its_marker_and_never_installs(checkout):
    """A runner's checkout has a `.git` directory and runs under `DEVKIT_E2E`, the two
    things the nightly install looks for: `--ci` alone must keep it from deploying."""
    runs = Runs()
    assert e2e.main(["--ci"], runner=runs, env={e2e.ENV_FLAG: "1"}) == 0
    assert e2e.INSTALL_ARGV not in runs.calls
    assert not [call for call in runs.calls if call[0] == "git"]
    pytest_call = runs.calls[0]
    assert pytest_call[pytest_call.index("ci_e2e") - 1] == "-m"
    assert "--ci" not in pytest_call


def test_installs_merged_is_false_for_the_ci_tier(checkout):
    runs = Runs()
    assert not e2e.installs_merged(checkout, [], True, runs, marker=e2e.CI_MARKER)
    assert runs.calls == []


def test_the_ci_tier_keeps_the_contract(checkout, capsys):
    def failing(argv, **_kwargs):
        write_report(
            e2e.REPORT,
            '<testcase classname="tests.test_x" name="test_ok"/>'
            '<testcase classname="tests.test_x" name="test_s"><skipped message="no csc"/></testcase>',
        )
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    assert e2e.main(["--ci"], runner=failing, env={e2e.ENV_FLAG: "1"}) == 1
    assert "FAILED tests/test_x.py::test_s - skipped: no csc" in capsys.readouterr().out

    def empty(argv, **_kwargs):
        write_report(e2e.REPORT, "")
        return subprocess.CompletedProcess(argv, 5, stdout="", stderr="")

    assert e2e.main(["--ci"], runner=empty, env={e2e.ENV_FLAG: "1"}) == 5
    assert "no ci_e2e test executed" in capsys.readouterr().out


def test_main_runs_pytest_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(e2e, "REPORT", tmp_path / "logs" / "e2e-junit.xml")
    monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

    def fake_run(argv, **_kwargs):
        write_report(e2e.REPORT, '<testcase classname="tests.test_deploy" name="test_compiles"/>')
        return subprocess.CompletedProcess(argv, 0, stdout="1 passed\n", stderr="")

    assert e2e.main([], runner=fake_run, env={}) == 0
    out = capsys.readouterr().out
    assert "1 passed" in out and "e2e: report in logs/e2e-junit.xml" in out
