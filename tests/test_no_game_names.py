"""The repository names no game, publisher or game store (see CLAUDE.md).

The game this project can launch is configured locally in `game.cfg`, outside the
repository. This guard fails if a name slips into code, tests or docs. The names are kept
as SHA-256 prefixes so that this file does not itself contain them.
"""

import hashlib
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# sha256(word)[:16] for each word the repository must not contain, lower-cased. These are
# hashes of ordinary words, not credentials, hence the secret-scanner allowlist pragmas.
FORBIDDEN = {
    "3d67304cf9b151d4",  # pragma: allowlist secret
    "8c649a8f85f3d89d",  # pragma: allowlist secret
    "793da17aec26d53f",  # pragma: allowlist secret
    "0e5e4b62e81cd569",  # pragma: allowlist secret
    "bc070508bac240c5",  # pragma: allowlist secret
}


def digest(word: str) -> str:
    return hashlib.sha256(word.encode("utf-8")).hexdigest()[:16]


def words(text: str) -> set[str]:
    """Every word, both whole (``name.ext``) and split on dots, lower-cased."""
    found = set()
    for token in re.findall(r"[a-z0-9][a-z0-9.]*", text.lower()):
        token = token.strip(".")
        found.add(token)
        found.update(part for part in token.split(".") if part)
    return found


def repo_files() -> list[Path]:
    cmd = ["git", "ls-files", "--cached", "--others", "--exclude-standard"]
    done = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, check=True)  # noqa: S603 - fixed git command
    listed = done.stdout.splitlines()
    return [REPO / name for name in listed if (REPO / name).is_file()]


def offenders(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []  # binary or unreadable: not prose or code
    return sorted(w for w in words(text) if digest(w) in FORBIDDEN)


def test_digest_and_words_catch_a_dotted_name():
    assert digest("abc") == hashlib.sha256(b"abc").hexdigest()[:16]
    assert {"game.exe", "game", "exe"} <= words("Run GAME.exe now.")


def test_guard_detects_a_forbidden_word(tmp_path, monkeypatch):
    sample = tmp_path / "sample.txt"
    sample.write_text("Launch Zebra.exe through its store.", encoding="utf-8")
    assert offenders(sample) == []
    monkeypatch.setattr(__import__(__name__), "FORBIDDEN", FORBIDDEN | {digest("zebra")})
    assert offenders(sample) == ["zebra"]


def test_guard_skips_binary_files(tmp_path):
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"\xff\xfe\x00\x80")
    assert offenders(blob) == []


def main(argv: list[str]) -> int:
    """The commit hook: report each named file that names a game, exit 1 if any does.

    Delivery is direct (merge first, gate after), so the gate alone finds a name only
    once it is on main; the hook stops it at the commit. Stdlib only, like any hook.
    """
    bad = 0
    for name in argv:
        hits = offenders(Path(name))
        if hits:
            bad += 1
            print(f"{name}: names a game, publisher or store ({len(hits)} word(s)); see CLAUDE.md")
    return 1 if bad else 0


def test_main_reports_a_file_that_names_a_game(tmp_path, monkeypatch, capsys):
    clean = tmp_path / "clean.txt"
    clean.write_text("Launch it through the store launcher.", encoding="utf-8")
    dirty = tmp_path / "dirty.txt"
    dirty.write_text("Launch Zebra.exe.", encoding="utf-8")
    monkeypatch.setattr(__import__(__name__), "FORBIDDEN", FORBIDDEN | {digest("zebra")})
    assert main([str(clean)]) == 0
    assert main([str(clean), str(dirty)]) == 1
    out = capsys.readouterr().out
    assert "dirty.txt" in out
    assert "clean.txt" not in out
    assert "zebra" not in out.lower()  # the hook does not print the name either


def test_main_with_no_files_passes():
    assert main([]) == 0


def test_no_file_in_the_repository_names_a_game():
    found = {str(p.relative_to(REPO)): offenders(p) for p in repo_files()}
    assert not {path: hits for path, hits in found.items() if hits}


if __name__ == "__main__":
    import sys

    sys.exit(main(sys.argv[1:]))
