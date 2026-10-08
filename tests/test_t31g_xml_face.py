from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from t31g_face_preview import HEIGHT, WIDTH, render
from t31g_xml_face import dob_bytes, image_screen_xml


class T31GXmlFaceTests(unittest.TestCase):
    def test_dob_has_two_pixels_per_byte_without_dimensions_prefix(self):
        payload = dob_bytes(render("idle", 0))
        self.assertEqual(len(payload), WIDTH * HEIGHT // 2)

    def test_binary_black_and_white_nibbles(self):
        pixels = bytearray([0, 255]) * (WIDTH * HEIGHT // 2)
        payload = dob_bytes(pixels)
        self.assertEqual(payload, bytes([0x0F]) * (WIDTH * HEIGHT // 2))

    def test_image_screen_embeds_dob_hex_and_refresh(self):
        xml = image_screen_xml(render("speaking", 3),
                               "http://192.168.50.10:8767/face.xml").decode()
        self.assertIn('<YealinkIPPhoneImageScreen', xml)
        self.assertIn('mode="fullscreen"', xml)
        self.assertIn('refresh="2"', xml)
        self.assertIn('height="64" width="132"', xml)
        image_hex = xml.split('width="132">', 1)[1].split('</Image>', 1)[0]
        self.assertEqual(len(image_hex), WIDTH * HEIGHT)

    def test_image_screen_can_auto_exit_after_last_pushed_frame(self):
        xml = image_screen_xml(
            render("idle", 0), None, timeout_seconds=2).decode()
        self.assertIn('Timeout="2"', xml)
        with self.assertRaises(ValueError):
            image_screen_xml(render("idle", 0), None, timeout_seconds=61)

    def test_image_screen_can_bind_dial_to_ok_and_softkey(self):
        xml = image_screen_xml(
            render("idle", 0), None, done_action="Dial:192.168.50.10",
            softkeys=((1, "VOICE", "Dial:192.168.50.10"),),
        ).decode()
        self.assertIn('doneAction="Dial:192.168.50.10"', xml)
        self.assertIn('<SoftKey index="1">', xml)
        self.assertIn('<Label>VOICE</Label>', xml)
        self.assertIn('<URI>Dial:192.168.50.10</URI>', xml)


if __name__ == "__main__":
    unittest.main()
