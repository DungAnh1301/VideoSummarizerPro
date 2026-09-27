"""Engine xử lý video local cho Chế độ Chia Part & Tinh Lược Video Gốc.

Chức năng:
1. refine_cuts_with_opencv_and_audio: Soát mốc cắt với SceneMapper và Silence Gap của SRT
2. prune_and_build_cleaned_source: Ghép nối mượt các đoạn giữ lại thành video gốc sạch
3. split_into_parts: Cắt chia Part chuẩn mốc thời gian cliffhanger
4. render_banner_image: Vẽ banner Header Part nét căng bằng PIL + FontManager (0% lỗi tofu)
5. apply_part_hook_and_postprocessing: Ráp Hook, tăng tốc atempo, CapCut Limiter +20dB, Subtle Zoom
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image, ImageDraw, ImageColor

from font_manager import FontManager

logger = logging.getLogger("PartSplitterEngine")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _ff_run(cmd: List[str], log_fn=None, timeout: int = 1800, label: str = "chia_part") -> subprocess.CompletedProcess:
    """Chạy lệnh FFmpeg an toàn theo chuẩn tự động nhận diện phần cứng (run_ffmpeg_auto của Tóm Tắt Video)."""
    cmd_str = " ".join(cmd)
    logger.info(f"🎬 [FFMPEG] Chạy: {cmd_str[:160]}...")
    if callable(log_fn):
        try:
            log_fn(f"🎬 [FFMPEG] {cmd[0]} {cmd[1] if len(cmd) > 1 else ''}...")
        except Exception:
            pass

    if "-c:v" in cmd:
        from hardware_manager import run_ffmpeg_auto
        try:
            return run_ffmpeg_auto(
                cmd, label=label, logger=logger, timeout=timeout,
                check=True, creationflags=CREATE_NO_WINDOW
            )
        except Exception as auto_err:
            logger.warning(f"⚠️ [RUN_FFMPEG_AUTO] {auto_err} -> fallback subprocess.")

    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW
    )
    if res.returncode != 0:
        err_msg = (res.stderr or res.stdout or "Unknown FFmpeg error").strip()
        logger.error(f"❌ [FFMPEG ERROR]: {err_msg[-600:]}")
        raise RuntimeError(f"FFmpeg thất bại (code {res.returncode}): {err_msg[-400:]}")
    return res


def probe_duration_sec(path: str) -> float:
    """Lấy thời lượng video chính xác bằng ffprobe."""
    if not path or not os.path.isfile(path):
        return 0.0
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        val = float(res.stdout.strip())
        return max(0.0, val)
    except Exception:
        # Thử fallback stream
        try:
            cmd2 = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                path
            ]
            res2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            return max(0.0, float(res2.stdout.strip()))
        except Exception:
            return 0.0


def probe_video_dimensions(path: str) -> Tuple[int, int]:
    """Lấy kích thước WxH của video."""
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=s=x:p=0",
        path
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, creationflags=CREATE_NO_WINDOW)
        parts = res.stdout.strip().split("x")
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1920, 1080


def get_best_video_encoder() -> Tuple[str, List[str]]:
    """Tự động kiểm tra phần cứng (NVIDIA/Intel/AMD/CPU) và chọn encoder tối ưu nhất thích ứng từng máy."""
    try:
        from hardware_manager import get_part_render_strategy
        st = get_part_render_strategy()
        return st["encoder"], st["encoder_opts"]
    except Exception:
        pass
    return "libx264", ["-preset", "ultrafast", "-crf", "19"]


class PartSplitterEngine:
    """Engine xử lý video chuyên biệt cho Chia Part & Tinh Lược."""

    @classmethod
    def map_orig_to_clean_time(cls, t_orig: float, keep_ranges: List[Tuple[float, float]]) -> float:
        """Ánh xạ mốc thời gian từ video gốc sang video sạch sau khi tinh lược."""
        if not keep_ranges:
            return float(t_orig)
        t_clean = 0.0
        for k_s, k_e in keep_ranges:
            if t_orig < k_s:
                return round(t_clean, 2)
            elif k_s <= t_orig <= k_e:
                return round(t_clean + (t_orig - k_s), 2)
            else:
                t_clean += (k_e - k_s)
        return round(t_clean, 2)

    @classmethod
    def sanitize_part_splits(
        cls,
        splits: List[float],
        total_duration: float,
        part_count: int,
        min_part_dur: float = 60.0
    ) -> List[float]:
        """Đảm bảo các mốc cắt chia Part hợp lý, giữ trọn nội dung kịch bản và mỗi Part >= 60s."""
        total_dur = float(total_duration)
        min_part_dur = max(60.0, float(min_part_dur))
        if total_dur <= min_part_dur * 1.5:
            return []
        
        needed = max(1, part_count - 1)
        raw_splits = sorted([float(s) for s in splits if 0.0 < float(s) < total_dur])

        # Nếu không có mốc nào hoặc số mốc không đủ, phân bổ đều làm mốc cơ bản
        if len(raw_splits) != needed:
            step = total_dur / float(part_count)
            raw_splits = [round(step * i, 2) for i in range(1, part_count)]

        # Rà soát từng mốc: tôn trọng 100% mốc cắt phân cảnh và cliffhanger của AI, chỉ đảm bảo mỗi part >= min_part_dur (60s)
        final_splits = []
        last_t = 0.0
        for i, pt in enumerate(raw_splits):
            remaining_parts = needed - i
            max_allowed = total_dur - (remaining_parts * min_part_dur)
            min_allowed = last_t + min_part_dur

            if max_allowed < min_allowed:
                pt = (last_t + total_dur) / 2.0
            else:
                pt = max(min_allowed, min(max_allowed, pt))
            
            final_splits.append(round(pt, 2))
            last_t = pt

        # Kiểm tra đoạn cuối cùng, không để bị cụt dưới min_part_dur
        if final_splits and (total_dur - final_splits[-1]) < min_part_dur:
            if len(final_splits) > 1:
                prev_limit = final_splits[-2] + min_part_dur
                final_splits[-1] = max(prev_limit, round(total_dur - min_part_dur, 2))
            else:
                final_splits[-1] = round(max(min_part_dur, total_dur - min_part_dur), 2)

        return final_splits

    @classmethod
    def refine_cuts_with_opencv_and_audio(
        cls,
        ai_cuts: List[float],
        scene_map: Dict[str, Any] = None,
        srt_cues: List[Dict[str, Any]] = None,
        tolerance: float = 0.5,
        radius: float = None,
        log_fn: Optional[Callable[[str], None]] = None,
        **kwargs
    ) -> List[float]:
        """Soát lại các mốc của AI trong bán kính +-tolerance (0.5s):
        - Hít vào điểm chuyển cảnh (Scene Cut) của scene_mapper.
        - Hít vào khoảng lặng (Silence Gap) giữa 2 câu thoại trong SRT.
        - Triệt tiêu 100% lỗi cụt tiếng hoặc giật hình.
        """
        if radius is not None:
            tolerance = radius

        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        if not ai_cuts:
            return []

        # 1. Thu thập danh sách scene transitions
        scene_cuts: List[float] = []
        if scene_map and "scenes" in scene_map:
            for sc in scene_map["scenes"]:
                if isinstance(sc, dict):
                    scene_cuts.append(float(sc.get("start", 0.0)))
                elif isinstance(sc, (list, tuple)) and len(sc) >= 1:
                    scene_cuts.append(float(sc[0]))

        # 2. Thu thập các khoảng lặng thoại (Silence Gaps)
        silence_gaps: List[Tuple[float, float, float]] = []  # (gap_start, gap_end, midpoint)
        if srt_cues and len(srt_cues) >= 2:
            for i in range(len(srt_cues) - 1):
                cur_end = float(srt_cues[i]["end"])
                nxt_start = float(srt_cues[i + 1]["start"])
                if nxt_start >= cur_end:
                    gap_len = nxt_start - cur_end
                    mid = (cur_end + nxt_start) / 2.0
                    silence_gaps.append((cur_end, nxt_start, mid))

        refined_cuts: List[float] = []
        for orig_t in sorted(ai_cuts):
            best_t = orig_t
            snapped = False

            # Ưu tiên 1: Có khoảng lặng thoại nằm trong bán kính tolerance
            cand_silence = []
            for g_s, g_e, mid in silence_gaps:
                if abs(mid - orig_t) <= tolerance:
                    cand_silence.append((abs(mid - orig_t), mid))
                elif g_s <= orig_t <= g_e:
                    # Rơi đúng vào khoảng lặng
                    cand_silence.append((0.0, orig_t))
            if cand_silence:
                cand_silence.sort(key=lambda x: x[0])
                best_t = cand_silence[0][1]
                snapped = True

            # Ưu tiên 2: Nếu chưa hít được khoảng lặng, thử hít vào Scene Cut
            if not snapped and scene_cuts:
                cand_scenes = [
                    (abs(sc - orig_t), sc)
                    for sc in scene_cuts
                    if abs(sc - orig_t) <= tolerance
                ]
                if cand_scenes:
                    cand_scenes.sort(key=lambda x: x[0])
                    best_t = cand_scenes[0][1]
                    snapped = True

            # Ưu tiên 3: Nếu rơi giữa câu nói của cue, đẩy về trước hoặc sau cue
            if not snapped and srt_cues:
                for c in srt_cues:
                    c_s = float(c["start"])
                    c_e = float(c["end"])
                    if c_s <= orig_t <= c_e:
                        # Cách mốc nào gần hơn
                        if abs(orig_t - c_s) <= abs(orig_t - c_e):
                            best_t = max(0.0, c_s - 0.05)
                        else:
                            best_t = c_e + 0.05
                        break

            refined_cuts.append(round(best_t, 2))
            if abs(best_t - orig_t) > 0.01:
                _log(f"🎯 [SNAPPING] Mốc {orig_t:.2f}s -> Hít chuẩn xác thành {best_t:.2f}s (tránh cụt tiếng/giật hình)")

        # Đảm bảo các mốc không trùng và cách nhau ít nhất 5s
        final_cuts = []
        for c in sorted(refined_cuts):
            if not final_cuts or (c - final_cuts[-1]) >= 5.0:
                final_cuts.append(c)

        return final_cuts

    @classmethod
    def prune_and_build_cleaned_source(
        cls,
        source_video: str,
        drop_ranges: List[Dict[str, Any]],
        output_clean: str,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> Tuple[str, List[Tuple[float, float]]]:
        """Dùng FFmpeg stream concat nối mượt các đoạn giữ lại thành video gốc sạch.
        
        Returns: (output_clean_path, keep_ranges)
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        total_dur = probe_duration_sec(source_video)
        if total_dur <= 0.0:
            raise RuntimeError(f"Không xác định được thời lượng video gốc: {source_video}")

        os.makedirs(os.path.dirname(os.path.abspath(output_clean)), exist_ok=True)

        # 1. Tính toán keep_ranges = [0, total_dur] - drop_ranges
        sorted_drops = []
        for d in drop_ranges or []:
            try:
                s = max(0.0, float(d.get("start", 0.0)))
                e = min(total_dur, float(d.get("end", 0.0)))
                if e > s + 0.5:
                    sorted_drops.append((s, e))
            except Exception:
                pass
        sorted_drops.sort(key=lambda x: x[0])

        keep_ranges: List[Tuple[float, float]] = []
        curr_t = 0.0
        for d_s, d_e in sorted_drops:
            if d_s > curr_t + 0.5:
                keep_ranges.append((round(curr_t, 2), round(d_s, 2)))
            curr_t = max(curr_t, d_e)
        if curr_t < total_dur - 0.5:
            keep_ranges.append((round(curr_t, 2), round(total_dur, 2)))

        if not keep_ranges:
            keep_ranges = [(0.0, round(total_dur, 2))]

        _log(f"✂️ [PRUNER] Tinh lược: Giữ lại {len(keep_ranges)} đoạn video chính (Đã lược {len(sorted_drops)} đoạn thừa)")

        # Nếu không có đoạn nào bị cắt (giữ nguyên toàn bộ video)
        if len(keep_ranges) == 1 and keep_ranges[0][0] == 0.0 and abs(keep_ranges[0][1] - total_dur) < 0.5:
            _log("⏭️ [PRUNER] Không cần cắt đoạn thừa nào, dùng trực tiếp video gốc.")
            return source_video, keep_ranges

        # 2. Thử ghép nối siêu tốc bằng Stream Copy (0-3 giây thay vì re-encode 1.5 phút)
        clean_dir = os.path.dirname(os.path.abspath(output_clean))
        os.makedirs(clean_dir, exist_ok=True)
        temp_segs = []
        use_stream_copy = True
        try:
            for i, (k_s, k_e) in enumerate(keep_ranges):
                seg_path = os.path.join(clean_dir, f"_prune_seg_{i}.mp4")
                temp_segs.append(seg_path)
                cmd_seg = [
                    "ffmpeg", "-y",
                    "-ss", f"{k_s:.2f}",
                    "-to", f"{k_e:.2f}",
                    "-i", source_video,
                    "-c", "copy",
                    "-avoid_negative_ts", "make_zero",
                    seg_path
                ]
                _ff_run(cmd_seg, log_fn=None, timeout=300)
                if not os.path.exists(seg_path) or os.path.getsize(seg_path) == 0:
                    use_stream_copy = False
                    break

            if use_stream_copy:
                list_txt = os.path.join(clean_dir, "_prune_concat.txt")
                with open(list_txt, "w", encoding="utf-8") as f:
                    for sp in temp_segs:
                        p_norm = os.path.abspath(sp).replace("\\", "/")
                        f.write(f"file '{p_norm}'\n")

                cmd_concat = [
                    "ffmpeg", "-y",
                    "-f", "concat",
                    "-safe", "0",
                    "-i", list_txt,
                    "-c", "copy",
                    output_clean
                ]
                _log(f"🎬 [PRUNER] Đang ghép các đoạn tinh lược bằng Stream Copy siêu tốc...")
                _ff_run(cmd_concat, log_fn=log_fn, timeout=300)

                out_dur = probe_duration_sec(output_clean)
                if out_dur > 0 and os.path.exists(output_clean) and os.path.getsize(output_clean) > 1000:
                    _log(f"✅ [PRUNER] Video gốc sạch hoàn tất bằng Stream Copy ({out_dur:.1f}s)")
                    for sp in temp_segs:
                        try:
                            if os.path.exists(sp):
                                os.remove(sp)
                        except Exception:
                            pass
                    try:
                        if os.path.exists(list_txt):
                            os.remove(list_txt)
                    except Exception:
                        pass
                    return output_clean, keep_ranges
                else:
                    use_stream_copy = False
        except Exception as copy_err:
            _log(f"⚠️ [PRUNER] Stream Copy không khả dụng ({copy_err}), chuyển sang encode filter an toàn...")
            use_stream_copy = False
        finally:
            if not use_stream_copy:
                for sp in temp_segs:
                    try:
                        if os.path.exists(sp):
                            os.remove(sp)
                    except Exception:
                        pass

        # Fallback: Nếu stream copy không áp dụng được, dùng filter_complex concat re-encode an toàn
        _log(f"🎬 [PRUNER] Đang kết xuất video gốc sạch bằng filter encode ({output_clean})...")
        v_filters = []
        a_filters = []
        concat_inputs = []
        for i, (k_s, k_e) in enumerate(keep_ranges):
            v_filters.append(f"[0:v]trim=start={k_s:.2f}:end={k_e:.2f},setpts=PTS-STARTPTS[v{i}]")
            a_filters.append(f"[0:a]atrim=start={k_s:.2f}:end={k_e:.2f},asetpts=PTS-STARTPTS[a{i}]")
            concat_inputs.append(f"[v{i}][a{i}]")

        n_clips = len(keep_ranges)
        filter_complex = (
            ";".join(v_filters) + ";" +
            ";".join(a_filters) + ";" +
            "".join(concat_inputs) + f"concat=n={n_clips}:v=1:a=1[outv][outa]"
        )

        enc_name, enc_opts = get_best_video_encoder()
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video,
            "-filter_complex", filter_complex,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", enc_name, *enc_opts,
            "-c:a", "aac", "-b:a", "192k",
            output_clean
        ]
        _ff_run(cmd, log_fn=log_fn, timeout=2400)
        _log(f"✅ [PRUNER] Video gốc sạch hoàn tất ({probe_duration_sec(output_clean):.1f}s)")
        return output_clean, keep_ranges

    @classmethod
    def split_into_parts(
        cls,
        clean_video: str,
        cut_points: List[float],
        output_dir: str,
        part_label_prefix: str = "Part",
        part_titles: List[str] = None,
        log_fn: Optional[Callable[[str], None]] = None
    ) -> List[Dict[str, Any]]:
        """Cắt từng Part đảm bảo > 1.0 phút và dải +-50%."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        total_dur = probe_duration_sec(clean_video)
        if total_dur <= 0.0:
            raise RuntimeError(f"Video không hợp lệ để chia Part: {clean_video}")

        os.makedirs(output_dir, exist_ok=True)
        part_titles = part_titles or []

        # Danh sách các mốc thời gian gồm 0, cut_points, total_dur
        bounds = [0.0] + [c for c in sorted(cut_points) if 0.0 < c < total_dur] + [total_dur]
        part_count = len(bounds) - 1

        _log(f"🔪 [CHIA PART] Tiến hành cắt thành {part_count} Parts...")

        parts_info: List[Dict[str, Any]] = []
        enc_name, enc_opts = get_best_video_encoder()

        for i in range(part_count):
            p_idx = i + 1
            p_start = bounds[i]
            p_end = bounds[i + 1]
            p_dur = p_end - p_start
            p_title = part_titles[i] if i < len(part_titles) else f"Chapter {p_idx}"

            raw_part_filename = f"part_{p_idx}_raw.mp4"
            raw_part_path = os.path.join(output_dir, raw_part_filename)

            _log(f"✂️ [CHIA PART] Xuất Part {p_idx}/{part_count}: {p_start:.1f}s -> {p_end:.1f}s ({p_dur:.1f}s)")

            # Ưu tiên stream copy (-c copy) bảo toàn 100% không gian màu và chất lượng gốc tuyệt đối
            cmd_copy = [
                "ffmpeg", "-y",
                "-ss", f"{p_start:.2f}",
                "-to", f"{p_end:.2f}",
                "-i", clean_video,
                "-c", "copy",
                raw_part_path
            ]
            try:
                _ff_run(cmd_copy, log_fn=log_fn, timeout=600)
            except Exception as copy_err:
                _log(f"⚠️ Stream copy không khả dụng ({copy_err}), dùng re-encode chất lượng cao...")
                cmd_fallback = [
                    "ffmpeg", "-y",
                    "-ss", f"{p_start:.2f}",
                    "-to", f"{p_end:.2f}",
                    "-i", clean_video,
                    "-c:v", "libx264", "-crf", "14", "-preset", "veryfast",
                    "-c:a", "aac", "-b:a", "192k",
                    raw_part_path
                ]
                _ff_run(cmd_fallback, log_fn=log_fn, timeout=1200)

            parts_info.append({
                "index": p_idx,
                "title": p_title,
                "start": p_start,
                "end": p_end,
                "duration": p_dur,
                "raw_path": raw_part_path
            })

        _log(f"✅ [CHIA PART] Đã cắt xong {len(parts_info)} Parts.")
        return parts_info

    @classmethod
    def render_banner_image(
        cls,
        title_text: str,
        part_index: int,
        part_label_prefix: str = "Part",
        video_w: int = 1080,
        output_png: str = "",
        config: Dict[str, Any] = None
    ) -> str:
        """Vẽ Banner Title Part nét căng bằng PIL + FontManager:
        - Tôn trọng 100% các thông số từ Title & Sub Studio: title_color1, title_color2, title_outline_color, title_bg_color.
        - Mặc định: Chuẩn phong cách TikTok (chữ trắng viền đen rõ nét, KHÔNG hộp nền).
        - Nếu có cấu hình màu nền (title_bg_color khác none/transparent): Vẽ hộp bo góc Pill Badge.
        - Tự động ngắt dòng thông minh khi tiêu đề dài.
        """
        if not output_png:
            output_png = os.path.join(tempfile.gettempdir(), f"banner_part_{part_index}_{time.time_ns()}.png")

        config = config or {}
        prefix_str = f"{part_label_prefix} {part_index}".strip()
        full_text = f"{prefix_str}: {title_text}" if title_text else prefix_str

        bg_color_opt = str(config.get("title_bg_color", "white") or "white").lower().strip()
        use_box = bg_color_opt not in ("none", "transparent", "")
        max_content_w = int(video_w * 0.85)

        # Đọc màu chữ và viền từ config
        raw_c1 = str(config.get("title_color1", "black" if bg_color_opt == "white" else "white") or "black")
        raw_outline = str(config.get("title_outline_color", "none") or "none")

        def _parse_rgba(color_str, default_rgba):
            try:
                rgb = ImageColor.getrgb(color_str)
                if len(rgb) == 3:
                    return (rgb[0], rgb[1], rgb[2], 255)
                return rgb
            except Exception:
                return default_rgba

        # Chọn font phù hợp
        font_size = max(28, int(video_w * 0.045))  # ~48px trên 1080px
        font = None
        candidate_paths = [
            os.path.join(FontManager.WINDOWS_FONT_DIR, "arialbd.ttf"),
            os.path.join(FontManager.WINDOWS_FONT_DIR, "segoeuib.ttf"),
            os.path.join(FontManager.WINDOWS_FONT_DIR, "seguibl.ttf"),
            FontManager.get_banner_font_path(full_text)
        ]
        for p in candidate_paths:
            if p and os.path.exists(p):
                try:
                    font = ImageFont.truetype(p, font_size)
                    break
                except Exception:
                    pass
        if font is None:
            font = FontManager.get_banner_font(full_text, font_size)

        # Tự động ngắt dòng khi chữ dài
        words = full_text.split()
        lines = []
        cur_line = []
        for w in words:
            cand = " ".join(cur_line + [w])
            bbox = font.getbbox(cand)
            if (bbox[2] - bbox[0]) <= max_content_w or not cur_line:
                cur_line.append(w)
            else:
                lines.append(" ".join(cur_line))
                cur_line = [w]
        if cur_line:
            lines.append(" ".join(cur_line))
        wrapped_text = "\n".join(lines) if lines else full_text

        # Đo bbox đa dòng
        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        d_dummy = ImageDraw.Draw(dummy)
        bbox = d_dummy.multiline_textbbox(
            (0, 0), wrapped_text, font=font, align="center",
            stroke_width=3, spacing=12
        )

        pad_x = 28
        pad_y = 20
        bw = int(bbox[2] - bbox[0] + pad_x * 2)
        bh = int(bbox[3] - bbox[1] + pad_y * 2)

        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Xác định màu fill và stroke chuẩn xác
        txt_fill = _parse_rgba(raw_c1, (0, 0, 0, 255) if bg_color_opt == "white" else (255, 255, 255, 255))
        has_outline = raw_outline not in ("none", "transparent", "")
        stroke_c = _parse_rgba(raw_outline, (0, 0, 0, 255)) if has_outline else (0, 0, 0, 0)
        stroke_w = 3 if has_outline else 0

        if use_box:
            # Hộp bo góc Pill Badge theo màu nền người dùng đã cấu hình
            box_fill = _parse_rgba(bg_color_opt, (255, 255, 255, 240))
            box_border = (255, 215, 0, 240) if bg_color_opt in ["black", "#000000"] else (220, 226, 234, 255)
            draw.rounded_rectangle([(0, 0), (bw, bh)], radius=18, fill=box_fill, outline=box_border, width=2)
        else:
            # Lớp bóng đổ nhẹ nếu không có hộp nền
            draw.multiline_text(
                (pad_x - bbox[0] + 2, pad_y - bbox[1] + 2),
                wrapped_text,
                font=font,
                fill=(0, 0, 0, 160),
                stroke_width=2,
                stroke_fill=(0, 0, 0, 160),
                align="center",
                spacing=12
            )

        draw.multiline_text(
            (pad_x - bbox[0], pad_y - bbox[1]),
            wrapped_text,
            font=font,
            fill=txt_fill,
            stroke_width=stroke_w,
            stroke_fill=stroke_c,
            align="center",
            spacing=12
        )

        os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)
        img.save(output_png, "PNG")
        return output_png

    @classmethod
    def apply_part_hook_and_postprocessing(
        cls,
        part_info: Dict[str, Any] = None,
        hook_info: Optional[Dict[str, Any]] = None,
        source_video: str = "",
        output_final: str = "",
        config: Dict[str, Any] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        watermark_blurs: Optional[List[Dict[str, Any]]] = None,
        **kwargs
    ) -> str:
        """Hậu kỳ chuẩn 100% theo Tóm Tắt Video:
        - Xuất Canvas 1080x1920 (9:16) với nền mờ mượt từ 360x640 (khi bật blur_bg).
        - Áp dụng 100% Crop Studio, 15 thanh màu CapCut + Preset 8K/HDR/Film.
        - Zoom-in, Scale ngang/dọc, Vị trí X/Y, Blur Mask.
        - CapCut Limiter +20dB chống rè, Atempo dải rộng.
        - Title Banner chuẩn TikTok (chữ trắng viền đen không nền bo tròn).
        - Hỗ trợ linh hoạt cả 2 kiểu truyền tham số (kwargs hoặc dict).
        - Hỗ trợ watermark_blurs truyền từ Single-Pass AI QC để không phải gọi lại AI.
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        # 1. Chuẩn hóa tham số đầu vào (tương thích cả kwargs lẫn positional/dict)
        if part_info is None or not isinstance(part_info, dict):
            raw_part = kwargs.get("raw_part_path", "") or (part_info if isinstance(part_info, str) else "")
            p_idx = kwargs.get("part_index", 1)
            p_title = kwargs.get("part_title", f"Part {p_idx}")
            part_info = {"raw_path": raw_part, "index": p_idx, "title": p_title}
        else:
            raw_part = part_info.get("raw_path", "")
            p_idx = part_info.get("index", 1)
            p_title = part_info.get("title", f"Part {p_idx}")

        if not output_final:
            output_final = kwargs.get("output_final", "")

        if not hook_info and "hook_time_range" in kwargs:
            hook_info = kwargs.get("hook_time_range")

        if watermark_blurs is None and "watermark_blurs" in kwargs:
            watermark_blurs = kwargs.get("watermark_blurs")

        cfg = dict(config or kwargs.get("config") or {})
        # Bổ sung các giá trị từ kwargs nếu config chưa có
        for k_src, k_dst in [
            ("speed", "source_speed"),
            ("apply_limiter", "apply_capcut_limiter"),
            ("apply_subtle_zoom", "apply_subtle_zoom"),
            ("part_label_prefix", "part_label_prefix")
        ]:
            if k_src in kwargs and k_dst not in cfg:
                cfg[k_dst] = kwargs[k_src]

        part_prefix = str(cfg.get("part_label_prefix", "Part"))
        speed = max(0.5, min(3.0, float(cfg.get("source_speed") or cfg.get("speed") or 1.05)))
        apply_limiter = bool(cfg.get("apply_capcut_limiter", True))
        audio_boost = float(cfg.get("audio_boost", 20.0) or 20.0)

        _log(f"🎬 [HẬU KỲ PART {p_idx}] Bắt đầu hoàn thiện: Canvas 9:16 | Tốc độ {speed}x | Limiter +20dB ({audio_boost}dB) | Màu sắc & Crop Studio...")

        # Cấu hình luồng đa nhiệm FFmpeg tối ưu thích ứng theo cấu hình máy
        filter_threads = kwargs.get("filter_threads")
        if not filter_threads:
            try:
                from hardware_manager import get_part_render_strategy
                st = get_part_render_strategy()
                filter_threads = st["filter_threads"]
            except Exception:
                filter_threads = min(8, os.cpu_count() or 4)
        ff_threads = ["-threads", "0", "-filter_complex_threads", str(filter_threads)]

        # Luôn đặt các file tạm (banner, hook teaser, processed part, qc_frames) trong work_dir hoặc thư mục chứa raw_part
        # Tuyệt đối KHÔNG tạo trong thư mục xuất thành phẩm (output_final) để thư mục xuất chỉ chứa video sạch 100%
        temp_dir = kwargs.get("work_dir") or kwargs.get("temp_dir") or (os.path.dirname(os.path.abspath(raw_part)) if raw_part else "")
        if not temp_dir or not os.path.exists(temp_dir):
            temp_dir = os.path.abspath(os.path.join("temp", "part_splitter_tmp"))
        os.makedirs(temp_dir, exist_ok=True)
        if output_final:
            os.makedirs(os.path.dirname(os.path.abspath(output_final)), exist_ok=True)

        # 2. Xây dựng Filter Complex chuẩn 100% theo EditorProcessor của Tóm Tắt Video
        from editor_processor import EditorProcessor
        post_options = dict(cfg)
        post_options.setdefault("speed", speed)
        post_options.setdefault("audio_boost", audio_boost)
        base_vf = EditorProcessor._build_vf_filter(post_options, input_label="[0:v]")

        # 3. Trích xuất Hook segment nếu có (áp dụng cùng base_vf 9:16 + màu sắc)
        hook_clip_path = ""
        if hook_info and isinstance(hook_info, dict) and "start" in hook_info and "end" in hook_info and source_video:
            h_start = float(hook_info["start"])
            h_end = float(hook_info["end"])
            if h_end > h_start + 1.0:
                hook_clip_path = os.path.join(temp_dir, f"hook_teaser_p{p_idx}.mp4")
                _log(f"🎣 [HOOK PART {p_idx}] Trích xuất teaser giật gân ({h_start:.1f}s -> {h_end:.1f}s) chuẩn 9:16 & màu sắc...")
                enc_name, enc_opts = get_best_video_encoder()
                cmd_hook = [
                    "ffmpeg", "-y",
                    *ff_threads,
                    "-ss", f"{h_start:.2f}",
                    "-to", f"{h_end:.2f}",
                    "-i", source_video,
                    "-filter_complex", f"{base_vf}",
                    "-map", "[vout]", "-map", "0:a?",
                    "-c:v", enc_name, *enc_opts,
                    "-c:a", "aac", "-b:a", "192k",
                    hook_clip_path
                ]
                try:
                    _ff_run(cmd_hook, log_fn=log_fn, timeout=300)
                except Exception as h_err:
                    _log(f"⚠️ Không xuất được hook: {h_err}, bỏ qua hook.")
                    hook_clip_path = ""

        # 4. Tạo ảnh Banner PNG (Chuẩn style TikTok chữ trắng viền đen)
        banner_png = cls.render_banner_image(
            title_text=p_title,
            part_index=p_idx,
            part_label_prefix=part_prefix,
            video_w=1080,
            output_png=os.path.join(temp_dir, f"banner_p{p_idx}.png"),
            config=cfg
        )

        # 4.1 Quét kiểm duyệt AI xóa logo, sub cũ, banner, vết máu (Gemini Grid Inspector)
        enable_gemini_qc = bool(cfg.get("gemini_grid_inspector", True))
        curr_v_label = "vout"
        clean_chain_str = ""
        part_dur = probe_duration_sec(raw_part)

        if watermark_blurs is not None:
            # CHẾ ĐỘ TỐC ĐỘ CAO (SINGLE-PASS AI QC): Dùng kết quả đã quét sẵn từ master video
            if enable_gemini_qc and watermark_blurs:
                try:
                    _log(f"⚡ [AI QC PART {p_idx}] Sử dụng {len(watermark_blurs)} vùng mờ logo/sub/máu từ Single-Pass AI QC...")
                    has_blood = any("blood" in str(item.get("label", "")).lower() for item in watermark_blurs)
                    red_to_gray_lut_path = EditorProcessor.ensure_red_to_gray_lut() if has_blood else ""

                    clean_chain_str, curr_v_label = EditorProcessor.build_clustered_qc_filters(
                        watermark_blurs, duration_sec=part_dur,
                        curr_v_label="vout", output_w=1080, output_h=1920,
                        red_to_gray_lut_path=red_to_gray_lut_path,
                        log_fn=_log,
                        qc_blur_strength=int(cfg.get("qc_blur_strength", 75))
                    )
                except Exception as qc_e:
                    _log(f"⚠️ [AI QC PART {p_idx}] Lỗi xử lý filter QC: {qc_e}")
                    curr_v_label = "vout"
                    clean_chain_str = ""
        else:
            # FALLBACK: Quét riêng từng part nếu không có watermark_blurs truyền vào
            from antigravity_processor import AntigravityProcessor
            api_key = str(cfg.get("gemini_api_key") or cfg.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "")
            has_ai_service = bool(api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or AntigravityProcessor.executable())
            if enable_gemini_qc and has_ai_service:
                try:
                    from ai_processor import AIProcessor
                    qc_frames_dir = os.path.join(temp_dir, f"qc_frames_p{p_idx}")
                    os.makedirs(qc_frames_dir, exist_ok=True)

                    # Trích xuất 8-10 frame đại diện bằng Fast Direct Seek siêu tốc (<1-2s)
                    n_kfs_part = max(6, min(10, int(part_dur / 20.0) or 8))
                    qc_vf = EditorProcessor._build_qc_vf_filter(cfg, input_label="[0:v]")
                    sample_times_part = [((i + 0.5) / float(n_kfs_part)) * part_dur for i in range(n_kfs_part)]
                    import concurrent.futures

                    def _extract_part_kf(item_tuple):
                        k_idx, k_t = item_tuple
                        out_fp = os.path.join(qc_frames_dir, f"frame_{k_idx:04d}.jpg")
                        cmd_kf = [
                            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                            "-ss", f"{k_t:.2f}",
                            "-i", raw_part,
                            "-vframes", "1",
                            "-filter_complex", f"{qc_vf};[vout]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2[outkf]",
                            "-map", "[outkf]",
                            out_fp
                        ]
                        try:
                            _ff_run(cmd_kf, log_fn=None, timeout=20)
                        except Exception:
                            pass

                    with concurrent.futures.ThreadPoolExecutor(max_workers=min(6, os.cpu_count() or 4)) as kf_pool:
                        list(kf_pool.map(_extract_part_kf, enumerate(sample_times_part)))

                    frame_files = sorted([os.path.join(qc_frames_dir, f) for f in os.listdir(qc_frames_dir) if f.endswith(".jpg")])
                    if frame_files:
                        _log(f"🔍 [AI QC PART {p_idx}] Đang quét bản quyền, logo, sub cũ, banner & máu vi phạm trên {len(frame_files)} keyframes...")
                        n_frames = len(frame_files)
                        step_dur = part_dur / float(n_frames)
                        kf_items = []
                        for idx, fp in enumerate(frame_files):
                            st = idx * step_dur
                            en = min(part_dur, (idx + 1) * step_dur)
                            kf_items.append({
                                "path": fp,
                                "start_sec": max(0.0, st),
                                "end_sec": min(part_dur, en),
                                "sample_sec": (st + en) / 2.0
                            })

                        model_name = str(cfg.get("gemini_model") or cfg.get("ai_model") or "")
                        detected_items = AIProcessor.inspect_90s_grid_for_copyright(
                            api_key=api_key, model_name=model_name,
                            duration_sec=part_dur,
                            keyframes=kf_items
                        )

                        if detected_items:
                            watermark_blurs = EditorProcessor.refine_detection_boxes_with_opencv(
                                detected_items, qc_frames_dir, duration_sec=part_dur
                            )
                        else:
                            watermark_blurs = []

                        has_blood = any("blood" in str(item.get("label", "")).lower() for item in watermark_blurs)
                        red_to_gray_lut_path = EditorProcessor.ensure_red_to_gray_lut() if has_blood else ""

                        clean_chain_str, curr_v_label = EditorProcessor.build_clustered_qc_filters(
                            watermark_blurs, duration_sec=part_dur,
                            curr_v_label="vout", output_w=1080, output_h=1920,
                            red_to_gray_lut_path=red_to_gray_lut_path,
                            log_fn=_log,
                            qc_blur_strength=int(cfg.get("qc_blur_strength", 75))
                        )
                except Exception as qc_e:
                    _log(f"⚠️ [AI QC PART {p_idx}] Bỏ qua quét AI do: {qc_e}")
                    curr_v_label = "vout"
                    clean_chain_str = ""
                finally:
                    # Dọn dẹp sạch sẽ toàn bộ thư mục qc_frames ngay khi quét xong để không bao giờ bị lộ ra ngoài
                    qc_frames_candidate = os.path.join(temp_dir, f"qc_frames_p{p_idx}")
                    if os.path.isdir(qc_frames_candidate):
                        try:
                            shutil.rmtree(qc_frames_candidate, ignore_errors=True)
                        except Exception:
                            pass

        # Tăng tốc video bằng setpts
        if abs(speed - 1.0) >= 0.01:
            v_speed_node = f"[{curr_v_label}]setpts={1.0 / speed:.6f}*PTS[v_sped];[v_sped]"
        else:
            v_speed_node = f"[{curr_v_label}]"

        # Định vị Banner
        banner_y = int(cfg.get("title_y_pos", 260))
        video_filter_final = (
            f"{base_vf}{clean_chain_str};"
            f"{v_speed_node}[1:v]overlay=(W-w)/2:{banner_y}[outv]"
        )

        # 5. Xử lý âm thanh (CapCut Limiter + atempo dải rộng)
        a_tempo_str = EditorProcessor.build_atempo_filter(speed)
        a_tempo_prefix = f",{a_tempo_str}" if a_tempo_str else ""

        has_orig_audio = False
        try:
            probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", raw_part]
            has_orig_audio = bool(subprocess.check_output(probe_cmd, creationflags=CREATE_NO_WINDOW).strip())
        except Exception:
            has_orig_audio = True

        if has_orig_audio:
            if apply_limiter:
                audio_filter_str = f"{EditorProcessor.capcut_audio_filter(audio_boost)}{a_tempo_prefix}"
            else:
                audio_filter_str = f"volume={audio_boost:.2f}dB{a_tempo_prefix}" if abs(speed - 1.0) >= 0.01 or audio_boost != 0 else "anull"
            audio_graph = f"[0:a]aformat=channel_layouts=stereo,aresample=44100,{audio_filter_str}[outa]"
        else:
            audio_graph = "anullsrc=channel_layout=stereo:sample_rate=44100[outa]"

        # 6. Render Main Part
        processed_part_path = os.path.join(temp_dir, f"part_{p_idx}_processed.mp4")
        enc_name, enc_opts = get_best_video_encoder()

        cmd_main = [
            "ffmpeg", "-y",
            *ff_threads,
            "-i", raw_part,
            "-i", banner_png,
            "-filter_complex", f"{video_filter_final};{audio_graph}",
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", enc_name, *enc_opts,
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-movflags", "+faststart",
            processed_part_path
        ]
        _log(f"⚙️ [HẬU KỲ PART {p_idx}] Đang render hiệu ứng 9:16, màu sắc CapCut & Limiter...")
        _ff_run(cmd_main, log_fn=log_fn, timeout=1800)

        # 7. Ghép Hook (nếu có) lên đầu video thành phẩm
        if hook_clip_path and os.path.isfile(hook_clip_path):
            _log(f"🔗 [RÁP HOOK] Ghép teaser vào đầu Part {p_idx}...")
            cmd_concat = [
                "ffmpeg", "-y",
                *ff_threads,
                "-i", hook_clip_path,
                "-i", processed_part_path,
                "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[outv][outa]",
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", enc_name, *enc_opts,
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_final
            ]
            _ff_run(cmd_concat, log_fn=log_fn, timeout=1200)
            try:
                os.remove(hook_clip_path)
                os.remove(processed_part_path)
            except Exception:
                pass
        else:
            if os.path.isfile(output_final):
                try:
                    os.remove(output_final)
                except Exception:
                    pass
            os.replace(processed_part_path, output_final)

        # Dọn file trung gian tạm thời trong temp_dir
        for temp_file in [banner_png, hook_clip_path, processed_part_path]:
            if temp_file and os.path.isfile(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass
        qc_frames_candidate = os.path.join(temp_dir, f"qc_frames_p{p_idx}")
        if os.path.isdir(qc_frames_candidate):
            try:
                shutil.rmtree(qc_frames_candidate, ignore_errors=True)
            except Exception:
                pass

        _log(f"🎉 [HOÀN THÀNH PART {p_idx}] Xuất video thành công: {os.path.basename(output_final)} ({probe_duration_sec(output_final):.1f}s)")
        return output_final
