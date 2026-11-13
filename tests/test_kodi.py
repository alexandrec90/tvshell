import json
import socket
import struct
import threading

import pytest

from tvshell import kodi


def test_event_packet_header_is_32_bytes_with_big_endian_fields():
    packet = kodi.event_packet(kodi.PT_ACTION, b"abc", uid=4242)
    assert packet[:4] == b"XBMC"
    assert packet[4:6] == bytes([2, 0])
    packet_type, seq, max_seq, size, uid = struct.unpack(">HIIHI", packet[6:22])
    assert (packet_type, seq, max_seq, size, uid) == (kodi.PT_ACTION, 1, 1, 3, 4242)
    assert packet[22:32] == b"\0" * 10
    assert packet[32:] == b"abc"


def test_builtin_packet_carries_action_type_and_nul_terminated_command():
    packet = kodi.builtin_packet("PlayerControl(Stop)")
    assert packet[32] == kodi.ACTION_BUILTIN
    assert packet[33:] == b"PlayerControl(Stop)\0"


def test_helo_packet_payload_is_name_icon_port_reserved():
    packet = kodi.helo_packet("tv")
    assert packet[32:] == b"tv\0" + bytes([0]) + b"\0" * 10


def test_first_reply_skips_notifications():
    stream = '{"jsonrpc":"2.0","method":"Player.OnPlay","params":{}}{"id":1,"result":"OK"}'
    assert json.loads(kodi.first_reply(stream, 1)) == {"id": 1, "result": "OK"}


def test_first_reply_ignores_braces_and_escaped_quotes_inside_strings():
    stream = '{"id":1,"result":"a } { \\" quote"}'
    assert json.loads(kodi.first_reply(stream, 1))["result"] == 'a } { " quote'


def test_first_reply_returns_none_for_incomplete_object():
    assert kodi.first_reply('{"id":1,"result":{"x"', 1) is None


def test_first_reply_returns_none_when_id_differs():
    assert kodi.first_reply('{"id":2,"result":1}', 1) is None


class FakeKodi:
    """Records calls and answers GUI.GetProperties from a scripted list of states."""

    def __init__(self, states):
        self.states = list(states)
        self.calls = []

    def __call__(self, method, params=None):
        self.calls.append(method)
        if method == "GUI.GetProperties":
            window, label = self.states.pop(0)
            return {"result": {"currentwindow": {"id": window}, "currentcontrol": {"label": label}}}
        return {"result": "OK"}


def test_confirm_yes_moves_left_then_selects():
    fake = FakeKodi([(kodi.CONFIRM_DIALOG, "No"), (kodi.CONFIRM_DIALOG, "Yes")])
    assert kodi.confirm_yes(fake)
    assert fake.calls == ["GUI.GetProperties", "Input.Left", "GUI.GetProperties", "Input.Select"]


def test_confirm_yes_selects_immediately_when_already_on_yes():
    fake = FakeKodi([(kodi.CONFIRM_DIALOG, "Yes")])
    assert kodi.confirm_yes(fake)
    assert fake.calls == ["GUI.GetProperties", "Input.Select"]


def test_confirm_yes_does_nothing_without_a_dialog():
    fake = FakeKodi([(10000, "Movies")])
    assert not kodi.confirm_yes(fake)
    assert fake.calls == ["GUI.GetProperties"]


def test_confirm_yes_refuses_when_yes_cannot_be_focused():
    fake = FakeKodi([(kodi.CONFIRM_DIALOG, "No"), (kodi.CONFIRM_DIALOG, "No")])
    assert not kodi.confirm_yes(fake)
    assert "Input.Select" not in fake.calls


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ({"result": {"addon": {"enabled": True}}}, True),
        ({"result": {"addon": {"enabled": False}}}, False),
        ({"error": {"message": "Invalid params."}}, False),
    ],
)
def test_addon_enabled(reply, expected):
    assert kodi.addon_enabled("x", lambda *_: reply) is expected


def test_json_objects_splits_concatenated_objects():
    assert list(kodi.json_objects('{"a":1}{"b":{"c":2}} {"d":"}"}')) == [
        '{"a":1}',
        '{"b":{"c":2}}',
        '{"d":"}"}',
    ]


def test_gui_state_reads_window_and_focus():
    assert kodi.gui_state(FakeKodi([(10000, "Movies")])) == (10000, "Movies")


