import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from part_processor import PartProcessor


class PartProcessorTests(unittest.TestCase):
    def test_two_cuts_make_three_contiguous_parts(self):
        self.assertEqual(
            PartProcessor.validate_ranges(100, [30, 70]),
            [(0.0, 30.0), (30.0, 70.0), (70.0, 100.0)],
        )

    def test_invalid_cut_order_is_rejected(self):
        with self.assertRaises(ValueError):
            PartProcessor.validate_ranges(100, [70, 30])

    def test_cut_outside_video_is_rejected(self):
        with self.assertRaises(ValueError):
            PartProcessor.validate_ranges(100, [100])

    def test_srt_is_clipped_and_shifted_to_part_time(self):
        source = (
            "1\n00:00:08,000 --> 00:00:12,000\nA\n\n"
            "2\n00:00:15,000 --> 00:00:18,000\nB\n\n"
            "3\n00:00:25,000 --> 00:00:27,000\nC\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "all.srt"
            dst = Path(folder) / "part.srt"
            src.write_text(source, encoding="utf-8")
            rendered = PartProcessor.split_srt(str(src), str(dst), 10.0, 20.0)
            self.assertIn("00:00:00,000 --> 00:00:02,000", rendered)
            self.assertIn("00:00:05,000 --> 00:00:08,000", rendered)
            self.assertNotIn("C", rendered)

    def test_gemini_preview_is_split_with_each_part(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(
            PartProcessor, "_cut"
        ) as cut:
            parts = PartProcessor.split_sources(
                "source_hd.mp4", "source_low.mp4", 100.0, [40.0], folder,
                source_title="Long source title", source_gemini="gemini_preview.mp4",
            )
            self.assertEqual(2, len(parts))
            self.assertTrue(parts[0]["gemini_preview_path"].endswith("gemini_preview.mp4"))
            gemini_calls = [call for call in cut.call_args_list
                            if call.args[0] == "gemini_preview.mp4"]
            self.assertEqual([(0.0, 40.0), (40.0, 100.0)],
                             [(call.args[2], call.args[3]) for call in gemini_calls])


if __name__ == "__main__":
    unittest.main()
