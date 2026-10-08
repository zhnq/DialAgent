# DialAgent

Turn a SIP desk phone into a physical handset and control surface for a desktop
AI voice app on macOS.

DialAgent receives a direct SIP call from the phone, bridges its RTP audio into
two named virtual audio buses, starts Voice in an existing Codex task, renders
a monochrome animation on a Yealink 132×64 display, and restores the Mac's
previous audio devices after hangup.

> Status: experimental hardware demo. The verified reference path is macOS,
> Yealink SIP-T31G, PJSIP 2.17, and two Loopback virtual devices. It does not
> register with a PBX, expose a public phone number, or record audio.

## What works

- Direct IP SIP call, PCMU/PCMA RTP, automatic or manual answer.
- Call-state-driven macOS input/output switching with best-effort restoration.
- Start/stop Codex Voice with its app shortcut; optionally open one existing
  task by UUID before starting Voice.
- Yealink Push XML with certificate pinning, 132×64 DOB frames, and an original
  monochrome face animation.
- One screen action that dials while idle and hangs up while active.
- Local tests that do not need a phone.

The display state is inferred from SIP and control actions. Codex currently
does not provide this project a live Voice state stream, so the animation must
not be presented as authoritative model telemetry.

## Architecture

```text
Yealink / SIP phone
  ├─ SIP + RTP ───────────────► pjsua receiver on the Mac
  │                              ├─ Phone-to-Codex virtual input
  │                              └─ Codex-to-Phone virtual output
  └─ HTTPS Push XML ◄────────── screen pusher + local action server
                                   │
                                   └─ call lifecycle → Codex Voice shortcut
```

See [docs/architecture.md](docs/architecture.md) for boundaries and tradeoffs.

## Requirements

- macOS 14 or newer on Apple Silicon (the current tested target).
- Python 3.11+ and Xcode Command Line Tools.
- PJSIP/pjsua (`brew install pjproject`).
- Two full-duplex virtual audio devices named `Phone-to-Codex` and
  `Codex-to-Phone`. The verified setup uses Loopback; other devices may work.
- Codex desktop with Voice and its `Control+Shift+V` shortcut.
- For the screen integration: a Yealink firmware that supports Push XML plus
  the phone certificate's SHA-256 fingerprint.

## Quick start

```bash
git clone https://github.com/zhnq/DialAgent.git
cd dialagent
cp config.example.env config.env
./scripts/build_macos_helpers.sh
python3 -m unittest discover -s tests -v
```

Fill `config.env` with your own private addresses and phone identity. Configure
a phone DSS/speed-dial key to call the Mac's wired address, then launch:

```bash
./scripts/start_dialagent.command
```

The first Voice-control launch requires a macOS permission grant. Full setup,
audio wiring, certificate collection, and rollback instructions are in
[docs/setup-macos-yealink.md](docs/setup-macos-yealink.md).

## Safe defaults

- Explicit RFC1918 bind address; never `0.0.0.0`.
- One call at a time and a bounded receiver lifetime.
- No PBX registration, STUN, TCP SIP, audio recording, or bundled credentials.
- Automatic answer requires an explicit command-line acknowledgement.
- Push XML validates a pinned certificate fingerprint despite legacy phone CAs.
- The action server accepts only the configured phone source address.

This is still demo software. Use an isolated lab network when possible and
review your organization's network and telephony policy.

## Scope and roadmap

The first release is deliberately a desktop bridge, not a telephone bot.

- v0.1: reproducible direct-call bridge, audio restoration, Codex adapter,
  Yealink screen, diagnostics.
- v0.2: configurable voice-app adapters and more SIP phones.
- v0.3: vendor-neutral Action URL/URI adapters and richer hardware controls.
- Later: PBX registration and external "call my agent" flows as a separate,
  security-reviewed backend.

## Branding and third-party software

The public project intentionally excludes HAL 9000, Lumon, and other film or
television branding used in private visual experiments. It ships an original
neutral animation. PJSIP, Loopback, BlackHole, Yealink firmware, and Codex are
external dependencies and are not redistributed here. See [NOTICE.md](NOTICE.md).

## Contributing and license

Contributions are welcome; start with [CONTRIBUTING.md](CONTRIBUTING.md).
Security reports should follow [SECURITY.md](SECURITY.md). DialAgent's own code
is available under the [MIT License](LICENSE).
