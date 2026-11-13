"""Talk to the local Kodi: JSON-RPC (TCP 9090) and built-in commands (EventServer, UDP 9777).

JSON-RPC cannot run Kodi's built-in commands (``ActivateWindow``, ``InstallAddon``...),
so those go through the EventServer, Kodi's remote-control protocol. Both only listen
on this machine ("allow remote control by programs on this system").

    python -m tvshell.kodi rpc JSONRPC.Version
    python -m tvshell.kodi builtin "ActivateWindow(Music,plugin://plugin.audio.cbcradio/,return)"
    python -m tvshell.kodi install plugin.audio.radiobrowser
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from collections.abc import Callable

HOST = "127.0.0.1"
RPC_PORT = 9090
EVENT_PORT = 9777
CONFIRM_DIALOG = 10100  # Kodi's Yes/No dialog window id

PT_HELO, PT_ACTION = 0x01, 0x0A
ACTION_BUILTIN = 0x01
CLIENT_UID = 4242


def event_packet(packet_type: int, payload: bytes, uid: int = CLIENT_UID) -> bytes:
    """One single-part EventServer packet (protocol 2.0): a 32-byte header, then ``payload``."""
    header = b"XBMC" + bytes([2, 0]) + struct.pack(">HIIHI", packet_type, 1, 1, len(payload), uid)
    return header + b"\0" * (32 - len(header)) + payload


def helo_packet(name: str = "tvshell") -> bytes:
    """The HELO that registers this client before it may send actions."""
    # device name, icon type (none), port, two reserved fields
    return event_packet(PT_HELO, name.encode("utf-8") + b"\0" + bytes([0]) + b"\0" * 10)


def builtin_packet(command: str) -> bytes:
    """An ACTION packet that runs ``command`` as a Kodi built-in."""
    return event_packet(PT_ACTION, bytes([ACTION_BUILTIN]) + command.encode("utf-8") + b"\0")


def _after_string(stream: str, i: int) -> int:
    """Index just past the JSON string whose contents start at ``i`` (after the opening quote)."""
    while i < len(stream):
        if stream[i] == "\\":
            i += 2
            continue
        if stream[i] == '"':
            return i + 1
        i += 1
    return len(stream)


def json_objects(stream: str):
    """Yield each complete top-level JSON object in ``stream``, split on brace depth outside strings."""
    depth, start, i = 0, 0, 0
    while i < len(stream):
        ch = stream[i]
        if ch == '"':
            i = _after_string(stream, i + 1)
            continue
        if ch == "{":
            start = i if depth == 0 else start
            depth += 1
        elif ch == "}" and depth:
            depth -= 1
            if depth == 0:
                yield stream[start : i + 1]
        i += 1


def first_reply(stream: str, request_id: int) -> str | None:
    """The first complete JSON object in ``stream`` whose id is ``request_id``.

    Kodi interleaves notifications with replies on the same socket, so the objects without
    our id are skipped.
    """
    return next(
        (obj for obj in json_objects(stream) if json.loads(obj).get("id") == request_id), None
    )


def rpc(method: str, params: dict | None = None, timeout: float = 15.0) -> dict:
    """Call one JSON-RPC method; return the decoded reply.

    Raises OSError when Kodi is not up or closes the connection, and TimeoutError (an
    OSError) when no reply arrives in time.
    """
    request = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    with socket.create_connection((HOST, RPC_PORT), timeout=timeout) as sock:
        sock.sendall(json.dumps(request).encode("utf-8"))
        received = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = sock.recv(1 << 20)
            if not chunk:
                break
            received += chunk.decode("utf-8", errors="replace")
            reply = first_reply(received, 1)
            if reply is not None:
                return json.loads(reply)
    raise TimeoutError(f"no reply from Kodi to {method}")


def builtin(command: str) -> None:
    """Run a Kodi built-in command through the EventServer."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(helo_packet(), (HOST, EVENT_PORT))
        time.sleep(0.2)
        sock.sendto(builtin_packet(command), (HOST, EVENT_PORT))


def gui_state(call: Callable[..., dict] = rpc) -> tuple[int, str]:
    """The current window id and the label of the focused control."""
    result = call("GUI.GetProperties", {"properties": ["currentwindow", "currentcontrol"]})[
        "result"
    ]
    return result["currentwindow"]["id"], result["currentcontrol"]["label"]


def confirm_yes(call: Callable[..., dict] = rpc) -> bool:
    """If a Yes/No dialog is open, move to Yes and press it. Returns whether it did."""
    window, focus = gui_state(call)
    if window != CONFIRM_DIALOG:
        return False
    if focus != "Yes":
        call("Input.Left")
        window, focus = gui_state(call)
    if focus != "Yes":
        return False
    call("Input.Select")
    return True


def addon_enabled(addon_id: str, call: Callable[..., dict] = rpc) -> bool:
    reply = call("Addons.GetAddonDetails", {"addonid": addon_id, "properties": ["enabled"]})
    return bool(reply.get("result", {}).get("addon", {}).get("enabled"))


def install_addon(addon_id: str, timeout: float = 180.0) -> bool:
    """Install an add-on from Kodi's repositories, answering Kodi's confirmation prompts."""
    if addon_enabled(addon_id):
        return True
    builtin(f"InstallAddon({addon_id})")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(1.5)
        confirm_yes()
        if addon_enabled(addon_id):
            return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_rpc = sub.add_parser("rpc", help="call a JSON-RPC method")
    p_rpc.add_argument("method")
    p_rpc.add_argument("params", nargs="?", default="{}", help="JSON object")
    sub.add_parser("builtin", help="run a Kodi built-in").add_argument("text")
    sub.add_parser("install", help="install an add-on").add_argument("addon_id")
    args = parser.parse_args(argv)

    try:
        if args.command == "rpc":
            print(json.dumps(rpc(args.method, json.loads(args.params)), indent=1))
        elif args.command == "builtin":
            builtin(args.text)
        else:
            ok = install_addon(args.addon_id)
            print(f"{args.addon_id}: {'installed' if ok else 'NOT installed'}")
            return 0 if ok else 1
    except OSError as exc:
        print(f"Kodi not reachable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
