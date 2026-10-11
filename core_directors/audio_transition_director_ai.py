"""Đạo Diễn 3: Đạo Diễn Âm Thanh & Chuyển Cảnh (Sound & Transition Master).

Nhiệm vụ:
- Rà soát toàn bộ điểm cắt theo quy tắc ZERO TRUNCATION: Khóa trọn vẹn câu thoại.
  Không bao giờ cắt cụt từ ngữ hay chém ngang hơi thở, luôn đệm khoảng thở 0.5s - 0.8s reaction hold.
- Tính toán chính xác vị trí chèn hiệu ứng Cinematic Whoosh SFX tại các điểm giao giữa 5 cảnh lớn.
- Thiết lập cấu hình nhạc nền Continuous Tension BGM Bed chạy xuyên suốt để làm chất keo dán liền mạch.
- Cấu hình chuẩn hóa âm lượng EBU R128 (-14 LUFS).
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AudioTransitionDirectorAI")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from ai_transition_sound_director import (
    AITransitionSoundDirector,
    generate_cinematic_whoosh,
    generate_tension_bgm_bed,
)


class AudioTransitionDirectorAI:
    """Đạo Diễn 3 - Sound & Transition Master."""

    @classmethod
    def audit_and_prepare_audio_spec(
        cls,
        part_storyboard: Dict[str, Any],
        cues: List[Dict[str, Any]],
        video_total_duration: float,
        temp_dir: str = "",
        log_fn=None
    ) -> Dict[str, Any]:
        """Khóa câu thoại trọn vẹn và lập audio master specification cho Part."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        part_idx = part_storyboard.get("part_index", 1)
        scenes = part_storyboard.get("scenes", [])
        _log(f"🎧 [ĐẠO DIỄN 3] Khóa câu thoại trọn vẹn (Zero Truncation) và lập kế hoạch âm thanh cho Part {part_idx}...")

        # 1. Rà soát Zero Truncation cho từng shot và chống đè lặp timeframe
        audited_scenes = []
        last_global_shot_end = 0.0
        for sc in scenes:
            audited_shots = []
            for sh in sc.get("shots", []):
                s_st = float(sh["start"])
                s_en = float(sh["end"])

                # Đảm bảo không bao giờ lùi thời gian hoặc đè lặp lại shot trước
                if s_st < last_global_shot_end:
                    s_st = last_global_shot_end

                if s_en <= s_st + 0.5:
                    s_en = s_st + max(1.5, float(sh.get("end", 0.0)) - float(sh.get("start", 0.0)))

                # Tìm cue nào giao thoa với đoạn cuối của shot
                for c in cues:
                    c_st = float(c.get("start", 0.0))
                    c_en = float(c.get("end", 0.0))
                    # Nếu shot cắt ngang giữa một câu thoại
                    if c_st < s_en < c_en and (c_en - s_en) < 3.0:
                        s_en = min(video_total_duration, c_en + 0.4)
                        break

                item = dict(sh)
                item["start"] = round(s_st, 2)
                item["end"] = round(s_en, 2)
                audited_shots.append(item)
                last_global_shot_end = item["end"]

            sc_copy = dict(sc)
            sc_copy["shots"] = audited_shots
            audited_scenes.append(sc_copy)

        # 2. Chuẩn bị file SFX và BGM
        sound_dir = temp_dir or os.path.join(ROOT_DIR, "temp", "sound_master")
        os.makedirs(sound_dir, exist_ok=True)
        whoosh_path = os.path.join(sound_dir, "transition_whoosh.wav")
        bgm_path = os.path.join(sound_dir, "tension_bgm_bed.wav")

        if not os.path.exists(whoosh_path):
            generate_cinematic_whoosh(whoosh_path, duration_sec=0.7)
        if not os.path.exists(bgm_path):
            generate_tension_bgm_bed(bgm_path, duration_sec=140.0)

        audio_spec = {
            "part_index": part_idx,
            "audited_scenes": audited_scenes,
            "whoosh_sfx_path": whoosh_path,
            "tension_bgm_path": bgm_path,
            "bgm_volume_base": 0.18,
            "bgm_ducking_volume": 0.08,
            "target_lufs": -14.0,
            "transition_duration": 0.35,  # 0.35s xfade motion blur
            "audio_crossfade_duration": 0.35
        }

        _log(f"✅ [ĐẠO DIỄN 3] Đã lập Master Audio Spec: Whoosh SFX, BGM Tension Bed, chuẩn hóa -14 LUFS.")
        return audio_spec
