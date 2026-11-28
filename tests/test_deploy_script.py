"""`scripts/deploy.py`: devkit's install contract, run on every fire, idle or not."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "deploy.py"
spec = importlib.util.spec_from_file_location("deploy_script", SCRIPT)
assert spec is not None and spec.loader is not None, f"cannot load {SCRIPT}"
deploy_script = importlib.util.module_from_spec(spec)
sys.modules["deploy_script"] = deploy_script
spec.loader.exec_module(deploy_script)


def test_it_installs_only_when_stale_and_never_drills_the_display():
    seen: list[list[str]] = []
    deploy_script.main(run=lambda argv: seen.append(argv) or 0, stale=lambda: False)
    assert seen == [["--if-stale", "--no-drill"]]


@pytest.mark.parametrize(
    ("deploy_code", "still_stale", "expected"),
    [
        (0, False, 0),
        (1, False, 0),  # installed, tests or verify red: e2e.py reports those
        (0, True, 1),  # said it was done, yet the TV runs other source
        (3, True, 3),
    ],
)
def test_red_means_the_merged_source_is_not_what_is_installed(
    deploy_code, still_stale, expected, capsys
):
    code = deploy_script.main(run=lambda _argv: deploy_code, stale=lambda: still_stale)
    assert code == expected
    out = capsys.readouterr().out
    if still_stale:
        assert "still not what is installed" in out
    elif deploy_code:
        assert "installed; its tests or verify are red" in out


def test_the_default_run_is_the_real_deploy():
    import inspect

    from tvshell import deploy

    default = inspect.signature(deploy_script.main).parameters["run"].default
    assert default is deploy.main
