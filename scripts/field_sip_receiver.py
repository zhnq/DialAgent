#!/usr/bin/env python3
"""Start a bounded SIP receiver on one explicit private Mac address.

The receiver bridges a real phone call to the two existing Loopback buses:
capture=Codex-to-Phone (B), playback=Phone-to-Codex (A). It never selects a
physical microphone, records audio, registers to a PBX, or listens on every
interface. Manual answer is the default. A deliberately explicit demo override
can enable automatic 200 OK on a shared network for a short, user-authorized
recording session. The wrapper also stops pjsua when the test window expires,
even when no call came in.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
from uuid import UUID

from mac_sip_audio_bridge import devices
from face_state import FaceStateSink
from voice_control_client import (DEFAULT_SOCKET, parse_accessibility_status,
                                  require_accessibility, toggle_voice)

ROOT = Path(__file__).resolve().parents[1]
HELPER_DIR = Path(os.environ.get("DIALAGENT_HELPER_DIR", ROOT / "work/bin"))
DEVICE_BINARY = HELPER_DIR / "pj-audio-devices"
PJSUA = Path(os.environ.get("DIALAGENT_PJSUA", "/opt/homebrew/bin/pjsua"))
AUDIO_DEFAULTS = HELPER_DIR / "coreaudio-defaults"
VOICE_CONTROL_APP = Path(os.environ.get(
    "DIALAGENT_VOICE_CONTROL_APP", ROOT / "work/DialAgentVoiceControl.app"))
VOICE_CONTROL_SOCKET = DEFAULT_SOCKET
VOICE_HOTKEY_NAME = "Control+Shift+V"
CODEX_APP_BUNDLE_ID = "com.openai.codex"
VOICE_THREAD_NAVIGATION_SECONDS = 1.5
VOICE_CONTROL_BINARY = VOICE_CONTROL_APP / "Contents/MacOS/DialAgentVoiceControl"


def private_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise argparse.ArgumentTypeError(str(error))
    ranges = (ipaddress.IPv4Network("10.0.0.0/8"),
              ipaddress.IPv4Network("172.16.0.0/12"),
              ipaddress.IPv4Network("192.168.0.0/16"))
    if not any(address in network for network in ranges):
        raise argparse.ArgumentTypeError("bind IP must be an explicit RFC1918 IPv4 address")
    return str(address)


def port(value):
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(str(error))
    if not 1024 <= number <= 65534:
        raise argparse.ArgumentTypeError("port must be between 1024 and 65534")
    return number


def codex_thread_id(value):
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise argparse.ArgumentTypeError("voice thread ID must be a UUID") from error
    if str(parsed) != value.lower():
        raise argparse.ArgumentTypeError("voice thread ID must be a canonical UUID")
    return str(parsed)


def open_codex_thread(thread_id):
    """Ask the installed Codex app to show an existing task, not create one."""
    # The host is part of a Codex task's identity. Supplying it avoids the
    # cross-host task-search route and opens this local task directly.
    url = f"codex://threads/{codex_thread_id(thread_id)}?hostId=local"
    subprocess.run(["/usr/bin/open", "-b", CODEX_APP_BUNDLE_ID, url],
                   capture_output=True, text=True, timeout=10, check=True)


def build_command(bind_ip, mapping, codec, seconds, log, auto_answer=False,
                  sip_port=15160, rtp_port=24200):
    command = [str(PJSUA), "--no-tcp", "--no-tones", "--no-vad",
               "--disable-stun", "--auto-update-nat=0",
               f"--bound-addr={bind_ip}", f"--ip-addr={bind_ip}",
               f"--id=sip:dialagent@{bind_ip}", f"--local-port={sip_port}",
               f"--rtp-port={rtp_port}", "--clock-rate=8000", "--ec-tail=0",
               "--max-calls=1", f"--duration={seconds}",
               "--dis-codec=*", f"--add-codec={codec}/8000",
               f"--capture-dev={mapping['Codex-to-Phone']}",
               f"--playback-dev={mapping['Phone-to-Codex']}",
               "--snd-clock-rate=48000", "--snd-auto-close=0",
               f"--log-file={log}", "--log-level=5", "--app-log-level=5",
               "--stdout-no-buf", "--no-color"]
    if auto_answer:
        command.append("--auto-answer=200")
    return command


def check_ports(bind_ip, sip_port, rtp_port):
    held = []
    try:
        for current in (sip_port, rtp_port, rtp_port + 1):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            held.append(sock)
            sock.bind((bind_ip, current))
    finally:
        for sock in held:
            sock.close()


def auto_answer_allowed(auto_answer, isolated_link_confirmed,
                        demo_auto_answer_confirmed):
    """Require an explicit environment acknowledgement for auto-answer."""
    return (not auto_answer or isolated_link_confirmed or
            demo_auto_answer_confirmed)


def parse_audio_defaults(output):
    defaults = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3 or parts[0] not in ("INPUT", "OUTPUT"):
            continue
        direction = parts[0].lower()
        try:
            device_id = int(parts[1])
        except ValueError as error:
            raise RuntimeError("Invalid CoreAudio device id") from error
        defaults[direction] = {"id": device_id, "name": parts[2]}
    if set(defaults) != {"input", "output"}:
        raise RuntimeError("Unable to read both default CoreAudio devices")
    return defaults


def read_audio_defaults(binary=AUDIO_DEFAULTS):
    result = subprocess.run([str(binary), "defaults"], capture_output=True,
                            text=True, timeout=10, check=True)
    return parse_audio_defaults(result.stdout)


def set_audio_default(binary, direction, mode, value):
    if direction not in ("input", "output") or mode not in ("name", "id"):
        raise ValueError("invalid audio default selector")
    subprocess.run([str(binary), f"set-{mode}", direction, str(value)],
                   capture_output=True, text=True, timeout=10, check=True)


def restore_audio_default(binary, direction, previous):
    """Restore by stable id, falling back to name after device re-enumeration."""
    try:
        set_audio_default(binary, direction, "id", previous["id"])
    except subprocess.CalledProcessError:
        set_audio_default(binary, direction, "name", previous["name"])


def call_state_event(line):
    lowered = line.lower()
    if "state changed" in lowered and "to confirmed" in lowered:
        return "connected"
    if "state changed" in lowered and "to disconnected" in lowered:
        return "disconnected"
    return None


def require_voice_hotkey_access(socket_path=VOICE_CONTROL_SOCKET):
    return require_accessibility(socket_path)


def post_voice_hotkey(socket_path=VOICE_CONTROL_SOCKET):
    return toggle_voice(socket_path)


def post_voice_hotkey_without_activation(binary=VOICE_CONTROL_BINARY):
    """Post the contextual Voice command without changing the selected window."""
    result = subprocess.run([str(binary), "toggle"], capture_output=True,
                            text=True, timeout=10, check=True)
    if result.stdout.strip() != "TOGGLE\tposted":
        raise RuntimeError("Voice controller did not post the contextual shortcut")
    return result.stdout.strip()


class AutomaticAudioRoute:
    """Switch defaults on a confirmed call and restore them on disconnect."""

    def __init__(self, binary=AUDIO_DEFAULTS):
        self.binary = binary
        self.previous = read_audio_defaults(binary)
        self.active = False
        self.needs_restore = False
        self.events = []
        self.lock = threading.Lock()

    def _snapshot(self):
        return read_audio_defaults(self.binary)

    def switch(self):
        with self.lock:
            if self.active:
                return True
            try:
                set_audio_default(self.binary, "input", "name", "Phone-to-Codex")
                self.needs_restore = True
                set_audio_default(self.binary, "output", "name", "Codex-to-Phone")
                current = self._snapshot()
                if (current["input"]["name"] != "Phone-to-Codex" or
                        current["output"]["name"] != "Codex-to-Phone"):
                    raise RuntimeError("CoreAudio defaults did not change to the phone route")
                self.active = True
                self.events.append({"event": "switched", "devices": current})
                print("[DialAgent] Audio switched to Phone-to-Codex / Codex-to-Phone.",
                      flush=True)
                return True
            except Exception as error:
                self.events.append({"event": "switch_failed", "error": str(error)})
                print(f"[DialAgent] Audio switch failed: {error}", flush=True)
                self._restore_locked()
                return False

    def _restore_locked(self):
        if not self.needs_restore:
            return
        try:
            restore_audio_default(self.binary, "input", self.previous["input"])
            restore_audio_default(self.binary, "output", self.previous["output"])
            current = self._snapshot()
            if (current["input"]["id"] != self.previous["input"]["id"] or
                    current["output"]["id"] != self.previous["output"]["id"]):
                raise RuntimeError("CoreAudio defaults did not restore to their prior devices")
            self.events.append({"event": "restored", "devices": current})
            self.active = False
            self.needs_restore = False
            print("[DialAgent] Previous Mac audio devices restored.", flush=True)
        except Exception as error:
            self.events.append({"event": "restore_failed", "error": str(error)})
            print(f"[DialAgent] Audio restore failed: {error}", flush=True)

    def restore(self):
        with self.lock:
            self._restore_locked()


class AutomaticVoice:
    """Toggle Voice in an optional fixed task for the lifetime of one call."""

    def __init__(self, socket_path=VOICE_CONTROL_SOCKET, thread_id=None,
                 navigation_seconds=VOICE_THREAD_NAVIGATION_SECONDS):
        self.socket_path = socket_path
        require_voice_hotkey_access(socket_path)
        self.thread_id = codex_thread_id(thread_id) if thread_id else None
        self.navigation_seconds = navigation_seconds
        self.active = False
        self.events = []
        self.lock = threading.Lock()

    def start(self):
        with self.lock:
            if self.active:
                return True
            try:
                if self.thread_id:
                    open_codex_thread(self.thread_id)
                    self.events.append({"event": "voice_thread_open_requested",
                                        "thread_id": self.thread_id})
                    # `open` only hands the URL to macOS; the app routes it asynchronously.
                    time.sleep(self.navigation_seconds)
                    # Use Codex's app-scoped Voice toggle directly. The helper
                    # intentionally does not re-activate every Codex window after
                    # the deep link selected the target task.
                    start_result = post_voice_hotkey_without_activation()
                else:
                    start_result = post_voice_hotkey(self.socket_path)
                self.active = True
                self.events.append({"event": "voice_start_triggered",
                                    "method": start_result,
                                    "thread_id": self.thread_id})
                print("[DialAgent] Codex Voice start command triggered.",
                      flush=True)
                return True
            except Exception as error:
                self.events.append({"event": "voice_start_failed",
                                    "error": str(error)})
                print(f"[DialAgent] Codex Voice start failed: {error}", flush=True)
                return False

    def stop(self):
        with self.lock:
            if not self.active:
                return
            try:
                # The installed app declares composer.startVoiceMode as
                # "Start or stop Voice Chat". Reuse the same direct toggle;
                # do not open a command/search overlay.
                stop_result = post_voice_hotkey(self.socket_path)
                self.events.append({"event": "voice_stop_triggered",
                                    "method": stop_result})
                self.active = False
                print("[DialAgent] Codex Voice end command triggered.", flush=True)
            except Exception as error:
                self.events.append({"event": "voice_stop_failed",
                                    "error": str(error)})
                print(f"[DialAgent] Codex Voice stop failed: {error}", flush=True)


def forward_output(stream, audio_route, voice_route, face_state=None):
    for line in iter(stream.readline, ""):
        print(line, end="", flush=True)
        if audio_route is None and voice_route is None and face_state is None:
            continue
        event = call_state_event(line)
        if event == "connected":
            audio_ready = audio_route is None or audio_route.switch()
            voice_requested = (voice_route.start() if voice_route is not None and audio_ready
                               else False)
            if face_state is not None:
                if not audio_ready or (voice_route is not None and not voice_requested):
                    face_state.publish("error", "audio_or_voice_start_failed", "observed")
                elif voice_route is not None:
                    face_state.publish("listening", "voice_start_hotkey_posted", "inferred")
                else:
                    face_state.publish("idle", "call_connected_without_voice", "observed")
        elif event == "disconnected":
            if voice_route is not None:
                voice_route.stop()
            if audio_route is not None:
                audio_route.restore()
            if face_state is not None:
                face_state.publish("idle", "call_disconnected", "observed")


def stop_process(process, grace_seconds=5):
    """Stop the receiver and wait for its sockets to be released."""
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def read_request_token(path):
    try:
        return path.read_text(encoding="ascii").strip()
    except OSError:
        return None


def watch_hangup_requests(path, process, stop_event, events):
    """Translate the phone screen's OK action into a pjsua BYE."""
    previous = read_request_token(path)
    while not stop_event.wait(0.1):
        current = read_request_token(path)
        if not current or current == previous:
            continue
        previous = current
        if process.poll() is not None or process.stdin is None:
            return
        try:
            process.stdin.write("h\n")
            process.stdin.flush()
            events.append({"event": "local_hangup_requested",
                           "token": current})
            print("[DialAgent] Phone OK requested local SIP hangup.", flush=True)
        except (BrokenPipeError, OSError, ValueError):
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind-ip", required=True, type=private_ipv4,
                        help="current Mac address reachable from the phone")
    parser.add_argument("--codec", choices=("PCMU", "PCMA"), default="PCMU")
    parser.add_argument("--sip-port", type=port, default=15160)
    parser.add_argument("--rtp-port", type=port, default=24200,
                        help="RTP uses this port and RTCP uses the following port")
    parser.add_argument("--seconds", type=int, default=600, metavar="60..3600")
    parser.add_argument("--auto-answer", action="store_true")
    parser.add_argument("--isolated-link-confirmed", action="store_true",
                        help="allow auto-answer because the link is isolated")
    parser.add_argument("--demo-auto-answer-confirmed", action="store_true",
                        help="allow short-lived auto-answer on a shared network for a user-authorized demo")
    parser.add_argument("--auto-audio-route", action="store_true",
                        help="switch system defaults to A/B while a call is confirmed, then restore")
    parser.add_argument("--auto-voice", action="store_true",
                        help="start Voice on CONFIRMED and stop it on DISCONNECTED")
    parser.add_argument("--voice-thread-id", type=codex_thread_id,
                        help="open this existing Codex task before starting Voice")
    parser.add_argument("--face-state-file", type=Path,
                        help="write local face-preview state JSON on SIP/control events")
    parser.add_argument("--hangup-request-file", type=Path,
                        help="watch this file for a phone-requested local SIP hangup")
    parser.add_argument("--dry-run", action="store_true",
                        help="validate devices and print the argv; do not listen")
    args = parser.parse_args()
    if not 60 <= args.seconds <= 3600:
        parser.error("--seconds must be between 60 and 3600")
    if not auto_answer_allowed(args.auto_answer,
                               args.isolated_link_confirmed,
                               args.demo_auto_answer_confirmed):
        parser.error("--auto-answer requires --isolated-link-confirmed or --demo-auto-answer-confirmed")
    if args.rtp_port + 1 > 65535 or args.sip_port in (args.rtp_port, args.rtp_port + 1):
        parser.error("SIP, RTP and RTCP ports must be distinct and valid")
    if not PJSUA.is_file() or not DEVICE_BINARY.is_file():
        parser.error("pjsua or the checked-in audio device helper is missing")
    if args.auto_audio_route and not AUDIO_DEFAULTS.is_file():
        parser.error("the CoreAudio default-device helper is missing")
    if args.auto_voice and not VOICE_CONTROL_APP.is_dir():
        parser.error("the DialAgent Voice Control app is missing")
    if args.voice_thread_id and not args.auto_voice:
        parser.error("--voice-thread-id requires --auto-voice")

    mapping = devices(DEVICE_BINARY)
    parent = ROOT / "work/field"
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="receiver-", dir=parent))
    log = run / "pjsua.log"
    command = build_command(args.bind_ip, mapping, args.codec, args.seconds,
                            log, args.auto_answer, args.sip_port, args.rtp_port)
    report = {"created_at": datetime.now(timezone.utc).isoformat(),
              "bind_ip": args.bind_ip,
              "sip_uri": f"sip:dialagent@{args.bind_ip}:{args.sip_port}",
              "sip_port": args.sip_port, "rtp_port": args.rtp_port,
              "codec": args.codec,
              "call_duration_limit_seconds": args.seconds,
              "receiver_session_limit_seconds": args.seconds,
              "devices": mapping, "auto_answer": args.auto_answer,
              "auto_audio_route": args.auto_audio_route,
              "auto_voice": args.auto_voice,
              "face_state_file": str(args.face_state_file) if args.face_state_file else None,
              "voice_thread_id": args.voice_thread_id,
              "voice_hotkey": VOICE_HOTKEY_NAME if args.auto_voice else None,
              "voice_control_app": str(VOICE_CONTROL_APP) if args.auto_voice else None,
              "voice_control_socket": str(VOICE_CONTROL_SOCKET) if args.auto_voice else None,
              "hangup_request_file": (str(args.hangup_request_file)
                                      if args.hangup_request_file else None),
              "audio_recording": False, "external_registration": False,
              "command": command, "started": False}
    state = run / "state.json"
    if args.dry_run:
        state.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f"Evidence: {run}")
        return 0

    check_ports(args.bind_ip, args.sip_port, args.rtp_port)
    report["started"] = True
    state.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Dial from the phone to: {report['sip_uri']}", flush=True)
    if args.auto_answer:
        print("Demo auto-answer is enabled: an incoming call receives 200 OK automatically.",
              flush=True)
    else:
        print("Manual answer is enabled: at an incoming-call prompt, use pjsua's 'a' command and 200.",
              flush=True)
    print("Use 'q' to stop. No audio is recorded.", flush=True)
    print(f"Evidence: {run}", flush=True)
    process = None
    output_thread = None
    hangup_thread = None
    hangup_stop = threading.Event()
    hangup_events = []
    face_state = FaceStateSink(args.face_state_file) if args.face_state_file else None
    audio_route = AutomaticAudioRoute() if args.auto_audio_route else None
    try:
        voice_route = (AutomaticVoice(thread_id=args.voice_thread_id)
                       if args.auto_voice else None)
    except RuntimeError as error:
        parser.error(str(error))
    if audio_route is not None:
        report["previous_system_audio"] = audio_route.previous
        print("[DialAgent] System audio will switch on CONFIRMED and restore on DISCONNECTED.",
              flush=True)
    if voice_route is not None:
        print("[DialAgent] Codex Voice will start on CONFIRMED and stop on DISCONNECTED.",
              flush=True)
        if args.voice_thread_id:
            print(f"[DialAgent] Each call will open Codex task {args.voice_thread_id} before Voice.",
                  flush=True)
            print("[DialAgent] Leave Voice off before calling; the target task need not be visible.",
                  flush=True)
        else:
            print("[DialAgent] Leave this Codex task visible and Voice off before calling.",
                  flush=True)
    exit_code = 1
    try:
        process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True,
                                   bufsize=1)
        if args.hangup_request_file is not None:
            hangup_thread = threading.Thread(
                target=watch_hangup_requests,
                args=(args.hangup_request_file, process, hangup_stop,
                      hangup_events), daemon=True)
            hangup_thread.start()
        output_thread = threading.Thread(target=forward_output,
                                         args=(process.stdout, audio_route,
                                               voice_route, face_state),
                                         daemon=True)
        output_thread.start()
        try:
            exit_code = process.wait(timeout=args.seconds)
        except subprocess.TimeoutExpired:
            report["session_timeout_reached"] = True
            stop_process(process)
            exit_code = 124
        except KeyboardInterrupt:
            report["interrupted"] = True
            stop_process(process)
            exit_code = 130
    finally:
        hangup_stop.set()
        stop_process(process)
        if output_thread is not None:
            output_thread.join(timeout=5)
        if hangup_thread is not None:
            hangup_thread.join(timeout=2)
        report["hangup_events"] = hangup_events
        if voice_route is not None:
            voice_route.stop()
            report["voice_events"] = voice_route.events
        if audio_route is not None:
            audio_route.restore()
            report["audio_route_events"] = audio_route.events
        if face_state is not None:
            face_state.publish("idle", "receiver_stopped", "observed")
            face_state.close()
        report["ended_at"] = datetime.now(timezone.utc).isoformat()
        report["exit_code"] = exit_code
        try:
            check_ports(args.bind_ip, args.sip_port, args.rtp_port)
            report["ports_released"] = True
        except OSError:
            report["ports_released"] = False
        state.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
