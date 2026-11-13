import sqlite3
from contextlib import closing

import pytest

from tvshell import slyguy


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "settings.db"
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            "CREATE TABLE settings (addon_id TEXT, key TEXT, value TEXT, PRIMARY KEY (addon_id, key))"
        )
    return path


def test_set_stores_json_and_replaces(db):
    slyguy.set_value(db, "max_bandwidth", 1)
    slyguy.set_value(db, "max_bandwidth", 0.7)
    assert slyguy.list_values(db) == {"max_bandwidth": 0.7}
    with closing(sqlite3.connect(db)) as conn:
        assert conn.execute("SELECT value FROM settings").fetchall() == [("0.7",)]


def test_settings_are_per_addon(db):
    slyguy.set_value(db, "region", "ca", addon_id="slyguy.pluto.tv.provider")
    assert slyguy.list_values(db) == {}
    assert slyguy.list_values(db, "slyguy.pluto.tv.provider") == {"region": "ca"}


def test_delete_reports_whether_key_existed(db):
    slyguy.set_value(db, "max_height", 480)
    assert slyguy.delete_value(db, "max_height")
    assert not slyguy.delete_value(db, "max_height")


@pytest.mark.parametrize(
    ("text", "value"), [("0.7", 0.7), ("true", True), ("480", 480), ("ca", "ca")]
)
def test_parse_value(text, value):
    assert slyguy.parse_value(text) == value


def test_main_set_and_list(db, capsys):
    assert slyguy.main(["--db", str(db), "set", "max_bandwidth", "0.7"]) == 0
    assert '"max_bandwidth": 0.7' in capsys.readouterr().out


def test_main_delete_missing_key(db, capsys):
    assert slyguy.main(["--db", str(db), "delete", "nope"]) == 0
    assert "nope was not set" in capsys.readouterr().out


def test_main_fails_without_database(tmp_path, capsys):
    assert slyguy.main(["--db", str(tmp_path / "missing.db"), "list"]) == 1
    assert "no SlyGuy settings database" in capsys.readouterr().err


def test_default_db_is_under_appdata(monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    assert (
        slyguy.default_db()
        == tmp_path / "Kodi/userdata/addon_data/script.module.slyguy/settings.db"
    )
