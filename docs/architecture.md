# Architecture

DialAgent separates the physical phone, media bridge, desktop voice adapter,
and display integration so each layer can be replaced independently.

## Runtime flow

1. The phone makes a direct IP SIP call to one explicit Mac address.
2. `field_sip_receiver.py` starts `pjsua` with one call, a bounded duration,
   PCMU/PCMA, and two named virtual devices.
3. On `CONFIRMED`, the receiver saves the current system audio defaults,
   selects the virtual buses, optionally opens a configured Codex task, and
   requests the Voice shortcut.
4. On `DISCONNECTED` or process cleanup, it toggles Voice off and restores the
   saved audio devices.
5. `FaceStateSink` publishes conservative local lifecycle states. The Yealink
   pusher converts 132×64 one-bit frames into inline DOB data and sends them
   through certificate-pinned HTTPS Push XML.
6. The resident XML screen's OK action calls a source-restricted local server.
   It returns either a Yealink `Dial:` command or `Key:ON_HOOK`.

## Trust boundaries

- SIP/RTP is unauthenticated direct-IP traffic in v0.1. Bind only to a trusted
  private interface and keep the run window short.
- The screen pusher does not trust the phone's legacy CA. It connects to the
  configured IP while checking the exact leaf certificate fingerprint.
- The Voice helper exposes a mode-0600 Unix socket and relies on an explicit
  macOS accessibility/device-control grant.
- A posted keyboard shortcut is a request, not proof that Voice entered a
  particular state. Logs use `triggered` or `inferred`, never `confirmed`.

## Non-goals for v0.1

- PBX accounts, public PSTN ingress, DTMF authentication, STT/LLM/TTS hosting.
- Recording, transcription, or storing voice content.
- Automatic phone administration or firmware modification.
- A general Codex or OpenAI API client.
