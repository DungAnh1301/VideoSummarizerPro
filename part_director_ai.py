"""Module Đạo Diễn AI Hollywood (PartDirectorAI) chuyên dụng cho Chế độ 2.

Đúc kết toàn bộ tư duy đối thủ triệu view 10/10:
- Đạo diễn 1 (Macro): Phân tách cốt truyện vĩ mô thành N Parts cân đối, tuyến tính thời gian.
- Đạo diễn 2 (Scene & Rhythm): Lọc đúng 4 - 5 phân cảnh cốt lõi (~18s - 22s/cảnh),
  mỗi cảnh gồm 2-4 shots tự nhiên (3.5s - 7s), bắt góc quay (speaker centering X=540)
  và emotion punch zoom (1.18x).
- Đạo diễn 3 (Sound & Transition Master): Khóa câu thoại hoàn chỉnh (Zero Truncation),
  bảo vệ khoảng thở tự nhiên, bố trí Whoosh SFX và Continuous Tension BGM Bed.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("PartDirectorAI")

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from antigravity_processor import AntigravityProcessor
except ImportError:
    AntigravityProcessor = None

try:
    from ai_processor import AIProcessor
except ImportError:
    AIProcessor = None

from core_directors import (
    MacroDirectorAI,
    SceneRhythmDirectorAI,
    AudioTransitionDirectorAI,
)
from core_crew import (
    VisualEditorEngine,
    AudioMasterEngine,
    PackagingTypographerEngine,
)
from audio_boundary_guard import AudioBoundaryGuard


class PartDirectorAI:
    """Đạo Diễn AI Hollywood Toàn Năng cho Chế độ 2."""

    GENRE_PROFILES = {
        "reality": {
            "label": "Thực tế & Drama / Thử lòng (Loyalty Test, Prank)",
            "pacing": "hollywood_linear",
            "scene_count": 5,
            "scene_dur": 20.0,
            "instructions": (
                "Thể loại kịch tính thực tế: Cực kỳ nhạy bén với cảm xúc sốc, ngỡ ngàng. "
                "Cắt tuần tự giữa các bối cảnh (Sân bay -> Xe -> Biệt thự -> Bikini -> Bể bơi). "
                "Cắt giữa người hỏi (Pan Left) và người bị thử lòng (Pan Right). "
                "Khi có câu nói giật gân, PUNCH ZOOM (1.18x) vào mặt đối phương để bắt trọn biểu cảm."
            )
        },
        "storytelling": {
            "label": "Kể chuyện & Podcast / Tự sự",
            "pacing": "balanced_linear",
            "scene_count": 4,
            "scene_dur": 24.0,
            "instructions": (
                "Thể loại kể chuyện/podcast: Nhịp điệu mượt mà, sâu lắng. "
                "Giữ trọn vẹn ý tứ câu chuyện, luân chuyển giữa người dẫn chuyện và các dẫn chứng, tài liệu. "
                "Punch Zoom tại các bước ngoặt bất ngờ của câu chuyện (twist)."
            )
        },
        "movie_recap": {
            "label": "Tóm tắt phim & Review / Phân tích",
            "pacing": "fast_linear",
            "scene_count": 5,
            "scene_dur": 18.0,
            "instructions": (
                "Thể loại tóm tắt phim: Nhịp dồn dập, chuyển cảnh theo từng nhịp hành động. "
                "Loại bỏ 100% cảnh đi bộ hay thừa thãi, chỉ giữ các xung đột gay cấn và nút thắt."
            )
        },
        "gaming": {
            "label": "Gaming & Esports / Streamer Highlight & Thử thách",
            "pacing": "intense_gaming",
            "scene_count": 5,
            "scene_dur": 19.0,
            "instructions": (
                "Thể loại Game & Streamer: Tiết tấu dồn dập, kịch tính cao độ. "
                "Cắt bỏ 100% thời gian đi bộ, nhặt đồ, chạy map nhạt nhẽo; chỉ giữ các pha giao tranh (combat/clutch), "
                "đánh trùm (boss fight), lỗi hài hước (fails/troll), và phản ứng sốc/rage của streamer. "
                "Khi streamer hét lên, ăn mừng hoặc bị tiêu diệt bất ngờ: PUNCH ZOOM (1.18x) vào biểu cảm/khoảnh khắc đó. "
                "Cliffhanger Part: Cắt ngay trước đòn chí mạng của Boss hoặc tình huống sống còn!"
            )
        }
    }

    @classmethod
    def direct_full_series(
        cls,
        transcript_data: List[Dict[str, Any]],
        video_duration_sec: float,
        part_count: int = 6,
        genre: str = "reality",
        master_title: str = "",
        config: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> List[Dict[str, Any]]:
        """Đạo diễn toàn diện trọn bộ series N Parts chuẩn công thức Hollywood 4-5 phân cảnh."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        _log(f"🎬 [HOLLYWOOD DIRECTOR] Bắt đầu đạo diễn bộ video {part_count} Parts (Thể loại: {genre})...")

        # 1. Đạo diễn 1: Lập Master Series Plan
        master_plan = MacroDirectorAI.plan_macro_series(
            cues=transcript_data,
            total_duration=video_duration_sec,
            part_count=part_count,
            genre=genre,
            master_title=master_title,
            config=config,
            log_fn=_log
        )

        parts_specs = master_plan.get("parts", [])
        verified_parts = []

        # 2. Đạo diễn 2 & 3 cho từng Part
        for p_info in parts_specs:
            p_idx = int(p_info.get("part_index", len(verified_parts) + 1))
            storyboard = SceneRhythmDirectorAI.direct_part_scenes(
                part_index=p_idx,
                part_info=p_info,
                cues=transcript_data,
                total_duration=video_duration_sec,
                target_scene_count=5,
                target_part_duration=float(config.get("target_part_duration", 95.0)),
                config=config,
                log_fn=_log
            )

            audio_spec = AudioTransitionDirectorAI.audit_and_prepare_audio_spec(
                part_storyboard=storyboard,
                cues=transcript_data,
                video_total_duration=video_duration_sec,
                temp_dir=os.path.join(ROOT_DIR, "temp", f"director_p{p_idx}"),
                log_fn=_log
            )

            audited_scenes = audio_spec.get("audited_scenes", storyboard.get("scenes", []))

            # Tổng hợp danh sách phẳng các cuts để duy trì tương thích
            flat_cuts = []
            for sc in audited_scenes:
                for sh in sc.get("shots", []):
                    flat_cuts.append(sh)

            verified_parts.append({
                "part_index": p_idx,
                "part_title": p_info.get("part_title", f"Part {p_idx}"),
                "master_title": master_plan.get("series_title", master_title),
                "scenes": audited_scenes,
                "cuts": flat_cuts,
                "audio_spec": audio_spec,
                "narrative_goal": p_info.get("narrative_goal", ""),
                "cliffhanger_desc": p_info.get("cliffhanger_topic", "")
            })

        _log(f"✅ [HOLLYWOOD DIRECTOR] Hoàn tất đạo diễn {len(verified_parts)} Parts chuẩn 10/10!")
        return verified_parts

    @classmethod
    def render_hollywood_part(
        cls,
        source_video: str,
        part_data: Dict[str, Any],
        cues: List[Dict[str, Any]],
        output_final: str,
        master_title: str = "",
        part_index: int = 1,
        part_prefix: str = "PART",
        work_dir: str = "",
        config: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> str:
        """Render một Part hoàn chỉnh từ kịch bản phân cảnh của Hollywood Film Crew."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        work_dir = work_dir or os.path.dirname(os.path.abspath(output_final))
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(output_final)), exist_ok=True)

        scenes = part_data.get("scenes", [])
        if not scenes:
            # Fallback nếu part_data chỉ có cuts
            cuts = part_data.get("cuts", [])
            scenes = [{"scene_id": 1, "name": "Core Scene", "shots": cuts}]

        audio_spec = part_data.get("audio_spec") or AudioTransitionDirectorAI.audit_and_prepare_audio_spec(
            part_storyboard={"part_index": part_index, "scenes": scenes},
            cues=cues,
            video_total_duration=probe_duration_sec(source_video),
            temp_dir=work_dir,
            log_fn=_log
        )

        # 1. Nhân viên 1: Visual Editor (9:16 Canvas blur 2 đầu, video 1188px, speaker centering, punch zoom 1.18x, xfade hblur)
        scenes_merged_path = os.path.join(work_dir, f"_scenes_merged_p{part_index}.mp4")
        merged_video, transition_timestamps = VisualEditorEngine.render_scenes_with_transitions(
            source_video=source_video,
            scenes=scenes,
            output_merged_path=scenes_merged_path,
            work_dir=os.path.join(work_dir, "visual"),
            transition_duration=float(audio_spec.get("transition_duration", 0.35)),
            target_fg_h=int(config.get("video_center_height", 1188)),
            log_fn=_log
        )

        # 2. Nhân viên 2: Audio Master (Whoosh SFX + Continuous Tension BGM bed + -14 LUFS)
        audio_mastered_path = os.path.join(work_dir, f"_audio_mastered_p{part_index}.mp4")
        mastered_video = AudioMasterEngine.master_and_mix_audio(
            video_path=merged_video,
            transition_timestamps=transition_timestamps,
            audio_spec=audio_spec,
            output_path=audio_mastered_path,
            log_fn=_log
        )

        # 3. Nhân viên 3: Packaging & Typography (ASS Subtitles Safe Zone, Top Card Y=301, Bottom Badge Y=1519)
        ass_path = os.path.join(work_dir, f"_safe_zone_p{part_index}.ass")
        PackagingTypographerEngine.generate_safe_zone_ass(
            scenes=scenes,
            cues=cues,
            output_ass_path=ass_path,
            transition_duration=float(audio_spec.get("transition_duration", 0.35)),
            config=config,
            video_path=mastered_video,
            log_fn=_log
        )

        final_out = PackagingTypographerEngine.burn_subtitles_and_package(
            audio_mastered_video=mastered_video,
            ass_path=ass_path,
            master_title=master_title or part_data.get("part_title", f"Part {part_index}"),
            part_index=part_index,
            output_final=output_final,
            part_prefix=part_prefix,
            work_dir=work_dir,
            config=config,
            log_fn=_log
        )

        return final_out
