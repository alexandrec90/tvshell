"""Every function in the helper is claimed by a test that runs the helper, or is on a list
that may only shrink.

The helper (``helper/TvShell.cs``) is C#, which the Python ratchets do not see, so its
logic could grow with no test and nothing would say so. This is that check. It reads the
source as text, so it runs in the plain suite, on any machine, on every change:

- A test module that runs the built helper (it uses the ``exe`` fixture) declares what it
  exercises in a module-level ``COVERS`` tuple of ``Class.Method`` names and ``mode:<name>``
  entries (a mode is the exe's first argument).
- Every method and every mode in the helper must be in some ``COVERS``, or in
  ``tests/helper-untested.txt``: the window-and-focus code that was written before this
  check and still has no test.
- That file may only shrink. Its length is pinned here (``UNTESTED``), so adding a line
  to it means editing this test as well, in plain view of a review; a new function has to
  come with a test instead. An entry that has gained a test, or whose function is gone,
  must be deleted.

What it cannot do is judge the test: a ``COVERS`` entry is a claim. It makes forgetting
impossible, not cheating.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "helper" / "TvShell.cs"
BASELINE = REPO / "tests" / "helper-untested.txt"

# The number of entries in tests/helper-untested.txt. Lower it when the list shrinks.
# It never goes up: a function that is new, or newly found untested, gets a test.
UNTESTED = 92

CLASS = re.compile(r"^(?:(?:static|sealed|public|internal|abstract)\s+)*class\s+(\w+)")
# A member at class level (four spaces): optional attribute, modifiers, a type, a name, "(".
METHOD = re.compile(
    r"^ {4}(?:\[[^\]]*\]\s*)?"
    r"(?:(?:public|private|internal|protected|static|override|virtual|new)\s+)*"
    r"(?!delegate\b|return\b|new\b|if\b|else\b|using\b|var\b|throw\b)"
    r"[A-Za-z_][\w.]*(?:<[^()=]*>)?(?:\[\])?\??\s+(\w+)\s*\("
)
MODE = re.compile(r'\bmode == "([\w-]+)"')


def helper_symbols(source: str) -> set[str]:
    """Every ``Class.Method`` the helper declares. Pure.

    Win32 imports (``extern``) are declarations of Windows' functions, not logic of ours.
    """
    found: set[str] = set()
    current = ""
    for line in source.splitlines():
        cls = CLASS.match(line)
        if cls:
            current = cls.group(1)
            continue
        method = METHOD.match(line)
        # `public Helper() {` reads as type "public", name "Helper": a constructor, skipped.
        if method and current and " extern " not in line and method.group(1) != current:
            found.add(f"{current}.{method.group(1)}")
    return found


def helper_modes(source: str) -> set[str]:
    """Every mode ``Program.Main`` dispatches on, as ``mode:<name>``. Pure."""
    return {f"mode:{name}" for name in MODE.findall(source)}


def uses_the_helper(tree: ast.Module) -> bool:
    """Whether a test module runs the built helper: something in it takes ``exe``."""
    return any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(arg.arg == "exe" for arg in node.args.args)
        for node in ast.walk(tree)
    )


def covers(tree: ast.Module) -> list[str] | None:
    """A test module's ``COVERS`` entries, or None when it declares none. Pure."""
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "COVERS" for t in node.targets
        ):
            return list(ast.literal_eval(node.value))
    return None


def read_baseline(text: str) -> list[str]:
    """The entries of the untested list: one per line, ``#`` comments and blanks apart. Pure."""
    return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


SOURCE = HELPER.read_text(encoding="utf-8")
DECLARED = helper_symbols(SOURCE) | helper_modes(SOURCE)
MODULES = {
    path.name: ast.parse(path.read_text(encoding="utf-8"))
    for path in sorted((REPO / "tests").glob("test_*.py"))
}
CLAIMS = {name: covers(tree) for name, tree in MODULES.items()}
CLAIMED = {entry for entries in CLAIMS.values() for entry in entries or ()}
LISTED = read_baseline(BASELINE.read_text(encoding="utf-8"))


