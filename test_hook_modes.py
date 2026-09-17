import json
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from antigravity_processor import AntigravityProcessor
from ai_processor import AIProcessor, DYNAMIC_WRITER_SYSTEM_PROMPT, PROMPT_BUILDER_VERSION
from config_manager import normalize_config, normalize_hook_mode


class HookModeTests(unittest.TestCase):
    def test_youtube_without_caption_returns_visual_only_without_whisper(self):
        class MissingCaptionAPI:
            def fetch(self, *_args, **_kwargs):
                raise RuntimeError("captions disabled")

        fake_module = types.SimpleNamespace(YouTubeTranscriptApi=MissingCaptionAPI)
        with tempfile.TemporaryDirectory() as folder, patch.dict(
            sys.modules, {"youtube_transcript_api": fake_module}
        ):
            result = AIProcessor.transcribe_audio(
                audio_path=None,
                video_url="https://www.youtube.com/watch?v=abcdefghijk",
                output_dir=folder,
                youtube_caption_only=True,
            )
            self.assertEqual(result, "")
            self.assertTrue(os.path.isfile(os.path.join(folder, "transcript_sub.srt")))
            self.assertEqual(os.path.getsize(os.path.join(folder, "transcript_sub.srt")), 0)

    def test_legacy_custom_hook_migrates_to_ai(self):
        self.assertEqual(normalize_hook_mode(None, True), "ai")
        self.assertEqual(normalize_config({"use_custom_hook": True})["hook_mode"], "ai")

    def test_all_three_hook_modes_survive_normalization(self):
        self.assertEqual(normalize_hook_mode("native"), "native")
        self.assertEqual(normalize_hook_mode("gemini"), "gemini")
        self.assertEqual(normalize_hook_mode("ai"), "ai")

    def test_gemini_hook_json_is_unwrapped_and_clamped(self):
        raw = '```json\n{"start_sec": 98, "end_sec": 108, "reason": "impact", "confidence": 1.4}\n```'
        result = AntigravityProcessor.parse_hook_selection(raw, 100, 9)
        self.assertEqual(result["start_sec"], 91.0)
        self.assertEqual(result["end_sec"], 100.0)
        self.assertEqual(result["confidence"], 1.0)

    def test_hook_is_read_from_same_script_response(self):
        raw = json.dumps({
            "title": "A title",
            "hook_selection": {"start_sec": 44, "end_sec": 52, "reason": "impact", "confidence": .9},
            "segments": [{"id": "V01", "narration": "A valid narration segment."}],
        })
        hook = AIProcessor.extract_hook_selection(raw, video_duration=100, hook_duration=8)
        self.assertEqual(hook["start_sec"], 44.0)
        self.assertEqual(hook["end_sec"], 52.0)

    def test_gemini_prompts_reject_obscured_visuals(self):
        _, user_prompt = AIProcessor.build_dynamic_prompts(
            "", "A confrontation happens and the result is visible.",
            source_context={
                "title": "Visible confrontation result",
                "gemini_hook_duration": 8,
                "video_duration": 120,
            },
            narration_mode="heatmap_ranked",
            highlight_candidates=[{
                "id": "H01", "start": 10, "end": 16,
                "subtitle": "the confrontation begins",
            }],
        )
        self.assertEqual(PROMPT_BUILDER_VERSION, "voice-matrix-v22-multimarket")
        self.assertIn("VISUAL USABILITY OVERRIDES", DYNAMIC_WRITER_SYSTEM_PROMPT)
        self.assertIn("Heatmap rank never overrides visual", user_prompt)
        self.assertIn("nearest clear moment", user_prompt)
        self.assertIn("censor blur", user_prompt)

    def test_unusable_visual_ranges_are_parsed_and_merged(self):
        raw = json.dumps({
            "unusable_visual_ranges": [
                {"start_sec": 12, "end_sec": 15, "reason": "giant text"},
                {"start_sec": 15.1, "end_sec": 18, "reason": "channel graphic"},
                {"start_sec": 200, "end_sec": 210, "reason": "outside video"},
            ]
        })
        ranges = AIProcessor.extract_unusable_visual_ranges(raw, video_duration=120)
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]["start_sec"], 12.0)
        self.assertEqual(ranges[0]["end_sec"], 18.0)

    def test_parse_script_payload_repairs_missing_script_from_segments(self):
        raw = json.dumps({
            "title": "A Great Title",
            "segments": [
                {"id": "V01", "narration": "First sentence of narration."},
                {"id": "V02", "narration": "Second sentence of narration."},
            ]
        })
        script, title = AIProcessor.parse_script_payload(raw)
        self.assertEqual(title, "A Great Title")
        self.assertIn("First sentence of narration.", script)
        self.assertIn("Second sentence of narration.", script)

    def test_storyboard_generation_and_manifest(self):
        import cv2
        import numpy as np
        from scene_mapper import generate_visual_storyboards

        with tempfile.TemporaryDirectory() as folder:
            video_file = os.path.join(folder, "dummy_360p.mp4")
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            out = cv2.VideoWriter(video_file, fourcc, 30.0, (640, 360))
            for i in range(90):
                frame = np.full((360, 640, 3), (i * 2) % 255, dtype=np.uint8)
                out.write(frame)
            out.release()

            candidates = [{"id": "H01", "start": 1.0, "end": 2.0}]
            sheets = generate_visual_storyboards(
                video_file, folder, candidates=candidates, fps=1.0, grid=(2, 2), tile_size=(160, 90), max_frames=600
            )
            self.assertTrue(len(sheets) >= 1)
            self.assertTrue(os.path.isfile(sheets[0]))
            manifest_path = os.path.join(folder, "storyboard_manifest.json")
            self.assertTrue(os.path.isfile(manifest_path))
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            self.assertEqual(manifest["total_sheets"], len(sheets))
            self.assertEqual(manifest["fps"], 1.0)
            self.assertLessEqual(manifest["total_sampled_frames"], 600)


if __name__ == "__main__":
    unittest.main()
