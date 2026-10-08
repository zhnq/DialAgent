#!/usr/bin/env python3
"""Serve the 132x64 face as Yealink ImageScreen XML with inline DOB data.

The T31G XML Browser does not load the preview PNGs directly.  It expects the
legacy DOB payload as hexadecimal characters inside the Image element.  This
server renders from the same source as the browser preview, binds to one
explicit private address, and accepts only the selected phone plus localhost.
"""

from __future__ import annotations

import argparse
import html
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import threading
from urllib.parse import parse_qs, urlparse

from t31g_face_preview import FRAMES, HEIGHT, STATES, WIDTH, render


def dob_bytes(pixels: bytes | bytearray) -> bytes:
    """Encode two 4-bit grayscale pixels per byte for the XML Image body.

    Width and height belong in the Image element attributes.  Yealink's
    documented 8x12 example contains exactly 48 bytes (8 * 12 / 2), so the
    hexadecimal DOB body must not carry an additional dimensions prefix.
    """
    if len(pixels) != WIDTH * HEIGHT:
        raise ValueError("image size must be 132x64")
    levels = [15 - (pixel * 16 // 256) for pixel in pixels]
    body = bytes(levels[index] | (levels[index + 1] << 4)
                 for index in range(0, len(levels), 2))
    return body


def image_screen_xml(pixels: bytes | bytearray, refresh_url: str | None,
                     refresh_seconds: int = 2,
                     timeout_seconds: int = 0,
                     done_action: str | None = None,
                     softkeys: tuple[tuple[int, str, str], ...] = ()) -> bytes:
    if not 0 <= timeout_seconds <= 60:
        raise ValueError("timeout_seconds must be 0..60")
    refresh = ""
    if refresh_url:
        refresh = f' refresh="{refresh_seconds}" url="{refresh_url}"'
    action = ""
    if done_action:
        action = f' doneAction="{html.escape(done_action, quote=True)}"'
    seen_indexes = set()
    softkey_xml = []
    for index, label, uri in softkeys:
        if not 1 <= index <= 6 or index in seen_indexes:
            raise ValueError("softkey indexes must be unique and in 1..6")
        seen_indexes.add(index)
        softkey_xml.append(
            f'<SoftKey index="{index}"><Label>{html.escape(label)}</Label>'
            f'<URI>{html.escape(uri)}</URI></SoftKey>\n')
    hex_image = dob_bytes(pixels).hex()
    xml = (
        '<?xml version="1.0" encoding="ISO-8859-1"?>\n'
        f'<YealinkIPPhoneImageScreen Beep="no" Timeout="{timeout_seconds}" LockIn="no" '
        f'mode="fullscreen"{action}{refresh}>\n'
        f'<Image horizontalAlign="left" verticalAlign="top" '
        f'height="{HEIGHT}" width="{WIDTH}">{hex_image}</Image>\n'
        f'{"".join(softkey_xml)}'
        '</YealinkIPPhoneImageScreen>\n'
    )
    return xml.encode("ascii")


class FaceSource:
    def __init__(self, state_path: Path, default_state: str, inverted: bool,
                 week_remaining: int):
        self.state_path = state_path
        self.default_state = default_state
        self.inverted = inverted
        self.week_remaining = week_remaining
        self.phase = 0
        self.lock = threading.Lock()

    def current(self, requested_state: str | None = None,
                requested_phase: int | None = None):
        state = requested_state or self.default_state
        certainty = "static"
        if requested_state is None:
            try:
                candidate = json.loads(self.state_path.read_text(encoding="utf-8"))
                if candidate.get("state") in STATES:
                    state = candidate["state"]
                    certainty = candidate.get("certainty", "unknown")
            except (OSError, ValueError, TypeError):
                pass
        with self.lock:
            phase = self.phase if requested_phase is None else requested_phase
            if requested_phase is None:
                self.phase = (self.phase + 1) % FRAMES
        return state, phase % FRAMES, certainty, render(
            state, phase % FRAMES, self.week_remaining, self.inverted)


class FaceServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(self, address, handler, *, source, phone_ip, public_url,
                 refresh_seconds):
        super().__init__(address, handler)
        self.source = source
        self.phone_ip = phone_ip
        self.bind_ip = address[0]
        self.public_url = public_url
        self.refresh_seconds = refresh_seconds


class Handler(BaseHTTPRequestHandler):
    server_version = "DialAgent-T31G/1.0"

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.client_address[0] not in (
                self.server.phone_ip, self.server.bind_ip, "127.0.0.1", "::1"):
            self.send_error(403)
            return
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._reply(b"ok\n", "text/plain; charset=utf-8")
            return
        if parsed.path != "/face.xml":
            self.send_error(404)
            return
        query = parse_qs(parsed.query)
        requested_state = query.get("state", [None])[0]
        if requested_state is not None and requested_state not in STATES:
            self.send_error(400, "unknown state")
            return
        requested_phase = None
        if "phase" in query:
            try:
                requested_phase = int(query["phase"][0])
            except ValueError:
                self.send_error(400, "invalid phase")
                return
        state, phase, certainty, pixels = self.server.source.current(
            requested_state, requested_phase)
        refresh_url = None if query.get("static") == ["1"] else self.server.public_url
        payload = image_screen_xml(pixels, refresh_url, self.server.refresh_seconds)
        self._reply(payload, "application/xml")
        print(json.dumps({
            "at": datetime.now(timezone.utc).isoformat(),
            "client": self.client_address[0], "state": state, "phase": phase,
            "certainty": certainty, "refresh": refresh_url is not None,
        }, ensure_ascii=False), flush=True)

    def _reply(self, payload: bytes, content_type: str):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def private_ipv4(value: str) -> str:
    address = ipaddress.IPv4Address(value)
    if not address.is_private:
        raise argparse.ArgumentTypeError("address must be a private IPv4")
    return str(address)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-ip", type=private_ipv4, required=True)
    parser.add_argument("--phone-ip", type=private_ipv4, required=True)
    parser.add_argument("--port", type=int, default=8767)
    parser.add_argument("--state-file", type=Path,
                        default=Path("outputs/t31g-face/state.json"))
    parser.add_argument("--default-state", choices=STATES, default="idle")
    parser.add_argument("--week-remaining", type=int, default=8)
    parser.add_argument("--refresh-seconds", type=int, default=2)
    polarity = parser.add_mutually_exclusive_group()
    polarity.add_argument("--light", dest="light", action="store_true",
                          default=True,
                          help="white background / black drawing (default)")
    polarity.add_argument("--dark", dest="light", action="store_false",
                          help="black background / white drawing")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    if not 0 <= args.week_remaining <= 100:
        parser.error("week remaining must be 0..100")
    if not 1 <= args.refresh_seconds <= 60:
        parser.error("refresh seconds must be 1..60")

    public_url = f"http://{args.bind_ip}:{args.port}/face.xml"
    source = FaceSource(args.state_file, args.default_state, args.light,
                        args.week_remaining)
    server = FaceServer((args.bind_ip, args.port), Handler, source=source,
                        phone_ip=args.phone_ip, public_url=public_url,
                        refresh_seconds=args.refresh_seconds)
    print(f"T31G XML Browser URL: {public_url}", flush=True)
    print(f"Static first-frame URL: {public_url}?state=idle&phase=0&static=1", flush=True)
    print(f"Only {args.phone_ip} and localhost may fetch it. Ctrl-C stops.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
