import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from field_sip_receiver import (auto_answer_allowed, build_command,
                                AutomaticVoice, call_state_event,
                                codex_thread_id, open_codex_thread,
                                parse_accessibility_status,
                                parse_audio_defaults, port, private_ipv4,
                                restore_audio_default,
                                watch_hangup_requests)
from voice_control_client import request as voice_control_request


class FieldSipReceiverTests(unittest.TestCase):
    VOICE_THREAD_ID = "123e4567-e89b-42d3-a456-426614174000"

    def test_fixed_voice_thread_id_must_be_canonical_uuid(self):
        self.assertEqual(codex_thread_id(self.VOICE_THREAD_ID), self.VOICE_THREAD_ID)
        for value in ("new", "../new", "123e4567-e89b-42d3-a456-426614174000?x=1"):
            with self.assertRaises(argparse.ArgumentTypeError):
                codex_thread_id(value)

    def test_fixed_task_uses_codex_deep_link_without_shell(self):
        with patch("field_sip_receiver.subprocess.run") as run:
            open_codex_thread(self.VOICE_THREAD_ID)
        run.assert_called_once_with(
             ["/usr/bin/open", "-b", "com.openai.codex",
             f"codex://threads/{self.VOICE_THREAD_ID}?hostId=local"],
            capture_output=True, text=True, timeout=10, check=True)

    def test_fixed_task_opens_before_voice_and_hangup_does_not_reopen(self):
        order = []
        with patch("field_sip_receiver.require_voice_hotkey_access"), \
             patch("field_sip_receiver.open_codex_thread",
                   side_effect=lambda _id: order.append("open")), \
             patch("field_sip_receiver.time.sleep",
                   side_effect=lambda _seconds: order.append("wait")), \
             patch("field_sip_receiver.post_voice_hotkey_without_activation",
                   side_effect=lambda: order.append("start") or "TOGGLE\tposted"), \
             patch("field_sip_receiver.post_voice_hotkey",
                   side_effect=lambda _socket: order.append("stop") or "TOGGLE\tposted"):
            voice = AutomaticVoice(thread_id=self.VOICE_THREAD_ID)
            self.assertTrue(voice.start())
            self.assertTrue(voice.start())
            voice.stop()
        self.assertEqual(order, ["open", "wait", "start", "stop"])
        self.assertEqual(voice.events[0]["thread_id"], self.VOICE_THREAD_ID)
        self.assertEqual(voice.events[1]["thread_id"], self.VOICE_THREAD_ID)

    def test_fixed_task_navigation_failure_does_not_toggle_other_task(self):
        with patch("field_sip_receiver.require_voice_hotkey_access"), \
             patch("field_sip_receiver.open_codex_thread",
                   side_effect=OSError("link failed")), \
             patch("field_sip_receiver.post_voice_hotkey_without_activation") as start:
            voice = AutomaticVoice(thread_id=self.VOICE_THREAD_ID,
                                   navigation_seconds=0)
            self.assertFalse(voice.start())
        start.assert_not_called()
        self.assertFalse(voice.active)
        self.assertEqual(voice.events[-1]["event"], "voice_start_failed")

    def test_existing_frontmost_task_mode_does_not_navigate(self):
        with patch("field_sip_receiver.require_voice_hotkey_access"), \
             patch("field_sip_receiver.open_codex_thread") as navigate, \
             patch("field_sip_receiver.post_voice_hotkey",
                   return_value="TOGGLE\tposted") as toggle:
            voice = AutomaticVoice()
            self.assertTrue(voice.start())
            voice.stop()
        navigate.assert_not_called()
        self.assertEqual(toggle.call_count, 2)

    def test_command_is_bounded_and_uses_only_virtual_buses(self):
        with tempfile.TemporaryDirectory() as temp:
            args = build_command("192.168.77.1",
                                 {"Phone-to-Codex": 7, "Codex-to-Phone": 8},
                                 "PCMU", 600, Path(temp) / "test.log")
        self.assertIn("--bound-addr=192.168.77.1", args)
        self.assertIn("--ip-addr=192.168.77.1", args)
        self.assertIn("--capture-dev=8", args)
        self.assertIn("--playback-dev=7", args)
        self.assertIn("--max-calls=1", args)
        self.assertIn("--duration=600", args)
        self.assertNotIn("--auto-answer=200", args)
        self.assertFalse(any(value.startswith(("--rec", "--registrar")) for value in args))

    def test_auto_answer_must_be_explicit_in_command(self):
        args = build_command("192.168.50.10",
                             {"Phone-to-Codex": 0, "Codex-to-Phone": 1},
                             "PCMA", 120, Path("test.log"), True)
        self.assertIn("--auto-answer=200", args)
        self.assertIn("--add-codec=PCMA/8000", args)

    def test_auto_answer_requires_explicit_environment_acknowledgement(self):
        self.assertTrue(auto_answer_allowed(False, False, False))
        self.assertTrue(auto_answer_allowed(True, True, False))
        self.assertTrue(auto_answer_allowed(True, False, True))
        self.assertFalse(auto_answer_allowed(True, False, False))

    def test_call_state_events_drive_audio_route(self):
        self.assertEqual(call_state_event(
            "Call 0 state changed to CONFIRMED"), "connected")
        self.assertEqual(call_state_event(
            "State changed from CONFIRMED to DISCONNECTED"), "disconnected")
        self.assertIsNone(call_state_event("Incoming call"))

    def test_parse_audio_defaults(self):
        parsed = parse_audio_defaults(
            "INPUT\t101\tMacBook Air麦克风\n"
            "OUTPUT\t106\tMacBook Air扬声器\n")
        self.assertEqual(parsed["input"]["id"], 101)
        self.assertEqual(parsed["output"]["name"], "MacBook Air扬声器")
        with self.assertRaises(RuntimeError):
            parse_audio_defaults("INPUT\t101\tOnly input\n")

    def test_audio_restore_falls_back_to_device_name_if_id_changed(self):
        with patch("field_sip_receiver.set_audio_default",
                   side_effect=[subprocess.CalledProcessError(2, "set-id"), None]) as set_default:
            restore_audio_default("audio-helper", "input",
                                  {"id": 150, "name": "Xiaomi 开放式耳机"})
        self.assertEqual(set_default.call_args_list[0].args,
                         ("audio-helper", "input", "id", 150))
        self.assertEqual(set_default.call_args_list[1].args,
                         ("audio-helper", "input", "name", "Xiaomi 开放式耳机"))

    def test_parse_accessibility_status(self):
        self.assertEqual(parse_accessibility_status(
            "ACCESSIBILITY\tgranted\nHOTKEY\tControl+Shift+V\n"),
                         "granted")
        self.assertEqual(parse_accessibility_status(
            "ACCESSIBILITY\tdenied\n"), "denied")
        with self.assertRaises(RuntimeError):
            parse_accessibility_status("HOTKEY\tControl+Shift+V\n")

    def test_voice_control_client_uses_local_socket_protocol(self):
        class FakeClient:
            def __init__(self):
                self.responses = [
                    b"ACCESSIBILITY\tgranted\nHOTKEY\tControl+Shift+V\n", b""]
                self.sent = None
                self.connected = None

            def settimeout(self, _timeout):
                pass

            def connect(self, path):
                self.connected = path

            def sendall(self, data):
                self.sent = data

            def recv(self, _size):
                return self.responses.pop(0)

            def close(self):
                pass

        client = FakeClient()
        path = Path("/private/tmp/example.sock")
        with patch("voice_control_client.socket.socket", return_value=client):
            response = voice_control_request("check", path, timeout=2)
        self.assertEqual(client.connected, str(path))
        self.assertEqual(client.sent, b"check\n")
        self.assertIn("ACCESSIBILITY\tgranted", response)

    def test_voice_control_client_rejects_unknown_command(self):
        with self.assertRaises(ValueError):
            voice_control_request("launch-everything")

    def test_phone_default_sip_port_can_be_selected(self):
        args = build_command("192.168.50.10",
                             {"Phone-to-Codex": 0, "Codex-to-Phone": 1},
                             "PCMU", 120, Path("test.log"), False, 5060, 25000)
        self.assertIn("--local-port=5060", args)
        self.assertIn("--rtp-port=25000", args)

    def test_port_bounds(self):
        self.assertEqual(port("5060"), 5060)
        for value in ("0", "1023", "65535", "nope"):
            with self.assertRaises(argparse.ArgumentTypeError):
                port(value)

    def test_only_explicit_private_ipv4_is_accepted(self):
        self.assertEqual(private_ipv4("192.168.50.10"), "192.168.50.10")
        self.assertEqual(private_ipv4("172.16.0.2"), "172.16.0.2")
        self.assertEqual(private_ipv4("192.168.77.1"), "192.168.77.1")
        for value in ("0.0.0.0", "127.0.0.1", "8.8.8.8", "example.com",
                      "192.168.50.10/24", "192.168.50.10:15160"):
            with self.assertRaises(argparse.ArgumentTypeError):
                private_ipv4(value)


if __name__ == "__main__":
    unittest.main()
