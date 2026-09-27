"""
Xử lý nguồn video có sẵn trên máy — không tải YouTube, không gọi yt-dlp.

Luồng:
1. Copy/link file gốc vào temp/<slug>/source_video.mp4 (không sửa file user).
2. Render low-res 360p → source_video_low.mp4 (ffmpeg, cùng scale với nhánh YouTube).
3. Xuất sub: sidecar / stream phụ đề nhúng / Whisper qua AIProcessor.
4. Trả về dict cùng shape với nhánh download để AI + TTS + editor nhận được.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("LocalSourceProcessor")

from downloader_processor import DownloaderProcessor

try:
    from ai_processor import AIProcessor
except ImportError:
    AIProcessor = None

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".flv", ".ts", ".mpeg", ".mpg", ".wmv"}
SUB_EXTS = {".srt", ".vtt", ".ass", ".ssa"}


class LocalSourceProcessor:
    """Chuẩn bị clip local thành source_video.mp4 + low-res + transcript cho pipeline hiện tại."""

    @staticmethod
    def _run(cmd, check=True, capture=True):
        kwargs = {"check": check}
        if capture:
            kwargs["stdout"] = subprocess.PIPE
            kwargs["stderr"] = subprocess.PIPE
            kwargs["text"] = True
            kwargs["encoding"] = "utf-8"
            kwargs["errors"] = "replace"
        if CREATE_NO_WINDOW:
            kwargs["creationflags"] = CREATE_NO_WINDOW
        return subprocess.run(cmd, **kwargs)

    @classmethod
    def resolve_source_file(cls, path: str) -> str:
        """Nhận file video hoặc thư mục chứa video → đường dẫn file nguồn."""
        if not path or not str(path).strip():
            raise FileNotFoundError("❌ Chưa chọn đường dẫn video có sẵn.")
        path = os.path.abspath(os.path.expanduser(str(path).strip()))
        if not os.path.exists(path):
            raise FileNotFoundError(f"❌ Không tìm thấy đường dẫn: {path}")

        if os.path.isfile(path):
            ext = os.path.splitext(path)[1].lower()
            if ext not in VIDEO_EXTS:
                raise ValueError(f"❌ File không phải video hỗ trợ ({ext}). Dùng mp4/mkv/mov/avi/webm...")
            return path

        preferred = os.path.join(path, "source_video.mp4")
        if os.path.isfile(preferred):
            return preferred

        found = []
        for name in os.listdir(path):
            full = os.path.join(path, name)
            if os.path.isfile(full) and os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                found.append(full)
        if not found:
            raise FileNotFoundError(f"❌ Thư mục không chứa file video: {path}")
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        return found[0]

    @classmethod
    def probe_duration(cls, video_path: str) -> float:
        try:
            res = cls._run([
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ], check=False)
            val = (res.stdout or "").strip()
            if val:
                return float(val)
        except Exception as e:
            logger.warning(f"⚠️ Không đọc được duration bằng ffprobe: {e}")
        return 0.0

    @classmethod
    def _safe_remove(cls, dest: str) -> None:
        if os.path.exists(dest) or os.path.islink(dest):
            try:
                os.remove(dest)
            except OSError:
                pass

    @classmethod
    def ingest_to_source_mp4(cls, src: str, dest: str) -> str:
        """
        Đưa file user vào dest (source_video.mp4) mà không sửa file gốc.
        Ưu tiên hardlink → copy. File khác mp4 thì remux/transcode bằng ffmpeg.
        """
        src_abs = os.path.abspath(src)
        dest_abs = os.path.abspath(dest)
        os.makedirs(os.path.dirname(dest_abs), exist_ok=True)

        if src_abs == dest_abs:
            logger.info(f"📎 File nguồn đã đúng vị trí pipeline: {dest_abs}")
            return dest_abs

        cls._safe_remove(dest_abs)
        ext = os.path.splitext(src_abs)[1].lower()

        if ext == ".mp4":
            try:
                os.link(src_abs, dest_abs)
                logger.info(f"🔗 Hardlink source_video.mp4 (file gốc nguyên vẹn): {src_abs}")
                return dest_abs
            except OSError:
                pass
            shutil.copy2(src_abs, dest_abs)
            logger.info(f"📋 Đã copy source_video.mp4 (file gốc nguyên vẹn): {src_abs}")
            return dest_abs

        logger.info(f"⏳ Đang remux/transcode {ext} → source_video.mp4 (không sửa file gốc)...")
        try:
            cls._run([
                "ffmpeg", "-y", "-i", src_abs,
                "-c", "copy", "-movflags", "+faststart",
                dest_abs,
            ], check=True)
        except Exception:
            cls._safe_remove(dest_abs)
            cls._run([
                "ffmpeg", "-y", "-i", src_abs,
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100",
                "-movflags", "+faststart",
                dest_abs,
            ], check=True)
        if not os.path.exists(dest_abs) or os.path.getsize(dest_abs) <= 0:
            raise RuntimeError("❌ Không tạo được source_video.mp4 từ file local.")
        logger.info(f"✅ Đã xuất source_video.mp4 tại: {dest_abs}")
        return dest_abs

    @classmethod
    def render_low_res(cls, video_path: str, low_res_path: str) -> str:
        """Render 360p giống fallback FFmpeg của DownloaderProcessor — không tải YouTube."""
        logger.info("⏳ [LOCAL] Đang render bản low-res 360p bằng FFmpeg (không yt-dlp)...")
        DownloaderProcessor.render_low_res_360p(video_path, low_res_path)
        if not os.path.exists(low_res_path) or os.path.getsize(low_res_path) <= 0:
            raise RuntimeError("❌ Render source_video_low.mp4 thất bại.")
        logger.info(f"✅ [LOCAL] Đã render low-res 360p tại: {low_res_path}")
        return low_res_path

    @classmethod
    def extract_audio_wav(cls, video_path: str, audio_path: str) -> str:
        logger.info("⏳ [LOCAL] Đang trích audio WAV cho Whisper/AI...")
        cls._run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1",
            audio_path,
        ], check=True)
        logger.info(f"✅ [LOCAL] Audio WAV: {audio_path}")
        return audio_path

    @classmethod
    def _srt_to_plain_text(cls, srt_path: str) -> str:
        if not os.path.exists(srt_path):
            return ""
        with open(srt_path, "r", encoding="utf-8", errors="replace") as f:
            raw = f.read()
        if AIProcessor is not None:
            return (AIProcessor._parse_subtitle_content(raw) or "").strip()
        lines = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.isdigit() or "-->" in line:
                continue
            if line.startswith("WEBVTT") or line.startswith("Kind:") or line.startswith("Language:"):
                continue
            clean = re.sub(r"<[^<]+?>", "", line)
            if clean:
                lines.append(clean)
        return " ".join(lines).strip()

    @classmethod
    def _find_sidecar_sub(cls, video_path: str) -> str:
        stem = os.path.splitext(video_path)[0]
        for ext in (".srt", ".vtt", ".ass", ".ssa"):
            candidate = stem + ext
            if os.path.isfile(candidate):
                return candidate
        parent = os.path.dirname(video_path)
        for name in os.listdir(parent):
            if os.path.splitext(name)[1].lower() in SUB_EXTS:
                return os.path.join(parent, name)
        return ""

    @classmethod
    def _convert_sub_file_to_srt(cls, src_sub: str, dest_srt: str) -> bool:
        ext = os.path.splitext(src_sub)[1].lower()
        if ext == ".srt":
            shutil.copy2(src_sub, dest_srt)
            return os.path.exists(dest_srt) and os.path.getsize(dest_srt) > 0
        try:
            cls._run([
                "ffmpeg", "-y", "-i", src_sub, "-c:s", "srt", dest_srt,
            ], check=True)
            return os.path.exists(dest_srt) and os.path.getsize(dest_srt) > 0
        except Exception:
            return False

    @classmethod
    def _extract_embedded_sub(cls, video_path: str, dest_srt: str) -> bool:
        try:
            probe = cls._run([
                "ffprobe", "-v", "error",
                "-select_streams", "s",
                "-show_entries", "stream=index,codec_name",
                "-of", "csv=p=0",
                video_path,
            ], check=False)
            streams = [ln.strip() for ln in (probe.stdout or "").splitlines() if ln.strip()]
            if not streams:
                return False
            logger.info(f"📝 [LOCAL] Phát hiện {len(streams)} stream phụ đề nhúng, đang extract...")
            cls._run([
                "ffmpeg", "-y", "-i", video_path,
                "-map", "0:s:0", "-c:s", "srt",
                dest_srt,
            ], check=True)
            return os.path.exists(dest_srt) and os.path.getsize(dest_srt) > 20
        except Exception as e:
            logger.warning(f"⚠️ [LOCAL] Không extract được sub nhúng: {e}")
            return False

    @staticmethod
    def _format_srt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        milliseconds = int((seconds - int(seconds)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{milliseconds:03d}"

    @classmethod
    def _whisper_on_local_audio(cls, audio_path: str, output_dir: str) -> str:
        """
        Whisper trực tiếp trên extracted_audio.wav.
        Không gọi AIProcessor.transcribe_audio (tránh YouTube API + GPU cublas rồi chạy lại CPU).
        Local luôn device=cpu, một lần.
        """
        if not audio_path or not os.path.exists(audio_path):
            raise FileNotFoundError(f"❌ Không tìm thấy file âm thanh local: {audio_path}")
        os.makedirs(output_dir, exist_ok=True)
        srt_output = os.path.join(output_dir, "transcript_sub.srt")
        logger.info("🖥️ [LOCAL] Whisper CPU trên extracted_audio.wav (không YouTube API, không GPU)...")

        from faster_whisper import WhisperModel
        model = WhisperModel("base", device="cpu", compute_type="int8")
        # Để Whisper tự nhận diện ngôn ngữ nguồn; ngôn ngữ thành phẩm được
        # quyết định riêng bởi cấu hình thị trường.
        segments, _ = model.transcribe(audio_path, beam_size=5)

        srt_lines = []
        text_lines = []
        for i, seg in enumerate(segments, start=1):
            start_str = cls._format_srt_time(seg.start)
            end_str = cls._format_srt_time(seg.end)
            text_clean = seg.text.strip()
            srt_lines.append(f"{i}\n{start_str} --> {end_str}\n{text_clean}\n")
            text_lines.append(text_clean)

        transcript_text = " ".join(text_lines)
        with open(srt_output, "w", encoding="utf-8") as f_srt:
            f_srt.write("\n".join(srt_lines))
        logger.info(f"✅ [LOCAL] Whisper CPU xong, transcript_sub.srt ({len(transcript_text)} ký tự).")
        if not (transcript_text or "").strip():
            logger.warning("🎥 [VISUAL-ONLY] Video local không có lời tin cậy; chuyển sang Gemini đọc hình.")
        return transcript_text

    @classmethod
    def export_transcript(cls, video_path: str, audio_path: str, output_dir: str, original_src: str = "") -> str:
        """
        Ưu tiên sidecar / sub nhúng; không được thì Whisper CPU trong file này.
        Không gọi AIProcessor.transcribe_audio.
        """
        os.makedirs(output_dir, exist_ok=True)
        srt_output = os.path.join(output_dir, "transcript_sub.srt")

        sidecar = ""
        if original_src:
            sidecar = cls._find_sidecar_sub(original_src)
        if not sidecar:
            sidecar = cls._find_sidecar_sub(video_path)

        if sidecar:
            logger.info(f"📝 [LOCAL] Dùng file sub cạnh video: {sidecar}")
            if cls._convert_sub_file_to_srt(sidecar, srt_output):
                text = cls._srt_to_plain_text(srt_output)
                if len(text) > 20:
                    logger.info(f"✅ [LOCAL] Xuất sub sidecar thành transcript_sub.srt ({len(text)} ký tự).")
                    return text

        if cls._extract_embedded_sub(video_path, srt_output):
            text = cls._srt_to_plain_text(srt_output)
            if len(text) > 20:
                logger.info(f"✅ [LOCAL] Xuất sub nhúng thành transcript_sub.srt ({len(text)} ký tự).")
                return text

        logger.info("🎙️ [LOCAL] Không có sub sẵn — Whisper trên audio file local (không gọi YouTube)...")
        return cls._whisper_on_local_audio(audio_path, output_dir)

    @classmethod
    def prepare(cls, source_path: str, with_transcript: bool = True,
                work_key: str = "", skip_whisper: bool = False) -> dict:
        """
        Entry point khi mode Local + Start.
        Không gọi yt-dlp / YouTube. Trả dict để main.py nhảy vào AI + TTS + editor.
        """
        src = cls.resolve_source_file(source_path)
        title = os.path.splitext(os.path.basename(src))[0] or "local_video"
        logger.info(f"📁 [LOCAL] Nguồn: {src}")

        # Part jobs provide a short unique key. Without it, the legacy folder
        # helper keeps only the first 20 title characters, so long Part titles
        # can collide and silently reuse another Part's AI/timeline cache.
        specific_dir = DownloaderProcessor.get_safe_video_folder(work_key or title)
        logger.info(f"📁 [LOCAL] Thư mục làm việc: {specific_dir}")

        try:
            title_file = os.path.join(specific_dir, "original_title.txt")
            with open(title_file, "w", encoding="utf-8") as f_title:
                f_title.write(title)
        except Exception:
            pass

        video_path = os.path.join(specific_dir, "source_video.mp4")
        if DownloaderProcessor.source_is_usable(video_path):
            logger.info("⏭️ [LOCAL] Đã có source_video.mp4 hợp lệ — bỏ qua copy/remux.")
        else:
            cls.ingest_to_source_mp4(src, video_path)

        duration = cls.probe_duration(video_path)
        low_res_path = os.path.join(specific_dir, "source_video_low.mp4")
        if DownloaderProcessor.low_res_is_usable(low_res_path, expected_duration=duration):
            logger.info("⏭️ [LOCAL] Đã có source_video_low.mp4 hợp lệ — bỏ qua render.")
        else:
            cls.render_low_res(video_path, low_res_path)

        audio_path = os.path.join(specific_dir, "extracted_audio.wav") if with_transcript else ""
        transcript = ""
        if not with_transcript:
            logger.info("⏭️ [LOCAL] AI + Voice đã tắt — bỏ trích WAV, subtitle và Whisper.")
        else:
            prepared_srt = os.path.join(specific_dir, "transcript_sub.srt")
            if os.path.isfile(prepared_srt):
                transcript = cls._srt_to_plain_text(prepared_srt)
            if transcript:
                logger.info("⏭️ [LOCAL] Đã có subtitle Part chuẩn bị sẵn — bỏ trích WAV/Whisper.")
            elif skip_whisper:
                with open(prepared_srt, "w", encoding="utf-8") as stream:
                    stream.write("")
                logger.info("🎥 [VISUAL-ONLY] Part không có caption — bỏ Whisper, Gemini tự xem video.")
            else:
                wav_ok = (
                    os.path.exists(audio_path)
                    and os.path.getsize(audio_path) > 16 * 1024
                    and cls.probe_duration(audio_path) > 1.0
                )
                if wav_ok:
                    logger.info("⏭️ [LOCAL] Đã có extracted_audio.wav hợp lệ — bỏ qua trích xuất.")
                else:
                    try:
                        cls.extract_audio_wav(video_path, audio_path)
                    except Exception as exc:
                        logger.warning("🎥 [VISUAL-ONLY] Nguồn không có audio dùng được: %s", exc)

                try:
                    transcript = cls.export_transcript(
                        video_path=video_path,
                        audio_path=audio_path,
                        output_dir=specific_dir,
                        original_src=src,
                    )
                except Exception as exc:
                    logger.warning("🎥 [VISUAL-ONLY] Không có transcript tin cậy: %s", exc)
                    transcript = ""
                    with open(prepared_srt, "w", encoding="utf-8") as stream:
                        stream.write("")

        return {
            "video_path": video_path,
            "audio_path": audio_path,
            "low_res_path": low_res_path,
            "title": title,
            "duration": duration,
            "specific_dir": specific_dir,
            "transcript": transcript,
        }
