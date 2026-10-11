"""Nhân Viên 1: Chuyên Gia Dựng Hình Ảnh (Lead Visual Editor Engine).

Nhiệm vụ:
- Thiết lập khung hình chuẩn 9:16 Canvas (1080x1920), nền blur 2 đầu (boxblur 24:6), video trung tâm 1188px.
- Speaker Centering: Bắt chính xác mặt người nói vào tâm giữa (X=540):
  + pan="left": x_off = 0.28 * max_x_off
  + pan="right": x_off = 0.52 * max_x_off
  + pan="center": x_off = 0.50 * max_x_off
- Emotion Punch Zoom (1.18x): Phóng to bắt cảm xúc trên nền đã blur, giữ nguyên bố cục thẩm mỹ.
- Visual Transition: Chèn kỹ xảo Motion Blur Whip Transition (xfade=hblur, d=0.35s) giữa các cảnh.
- Trả về video ghép các cảnh cùng danh sách mốc thời gian chuyển cảnh (transition_timestamps).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VisualEditorEngine")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from part_splitter_engine import (
    probe_duration_sec,
    probe_video_dimensions,
    get_best_video_encoder,
    _ff_run,
)


class VisualEditorEngine:
    """Nhân Viên 1 - Lead Visual Editor Engine."""

    @classmethod
    def render_scenes_with_transitions(
        cls,
        source_video: str,
        scenes: List[Dict[str, Any]],
        output_merged_path: str,
        work_dir: str,
        transition_duration: float = 0.35,
        target_fg_h: int = 1188,
        log_fn=None
    ) -> Tuple[str, List[float]]:
        """Render từng phân cảnh và nối lại bằng kỹ xảo xfade=hblur."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(output_merged_path)), exist_ok=True)

        src_w, src_h = probe_video_dimensions(source_video)
        y_top_boundary = (1920 - target_fg_h) // 2

        scale_w = int(round(float(target_fg_h) * float(src_w) / max(1.0, float(src_h))))
        scale_w = scale_w + (scale_w % 2)
        max_x_off = max(0, scale_w - 1080)

        x_left = int(max_x_off * 0.28)
        x_right = int(max_x_off * 0.52)
        x_center = int(max_x_off * 0.50)

        enc_name, enc_opts = get_best_video_encoder()
        scene_files = []
        scene_durations = []

        _log(f"✂️ [NHÂN VIÊN 1] Bắt đầu render {len(scenes)} phân cảnh chuẩn 9:16 Blur + Speaker Centering...")

        # 1. Render từng cảnh
        for sc_idx, sc in enumerate(scenes):
            sc_name = sc.get("name", f"Scene {sc_idx + 1}")
            shots = sc.get("shots", [])
            if not shots:
                continue

            seg_files = []
            for sh_idx, sh in enumerate(shots):
                start = float(sh["start"])
                end = float(sh["end"])
                dur = max(0.5, end - start)
                pan = str(sh.get("pan", "center")).lower().strip()
                punch = bool(sh.get("punch", False) or sh.get("punch_in", False))

                x_off = x_left if pan == "left" else (x_right if pan == "right" else x_center)

                if punch:
                    p_scale_w = int(scale_w * 1.18) + (int(scale_w * 1.18) % 2)
                    p_scale_h = int(target_fg_h * 1.18) + (int(target_fg_h * 1.18) % 2)
                    px_off = int(x_off * 1.18 + (p_scale_w - scale_w) / 4)
                    py_off = int((p_scale_h - target_fg_h) / 2)
                    fg_filter = f"scale={p_scale_w}:{p_scale_h},crop=1080:{target_fg_h}:{px_off}:{py_off}"
                else:
                    fg_filter = f"scale={scale_w}:{target_fg_h},crop=1080:{target_fg_h}:{x_off}:0"

                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:6[bg];"
                    f"[fg_in]{fg_filter}[fg];"
                    f"[bg][fg]overlay=0:{y_top_boundary}[vout]"
                )

                fade_d = min(0.04, dur * 0.05)
                af = f"afade=t=in:ss=0:d={fade_d:.3f},afade=t=out:st={dur - fade_d:.3f}:d={fade_d:.3f}"

                seg_ts = os.path.join(work_dir, f"sc_{sc_idx:02d}_shot_{sh_idx:02d}.ts")
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start:.3f}",
                    "-t", f"{dur:.3f}",
                    "-i", source_video,
                    "-filter_complex", vf,
                    "-af", af,
                    "-map", "[vout]", "-map", "0:a",
                    "-c:v", enc_name, *enc_opts,
                    "-c:a", "aac", "-b:a", "192k",
                    "-r", "30",
                    "-f", "mpegts",
                    seg_ts
                ]
                _ff_run(cmd, log_fn=None, timeout=60, label=f"sh_{sc_idx}_{sh_idx}")
                seg_files.append(seg_ts)

            if not seg_files:
                continue

            # Ghép các shots của cảnh này thành video cảnh
            concat_list = os.path.join(work_dir, f"sc_{sc_idx:02d}_concat.txt")
            with open(concat_list, "w", encoding="utf-8") as f:
                for sf in seg_files:
                    f.write(f"file '{sf}'\n")

            sc_video = os.path.join(work_dir, f"scene_{sc_idx + 1}.mp4")
            concat_cmd = [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_list,
                "-c", "copy",
                sc_video
            ]
            _ff_run(concat_cmd, log_fn=None, timeout=120, label=f"sc_{sc_idx}")
            sc_dur = probe_duration_sec(sc_video)
            scene_files.append(sc_video)
            scene_durations.append(sc_dur)
            _log(f"   ✓ Cảnh {sc_idx + 1}/{len(scenes)} ({sc_dur:.1f}s): {sc_name}")

        if not scene_files:
            raise RuntimeError("Không có phân cảnh nào được render thành công!")

        if len(scene_files) == 1:
            shutil.copyfile(scene_files[0], output_merged_path)
            return output_merged_path, []

        # 2. Ghép các cảnh bằng Kỹ Xảo Motion Blur (xfade=hblur) & Audio acrossfade
        _log(f"🌪️ [NHÂN VIÊN 1] Áp dụng Kỹ Xảo Motion Blur Whip Transition (xfade=hblur {transition_duration}s) liên hoàn {len(scene_files)} cảnh...")
        inputs_cmd = []
        for sf in scene_files:
            inputs_cmd.extend(["-i", sf])

        filter_v_chains = []
        filter_a_chains = []
        transition_timestamps = []

        cur_offset = scene_durations[0] - transition_duration
        transition_timestamps.append(cur_offset)
        filter_v_chains.append(f"[0:v][1:v]xfade=transition=hblur:duration={transition_duration}:offset={cur_offset:.2f}[v01]")
        filter_a_chains.append(f"[0:a][1:a]acrossfade=d={transition_duration}:c1=tri:c2=tri[a01]")

        last_v = "v01"
        last_a = "a01"

        for i in range(2, len(scene_files)):
            cur_offset = cur_offset + scene_durations[i - 1] - transition_duration
            transition_timestamps.append(cur_offset)
            next_v = f"v0{i}"
            next_a = f"a0{i}"
            filter_v_chains.append(f"[{last_v}][{i}:v]xfade=transition=hblur:duration={transition_duration}:offset={cur_offset:.2f}[{next_v}]")
            filter_a_chains.append(f"[{last_a}][{i}:a]acrossfade=d={transition_duration}:c1=tri:c2=tri[{next_a}]")
            last_v = next_v
            last_a = next_a

        full_chain = ";".join(filter_v_chains + filter_a_chains)
        cmd_merge = [
            "ffmpeg", "-y",
            *inputs_cmd,
            "-filter_complex", full_chain,
            "-map", f"[{last_v}]", "-map", f"[{last_a}]",
            "-c:v", enc_name, *enc_opts,
            "-c:a", "aac", "-b:a", "192k",
            output_merged_path
        ]
        _ff_run(cmd_merge, log_fn=log_fn, timeout=600, label="merge_xfade")
        _log(f"✅ [NHÂN VIÊN 1] Đã hoàn thành ghép nối {len(scene_files)} cảnh: {output_merged_path} ({probe_duration_sec(output_merged_path):.1f}s)")
        return output_merged_path, transition_timestamps
