#!/usr/bin/env python3
"""Talk to the user-local DialAgent Voice control app."""
import argparse
from pathlib import Path
import socket


DEFAULT_SOCKET = Path("/private/tmp/dialagent-codex-voice.sock")


def request(command, socket_path=DEFAULT_SOCKET, timeout=10):
    if command not in {"check", "toggle", "quit"}:
        raise ValueError("unsupported Voice control command")
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(socket_path))
        client.sendall(f"{command}\n".encode("utf-8"))
        chunks = []
        while True:
            chunk = client.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    except (OSError, TimeoutError) as error:
        raise RuntimeError(
            "DialAgent Voice Control is not reachable. Launch "
            "DialAgentVoiceControl.app and try again."
        ) from error
    finally:
        client.close()
    response = b"".join(chunks).decode("utf-8", errors="replace").strip()
    if not response:
        raise RuntimeError("DialAgent Voice Control returned an empty response")
    if response.startswith("ERROR\t"):
        reason = response.partition("\t")[2]
        raise RuntimeError(f"DialAgent Voice Control rejected the command: {reason}")
    return response


def parse_accessibility_status(output):
    for line in output.splitlines():
        key, separator, value = line.partition("\t")
        if key == "ACCESSIBILITY" and separator:
            if value in ("granted", "denied"):
                return value
    raise RuntimeError("Unable to read Accessibility permission status")


def require_accessibility(socket_path=DEFAULT_SOCKET):
    response = request("check", socket_path)
    if parse_accessibility_status(response) != "granted":
        raise RuntimeError(
            "DialAgent Voice Control does not have macOS Device Control and "
            "Data Access permission. Enable DialAgentVoiceControl.app in "
            "System Settings, then relaunch the app."
        )
    return response


def toggle_voice(socket_path=DEFAULT_SOCKET):
    response = request("toggle", socket_path)
    if response != "TOGGLE\tposted":
        raise RuntimeError(f"Unexpected Voice control response: {response}")
    return response


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "toggle", "quit"))
    parser.add_argument("--socket", type=Path, default=DEFAULT_SOCKET)
    args = parser.parse_args()
    try:
        if args.command == "check":
            print(require_accessibility(args.socket))
        elif args.command == "toggle":
            print(toggle_voice(args.socket))
        else:
            print(request("quit", args.socket))
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