def test_the_parser_finds_the_helper():
    # Guards the regexes: if they stop matching, every check below passes on nothing.
    assert {"TvDisplay.Pick", "DeskPrimary.Plan", "Program.Main", "Helper.Tick"} <= DECLARED
    assert {"mode:pick-tv", "mode:kodi", "mode:launch"} <= DECLARED
    assert len(DECLARED) > 100
    assert not any("extern" in name or name.startswith("N.Enum") for name in DECLARED)


def test_every_helper_function_and_mode_has_a_test_or_is_on_the_shrinking_list():
    unclaimed = sorted(DECLARED - CLAIMED - set(LISTED))
    assert not unclaimed, (
        "new in helper/TvShell.cs with no test: "
        + ", ".join(unclaimed)
        + ". Add a test that runs the helper and name these in its COVERS; "
        "tests/helper-untested.txt is not open to new entries."
    )


def test_the_untested_list_only_shrinks():
    assert len(LISTED) == UNTESTED, (
        f"tests/helper-untested.txt has {len(LISTED)} entries and UNTESTED says {UNTESTED}. "
        "Shorter: lower UNTESTED to match. Longer: the list takes no new entries, write the test."
    )


def test_the_untested_list_is_exact():
    assert LISTED == sorted(set(LISTED)), "keep tests/helper-untested.txt sorted, one entry once"
    gone = sorted(set(LISTED) - DECLARED)
    assert not gone, f"listed but no longer in the helper, drop these lines: {', '.join(gone)}"
    tested = sorted(set(LISTED) & CLAIMED)
    assert not tested, f"listed but now claimed by a test, drop these lines: {', '.join(tested)}"


def test_every_claim_names_something_in_the_helper():
    stale = sorted(CLAIMED - DECLARED)
    assert not stale, f"COVERS names what the helper does not have: {', '.join(stale)}"


@pytest.mark.parametrize("name", sorted(MODULES))
def test_a_module_claims_coverage_exactly_when_it_runs_the_helper(name):
    runs, claims = uses_the_helper(MODULES[name]), CLAIMS[name]
    if runs:
        assert claims, f"{name} runs the helper: say what it exercises in a COVERS tuple"
    else:
        assert claims is None, f"{name} never runs the helper, so its COVERS proves nothing"


def test_a_claimed_mode_is_actually_passed_to_the_exe():
    for name, claims in CLAIMS.items():
        text = (REPO / "tests" / name).read_text(encoding="utf-8")
        for claim in claims or ():
            if claim.startswith("mode:"):
                assert f'"{claim[5:]}"' in text, f"{name} claims {claim} but never passes it"


def test_helper_symbols_reads_methods_and_skips_imports():
    source = "\n".join(
        [
            "static class A {",
            "    public static int Pick(string[] ids) {",
            "    static readonly List<DateTime> seen = new List<DateTime>();",
            '    [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);',
            "    public delegate bool EnumProc(IntPtr h, IntPtr l);",
            "    static Dictionary<string, int> Count(string s) {",
            "    void Tick() {",
            "        var x = Other(1);",
            "}",
            "class B : Form {",
            "    public B() {",
            "    static string[] Names() { return null; }",
        ]
    )
    assert helper_symbols(source) == {"A.Pick", "A.Count", "A.Tick", "B.Names"}


def test_helper_modes_reads_the_dispatch():
    source = 'if (mode == "pick-tv") return 1;\nif (mode == "kodi") return 2;\nif (mode != "") x();'
    assert helper_modes(source) == {"mode:pick-tv", "mode:kodi"}


def test_covers_and_uses_the_helper():
    runs = ast.parse('COVERS = ("A.Pick", "mode:pick-tv")\ndef test_x(exe, tmp_path): pass\n')
    plain = ast.parse("def test_y(tmp_path): pass\n")
    assert covers(runs) == ["A.Pick", "mode:pick-tv"] and uses_the_helper(runs)
    assert covers(plain) is None and not uses_the_helper(plain)


def test_read_baseline_skips_comments_and_blanks():
    assert read_baseline("# why\n\nHelper.Tick\n  mode:kodi  \n") == ["Helper.Tick", "mode:kodi"]
