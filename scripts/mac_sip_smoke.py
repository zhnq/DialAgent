#!/usr/bin/env python3
"""Exercise two local pjsua peers using generated tones, never a microphone.

This checks SIP setup, bidirectional encoded media and hangup on 127.0.0.1.
It does not test the phone, CoreAudio devices, Loopback or Codex Voice.
"""

import argparse
import array
import json
import math
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import wave


def tone(path, frequency):
    samples = array.array("h", (
        int(7000 * math.sin(2 * math.pi * frequency * i / 8000))
        for i in range(16000)
    ))
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(path), "wb") as output:
        output.setparams((1, 2, 8000, 0, "NONE", "not compressed"))
        output.writeframes(samples.tobytes())


def check_audio(path, expected, unwanted):
    with wave.open(str(path), "rb") as recording:
        if recording.getsampwidth() != 2 or recording.getnchannels() != 1:
            raise RuntimeError("Expected mono 16-bit PCM recording")
        rate = recording.getframerate()
        samples = array.array("h", recording.readframes(recording.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    # Inspect one-second windows; startup and shutdown may include silence.
    amplitudes = []
    for offset in range(0, len(samples) - rate + 1, rate):
        window = samples[offset:offset + rate]
        def amplitude(frequency):
            omega = 2 * math.pi * frequency / rate
            real = sum(x * math.cos(omega * i) for i, x in enumerate(window))
            imag = sum(x * math.sin(omega * i) for i, x in enumerate(window))
            return 2 * math.hypot(real, imag) / rate
        amplitudes.append((amplitude(expected), amplitude(unwanted)))
    matched = sum(a > 500 and a > 5 * b for a, b in amplitudes)
    return {"seconds": round(len(samples) / rate, 2),
            "expected_hz": expected, "matched_seconds": matched,
            "passed": matched >= 2}


def contents(path):
    return path.read_text(errors="replace") if path.exists() else ""


def wait_for(process, logfile, marker, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if marker in contents(logfile):
            return
        if process.poll() is not None:
            raise RuntimeError(f"pjsua exited early; inspect {logfile}")
        time.sleep(0.1)
    raise RuntimeError(f"Timed out waiting for {marker!r}; inspect {logfile}")


def stop(process):
    if process.poll() is not None:
        return
    try:
        process.communicate("q\n", timeout=5)
    except (subprocess.TimeoutExpired, BrokenPipeError):
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pjsua", default=shutil.which("pjsua"))
    parser.add_argument("--output-root", type=Path,
                        default=Path(__file__).resolve().parents[1] / "work/mac-preflight")
    args = parser.parse_args()
    if not args.pjsua:
        parser.error("pjsua is not installed or not on PATH")
    args.output_root.mkdir(parents=True, exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="sip-", dir=args.output_root)).resolve()
    report = {"scope": "localhost SIP + generated audio only", "passed": False}
    processes = []
    try:
        # Fail without disturbing existing listeners. pjsua subsequently binds
        # the same ports; if another program races us, startup must fail.
        reserved = []
        try:
            for port in (15060, 15062, 24000, 24001, 24010, 24011):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                reserved.append(sock)
                sock.bind(("127.0.0.1", port))
        finally:
            for sock in reserved:
                sock.close()

        peers = (("receiver", 15060, 24000, 697),
                 ("caller", 15062, 24010, 1209))
        for name, sip_port, rtp_port, frequency in peers:
            wav = run / f"{name}-source.wav"
            tone(wav, frequency)
            log = run / f"{name}.log"
            command = [args.pjsua, "--null-audio", "--no-tcp", "--no-tones",
                       "--no-vad", "--disable-stun", "--auto-update-nat=0",
                       "--bound-addr=127.0.0.1", "--ip-addr=127.0.0.1",
                       f"--id=sip:{name}@127.0.0.1",
                       f"--local-port={sip_port}", f"--rtp-port={rtp_port}",
                       "--clock-rate=8000", "--max-calls=1", "--duration=5",
                       "--auto-answer=200", "--auto-play", f"--play-file={wav}",
                       "--auto-rec", f"--rec-file={run / (name + '-received.wav')}",
                       f"--log-file={log}", "--log-level=5", "--app-log-level=0"]
            if name == "caller":
                command.append("sip:receiver@127.0.0.1:15060")
            process = subprocess.Popen(command, stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, text=True)
            processes.append(process)
            wait_for(process, log, "SIP UDP transport started")
        for process, (name, _, _, _) in zip(processes, peers):
            wait_for(process, run / f"{name}.log", "CONFIRMED")
            wait_for(process, run / f"{name}.log", "DISCONNECTED")
        for process in processes:
            stop(process)
        report["receiver"] = check_audio(run / "receiver-received.wav", 1209, 697)
        report["caller"] = check_audio(run / "caller-received.wav", 697, 1209)
        report["passed"] = all(report[name]["passed"] for name in ("receiver", "caller"))
    except Exception as error:
        report["error"] = str(error)
    finally:
        for process in processes:
            stop(process)
        (run / "result.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    print(f"Evidence: {run}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
