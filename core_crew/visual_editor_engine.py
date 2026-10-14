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


try:
    import cv2
except ImportError:
    cv2 = None


class VisualEditorEngine:
    """Nhân Viên 1 - Lead Visual Editor & AI Cinematographer (Đạo Diễn Hình Ảnh)."""

    # Bỏ hoàn toàn quét khuôn mặt CPU/Haar/YuNet - 100% tuân thủ chỉ định từ Đạo diễn AI CLI

    @classmethod
    def plan_scene_camera_shots(
        cls,
        shots: List[Dict[str, Any]],
        scale_w: int,
        target_fg_h: int
    ) -> List[Dict[str, Any]]:
        """Đạo Diễn Hình Ảnh Tự Động (Pure AI Command Driven - Tốc độ 0.0001s):
        - BỎ HOÀN TOÀN QUÉT MẶT CPU: Đọc trực tiếp quyết định nghệ thuật từ Kịch bản Đạo diễn AI CLI.
        - Căn chỉnh góc máy ngang (u_focus):
          + "left" / "speaker_left": u=0.38 (bắt trọn nhân vật/hành động bên trái)
          + "right" / "speaker_right": u=0.62 (bắt trọn nhân vật/hành động bên phải)
          + "center" / "wide_zoom_out": u=0.50 (trung tâm cân đối)
        - ĐỘNG CƠ RANDOM SCALE DỌC (Vertical Anamorphic Stretch 110% ~ 130%):
          + Mỗi shot ngẫu nhiên nhận tỉ lệ kéo giãn dọc v_scale trong khoảng [1.10, 1.30].
          + Đồng thời GIẢM mức Zoom In tương ứng (z_eff = max(0.80, base_zoom / v_scale)) để chiều cao
            sau khi scale luôn vừa khít 100% với khung hình trung tâm hiện tại (target_fg_h).
        """
        import random
        planned: List[Dict[str, Any]] = []

        for sh_idx, sh in enumerate(shots):
            role = str(sh.get("camera_role") or "").lower().strip()
            pan_hint = str(sh.get("pan", "center")).lower().strip()
            punch_flag = bool(sh.get("punch", False) or sh.get("punch_in", False))

            # 1. Tọa độ ngang theo chỉ định góc máy của Đạo diễn AI
            if pan_hint == "left" or role == "speaker_left":
                u_focus = 0.38
            elif pan_hint == "right" or role == "speaker_right":
                u_focus = 0.62
            elif role == "wide_zoom_out":
                u_focus = 0.50
            else:
                u_focus = 0.44 if (sh_idx % 2 == 1) else 0.56

            v_focus = 0.38  # Tỉ lệ vàng 38% từ đỉnh

            # 2. Random Scale Dọc trong khoảng [1.10, 1.30] (110% ~ 130%)
            v_scale = round(random.uniform(1.10, 1.30), 3)

            # 3. Zoom In cơ bản theo chỉ định của AI
            if "zoom_factor" in sh and sh["zoom_factor"] is not None:
                base_zoom = float(sh["zoom_factor"])
            elif role == "wide_zoom_out":
                base_zoom = 1.00
            elif role == "closeup_zoom_in" or punch_flag:
                base_zoom = 1.16
            elif role == "speaker_left" or role == "speaker_right":
                base_zoom = 1.07
            else:
                base_zoom = 1.14 if punch_flag else (1.08 if sh_idx % 2 == 1 else 1.00)

            # 4. Giảm Zoom In để bù cho Scale Dọc tăng, làm cho chiều cao tổng thể vừa khít khung hình
            z_eff = max(0.80, base_zoom / v_scale)

            # Tính kích thước scale thực tế:
            z_w = int(round(scale_w * z_eff))
            z_w = z_w + (z_w % 2)
            if z_w < 1080:
                z_w = 1080  # Tránh hụt viền đen khi crop ngang 1080

            z_h = int(round(target_fg_h * z_eff * v_scale))
            z_h = z_h + (z_h % 2)
            if z_h < target_fg_h:
                z_h = target_fg_h  # Vừa khít khung hình trung tâm

            # Tọa độ Crop X & Y
            desired_x = int(round(u_focus * z_w - 540.0))
            x_off = max(0, min(max(0, z_w - 1080), desired_x))
            x_off = x_off - (x_off % 2)

            desired_y = int(round(v_focus * z_h - target_fg_h * 0.38))
            y_off = max(0, min(max(0, z_h - target_fg_h), desired_y))
            y_off = y_off - (y_off % 2)

            sh_plan = dict(sh)
            sh_plan["u_focus"] = round(u_focus, 3)
            sh_plan["v_focus"] = round(v_focus, 3)
            sh_plan["zoom_factor"] = round(base_zoom, 2)
            sh_plan["v_scale"] = v_scale
            sh_plan["z_w"] = z_w
            sh_plan["z_h"] = z_h
            sh_plan["x_off"] = x_off
            sh_plan["y_off"] = y_off
            planned.append(sh_plan)

        return planned

    @classmethod
    def render_scenes_with_transitions(
        cls,
        source_video: str,
        scenes: List[Dict[str, Any]],
        output_merged_path: str,
        work_dir: str,
        transition_duration: float = 0.35,
        target_fg_h: int = 1188,
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> Tuple[str, List[float]]:
        """Render từng phân cảnh với Đạo Diễn Hình Ảnh (Zoom In / Zoom Out / Speaker Shift) và nối bằng xfade=hblur."""
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

        # Không cần mở VideoCapture quét khuôn mặt CPU - 100% tuân thủ chỉ định từ Đạo diễn AI
        # Chuẩn bị bộ lọc màu CapCut nếu có cấu hình chỉnh màu
        color_fx = ""
        try:
            from capcut_filters import build_adjust_filters
            fx_cand = build_adjust_filters(config or {}, allow_blur_fx=False)
            if fx_cand and fx_cand != "eq=contrast=1.000:brightness=0.000:saturation=1.000":
                color_fx = fx_cand
                look_name = (config or {}).get("color_look", "Tùy chỉnh")
                _log(f"🎨 [NHÂN VIÊN 1] Áp dụng bảng màu CapCut Studio ({look_name}) cho toàn bộ cảnh quay.")
        except Exception as e:
            logger.warning(f"Không thể khởi tạo bộ lọc màu CapCut: {e}")

        enc_name, enc_opts = get_best_video_encoder()
        scene_files = []
        scene_durations = []

        _log(f"🎬 [ĐẠO DIỄN HÌNH ẢNH CLI AI] Thực thi góc máy & Random Scale Dọc (110% - 130%) cho {len(scenes)} phân cảnh...")

        base_speed = float(config.get("speed") or config.get("source_speed") or config.get("playback_speed") or 1.0)
        base_speed = max(0.5, min(3.0, base_speed))
        broll_speed_multiplier = float(config.get("broll_speed_multiplier", 1.8))
        broll_speed_multiplier = max(1.2, min(3.0, broll_speed_multiplier))

        def _get_atempo(s: float) -> str:
            if abs(s - 1.0) <= 0.03:
                return ""
            if s <= 2.0:
                return f"atempo={s:.3f},"
            return f"atempo=2.0,atempo={s / 2.0:.3f},"

        # 1. Render từng cảnh
        for sc_idx, sc in enumerate(scenes):
            sc_name = sc.get("name", f"Scene {sc_idx + 1}")
            raw_shots = sc.get("shots", [])
            if not raw_shots:
                continue

            # Đạo diễn hình ảnh CLI AI lập phương án góc máy & Random Scale Dọc (110% - 130%) cho từng shot
            planned_shots = cls.plan_scene_camera_shots(
                shots=raw_shots,
                scale_w=scale_w,
                target_fg_h=target_fg_h,
            )

            seg_files = []
            cam_summary = []
            for sh_idx, sh in enumerate(planned_shots):
                start = float(sh["start"])
                end = float(sh["end"])
                dur = max(0.5, end - start)

                a_start = float(sh.get("audio_start", sh.get("start", start)))
                v_start = float(sh.get("video_start", sh.get("start", start)))
                is_anchor = bool(sh.get("is_anchor", abs(v_start - a_start) < 0.05))

                # Tốc độ âm thanh thoại: Theo đúng config người dùng (ví dụ 1.2x)
                spd_audio = base_speed
                eff_dur = round(dur / spd_audio, 3)

                if is_anchor or abs(v_start - a_start) < 0.05:
                    # Shot Gốc: Video chạy cùng tốc độ thoại (base_speed) để khớp khẩu hình
                    spd_video = base_speed
                    v_read_dur = dur
                else:
                    # Shot B-Roll (không có âm thanh gốc): ĐẨY TỐC ĐỘ LÊN CAO (timelapse / fast-motion)
                    spd_video = round(base_speed * broll_speed_multiplier, 3)
                    v_read_dur = round(eff_dur * spd_video, 3)

                z_w = int(sh["z_w"])
                z_h = int(sh["z_h"])
                x_off = int(sh["x_off"])
                y_off = int(sh["y_off"])
                z_fac = float(sh["zoom_factor"])
                u_foc = float(sh["u_focus"])
                v_sc = float(sh.get("v_scale", 1.0))

                spd_tag = f" (Audio {spd_audio:.2f}x | Video {spd_video:.2f}x)"
                cam_summary.append(f"Shot {sh_idx+1}: Zoom {z_fac:.2f}x + Dọc {v_sc*100:.0f}% @ u={u_foc:.2f}{spd_tag}")

                fg_filter = f"scale={z_w}:{z_h},crop=1080:{target_fg_h}:{x_off}:{y_off}"
                if color_fx:
                    fg_filter = f"{fg_filter},{color_fx}"

                pts_filter = f"setpts=(PTS-STARTPTS)/{spd_video:.3f}" if abs(spd_video - 1.0) > 0.04 else "setpts=PTS-STARTPTS"
                vf = (
                    f"[0:v]split=2[bg_in][fg_in];"
                    f"[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:6[bg];"
                    f"[fg_in]{fg_filter}[fg];"
                    f"[bg][fg]overlay=0:{y_top_boundary},{pts_filter}[vout]"
                )

                fade_d = min(0.04, eff_dur * 0.05)
                atempo_filter = _get_atempo(spd_audio)
                af = f"{atempo_filter}asetpts=PTS-STARTPTS,afade=t=in:ss=0:d={fade_d:.3f},afade=t=out:st={eff_dur - fade_d:.3f}:d={fade_d:.3f}"

                seg_ts = os.path.join(work_dir, f"sc_{sc_idx:02d}_shot_{sh_idx:02d}.ts")
                if is_anchor or abs(v_start - a_start) < 0.05:
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", f"{a_start:.3f}",
                        "-t", f"{dur:.3f}",
                        "-i", source_video,
                        "-filter_complex", vf,
                        "-af", af,
                        "-map", "[vout]", "-map", "0:a",
                        "-c:v", enc_name, *enc_opts,
                        "-c:a", "aac", "-b:a", "192k",
                        "-r", "30",
                        "-t", f"{eff_dur:.3f}",
                        "-f", "mpegts",
                        seg_ts
                    ]
                else:
                    # B-Roll Footage Swap: Video chạy nhanh (spd_video), Audio thoại chạy theo base_speed
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", f"{v_start:.3f}",
                        "-t", f"{v_read_dur:.3f}",
                        "-i", source_video,
                        "-ss", f"{a_start:.3f}",
                        "-t", f"{dur:.3f}",
                        "-i", source_video,
                        "-filter_complex", vf,
                        "-af", af,
                        "-map", "[vout]", "-map", "1:a",
                        "-c:v", enc_name, *enc_opts,
                        "-c:a", "aac", "-b:a", "192k",
                        "-r", "30",
                        "-t", f"{eff_dur:.3f}",
                        "-f", "mpegts",
                        seg_ts
                    ]
                shot_timeout = max(360, int(eff_dur * 8))
                _ff_run(cmd, log_fn=None, timeout=shot_timeout, label=f"sh_{sc_idx}_{sh_idx}")
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
            _ff_run(concat_cmd, log_fn=None, timeout=300, label=f"sc_{sc_idx}")
            sc_dur = probe_duration_sec(sc_video)
            scene_files.append(sc_video)
            scene_durations.append(sc_dur)
            _log(f"   ✓ Cảnh {sc_idx + 1}/{len(scenes)} ({sc_dur:.1f}s): {' | '.join(cam_summary)}")

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
        _log(f"   🌪️ [CHUYỂN CẢNH HOLLYWOOD] Ghép nối {len(scene_files)} cảnh bằng Motion Blur (xfade=hblur {transition_duration}s) tại {len(transition_timestamps)} điểm nối:")
        for t_idx, t_pos in enumerate(transition_timestamps):
            _log(f"      • Điểm nối {t_idx+1}: tại giây {t_pos:.2f}s (Cảnh {t_idx+1} -> Cảnh {t_idx+2})")

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
        merged_dur = probe_duration_sec(output_merged_path)
        _log(f"✅ [NHÂN VIÊN 1] Đã hoàn thành ghép nối {len(scene_files)} cảnh: {output_merged_path} (Thời lượng: {merged_dur:.2f}s)")
        return output_merged_path, transition_timestamps
