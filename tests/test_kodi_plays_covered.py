"""Kodi's video keeps playing behind the game (the owner's choice, 2026-11-25).

The helper can stop a TV app's video once it has been covered for a minute
(``StopVideoWhenCovered``), because Kodi 21.3 can crash rendering video it cannot show. For
Kodi that cut off programmes listened to behind the game, so it is off. The setting is a field
in the helper's list of TV apps, which no mode of the exe reads, so this reads the source.
"""

from __future__ import annotations

import re
from pathlib import Path

HELPER = Path(__file__).resolve().parent.parent / "helper" / "TvShell.cs"


def tv_app_flag(source: str, name: str, flag: str) -> str | None:
    """The value ``flag`` is set to in the ``new TvApp { Name = "<name>", ... }`` entry. Pure."""
    entry = re.search(r'new TvApp \{ Name = "' + re.escape(name) + r'"[^}]*\}', source)
    if entry is None:
        return None
    value = re.search(r"\b" + re.escape(flag) + r"\s*=\s*(\w+)", entry.group(0))
    return value.group(1) if value else None


def test_kodi_video_is_not_stopped_behind_the_game():
    source = HELPER.read_text(encoding="utf-8")
    assert tv_app_flag(source, "Kodi", "StopVideoWhenCovered") == "false"


def test_tv_app_flag_reads_one_entry():
    source = (
        'new TvApp { Name = "Kodi", Borderless = true, StopVideoWhenCovered = true },\n'
        'new TvApp { Name = "Other", StopVideoWhenCovered = false },'
    )
    assert tv_app_flag(source, "Kodi", "StopVideoWhenCovered") == "true"
    assert tv_app_flag(source, "Other", "StopVideoWhenCovered") == "false"
    assert tv_app_flag(source, "Other", "Borderless") is None
    assert tv_app_flag(source, "Missing", "Borderless") is None
