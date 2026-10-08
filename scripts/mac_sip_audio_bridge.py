#!/usr/bin/env python3
"""Test pjsua's real virtual sound devices against a localhost synthetic caller.

Precondition: Voice stopped, A's physical microphone source disabled, B's
headphone monitor disabled; both devices contain an enabled Pass-Thru.
Never selects a physical sound device. No external network targets are allowed.
"""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import socket

from mac_sip_smoke import check_audio, contents, stop, tone, wait_for


def devices(binary):
    result = subprocess.run([str(binary)], capture_output=True, text=True,
                            timeout=20, check=True)
    selected = {}
    for line in result.stdout.splitlines():
        if not line.startswith("DEVICE\t"):
            continue
        _, index, name, inputs, outputs = line.split("\t")
        if name in ("Phone-to-Codex", "Codex-to-Phone"):
            if name in selected or int(inputs) < 1 or int(outputs) < 1:
                raise RuntimeError(f"Ambiguous or unsuitable audio device: {name}")
            selected[name] = int(index)
    if len(selected) != 2:
        raise RuntimeError("Both named virtual devices must be available")
    return selected


def check_ports_free():
    sockets = []
    try:
        for port in (15160, 15162, 24200, 24201, 24210, 24211):
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sockets.append(sock)
            sock.bind(("127.0.0.1", port))
    finally:
        for sock in sockets:
            sock.close()


def base(pjsua, role, sip, rtp, log, codec):
    return [str(pjsua), "--no-tcp", "--no-tones", "--no-vad",
            "--disable-stun", "--auto-update-nat=0",
            "--bound-addr=127.0.0.1", "--ip-addr=127.0.0.1",
            f"--id=sip:{role}@127.0.0.1", f"--local-port={sip}",
            f"--rtp-port={rtp}", "--clock-rate=8000", "--ec-tail=0",
            "--max-calls=1", f"--duration={20 if role == 'receiver' else 6}",
            "--auto-answer=200",
            "--dis-codec=*", f"--add-codec={codec}/8000",
            f"--log-file={log}", "--log-level=5", "--app-log-level=0"]


def spawn(command):
    return subprocess.Popen(command, stdin=subprocess.PIPE, text=True,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_calls(args, run, report):
    check_ports_free()
    mapping = devices(args.devices)
    report["devices"] = mapping
    receiver_log = run / "receiver.log"
    receiver = spawn(base(args.pjsua, "receiver", 15160, 24200,
                          receiver_log, args.codec) +
                     [f"--capture-dev={mapping['Codex-to-Phone']}",
                      f"--playback-dev={mapping['Phone-to-Codex']}",
                      "--snd-clock-rate=48000", "--snd-auto-close=0"])
    report["calls"] = []
    try:
        wait_for(receiver, receiver_log, "SIP UDP transport started")
        for number in range(1, args.calls + 1):
            if devices(args.devices) != mapping:
                raise RuntimeError("Audio device IDs changed; aborting safely")
            call_dir = run / f"call-{number}"
            call_dir.mkdir()
            tone(call_dir / "caller-source.wav", 1209)
            caller_log = call_dir / "caller.log"
            before = len(contents(receiver_log))
            caller = probe = None
            try:
                # The probe injects 697 Hz into B, reads 1209 Hz from A and
                # checks A is quiet in the last two seconds after hangup.
                with (call_dir / "probe.log").open("w") as probe_output:
                    probe = subprocess.Popen(
                        [str(args.probe), "Codex-to-Phone", "Phone-to-Codex", "--bridge"],
                        stdout=probe_output, stderr=subprocess.STDOUT, text=True)
                    wait_for(probe, call_dir / "probe.log", "PROBE_READY", timeout=8)
                    caller = spawn(base(args.pjsua, "caller", 15162, 24210,
                                        caller_log, args.codec) +
                                   ["--null-audio", "--auto-play",
                                    f"--play-file={call_dir / 'caller-source.wav'}",
                                    "--auto-rec", f"--rec-file={call_dir / 'caller-received.wav'}",
                                    "sip:receiver@127.0.0.1:15160"])
                    wait_for(caller, caller_log, "CONFIRMED")
                    wait_for(caller, caller_log, "DISCONNECTED")
                    stop(caller)
                    probe.wait(timeout=20)
                probe_text = contents(call_dir / "probe.log")
                readings = [json.loads(line) for line in probe_text.splitlines()
                            if line.startswith("{")]
                if not readings:
                    raise RuntimeError(f"Audio probe did not produce results: {call_dir}")
                uplink = readings[-1]
                downlink = check_audio(call_dir / "caller-received.wav", 697, 1209)
                receiver_part = contents(receiver_log)[before:]
                connected = "CONFIRMED" in receiver_part and "DISCONNECTED" in receiver_part
                remote_hangup = ("Received Request msg BYE/" in receiver_part and
                                 "Sending Request msg BYE/" not in receiver_part)
                codec_selected = (f"audio updated, stream #0: {args.codec} (sendrecv)"
                                  in receiver_part)
                # Every captured result needs fresh receiver call markers, not
                # a CONFIRMED leftover from a previous call. The receiver's
                # 20-second watchdog must not cause the caller's 6-second hangup.
                call_result = {"call": number, "uplink": uplink,
                               "downlink": downlink, "receiver_connected_and_hung_up": connected,
                               "caller_initiated_hangup": remote_hangup,
                               "negotiated_codec_confirmed": codec_selected,
                               "passed": connected and remote_hangup and codec_selected and
                                         uplink["passed"] and downlink["passed"]}
                (call_dir / "result.json").write_text(json.dumps(call_result, indent=2) + "\n")
                report["calls"].append(call_result)
                print(json.dumps(call_result), flush=True)
                if not call_result["passed"]:
                    raise RuntimeError(f"Call {number} failed; inspect {call_dir}")
                time.sleep(0.25)
            finally:
                if caller is not None:
                    stop(caller)
                if probe is not None and probe.poll() is None:
                    probe.terminate()
                    try:
                        probe.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        probe.kill()
                        probe.wait()
    finally:
        stop(receiver)
    # Verify the test listener and media ports have actually been released.
    check_ports_free()
    report["ports_released"] = True


def main():
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pjsua", type=Path, default=Path("/opt/homebrew/bin/pjsua"))
    parser.add_argument("--probe", type=Path, default=root / "work/mac-preflight/audio-probe")
    parser.add_argument("--devices", type=Path, default=root / "work/mac-preflight/pj-audio-devices")
    parser.add_argument("--calls", type=int, choices=range(1, 4), default=3)
    parser.add_argument("--codec", choices=("PCMU", "PCMA"), default="PCMU")
    parser.add_argument("--isolated-audio-confirmed", action="store_true",
                        help="Confirm Voice stopped and all physical sources/monitors disabled")
    args = parser.parse_args()
    if not args.isolated_audio_confirmed:
        parser.error("Stop Voice and disable physical Loopback sources/monitors before this test")
    parent = root / "work/mac-preflight"
    parent.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="sip-audio-", dir=parent))
    report = {"scope": "localhost SIP + named virtual sound devices + synthetic tones",
              "codec": args.codec, "hangup_policy": "caller 6s, receiver safety timeout 20s",
              "passed": False}
    try:
        run_calls(args, run, report)
        report["passed"] = len(report["calls"]) == args.calls and all(c["passed"] for c in report["calls"])
    except Exception as error:
        report["error"] = str(error)
    (run / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)
    print(f"Evidence: {run}", flush=True)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
