"""Tổng Chỉ Huy Quy Trình Hollywood AI Film Crew (Pipeline Orchestrator).

Vận hành chuỗi sản xuất 100% Zero-Touch:
1. INGEST: Nhận link YouTube hoặc file local -> Tự tải 1080p -> Tự bóc/kéo .srt.
2. BAN ĐẠO DIỄN AI:
   - Đạo diễn 1: Lập Master Series Plan (N Parts tuyến tính).
   - Đạo diễn 2: Lọc 4-5 phân cảnh cốt lõi (~18s - 22s/cảnh) thuận chiều thời gian.
   - Đạo diễn 3: Khóa câu thoại hoàn chỉnh (Zero Truncation), tính mốc Whoosh SFX & Tension BGM.
3. ĐỘI NGŨ THỰC THI (CREW):
   - Nhân viên 1 (Visual Editor): 9:16 Canvas blur 2 đầu, video 1188px, speaker centering, punch zoom 1.18x, Motion Blur Whip Transition (xfade=hblur).
   - Nhân viên 2 (Audio Master): Mix Whoosh SFX + Tension BGM bed + Loudness Normalization -14 LUFS.
   - Nhân viên 3 (Packaging): ASS Subtitle Safe Zone, Top Title Card, Bottom Part Badge.
4. XUẤT XƯỞNG: Trọn bộ N Parts sẵn sàng triệu view!
"""

from __future__ import annotations

import glob
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("PipelineOrchestrator")

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from auto_youtube_pipeline import AutoYouTubePipeline
from broll_semantic import parse_srt
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
from part_splitter_engine import probe_duration_sec


