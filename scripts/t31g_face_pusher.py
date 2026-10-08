#!/usr/bin/env python3
"""Push successive T31G DOB frames from the Mac via Yealink Push XML."""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
import signal
import time
from urllib.parse import urlsplit

from t31g_face_preview import FRAMES as FACE_FRAMES, STATES, render as render_face
from t31g_xml_face import image_screen_xml
from yealink_push_face import PinnedHTTPSConnection


def current_state(path: Path, fallback: str) -> tuple[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        state = payload.get("state")
        if state in STATES:
            return state, payload.get("certainty", "unknown")
    except (OSError, TypeError, ValueError):
        pass
    return fallback, "static"


def push_frame(connection: PinnedHTTPSConnection, xml: bytes) -> None:
    body = b"xml=" + xml
    connection.request(
        "POST",
        "/servlet?push=xml",
        body=body,
        headers={
            "Content-Type": "text/xml",
            "Content-Length": str(len(body)),
            "Connection": "keep-alive",
        },
    )
    response = connection.getresponse()
    response.read()
    if not 200 <= response.status < 300:
        raise RuntimeError(f"Push XML returned HTTP {response.status}")


def push_frame_resilient(connection: PinnedHTTPSConnection,
                         xml: bytes) -> str | None:
    """Push one frame, closing a broken keep-alive channel for next retry."""
    try:
        push_frame(connection, xml)
        return None
    except (OSError, RuntimeError) as error:
        connection.close()
        return f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone-ip", required=True)
    parser.add_argument("--device-name", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--state-file", type=Path,
                        default=Path("outputs/t31g-face/state.json"))
    parser.add_argument("--default-state", choices=STATES, default="idle")
    parser.add_argument("--week-remaining", type=int, default=8)
    parser.add_argument("--interval", type=float, default=1.5)
    parser.add_argument("--screen-timeout", type=int, default=2,
                        help="seconds before XML exits; 0 keeps it resident")
    parser.add_argument(
        "--dial-target",
        help="map XML OK and softkey 1 to Dial:<private IPv4>",
    )
    parser.add_argument(
        "--action-uri",
        help="map XML OK and softkey 1 to a private HTTP test URI",
    )
    parser.add_argument("--cycles", type=int, default=0,
                        help="0 runs until interrupted")
    parser.add_argument(
        "--idle-normal-ui",
        action="store_true",
        help="push only during non-idle states; XML Timeout restores idle UI",
    )
    polarity = parser.add_mutually_exclusive_group()
    polarity.add_argument("--light", dest="light", action="store_true",
                          default=True,
                          help="white background / black drawing (default)")
    polarity.add_argument("--dark", dest="light", action="store_false")
    args = parser.parse_args()

    address = ipaddress.IPv4Address(args.phone_ip)
    fingerprint = args.fingerprint.replace(":", "")
    if not address.is_private or not args.device_name.isalnum():
        parser.error("phone must be a private IPv4 with an alphanumeric device name")
    if len(fingerprint) != 64 or any(
            character not in "0123456789abcdefABCDEF" for character in fingerprint):
        parser.error("fingerprint must be a SHA-256 hex digest")
    if not 0 <= args.week_remaining <= 100:
        parser.error("week remaining must be 0..100")
    if not 0.5 <= args.interval <= 30:
        parser.error("interval must be 0.5..30 seconds")
    if not 0 <= args.screen_timeout <= 10:
        parser.error("screen timeout must be 0..10 seconds")
    if args.cycles < 0:
        parser.error("cycles must be zero or positive")
    if args.dial_target and args.action_uri:
        parser.error("use only one of --dial-target and --action-uri")
    dial_action = None
    softkeys = ()
    if args.dial_target:
        dial_target = ipaddress.IPv4Address(args.dial_target)
        if not dial_target.is_private:
            parser.error("dial target must be a private IPv4 address")
        dial_action = f"Dial:{dial_target}"
    elif args.action_uri:
        parsed_action = urlsplit(args.action_uri)
        try:
            action_host = ipaddress.IPv4Address(parsed_action.hostname or "")
        except ipaddress.AddressValueError:
            parser.error("action URI host must be a private IPv4 address")
        if parsed_action.scheme != "http" or not action_host.is_private:
            parser.error("action URI must use HTTP with a private IPv4 host")
        dial_action = args.action_uri

    push_connection = PinnedHTTPSConnection(
        args.device_name, str(address), args.fingerprint)
    frame_count = FACE_FRAMES
    phase = 0
    frames_left = args.cycles * frame_count if args.cycles else None
    xml_visible = False
    ready_emitted = False

    def stop_cleanly(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_cleanly)
    signal.signal(signal.SIGINT, stop_cleanly)
    try:
        if args.idle_normal_ui:
            # Verify the pinned TLS channel without opening XML Browser. The
            # normal phone UI and CODEX DSS key remain available before a call.
            push_connection.connect()
            push_connection.close()
            print(json.dumps({
                "event": "ready",
                "display": "normal_phone_ui",
                "animation": "face",
            }), flush=True)
            ready_emitted = True
            # Give the launcher time to start the receiver, which publishes a
            # fresh idle state and replaces any stale state from an earlier run.
            time.sleep(args.interval)
        while frames_left is None or frames_left > 0:
            started = time.monotonic()
            state, certainty = current_state(args.state_file, args.default_state)
            if args.idle_normal_ui and state == "idle":
                if xml_visible:
                    xml_visible = False
                    phase = 0
                    print(json.dumps({
                        "event": "normal_ui_pending_timeout",
                        "state": state,
                        "certainty": certainty,
                        "timeout_seconds": args.screen_timeout,
                    }, ensure_ascii=False), flush=True)
                time.sleep(args.interval)
                continue
            pixels = render_face(
                state, phase, args.week_remaining, args.light)
            xml = image_screen_xml(
                pixels, None, timeout_seconds=args.screen_timeout,
                done_action=dial_action, softkeys=softkeys)
            push_error = push_frame_resilient(push_connection, xml)
            if push_error is not None:
                # The phone may momentarily stop answering Push XML while it
                # changes between its native dialing/call UI. A failed frame
                # must not kill the resident animation; the next iteration
                # opens a fresh pinned TLS connection and tries again.
                print(json.dumps({
                    "event": "push_retry",
                    "state": state,
                    "phase": phase,
                    "error": push_error,
                }, ensure_ascii=False), flush=True)
                time.sleep(args.interval)
                continue
            xml_visible = True
            elapsed = time.monotonic() - started
            if not ready_emitted:
                print(json.dumps({
                    "event": "ready",
                    "display": "xml_image_screen",
                    "animation": "face",
                }), flush=True)
                ready_emitted = True
            print(json.dumps({
                "state": state,
                "phase": phase,
                "certainty": certainty,
                "push_seconds": round(elapsed, 3),
            }, ensure_ascii=False), flush=True)
            phase = (phase + 1) % frame_count
            if frames_left is not None:
                frames_left -= 1
            time.sleep(max(0.0, args.interval - elapsed))
    except KeyboardInterrupt:
        pass
    finally:
        # Stopping pushes lets the last ImageScreen frame's Timeout expire,
        # returning the handset to its normal UI without Action URI auth.
        push_connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
