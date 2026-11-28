#!/usr/bin/env python3
"""devkit's install contract: put the merged helper on the TV within the half hour.

devkit's `devkit-local-e2e` job runs this on every fire, whoever is using the PC, in a
detached tree at `origin/main`'s tip, once per tip (`deploy_merged.py` in devkit, and
the `scripts/deploy.py` section of `.claude/rules/local-e2e.md`). It is what makes a
merged fix reach the TV without waiting: the install `scripts/e2e.py` does after its
tests waits for an idle PC and for the checkout to be on `main`, and a fix for Kodi's
crashes once sat merged and uninstalled through a day of TV watching behind both.

`python -m tvshell.deploy --if-stale --no-drill`: nothing when the TV already runs this
source, and no display drill, since someone may be watching. Exit 0 once this source is
what is installed, red tests or verify included -- the deploy prints them and
`scripts/e2e.py` reports them -- and non-zero only when it is not.

Stdlib only, like the `tvshell` package it imports: the caller is the machine's Python.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tvshell import deploy  # noqa: E402 - after the path that makes it importable

ARGV = ["--if-stale", "--no-drill"]


def main(
    run: Callable[[list[str]], int] = deploy.main,
    stale: Callable[[], bool] = lambda: deploy.is_stale(deploy.INSTALL_DIR, deploy.SOURCE),
) -> int:
    code = run(list(ARGV))
    if stale():
        print("deploy: this source is still not what is installed", flush=True)
        return code or 1
    if code:
        print("deploy: installed; its tests or verify are red, as printed above", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
