"""Quy trình Tự Động Hóa 100% Zero-Touch: Chỉ Cần Gửi Link YouTube.

1. Tự Động Tải Video 1080p bằng yt-dlp chống chặn.
2. Tự Động Lấy Phụ Đề:
   - Ưu tiên kéo sub YouTube (Official Sub / Auto-captions) dạng .srt.
   - Fallback: Nếu video không có sub trên YouTube, tự động chạy faster-whisper GPU (CUDA) bóc sub ra .srt.
3. Chuyển giao trực tiếp cho Ban Đạo Diễn (1, 2, 3) và Đội Ngũ Kỹ Thuật (Visual Editor, Audio Mixer, Packaging).
4. Xuất xưởng trọn bộ Series Part 1 -> Part N hoàn hảo.
"""

import os
import sys
import glob
import subprocess
import logging
from pathlib import Path

logger = logging.getLogger("AutoYouTubePipeline")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


class AutoYouTubePipeline:
    """Pipeline tự động hóa từ YouTube URL đến Series Video Hoàn Chỉnh."""

    @staticmethod
    def download_video_and_sub(url: str, work_dir: str = r"E:\Video_summarizer_Pro\temp\yt_auto") -> tuple[str, str, str]:
        """Tự động tải video 1080p và tự động lấy/bóc sub .srt từ link YouTube.
        
        Trả về: (video_path, srt_path, video_title)
        """
        os.makedirs(work_dir, exist_ok=True)
        logger.info(f"🚀 [BƯỚC 1: TỰ ĐỘNG TẢI] Bắt đầu tải video từ YouTube: {url}")
        
        out_template = os.path.join(work_dir, "source_video.%(ext)s")
        
        # Lệnh yt-dlp tối ưu: Tải video 1080p + tải phụ đề tiếng Anh tự động
        cmd_dl = [
            "yt-dlp",
            "--format", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "--merge-output-format", "mp4",
            "--write-sub",
            "--write-auto-sub",
            "--sub-lang", "en",
            "--convert-subs", "srt",
            "--no-playlist",
            "-o", out_template,
            url
        ]
        
        subprocess.run(cmd_dl, check=True)
        
        # 1. Tìm video tải về
        video_path = os.path.join(work_dir, "source_video.mp4")
        if not os.path.exists(video_path):
            mp4_candidates = glob.glob(os.path.join(work_dir, "*.mp4"))
            if mp4_candidates:
                video_path = mp4_candidates[0]
            else:
                raise FileNotFoundError("Không tìm thấy file video sau khi tải từ YouTube!")
                
        # 2. Tìm file sub .srt được YouTube tải về
        srt_path = ""
        srt_candidates = glob.glob(os.path.join(work_dir, "*.srt"))
        if srt_candidates:
            srt_path = srt_candidates[0]
            logger.info(f"✅ [SUB PHÁT HIỆN] Đã tự động lấy được phụ đề YouTube: {srt_path}")
        else:
            # Nếu YouTube không có sẵn sub -> Tự động chạy faster-whisper bóc sub
            logger.info("⚠️ [WHISPER AI] YouTube không có phụ đề. Tự động kích hoạt Faster-Whisper GPU bóc sub...")
            srt_path = AutoYouTubePipeline.transcribe_with_whisper(video_path, work_dir)
            
        # Lấy tiêu đề video
        title = "YouTube Video"
        try:
            res_title = subprocess.run(
                ["yt-dlp", "--get-title", url],
                capture_output=True, text=True, check=True
            )
            title = res_title.stdout.strip()
        except Exception:
            pass
            
        logger.info(f"🎉 [INGEST HOÀN TẤT] Video: {video_path} | Sub: {srt_path} | Title: {title}")
        return video_path, srt_path, title

    @staticmethod
    def transcribe_with_whisper(video_path: str, work_dir: str) -> str:
        """Sử dụng faster-whisper bóc sub ra file .srt chuẩn."""
        try:
            import ctranslate2
            has_cuda = ctranslate2.get_cuda_device_count() > 0
        except Exception:
            try:
                import torch
                has_cuda = torch.cuda.is_available()
            except Exception:
                has_cuda = False
        
        device = "cuda" if has_cuda else "cpu"
        compute_type = "float16" if device == "cuda" else "int8"
        
        logger.info(f"Kích hoạt mô hình Faster-Whisper (device={device}, compute_type={compute_type})...")
        from faster_whisper import WhisperModel
        model = WhisperModel("base", device=device, compute_type=compute_type)
        
        segments, info = model.transcribe(video_path, beam_size=5, language="en")
        
        srt_path = os.path.join(work_dir, "auto_whisper.srt")
        idx = 1
        with open(srt_path, "w", encoding="utf-8") as f:
            for segment in segments:
                s_h = int(segment.start // 3600)
                s_m = int((segment.start % 3600) // 60)
                s_s = int(segment.start % 60)
                s_ms = int(round((segment.start - int(segment.start)) * 1000))
                
                e_h = int(segment.end // 3600)
                e_m = int((segment.end % 3600) // 60)
                e_s = int(segment.end % 60)
                e_ms = int(round((segment.end - int(segment.end)) * 1000))
                
                f.write(f"{idx}\n")
                f.write(f"{s_h:02d}:{s_m:02d}:{s_s:02d},{s_ms:03d} --> {e_h:02d}:{e_m:02d}:{e_s:02d},{e_ms:03d}\n")
                f.write(f"{segment.text.strip()}\n\n")
                idx += 1
                
        logger.info(f"✅ [WHISPER HOÀN TẤT] Đã tạo thành công file phụ đề: {srt_path}")
        return srt_path
