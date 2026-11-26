"""Shared fixtures."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tvshell import deploy


@pytest.fixture(scope="session")
def exe(tmp_path_factory) -> Path:
    """The helper under test, alone in a temporary directory (so no real ``game.cfg`` or
    ``tv.cfg`` sits beside it).

    Compiled from this checkout, or, when ``deploy.TEST_EXE_ENV`` names a build, a copy of
    that one: the deploy sets it so the tests judge the very binary it installs.
    """
    out = tmp_path_factory.mktemp("tvshell")
    given = os.environ.get(deploy.TEST_EXE_ENV)
    if given:
        if not Path(given).is_file():
            pytest.fail(f"{deploy.TEST_EXE_ENV} names {given}, which is not a file")
        shutil.copy2(given, out / "TvShell.exe")
        return out / "TvShell.exe"
    if deploy.find_csc() is None:
        pytest.fail("the .NET Framework C# compiler is not on this PC")
    (out / "logs").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deploy, "REPO", out)  # the build log goes under REPO/logs
        deploy.build(out / "TvShell.exe")
    return out / "TvShell.exe"


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items) -> None:
    """A test that runs the built helper is a ``helper`` test, whatever its file says.

    ``helper`` is what the deploy and the CI ``helper`` job select, so a test of the helper
    cannot be left out of them by forgetting a marker. It is ``ci_e2e`` too, which keeps it
    out of the plain suite (that one runs on Linux, where there is no C# compiler) and puts
    it in the CI end-to-end tier, which a ``windows-latest`` runner runs: building and
    running the helper needs Windows and csc.exe, nothing of this PC's. First, so the
    ``-m`` selection sees the markers.
    """
    for item in items:
        if "exe" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.helper)
            item.add_marker(pytest.mark.ci_e2e)
