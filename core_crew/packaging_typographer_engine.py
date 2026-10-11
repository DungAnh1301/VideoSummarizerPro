"""Nhân Viên 3: Chuyên Viên Typoghraphy & Đóng Gói (Packaging & Typography Engine).

Nhiệm vụ:
- Tạo và căn chỉnh phụ đề ASS chuẩn Hollywood:
  + Font: Arial Bold 48pt, Chữ trắng viền đen dày 3.5px.
  + Vị trí Safe Zone tuyệt đối: MarginV = 420px (ngay phía trên huy hiệu PART).
  + 100% SẠCH SẼ: Giữ nguyên lời thoại gốc của video, TUYỆT ĐỐI KHÔNG chèn bất kỳ dòng
    kêu gọi tiếng Việt hay banner giật gân bừa bãi.
- Burn phụ đề vào video.
- Dán Top Title Card tại ranh giới trên (Y=301) và Bottom Part Badge tại ranh giới dưới (Y=1519).
- Xuất thành phẩm Part hoàn hảo 10/10.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("PackagingTypographerEngine")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from part_splitter_engine import (
    probe_duration_sec,
    get_best_video_encoder,
    PartSplitterEngine,
    _ff_run,
)
from part_splitter_config import load_part_config


class PackagingTypographerEngine:
    """Nhân Viên 3 - Packaging & Typography Engine."""

    @classmethod
    def generate_safe_zone_ass(
        cls,
        scenes: List[Dict[str, Any]],
        cues: List[Dict[str, Any]],
        output_ass_path: str,
        transition_duration: float = 0.35
    ) -> str:
        """Tạo file phụ đề ASS chuẩn Safe Zone từ danh sách scenes và transcript cues."""
        ass_header = """[Script Info]
Title: Hollywood Safe Zone Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3.5,1.5,2,40,40,420,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        def _sec_to_ass_time(sec: float) -> str:
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = int(sec % 60)
            cs = int(round((sec - int(sec)) * 100))
            if cs >= 100:
                cs = 99
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        # Xây dựng timeline tương đối cho video đã ghép
        timeline_events = []
        cur_timeline = 0.0

        for sc_idx, sc in enumerate(scenes):
            shots = sc.get("shots", [])
            for sh in shots:
                sh_st = float(sh["start"])
                sh_en = float(sh["end"])
                sh_dur = max(0.5, sh_en - sh_st)

                # Tìm các cues nằm trong khoảng thời gian của shot này
                matching_cues = []
                for c in cues:
                    c_st = float(c.get("start", 0.0))
                    c_en = float(c.get("end", 0.0))
                    # Nếu cue giao cắt với shot
                    if c_en > sh_st and c_st < sh_en:
                        rel_st = max(0.0, c_st - sh_st)
                        rel_en = min(sh_dur, c_en - sh_st)
                        if rel_en > rel_st + 0.3:
                            matching_cues.append((rel_st, rel_en, c.get("text", "").strip()))

                if matching_cues:
                    for rel_st, rel_en, text in matching_cues:
                        t_start = cur_timeline + rel_st
                        t_end = cur_timeline + rel_en
                        timeline_events.append((t_start, t_end, text))
                else:
                    # Nếu shot có trường desc
                    desc = str(sh.get("desc", "")).strip()
                    if desc:
                        timeline_events.append((cur_timeline, cur_timeline + sh_dur, desc))

                cur_timeline += sh_dur

            # Trừ hao transition duration giữa các scene
            if sc_idx < len(scenes) - 1:
                cur_timeline -= transition_duration

        os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header.strip() + "\n")
            for t_st, t_en, txt in timeline_events:
                # Lọc bỏ dấu ngắt dòng thừa hoặc ký tự lạ
                clean_txt = txt.replace("\n", " ").strip()
                if clean_txt:
                    f.write(f"Dialogue: 0,{_sec_to_ass_time(t_st)},{_sec_to_ass_time(t_en)},Default,,0,0,0,,{clean_txt}\n")

        return output_ass_path

    @classmethod
    def burn_subtitles_and_package(
        cls,
        audio_mastered_video: str,
        ass_path: str,
        master_title: str,
        part_index: int,
        output_final: str,
        part_prefix: str = "PART",
        work_dir: str = "",
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> str:
        """Burn phụ đề sạch và dán Top Title Card, Bottom Part Badge."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        work_dir = work_dir or os.path.dirname(os.path.abspath(output_final))
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(output_final)), exist_ok=True)

        video_for_packaging = audio_mastered_video

        # 1. Burn phụ đề nếu có file ASS
        if ass_path and os.path.isfile(ass_path):
            _log(f"📝 [NHÂN VIÊN 3] Burn phụ đề ASS Safe Zone (Arial Bold 48pt viền nét 3.5px)...")
            subbed_path = os.path.join(work_dir, f"subbed_p{part_index}_{os.path.basename(audio_mastered_video)}")
            enc_name, enc_opts = get_best_video_encoder()
            ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

            cmd_burn = [
                "ffmpeg", "-y",
                "-i", audio_mastered_video,
                "-vf", f"ass='{ass_escaped}'",
                "-c:v", enc_name, *enc_opts,
                "-c:a", "copy",
                subbed_path
            ]
            try:
                _ff_run(cmd_burn, log_fn=None, timeout=600, label=f"burn_sub_p{part_index}")
                video_for_packaging = subbed_path
            except Exception as e:
                _log(f"⚠️ [NHÂN VIÊN 3] Lỗi burn phụ đề: {e}, bỏ qua bước burn sub.")

        # 2. Đóng gói bao bì: Top Card (Y=301) + Bottom Badge (Y=1519)
        _log(f"📦 [NHÂN VIÊN 3] Đóng gói Title Top Card & Huy Hiệu {part_prefix} {part_index}...")
        cfg = load_part_config()
        if config:
            cfg.update(config)

        cfg.update({
            "packaging_anchor_mode": "boundary",
            "competitor_top_card": True,
            "competitor_bottom_badge": True,
            "video_center_height": 1188,
            "top_card_boundary_offset": 0,
            "bottom_badge_boundary_offset": 0
        })

        final_result = PartSplitterEngine.apply_competitor_visual_packaging(
            condensed_video=video_for_packaging,
            output_final=output_final,
            master_title=master_title,
            part_index=part_index,
            part_prefix=part_prefix,
            config=cfg,
            log_fn=log_fn,
            work_dir=work_dir
        )

        _log(f"🎉 [NHÂN VIÊN 3] Xuất xưởng kiệt tác Part {part_index} hoàn chỉnh 100%: {output_final} ({probe_duration_sec(output_final):.1f}s)")
        return final_result
