"""Kodi's covered video is stopped, its radio is not (``KodiLauncher.StopVideo``).

Run through ``stop-kodi-video <port>`` against a stand-in Kodi on a local port, so no real
Kodi is touched.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading

import pytest

COVERS = (
    "KodiLauncher.StopVideo",
    "KodiLauncher.StopVideoCli",
    "KodiLauncher.Rpc",
    "mode:stop-kodi-video",
)

STOPPED, NOT_STOPPED, USAGE = 1, 0, 254
VIDEO = {"playerid": 1, "playertype": "internal", "type": "video"}
AUDIO = {"playerid": 0, "playertype": "internal", "type": "audio"}
NOTICE = b'{"jsonrpc":"2.0","method":"Player.OnPause","params":{}}'


class FakeKodi:
    """Answers one JSON-RPC call per connection, as the helper makes them."""

    def __init__(self, players, stop_result="OK", notice=False):
        self.answers = {"Player.GetActivePlayers": players, "Player.Stop": stop_result}
        self.notice = notice
        self.calls: list[tuple[str, dict]] = []
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                call = json.loads(conn.recv(65536))
                self.calls.append((call["method"], call["params"]))
                if self.notice:
                    conn.sendall(NOTICE)
                reply = {"id": call["id"], "jsonrpc": "2.0", "result": self.answers[call["method"]]}
                conn.sendall(json.dumps(reply, separators=(",", ":")).encode())
                conn.recv(1)  # until the helper hangs up


def stop_video(exe, *args) -> int:
    done = subprocess.run([str(exe), "stop-kodi-video", *map(str, args)], check=False, timeout=60)  # noqa: S603 - our own build
    return done.returncode


@pytest.fixture
def fake():
    made = []

    def make(*args, **kwargs):
        made.append(FakeKodi(*args, **kwargs))
        return made[-1]

    yield make
    for one in made:
        one.server.close()


@pytest.mark.parametrize("players", [[VIDEO], [AUDIO, VIDEO]])
def test_a_playing_video_is_stopped(exe, fake, players):
    kodi = fake(players)
    assert stop_video(exe, kodi.port) == STOPPED
    assert kodi.calls[-1] == ("Player.Stop", {"playerid": 1})


@pytest.mark.parametrize("players", [[], [AUDIO]])
def test_radio_or_nothing_is_left_alone(exe, fake, players):
    kodi = fake(players)
    assert stop_video(exe, kodi.port) == NOT_STOPPED
    assert [method for method, _ in kodi.calls] == ["Player.GetActivePlayers"]


def test_a_notification_before_the_answer_is_skipped(exe, fake):
    assert stop_video(exe, fake([VIDEO], notice=True).port) == STOPPED


def test_a_refused_stop_is_not_reported_as_stopped(exe, fake):
    assert stop_video(exe, fake([VIDEO], stop_result="Failed").port) == NOT_STOPPED


def test_no_kodi_is_not_stopped(exe):
    with socket.create_server(("127.0.0.1", 0)) as server:
        port = server.getsockname()[1]
    assert stop_video(exe, port) == NOT_STOPPED


@pytest.mark.parametrize("args", [(), ("x",), ("0",), ("70000",), ("1", "2")])
def test_a_malformed_call_is_a_usage_error(exe, args):
    assert stop_video(exe, *args) == USAGE
