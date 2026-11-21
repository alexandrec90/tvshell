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


def test_every_tile_mode_is_a_launcher_the_deploy_spares():
    # deploy kills running helpers but must spare a launcher (a show in progress); a tile
    # whose mode it does not know would be killed mid-show.
    from tvshell import deploy

    assert {spec[2].split()[0] for spec in tiles.TILE_SPECS} <= set(deploy.LAUNCHER_ARGS)


def test_every_tile_mode_is_handled_by_the_helper(tmp_path):
    helper = (Path(__file__).resolve().parent.parent / "helper" / "TvShell.cs").read_text(
        encoding="utf-8"
    )
    app = tmp_path / tiles.BACKWHEN_APP_DIR / "BackWhenTV-Portable-1.0.0.exe"
    app.parent.mkdir()
    app.write_bytes(b"")
    (tmp_path / "game.cfg").write_text("name=G\nprocess=g\n", encoding="utf-8")
    from tvshell import deploy

    for tile in tiles.tiles(tmp_path):
        mode = tile.options.split()[0]
        assert f'mode == "{mode}"' in helper or f'Mode = "{mode}"' in helper, mode
        assert mode in deploy.LAUNCHER_ARGS, mode


def test_backwhen_app_tile_needs_its_exe(tmp_path):
    assert tiles.backwhen_app_spec(tmp_path) is None
    (tmp_path / tiles.BACKWHEN_APP_DIR).mkdir()
    (tmp_path / tiles.BACKWHEN_APP_DIR / "readme.txt").write_text("x", encoding="utf-8")
    assert tiles.backwhen_app_spec(tmp_path) is None
    assert "BackWhenTV App" not in {t.name for t in tiles.tiles(tmp_path)}


def test_backwhen_app_tile_comes_last_with_the_newest_exe_as_icon(tmp_path):
    import os

    folder = tmp_path / tiles.BACKWHEN_APP_DIR
    folder.mkdir()
    old, new = folder / "BackWhenTV-Portable-1.0.0.exe", folder / "BackWhenTV-Portable-1.1.0.exe"
    old.write_bytes(b"")
    new.write_bytes(b"")
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    last = tiles.tiles(tmp_path)[-1]
    assert (last.name, last.options, last.icon) == (
        "BackWhenTV App",
        "backwhen-app",
        tiles.windows_path(new),
    )


def test_retired_tiles_are_no_longer_installed(tmp_path):
    folder = tmp_path / tiles.BACKWHEN_APP_DIR
    folder.mkdir()
    (folder / "BackWhenTV-Portable-1.1.0.exe").write_bytes(b"")
    names = {t.name for t in tiles.tiles(tmp_path)}
    assert "BackWhenTV" in tiles.RETIRED_TILES
    assert not names & set(tiles.RETIRED_TILES)


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
