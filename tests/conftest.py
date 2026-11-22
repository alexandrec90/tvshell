"""Shared fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from tvshell import deploy


@pytest.fixture(scope="session")
def exe(tmp_path_factory) -> Path:
    """The helper, compiled from this checkout into a temporary directory (``local`` tests only)."""
    if deploy.find_csc() is None:
        pytest.fail("the .NET Framework C# compiler is not on this PC")
    out = tmp_path_factory.mktemp("tvshell")
    (out / "logs").mkdir()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(deploy, "REPO", out)  # the build log goes under REPO/logs
        deploy.build(out / "TvShell.exe")
    return out / "TvShell.exe"
