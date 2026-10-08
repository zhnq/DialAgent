import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from face_state import FaceStateSink
from field_sip_receiver import forward_output


class FaceStateTests(unittest.TestCase):
    def test_state_file_records_observation_and_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "face.json"
            sink = FaceStateSink(path)
            self.assertEqual(json.loads(path.read_text())["state"], "idle")
            sink.publish("listening", "voice_start_hotkey_posted", "inferred")
            data = json.loads(path.read_text())
            self.assertEqual(data["state"], "listening")
            self.assertEqual(data["certainty"], "inferred")
            self.assertIn("updated_at", data)
            sink.close()

    def test_sip_drives_preview_without_claiming_internal_voice_state(self):
        state = Mock()
        audio = Mock()
        audio.switch.return_value = True
        voice = Mock()
        voice.start.return_value = True
        forward_output(io.StringIO("Call 0 state changed to CONFIRMED\n"
                                   "Call 0 state changed to DISCONNECTED\n"),
                       audio, voice, state)
        self.assertEqual(state.publish.call_args_list[0].args,
                         ("listening", "voice_start_hotkey_posted", "inferred"))
        self.assertEqual(state.publish.call_args_list[1].args,
                         ("idle", "call_disconnected", "observed"))

    def test_failed_start_is_error_not_listening(self):
        state = Mock()
        audio = Mock()
        audio.switch.return_value = False
        voice = Mock()
        forward_output(io.StringIO("Call 0 state changed to CONFIRMED\n"),
                       audio, voice, state)
        voice.start.assert_not_called()
        state.publish.assert_called_once_with(
            "error", "audio_or_voice_start_failed", "observed")


if __name__ == "__main__":
    unittest.main()
