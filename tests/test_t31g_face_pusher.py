import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from t31g_face_pusher import current_state, push_frame, push_frame_resilient


class FakeResponse:
    def __init__(self, status):
        self.status = status

    def read(self):
        return b""


class FakeConnection:
    def __init__(self, status=200):
        self.status = status
        self.target_ip = "192.0.2.20"
        self.request_args = None

    def request(self, *args, **kwargs):
        self.request_args = (args, kwargs)

    def getresponse(self):
        return FakeResponse(self.status)


class T31GFacePusherTests(unittest.TestCase):
    def test_current_state_reads_valid_receiver_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(json.dumps({
                "state": "listening", "certainty": "inferred"
            }), encoding="utf-8")
            self.assertEqual(current_state(path, "idle"),
                             ("listening", "inferred"))

    def test_current_state_falls_back_for_invalid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("not-json", encoding="utf-8")
            self.assertEqual(current_state(path, "idle"), ("idle", "static"))

    def test_push_frame_uses_documented_endpoint_without_refresh(self):
        connection = FakeConnection()
        push_frame(connection, b"<YealinkIPPhoneImageScreen />")
        args, kwargs = connection.request_args
        self.assertEqual(args[:2], ("POST", "/servlet?push=xml"))
        self.assertTrue(kwargs["body"].startswith(b"xml="))
        self.assertEqual(kwargs["headers"]["Connection"], "keep-alive")

    def test_push_frame_rejects_http_error(self):
        with self.assertRaises(RuntimeError):
            push_frame(FakeConnection(status=403), b"<xml />")

    def test_resilient_push_closes_connection_and_reports_retry(self):
        connection = FakeConnection(status=403)
        connection.close = Mock()
        error = push_frame_resilient(connection, b"<xml />")
        self.assertIn("HTTP 403", error)
        connection.close.assert_called_once_with()

    def test_resilient_push_returns_none_on_success(self):
        connection = FakeConnection()
        connection.close = Mock()
        self.assertIsNone(push_frame_resilient(connection, b"<xml />"))
        connection.close.assert_not_called()

if __name__ == "__main__":
    unittest.main()
