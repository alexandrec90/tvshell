"""The trace: what the controller sent, what the TV showed, screenshots, marks, UDP ports.

Each part runs through a mode of the built exe: ``trace`` writes a line, ``pad-names``
names what a report holds, ``tv-view`` snapshots a screen area, ``tv-shot`` saves a
screenshot, ``mark`` keeps the evidence of a problem, and ``udp-owners`` counts UDP
endpoints per process. Files go to ``tmp_path``, never the TV's own folders.
"""

from __future__ import annotations

import re
import socket
import subprocess
from pathlib import Path

import pytest

COVERS = (
    "Trace.Write",
    "Trace.Cli",
    "PadNames.Of",
    "PadNames.Bits",
    "PadNames.Stick",
    "PadNames.IsMark",
    "PadNames.Cli",
    "TvView.Snapshot",
    "TvView.Who",
    "TvView.Picture",
    "TvView.Windows",
    "TvView.Cli",
    "Shots.Name",
    "Shots.Save",
    "Shots.Prune",
    "Shots.Cli",
    "Mark.Now",
    "Mark.CopyTail",
    "Mark.Cli",
    "Ports.UdpOwners",
    "Ports.Describe",
    "Ports.Cli",
    "mode:trace",
    "mode:pad-names",
    "mode:tv-view",
    "mode:tv-shot",
    "mode:mark",
    "mode:udp-owners",
)

USAGE = 254
LINE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3} \w+\s+")


def run(exe: Path, *args: str) -> tuple[int, str]:
    done = subprocess.run(  # noqa: S603 - our own build
        [str(exe), *args], check=False, capture_output=True, text=True, timeout=60
    )
    return done.returncode, done.stdout.strip()


# --- trace ---


def test_a_line_is_written_with_its_time_and_kind(exe, tmp_path):
    assert run(exe, "trace", str(tmp_path), "1000000", "press", "left", "alone")[0] == 0
    lines = (tmp_path / "trace.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert LINE.match(lines[0])
    assert lines[0].endswith("press left alone")


def test_the_trace_rotates_past_its_size(exe, tmp_path):
    for n in range(3):
        run(exe, "trace", str(tmp_path), "10", "pad", f"line {n}")
    assert (tmp_path / "trace.old.log").read_text(encoding="utf-8").strip().endswith("line 1")
    assert (tmp_path / "trace.log").read_text(encoding="utf-8").strip().endswith("line 2")


@pytest.mark.parametrize("args", [(), ("d",), ("d", "x", "k", "m"), ("d", "-1", "k", "m")])
def test_trace_usage(exe, args):
    assert run(exe, "trace", *args)[0] == USAGE


# --- what the controller holds ---


def full(right: int = 0, shared: int = 0, left: int = 0, sticks: bytes | None = None) -> str:
    """A 0x30 full report: timer, battery, the three button bytes, then the two sticks."""
    neutral = bytes([0, 0x08, 0x80, 0, 0x08, 0x80])
    return bytes([0x30, 0, 0x90, right, shared, left, *(sticks or neutral)]).hex()


def simple(face: int = 0, shared: int = 0, hat: int = 8) -> str:
    return bytes([0x3F, face, shared, hat]).hex()


NAMED, MARK, UNKNOWN = 0, 1, 2


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (full(), "-"),
        (full(right=0x08), "A"),
        (full(right=0x04 | 0x80), "B+ZR"),
        (full(shared=0x10), "Home"),  # Pad.Buttons masks Home off; the trace must not
        (full(shared=0x20), "Capture"),
        (full(left=0x02 | 0x40), "Up+L"),
        # left stick pushed left (x 0x100), right stick pushed up (y 0xF00)
        (full(sticks=bytes([0x00, 0x81, 0x80, 0x00, 0x08, 0xF0])), "LS-left+RS-up"),
        (simple(face=0x02), "A"),
        (simple(face=0x01, shared=0x10), "B+Home"),
        (simple(hat=0), "Up"),
        (simple(hat=3), "Down+Right"),
        (simple(), "-"),
    ],
)
def test_the_trace_names_what_is_held(exe, report, expected):
    assert run(exe, "pad-names", report) == (NAMED, expected)


@pytest.mark.parametrize(
    ("report", "expected"),
    [
        (full(shared=0x01 | 0x02), "Minus+Plus"),
        (simple(face=0x02, shared=0x03), "A+Minus+Plus"),
    ],
)
def test_minus_and_plus_together_is_the_mark(exe, report, expected):
    assert run(exe, "pad-names", report) == (MARK, expected)


def test_minus_alone_is_not_the_mark(exe):
    assert run(exe, "pad-names", full(shared=0x01))[0] == NAMED


def test_a_report_it_does_not_know(exe):
    assert run(exe, "pad-names", "21000000") == (UNKNOWN, "unknown")


@pytest.mark.parametrize("args", [(), ("zz",), ("3",), ("30", "30")])
def test_pad_names_usage(exe, args):
    assert run(exe, "pad-names", *args)[0] == USAGE


# --- what the TV shows ---


WINDOW = re.compile(r'^.+\[\d+\] 0x[0-9A-F]+ .* ".*" -?\d+,-?\d+ \d+x\d+( [\w,]+)?$')


