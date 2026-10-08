# macOS + Yealink setup

## 1. Prepare the audio buses

Create two full-duplex virtual devices with these exact names:

- `Phone-to-Codex`: phone receive audio becomes the Mac's Voice input.
- `Codex-to-Phone`: the Mac's Voice output returns to the handset/speaker.

The verified configuration used Rogue Amoeba Loopback with Pass-Thru enabled
on each device. Do not add a physical microphone source or monitor during the
first test; accidental loops are loud and confusing.

## 2. Install and build

```bash
xcode-select --install
brew install pjproject
cp config.example.env config.env
./scripts/build_macos_helpers.sh
python3 -m unittest discover -s tests -v
```

Launch `work/DialAgentVoiceControl.app` once. In System Settings, grant the app
the permission macOS requests for controlling keyboard input, then relaunch it.
Rebuilding the app may invalidate that grant.

## 3. Discover your own values

- `DIALAGENT_BIND_IP`: the Mac interface reachable from the phone. Wi-Fi may be
  different from the phone-facing Ethernet/VLAN address.
- `DIALAGENT_PHONE_IP`: the phone's private address.
- `DIALAGENT_PHONE_DEVICE_NAME`: the DNS/SAN name in the phone certificate.
- `DIALAGENT_PHONE_CERT_SHA256`: SHA-256 of that exact leaf certificate.

Do not copy another person's certificate fingerprint or device name. Keep
`config.env` private; the repository ignores it.

The screen feature is optional. To test SIP first, run the receiver directly:

```bash
export DIALAGENT_HELPER_DIR="$PWD/work/bin"
export DIALAGENT_VOICE_CONTROL_APP="$PWD/work/DialAgentVoiceControl.app"
python3 scripts/field_sip_receiver.py \
  --bind-ip 192.168.50.10 --sip-port 5060 --rtp-port 24200 \
  --seconds 600 --auto-answer --demo-auto-answer-confirmed \
  --auto-audio-route --auto-voice
```

## 4. Configure one phone key

Use a spare DSS/speed-dial key and set its target to the Mac's phone-facing IP.
Direct IP call syntax varies by firmware. The provisioning fragment under
`examples/yealink/` is illustrative, not a universal configuration file.

## 5. Test in gates

1. Run unit tests.
2. Run `field_sip_receiver.py --dry-run` and verify the two virtual devices.
3. Place a call with Voice automation disabled and verify SIP/RTP.
4. Enable audio routing; hang up and verify the prior input/output return.
5. Enable Voice with the currently visible task.
6. Add `DIALAGENT_VOICE_THREAD_ID` only after the basic path is stable.
7. Start Push XML last. If it fails, SIP voice should remain independently
   testable.

## Rollback

Hang up, stop the launcher with Control-C, quit the helper app, and select the
original Mac input/output in System Settings. The launcher does not modify PBX
accounts, firmware, or persistent phone provisioning.
