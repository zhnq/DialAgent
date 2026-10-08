#!/usr/bin/env python3
"""Return a Yealink Execute object for the resident DialAgent screen button."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import time


def execute_xml(uri: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        '<YealinkIPPhoneExecute>\n'
        f'<ExecuteItem URI="{uri}"/>\n'
        '</YealinkIPPhoneExecute>\n'
    ).encode("ascii")


def call_is_active(state_path: Path) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")).get("state")
    except (OSError, TypeError, ValueError):
        return False
    return state not in (None, "idle")


def write_hangup_request(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{time.time_ns()}\n", encoding="ascii")


class ActionServer(ThreadingHTTPServer):
    def __init__(self, address, expected_source: str, dial_target: str,
                 state_path: Path, hangup_request_path: Path | None = None):
        super().__init__(address, ActionHandler)
        self.expected_source = expected_source
        self.dial_target = ipaddress.IPv4Address(dial_target)
        self.state_path = state_path
        self.hangup_request_path = hangup_request_path

    def current_action(self) -> tuple[str, bytes]:
        active = call_is_active(self.state_path)
        if active and self.hangup_request_path is not None:
            write_hangup_request(self.hangup_request_path)
        uri = "Key:ON_HOOK" if active else f"Dial:{self.dial_target}"
        return uri, execute_xml(uri)


class ActionHandler(BaseHTTPRequestHandler):
    server: ActionServer

    def do_GET(self) -> None:
        if self.client_address[0] != self.server.expected_source:
            self.send_error(403)
            return
        if self.path.split("?", 1)[0] != "/dialagent-button":
            self.send_error(404)
            return
        uri, payload = self.server.current_action()
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=ISO-8859-1")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)
        print(
            f"DialAgent action from {self.client_address[0]}: returned {uri}",
            flush=True,
        )

    def log_message(self, _format: str, *_args) -> None:
        return


def private_ipv4(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if not address.is_private:
        raise argparse.ArgumentTypeError("must be a private IPv4 address")
    return str(address)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-ip", type=private_ipv4, required=True)
    parser.add_argument("--expected-source", type=private_ipv4, required=True)
    parser.add_argument("--dial-target", type=private_ipv4, required=True)
    parser.add_argument("--state-file", type=Path,
                        default=Path("outputs/t31g-face/state.json"))
    parser.add_argument("--hangup-request-file", type=Path,
                        help="notify the local SIP receiver when OK is pressed during a call")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")

    server = ActionServer(
        (args.bind_ip, args.port), args.expected_source, args.dial_target,
        args.state_file, args.hangup_request_file)
    print(
        f"DialAgent action ready: http://{args.bind_ip}:{args.port}/dialagent-button "
        f"({args.expected_source} only) -> Dial / local hangup toggle",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
