import importlib.util
import struct
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/t31g_face_preview.py"
spec = importlib.util.spec_from_file_location("t31g_face_preview", SCRIPT)
face = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(face)


class FacePreviewTests(unittest.TestCase):
    def test_all_frames_are_binary_native_size(self):
        for state in face.STATES:
            for phase in range(face.FRAMES):
                pixels = face.render(state, phase)
                self.assertEqual(len(pixels), 132 * 64)
                self.assertLessEqual(set(pixels), {0, 255})
                self.assertGreater(pixels.count(255), 0)

    def test_png_header_declares_native_size(self):
        png = face.png_bytes(face.render("idle", 0))
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (132, 64))

    def test_blink_and_speech_change_visible_pixels(self):
        self.assertNotEqual(face.render("idle", 0), face.render("idle", 7))
        self.assertEqual(len({bytes(face.render("idle", phase))
                              for phase in range(face.FRAMES)}), face.FRAMES)
        self.assertNotEqual(face.render("speaking", 0), face.render("speaking", 3))
        self.assertNotEqual(face.render("listening", 3), face.render("thinking", 3))
        self.assertGreaterEqual(
            len({bytes(face.render("listening", phase)) for phase in range(face.FRAMES)}),
            6,
        )

    def test_week_meter_hugs_bottom_edge_and_is_adjustable(self):
        empty = face.render("idle", 0, week_remaining=0)
        full = face.render("idle", 0, week_remaining=100)
        self.assertNotEqual(empty, full)
        self.assertFalse(any(empty[y * face.WIDTH + x] for y in range(0, 17)
                             for x in range(face.WIDTH)))
        self.assertTrue(any(empty[y * face.WIDTH + x] for y in range(49, 63)
                            for x in range(face.WIDTH)))
        self.assertEqual(empty[61 * face.WIDTH + 3], 0)
        self.assertEqual(full[61 * face.WIDTH + 3], 255)
        self.assertEqual(empty[28 * face.WIDTH + 37], full[28 * face.WIDTH + 37])
        self.assertNotIn("5H", face.preview_html(8))
        self.assertIn("底边 WEEK 8%", face.preview_html(8))
        with self.assertRaises(ValueError):
            face.render("idle", 0, week_remaining=101)

    def test_light_frame_is_exact_inverse(self):
        dark = face.render("idle", 0, week_remaining=8)
        light = face.render("idle", 0, week_remaining=8, inverted=True)
        self.assertEqual(len(dark), len(light))
        self.assertTrue(all(a + b == 255 for a, b in zip(dark, light)))


if __name__ == "__main__":
    unittest.main()
