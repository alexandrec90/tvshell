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
    assert argv[argv.index("local") - 1] == "-m"
    assert argv.index("local") > argv.index("pytest")
    assert "tests/test_deploy.py" in argv and "tests" not in argv
    assert argv[-1] == "--junitxml=r.xml"


def test_main_runs_pytest_and_reports(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(e2e, "REPORT", tmp_path / "logs" / "e2e-junit.xml")
    monkeypatch.setattr(e2e, "REPO_ROOT", tmp_path)

    def fake_run(argv, **_kwargs):
        write_report(e2e.REPORT, '<testcase classname="tests.test_deploy" name="test_compiles"/>')
        return subprocess.CompletedProcess(argv, 0, stdout="1 passed\n", stderr="")

    assert e2e.main([], runner=fake_run, env={}) == 0
    out = capsys.readouterr().out
    assert "1 passed" in out and "e2e: report in logs/e2e-junit.xml" in out
