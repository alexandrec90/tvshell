"""CLAUDE.md states facts about this code; hold it to them.

An instruction file is read as authority, so a stale command or path in it sends the next
agent somewhere that no longer exists. These are the checkable claims.
"""

import importlib
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DOC = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
HELPER = (REPO / "helper" / "TvShell.cs").read_text(encoding="utf-8")

MODULES = sorted(set(re.findall(r"python -m (tvshell\.\w+)", DOC)))
PATHS = sorted(
    {
        p.rstrip("/")
        for p in re.findall(r"`((?:helper|scripts|tests|tvshell)/[\w./-]+|TODO\.md)`", DOC)
    }
)
CSHARP_NAMES = sorted(set(re.findall(r"`(Ensure\w+)`", DOC)))


def test_the_doc_makes_checkable_claims():
    # Guards the regexes: if they stop matching, every parametrised test below vanishes.
    assert MODULES and PATHS and CSHARP_NAMES


@pytest.mark.parametrize("module", MODULES)
def test_every_documented_command_has_a_main(module):
    assert callable(getattr(importlib.import_module(module), "main", None))


@pytest.mark.parametrize("path", PATHS)
def test_every_cited_path_exists(path):
    assert (REPO / path).exists()


@pytest.mark.parametrize("name", CSHARP_NAMES)
def test_every_cited_helper_method_exists(name):
    assert re.search(rf"\b{name}\s*\(", HELPER)


def test_install_dir_claim_matches_code():
    from tvshell import tiles

    assert "`tiles.INSTALL_DIR` (`~/TvShell`)" in DOC
    assert tiles.INSTALL_DIR == Path.home() / "TvShell"
