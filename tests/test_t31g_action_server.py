import ipaddress
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from t31g_action_server import (call_is_active, execute_xml,
                                write_hangup_request)


class T31GActionServerTests(unittest.TestCase):
    def test_execute_response_dials_private_ip(self):
        payload = execute_xml(
            f"Dial:{ipaddress.IPv4Address('192.168.50.10')}")
        self.assertIn(b"<YealinkIPPhoneExecute>", payload)
        self.assertIn(b'<ExecuteItem URI="Dial:192.168.50.10"/>', payload)

    def test_non_idle_call_state_selects_hangup(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({"state": "listening"}), encoding="utf-8")
            self.assertTrue(call_is_active(path))
            path.write_text(json.dumps({"state": "idle"}), encoding="utf-8")
            self.assertFalse(call_is_active(path))

    def test_active_action_notifies_local_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / "hangup.request"
            write_hangup_request(request)
            self.assertTrue(request.read_text(encoding="ascii").strip())


if __name__ == "__main__":
    unittest.main()