def test_a_snapshot_names_focus_top_picture_and_windows(exe):
    # Whatever this desktop shows: the shape of the line, never a crash.
    code, out = run(exe, "tv-view", "0", "0", "640", "480")
    assert code == 0
    head, *windows = out.split(" || ")
    assert re.match(
        r"^focus .+; on top .+; picture (black|not black|unreadable); overlay process (running|not running)$",
        head,
    )
    assert windows
    assert windows == ["no windows"] or all(WINDOW.match(w) for w in windows)


def test_an_area_off_every_display_shows_no_windows(exe):
    code, out = run(exe, "tv-view", "-90000", "-90000", "100", "100")
    assert code == 0
    assert out.endswith("|| no windows")
    assert "picture unreadable" in out or "picture black" in out


@pytest.mark.parametrize("args", [(), ("1", "2", "3"), ("1", "2", "3", "x")])
def test_tv_view_usage(exe, args):
    assert run(exe, "tv-view", *args)[0] == USAGE


# --- screenshots ---


def test_a_screenshot_is_a_half_size_jpeg_named_by_time(exe, tmp_path):
    code, out = run(exe, "tv-shot", str(tmp_path), "10", "0", "0", "320", "240", "press")
    if code == 4:
        pytest.fail("the screen could not be read on this runner")
    assert code == 0
    shot = Path(out)
    assert shot.parent == tmp_path
    assert re.match(r"^\d{8}-\d{6}-\d{3}-press\.jpg$", shot.name)
    data = shot.read_bytes()
    assert data[:2] == b"\xff\xd8"  # JPEG
    # SOF0 carries height then width: half of 240x320
    sof = data.index(b"\xff\xc0")
    assert (int.from_bytes(data[sof + 5 : sof + 7], "big"), int.from_bytes(data[sof + 7 : sof + 9], "big")) == (120, 160)


def test_only_the_newest_screenshots_are_kept(exe, tmp_path):
    for name in ("20200101-000000-000-old.jpg", "20200101-000001-000-older.jpg"):
        (tmp_path / name).write_bytes(b"x")
    code, out = run(exe, "tv-shot", str(tmp_path), "1", "0", "0", "64", "64", "new")
    assert code == 0
    assert [p.name for p in tmp_path.glob("*.jpg")] == [Path(out).name]


def test_a_why_is_made_safe_for_a_file_name(exe, tmp_path):
    code, out = run(exe, "tv-shot", str(tmp_path), "5", "0", "0", "64", "64", "a b/c")
    assert code == 0
    assert Path(out).name.endswith("-abc.jpg")


def test_an_empty_area_is_not_a_screenshot(exe, tmp_path):
    assert run(exe, "tv-shot", str(tmp_path), "5", "0", "0", "0", "0", "x")[0] == 4


@pytest.mark.parametrize("args", [(), ("d", "5", "0", "0", "64", "64"), ("d", "x", "0", "0", "64", "64", "w")])
def test_tv_shot_usage(exe, args):
    assert run(exe, "tv-shot", *args)[0] == USAGE


# --- marks ---


def test_a_mark_keeps_the_tails_and_the_last_screenshots(exe, tmp_path):
    shots = tmp_path / "shots"
    shots.mkdir()
    for n in range(5):
        (shots / f"2020010{n}-000000-000-press.jpg").write_bytes(b"x")
    trace = tmp_path / "trace.log"
    trace.write_bytes(b"old stuff\n" + b"x" * 100 + b"\nthe end\n")
    code, out = run(
        exe, "mark", str(tmp_path / "marks"), str(shots), "3", "8",
        "0", "0", "0", "0", "Minus and Plus", str(trace), str(tmp_path / "missing.log"),
    )  # fmt: skip
    assert code == 0
    mark = Path(out)
    assert mark.parent == tmp_path / "marks"
    assert (mark / "why.txt").read_text(encoding="utf-8").strip().endswith("Minus and Plus")
    assert (mark / "trace.log").read_bytes() == b"the end\n"
    assert not (mark / "missing.log").exists()
    assert sorted(p.name for p in mark.glob("*.jpg")) == [f"2020010{n}-000000-000-press.jpg" for n in (2, 3, 4)]


def test_a_mark_takes_a_screenshot_of_the_tv_first(exe, tmp_path):
    shots = tmp_path / "shots"
    code, out = run(exe, "mark", str(tmp_path / "marks"), str(shots), "5", "100", "0", "0", "64", "64", "tray")
    assert code == 0
    assert [p.name.endswith("-mark.jpg") for p in Path(out).glob("*.jpg")] == [True]


@pytest.mark.parametrize("args", [(), ("m", "s", "x", "8", "0", "0", "0", "0", "why"), ("m", "s", "3", "8", "0", "0", "w", "0", "why")])
def test_mark_usage(exe, args):
    assert run(exe, "mark", *args)[0] == USAGE


# --- UDP ports ---


def test_udp_owners_counts_this_process_socket(exe):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as held:
        held.bind(("127.0.0.1", 0))
        code, out = run(exe, "udp-owners")
    assert code == 0
    total = re.match(r"^(\d+) UDP endpoints: (.*)$", out)
    assert total
    assert int(total.group(1)) >= 1
    assert all(re.match(r"^.+\[\d+\] \d+$", part) for part in total.group(2).split(", ") if part)