def test_rpc_returns_reply_after_a_notification(monkeypatch):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        monkeypatch.setattr(kodi, "RPC_PORT", server.getsockname()[1])
        received = {}

        def serve():
            conn, _ = server.accept()
            with conn:
                received["request"] = json.loads(conn.recv(65536))
                conn.sendall(b'{"jsonrpc":"2.0","method":"GUI.OnScreensaverActivated","params":{}}')
                conn.sendall(b'{"id":1,"jsonrpc":"2.0","result":{"version":{"major":13}}}')

        thread = threading.Thread(target=serve)
        thread.start()
        reply = kodi.rpc("JSONRPC.Version", timeout=5)
        thread.join()
    assert reply["result"]["version"]["major"] == 13
    assert received["request"] == {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "JSONRPC.Version",
        "params": {},
    }


def test_rpc_raises_when_connection_closes_without_reply(monkeypatch):
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        monkeypatch.setattr(kodi, "RPC_PORT", server.getsockname()[1])
        thread = threading.Thread(target=lambda: server.accept()[0].close())
        thread.start()
        # Windows reports the closed socket as ConnectionAbortedError, POSIX as an empty
        # read (-> TimeoutError); both are the OSError `main` reports as "not reachable".
        with pytest.raises(OSError):
            kodi.rpc("Player.Stop", timeout=5)
        thread.join()


def test_builtin_sends_helo_then_action(monkeypatch):
    monkeypatch.setattr(kodi.time, "sleep", lambda _s: None)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
        server.bind(("127.0.0.1", 0))
        server.settimeout(5)
        monkeypatch.setattr(kodi, "EVENT_PORT", server.getsockname()[1])
        kodi.builtin("ActivateWindow(Home)")
        assert server.recv(1024) == kodi.helo_packet()
        assert server.recv(1024) == kodi.builtin_packet("ActivateWindow(Home)")


def test_install_addon_skips_when_already_installed(monkeypatch):
    monkeypatch.setattr(kodi, "addon_enabled", lambda _id: True)
    monkeypatch.setattr(kodi, "builtin", lambda _cmd: pytest.fail("must not reinstall"))
    assert kodi.install_addon("plugin.audio.cbcradio")


def test_install_addon_confirms_prompts_until_enabled(monkeypatch):
    states = iter([False, False, True])
    sent, confirmed = [], []
    monkeypatch.setattr(kodi, "addon_enabled", lambda _id: next(states))
    monkeypatch.setattr(kodi, "builtin", sent.append)
    monkeypatch.setattr(kodi, "confirm_yes", lambda: confirmed.append(1) or True)
    monkeypatch.setattr(kodi.time, "sleep", lambda _s: None)
    assert kodi.install_addon("plugin.audio.cbcradio")
    assert sent == ["InstallAddon(plugin.audio.cbcradio)"]
    assert len(confirmed) == 2


def test_install_addon_gives_up_at_timeout(monkeypatch):
    monkeypatch.setattr(kodi, "addon_enabled", lambda _id: False)
    monkeypatch.setattr(kodi, "builtin", lambda _cmd: None)
    monkeypatch.setattr(kodi, "confirm_yes", lambda: False)
    monkeypatch.setattr(kodi.time, "sleep", lambda _s: None)
    assert not kodi.install_addon("x", timeout=0)


def test_main_install_exit_codes(monkeypatch, capsys):
    monkeypatch.setattr(kodi, "install_addon", lambda addon_id: addon_id == "good")
    assert kodi.main(["install", "good"]) == 0
    assert kodi.main(["install", "bad"]) == 1
    assert "bad: NOT installed" in capsys.readouterr().out


def test_main_builtin_and_rpc(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr(kodi, "builtin", sent.append)
    monkeypatch.setattr(kodi, "rpc", lambda method, params: {"method": method, "params": params})
    assert kodi.main(["builtin", "Quit"]) == 0
    assert kodi.main(["rpc", "Player.Open", '{"item":{"channelid":146}}']) == 0
    assert sent == ["Quit"]
    assert '"channelid": 146' in capsys.readouterr().out


def test_main_reports_unreachable_kodi(monkeypatch, capsys):
    def refuse(*_args, **_kwargs):
        raise ConnectionRefusedError("refused")

    monkeypatch.setattr(kodi, "rpc", refuse)
    assert kodi.main(["rpc", "JSONRPC.Version"]) == 2
    assert "Kodi not reachable" in capsys.readouterr().err
