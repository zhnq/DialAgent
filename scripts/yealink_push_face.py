#!/usr/bin/env python3
"""Push one Yealink ImageScreen frame directly to a certificate-pinned phone."""

from __future__ import annotations

import argparse
import hashlib
import http.client
import ipaddress
import socket
import ssl

from t31g_face_preview import STATES, render as render_face
from t31g_xml_face import image_screen_xml


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, device_name: str, target_ip: str, fingerprint: str):
        super().__init__(device_name, 443, timeout=8,
                         context=ssl._create_unverified_context())
        self.target_ip = target_ip
        self.fingerprint = fingerprint.replace(":", "").lower()

    def connect(self):
        raw = socket.create_connection((self.target_ip, 443), self.timeout)
        wrapped = self._context.wrap_socket(raw, server_hostname=self.host)
        actual = hashlib.sha256(wrapped.getpeercert(binary_form=True)).hexdigest()
        if actual != self.fingerprint:
            wrapped.close()
            raise ssl.SSLError("Yealink certificate fingerprint mismatch")
        self.sock = wrapped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phone-ip", required=True)
    parser.add_argument("--device-name", required=True)
    parser.add_argument("--fingerprint", required=True)
    parser.add_argument("--state", choices=STATES, default="idle")
    parser.add_argument("--phase", type=int, default=0)
    parser.add_argument("--week-remaining", type=int, default=8)
    polarity = parser.add_mutually_exclusive_group()
    polarity.add_argument("--light", dest="light", action="store_true",
                          default=True,
                          help="white background / black drawing (default)")
    polarity.add_argument("--dark", dest="light", action="store_false",
                          help="black background / white drawing")
    parser.add_argument("--refresh-url",
                        help="optional URL the phone should refetch after the pushed frame")
    parser.add_argument("--refresh-seconds", type=int, default=2)
    parser.add_argument("--screen-timeout", type=int, default=0)
    args = parser.parse_args()
    address = ipaddress.IPv4Address(args.phone_ip)
    fingerprint = args.fingerprint.replace(":", "")
    if not address.is_private or not args.device_name.isalnum():
        parser.error("phone must be a private IPv4 with an alphanumeric device name")
    if len(fingerprint) != 64 or any(c not in "0123456789abcdefABCDEF" for c in fingerprint):
        parser.error("fingerprint must be a SHA-256 hex digest")
    if not 0 <= args.week_remaining <= 100:
        parser.error("week remaining must be 0..100")
    if not 1 <= args.refresh_seconds <= 60:
        parser.error("refresh seconds must be 1..60")
    if not 0 <= args.screen_timeout <= 60:
        parser.error("screen timeout must be 0..60")

    frame_count = 8
    if not 0 <= args.phase < frame_count:
        parser.error(f"phase must be 0..{frame_count - 1}")

    pixels = render_face(
        args.state, args.phase, args.week_remaining, args.light)
    xml = image_screen_xml(
        pixels, args.refresh_url, args.refresh_seconds, args.screen_timeout)
    body = b"xml=" + xml
    connection = PinnedHTTPSConnection(args.device_name, str(address),
                                       args.fingerprint)
    try:
        connection.request(
            "POST", "/servlet?push=xml", body=body,
            headers={"Host": args.device_name, "Content-Type": "text/xml",
                     "Content-Length": str(len(body)), "Connection": "close"})
        response = connection.getresponse()
        response_body = response.read(4096).decode("utf-8", "replace").strip()
        print(f"HTTP {response.status} {response.reason}")
        if response_body:
            print(response_body[:1000])
        return 0 if 200 <= response.status < 300 else 1
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