class PipelineOrchestrator:
    """Tổng Chỉ Huy Toàn Diện Quy Trình Hollywood AI Film Crew."""

    @classmethod
    def run_full_pipeline(
        cls,
        input_source: str,
        output_dir: str = r"E:\Video_summarizer_Pro\output\Hollywood_Series",
        part_count: int = 6,
        genre: str = "reality_drama",
        config: Optional[Dict[str, Any]] = None,
        log_fn: Optional[Callable[[str], None]] = None,
        work_dir: str = ""
    ) -> List[str]:
        """Quy trình chạy 1-click từ đầu vào đến danh sách video xuất xưởng."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        job_id = int(time.time())
        work_dir = work_dir or os.path.join(ROOT_DIR, "temp", f"hollywood_job_{job_id}")
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(output_dir, exist_ok=True)

        pipeline_t0 = time.time()
        pipeline_start_clock = time.strftime("%H:%M:%S")
        _log("==================================================================")
        _log(f"🚀 KHỞI ĐỘNG QUY TRÌNH SẢN XUẤT HOLLYWOOD AI FILM CREW (Lúc: {pipeline_start_clock})")
        _log("==================================================================")

        # -------------------------------------------------------------
        # BƯỚC 1: INGEST (TỰ ĐỘNG TẢI VIDEO & TỰ ĐỘNG LẤY/BÓC SUBTITLE)
        # -------------------------------------------------------------
        video_path = ""
        srt_path = ""
        video_title = ""

        is_youtube = input_source.strip().startswith("http") or "youtu" in input_source
        if is_youtube:
            _log(f"📥 [INGEST] Phát hiện link YouTube: {input_source}")
            _log("   ↳ Tự động tải video 1080p và tự động kéo/bóc phụ đề tiếng Anh...")
            yt_dir = os.path.join(work_dir, "yt_download")
            video_path, srt_path, video_title = AutoYouTubePipeline.download_video_and_sub(input_source, yt_dir)
        else:
            video_path = os.path.abspath(input_source)
            if not os.path.isfile(video_path):
                raise FileNotFoundError(f"Không tìm thấy video nguồn: {video_path}")
            video_title = os.path.splitext(os.path.basename(video_path))[0]
            _log(f"📁 [INGEST] Sử dụng file video local: {video_path}")

            # Tìm file srt có sẵn (cùng tên, caption.srt hoặc file srt trong cùng thư mục)
            custom_srt = config.get("srt_path") or config.get("subtitle_path")
            cand_srt = os.path.splitext(video_path)[0] + ".srt"
            caption_srt = os.path.join(os.path.dirname(video_path), "caption.srt")
            dir_srts = glob.glob(os.path.join(os.path.dirname(video_path), "*.srt"))

            if custom_srt and os.path.isfile(custom_srt):
                srt_path = custom_srt
                _log(f"   ✓ Đã nhận diện file phụ đề cấu hình: {srt_path}")
            elif os.path.isfile(cand_srt):
                srt_path = cand_srt
                _log(f"   ✓ Đã tìm thấy file phụ đề cùng tên: {srt_path}")
            elif os.path.isfile(caption_srt):
                srt_path = caption_srt
                _log(f"   ✓ Đã tìm thấy file phụ đề caption.srt: {srt_path}")
            elif dir_srts:
                srt_path = dir_srts[0]
                _log(f"   ✓ Đã tìm thấy file phụ đề trong thư mục: {srt_path}")
            else:
                # Nếu không có sub, tự động chạy faster-whisper GPU
                _log("   ⚠️ Video chưa có phụ đề. Tự động kích hoạt Faster-Whisper GPU CUDA...")
                srt_path = AutoYouTubePipeline.transcribe_with_whisper(video_path, work_dir)

        total_dur = probe_duration_sec(video_path)
        _log(f"⏱️ Video: {video_title} | Thời lượng gốc: {total_dur:.1f}s ({total_dur / 60:.1f} phút)")

        # Đọc dữ liệu cues từ file .srt
        cues = []
        if srt_path and os.path.isfile(srt_path):
            try:
                cues = parse_srt(srt_path)
            except Exception as e:
                logger.warning(f"Lỗi parse SRT cues: {e}")

        # -------------------------------------------------------------
        # BƯỚC 2: BAN ĐẠO DIỄN AI (STRATEGY & STORYTELLING)
        # -------------------------------------------------------------
        # 2.1. ĐẠO DIỄN 1: Tổng Đạo Diễn Cốt Truyện Vĩ Mô
        master_plan = MacroDirectorAI.plan_macro_series(
            cues=cues,
            total_duration=total_dur,
            part_count=part_count,
            genre=genre,
            master_title=video_title,
            config=config,
            log_fn=_log
        )

        series_title = master_plan.get("series_title", video_title)
        parts_list = master_plan.get("parts", [])
        target_p_idx = config.get("target_part_index")
        if target_p_idx is not None:
            parts_list = [p for p in parts_list if int(p.get("part_index", 0)) == int(target_p_idx)]
            if not parts_list:
                parts_list = master_plan.get("parts", [])[:1]

        _log(f"👑 [ĐẠO DIỄN 1] Hoàn thành phân bổ {len(parts_list)} Parts: '{series_title}'")

        exported_parts = []
        part_prefix = str(config.get("part_label_prefix", "PART")).strip() or "PART"
        if not part_prefix or part_prefix.lower() in ("part", "tập", "tap") or "tập" in part_prefix.lower():
            part_prefix = "PART"

        # -------------------------------------------------------------
        # BƯỚC 3 & 4: SẢN XUẤT TỪNG PART (ĐẠO DIỄN 2, 3 & ĐỘI NGŨ CREW)
        # -------------------------------------------------------------
        prev_part_end_sec = 0.0
        for p_idx, p_info in enumerate(parts_list, start=1):
            _log("------------------------------------------------------------------")
            _log(f"🎬 [SẢN XUẤT] BẮT ĐẦU DỰNG {part_prefix} {p_idx}/{len(parts_list)}: {p_info.get('part_title', '')}")
            _log("------------------------------------------------------------------")

            if prev_part_end_sec > 0.0 and float(p_info.get("start_sec", 0.0)) < prev_part_end_sec + 2.0:
                p_info["start_sec"] = round(prev_part_end_sec + 2.0, 2)

            part_work_dir = os.path.join(work_dir, f"part_{p_idx}")
            os.makedirs(part_work_dir, exist_ok=True)
            final_part_path = os.path.join(output_dir, f"{part_prefix}_{p_idx}.mp4")

            # 3.1. ĐẠO DIỄN 2: Lọc 4-5 phân cảnh LIỀN MẠCH (~20s/cảnh) không cắt vụn
            storyboard = SceneRhythmDirectorAI.direct_part_scenes(
                part_index=p_idx,
                part_info=p_info,
                cues=cues,
                total_duration=total_dur,
                target_scene_count=5,
                target_part_duration=float(config.get("target_part_duration", 95.0)),
                config=config,
                log_fn=_log
            )

            # 3.2. ĐẠO DIỄN 3: Khóa câu thoại hoàn chỉnh (Zero Truncation) & Lập Audio Spec
            audio_spec = AudioTransitionDirectorAI.audit_and_prepare_audio_spec(
                part_storyboard=storyboard,
                cues=cues,
                video_total_duration=total_dur,
                temp_dir=os.path.join(part_work_dir, "audio"),
                log_fn=_log
            )

            audited_scenes = audio_spec.get("audited_scenes", storyboard.get("scenes", []))
            if audited_scenes and audited_scenes[-1].get("shots"):
                prev_part_end_sec = max(prev_part_end_sec, float(audited_scenes[-1]["shots"][-1].get("end", 0.0)))

            # 4.1. NHÂN VIÊN 1: Visual Editor Engine
            # Render 9:16 Canvas Blur 2 đầu, video 1188px, speaker centering, punch zoom 1.18x, Motion Blur Whip xfade
            scenes_merged_path = os.path.join(part_work_dir, f"scenes_merged_p{p_idx}.mp4")
            merged_video, transition_timestamps = VisualEditorEngine.render_scenes_with_transitions(
                source_video=video_path,
                scenes=audited_scenes,
                output_merged_path=scenes_merged_path,
                work_dir=os.path.join(part_work_dir, "visual_cuts"),
                transition_duration=float(audio_spec.get("transition_duration", 0.35)),
                target_fg_h=int(config.get("video_center_height", 1188)),
                config=config,
                log_fn=_log
            )

            # 4.2. NHÂN VIÊN 2: Audio Mixing & Mastering Engine
            # Mix Whoosh SFX + Continuous Tension BGM Bed + Loudness Normalization -14 LUFS
            audio_mastered_path = os.path.join(part_work_dir, f"audio_mastered_p{p_idx}.mp4")
            mastered_audio_video = AudioMasterEngine.master_and_mix_audio(
                video_path=merged_video,
                transition_timestamps=transition_timestamps,
                audio_spec=audio_spec,
                output_path=audio_mastered_path,
                log_fn=_log
            )

            # 4.3. NHÂN VIÊN 3: Packaging & Typography Engine
            # Tạo phụ đề ASS Safe Zone (Arial Bold 48pt, viền đen 3.5px, Y=1500)
            ass_path = os.path.join(part_work_dir, f"safe_zone_p{p_idx}.ass")
            PackagingTypographerEngine.generate_safe_zone_ass(
                scenes=audited_scenes,
                cues=cues,
                output_ass_path=ass_path,
                transition_duration=float(audio_spec.get("transition_duration", 0.35)),
                config=config,
                video_path=mastered_audio_video,
                log_fn=_log
            )

            # Burn sub và dán Top Title Card (Y=301), Bottom Part Badge (Y=1519)
            final_rendered = PackagingTypographerEngine.burn_subtitles_and_package(
                audio_mastered_video=mastered_audio_video,
                ass_path=ass_path,
                master_title=series_title,
                part_index=p_idx,
                output_final=final_part_path,
                part_prefix=part_prefix,
                work_dir=part_work_dir,
                config=config,
                log_fn=_log
            )

            exported_parts.append(final_rendered)
            _log(f"🏆 [HOÀN TẤT] {part_prefix} {p_idx} đã xuất xưởng thành công: {final_rendered} ({probe_duration_sec(final_rendered):.1f}s)")

        pipe_total_sec = time.time() - pipeline_t0
        pipe_end_clock = time.strftime("%H:%M:%S")
        m_min = int(pipe_total_sec // 60)
        m_sec = int(pipe_total_sec % 60)

        parts_details = []
        tot_output_dur = 0.0
        for ep in exported_parts:
            if os.path.isfile(ep):
                dur_s = probe_duration_sec(ep)
                sz_mb = os.path.getsize(ep) / (1024 * 1024)
                tot_output_dur += dur_s
                parts_details.append((os.path.basename(ep), dur_s, sz_mb))

        v_min = int(tot_output_dur // 60)
        v_sec = int(tot_output_dur % 60)
        avg_time = pipe_total_sec / max(1, len(exported_parts))
        speed_ratio = tot_output_dur / max(0.1, pipe_total_sec)

        _log("================================================================================")
        _log(f"🎉 [TỔNG KẾT HOÀN TẤT] XUẤT XƯỞNG TOÀN DIỆN {len(exported_parts)} PARTS HOLLYWOOD THÀNH CÔNG!")
        _log(f"⏱️ Bắt đầu lúc: {pipeline_start_clock} | Hoàn tất lúc: {pipe_end_clock}")
        _log(f"⏳ TỔNG THỜI GIAN LÀM VIỆC: {m_min} phút {m_sec:02d} giây ({pipe_total_sec:.1f} giây)")
        _log(f"⚡ Tốc độ sản xuất trung bình: {avg_time:.1f}s / Part | Hiệu suất: {speed_ratio:.2f}x Realtime")
        _log(f"🎬 Tổng thời lượng video thành phẩm: {v_min} phút {v_sec:02d} giây ({tot_output_dur:.1f}s)")
        _log(f"📁 Thư mục lưu trữ: {output_dir}")
        _log("📦 Bảng chi tiết thành phẩm xuất xưởng:")
        for name, dur_s, size_mb in parts_details:
            _log(f"   • {name}: Thời lượng {dur_s:.2f}s | Dung lượng {size_mb:.1f} MB")
        _log("================================================================================")

        return exported_parts
