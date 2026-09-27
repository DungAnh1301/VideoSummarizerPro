import json
import tempfile
import unittest
import os
from unittest.mock import patch

from ai_processor import AIProcessor
from editor_processor import EditorProcessor
from config_manager import normalize_broll_mode, normalize_mode_for_voice
from scene_mapper import (
    expand_if_gap_is_small, expand_window_safely, map_plan_to_highlights,
    map_timesub_story, order_highlights,
    load_highlight_candidates, save_highlight_candidates,
    select_highlight_pool, validate_broll_timeline, exclude_unusable_visual_ranges,
)


class BrollModeTests(unittest.TestCase):
    def setUp(self):
        self.pool = [
            {"id": "H01", "rank": 1, "start": 30.0, "end": 36.0},
            {"id": "H02", "rank": 2, "start": 10.0, "end": 16.0},
            {"id": "H03", "rank": 3, "start": 50.0, "end": 56.0},
            {"id": "H04", "rank": 4, "start": 70.0, "end": 76.0},
        ]

    def test_legacy_modes_are_migrated(self):
        self.assertEqual(normalize_broll_mode("opencv"), "heatmap_ranked")
        self.assertEqual(normalize_broll_mode("timesub_semantic"), "timesub_content")

    def test_obscured_interval_is_removed_from_reserve_pool(self):
        clips = [{
            "id": "H01", "start": 10.0, "end": 20.0,
            "scene_safe_start": 10.0, "scene_safe_end": 20.0,
        }]
        result = exclude_unusable_visual_ranges(
            clips, [{"start_sec": 12.0, "end_sec": 18.0}]
        )
        self.assertEqual([(c["start"], c["end"]) for c in result],
                         [(10.0, 12.0), (18.0, 20.0)])
        self.assertTrue(all(c["scene_safe_start"] == c["start"] for c in result))
        self.assertTrue(all(c["scene_safe_end"] == c["end"] for c in result))

    def test_voice_mode_matrix_rejects_removed_combinations(self):
        self.assertEqual(
            normalize_mode_for_voice("heatmap_chronological", True),
            "timesub_content",
        )
        self.assertEqual(
            normalize_mode_for_voice("timesub_content", False),
            "heatmap_chronological",
        )
        self.assertEqual(normalize_mode_for_voice("heatmap_ranked", True), "heatmap_ranked")
        self.assertEqual(normalize_mode_for_voice("heatmap_ranked", False), "heatmap_ranked")

    def test_ranked_and_chronological_use_same_top_pool(self):
        top = select_highlight_pool(self.pool, 12.0, reserve_ratio=1.0)
        ranked = order_highlights(top, "heatmap_ranked")
        chronological = order_highlights(top, "heatmap_chronological")
        self.assertEqual({c["id"] for c in ranked}, {c["id"] for c in chronological})
        self.assertEqual([c["id"] for c in ranked], ["H01", "H02", "H03", "H04"])
        self.assertEqual([c["id"] for c in chronological], ["H02", "H01", "H03", "H04"])

    def test_pool_preserves_strict_score_order_across_scenes(self):
        windows = [
            {"id": "A1", "scene_id": 1, "start": 0.0, "end": 6.0},
            {"id": "A2", "scene_id": 1, "start": 6.0, "end": 12.0},
            {"id": "B1", "scene_id": 2, "start": 20.0, "end": 26.0},
            {"id": "B2", "scene_id": 2, "start": 26.0, "end": 32.0},
        ]
        selected = select_highlight_pool(windows, 18.0, reserve_ratio=1.0)
        self.assertEqual([item["id"] for item in selected], ["A1", "A2", "B1", "B2"])

    def test_mapping_never_repeats_and_trims_last_clip(self):
        plan = {"segments": [
            {"id": "V01", "visual_refs": ["H01"], "narration": "one two three"},
            {"id": "V02", "visual_refs": ["H02"], "narration": "four five three"},
        ]}
        result = map_plan_to_highlights(plan, self.pool, 10.0, "heatmap_ranked")
        ids = [item["id"] for item in result]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertAlmostEqual(sum(item["end"] - item["start"] for item in result), 10.0)
        self.assertTrue(all(item["end"] > item["start"] for item in result))

    def test_ranked_inserts_near_reserve_before_next_top(self):
        pool = [
            {"id": "H01", "rank": 1, "scene_id": 1, "start": 100.0, "end": 104.0,
             "subtitle": "first confrontation suspect argues"},
            {"id": "H02", "rank": 2, "scene_id": 2, "start": 300.0, "end": 304.0,
             "subtitle": "second arrest police arrive"},
            {"id": "R1", "rank": 3, "scene_id": 1, "start": 104.0, "end": 110.0,
             "subtitle": "suspect argues during first confrontation"},
            {"id": "R2", "rank": 4, "scene_id": 2, "start": 304.0, "end": 310.0,
             "subtitle": "police continue the second arrest"},
        ]
        plan = {"segments": [
            {"id": "V01", "visual_refs": ["H01"],
             "narration": "The suspect argues in the first confrontation."},
            {"id": "V02", "visual_refs": ["H02"],
             "narration": "Police complete the second arrest."},
        ]}
        result = map_plan_to_highlights(plan, pool, 16.0, "heatmap_ranked")
        self.assertEqual([item["id"] for item in result], ["H01", "R1", "H02", "R2"])
        self.assertEqual([item["narration_segment"] for item in result], ["V01", "V01", "V02", "V02"])
        self.assertEqual([item["mapping_role"] for item in result],
                         ["ai_anchor", "reserve", "ai_anchor", "reserve"])

    def test_ai_segment_json_is_voice_only_plus_mapping_plan(self):
        raw = json.dumps({
            "title": "BIG TITLE",
            "segments": [
                {"id": "V01", "visual_refs": ["H01"], "narration": "First line."},
                {"id": "V02", "visual_refs": ["H02"], "narration": "Second line."},
            ],
        })
        script, title, segments = AIProcessor.parse_script_payload_detailed(raw)
        self.assertEqual(script, "First line. Second line.")
        self.assertEqual(title, "BIG TITLE")
        self.assertEqual(segments[1]["visual_refs"], ["H02"])

    def test_visual_only_detector_does_not_touch_normal_transcript(self):
        normal = " ".join(
            "The officer approaches the driver and explains what happened next".split() * 8
        )
        self.assertEqual(AIProcessor.detect_evidence_mode(normal), "transcript")
        self.assertEqual(AIProcessor.detect_evidence_mode("[Music] applause"), "visual_only")

    def test_visual_only_prompt_uses_video_as_authority(self):
        highlights = [{
            "id": "H01", "rank": 1, "start": 12.0, "end": 18.0,
            "subtitle": "", "context_before": "", "context_after": "",
        }]
        _, prompt = AIProcessor.build_dynamic_prompts(
            "energetic", "", narration_mode="heatmap_ranked",
            source_context={"title": "Big Fish Final Catch", "evidence_mode": "visual_only"},
            highlight_candidates=highlights,
        )
        self.assertIn("MODE: VISUAL-ONLY VIDEO REVIEW", prompt)
        self.assertIn("LOCAL VIDEO is the primary and only", prompt)
        self.assertIn("never invent names, relationships, dialogue", prompt)
        self.assertIn("source_start", prompt)

    def test_visual_only_timestamps_survive_json_parser(self):
        raw = json.dumps({"title": "A Visible Fishing Result", "segments": [{
            "id": "V01", "visual_refs": ["H01"], "source_start": 12.0,
            "source_end": 18.0, "source_hint": "angler lifts the fish",
            "narration": "The angler finally lifts the catch into view."
        }]})
        _, _, segments = AIProcessor.parse_script_payload_detailed(raw)
        self.assertEqual(segments[0]["source_start"], 12.0)
        self.assertEqual(segments[0]["source_end"], 18.0)

    def test_ai_single_narration_field_remains_backward_compatible(self):
        script, title, segments = AIProcessor.parse_script_payload_detailed(
            '{"title":"TEST","narration":"A complete voiceover returned without segments."}'
        )
        self.assertEqual(script, "A complete voiceover returned without segments.")
        self.assertEqual(title, "TEST")
        self.assertEqual(segments[0]["id"], "V01")

    def test_prompt_requires_title_length_comparable_to_source(self):
        _, user_prompt = AIProcessor.build_dynamic_prompts(
            "crime", "source", source_context={
                "title": "Hidden Camera Reveals A Terrifying Family Secret Tonight Now"
            }
        )
        self.assertIn("EXACTLY 9 words", user_prompt)
        self.assertIn("preserving the specific", user_prompt)

    def test_word_window_compensates_for_playback_speed_inside_hard_cap(self):
        _, user_prompt = AIProcessor.build_dynamic_prompts(
            "crime", "source", minimum_duration_sec=90, playback_speed=1.1,
            source_context={"title": "A Normal Source Title"},
        )
        self.assertIn("WORD COUNT: 251 to 280 spoken narration words", user_prompt)
        self.assertIn("AIM FOR approximately 264 narration words", user_prompt)

    def test_prompt_states_word_and_character_ranges_for_one_response(self):
        _, user_prompt = AIProcessor.build_dynamic_prompts(
            "crime", "source", minimum_duration_sec=90, playback_speed=1.2,
            source_context={"title": "A Normal Source Title"},
        )
        self.assertIn("WORD COUNT: 266 to 280 spoken narration words", user_prompt)
        self.assertIn("CHARACTER COUNT: 1330 to 1960 characters", user_prompt)
        self.assertIn("exactly ONE API response", user_prompt)

    def test_climax_prompt_treats_heatmap_as_soft_priority(self):
        highlights = [{
            "id": f"H{i:02d}", "rank": i, "start": i * 6.0, "end": i * 6.0 + 6.0,
            "subtitle": f"suspect action evidence number {i}",
            "context_before": "officer approaches", "context_after": "conflict continues",
        } for i in range(1, 61)]
        _, prompt = AIProcessor.build_dynamic_prompts(
            "crime", "source transcript", minimum_duration_sec=90,
            playback_speed=1.2, narration_mode="heatmap_ranked",
            highlight_candidates=highlights,
        )
        self.assertIn("NOT mandatory assignments", prompt)
        self.assertIn("Do not mechanically open with H01", prompt)
        self.assertIn("You may reorder, merge or omit", prompt)
        self.assertIn("first 30-40%", prompt)
        self.assertIn("source_hint is mandatory", prompt)
        self.assertLessEqual(prompt.count('"sequence":'), 48)
        self.assertIn('"id":"H33"', prompt)
        self.assertIn("REQUIRED_PAYOFF", prompt)

    def test_timesub_prompt_spends_most_words_on_escalation_and_climax(self):
        _, prompt = AIProcessor.build_dynamic_prompts(
            "crime", "source transcript", narration_mode="timesub_content",
        )
        self.assertIn("At least 60%", prompt)
        self.assertIn("introduction: no more than 8%", prompt)
        self.assertIn("main climax/strongest confrontation: 35-40%", prompt)
        self.assertIn("always include the real result", prompt)

    def test_short_highlight_plan_is_soft_warning_not_pipeline_failure(self):
        segments = [{
            "id": "V01", "visual_refs": ["H01"], "source_hint": "truck argument",
            "narration": "A short narration about the truck argument.",
        }]
        highlights = [{
            "id": "H01", "subtitle": "truck argument", "context_before": "", "context_after": ""
        }]
        AIProcessor._validate_generated_plan(
            segments, 270, "heatmap_chronological", highlights
        )

    def test_unsupported_number_is_soft_warning_not_pipeline_failure(self):
        segments = [
            {"id": f"V{i:02d}", "visual_refs": [f"H{i:02d}"],
             "source_hint": "show the number now",
             "narration": ("They show the number 911. " if i == 1 else
                           "They show the number now with clear evidence. ") * 15}
            for i in range(1, 7)
        ]
        highlights = [
            {"id": "H01", "subtitle": "show the number now", "context_after": "read 911"},
            *[{"id": f"H{i:02d}", "subtitle": "show the number now",
               "context_before": "", "context_after": ""} for i in range(2, 7)],
        ]
        AIProcessor._validate_generated_plan(
            segments, 200, "heatmap_ranked", highlights
        )

    def test_duplicate_highlight_ref_is_repaired_without_api_retry(self):
        highlights = [
            {"id": "H01", "rank": 1, "subtitle": "crowd applause"},
            {"id": "H02", "rank": 2, "subtitle": "owner hands over the key"},
            {"id": "H03", "rank": 3, "subtitle": "tow truck leaves"},
        ]
        segments = [
            {"id": "V01", "visual_refs": ["H01"], "source_hint": "crowd applause",
             "narration": "The crowd erupts in applause."},
            {"id": "V02", "visual_refs": ["H01"], "source_hint": "hands over key",
             "narration": "The owner hands over the key."},
        ]
        repaired = AIProcessor._repair_highlight_refs(
            segments, "heatmap_ranked", highlights
        )
        self.assertEqual(repaired[0]["visual_refs"], ["H01"])
        self.assertEqual(repaired[1]["visual_refs"], ["H02"])

    def test_empty_review_ref_stays_soft_and_is_not_forced_to_h01(self):
        highlights = [{"id": "H01", "rank": 1, "subtitle": "crowd applause"}]
        segments = [{
            "id": "V01", "visual_refs": [], "source_hint": "owner explains the debt",
            "narration": "The owner explains why the payment dispute began.",
        }]
        repaired = AIProcessor._repair_highlight_refs(
            segments, "heatmap_ranked", highlights
        )
        self.assertEqual(repaired[0]["visual_refs"], [])

    def test_required_payoff_without_ref_gets_semantic_anchor(self):
        highlights = [
            {"id": "H01", "rank": 1, "subtitle": "washing the dashboard"},
            {"id": "H19", "rank": 19, "subtitle": "dad sees the finished car surprise"},
        ]
        segments = [{
            "id": "V08", "beat_type": "payoff", "required": True,
            "visual_refs": [], "source_hint": "dad sees the finished car surprise",
            "visual_subject": "dad reacts to the restored car",
            "narration": "His dad finally sees the restored car and reacts to the surprise.",
        }]
        repaired = AIProcessor._repair_highlight_refs(
            segments, "heatmap_ranked", highlights
        )
        self.assertEqual(repaired[0]["visual_refs"], ["H19"])

    def test_required_opening_replaces_wrong_setup_ref(self):
        highlights = [
            {"id": "H01", "rank": 1, "subtitle": "mechanic cleans the dashboard"},
            {"id": "H07", "rank": 7, "subtitle": "father hears the v8 and freezes in disbelief"},
        ]
        segments = [{
            "id": "V01", "beat_type": "climax", "required": True,
            "visual_refs": ["H01"],
            "source_hint": "father hears the v8 and freezes in disbelief",
            "visual_subject": "father reacts to the restored firebird",
            "narration": "The father hears the V8 and freezes in disbelief.",
        }]
        repaired = AIProcessor._repair_highlight_refs(
            segments, "heatmap_ranked", highlights
        )
        self.assertEqual(repaired[0]["visual_refs"], ["H07"])

    def test_long_banner_title_keeps_all_words_without_ellipsis(self):
        title = (
            "DAUGHTER SECRETLY RESTORES DAD'S DREAM 1986 FIREBIRD "
            "UNLEASHING AN UNFORGETTABLE REACTION AFTER DECADES IN STORAGE"
        )
        line1, line2, font, _size = EditorProcessor._layout_banner_title(title)
        self.assertEqual(" ".join((line1 + " " + line2).split()), title)
        self.assertNotIn("...", line1 + line2)
        self.assertLessEqual(EditorProcessor._banner_text_size(font, line1)[0],
                             EditorProcessor.BANNER_TEXT_MAX_W)
        self.assertLessEqual(EditorProcessor._banner_text_size(font, line2)[0],
                             EditorProcessor.BANNER_TEXT_MAX_W)

    def test_chronological_mapping_repairs_out_of_order_ai_refs(self):
        plan = {"segments": [
            {"id": "V01", "visual_refs": ["H04"], "narration": "opening words"},
            {"id": "V02", "visual_refs": ["H02"], "narration": "ending words"},
        ]}
        result = map_plan_to_highlights(plan, self.pool, 18.0, "heatmap_chronological")
        self.assertEqual([item["start"] for item in result], sorted(item["start"] for item in result))

    def test_chronological_chooses_top_before_sorting_by_time(self):
        pool = [
            {"id": "TOP", "rank": 1, "start": 80.0, "end": 86.0},
            {"id": "SECOND", "rank": 2, "start": 40.0, "end": 46.0},
            {"id": "WEAK_EARLY", "rank": 3, "start": 5.0, "end": 11.0},
        ]
        plan = {"segments": [{"id": "V01", "narration": "complete story words"}]}
        result = map_plan_to_highlights(plan, pool, 10.0, "heatmap_chronological")
        self.assertEqual({item["id"] for item in result}, {"TOP", "SECOND"})
        self.assertEqual([item["start"] for item in result], [40.0, 80.0])

    def test_highlight_pool_is_strict_top_n_not_round_robin_scenes(self):
        from scene_mapper import select_highlight_pool
        ranked = [
            {"scene_id": 1, "start": 0.0, "end": 6.0, "score": 10.0},
            {"scene_id": 1, "start": 6.0, "end": 12.0, "score": 9.0},
            {"scene_id": 2, "start": 20.0, "end": 26.0, "score": 1.0},
        ]
        result = select_highlight_pool(ranked, 12.0, reserve_ratio=1.0)
        self.assertEqual([item["score"] for item in result], [10.0, 9.0, 1.0])

    def test_short_timeline_is_warning_not_pipeline_failure(self):
        validate_broll_timeline(
            [{"id": "A", "start": 0.0, "end": 3.0}],
            6.0, "heatmap_ranked",
        )

    def test_chronological_mapping_obeys_ai_refs_when_present(self):
        pool = [
            {"id": "H01", "rank": 1, "start": 80.0, "end": 86.0},
            {"id": "H02", "rank": 2, "start": 10.0, "end": 16.0},
            {"id": "H03", "rank": 3, "start": 40.0, "end": 46.0},
        ]
        plan = {"segments": [
            {"id": "V01", "visual_refs": ["H02"], "narration": "early event"},
            {"id": "V02", "visual_refs": ["H03"], "narration": "middle event"},
        ]}
        result = map_plan_to_highlights(plan, pool, 10.0, "heatmap_chronological")
        anchors = [item for item in result if item.get("mapping_role") == "ai_anchor"]
        self.assertEqual([item["id"] for item in anchors], ["H02", "H03"])
        self.assertEqual([item["narration_segment"] for item in anchors], ["V01", "V02"])

    def test_saved_highlight_ids_round_trip_without_regeneration(self):
        with tempfile.TemporaryDirectory() as folder:
            clips = [{"id": "H77", "rank": 1, "start": 12.0, "end": 18.0}]
            save_highlight_candidates(folder, clips, "heatmap_ranked")
            loaded = load_highlight_candidates(folder, "heatmap_ranked")
            self.assertEqual(loaded, clips)

    def test_overlong_ai_segments_are_capped_without_second_api_call(self):
        segments = [
            {"id": "V01", "narration": "One short sentence. " * 100},
            {"id": "V02", "narration": "Would you believe this ending? " * 40},
        ]
        capped = AIProcessor._cap_segments_by_words(segments, 120)
        count = sum(len(item["narration"].split()) for item in capped)
        self.assertLessEqual(count, 120)
        self.assertTrue(capped[-1]["narration"].endswith("?"))

    def test_timesub_story_keeps_segment_and_source_order(self):
        plan = {"segments": [
            {"id": "V01", "narration": "one two three"},
            {"id": "V02", "narration": "four five six"},
        ]}
        ranked = [
            {"scene_id": 1, "start": 10.0, "end": 14.0,
             "narration_segment": "V01", "score": 1.0},
            {"scene_id": 4, "start": 70.0, "end": 74.0,
             "narration_segment": "V02", "score": 0.9},
            {"scene_id": 3, "start": 60.0, "end": 66.0, "score": 0.8},
            {"scene_id": 2, "start": 20.0, "end": 26.0, "score": 0.7},
        ]
        result = map_timesub_story(plan, ranked, 16.0)
        segment_order = [clip["narration_segment"] for clip in result]
        self.assertEqual(segment_order, sorted(segment_order))
        for segment_id in ("V01", "V02"):
            starts = [c["start"] for c in result if c["narration_segment"] == segment_id]
            self.assertEqual(starts, sorted(starts))
        self.assertGreaterEqual(sum(c["end"] - c["start"] for c in result), 16.0)

    def test_timesub_story_falls_back_when_anchor_is_missing(self):
        plan = {"segments": [
            {"id": "V01", "narration": "first"},
            {"id": "V02", "narration": "second"},
        ]}
        ranked = [{"scene_id": 1, "start": 10.0, "end": 16.0,
                   "narration_segment": "V01"}]
        result = map_timesub_story(plan, ranked, 6.0)
        self.assertTrue(result)
        self.assertGreaterEqual(sum(c["end"] - c["start"] for c in result), 6.0)

    def test_validation_rejects_chronological_time_jump(self):
        clips = [
            {"scene_id": 1, "start": 20.0, "end": 25.0, "narration_segment": "V01"},
            {"scene_id": 2, "start": 10.0, "end": 16.0, "narration_segment": "V02"},
        ]
        with self.assertRaisesRegex(RuntimeError, "đảo thời gian"):
            validate_broll_timeline(clips, 10.0, "timesub_content")

    def test_validation_rejects_repeated_window(self):
        clips = [
            {"scene_id": 7, "start": 10.0, "end": 16.0},
            {"scene_id": 7, "start": 10.0, "end": 16.0},
        ]
        with self.assertRaisesRegex(RuntimeError, "lặp scene"):
            validate_broll_timeline(clips, 10.0, "heatmap_ranked")

    def test_safe_expansion_uses_side_farthest_from_transition_and_caps_at_half(self):
        clip = {
            "start": 12.0, "end": 18.0,
            "scene_safe_start": 10.0, "scene_safe_end": 30.0,
            "configured_max_duration": 6.0,
        }
        expanded = expand_window_safely(clip, 10.0)
        self.assertEqual((expanded["start"], expanded["end"]), (12.0, 21.0))

        safer_before = dict(clip, scene_safe_start=2.0, scene_safe_end=19.0)
        expanded_before = expand_window_safely(safer_before, 3.0)
        self.assertEqual((expanded_before["start"], expanded_before["end"]), (9.0, 18.0))

    def test_safe_expansion_never_crosses_scene_boundary_or_timeline_clip(self):
        clip = {
            "start": 12.0, "end": 18.0,
            "scene_safe_start": 11.0, "scene_safe_end": 19.0,
            "configured_max_duration": 6.0,
        }
        expanded = expand_window_safely(
            clip, 3.0, occupied=[{"start": 19.0, "end": 25.0}]
        )
        self.assertEqual((expanded["start"], expanded["end"]), (11.0, 19.0))

    def test_small_gap_expands_but_large_gap_uses_trimmed_reserve(self):
        anchor = {
            "id": "H01", "rank": 1, "scene_id": 1,
            "start": 0.0, "end": 6.0,
            "scene_safe_start": 0.0, "scene_safe_end": 9.0,
            "configured_max_duration": 6.0,
        }
        reserve = {
            "id": "R01", "rank": 2, "scene_id": 2,
            "start": 20.0, "end": 26.0,
            "scene_safe_start": 20.0, "scene_safe_end": 29.0,
            "configured_max_duration": 6.0,
        }
        plan = {"segments": [{
            "id": "V01", "visual_refs": ["H01"], "narration": "one complete beat"
        }]}

        small_gap = map_plan_to_highlights(plan, [anchor, reserve], 8.0, "heatmap_ranked")
        self.assertEqual([clip["id"] for clip in small_gap], ["H01"])
        self.assertAlmostEqual(small_gap[0]["end"] - small_gap[0]["start"], 8.0)

        large_gap = map_plan_to_highlights(plan, [anchor, reserve], 10.0, "heatmap_ranked")
        self.assertEqual([clip["id"] for clip in large_gap], ["H01", "R01"])
        self.assertFalse(any(clip.get("expanded_before") or clip.get("expanded_after")
                             for clip in large_gap))
        self.assertAlmostEqual(large_gap[1]["end"] - large_gap[1]["start"], 4.0)
        self.assertAlmostEqual(sum(c["end"] - c["start"] for c in large_gap), 10.0)

    def test_short_reserve_can_expand_only_after_it_is_added(self):
        anchor = {
            "id": "H01", "rank": 1, "scene_id": 1,
            "start": 0.0, "end": 6.0,
            "scene_safe_start": 0.0, "scene_safe_end": 9.0,
            "configured_max_duration": 6.0,
        }
        short_reserve = {
            "id": "R01", "rank": 2, "scene_id": 2,
            "start": 20.0, "end": 22.0,
            "scene_safe_start": 20.0, "scene_safe_end": 25.0,
            "configured_max_duration": 6.0,
        }
        plan = {"segments": [{
            "id": "V01", "visual_refs": ["H01"], "narration": "one complete beat"
        }]}
        result = map_plan_to_highlights(
            plan, [anchor, short_reserve], 10.0, "heatmap_ranked"
        )
        self.assertEqual([clip["id"] for clip in result], ["H01", "R01"])
        self.assertFalse(result[0].get("expanded_before") or result[0].get("expanded_after"))
        self.assertTrue(result[1].get("expanded_before") or result[1].get("expanded_after"))
        self.assertAlmostEqual(sum(c["end"] - c["start"] for c in result), 10.0)

    def test_validation_rejects_overlapping_different_windows(self):
        clips = [
            {"id": "A", "start": 10.0, "end": 17.0},
            {"id": "B", "start": 16.0, "end": 22.0},
        ]
        with self.assertRaisesRegex(RuntimeError, "chồng timeframe"):
            validate_broll_timeline(clips, 10.0, "heatmap_ranked")

    def test_script_length_guard_stops_before_tts(self):
        segments = [{"narration": "far too short"}]
        with self.assertRaisesRegex(RuntimeError, "AI viết quá ngắn"):
            AIProcessor._validate_generated_plan(
                segments, 200, "timesub_content", [], enforce_minimum_words=True
            )

    def test_final_duration_guard_rejects_25_second_ai_video(self):
        with tempfile.TemporaryDirectory() as folder:
            exported = os.path.join(folder, "final_video.mp4")
            with open(exported, "wb") as stream:
                stream.write(b"not-empty")
            with patch.object(EditorProcessor, "probe_media_duration", return_value=24.8):
                with self.assertRaisesRegex(RuntimeError, "Video cuối chỉ dài 24.8s"):
                    EditorProcessor.finalize_exported_video(
                        folder,
                        exported,
                        {"use_ai_voice": True, "video_min_duration_sec": 90.0},
                    )


if __name__ == "__main__":
    unittest.main()
