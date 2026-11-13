import struct

import pytest

from tvshell import steam_shortcuts as ss
from tvshell.tiles import Tile

TILE = Tile("Live TV", '"C:\\x\\TvShell.exe"', '"C:\\x\\"', "C:\\Kodi\\kodi.exe", "kodi tv")


def vdf_with(*entries: dict) -> bytes:
    return ss.dump({"shortcuts": {str(i): e for i, e in enumerate(entries)}})


def test_dump_then_parse_round_trips_every_type():
    values = {"shortcuts": {"0": {"appid": 0xFFFFFFFF, "AppName": "Ü tv", "tags": {}}}}
    parsed, end = ss.parse(ss.dump(values))
    assert parsed == values
    assert end == len(ss.dump(values))


def test_dump_encodes_int32_little_endian_after_type_and_key():
    assert ss.dump({"n": 1}) == b"\x02n\x00" + struct.pack("<I", 1) + b"\x08"


def test_parse_rejects_unknown_type():
    with pytest.raises(ValueError, match="unsupported KeyValues type 7"):
        ss.parse(b"\x07k\x00")


def test_dump_rejects_unsupported_value():
    with pytest.raises(TypeError, match="'k'"):
        ss.dump({"k": 1.5})


def test_appid_has_high_bit_set_and_is_stable():
    appid = ss.shortcut_appid('"C:\\a.exe"', "Live TV")
    assert appid & 0x80000000
    assert appid == ss.shortcut_appid('"C:\\a.exe"', "Live TV")
    assert appid != ss.shortcut_appid('"C:\\a.exe"', "Radio")


def test_game_id_matches_steam_rungameid_format():
    # The Live TV tile's real ids on the install machine.
    assert ss.game_id(3104449094) == 13333507330860384256


def test_upsert_adds_new_tile_with_defaults():
    shortcuts: dict = {}
    entry, added = ss.upsert(shortcuts, TILE)
    assert added
    assert shortcuts == {"0": entry}
    assert entry["LaunchOptions"] == "kodi tv"
    assert entry["AllowOverlay"] == 1


def test_upsert_updates_existing_tile_and_keeps_its_appid():
    old = ss.new_entry(Tile("Live TV", '"C:\\Kodi\\kodi.exe"', "", "", ""))
    old["LastPlayTime"] = 123
    shortcuts = {"0": old}
    entry, added = ss.upsert(shortcuts, TILE)
    assert not added
    assert entry is old
    assert entry["appid"] == ss.shortcut_appid('"C:\\Kodi\\kodi.exe"', "Live TV")
    assert entry["Exe"] == TILE.exe
    assert entry["LastPlayTime"] == 123


def test_apply_leaves_other_entries_and_trailing_bytes_untouched():
    other = ss.new_entry(Tile("Some Game", "game.exe", "", "", ""))
    data = vdf_with(other) + b"\x08"
    new, report = ss.apply(data, [TILE])
    top, end = ss.parse(new)
    assert top["shortcuts"]["0"] == other
    assert top["shortcuts"]["1"]["AppName"] == "Live TV"
    assert new[end:] == b"\x08"
    assert report[0].startswith("added Live TV: appid ")


def test_apply_creates_shortcuts_map_when_missing():
    new, _ = ss.apply(ss.dump({}), [TILE])
    assert ss.parse(new)[0]["shortcuts"]["0"]["AppName"] == "Live TV"


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    vdf = tmp_path / "shortcuts.vdf"
    vdf.write_bytes(vdf_with())
    assert ss.main(["--vdf", str(vdf), "--dry-run"]) == 0
    assert vdf.read_bytes() == vdf_with()
    assert "added Live TV" in capsys.readouterr().out


def test_main_writes_backup_and_new_file(tmp_path):
    vdf = tmp_path / "shortcuts.vdf"
    vdf.write_bytes(vdf_with())
    assert ss.main(["--vdf", str(vdf)]) == 0
    assert (tmp_path / "shortcuts.vdf.bak-tvshell").read_bytes() == vdf_with()
    names = [e["AppName"] for e in ss.parse(vdf.read_bytes())[0]["shortcuts"].values()]
    assert "Live TV" in names and "YouTube" in names


def test_main_creates_file_when_absent(tmp_path):
    vdf = tmp_path / "shortcuts.vdf"
    assert ss.main(["--vdf", str(vdf)]) == 0
    assert vdf.exists()
    assert not (tmp_path / "shortcuts.vdf.bak-tvshell").exists()
