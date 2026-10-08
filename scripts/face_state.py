"""Small, honest state bridge between the SIP demo and the face preview.

These are observations of our own call/control path, not Codex Voice events.
"""

import json
import os
from pathlib import Path
import tempfile
from datetime import datetime, timezone
import threading

STATES = frozenset(("idle", "listening", "thinking", "speaking", "completed", "error"))


class FaceStateSink:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.current = None
        self.stopped = threading.Event()
        self.publish("idle", "receiver_ready", "observed")
        self.heartbeat = threading.Thread(target=self._heartbeat, daemon=True)
        self.heartbeat.start()

    def publish(self, state, reason, certainty):
        if state not in STATES:
            raise ValueError(f"unknown face state: {state}")
        if certainty not in ("observed", "inferred"):
            raise ValueError(f"unknown certainty: {certainty}")
        with self.lock:
            self.current = (state, reason, certainty)
            self._write()

    def _write(self):
        state, reason, certainty = self.current
        payload = {"state": state, "reason": reason, "certainty": certainty,
                   "updated_at": datetime.now(timezone.utc).isoformat()}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".face-state-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, ensure_ascii=False)
                output.write("\n")
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _heartbeat(self):
        while not self.stopped.wait(5):
            with self.lock:
                self._write()

    def close(self):
        self.stopped.set()
        self.heartbeat.join(timeout=1)
