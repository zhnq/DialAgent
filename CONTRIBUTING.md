# Contributing

Small, testable changes are preferred. Run:

```bash
python3 -m unittest discover -s tests -v
```

Never commit real phone IPs, VLANs, MAC addresses, certificate fingerprints,
Codex task IDs, cookies, credentials, call logs, audio, or proprietary firmware.
Use `192.168.50.0/24` for private-network examples and obvious placeholder
fingerprints. New vendor-specific features need a documented source and should
fail safely when unsupported.

Visual contributions must be original or include a compatible license and
attribution. Do not submit film, television, or corporate logos as defaults.
