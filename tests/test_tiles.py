from pathlib import Path

from tvshell import tiles


def test_windows_path_uses_backslashes():
    assert (
        tiles.windows_path(Path("C:/Program Files/Kodi/kodi.exe"))
        == "C:\\Program Files\\Kodi\\kodi.exe"
    )


def test_every_tile_launches_the_installed_helper(tmp_path):
    result = tiles.tiles(tmp_path)
    exe = '"' + tiles.windows_path(tmp_path / "TvShell.exe") + '"'
    assert {t.exe for t in result} == {exe}
    assert {t.start_dir for t in result} == {'"' + tiles.windows_path(tmp_path) + '\\"'}


def test_without_game_cfg_only_the_tv_tiles(tmp_path):
    result = tiles.tiles(tmp_path)
    assert [t.options for t in result] == [spec[2] for spec in tiles.TILE_SPECS]
    assert len({t.name for t in result}) == len(result)


def test_game_cfg_adds_a_launch_tile_first(tmp_path):
    (tmp_path / "game.cfg").write_text(
        "# local\nname=Some Game\nprocess=somegame\nicon=C:/Games/somegame.exe\n", encoding="utf-8"
    )
    first = tiles.tiles(tmp_path)[0]
    assert (first.name, first.options, first.icon) == (
        "Some Game",
        "launch",
        "C:\\Games\\somegame.exe",
    )


def test_game_cfg_without_process_adds_no_tile(tmp_path):
    (tmp_path / "game.cfg").write_text("name=Some Game\n", encoding="utf-8")
    assert tiles.game_spec(tmp_path) is None


def test_game_tile_falls_back_to_helper_icon(tmp_path):
    (tmp_path / "game.cfg").write_text("name=G\nprocess=g\n", encoding="utf-8")
    assert tiles.game_spec(tmp_path) == ("G", tmp_path / "TvShell.exe", "launch")


def test_parse_cfg_matches_the_helpers_rules():
    text = '# comment\n\n Name = Some Game \nLAUNCH_ARGS=--exec="go x=1"\n=nokey\nnoequals\n'
    assert tiles.parse_cfg(text) == {"name": "Some Game", "launch_args": '--exec="go x=1"'}


def test_default_install_dir_is_under_home():
    assert tiles.INSTALL_DIR == Path.home() / "TvShell"
