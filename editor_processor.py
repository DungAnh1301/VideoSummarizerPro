import os
import sys
import shutil
import subprocess
import random
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from hardware_manager import run_ffmpeg_auto

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("EditorProcessor")

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class EditorProcessor:
    """
    Module xử lý Biên tập và Xuất Video Final (Bước 3) - Thuật toán Chấm điểm OpenCV & Hậu kỳ Nâng cao:
    - Chia nhỏ video nguồn thành các đoạn 6s[cite: 1, 2].
    - Dùng OpenCV phân tích chuyển động và bộ lọc astats của FFmpeg đo đỉnh sóng âm thanh action dồn dập[cite: 1, 2].
    - Sắp xếp các phân cảnh điểm cao lên ngay sau đoạn Hook, tạo nhịp độ cuốn hút đỉnh cao[cite: 1, 2].
    - Hiệu ứng lóe sáng (Flash Transition) và khoảng nghỉ 1 giây giữa Hook và Voice AI[cite: 1, 2].
    - Khớp hoàn toàn thời lượng hình ảnh và âm thanh, chống hiện tượng kịch câm ở cuối video[cite: 1, 2].
    - Hỗ trợ tiêu đề động tự động co giãn font béo tốt, bo tròn góc ôm sát chữ và sub câu dài mượt mà theo tốc độ[cite: 1, 2].
    """

    # Crop UI / Path A luôn thiết kế trên canvas 16:9 này, không phụ thuộc resolution file tải về.
    CANVAS_W = 1920
    CANVAS_H = 1080
    OUTPUT_W = 1080
    OUTPUT_H = 1920
    # Hình phải dài hơn audio (TTS đã mux) ít nhất 1s — không cắt giọng.
    VISUAL_TAIL_AFTER_AUDIO_SEC = 1.0

    DEFAULT_TITLE_LINE1 = "VIRAL VIDEO MOMENT"
    DEFAULT_TITLE_LINE2 = "MUST WATCH CONTENT"

    # Banner title trên canvas 9:16 1080x1920. Pill ~1000px (W chữ = 950).
    # FONT_MIN=48: Impact 48px vẫn là title đọc được; 24px cũ biến title dài thành caption.
    # FONT_MAX_1LINE=76: 1 dòng lấp pill, chưa quá to so với 2 dòng.
    # FONT_MAX_2LINE=64: 2 dòng vẫn là title; câu dài thường fit ~48–52px.
    BANNER_TEXT_MAX_W = 950
    BANNER_PAD_X = 28
    BANNER_PAD_Y = 18
    # 32px trên canvas 1080 vẫn đọc rõ và giúp title nguồn dài giữ đủ chữ
    # trong đúng hai dòng, thay vì cắt mất payoff bằng dấu ba chấm.
    BANNER_FONT_MIN = 32
    BANNER_FONT_MAX_1LINE = 76
    BANNER_FONT_MAX_2LINE = 64
    BANNER_LINE_GAP_RATIO = 0.18
    BANNER_CORNER_RADIUS = 20
    BANNER_FONT_PATHS = (
        "C:/Windows/Fonts/impact.ttf",
        "C:/Windows/Fonts/ariblk.ttf",
    )

    @staticmethod
    def _even(n: int) -> int:
        n = int(n)
        return n - (n % 2)

    @staticmethod
    def _silence_opencv_ffmpeg_logs():
        os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "8"  # AV_LOG_FATAL — ẩn partial file

    @staticmethod
    def _has_audio(video_path: str) -> bool:
        if not video_path or not os.path.isfile(video_path):
            return False
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type", "-of", "csv=p=0", video_path,
        ], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
           creationflags=CREATE_NO_WINDOW)
        return "audio" in (result.stdout or "").lower()

    @classmethod
    def resolve_low_res_for_visual(cls, specific_dir: str, source_video: str) -> tuple:
        """
        Chấm hình Timesub/OpenCV CHỈ dùng source_video_low.mp4.
        Timeline (safe start/end) lấy duration từ source_video.mp4.
        File low rác/stub → transcode 360p; vẫn hỏng → bỏ chấm hình.
        """
        from downloader_processor import DownloaderProcessor

        cls._silence_opencv_ffmpeg_logs()
        expected = DownloaderProcessor.probe_duration_sec(source_video)
        if expected <= 0:
            cap_dur = 0.0
            try:
                import cv2
                cap = cv2.VideoCapture(source_video)
                fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
                cap.release()
                cap_dur = (frames / fps) if fps else 0.0
            except Exception:
                cap_dur = 0.0
            expected = cap_dur or 60.0

        low = os.path.join(specific_dir, "source_video_low.mp4")
        if DownloaderProcessor.low_res_is_usable(low, expected):
            size_mb = os.path.getsize(low) / (1024 * 1024)
            logger.info(f"🎞️ [VISUAL] Chấm hình trên source_video_low.mp4 ({size_mb:.1f} MB)")
            return low, expected

        if os.path.exists(low):
            size = os.path.getsize(low)
            logger.warning(
                f"⚠️ [VISUAL] source_video_low.mp4 không dùng được ({size} bytes, "
                "stub/partial) — transcode 360p từ source_video.mp4."
            )
        else:
            logger.warning("⚠️ [VISUAL] Thiếu source_video_low.mp4 — transcode 360p từ bản gốc.")

        try:
            DownloaderProcessor.render_low_res_360p(source_video, low)
            if DownloaderProcessor.low_res_is_usable(low, expected):
                size_mb = os.path.getsize(low) / (1024 * 1024)
                logger.info(f"✅ [VISUAL] Đã render lại 360p ({size_mb:.1f} MB) cho chấm hình.")
                return low, expected
        except Exception as e:
            logger.warning(f"⚠️ [VISUAL] Transcode 360p thất bại: {e}")

        logger.warning(
            "⚠️ [VISUAL] Bỏ chấm hình trên low-res (file rác). "
            "Timesub chỉ dùng điểm nghĩa; OpenCV chia đều theo thời gian."
        )
        return "", expected

    BROLL_SCAN_START_RATIO = 0.02
    BROLL_SCAN_END_RATIO = 0.98

    @classmethod
    def _fill_intervals_to_duration(cls, clips: list, required_duration: float) -> list:
        """Trường hợp 1: giữ clip độc nhất. Trường hợp 2: lặp interval nếu tổng vẫn thiếu."""
        if not clips:
            return []
        required_duration = float(required_duration or 0.0)
        filled = []
        acc = 0.0
        for c in clips:
            s, e = float(c.get("start", 0)), float(c.get("end", 0))
            if e - s <= 0.05:
                continue
            filled.append({"start": s, "end": e})
            acc += e - s
        if acc + 0.05 >= required_duration:
            logger.info(f"✅ [B-ROLL] Đủ cảnh độc nhất: {acc:.2f}s ≥ {required_duration:.2f}s.")
            return filled
        logger.warning(
            f"⚠️ [B-ROLL] Độc nhất chỉ {acc:.2f}s < cần {required_duration:.2f}s — lặp clip cho đủ."
        )
        unique = list(filled)
        i = 0
        while acc + 0.05 < required_duration and unique and i < 400:
            src = unique[i % len(unique)]
            dur = src["end"] - src["start"]
            need = required_duration - acc
            if dur > need + 0.05:
                filled.append({"start": src["start"], "end": src["start"] + need})
                acc += need
            else:
                filled.append({"start": src["start"], "end": src["end"]})
                acc += dur
            i += 1
        logger.info(f"🔁 [B-ROLL] Sau lặp: {acc:.2f}s / {len(filled)} đoạn.")
        return filled

    @classmethod
    def ensure_broll_files_duration(cls, clip_paths: list, required_duration: float, temp_dir: str) -> list:
        """Sau khi cắt file: đủ thì giữ; thiếu thì copy file đã cắt (lặp)."""
        paths = list(clip_paths or [])
        if not paths:
            return paths
        acc = cls.sum_clip_durations(paths)
        if acc + 0.2 >= float(required_duration or 0):
            logger.info(f"✅ [B-ROLL] File cắt đủ {acc:.2f}s ≥ {required_duration:.2f}s.")
            return paths
        logger.warning(
            f"⚠️ [B-ROLL] File cắt {acc:.2f}s < cần {required_duration:.2f}s — nhân bản file."
        )
        base = list(paths)
        extra = 0
        while acc + 0.2 < float(required_duration) and extra < 400:
            src = base[extra % len(base)]
            dup = os.path.join(temp_dir, f"highlight_broll_loop_{extra + 1}.mp4")
            try:
                shutil.copy2(src, dup)
                paths.append(dup)
                acc += cls.probe_media_duration(dup) or 0.0
            except Exception as e:
                logger.warning(f"⚠️ Không copy clip lặp: {e}")
                break
            extra += 1
        logger.info(f"🔁 [B-ROLL] Sau nhân bản file: {acc:.2f}s / {len(paths)} file.")
        return paths

    @classmethod
    def _even_time_clips(cls, safe_start: float, safe_end: float, required_duration: float) -> list:
        chunk = 6.0
        clips = []
        t = float(safe_start)
        end = float(safe_end)
        while t + 0.4 < end:
            clips.append({"start": t, "end": min(end, t + chunk)})
            t += chunk
        if not clips:
            return [{"start": safe_start, "end": min(safe_end, safe_start + max(required_duration, 2.0))}]
        chosen = []
        acc = 0.0
        i = 0
        while acc < required_duration and clips:
            c = clips[i % len(clips)]
            dur = c["end"] - c["start"]
            if acc + dur > required_duration:
                chosen.append({"start": c["start"], "end": c["start"] + (required_duration - acc)})
                break
            chosen.append({"start": c["start"], "end": c["end"]})
            acc += dur
            i += 1
            if i > 400:
                break
        return chosen

    @staticmethod
    def split_banner_title(raw_title: str) -> tuple:
        """
        Tách title banner thành (dòng 1, dòng 2) cho title_color1 / title_color2.
        - Có xuống dòng thật (hoặc chuỗi \\n): dùng đúng 2 dòng đó.
        - 1 dòng: cả banner màu 1 — KHÔNG cắt giữa số từ.
        """
        if raw_title is None:
            return "", ""
        text = str(raw_title).replace("\r\n", "\n").replace("\r", "\n")
        if "\n" not in text and "\\n" in text:
            text = text.replace("\\n", "\n")

        lines = []
        for part in text.split("\n"):
            cleaned = part.strip().strip("'\"").strip()
            if not cleaned or cleaned.startswith("#"):
                continue
            lines.append(cleaned.upper())
            if len(lines) >= 2:
                break

        if not lines:
            return "", ""
        if len(lines) == 1:
            return lines[0], ""
        return lines[0], lines[1]

    @classmethod
    def read_banner_title_lines(cls, specific_dir: str, title_file_path: str = None, summary_path: str = None) -> tuple:
        """Đọc original_title.txt (ưu tiên) hoặc summary_output.txt, tách đúng 1/2 dòng banner."""
        title_file = title_file_path or os.path.join(specific_dir, "original_title.txt")
        summary = summary_path or os.path.join(specific_dir, "summary_output.txt")
        target = title_file if os.path.exists(title_file) else summary
        if os.path.exists(target):
            try:
                with open(target, "r", encoding="utf-8") as f_t:
                    content = f_t.read()
                line1, line2 = cls.split_banner_title(content)
                if line1 or line2:
                    return line1, line2
            except Exception:
                pass
        return cls.DEFAULT_TITLE_LINE1, cls.DEFAULT_TITLE_LINE2

    @staticmethod
    def sanitize_windows_filename(name: str, max_len: int = 120) -> str:
        """Tên file Windows: bỏ xuống dòng, ký tự cấm, dấu chấm/khoảng trắng cuối, rút gọn."""
        if name is None:
            name = ""
        text = str(name).replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
        text = re.sub(r'[<>:"/\\|?*]', " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        text = text.rstrip(". ")
        reserved = {"CON", "PRN", "AUX", "NUL"}
        reserved.update(f"COM{i}" for i in range(1, 10))
        reserved.update(f"LPT{i}" for i in range(1, 10))
        if not text:
            text = "video_xuat"
        elif text.upper() in reserved:
            text = f"_{text}"
        if len(text) > max_len:
            text = text[:max_len].rstrip(". ") or "video_xuat"
        return text

    @classmethod
    def read_banner_title_raw(cls, specific_dir: str) -> str:
        """Title banner (DeepSeek / original_title.txt), không dùng slug thư mục YouTube."""
        title_file = os.path.join(specific_dir, "original_title.txt")
        summary = os.path.join(specific_dir, "summary_output.txt")
        raw = ""
        for path in (title_file, summary):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except Exception:
                continue
            if not content:
                continue
            text = content.replace("\r\n", "\n").replace("\r", "\n")
            if "\n" not in text and "\\n" in text:
                text = text.replace("\\n", "\n")
            parts = []
            for part in text.split("\n"):
                cleaned = part.strip().strip("'\"").strip()
                if not cleaned or cleaned.startswith("#"):
                    continue
                parts.append(cleaned)
                if len(parts) >= 2:
                    break
            if parts:
                raw = " ".join(parts)
                break
        if not raw:
            line1, line2 = cls.read_banner_title_lines(specific_dir)
            raw = " ".join(p for p in (line1, line2) if p)
        return raw or "video_xuat"

    @staticmethod
    def unique_titled_mp4_path(dest_dir: str, stem: str) -> str:
        candidate = os.path.join(dest_dir, f"{stem}.mp4")
        if not os.path.exists(candidate):
            return candidate
        n = 2
        while n <= 9999:
            candidate = os.path.join(dest_dir, f"{stem}_{n}.mp4")
            if not os.path.exists(candidate):
                return candidate
            n += 1
        raise RuntimeError("❌ Quá nhiều file trùng tên trong thư mục output.")

    @classmethod
    def _is_safe_job_temp_dir(cls, specific_dir: str) -> bool:
        """Chỉ cho phép xóa đúng 1 folder job dưới temp/, không xóa temp gốc hay job khác."""
        if not specific_dir:
            return False
        abs_job = os.path.abspath(specific_dir)
        if not os.path.isdir(abs_job):
            return False
        abs_temp = os.path.abspath(os.path.join(str(ROOT_DIR), "temp"))
        job_norm = os.path.normcase(abs_job)
        temp_norm = os.path.normcase(abs_temp)
        if job_norm == temp_norm:
            return False
        prefix = temp_norm + os.sep
        if not job_norm.startswith(prefix):
            return False
        rel = os.path.relpath(abs_job, abs_temp)
        parts = [p for p in rel.split(os.sep) if p and p not in (".", "..")]
        return len(parts) == 1 and ".." not in rel.split(os.sep)

    @classmethod
    def finalize_exported_video(cls, specific_dir: str, exported_mp4: str, post_options: dict = None) -> str:
        """
        Sau xuất thành công: copy video ra output\\ với tên = title banner,
        rồi (tuỳ checkbox) mới xóa đúng thư mục temp của job này.
        """
        post_options = post_options or {}
        if not exported_mp4 or not os.path.isfile(exported_mp4):
            raise FileNotFoundError(f"❌ Không tìm thấy video vừa xuất: {exported_mp4}")
        src_size = os.path.getsize(exported_mp4)
        if src_size <= 0:
            raise RuntimeError("❌ File video xuất ra rỗng — không đổi tên / không xóa thư mục tạm.")

        # Chốt cuối độc lập với prompt/TTS/editor: một job có giọng AI không
        # bao giờ được báo thành công nếu file cuối thấp hơn 80% mốc người dùng.
        if bool(post_options.get("use_ai_voice", False)):
            try:
                requested_duration = max(
                    30.0,
                    min(900.0, float(post_options.get("video_min_duration_sec", 90.0) or 90.0)),
                )
            except (TypeError, ValueError):
                requested_duration = 90.0
            output_duration = cls.probe_media_duration(exported_mp4)
            minimum_safe_duration = requested_duration * 0.80
            if output_duration + 0.05 < minimum_safe_duration:
                raise RuntimeError(
                    f"❌ Video cuối chỉ dài {output_duration:.1f}s, thấp hơn mức an toàn "
                    f"{minimum_safe_duration:.1f}s của cấu hình {requested_duration:.1f}s. "
                    "Không copy sang output, không xóa temp."
                )
            logger.info(
                "✅ [DURATION GUARD] Video cuối %.1fs đạt mức an toàn %.1fs.",
                output_duration, minimum_safe_duration,
            )

        output_dir = os.path.join(str(ROOT_DIR), "output")
        os.makedirs(output_dir, exist_ok=True)

        stem = cls.sanitize_windows_filename(cls.read_banner_title_raw(specific_dir))
        suffix = cls.sanitize_windows_filename(str(post_options.get("output_suffix") or "").strip())
        if suffix:
            stem = f"{stem} - {suffix}"
        dest = cls.unique_titled_mp4_path(output_dir, stem)

        shutil.copy2(exported_mp4, dest)
        if not os.path.isfile(dest) or os.path.getsize(dest) < src_size:
            raise RuntimeError(f"❌ Sao chép video ra output thất bại: {dest}")

        logger.info(f"📁 [XUẤT] Đã lưu video theo tên title banner: {dest}")

        cleanup = bool(post_options.get("cleanup_temp_after_export", False))
        if not cleanup:
            logger.info(f"📂 [XUẤT] Giữ thư mục tạm (checkbox tắt): {specific_dir}")
            return dest

        if not cls._is_safe_job_temp_dir(specific_dir):
            logger.warning(f"⚠️ [XUẤT] Không xóa thư mục (không phải temp job hợp lệ): {specific_dir}")
            return dest

        abs_dest = os.path.normcase(os.path.abspath(dest))
        abs_job = os.path.normcase(os.path.abspath(specific_dir))
        if abs_dest.startswith(abs_job + os.sep):
            logger.error("❌ [XUẤT] File đích vẫn nằm trong thư mục tạm — HUỶ xóa.")
            return dest

        try:
            shutil.rmtree(specific_dir)
            logger.info(f"🗑️ [XUẤT] Đã xóa thư mục tạm của job: {specific_dir}")
        except Exception as e:
            logger.warning(f"⚠️ [XUẤT] Video đã lưu OK nhưng không xóa được thư mục tạm {specific_dir}: {e}")
        return dest

    @staticmethod
    def probe_media_duration(path: str) -> float:
        """Đo duration file video/audio (format, fallback stream)."""
        if not path or not os.path.exists(path):
            return 0.0

        def _run_probe(entries: str) -> float:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", entries,
                "-of", "default=noprint_wrappers=1:nokey=1",
                path,
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=0x08000000,
            )
            for line in (res.stdout or "").strip().splitlines():
                line = line.strip()
                if not line or line.lower() in ("n/a", "nan"):
                    continue
                try:
                    val = float(line)
                    if val > 0.0:
                        return val
                except ValueError:
                    continue
            return 0.0

        duration = _run_probe("format=duration")
        if duration <= 0.05:
            duration = _run_probe("stream=duration")
        return duration

    @staticmethod
    def capcut_audio_filter(level) -> str:
        """Gain 0–20 dB kiểu CapCut: mức cao vẫn lớn, chỉ chặn đỉnh gây méo."""
        try:
            level = max(0.0, min(20.0, float(level)))
        except (TypeError, ValueError):
            level = 6.0
        # Bù sau compressor theo đúng vị trí thanh kéo. Ở mức 20, makeup 12 dB
        # đưa master lên khoảng -12 LUFS; ở mức 0 không tự ý tăng âm lượng.
        post_makeup = level * 0.60
        return (
            f"volume={level:.2f}dB,"
            "acompressor=threshold=0.125:ratio=4:attack=5:release=120:"
            "makeup=1:knee=4:detection=rms:link=maximum,"
            "asoftclip=type=tanh:threshold=0.90:output=0.95:oversample=4,"
            # Không dùng makeup cố định: mức 20 phải lớn rõ rệt hơn mức thấp.
            f"volume={post_makeup:.2f}dB,"
            # Chỉ chặn peak; level=false ngăn limiter tự thay đổi gain tổng.
            "alimiter=limit=0.75:attack=5:release=80:level=false"
        )

    @staticmethod
    def scramble_original_audio_filter(level=6.0, enabled: bool = True) -> str:
        """
        Bóp méo & biến đổi tần số âm thanh gốc để lách Audio Fingerprint của TikTok (Không dùng BGM):
        - Pitch shift nhẹ (+3%) + bù tempo để giữ nguyên 100% thời lượng (không lệch sync 1ms nào).
        - Parametric EQ: Boost 3kHz (+2.5dB), cut 600Hz (-2.5dB), highpass 65Hz để thay đổi phổ tần và đỉnh sóng.
        - Micro-reverb: 18ms acoustic room delay (aecho=0.8:0.88:18:0.12) phá tan fingerprint đuôi sóng nhưng tai người nghe tự nhiên.
        - Compressor + tanh softclip + limiter: Chống méo tiếng, âm thanh dày và chắc.
        """
        if not enabled:
            return EditorProcessor.capcut_audio_filter(level)
        try:
            level = max(0.0, min(20.0, float(level)))
        except (TypeError, ValueError):
            level = 6.0
        post_makeup = level * 0.60
        return (
            f"volume={level:.2f}dB,"
            "highpass=f=65,"
            "equalizer=f=3000:t=q:w=1:g=2.5,"
            "equalizer=f=600:t=q:w=1:g=-2.5,"
            "aecho=0.8:0.88:18:0.12,"
            "asetrate=44100*1.03,atempo=0.970873786,"
            "aresample=44100,"
            "acompressor=threshold=0.125:ratio=4:attack=5:release=120:makeup=1:knee=4:detection=rms:link=maximum,"
            "asoftclip=type=tanh:threshold=0.90:output=0.95:oversample=4,"
            f"volume={post_makeup:.2f}dB,"
            "alimiter=limit=0.75:attack=5:release=80:level=false"
        )

    @classmethod
    def sum_clip_durations(cls, paths) -> float:
        total = 0.0
        for path in paths or []:
            total += cls.probe_media_duration(path)
        return total

    @classmethod
    def visual_pad_seconds(cls, visual_dur: float, audio_dur: float, speed: float = 1.0) -> float:
        """Số giây đóng băng khung cuối (sau speed) để hình >= audio + 1s."""
        speed = float(speed) if speed and float(speed) > 0 else 1.0
        tail = float(cls.VISUAL_TAIL_AFTER_AUDIO_SEC)
        v_out = float(visual_dur) / speed
        a_out = float(audio_dur) / speed
        return max(0.0, a_out + tail + 0.08 - v_out)

    @classmethod
    def log_av_tail(cls, audio_s: float, video_s: float):
        logger.info(
            f"⏱️ [ĐỒNG BỘ] Giọng {audio_s:.2f}s, hình {video_s:.2f}s "
            f"(hình >= giọng+{cls.VISUAL_TAIL_AFTER_AUDIO_SEC:.0f}s)."
        )

    @classmethod
    def freeze_pad_visual_file(cls, visual_path: str, min_duration: float, out_path: str) -> str:
        """Giữ khung cuối đến khi duration hình >= min_duration. Không đụng audio."""
        visual_dur = cls.probe_media_duration(visual_path)
        extra = float(min_duration) - visual_dur
        if extra <= 0.05:
            return visual_path
        logger.info(
            f"🧊 [ĐỒNG BỘ] Hình thiếu {extra:.2f}s so với giọng+1s — đóng băng khung cuối."
        )
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", visual_path,
            "-vf", f"tpad=stop_mode=clone:stop_duration={extra:.4f}",
            "-an", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            out_path,
        ]
        subprocess.run(cmd, check=True, creationflags=0x08000000)
        if os.path.isfile(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        logger.warning("⚠️ [ĐỒNG BỘ] tpad thất bại — giữ clip hình gốc.")
        return visual_path

    @classmethod
    def _clamp_crop_to_canvas(cls, crop_w, crop_h, crop_x, crop_y, canvas_w=None, canvas_h=None):
        canvas_w = int(canvas_w or cls.CANVAS_W)
        canvas_h = int(canvas_h or cls.CANVAS_H)
        crop_w = cls._even(max(2, min(int(crop_w), canvas_w)))
        crop_h = cls._even(max(2, min(int(crop_h), canvas_h)))
        crop_x = cls._even(max(0, min(int(crop_x), canvas_w - crop_w)))
        crop_y = cls._even(max(0, min(int(crop_y), canvas_h - crop_h)))
        return crop_w, crop_h, crop_x, crop_y

    @staticmethod
    def _probe_video_size(video_path: str) -> tuple:
        """Trả về (width, height) thực tế; (0, 0) nếu probe thất bại."""
        try:
            cmd = [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=p=0:s=x", video_path
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=0x08000000
            )
            parts = (res.stdout or "").strip().split("x")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 0, 0

    @staticmethod
    def _parse_srt(srt_path: str) -> list:
        segments = []
        if not os.path.exists(srt_path):
            return segments
        try:
            with open(srt_path, "r", encoding="utf-8") as f:
                content = f.read()
            blocks = content.strip().split("\n\n")
            for block in blocks:
                lines = block.strip().split("\n")
                if len(lines) >= 3:
                    time_line = lines[1]
                    text_line = " ".join(lines[2:])
                    if "-->" in time_line:
                        start_str, end_str = time_line.split("-->")
                        start_sec = EditorProcessor._srt_time_to_seconds(start_str.strip())
                        end_sec = EditorProcessor._srt_time_to_seconds(end_str.strip())
                        segments.append({"start": start_sec, "end": end_sec, "text": text_line})
        except Exception as e:
            logger.error(f"❌ Lỗi khi phân tích file SRT: {e}")
        return segments

    @staticmethod
    def _srt_time_to_seconds(time_str: str) -> float:
        try:
            time_str = time_str.replace(',', '.')
            parts = time_str.split(':')
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except:
            return 0.0

    @classmethod
    def _load_banner_font(cls, size: int, sample_text: str = ""):
        try:
            from font_manager import FontManager
            font = FontManager.get_banner_font(sample_text or "", size)
            if font is not None:
                return font
        except Exception:
            pass
        for path in cls.BANNER_FONT_PATHS:
            try:
                return ImageFont.truetype(path, int(size))
            except (OSError, ValueError):
                continue
        return ImageFont.load_default()

    @staticmethod
    def _banner_text_bbox(font, text: str, stroke_w: int = 0):
        if not text:
            return (0, 0, 0, 0)
        try:
            return font.getbbox(text, stroke_width=int(stroke_w) if stroke_w else 0)
        except TypeError:
            return font.getbbox(text)

    @classmethod
    def _banner_text_size(cls, font, text: str, stroke_w: int = 0):
        bbox = cls._banner_text_bbox(font, text, stroke_w)
        return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])

    @classmethod
    def _truncate_banner_line(cls, text: str, font, max_w: int, stroke_w: int = 0) -> str:
        if not text:
            return ""
        if cls._banner_text_size(font, text, stroke_w)[0] <= max_w:
            return text
        ell = "..."
        if cls._banner_text_size(font, ell, stroke_w)[0] > max_w:
            return ell
        lo, hi = 0, len(text)
        best = ell
        while lo <= hi:
            mid = (lo + hi) // 2
            cand = text[:mid].rstrip() + ell
            if cls._banner_text_size(font, cand, stroke_w)[0] <= max_w:
                best = cand
                lo = mid + 1
            else:
                hi = mid - 1
        return best

    @classmethod
    def _split_banner_title_balanced(cls, title: str, font) -> tuple:
        """Tách title thành 2 dòng cân bằng: ':' rồi ' - ' (1/3 giữa), rồi space gần 50% bề rộng."""
        title = " ".join(str(title).split())
        if not title:
            return "", ""
        n = len(title)

        colon = title.find(":")
        if colon >= 0:
            line1 = title[: colon + 1].strip()
            line2 = title[colon + 1 :].strip()
            # Bỏ split ':' nếu một phía quá ngắn (vd. "WAIT:" rồi cả câu còn lại).
            if line1 and line2 and len(line1) >= 0.25 * n and len(line2) >= 0.25 * n:
                return line1, line2

        lo, hi = n / 3.0, (2.0 * n) / 3.0
        dash_hits = []
        start = 0
        while True:
            i = title.find(" - ", start)
            if i < 0:
                break
            if lo <= i <= hi:
                dash_hits.append(i)
            start = i + 1
        if dash_hits:
            center = n / 2.0
            i = min(dash_hits, key=lambda x: abs(x - center))
            line1 = title[:i].strip()
            line2 = title[i + 3 :].strip()
            if line1 and line2:
                return line1, line2

        spaces = [i for i, ch in enumerate(title) if ch == " "]
        if not spaces:
            mid = max(1, n // 2)
            return title[:mid], title[mid:]

        best_i = None
        best_score = None
        for i in spaces:
            line1 = title[:i].strip()
            line2 = title[i + 1 :].strip()
            if not line1 or not line2:
                continue
            w1 = cls._banner_text_size(font, line1)[0]
            w2 = cls._banner_text_size(font, line2)[0]
            tot = (w1 + w2) or 1
            r1 = w1 / tot
            score = abs(r1 - 0.5)
            if 0.40 <= r1 <= 0.60:
                score -= 0.15
            if len(line2.split()) == 1 and len(line2) <= 8:
                score += 0.25
            if len(line1.split()) == 1 and len(line1) <= 8:
                score += 0.25
            if best_score is None or score < best_score:
                best_score = score
                best_i = i
        if best_i is None:
            mid = n // 2
            return title[:mid], title[mid:]
        return title[:best_i].strip(), title[best_i + 1 :].strip()

    @classmethod
    def _largest_banner_font(cls, texts, max_size: int, min_size: int, max_w: int, stroke_w: int = 0):
        sample_text = " ".join([str(t) for t in texts if t])
        for size in range(int(max_size), int(min_size) - 1, -1):
            font = cls._load_banner_font(size, sample_text)
            if all(cls._banner_text_size(font, t, stroke_w)[0] <= max_w for t in texts if t):
                return size, font
        return None, cls._load_banner_font(min_size, sample_text)

    @staticmethod
    def _join_banner_title_raw(line1: str, line2: str) -> str:
        parts = []
        for part in (line1, line2):
            cleaned = " ".join(str(part or "").split())
            if cleaned:
                parts.append(cleaned)
        return " ".join(parts)

    @classmethod
    def _layout_banner_title(cls, raw_title: str, stroke_w: int = 0):
        """
        Bố cục title banner: tối đa 2 dòng, không thu font dưới FONT_MIN.
        1 dòng nếu fit >= FONT_MIN; không thì tách 2 dòng cân bằng rồi chọn cỡ lớn nhất.
        """
        title = " ".join(str(raw_title or "").split()) or "TITLE"
        max_w = cls.BANNER_TEXT_MAX_W
        min_sz = cls.BANNER_FONT_MIN
        size1, font1 = cls._largest_banner_font(
            [title], cls.BANNER_FONT_MAX_1LINE, min_sz, max_w, stroke_w
        )
        if size1 is not None:
            return title, "", font1, size1

        measure_font = cls._load_banner_font(min_sz, title)
        line1, line2 = cls._split_banner_title_balanced(title, measure_font)
        if not line2:
            return line1, "", measure_font, min_sz

        size2, font2 = cls._largest_banner_font(
            [line1, line2], cls.BANNER_FONT_MAX_2LINE, min_sz, max_w, stroke_w
        )
        if size2 is None:
            # Không bao giờ làm mất payoff bằng "...". Trường hợp title cực dài
            # mới hạ thêm rất nhẹ đến 26px; vẫn giữ nguyên đủ hai dòng.
            size2, font2 = cls._largest_banner_font(
                [line1, line2], min_sz - 1, 26, max_w, stroke_w
            )
            if size2 is None:
                size2 = 26
                font2 = cls._load_banner_font(size2)
        return line1, line2, font2, size2

    @classmethod
    def _create_dynamic_title_banner(cls, specific_dir: str, summary_path: str):
        """Vẽ banner title (cùng helper với Path A / Path B)."""
        title_line_1, title_line_2 = cls.read_banner_title_lines(specific_dir, summary_path=summary_path)
        path, bw, _bh = cls._create_dynamic_title_banner_custom(
            specific_dir,
            title_line_1,
            title_line_2,
            {
                "title_color1": "black",
                "title_color2": "red",
                "title_outline_color": "none",
                "title_bg_color": "white",
            },
        )
        return path, bw

    @classmethod
    def _score_and_map_clips_opencv(cls, source_video: str, safe_start: float, safe_end: float, required_duration: float) -> list:
        """
        Quét toàn bộ video nguồn, chia thành các đoạn 6 giây[cite: 1, 2]:
        - Chấm điểm hình ảnh bằng OpenCV (Độ chuyển động, biến thiên khung hình)[cite: 1, 2].
        - Chấm điểm âm thanh dồn dập kiểu action movie bằng bộ lọc astats của FFmpeg[cite: 1, 2].
        - Tổng hợp điểm số, sắp xếp các cảnh gay cấn nhất lên đầu tiên[cite: 1, 2].
        """
        import cv2
        import numpy as np
        import gc

        if not source_video or not os.path.exists(source_video):
            logger.warning("⚠️ [OPENCV] Không có low-res hợp lệ — chia đều theo thời gian (không chấm hình).")
            return cls._even_time_clips(safe_start, safe_end, required_duration)

        cls._silence_opencv_ffmpeg_logs()
        cap = cv2.VideoCapture(source_video)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        chunk_duration = 6.0
        potential_clips = []

        curr_t = safe_start
        while curr_t + chunk_duration <= safe_end:
            start_frame = int(curr_t * fps)
            end_frame = int((curr_t + chunk_duration) * fps)
            
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
            prev_gray = None
            motion_scores = []
            sample_step = int(fps) if fps > 0 else 30
            
            for f_idx in range(start_frame, min(end_frame, total_frames), sample_step):
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                ret, frame = cap.read()
                if not ret:
                    break
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (224, 224))
                
                if prev_gray is not None:
                    diff = cv2.absdiff(gray, prev_gray)
                    motion_scores.append(np.mean(diff))
                prev_gray = gray

            motion_score = float(np.mean(motion_scores)) if motion_scores else 1.0

            audio_action_score = 1.0
            try:
                cmd_astats = [
                    "ffmpeg", "-hide_banner", "-ss", str(curr_t), "-t", str(chunk_duration), "-i", source_video,
                    "-af", "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.Peak_level", 
                    "-f", "null", "-"
                ]
                res_astats = subprocess.run(cmd_astats, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
                
                peaks = re.findall(r'Peak_level=([-\d.]+)', res_astats.stderr)
                if peaks:
                    peak_vals = [float(p) for p in peaks if float(p) > -60.0]
                    if peak_vals:
                        max_peak = max(peak_vals)
                        min_peak = min(peak_vals)
                        fluctuation = max_peak - min_peak
                        audio_action_score = max(1.0, (max_peak + 50.0) * 2.0 + (fluctuation * 3.0))
            except:
                pass

            total_score = (motion_score * 0.5) + (audio_action_score * 0.5)
            potential_clips.append({"start": curr_t, "end": curr_t + chunk_duration, "score": total_score})
            curr_t += chunk_duration

        # --- BỔ SUNG AN TOÀN: GIẢI PHÓNG BỘ NHỚ TRIỆT ĐỂ, CHỐNG TRÀN RAM ---
        cap.release()
        cv2.destroyAllWindows()
        gc.collect()

        if not potential_clips:
            curr_t = safe_start
            while curr_t < safe_end:
                potential_clips.append({"start": curr_t, "end": min(safe_end, curr_t + 6.0), "score": 1.0})
                curr_t += 6.0

        potential_clips.sort(key=lambda x: x["score"], reverse=True)
        logger.info(f"🔥 [AI SCORE] Đã chấm điểm {len(potential_clips)} phân cảnh (Cảnh gay cấn nhất được đưa lên đầu)[cite: 1, 2].")

        chosen_clips = []
        accumulated = 0.0
        idx = 0

        while accumulated < required_duration and potential_clips:
            target = potential_clips[idx % len(potential_clips)]
            dur = target["end"] - target["start"]

            if accumulated + dur > required_duration:
                end_t = target["start"] + (required_duration - accumulated)
                dur = end_t - target["start"]
                chosen_clips.append({"start": target["start"], "end": end_t})
            else:
                chosen_clips.append({"start": target["start"], "end": target["end"]})

            accumulated += dur
            idx += 1

        while accumulated < required_duration and chosen_clips:
            extra = chosen_clips[(idx - 1) % len(chosen_clips)]
            chosen_clips.append(extra)
            accumulated += (extra["end"] - extra["start"])
            idx += 1

        return chosen_clips

    @classmethod
    def _resolve_broll_intervals(
        cls,
        source_video: str,
        safe_start: float,
        safe_end: float,
        required_duration: float,
        post_options: dict = None,
        specific_dir: str = None,
    ) -> list:
        """
        Chọn lịch cắt B-roll theo post_options['broll_mode']:
        - timesub_content: từng story beat khớp SRT theo trình tự
        - heatmap_ranked: Top cảnh mạnh nhất đứng trước
        - heatmap_chronological: cùng Top cảnh nhưng sort theo time nguồn
        Timesub lỗi/thiếu file → log và fallback OpenCV.
        """
        import time

        mapping_started = time.perf_counter()
        mapping_logged = False

        def log_mapping_total() -> None:
            nonlocal mapping_logged
            if not mapping_logged:
                logger.info("⏱️ [CHI TIẾT] 🧩 Mapping lời và hình: %.2fs",
                            time.perf_counter() - mapping_started)
                mapping_logged = True

        def log_broll_timer(label: str, started_at: float) -> float:
            elapsed = max(0.0, time.perf_counter() - started_at)
            logger.info(f"⏱️ [TIMER B-ROLL] {label}: {elapsed:.2f}s")
            return elapsed

        post_options = post_options or {}
        mode = str(post_options.get("broll_mode") or "heatmap_ranked").strip().lower()
        mode = {
            "opencv": "heatmap_ranked", "opencv_action": "heatmap_ranked",
            "timesub_semantic": "timesub_content", "timesub": "timesub_content",
            "semantic": "timesub_content",
        }.get(mode, mode)
        original_audio_mode = bool(post_options.get("original_audio_mode"))
        # Phòng thủ ở tầng editor: job/preset cũ cũng không thể chạy tổ hợp đã loại.
        if original_audio_mode and mode == "timesub_content":
            mode = "heatmap_chronological"
        elif not original_audio_mode and mode == "heatmap_chronological":
            mode = "timesub_content"
        try:
            playback_speed = max(0.5, min(3.0, float(post_options.get("speed", 1.0) or 1.0)))
        except (TypeError, ValueError):
            playback_speed = 1.0
        required_source_duration = float(required_duration) * playback_speed
        try:
            max_broll_sec = float(post_options.get("broll_max_sec", 6.0) or 6.0)
        except (TypeError, ValueError):
            max_broll_sec = 6.0
        max_broll_sec = max(1.5, min(30.0, max_broll_sec))
        # V2: mọi chế độ cùng dùng một bản đồ shot-boundary. OpenCV/Timesub chỉ
        # xếp hạng scene, không còn tự chia khối thời gian hoặc cắt video.
        try:
            from scene_mapper import (
                attach_subtitle_context, build_scene_map, fill_timeline,
                load_highlight_candidates, load_narration_plan, map_plan_to_highlights, rank_action_windows,
                map_timesub_story, rank_semantic_story_windows, save_broll_timeline,
                save_highlight_candidates, select_highlight_pool, usable_scenes,
                validate_broll_timeline, exclude_unusable_visual_ranges,
            )
            timer_mark = time.perf_counter()
            scene_map = build_scene_map(source_video, target_limit=max_broll_sec)
            log_broll_timer("Đọc/tạo scene map", timer_mark)

            timer_mark = time.perf_counter()
            scenes = usable_scenes(
                scene_map, safe_start, safe_end,
                min_duration=0.35 if original_audio_mode else None,
            )
            plan_path = os.path.join(specific_dir or "", "narration_plan.json")
            plan = load_narration_plan(plan_path)
            unusable_ranges = plan.get("unusable_visual_ranges", []) if isinstance(plan, dict) else []
            if unusable_ranges:
                before_count = len(scenes)
                scenes = exclude_unusable_visual_ranges(scenes, unusable_ranges)
                logger.info("🚫 [VISUAL FILTER] Chặn %d vùng bị che; scene sạch %d → %d.",
                            len(unusable_ranges), before_count, len(scenes))
            log_broll_timer("Lọc scene trong vùng an toàn", timer_mark)
            if not scenes:
                raise RuntimeError("Bản đồ không có scene đủ dài trong vùng quét.")
            timer_mark = time.perf_counter()
            if mode == "timesub_content":
                base = specific_dir or ""
                ranked = rank_semantic_story_windows(
                    scenes,
                    os.path.join(base, "transcript_sub.srt"),
                    os.path.join(base, "summary_output.txt"),
                    plan_path=plan_path,
                    max_duration=max_broll_sec,
                )
                selected_mode = "timesub_content"
                logger.info("🎯 [B-ROLL V3] Timesub ghép từng story beat theo trình tự.")
            else:
                ranked = rank_action_windows(source_video, scenes, max_duration=max_broll_sec)
                selected_mode = mode
                logger.info("🔥 [B-ROLL V3] OpenCV chấm Top hành động cho %s.", selected_mode)

            # Heatmap chỉ cộng vào điểm xếp hạng; scene map, ranh giới và timeframe
            # vẫn giữ nguyên. Thiếu Edge/SVG/Selenium phải quay về đúng điểm cũ.
            heatmap_url = str(
                post_options.get("youtube_url") or post_options.get("source_url") or ""
            ).strip()
            if heatmap_url:
                try:
                    from youtube_heatmap import blend_heatmap_scores, get_youtube_heatmap
                    heat_markers = get_youtube_heatmap(heatmap_url, timeout=15)
                    if heat_markers:
                        heat_weight = 0.35 if selected_mode == "timesub_content" else 0.55
                        ranked = blend_heatmap_scores(ranked, heat_markers, heat_weight)
                        logger.info(
                            "🔥 [HEATMAP SCORE] %s: Heatmap %.0f%% + điểm cũ %.0f%%.",
                            "Timesub" if selected_mode == "timesub_content" else "OpenCV",
                            heat_weight * 100, (1.0 - heat_weight) * 100,
                        )
                except Exception as heatmap_error:
                    logger.warning(
                        "⚠️ [HEATMAP] Bỏ qua vì lỗi %s; giữ nguyên 100%% điểm cũ.",
                        heatmap_error,
                    )
            log_broll_timer(f"Chấm và xếp hạng {len(scenes)} scene ({selected_mode})", timer_mark)

            timer_mark = time.perf_counter()
            if selected_mode == "timesub_content":
                clips = map_timesub_story(plan, ranked, required_source_duration)
            else:
                base = specific_dir or ""
                pool = load_highlight_candidates(base, selected_mode)
                if not pool:
                    logger.warning("⚠️ [HIGHLIGHT FALLBACK] Thiếu cache Top; dùng bảng xếp hạng hiện tại.")
                    pool = [dict(item) for item in ranked]
                pool = exclude_unusable_visual_ranges(pool, unusable_ranges)
                clips = map_plan_to_highlights(
                    plan, pool, required_source_duration, selected_mode,
                    max_duration=max_broll_sec,
                )
            if not clips:
                raise RuntimeError("Không điền được timeline từ scene map.")
            validate_broll_timeline(clips, required_source_duration, selected_mode)
            if post_options.get("random_broll_mirror") and clips:
                # Random mirror trực tiếp, không quét OCR/chữ để giữ tốc độ.
                mirror_count = min(len(clips), max(1, int(round(len(clips) * 0.4))))
                for mirror_index in random.sample(range(len(clips)), mirror_count):
                    clips[mirror_index]["mirror"] = True
                logger.info(
                    "🪞 [MIRROR] Lật ngẫu nhiên %d/%d scene; không chạy dò chữ.",
                    mirror_count, len(clips),
                )
            if specific_dir:
                save_broll_timeline(
                    specific_dir, selected_mode, scene_map, clips, required_source_duration
                )
            log_broll_timer("Điền và lưu timeline", timer_mark)
            logger.info(
                "✅ [B-ROLL V3] %d timeframe | nguồn %.1fs cho timeline %.1fs @ %.2fx | mỗi cảnh ≤%.1fs.",
                len(clips), required_source_duration, required_duration, playback_speed, max_broll_sec,
            )
            log_mapping_total()
            return clips
        except Exception as scene_error:
            logger.warning(
                "⚠️ [B-ROLL AUTO-FALLBACK] %s — tự dựng timeline scene-map để không dừng pipeline.",
                scene_error,
            )
            fallback_map = {"source_duration": max(0.0, float(safe_end))}
            try:
                from scene_mapper import (
                    build_scene_map, fill_timeline, rank_action_windows,
                    save_broll_timeline, usable_scenes,
                )
                timer_mark = time.perf_counter()
                fallback_map = build_scene_map(source_video, target_limit=max_broll_sec)
                log_broll_timer("Fallback đọc/tạo scene map", timer_mark)

                timer_mark = time.perf_counter()
                fallback_scenes = usable_scenes(fallback_map, safe_start, safe_end)
                fallback_ranked = rank_action_windows(
                    source_video, fallback_scenes, max_duration=max_broll_sec
                )
                log_broll_timer(f"Fallback chấm {len(fallback_scenes)} scene", timer_mark)

                timer_mark = time.perf_counter()
                fallback_clips = fill_timeline(fallback_ranked, required_source_duration, allow_repeat=False)
                if not fallback_clips:
                    raise RuntimeError("Scene map không có cảnh sạch đủ dùng.")
                fallback_total = sum(
                    max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
                    for item in fallback_clips
                )
                if fallback_total + 0.05 < required_source_duration:
                    raise RuntimeError(
                        f"Kho scene fallback chỉ đủ {fallback_total:.2f}s/"
                        f"{required_source_duration:.2f}s; chuyển sang timeline cứu cuối."
                    )
                if specific_dir:
                    save_broll_timeline(
                        specific_dir, "scene_map_action_fallback", fallback_map,
                        fallback_clips, required_source_duration,
                    )
                log_broll_timer("Fallback điền và lưu timeline", timer_mark)
                log_mapping_total()
                return fallback_clips
            except Exception as fallback_error:
                # Phương án cứu cuối: băm trực tiếp vùng source an toàn. Chỉ
                # dùng khi scene-map/mapping đều hỏng; thà ghép ít thông minh
                # còn hơn làm chết toàn bộ pipeline sau khi đã tải và tạo voice.
                logger.warning(
                    "⚠️ [EMERGENCY TIMELINE] Scene-map fallback lỗi: %s; "
                    "băm trực tiếp source để tiếp tục render.", fallback_error,
                )
                emergency = []
                cursor = float(safe_start)
                upper = max(cursor + 0.1, float(safe_end))
                accumulated = 0.0
                cycle = 0
                while accumulated < required_source_duration - 0.001:
                    if cursor >= upper - 0.05:
                        cursor = float(safe_start)
                        cycle += 1
                    duration = min(
                        max_broll_sec,
                        upper - cursor,
                        required_source_duration - accumulated,
                    )
                    if duration < 0.05:
                        break
                    emergency.append({
                        "id": f"EMERGENCY_{cycle:02d}_{len(emergency) + 1:04d}",
                        "scene_id": f"emergency_{cycle}_{len(emergency) + 1}",
                        "start": round(cursor, 6),
                        "end": round(cursor + duration, 6),
                        "duration": round(duration, 6),
                        "mapping_role": "emergency_source",
                        "narration_segment": "reserve",
                    })
                    cursor += duration
                    accumulated += duration
                if emergency:
                    if specific_dir:
                        save_broll_timeline(
                            specific_dir, "emergency_source_fallback", fallback_map,
                            emergency, required_source_duration,
                        )
                    log_mapping_total()
                    return emergency
                raise RuntimeError(
                    f"Video nguồn không có vùng hình đọc được: {fallback_error}"
                ) from fallback_error

        if mode in ("timesub_semantic", "timesub", "semantic"):
            logger.info(
                "🎯 [B-ROLL] Chế độ Timesub: sát nghĩa TTS + hình ổn định (≤%.1fs).",
                max_broll_sec,
            )
            try:
                from broll_semantic import score_and_map_clips_semantic
                base = specific_dir or ""
                srt_path = os.path.join(base, "transcript_sub.srt")
                script_path = os.path.join(base, "summary_output.txt")
                clips = score_and_map_clips_semantic(
                    source_video=source_video,
                    srt_path=srt_path,
                    script_path=script_path,
                    safe_start=safe_start,
                    safe_end=safe_end,
                    required_duration=required_duration,
                )
                if clips:
                    logger.info(f"✅ [B-ROLL] Timesub chọn {len(clips)} đoạn độc nhất.")
                    result = cls._fill_intervals_to_duration(clips, required_duration)
                    log_mapping_total()
                    return result
                logger.warning("⚠️ [TIMESUB] Không chọn được clip hợp lệ — fallback OpenCV.")
            except Exception as e:
                logger.warning(f"⚠️ [TIMESUB] Lỗi ({e}) — fallback OpenCV.")
        else:
            logger.info("🔥 [B-ROLL] Chế độ OpenCV: quét hồi hộp/căng thẳng.")
        scored = cls._score_and_map_clips_opencv(
            source_video, safe_start, safe_end, required_duration
        )
        result = cls._fill_intervals_to_duration(scored, required_duration)
        log_mapping_total()
        return result

    @classmethod
    def _build_pure_color_filter(cls, options: dict) -> str:
        """
        Xây dựng chuỗi bộ lọc màu sắc CapCut thuần túy chuẩn 100% (không gồm scale/crop/blur nền),
        bao gồm 15 thông số màu CapCut + LUT look stack.
        Định dạng trả về: chuỗi filter ffmpeg (ví dụ 'eq=...,colorbalance=...') hoặc 'null'.
        """
        opts = dict(options or {})
        if not opts.get("_look_applied"):
            try:
                from capcut_filters import apply_look
                opts = apply_look(opts)
                opts["_look_applied"] = True
            except Exception:
                pass

        temp_val = float(opts.get("temperature", 0) or 0)
        tint_val = float(opts.get("tint", 0) or 0)
        sat_val = float(opts.get("saturation", 0) or 0)
        exposure_val = float(opts.get("exposure", 0) or 0)
        contrast_val = float(opts.get("contrast", 0) or 0)
        highlights_val = float(opts.get("highlights", 0) or 0)
        shadows_val = float(opts.get("shadows", 0) or 0)
        whites_val = float(opts.get("whites", 0) or 0)
        blacks_val = float(opts.get("blacks", 0) or 0)
        brilliance_val = float(opts.get("brilliance", 0) or 0)

        sharpen_val = float(opts.get("sharpen", 0) or 0)
        clarity_val = float(opts.get("clarity", 0) or 0)
        grain_val = float(opts.get("grain", 0) or 0)
        blur_val = float(opts.get("blur", 0) or 0)
        vignette_val = float(opts.get("vignette", 0) or 0)

        brightness = exposure_val / 100.0
        contrast = max(0.0, 1.0 + (contrast_val / 40.0))
        saturation = max(0.0, 1.0 + (sat_val / 25.0))

        r_mod = temp_val / 80.0
        b_mod = -temp_val / 80.0
        g_mod = tint_val / 80.0

        # 4. Bộ lọc màu sắc CapCut chuẩn 15 thông số gốc
        color_filters = f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:saturation={saturation:.3f}"

        if abs(temp_val) >= 0.05 or abs(tint_val) >= 0.05:
            color_filters += f",colorbalance=rh={r_mod:.3f}:bh={b_mod:.3f}:gh={g_mod:.3f}"

        if abs(highlights_val) >= 0.05 or abs(shadows_val) >= 0.05:
            h_shift = highlights_val / 200.0
            sh_shift = shadows_val / 200.0
            p1_x, p1_y = 0.25, max(0.01, min(0.49, 0.25 + sh_shift))
            p2_x, p2_y = 0.75, max(0.51, min(0.99, 0.75 + h_shift))
            color_filters += f",curves=all='0/0 {p1_x:.2f}/{p1_y:.2f} {p2_x:.2f}/{p2_y:.2f} 1/1'"

        if abs(whites_val) >= 0.05 or abs(blacks_val) >= 0.05:
            w_mod = max(0.55, min(0.98, 1.0 - (whites_val / 300.0)))
            b_mod = max(0.02, min(0.45, 0.0 + (blacks_val / 300.0)))
            color_filters += f",curves=all='{b_mod:.2f}/0.0 0.5/0.5 {w_mod:.2f}/1.0'"

        if abs(brilliance_val) >= 0.05:
            gamma_val = max(0.1, 1.0 + (brilliance_val / 100.0))
            color_filters += f",eq=gamma={gamma_val:.3f}"

        sharpen_amount = max(0.0, sharpen_val) / 100.0 * 1.5
        clarity_amount = max(0.0, clarity_val) / 100.0 * 1.2
        detail_amount = min(1.5, sharpen_amount + clarity_amount)
        if detail_amount > 0.01:
            detail_kernel = 7 if sharpen_amount > 0 and clarity_amount > 0 else (5 if sharpen_amount > 0 else 9)
            color_filters += (
                f",unsharp={detail_kernel}:{detail_kernel}:{detail_amount:.4f}:"
                f"{detail_kernel}:{detail_kernel}:0.0"
            )

        if blur_val > 0.1:
            b_radius = max(1, int(blur_val / 4))
            color_filters += f",gblur=sigma={b_radius}"

        if grain_val > 0.1:
            noise_v = int((grain_val / 100.0) * 40)
            if noise_v > 0:
                color_filters += f",noise=alls={noise_v}:allf=t+u"

        if vignette_val > 0.1:
            v_angle = 0.4 + ((100 - vignette_val) / 200.0)
            color_filters += f",vignette=PI/{v_angle:.3f}"

        try:
            from capcut_filters import extra_ffmpeg_tail
            tail = extra_ffmpeg_tail(opts)
            if tail:
                color_filters += tail
        except Exception:
            pass

        return color_filters or "null"

    @classmethod
    def _build_vf_filter(cls, options: dict, input_label: str = "[0:v]") -> str:
        """
        Xây dựng chuỗi filter chuẩn xác: Chuẩn hóa hệ quy chiếu 1920x1080, xử lý Blur Mask nhòe thực sự 
        trên video gốc trước khi scale/zoom-in và kết hợp đầy đủ 15 hiệu ứng màu sắc CapCut.
        """
        options = dict(options or {})
        if not options.get("_look_applied"):
            try:
                from capcut_filters import apply_look
                options = apply_look(options)
                options["_look_applied"] = True
            except Exception:
                pass

        zoom_val = float(options.get("zoom_percent", 100.0) or 100.0)
        zoom_in = bool(options.get("zoom_in", False)) or (abs(zoom_val - 100.0) >= 0.1)
        zoom_pct = (zoom_val / 100.0) if zoom_in else 1.0
        scale_w_pct = float(options.get("scale_w", 100.0) or 100.0) / 100.0
        scale_h_pct = float(options.get("scale_h", 100.0) or 100.0) / 100.0
        blur_bg = options.get("blur_bg", True)

        pos_x = int(options.get("pos_x", 0) or 0)
        pos_y = int(options.get("pos_y", 0) or 0)
        overlay_x = f"(W-w)/2+{pos_x}" if pos_x else "(W-w)/2"
        overlay_y = f"(H-h)/2+{pos_y}" if pos_y else "(H-h)/2"
        
        # Tham số Crop từ Config / Tool
        use_crop = options.get("use_crop", False)
        crop_w = options.get("crop_w", 1920)
        crop_h = options.get("crop_h", 1080)
        crop_x = options.get("crop_x", 0)
        crop_y = options.get("crop_y", 0)

        # Tham số Làm mờ vùng chọn (Blur Mask)
        use_blur_mask = options.get("use_blur_mask", False)
        blur_mx = int(options.get("blur_mask_x", 20))
        blur_my = int(options.get("blur_mask_y", 861))
        blur_mw = int(options.get("blur_mask_w", 932))
        blur_mh = int(options.get("blur_mask_h", 210))

        use_custom_hook = options.get("use_custom_hook", False)

        if use_custom_hook:
            # Path B (AI hook): giữ nguyên graph cũ, không đụng nhánh này.
            # Nền cuối cùng vốn bị blur mạnh: xử lý ở 360x640 rồi upscale giúp
            # giảm gần 9 lần số pixel của boxblur mà không làm mềm foreground.
            bg_pipeline = (
                f"{input_label}scale=360:640:force_original_aspect_ratio=increase,"
                "crop=360:640,boxblur=12:6,scale=1080:1920:flags=bilinear[bg];"
            )
            if use_crop:
                fg_base = f"{input_label}crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
                ref_w = crop_w
                ref_h = crop_h
            else:
                fg_base = f"{input_label}"
                ref_w = 1920
                ref_h = 1080

            base_h = int(round(1080.0 * ref_h / ref_w)) if ref_w > 0 else 608
            fg_w = cls._even(max(32, int(round(1080.0 * zoom_pct * scale_w_pct))))
            fg_h = cls._even(max(32, int(round(base_h * zoom_pct * scale_h_pct))))

            if use_blur_mask:
                fg_pipeline = (
                    f"{fg_base}scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_src];"
                    f"[v_src]split=2[orig1][orig2];"
                    f"[orig2]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=25:25[blur_chunk];"
                    f"[orig1][blur_chunk]overlay={blur_mx}:{blur_my}[video_blurred];"
                    f"[video_blurred]scale={fg_w}:{fg_h},setsar=1[fg];"
                )
            else:
                fg_pipeline = f"{fg_base},scale={fg_w}:{fg_h},setsar=1[fg];"
            if blur_bg:
                base_overlay = f"[bg][fg]overlay={overlay_x}:{overlay_y}[v_mid];"
            else:
                base_overlay = f"color=c=black:s=1080x1920[bg];[bg][fg]overlay={overlay_x}:{overlay_y}[v_mid];"
            vf = bg_pipeline + fg_pipeline + base_overlay
        else:
            # Path A (hook native): source có thể là 720p/480p/1440p chứ không phải 1920x1080.
            # Chuẩn hóa lên canvas crop 1920x1080 rồi mới crop — tránh lỗi
            # "Invalid too big or non positive size for width '1920'".
            crop_w, crop_h, crop_x, crop_y = cls._clamp_crop_to_canvas(
                crop_w, crop_h, crop_x, crop_y, cls.CANVAS_W, cls.CANVAS_H
            )
            vf = (
                f"{input_label}scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,split=2[v_ref][v_ref_bg];"
            )
            vf += (
                "[v_ref_bg]scale=360:640:force_original_aspect_ratio=increase,"
                f"crop=360:640,boxblur=12:6,scale={cls.OUTPUT_W}:{cls.OUTPUT_H}:flags=bilinear[bg];"
            )
            fg_chain = (
                f"[v_ref]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                if use_crop else "[v_ref]"
            )
            ref_w = crop_w if use_crop else cls.CANVAS_W
            ref_h = crop_h if use_crop else cls.CANVAS_H
            base_h = int(round(1080.0 * ref_h / ref_w)) if ref_w > 0 else 608
            fg_w = cls._even(max(32, int(round(1080.0 * zoom_pct * scale_w_pct))))
            fg_h = cls._even(max(32, int(round(base_h * zoom_pct * scale_h_pct))))

            if use_blur_mask:
                vf += (
                    f"{fg_chain}scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease,"
                    f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2[v_src];"
                    f"[v_src]split=2[orig1][orig2];"
                    f"[orig2]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=10:2[blur_chunk];"
                    f"[orig1][blur_chunk]overlay={blur_mx}:{blur_my}[video_blurred];"
                    f"[video_blurred]scale={fg_w}:{fg_h},setsar=1[fg];"
                )
            else:
                vf += f"{fg_chain}scale={fg_w}:{fg_h},setsar=1[fg];"
            if blur_bg:
                vf += f"[bg][fg]overlay={overlay_x}:{overlay_y}[v_mid];"
            else:
                vf += f"color=c=black:s={cls.OUTPUT_W}x{cls.OUTPUT_H}[bg];[bg][fg]overlay={overlay_x}:{overlay_y}[v_mid];"

        # 4. Bộ lọc màu sắc CapCut chuẩn 15 thông số + LUT look stack
        color_filters = cls._build_pure_color_filter(options)
        if color_filters and color_filters != "null":
            vf += f"[v_mid]{color_filters},setsar=1[vout]"
        else:
            vf += f"[v_mid]null,setsar=1[vout]"
        return vf

    @staticmethod
    def build_atempo_filter(speed: float) -> str:
        """Sinh chuỗi bộ lọc atempo cho FFmpeg, hỗ trợ dải tốc độ vượt ngoài [0.5, 2.0]."""
        if abs(speed - 1.0) < 0.01:
            return ""
        parts = []
        rem = float(speed)
        while rem > 2.0:
            parts.append("atempo=2.0")
            rem /= 2.0
        while rem < 0.5:
            parts.append("atempo=0.5")
            rem /= 0.5
        parts.append(f"atempo={rem:.4f}")
        return ",".join(parts)

    @classmethod
    def _cut_and_format_clip(cls, source_video: str, start: float, end: float, output_path: str) -> bool:
        """
        [CẮT THÔ TUYỆT ĐỐI]: Không áp dụng filter màu hay tỷ lệ để đạt tốc độ siêu tốc và không bao giờ lỗi.
        """
        duration = max(2.0, end - start)
        try:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(start),
                "-t", str(duration),
                "-i", source_video,
                "-c:v", "h264_nvenc",
                "-preset", "p1",
                "-cq", "17",
                "-an",
                "-pix_fmt", "yuv420p",
                output_path
            ]
            run_ffmpeg_auto(
                cmd, label="cắt clip", logger=logger, check=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
            )
            return os.path.exists(output_path) and os.path.getsize(output_path) > 0
        except Exception as e:
            logger.error(f"❌ Lỗi khi cắt thô clip tốc độ cao: {e}")
            return False

    @classmethod
    def _build_qc_vf_filter(cls, options: dict, input_label: str = "[v_1fps]") -> str:
        """
        Bộ lọc hình ảnh siêu tốc dành riêng cho ảnh lưới QC (Gemini Grid Inspector):
        - Giữ 100% độ chính xác về mặt hình học: Tỷ lệ khung hình 9:16, vị trí crop_x, crop_y, crop_w, crop_h, zoom.
        - Lược bỏ hoàn toàn 15 bộ lọc màu CapCut nặng nề (unsharp 7x7, curves, noise, vignette) vốn chỉ phục vụ
          thẩm mỹ render cuối và ngốn hơn 100s CPU.
        - Xử lý mờ nền siêu nhẹ nếu cần, giúp tốc độ trích xuất ảnh lưới đạt ~1-2 giây.
        """
        if not options:
            options = {}
        use_crop = bool(options.get("use_crop", False))
        crop_w = options.get("crop_w", 1920)
        crop_h = options.get("crop_h", 1080)
        crop_x = options.get("crop_x", 0)
        crop_y = options.get("crop_y", 0)
        zoom_in = bool(options.get("zoom_in", False))
        zoom_pct = (float(options.get("zoom_percent", 115)) / 100.0) if zoom_in else 1.0
        scale_w_pct = float(options.get("scale_w", 100.0) or 100.0) / 100.0
        scale_h_pct = float(options.get("scale_h", 100.0) or 100.0) / 100.0
        blur_bg = bool(options.get("blur_bg", True))

        use_blur_mask = bool(options.get("use_blur_mask", False))
        blur_mx = int(options.get("blur_mask_x", 20))
        blur_my = int(options.get("blur_mask_y", 861))
        blur_mw = int(options.get("blur_mask_w", 932))
        blur_mh = int(options.get("blur_mask_h", 210))

        use_custom_hook = bool(options.get("use_custom_hook", False))
        if use_custom_hook:
            # Path B
            if blur_bg:
                bg_pipeline = (
                    f"{input_label}scale=120:213:force_original_aspect_ratio=increase,"
                    f"crop=120:213,boxblur=4:2,scale=1080:1920:flags=bilinear[bg];"
                )
            else:
                bg_pipeline = f"color=c=black:s=1080x1920[bg];"

            if use_crop:
                fg_base = f"{input_label}crop={crop_w}:{crop_h}:{crop_x}:{crop_y}"
                ref_w = crop_w
                ref_h = crop_h
            else:
                fg_base = f"{input_label}"
                ref_w = 1920
                ref_h = 1080

            base_h = int(round(1080.0 * ref_h / ref_w)) if ref_w > 0 else 608
            fg_w = cls._even(max(32, int(round(1080.0 * zoom_pct * scale_w_pct))))
            fg_h = cls._even(max(32, int(round(base_h * zoom_pct * scale_h_pct))))

            if use_blur_mask:
                fg_pipeline = (
                    f"{fg_base}scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_src];"
                    f"[v_src]split=2[orig1][orig2];"
                    f"[orig2]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=5:2[blur_chunk];"
                    f"[orig1][blur_chunk]overlay={blur_mx}:{blur_my}[video_blurred];"
                    f"[video_blurred]scale={fg_w}:{fg_h},setsar=1[fg];"
                )
            else:
                fg_pipeline = f"{fg_base},scale={fg_w}:{fg_h},setsar=1[fg];"

            base_overlay = f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]"
            return bg_pipeline + fg_pipeline + base_overlay

        # Path A
        crop_w, crop_h, crop_x, crop_y = cls._clamp_crop_to_canvas(
            crop_w, crop_h, crop_x, crop_y, cls.CANVAS_W, cls.CANVAS_H
        )
        if blur_bg:
            vf = (
                f"{input_label}scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,split=2[v_ref][v_ref_bg];"
            )
            vf += (
                "[v_ref_bg]scale=120:213:force_original_aspect_ratio=increase,"
                f"crop=120:213,boxblur=4:2,scale={cls.OUTPUT_W}:{cls.OUTPUT_H}:flags=bilinear[bg];"
            )
        else:
            vf = (
                f"{input_label}scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v_ref];"
            )
            vf += f"color=c=black:s={cls.OUTPUT_W}x{cls.OUTPUT_H}[bg];"

        fg_chain = (
            f"[v_ref]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
            if use_crop else "[v_ref]"
        )
        ref_w = crop_w if use_crop else cls.CANVAS_W
        ref_h = crop_h if use_crop else cls.CANVAS_H
        base_h = int(round(1080.0 * ref_h / ref_w)) if ref_w > 0 else 608
        fg_w = cls._even(max(32, int(round(1080.0 * zoom_pct * scale_w_pct))))
        fg_h = cls._even(max(32, int(round(base_h * zoom_pct * scale_h_pct))))

        if use_blur_mask:
            vf += (
                f"{fg_chain}scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease,"
                f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2[v_src];"
                f"[v_src]split=2[orig1][orig2];"
                f"[orig2]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=5:2[blur_chunk];"
                f"[orig1][blur_chunk]overlay={blur_mx}:{blur_my}[video_blurred];"
                f"[video_blurred]scale={fg_w}:{fg_h},setsar=1[fg];"
            )
        else:
            vf += f"{fg_chain}scale={fg_w}:{fg_h},setsar=1[fg];"

        vf += f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vout]"
        return vf

    @classmethod
    def extract_timeline_shot_keyframes(
        cls,
        timeline_inputs: list,
        timeline_graph: str,
        vf_color_grading: str = "",
        timeline_segs: list = None,
        duration_sec: float = 90.0,
        timeline_audio_label: str = "",
        post_options: dict = None,
        timeline_label: str = "[timelinev]",
        output_dir: str = "",
    ) -> tuple:
        """
        Trích xuất Keyframe độ nét cao (720x1280 1:1 theo chuẩn 9:16) chuẩn xác theo từng Shot:
        1. Xuất chuỗi frame 1fps (720x1280) vào thư mục qc_timeline_frames để phục vụ bám sát từng giây.
        2. Chọn đúng khung hình ở điểm giữa (midpoint) của từng Shot B-roll đại diện gửi cho AI.
        3. Đảm bảo tỷ lệ 1:1 không bị vỡ nét, không bị sai lệch hệ quy chiếu tọa độ.
        """
        import time
        import math
        import shutil
        started = time.perf_counter()
        kf_items = []
        try:
            dur = max(3.0, float(duration_sec))
            t_label = timeline_label if timeline_label.startswith("[") else f"[{timeline_label}]"

            if post_options is not None:
                qc_vf = cls._build_qc_vf_filter(post_options, input_label="[v_1fps]")
            elif vf_color_grading:
                raw_vf = vf_color_grading.replace(t_label, "[v_1fps]") if t_label in vf_color_grading else vf_color_grading.replace("[timelinev]", "[v_1fps]")
                import re
                cleaned = re.sub(r",unsharp=[^,;\]]+", "", raw_vf)
                cleaned = re.sub(r",noise=[^,;\]]+", "", cleaned)
                cleaned = re.sub(r",vignette=[^,;\]]+", "", cleaned)
                cleaned = re.sub(r",curves=[^,;\]]+", "", cleaned)
                qc_vf = cleaned
            else:
                qc_vf = "[v_1fps]setsar=1[vout]"

            frames_dir = os.path.join(output_dir, "qc_timeline_frames")
            if os.path.isdir(frames_dir):
                for old_f in os.listdir(frames_dir):
                    try:
                        os.remove(os.path.join(frames_dir, old_f))
                    except Exception:
                        pass
            else:
                os.makedirs(frames_dir, exist_ok=True)

            keyframe_dir = os.path.join(output_dir, "qc_shot_keyframes")
            if os.path.isdir(keyframe_dir):
                for old_f in os.listdir(keyframe_dir):
                    try:
                        os.remove(os.path.join(keyframe_dir, old_f))
                    except Exception:
                        pass
            else:
                os.makedirs(keyframe_dir, exist_ok=True)

            frame_pattern = os.path.join(frames_dir, "frame_%04d.jpg")

            # Trích xuất 1 frame/giây (540x960) từ timeline graph (nhẹ hơn 85%, upload AI trong 2s, không trôi lệch thời gian)
            filter_str = (
                f"{timeline_graph};"
                f"{t_label}fps=1[v_1fps];"
                f"{qc_vf};"
                f"[vout]scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2[outkf]"
            )

            audio_pad = timeline_audio_label.strip("[]") if timeline_audio_label else ("timelinea" if "[timelinea]" in timeline_graph else "")
            if audio_pad and f"[{audio_pad}]" in timeline_graph:
                filter_str += f";[{audio_pad}]anullsink"

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                *timeline_inputs,
                "-t", f"{dur:.2f}",
                "-filter_complex", filter_str,
                "-map", "[outkf]",
                "-q:v", "5",
                frame_pattern
            ]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=CREATE_NO_WINDOW)
            if res.returncode != 0:
                logger.warning(f"⚠️ [SHOT KEYFRAMES] FFmpeg xuất keyframes lỗi: {(res.stderr or '').strip()[-300:]}")

            all_frames = sorted([
                os.path.join(frames_dir, f) for f in os.listdir(frames_dir)
                if f.startswith("frame_") and f.endswith(".jpg")
            ])
            total_frames = len(all_frames)

            segs = list(timeline_segs or [])
            if not segs:
                step = 5.0
                curr = 0.0
                while curr < dur:
                    segs.append({"start": curr, "end": min(dur, curr + step), "mirror": False})
                    curr += step

            for idx, seg in enumerate(segs, start=1):
                st = float(seg.get("start", 0.0))
                en = float(seg.get("end", dur))
                mid_t = (st + en) / 2.0

                f_idx = max(1, min(total_frames, int(math.floor(mid_t)) + 1)) if total_frames > 0 else 0
                target_src = os.path.join(frames_dir, f"frame_{f_idx:04d}.jpg")
                if os.path.isfile(target_src):
                    dst_name = f"shot_{idx:02d}_t{int(mid_t)}s.jpg"
                    dst_path = os.path.join(keyframe_dir, dst_name)
                    shutil.copy2(target_src, dst_path)
                    kf_items.append({
                        "shot_index": idx,
                        "path": dst_path,
                        "start_sec": st,
                        "end_sec": en,
                        "mid_sec": mid_t,
                        "mirror": bool(seg.get("mirror", False))
                    })

            ok = len(kf_items) > 0
            if ok:
                logger.info(
                    f"📸 [SHOT KEYFRAMES] Đã trích xuất {total_frames} frame timeline 1fps và {len(kf_items)} shot keyframes 540x960 trong {time.perf_counter() - started:.2f}s."
                )
            return (ok, kf_items, frames_dir)
        except Exception as exc:
            logger.warning(f"⚠️ [SHOT KEYFRAMES] Lỗi trích xuất keyframes: {exc}")
            return (False, [], "")

    @classmethod
    def ensure_red_to_gray_lut(cls) -> str:
        """
        Đảm bảo file 3D LUT red_to_gray.cube tồn tại (33x33x33 với Cosine Falloff).
        Triệt tiêu toàn bộ màu đỏ máu tươi, đỏ thẫm, máu bọt/nước bọt sang màu xám/đen sẹo khô tự nhiên.
        Đặc biệt sử dụng khóa sinh học Hemoglobin (G/R <= 0.48):
        - Bảo vệ tuyệt đối 100% màu da người (mặt, đầu, tay, cổ luôn có G/R >= 0.55), không bị biến thành tượng đen.
        - Khử triệt để 100% vết máu không còn sót pixel đỏ nào.
        """
        base_dir = os.path.dirname(os.path.abspath(__file__))
        lut_dir = os.path.join(base_dir, "luts")
        os.makedirs(lut_dir, exist_ok=True)
        lut_path = os.path.join(lut_dir, "red_to_gray.cube")

        needs_gen = True
        if os.path.exists(lut_path) and os.path.getsize(lut_path) >= 900000:
            try:
                with open(lut_path, "r", encoding="utf-8") as f:
                    first_line = f.readline()
                    if "Zero_Tolerance_Blood_Censor_v3" in first_line:
                        needs_gen = False
            except Exception:
                needs_gen = True

        if needs_gen:
            import math
            lut_size = 33
            lines = [
                'TITLE "Zero_Tolerance_Blood_Censor_v3"',
                f"LUT_3D_SIZE {lut_size}",
            ]
            for b_idx in range(lut_size):
                b = b_idx / (lut_size - 1)
                for g_idx in range(lut_size):
                    g = g_idx / (lut_size - 1)
                    for r_idx in range(lut_size):
                        r = r_idx / (lut_size - 1)

                        cmax = max(r, g, b)
                        cmin = min(r, g, b)
                        diff = cmax - cmin

                        # Bỏ qua các pixel quá tối
                        if cmax < 0.10:
                            lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
                            continue

                        h = 0.0
                        if diff > 1e-6:
                            if cmax == r:
                                h = (60.0 * ((g - b) / diff) + 360.0) % 360.0
                            elif cmax == g:
                                h = (60.0 * ((b - r) / diff) + 120.0) % 360.0
                            else:
                                h = (60.0 * ((r - g) / diff) + 240.0) % 360.0
                        dist_red = min(h, 360.0 - h)
                        s = (diff / cmax) if cmax > 1e-6 else 0.0

                        # ----------------------------------------------------------------
                        # NGUYÊN TẮC: Thà giết nhầm còn hơn bỏ sót máu (Zero Tolerance).
                        # Cứ pixel nào giống máu là chuyển màu sang xám/than chì.
                        #   1. GÓC MÀU H: Nhận H <= 22°, falloff đến 28° (bắt cả máu sẫm/khô)
                        #   2. ĐỘ BÃO HÒA S: S >= 0.22 (bắt cả máu loãng, máu lau khăn/mồ hôi)
                        #   3. TỶ LỆ R/G: R/G >= 1.32 và R - G >= 0.07 (bắt mọi sắc đỏ máu)
                        # ----------------------------------------------------------------

                        # 1. GÓC MÀU
                        if dist_red > 28.0:
                            lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
                            continue
                        if dist_red <= 20.0:
                            hue_w = 1.0
                        else:
                            hue_w = 0.5 * (1.0 + math.cos(math.pi * (dist_red - 20.0) / 8.0))

                        # 2. ĐỘ BÃO HÒA
                        if s < 0.22:
                            lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
                            continue
                        sat_w = max(0.0, min(1.0, (s - 0.22) / 0.18))

                        # 3. TỶ LỆ ĐỎ/XANH LÁ
                        rg_diff = r - g
                        rb_diff = r - b
                        if rg_diff < 0.07 or rb_diff < 0.05:
                            lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
                            continue

                        g_val = g + 1e-4
                        if (r / g_val) < 1.32:
                            lines.append(f"{r:.6f} {g:.6f} {b:.6f}")
                            continue

                        diff_w = max(0.0, min(1.0, (rg_diff - 0.07) / 0.13))
                        weight = max(0.0, min(1.0, hue_w * sat_w * diff_w))

                        # CHUYỂN THÀNH MÀU XÁM/THAN TỰ NHIÊN (0.70x LUMA)
                        luma = 0.299 * r + 0.587 * g + 0.114 * b
                        gray = 0.70 * luma
                        out_r = (1.0 - weight) * r + weight * gray
                        out_g = (1.0 - weight) * g + weight * gray
                        out_b = (1.0 - weight) * b + weight * gray

                        lines.append(f"{out_r:.6f} {out_g:.6f} {out_b:.6f}")
            with open(lut_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
        return lut_path

    @classmethod
    def refine_detection_boxes_with_opencv(cls, detected_items: list, frames_dir: str, duration_sec: float) -> list:
        """
        Tinh chỉnh đường biên và tọa độ từng điểm ảnh bằng OpenCV (Pixel-Perfect Snapping & Blood Validation):
        - Với 'blood_wound':
          Quét từng frame giây trong frames_dir (frame_%04d.jpg).
          Lọc mặt nạ màu đỏ HSV.
          Nếu không có pixel đỏ đạt ngưỡng -> Hủy (chống False Positive nhận diện nhầm găng tay/áo đỏ).
          Nếu có đốm đỏ -> bóp sát viền contour thực tế (+8px padding).
        - Với 'hardcoded_subtitles' và 'crawling_ticker_banner':
          Dùng Canny/Sobel trên ROI để tìm đường biên thật của dải chữ, cắt bỏ vùng đục lấn chiếm (+4px padding).
        - Với 'watermark_logo' và 'scoreboard_nameplate':
          Giữ tọa độ AI và lề an toàn khít (+4px padding).
        """
        import cv2
        import numpy as np
        import math
        if not detected_items:
            return []

        refined_list = []
        for item in detected_items:
            lbl = str(item.get("label") or "watermark_logo").strip()
            box = list(item.get("box", [0, 0, 0, 0]))
            ymin, xmin, ymax, xmax = [float(v) for v in box]
            st = max(0.0, float(item.get("start_sec", 0.0)))
            en = min(float(duration_sec), float(item.get("end_sec", duration_sec)))
            desc = str(item.get("description", ""))

            # 1. XỬ LÝ MÁU (BLOOD_WOUND): XÁC THỰC MÀU ĐỎ HSV & HỢP NHẤT THÀNH 1 HỘP DUY NHẤT
            if "blood" in lbl:
                start_sec_int = int(math.floor(st))
                end_sec_int = int(math.ceil(en))
                valid_blood_secs = 0
                total_red_px = 0
                accum_x1, accum_y1 = 999999, 999999
                accum_x2, accum_y2 = -1, -1

                for sec in range(start_sec_int, max(start_sec_int + 1, end_sec_int)):
                    f_idx = sec + 1
                    f_path = os.path.join(frames_dir, f"frame_{f_idx:04d}.jpg")
                    if not os.path.isfile(f_path):
                        continue

                    img = cv2.imread(f_path)
                    if img is None:
                        continue

                    h, w = img.shape[:2]
                    y1 = max(0, min(h - 4, int(ymin * h)))
                    y2 = max(y1 + 4, min(h, int(ymax * h)))
                    x1 = max(0, min(w - 4, int(xmin * w)))
                    x2 = max(x1 + 4, min(w, int(xmax * w)))

                    roi = img[y1:y2, x1:x2]
                    if roi.size == 0:
                        continue

                    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
                    # Quét dải màu đỏ rộng (H: 0-10 hoặc 170-179, S >= 55, V >= 35) để bắt cả máu khô, sẫm, loãng
                    mask1 = cv2.inRange(hsv, np.array([0, 55, 35]), np.array([10, 255, 240]))
                    mask2 = cv2.inRange(hsv, np.array([170, 55, 35]), np.array([179, 255, 240]))
                    blood_mask = cv2.bitwise_or(mask1, mask2)

                    red_pixel_count = cv2.countNonZero(blood_mask)
                    if red_pixel_count >= 5:
                        valid_blood_secs += 1
                        total_red_px += red_pixel_count
                        contours, _ = cv2.findContours(blood_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                        if contours:
                            all_pts = np.vstack(contours)
                            rx, ry, rw, rh = cv2.boundingRect(all_pts)
                            pad_px = 16
                            abs_x1 = max(0, (x1 + rx) - pad_px)
                            abs_y1 = max(0, (y1 + ry) - pad_px)
                            abs_x2 = min(w, (x1 + rx + rw) + pad_px)
                            abs_y2 = min(h, (y1 + ry + rh) + pad_px)

                            accum_x1 = min(accum_x1, abs_x1)
                            accum_y1 = min(accum_y1, abs_y1)
                            accum_x2 = max(accum_x2, abs_x2)
                            accum_y2 = max(accum_y2, abs_y2)

                if valid_blood_secs > 0 and accum_x2 > accum_x1 and accum_y2 > accum_y1:
                    refined_box = [
                        round(accum_y1 / float(h), 4),
                        round(accum_x1 / float(w), 4),
                        round(accum_y2 / float(h), 4),
                        round(accum_x2 / float(w), 4),
                    ]
                    refined_list.append({
                        "label": "blood_wound",
                        "start_sec": st,
                        "end_sec": en,
                        "box": refined_box,
                        "description": f"{desc} (HSV validated {valid_blood_secs}s, total {total_red_px}px)"
                    })
                    logger.info(f"🩸 [OPENCV BLOOD CONFIRMED] [{st:.1f}s -> {en:.1f}s]: Xác thực có máu ({valid_blood_secs}s, {total_red_px}px).")
                else:
                    # NGUYÊN TẮC: Thà giết nhầm còn hơn bỏ sót, cứ giống máu là xám cả đoạn Gemini phát hiện
                    refined_list.append({
                        "label": "blood_wound",
                        "start_sec": st,
                        "end_sec": en,
                        "box": list(box),
                        "description": f"{desc} (Gemini blood detection preserved - Zero Tolerance)"
                    })
                    logger.info(f"🩸 [OPENCV BLOOD RETAINED] [{st:.1f}s -> {en:.1f}s]: Gemini phát hiện giống máu -> Giữ nguyên để 3D LUT khử xám cả đoạn.")
                continue

            # 2. XỬ LÝ PHỤ ĐỀ CŨ VÀ BANNER CHỮ: BÓP KHÍT VIỀN CHỮ BẰNG SOBEL/CANNY
            elif "sub" in lbl or "ticker" in lbl or "banner" in lbl:
                mid_sec = int(round((st + en) / 2.0))
                f_idx = max(1, mid_sec + 1)
                f_path = os.path.join(frames_dir, f"frame_{f_idx:04d}.jpg")
                refined_box = list(box)

                if os.path.isfile(f_path):
                    img = cv2.imread(f_path)
                    if img is not None:
                        h, w = img.shape[:2]
                        y1 = max(0, min(h - 4, int(ymin * h)))
                        y2 = max(y1 + 4, min(h, int(ymax * h)))
                        x1 = max(0, min(w - 4, int(xmin * w)))
                        x2 = max(x1 + 4, min(w, int(xmax * w)))

                        roi = img[y1:y2, x1:x2]
                        if roi.size > 0:
                            gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                            edges = cv2.Canny(gray, 40, 140)

                            v_proj = np.sum(edges, axis=1)
                            thresh_v = np.max(v_proj) * 0.08 if np.max(v_proj) > 0 else 0
                            active_rows = np.where(v_proj > thresh_v)[0]

                            h_proj = np.sum(edges, axis=0)
                            thresh_h = np.max(h_proj) * 0.08 if np.max(h_proj) > 0 else 0
                            active_cols = np.where(h_proj > thresh_h)[0]

                            if len(active_rows) > 4:
                                tight_y1 = y1 + active_rows[0] - 4
                                tight_y2 = y1 + active_rows[-1] + 4
                                ymin = max(0.0, tight_y1 / float(h))
                                ymax = min(1.0, tight_y2 / float(h))

                            if len(active_cols) > 10 and "ticker" not in lbl:
                                tight_x1 = x1 + active_cols[0] - 8
                                tight_x2 = x1 + active_cols[-1] + 8
                                xmin = max(0.0, tight_x1 / float(w))
                                xmax = min(1.0, tight_x2 / float(w))

                            refined_box = [round(ymin, 4), round(xmin, 4), round(ymax, 4), round(xmax, 4)]

                refined_list.append({
                    "label": lbl,
                    "start_sec": st,
                    "end_sec": en,
                    "box": refined_box,
                    "description": desc
                })

            # 3. LOGO VÀ BẢNG ĐIỂM: BẢO TOÀN VỊ TRÍ, BỔ SUNG LỀ NHỎ (+4px)
            else:
                refined_list.append({
                    "label": lbl,
                    "start_sec": st,
                    "end_sec": en,
                    "box": box,
                    "description": desc
                })

        return refined_list

    @classmethod
    def build_clustered_qc_filters(
        cls,
        watermark_blurs: list,
        duration_sec: float,
        curr_v_label: str = "vout",
        output_w: int = 1080,
        output_h: int = 1920,
        red_to_gray_lut_path: str = "",
        log_fn=None
    ) -> tuple[str, str]:
        """
        Gom cụm không gian (Spatial Clustering) và hợp nhất thời gian theo chuẩn 100% của Tóm Tắt Video.
        Đảm bảo không bao giờ sinh ra hàng chục filter gblur/split/overlay chồng chéo gây lỗi FFmpeg.
        Trả về: (qc_filters_str, final_v_label)
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        if not watermark_blurs:
            return "", curr_v_label

        def _merge_time_intervals(ivs, max_gap=0.5):
            if not ivs:
                return []
            sorted_ivs = sorted(ivs, key=lambda x: x[0])
            merged = [sorted_ivs[0]]
            for c_st, c_end in sorted_ivs[1:]:
                p_st, p_end = merged[-1]
                if c_st <= p_end + max_gap:
                    merged[-1] = (p_st, max(p_end, c_end))
                else:
                    merged.append((c_st, c_end))
            return merged

        def _iou(b1, b2):
            y1 = max(b1[0], b2[0])
            x1 = max(b1[1], b2[1])
            y2 = min(b1[2], b2[2])
            x2 = min(b1[3], b2[3])
            inter = max(0.0, y2 - y1) * max(0.0, x2 - x1)
            a1 = max(0.0, b1[2] - b1[0]) * max(0.0, b1[3] - b1[1])
            a2 = max(0.0, b2[2] - b2[0]) * max(0.0, b2[3] - b2[1])
            union = a1 + a2 - inter
            return (inter / union) if union > 0 else 0.0

        def _should_cluster(cl, item):
            b1, b2 = cl["box"], item["box"]
            if _iou(b1, b2) >= 0.40:
                return True
            is_wide1 = (b1[3] - b1[1]) > 0.50
            is_wide2 = (b2[3] - b2[1]) > 0.50
            if is_wide1 and is_wide2:
                v_inter = max(0.0, min(b1[2], b2[2]) - max(b1[0], b2[0]))
                v_union = max(b1[2], b2[2]) - min(b1[0], b2[0])
                if v_union > 0 and (v_inter / v_union) >= 0.50:
                    return True
            return False

        blood_intervals = []
        non_blood_items = []
        for item in watermark_blurs:
            lbl = str(item.get("label") or "").strip().lower()
            st = max(0.0, float(item.get("start_sec", 0.0)))
            en = min(float(duration_sec), float(item.get("end_sec", duration_sec)))
            if en <= st:
                continue
            if "blood" in lbl:
                blood_intervals.append((st, en))
            else:
                non_blood_items.append(item)

        clustered_blurs = []
        for item in non_blood_items:
            lbl = str(item.get("label") or "watermark_logo").strip()
            box = [round(float(v), 3) for v in item.get("box", [0, 0, 0, 0])]
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            st = max(0.0, float(item.get("start_sec", 0.0)))
            en = min(float(duration_sec), float(item.get("end_sec", duration_sec)))
            if en <= st:
                continue

            merged = False
            for cl in clustered_blurs:
                if _should_cluster(cl, {"label": lbl, "box": box, "st": st, "en": en}):
                    cb = cl["box"]
                    cl["box"] = [min(cb[0], box[0]), min(cb[1], box[1]), max(cb[2], box[2]), max(cb[3], box[3])]
                    cl["intervals"].append((st, en))
                    merged = True
                    break
            if not merged:
                clustered_blurs.append({
                    "label": lbl,
                    "box": list(box),
                    "intervals": [(st, en)],
                })

        pad_x = 4
        pad_y = 4
        blur_filter_spec = "gblur=sigma=6.0:steps=1"
        clean_chain_filters = []
        filter_seq = 0

        merged_blood_intervals = _merge_time_intervals(blood_intervals, max_gap=0.3)
        if merged_blood_intervals and red_to_gray_lut_path and os.path.exists(red_to_gray_lut_path):
            blood_cond = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in merged_blood_intervals)
            blood_enable_str = f":enable='{blood_cond}'" if blood_cond else ""
            abs_lut = os.path.abspath(red_to_gray_lut_path).replace("\\", "/")
            drv, rest = abs_lut.split(":", 1) if ":" in abs_lut else ("", abs_lut)
            escaped_lut = f"{drv}\\:{rest}" if drv else abs_lut
            filter_seq += 1
            next_label = f"v_clean_{filter_seq}"
            clean_chain_filters.append(
                f"[{curr_v_label}]lut3d=file='{escaped_lut}'{blood_enable_str}[{next_label}]"
            )
            curr_v_label = next_label
            _log(f"🩸 [BLOOD 3D LUT STREAM] Khử màu đỏ máu từng pixel (Zero Crop - Motion-Immune): Kích hoạt: {blood_cond}")

        for item in clustered_blurs:
            lbl = item["label"]
            box = item["box"]
            ymin, xmin, ymax, xmax = box

            merged_intervals = _merge_time_intervals(item.get("intervals", []), max_gap=0.5)
            if not merged_intervals:
                continue

            tot_blur_time = sum(e - s for s, e in merged_intervals)
            if tot_blur_time >= duration_sec * 0.60:
                enable_str = ""
                cond = "toàn video"
            else:
                cond = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in merged_intervals)
                enable_str = f":enable='{cond}'" if cond else ""

            filter_seq += 1
            next_label = f"v_clean_{filter_seq}"

            bx = max(0, min(output_w - 16, int(xmin * output_w) - pad_x))
            by = max(0, min(output_h - 16, int(ymin * output_h) - pad_y))
            bw = cls._even(max(16, min(output_w - bx, int((xmax - xmin) * output_w) + pad_x * 2)))
            bh = cls._even(max(16, min(output_h - by, int((ymax - ymin) * output_h) + pad_y * 2)))

            clean_chain_filters.append(
                f"[{curr_v_label}]split=2[orig_{filter_seq}][crop_{filter_seq}];"
                f"[crop_{filter_seq}]crop={bw}:{bh}:{bx}:{by},{blur_filter_spec}[blur_{filter_seq}];"
                f"[orig_{filter_seq}][blur_{filter_seq}]overlay={bx}:{by}{enable_str}[{next_label}]"
            )
            curr_v_label = next_label
            _log(f"🛡️ [BLUR QC APPLIED] Làm mờ '{lbl}' [{blur_filter_spec}]: x={bx}, y={by}, w={bw}, h={bh} | Kích hoạt: {cond}")

        qc_filters_str = (";" + ";".join(clean_chain_filters)) if clean_chain_filters else ""
        return qc_filters_str, curr_v_label

    @classmethod
    def generate_90s_grid_image(
        cls,
        timeline_inputs: list,
        timeline_graph: str,
        vf_color_grading: str = "",
        output_grid_path: str = "",
        duration_sec: float = 90.0,
        timeline_audio_label: str = "",
        post_options: dict = None,
        timeline_label: str = "[timelinev]",
        ref_image_path: str = "",
        safety_sheet_dir: str = "",
    ) -> tuple:
        """
        Xuất đồng thời:
        1. Ảnh lưới tổng quan 1s/khung hình (108x192 mỗi ô) cho Pass 1 (Scoreboard / Ticker / Watermark)
        2. Ảnh tham chiếu đơn 9:16 Full HD tại t ~ 1s
        3. Bộ ảnh lưới chi tiết an toàn Safety Sheets 5x5 (216x384 mỗi ô) có đánh dấu thời gian t=Xs cho Pass 2 (Blood Detection)
        """
        import time
        import math
        grid_started = time.perf_counter()
        safety_files = []
        try:
            dur = max(5.0, float(duration_sec))
            step_sec = 1.0
            total_frames = max(5, int(math.ceil(dur / step_sec)))
            cols = 10
            rows = max(1, int(math.ceil(total_frames / cols)))
            actual_total = cols * rows
            sample_fps = actual_total / dur

            t_label = timeline_label if timeline_label.startswith("[") else f"[{timeline_label}]"
            pre_fps = f"{t_label}fps={sample_fps:.5f}[v_1fps]"

            if post_options is not None:
                qc_vf = cls._build_qc_vf_filter(post_options, input_label="[v_1fps]")
            elif vf_color_grading:
                raw_vf = vf_color_grading.replace(t_label, "[v_1fps]") if t_label in vf_color_grading else vf_color_grading.replace("[timelinev]", "[v_1fps]")
                import re
                cleaned = re.sub(r",unsharp=[^,;\]]+", "", raw_vf)
                cleaned = re.sub(r",noise=[^,;\]]+", "", cleaned)
                cleaned = re.sub(r",vignette=[^,;\]]+", "", cleaned)
                cleaned = re.sub(r",curves=[^,;\]]+", "", cleaned)
                qc_vf = cleaned
            else:
                qc_vf = "[v_1fps]setsar=1[vout]"

            safety_pattern = ""
            if not safety_sheet_dir and output_grid_path:
                safety_sheet_dir = os.path.dirname(os.path.abspath(output_grid_path))
            if safety_sheet_dir and os.path.isdir(safety_sheet_dir):
                safety_pattern = os.path.join(safety_sheet_dir, "safety_sheet_%02d.jpg")
                for old_s in os.listdir(safety_sheet_dir):
                    if old_s.startswith("safety_sheet_") and old_s.endswith(".jpg"):
                        try:
                            os.remove(os.path.join(safety_sheet_dir, old_s))
                        except Exception:
                            pass

            if ref_image_path and safety_pattern:
                grid_filter = (
                    f"{timeline_graph};{pre_fps};{qc_vf};"
                    f"[vout]split=3[for_grid][for_ref][for_safe];"
                    f"[for_ref]select='eq(n,1)',scale=720:1280[refv];"
                    f"[for_grid]scale=108:192,tile={cols}x{rows}[gridv];"
                    f"[for_safe]scale=216:384,tile=5x5[safev]"
                )
                map_args = [
                    "-map", "[gridv]", "-frames:v", "1", "-q:v", "3", output_grid_path,
                    "-map", "[refv]", "-frames:v", "1", "-q:v", "3", ref_image_path,
                    "-map", "[safev]", "-q:v", "3", safety_pattern
                ]
            elif ref_image_path:
                grid_filter = (
                    f"{timeline_graph};{pre_fps};{qc_vf};"
                    f"[vout]split=2[for_grid][for_ref];"
                    f"[for_ref]select='eq(n,1)',scale=720:1280[refv];"
                    f"[for_grid]scale=108:192,tile={cols}x{rows}[gridv]"
                )
                map_args = [
                    "-map", "[gridv]", "-frames:v", "1", "-q:v", "3", output_grid_path,
                    "-map", "[refv]", "-frames:v", "1", "-q:v", "3", ref_image_path
                ]
            else:
                grid_filter = (
                    f"{timeline_graph};{pre_fps};{qc_vf};"
                    f"[vout]scale=108:192,tile={cols}x{rows}[gridv]"
                )
                map_args = [
                    "-map", "[gridv]", "-frames:v", "1", "-q:v", "3", output_grid_path
                ]

            audio_pad = timeline_audio_label.strip("[]") if timeline_audio_label else ("timelinea" if "[timelinea]" in timeline_graph else "")
            if audio_pad and f"[{audio_pad}]" in timeline_graph:
                grid_filter += f";[{audio_pad}]anullsink"

            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                *timeline_inputs,
                "-t", f"{dur:.2f}",
                "-filter_complex", grid_filter,
                *map_args
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=CREATE_NO_WINDOW
            )
            if res.returncode != 0:
                logger.warning("⚠️ [GEMINI GRID] FFmpeg tạo ảnh lưới lỗi: %s", (res.stderr or "").strip()[-400:])
            ok = os.path.isfile(output_grid_path) and os.path.getsize(output_grid_path) > 1000

            if safety_sheet_dir and os.path.isdir(safety_sheet_dir):
                safety_files = sorted([
                    os.path.join(safety_sheet_dir, f)
                    for f in os.listdir(safety_sheet_dir)
                    if f.startswith("safety_sheet_") and f.endswith(".jpg")
                ])
                if safety_files:
                    try:
                        import cv2
                        for s_idx, sp in enumerate(safety_files):
                            img = cv2.imread(sp)
                            if img is not None:
                                base_sec = s_idx * 25
                                for r in range(5):
                                    for c in range(5):
                                        sec = base_sec + r * 5 + c
                                        if sec < int(dur + 1):
                                            x = c * 216 + 8
                                            y = r * 384 + 25
                                            cv2.putText(img, f"t={sec}s", (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
                                cv2.imwrite(sp, img)
                        logger.info(f"📸 [SAFETY SHEETS] Đã tạo & đánh dấu nhãn giây trên {len(safety_files)} Safety Sheets 5x5.")
                    except Exception as stamp_err:
                        logger.warning(f"⚠️ [SAFETY SHEETS] Lỗi đánh dấu nhãn giây: {stamp_err}")

            if ok:
                logger.info(
                    "📸 [GEMINI GRID] Đã tạo thành công ảnh lưới 1s/khung hình (%d frames, %dx%d) và %d safety sheets trong %.2fs.",
                    actual_total, cols, rows, len(safety_files), time.perf_counter() - grid_started
                )
            return (ok, safety_files)
        except Exception as exc:
            logger.warning("⚠️ [GEMINI GRID] Không thể tạo ảnh lưới (%s); bỏ qua bước QC.", exc)
            return (False, [])


    @classmethod
    def _build_direct_timeline_graph(cls, source_video: str, intervals: list,
                                     hook_video: str = "", hook_duration: float = 0.0) -> tuple:
        """Tạo input + filter graph từ timeframe; không xuất highlight_broll_*.mp4."""
        input_args = []
        filters = []
        labels = []
        input_index = 0

        def normalize(index, label, mirror=False):
            flip = ",hflip" if mirror else ""
            filters.append(
                f"[{index}:v]fps=30,"
                f"scale={cls.CANVAS_W}:{cls.CANVAS_H}:force_original_aspect_ratio=decrease,"
                f"pad={cls.CANVAS_W}:{cls.CANVAS_H}:(ow-iw)/2:(oh-ih)/2:black,"
                f"setsar=1{flip},setpts=PTS-STARTPTS[{label}]"
            )

        hook_label = ""
        if hook_video and os.path.isfile(hook_video):
            input_args += ["-i", hook_video]
            hook_label = "hookv"
            normalize(input_index, hook_label)
            input_index += 1

        for idx, interval in enumerate(intervals):
            start = max(0.0, float(interval.get("start", 0)))
            duration = max(0.05, float(interval.get("end", start)) - start)
            input_args += ["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", source_video]
            label = f"seg{idx}"
            normalize(input_index, label, bool(interval.get("mirror")))
            labels.append(label)
            input_index += 1

        if not labels and not hook_label:
            raise RuntimeError("Timeline không có Hook hoặc B-roll.")

        if hook_label and labels:
            first_duration = max(0.05, float(intervals[0]["end"]) - float(intervals[0]["start"]))
            fade_duration = min(0.5, max(0.1, float(hook_duration) / 2.0), first_duration / 2.0)
            offset = max(0.0, float(hook_duration) - fade_duration)
            filters.append(
                f"[{hook_label}][{labels[0]}]xfade=transition=fadewhite:"
                f"duration={fade_duration:.4f}:offset={offset:.4f}[headv]"
            )
            concat_labels = ["headv"] + labels[1:]
        else:
            concat_labels = ([hook_label] if hook_label else []) + labels

        if len(concat_labels) == 1:
            filters.append(f"[{concat_labels[0]}]null[timelinev]")
        else:
            joined = "".join(f"[{label}]" for label in concat_labels)
            filters.append(f"{joined}concat=n={len(concat_labels)}:v=1:a=0[timelinev]")
        return input_args, ";".join(filters), "[timelinev]", input_index

    @classmethod
    def _build_direct_av_timeline_graph(cls, source_video: str, intervals: list,
                                        hook_video: str = "", hook_duration: float = 0.0) -> tuple:
        """Dựng timeframe với hình và đúng âm thanh gốc của từng đoạn."""
        input_args, filters, video_labels, audio_labels = [], [], [], []
        input_index = 0

        def normalize(index, vlabel, alabel, mirror=False):
            flip = ",hflip" if mirror else ""
            filters.append(
                f"[{index}:v]fps=30,scale={cls.CANVAS_W}:{cls.CANVAS_H}:"
                f"force_original_aspect_ratio=decrease,pad={cls.CANVAS_W}:{cls.CANVAS_H}:"
                f"(ow-iw)/2:(oh-ih)/2:black,setsar=1{flip},setpts=PTS-STARTPTS[{vlabel}]"
            )
            filters.append(
                f"[{index}:a]aresample=44100,aformat=sample_fmts=fltp:"
                f"channel_layouts=stereo,asetpts=PTS-STARTPTS[{alabel}]"
            )

        hook_v = hook_a = ""
        if hook_video and os.path.isfile(hook_video):
            input_args += ["-i", hook_video]
            hook_v, hook_a = "hookv", "hooka"
            normalize(input_index, hook_v, hook_a)
            input_index += 1

        for idx, interval in enumerate(intervals):
            start = max(0.0, float(interval.get("start", 0)))
            duration = max(0.05, float(interval.get("end", start)) - start)
            input_args += ["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", source_video]
            vlabel, alabel = f"seg{idx}v", f"seg{idx}a"
            normalize(input_index, vlabel, alabel, bool(interval.get("mirror")))
            video_labels.append(vlabel)
            audio_labels.append(alabel)
            input_index += 1

        if not video_labels and not hook_v:
            raise RuntimeError("Timeline tiếng gốc không có Hook hoặc cảnh được chọn.")

        overlap = 0.0
        if hook_v and video_labels:
            first_duration = max(0.05, float(intervals[0]["end"]) - float(intervals[0]["start"]))
            overlap = min(0.5, max(0.1, float(hook_duration) / 2.0), first_duration / 2.0)
            offset = max(0.0, float(hook_duration) - overlap)
            filters.append(
                f"[{hook_v}][{video_labels[0]}]xfade=transition=fadewhite:"
                f"duration={overlap:.4f}:offset={offset:.4f}[headv]"
            )
            filters.append(f"[{hook_a}][{audio_labels[0]}]acrossfade=d={overlap:.4f}:c1=tri:c2=tri[heada]")
            video_labels = ["headv"] + video_labels[1:]
            audio_labels = ["heada"] + audio_labels[1:]
        else:
            video_labels = ([hook_v] if hook_v else []) + video_labels
            audio_labels = ([hook_a] if hook_a else []) + audio_labels

        if len(video_labels) == 1:
            filters += [f"[{video_labels[0]}]null[timelinev]", f"[{audio_labels[0]}]anull[timelinea]"]
        else:
            filters.append(
                f"{''.join(f'[{x}]' for x in video_labels)}concat=n={len(video_labels)}:v=1:a=0[timelinev]"
            )
            filters.append(
                f"{''.join(f'[{x}]' for x in audio_labels)}concat=n={len(audio_labels)}:v=0:a=1[timelinea]"
            )
        raw_duration = max(0.05, float(hook_duration) + sum(
            max(0.0, float(x.get("end", 0)) - float(x.get("start", 0))) for x in intervals
        ) - overlap)
        return input_args, ";".join(filters), "[timelinev]", "[timelinea]", input_index, raw_duration

    @classmethod
    def _compute_timeline_segments(cls, clip_intervals: list, hook_ready: str = "", hook_duration: float = 0.0) -> list:
        """Tính toán mốc thời gian (start, end) và trạng thái mirror của từng đoạn trên timeline."""
        segments = []
        curr_t = 0.0
        has_hook = bool(hook_ready and os.path.isfile(hook_ready) and hook_duration > 0.0)

        if has_hook and clip_intervals:
            first_duration = max(0.05, float(clip_intervals[0].get("end", 0)) - float(clip_intervals[0].get("start", 0)))
            fade_duration = min(0.5, max(0.1, float(hook_duration) / 2.0), first_duration / 2.0)
            offset = max(0.0, float(hook_duration) - fade_duration)
            segments.append({
                "start": 0.0,
                "end": offset,
                "mirror": False
            })
            segments.append({
                "start": offset,
                "end": offset + first_duration,
                "mirror": bool(clip_intervals[0].get("mirror", False))
            })
            curr_t = offset + first_duration
            remaining_intervals = clip_intervals[1:]
        elif has_hook:
            segments.append({
                "start": 0.0,
                "end": float(hook_duration),
                "mirror": False
            })
            curr_t = float(hook_duration)
            remaining_intervals = []
        else:
            remaining_intervals = clip_intervals

        for interval in remaining_intervals:
            st = max(0.0, float(interval.get("start", 0)))
            dur = max(0.05, float(interval.get("end", st)) - st)
            segments.append({
                "start": curr_t,
                "end": curr_t + dur,
                "mirror": bool(interval.get("mirror", False))
            })
            curr_t += dur

        return segments

    @classmethod
    def apply_random_broll_mirror(cls, clip_paths: list, post_options: dict = None) -> list:
        """Lật ngang ngẫu nhiên ~5–7 clip trên 15 B-roll (không đụng hook)."""
        paths = list(clip_paths or [])
        if not paths or not (post_options or {}).get("random_broll_mirror"):
            return paths
        n = len(paths)
        lo = max(1, int(round(n * 5 / 15)))
        hi = max(lo, int(round(n * 7 / 15)))
        hi = min(n, hi)
        count = random.randint(lo, hi) if n > 1 else 1
        count = min(n, max(1, count))
        chosen = sorted(random.sample(range(n), count))
        logger.info(
            f"🪞 [B-ROLL] Random phản chiếu {count}/{n} clip: "
            + ", ".join(str(i + 1) for i in chosen)
        )
        for i in chosen:
            src = paths[i]
            if not src or not os.path.isfile(src):
                continue
            tmp = src + ".hflip.mp4"
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", src, "-vf", "hflip", "-an",
                "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "17",
                "-pix_fmt", "yuv420p", tmp,
            ]
            try:
                run_ffmpeg_auto(cmd, label="lật cảnh", logger=logger, check=True, creationflags=CREATE_NO_WINDOW)
            except Exception:
                cmd[cmd.index("h264_nvenc")] = "libx264"
                if "-preset" in cmd:
                    cmd[cmd.index("-preset") + 1] = "ultrafast"
                try:
                    subprocess.run(cmd, check=True, creationflags=0x08000000)
                except Exception as e:
                    logger.warning(f"⚠️ Không hflip được clip {i + 1}: {e}")
                    if os.path.exists(tmp):
                        try:
                            os.remove(tmp)
                        except Exception:
                            pass
                    continue
            try:
                os.replace(tmp, src)
            except Exception:
                shutil.copy2(tmp, src)
                try:
                    os.remove(tmp)
                except Exception:
                    pass
        return paths

    @classmethod
    def build_final_video(cls, specific_dir: str, post_options: dict = None) -> str:
        """
        Hàm điều phối tổng thể Bước 3 với log chi tiết từng bước:
        1. Cắt thô các đoạn B-roll và Hook (siêu tốc, không dính filter).
        2. Ghép nối các đoạn thô thành video tổng.
        3. Ghép nối và đồng bộ âm thanh.
        4. Bốc sub chi tiết từ audio tổng hợp.
        5. Đóng gói tổng thể (Áp dụng 15 màu sắc, khung hình, speed, banner và sub ở bước cuối).
        """
        import time
        start_time_render = time.perf_counter()
        stage_times = {}

        def finish_stage(name: str, started_at: float) -> float:
            elapsed = max(0.0, time.perf_counter() - started_at)
            stage_times[name] = elapsed
            minutes, seconds = divmod(int(round(elapsed)), 60)
            if minutes:
                display = f"{minutes} phút {seconds:02d} giây"
            else:
                display = f"{seconds} giây"
            logger.info(f"⏱️ [TIMER EDITOR] {name}: {display} ({elapsed:.2f}s)")
            return elapsed

        stage_started = time.perf_counter()
        logger.info("🎬 [EDITOR] Bắt đầu quy trình biên tập Hậu kỳ & Xuất Video Final...")
        
        hook_path = os.path.join(specific_dir, "hook_segment.mp4")
        source_video = os.path.join(specific_dir, "source_video.mp4")
        srt_path = os.path.join(specific_dir, "transcript_sub.srt")
        voice_path = os.path.join(specific_dir, "voice_output.mp3")
        title_file_path = os.path.join(specific_dir, "original_title.txt")
        summary_path = os.path.join(specific_dir, "summary_output.txt")

        post_options = post_options or {
            "speed": 1.0, "audio_boost": 6.0, "blur_bg": True, "zoom_in": False,
            "zoom_percent": 115, "scale_w": 100, "scale_h": 100, "pos_x": 0, "pos_y": 0,
            "saturation": 1.0, "contrast": 1.0, "brightness": 0.0,
            "sub_style": "CapCut Classic (Trắng viền đen)", "sub_size": 10, "sub_outline": 3
        }
        use_ai_voice = bool(post_options.get("use_ai_voice", True))
        original_audio_mode = bool(post_options.get("original_audio_mode", not use_ai_voice))
        try:
            original_mix_level = max(0.0, min(
                1.0, float(post_options.get("original_audio_mix_percent", 50.0) or 0.0) / 100.0
            ))
        except (TypeError, ValueError):
            original_mix_level = 0.5
        mix_original_with_voice = bool(
            use_ai_voice and post_options.get("mix_original_audio_with_voice", False)
            and original_mix_level > 0.0 and cls._has_audio(source_video)
        )
        if use_ai_voice and post_options.get("mix_original_audio_with_voice", False) and not mix_original_with_voice:
            logger.warning("⚠️ [AUDIO MIX] Source không có audio hợp lệ hoặc mức bằng 0%; chỉ dùng giọng AI.")

        if not os.path.exists(source_video):
            raise FileNotFoundError("❌ Thiếu file source_video.mp4 trong thư mục video!")
        if use_ai_voice and not os.path.exists(voice_path):
            raise FileNotFoundError("❌ Chế độ AI + Voice đang bật nhưng thiếu voice_output.mp3!")

        src_w, src_h = cls._probe_video_size(source_video)
        if src_w and src_h and (src_w != cls.CANVAS_W or src_h != cls.CANVAS_H):
            logger.warning(
                f"⚠️ Source không phải {cls.CANVAS_W}x{cls.CANVAS_H} (thực tế {src_w}x{src_h}). "
                "Path A sẽ scale/pad về canvas 1920x1080 trước khi crop/render."
            )
        elif src_w and src_h:
            logger.info(f"📐 Source probe: {src_w}x{src_h}")

        temp_clips_dir = os.path.join(specific_dir, "temp_clips")
        if os.path.exists(temp_clips_dir):
            for f in os.listdir(temp_clips_dir):
                try: os.remove(os.path.join(temp_clips_dir, f))
                except: pass
        os.makedirs(temp_clips_dir, exist_ok=True)

        total_voice_duration = 0.0
        if use_ai_voice:
            logger.info("⏱️ Đang đo thời lượng file giọng đọc AI...")
            cmd_voice_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", voice_path]
            res_vd = subprocess.run(cmd_voice_dur, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
            raw_vd = res_vd.stdout.strip()
            try:
                total_voice_duration = float(raw_vd) if (raw_vd and raw_vd != "N/A") else 60.0
            except (ValueError, TypeError):
                total_voice_duration = 60.0
        else:
            logger.info("🥊 [TIẾNG GỐC] Không dùng AI, Voice hoặc Timesub; dựng theo scene OpenCV.")

        hook_duration = 0.0
        hook_ready = os.path.join(temp_clips_dir, "hook_ready.mp4")
        
        use_custom_hook = bool(post_options.get("use_custom_hook", False)) if use_ai_voice else False
        custom_hook_file = post_options.get("custom_hook_path", "")
        native_hook_enabled = (not use_custom_hook) and bool(post_options.get("native_hook_enabled", True))
        hook_path = os.path.join(specific_dir, "hook_segment.mp4")

        if use_custom_hook and custom_hook_file and os.path.exists(custom_hook_file):
            logger.info(f"🎬 Đang chuẩn bị Hook tùy chỉnh từ: {custom_hook_file}")
            run_cmd_logged([
                "ffmpeg", "-y", "-i", custom_hook_file,
                "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30",
                "-c:v", cls.ACTIVE_VIDEO_ENCODER, "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "256k",
                hook_ready
            ], label="chuẩn bị Hook AI", logger=logger, check=True, creationflags=CREATE_NO_WINDOW)
        elif native_hook_enabled and os.path.exists(hook_path) and os.path.getsize(hook_path) > 1024:
            # get_hook_segment() đã xuất H.264/yuv420p + AAC. Mã hóa lại ở đây
            # vừa tốn thời gian, vừa có thể lỗi driver/GPU dù Hook nguồn hoàn toàn tốt.
            logger.info("🎬 Đang chuẩn bị Hook đã được chuẩn hóa...")
            shutil.copy2(hook_path, hook_ready)
            
            if os.path.exists(hook_ready) and os.path.getsize(hook_ready) > 1024:
                cmd_hook_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", hook_ready]
                res_hd = subprocess.run(cmd_hook_dur, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
                raw_hd = res_hd.stdout.strip()
                try:
                    hook_duration = float(raw_hd) if (raw_hd and raw_hd != "N/A") else 8.0
                except (ValueError, TypeError):
                    hook_duration = 8.0

        if native_hook_enabled and os.path.exists(hook_ready) and os.path.getsize(hook_ready) > 1024 and hook_duration <= 0.05:
            cmd_hook_dur = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", hook_ready]
            res_hd = subprocess.run(cmd_hook_dur, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, creationflags=0x08000000)
            raw_hd = res_hd.stdout.strip()
            try:
                hook_duration = float(raw_hd) if (raw_hd and raw_hd != "N/A") else 0.0
            except (ValueError, TypeError):
                hook_duration = 0.0

        if not use_custom_hook and not native_hook_enabled:
            hook_duration = 0.0
            logger.info("⏭️ [HOOK TẮT] Bỏ qua mọi hook cũ; timeline bắt đầu trực tiếp từ B-roll.")

        finish_stage("1. Chuẩn bị source + Hook", stage_started)
        stage_started = time.perf_counter()

        opencv_target_video, total_source_duration = cls.resolve_low_res_for_visual(
            specific_dir, source_video
        )
        if total_source_duration <= 1.0:
            total_source_duration = 60.0

        safe_start_limit = total_source_duration * cls.BROLL_SCAN_START_RATIO
        safe_end_limit = total_source_duration * cls.BROLL_SCAN_END_RATIO
        if original_audio_mode:
            requested_total = max(60.0, min(300.0, float(post_options.get("original_audio_target_sec", 90.0) or 90.0)))
            # Tiếng gốc không bị ràng buộc bởi voice: lấy dư có chủ đích 10%,
            # nằm giữa khoảng linh hoạt +10..20% mà người dùng chấp nhận.
            target_total = min(360.0, requested_total * 1.10)
            available_body = max(0.0, safe_end_limit - safe_start_limit)
            required_body_duration = min(max(0.0, target_total - hook_duration), available_body)
            logger.info(
                f"🥊 [TIẾNG GỐC] Mốc {requested_total:.0f}s; dựng linh hoạt khoảng "
                f"{target_total:.0f}s (+10%); Hook {hook_duration:.2f}s + "
                f"cảnh hành động tối đa {required_body_duration:.2f}s. Source ngắn sẽ tự hạ chỉ tiêu."
            )
        else:
            # Speed là lựa chọn nhịp dựng của người dùng, không được tự thay đổi
            # để ép video về đúng thời lượng mục tiêu. Duration được phép dao
            # động tự nhiên khoảng ±20% theo độ dài voice thực tế.
            speed_value = max(0.5, min(3.0, float(post_options.get("speed", 1.0) or 1.0)))
            required_body_duration = total_voice_duration / speed_value + 1.0
            logger.info(
                f"⏱️ [B-ROLL] Voice thô {total_voice_duration:.2f}s / {speed_value:.2f}x + 1s "
                f"= {required_body_duration:.2f}s timeline."
            )
        logger.info(
            f"🎞️ [B-ROLL] Quét nguồn {safe_start_limit:.1f}s–{safe_end_limit:.1f}s "
            f"({cls.BROLL_SCAN_START_RATIO:.0%}–{cls.BROLL_SCAN_END_RATIO:.0%})."
        )

        logger.info("🔍 Đang lập lịch cắt B-roll (theo chế độ đã chọn)...")
        clip_intervals = cls._resolve_broll_intervals(
            opencv_target_video, safe_start_limit, safe_end_limit, required_body_duration,
            post_options=post_options, specific_dir=specific_dir,
        )
        if not clip_intervals:
            raise RuntimeError("❌ Scene map không tạo được timeline B-roll.")
        selected_body_duration = sum(
            max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
            for item in clip_intervals
        )
        required_source_duration = required_body_duration * max(0.5, min(3.0, float(post_options.get("speed", 1.0) or 1.0)))
        if selected_body_duration + 0.05 < required_source_duration:
            shortage = required_source_duration - selected_body_duration
            logger.warning(
                f"⚠️ [FINAL VISUAL SAFETY] Scene chỉ đủ {selected_body_duration:.2f}s/"
                f"{required_source_duration:.2f}s nguồn; tự động bổ sung {shortage:.2f}s B-roll phụ "
                "để đảm bảo luồng hình luôn phủ 100% âm thanh, triệt tiêu lỗi đứng hình."
            )
            loop_idx = 0
            while selected_body_duration < required_source_duration - 0.001 and clip_intervals:
                src_clip = dict(clip_intervals[loop_idx % len(clip_intervals)])
                dur_needed = required_source_duration - selected_body_duration
                clip_dur = max(0.05, float(src_clip.get("end", 0)) - float(src_clip.get("start", 0)))
                use_dur = min(clip_dur, dur_needed)
                extra_clip = dict(src_clip)
                extra_clip["end"] = round(float(extra_clip["start"]) + use_dur, 6)
                extra_clip["duration"] = round(use_dur, 6)
                extra_clip["mapping_role"] = "emergency_fill"
                clip_intervals.append(extra_clip)
                selected_body_duration += use_dur
                loop_idx += 1
        logger.info(
            f"🧾 [B-ROLL V2] Dùng trực tiếp {len(clip_intervals)} timeframe từ source gốc; "
            "không tạo highlight_broll_*.mp4."
        )

        finish_stage("2. Scene map + chấm cảnh + timeline", stage_started)
        stage_started = time.perf_counter()

        final_audio_merged = ""
        if original_audio_mode:
            logger.info("🎵 [TIẾNG GỐC] Âm thanh sẽ được cắt cùng chính xác từng timeframe; không tạo audio AI trung gian.")
        else:
            logger.info("🎵 Đang thực hiện đồng bộ và ghép nối các phần âm thanh (Hook + 1s silence + Voice AI)...")
            hook_audio_part = os.path.join(temp_clips_dir, "hook_audio_fixed.wav")
            if os.path.exists(hook_ready):
                hook_level = float(post_options.get("hook_audio_boost", post_options.get("audio_boost", 20.0)))
                hook_filter = cls.capcut_audio_filter(hook_level)
                logger.info(f"🎵 [HOOK AUDIO] Áp dụng chuẩn CapCut audio filter với mức gain: {hook_level:.1f} dB")
                subprocess.run([
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", hook_ready,
                    "-vn", "-ar", "44100", "-ac", "2", "-t", str(hook_duration),
                    "-af", hook_filter, hook_audio_part
                ], check=True, creationflags=0x08000000)
            silence_audio_part = os.path.join(temp_clips_dir, "silence_1s.wav")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1.0", silence_audio_part
            ], check=True, creationflags=CREATE_NO_WINDOW)
            voice_normalized = os.path.join(temp_clips_dir, "voice_normalized.wav")
            voice_level = post_options.get("audio_boost", 6.0)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", voice_path,
                "-ar", "44100", "-ac", "2", "-af", cls.capcut_audio_filter(voice_level), voice_normalized
            ], check=True, creationflags=CREATE_NO_WINDOW)
            audio_concat_list = os.path.join(specific_dir, "audio_concat_fixed.txt")
            with open(audio_concat_list, "w", encoding="utf-8") as f_ac:
                if os.path.exists(hook_audio_part) and os.path.getsize(hook_audio_part) > 0:
                    f_ac.write(f"file '{os.path.abspath(hook_audio_part).replace('\\', '/')}'\n")
                f_ac.write(f"file '{os.path.abspath(silence_audio_part).replace('\\', '/')}'\n")
                f_ac.write(f"file '{os.path.abspath(voice_normalized).replace('\\', '/')}'\n")
            final_audio_merged = os.path.join(specific_dir, "final_audio_fixed.aac")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", audio_concat_list, "-c:a", "aac", "-b:a", "256k", final_audio_merged
            ], check=True, creationflags=0x08000000)
            logger.info("✅ Đã hoàn tất gộp file âm thanh tổng hợp!")

        finish_stage("3. Xử lý + ghép âm thanh", stage_started)
        stage_started = time.perf_counter()

        # Dùng thẳng script TTS: bỏ hoàn toàn Whisper CPU ở hậu kỳ vì kết quả của
        # nó trước đây vẫn bị SRT exact ghi đè, vừa chậm vừa có thể sai nội dung.
        perfect_srt_path = ""
        exact_srt_path = os.path.join(specific_dir, "voice_script_exact.srt")
        enable_sub = bool(post_options.get("enable_sub", True))
        if original_audio_mode:
            logger.info("⏭️ [TIẾNG GỐC] Không tạo/nhúng subtitle vì không có lời đọc AI.")
        elif not enable_sub:
            logger.info("⏭️ [SUBTITLE TẮT] Phụ đề đã được tắt trong cấu hình Title & Sub.")
        elif os.path.exists(exact_srt_path):
            # Path A burn sub trước rồi mới setpts tăng tốc toàn bộ hình. Vì vậy
            # SRT phải giữ timeline gốc; setpts sẽ tự co sub đúng cùng audio.
            perfect_srt_path = exact_srt_path
            logger.info("📝 [SUB EXACT] Path A dùng đúng nội dung script TTS, không dùng transcript nguồn.")
        else:
            raise FileNotFoundError("Thiếu voice_script_exact.srt; dừng render để tránh nhúng sai subtitle.")

        # --- BỔ SUNG SHIFT SUB CHO PATH A ĐỂ KHỚP VỚI [HOOK] + [1S SILENCE] ---
        # SRT được burn trước setpts nên offset cũng giữ timeline gốc. Sau đó setpts
        # và atempo cùng co toàn bộ timeline theo đúng một hệ số speed.
        total_hook_and_silence_delay = hook_duration + 1.0
        if not original_audio_mode and os.path.exists(perfect_srt_path) and total_hook_and_silence_delay > 0:
            try:
                from editor_ai import EditorAI
                shifted_srt_path = os.path.join(specific_dir, "transcript_sub_shifted.srt")
                EditorAI.shift_srt_timestamps(perfect_srt_path, total_hook_and_silence_delay, shifted_srt_path)
                perfect_srt_path = shifted_srt_path
                logger.info(f"📝 [SUB PATH A] Đã shift toàn bộ sub trễ thêm {total_hook_and_silence_delay:.2f}s để khớp hoàn toàn với audio mix.")
            except Exception as shift_err:
                logger.warning(f"⚠️ Không thể shift sub Path A: {shift_err}")

        logger.info("🎨 Đang khởi tạo Banner tiêu đề động...")
        source_title = str(post_options.get("source_title") or "").strip()
        if original_audio_mode and source_title:
            title_line_1, title_line_2 = cls.split_banner_title(source_title)
            logger.info(f"🏷️ [TIẾNG GỐC] Banner dùng title gốc: {source_title}")
        else:
            title_line_1, title_line_2 = cls.read_banner_title_lines(
                specific_dir, title_file_path, summary_path
            )

        # 1. Gọi hàm vẽ banner custom nhận đầy đủ màu sắc dòng 1, dòng 2, màu viền và màu nền banner từ post_options
        banner_path, banner_w, banner_h = cls._create_dynamic_title_banner_custom(specific_dir, title_line_1, title_line_2, post_options)
        banner_x = int((1080 - banner_w) / 2)
        
        # 2. Đọc tọa độ Y của Title linh hoạt từ post_options (mặc định 260)
        banner_y = int(post_options.get("title_y_pos", 260))

        sub_size = post_options.get("sub_size", 10)
        sub_outline = post_options.get("sub_outline", 3)
        sub_margin_v = int(post_options.get("sub_margin_v", 100))
        
        # Nhận mã màu chữ sub và màu viền sub trực tiếp từ post_options (chuẩn ASS format)
        primary_color = post_options.get("sub_color", "&HFFFFFF&")
        outline_color = post_options.get("sub_outline_color", "&H000000&")

        sub_filter_str = ""
        target_srt_to_use = perfect_srt_path if (enable_sub and perfect_srt_path and os.path.exists(perfect_srt_path)) else ""
        if enable_sub and os.path.exists(target_srt_to_use):
            abs_srt_path = os.path.abspath(target_srt_to_use).replace('\\', '/')
            if ":" in abs_srt_path:
                drive, path_part = abs_srt_path.split(":", 1)
                formatted_srt_path = f"{drive}\\:{path_part}"
            else:
                formatted_srt_path = abs_srt_path

            # Đọc mẫu nội dung phụ đề để tự động phát hiện ngôn ngữ quốc gia (Nga, Đức, Pháp, Ả Rập, Việt...)
            sample_srt_text = ""
            try:
                with open(target_srt_to_use, "r", encoding="utf-8", errors="ignore") as sf:
                    sample_srt_text = sf.read(4096)
            except Exception:
                sample_srt_text = ""

            from font_manager import FontManager
            style_type = str(post_options.get("sub_style_type", "tiktok_slim"))
            style_specs = FontManager.get_subtitle_style_specs(
                style_type=style_type,
                sample_text=sample_srt_text,
                custom_overrides=post_options
            )

            font_name = style_specs["font_name"]
            sub_sz = style_specs["font_size"]
            sub_ol = style_specs["outline"]
            sub_sh = style_specs["shadow"]
            sub_bold = style_specs["bold"]

            # Nếu chọn kiểu classic in hoa toàn bộ, tạo bản SRT in hoa tạm thời
            srt_to_embed = formatted_srt_path
            if style_specs.get("uppercase") and sample_srt_text:
                try:
                    upper_srt_path = os.path.join(specific_dir, "sub_uppercase.srt")
                    with open(target_srt_to_use, "r", encoding="utf-8", errors="ignore") as in_f:
                        srt_content = in_f.read()
                    lines = srt_content.splitlines()
                    new_lines = []
                    for line in lines:
                        s_line = line.strip()
                        if s_line.isdigit() or "-->" in s_line or not s_line:
                            new_lines.append(line)
                        else:
                            new_lines.append(line.upper())
                    with open(upper_srt_path, "w", encoding="utf-8") as out_f:
                        out_f.write("\n".join(new_lines))
                    abs_upper = os.path.abspath(upper_srt_path).replace('\\', '/')
                    if ":" in abs_upper:
                        d, p = abs_upper.split(":", 1)
                        srt_to_embed = f"{d}\\:{p}"
                    else:
                        srt_to_embed = abs_upper
                except Exception as up_err:
                    logger.warning(f"⚠️ Không tạo được SRT in hoa: {up_err}")

            basic_sub_style = (
                f"FontName={font_name},FontSize={sub_sz},Bold={sub_bold},"
                f"PrimaryColour={primary_color},OutlineColour={outline_color},"
                f"BorderStyle=1,Outline={sub_ol},Shadow={sub_sh},"
                f"Alignment=2,MarginL=70,MarginR=70,MarginV={sub_margin_v},WrapStyle=1"
            )
            sub_filter_str = f"subtitles='{srt_to_embed}':force_style='{basic_sub_style}'"
            logger.info(
                f"💬 [SUBTITLE STYLE] Kiểu: '{style_specs['style_name']}' | "
                f"Font: {font_name} (Script: {style_specs['script']}) | "
                f"Size: {sub_sz} | Outline: {sub_ol} | Shadow: {sub_sh}"
            )

        finish_stage("4. Subtitle + banner", stage_started)
        stage_started = time.perf_counter()

        # ĐÓNG GÓI TỔNG THỂ AN TOÀN VÀ TỐC ĐỘ CAO (Khắc phục triệt để lỗi xung đột VRAM/Subtitles)
        final_output_path = os.path.join(specific_dir, "final_video.mp4")
        logger.info("⏳ [ĐÓNG GÓI TỔNG THỂ]: FFmpeg đang tiến hành kết nối broll, áp dụng 15 màu sắc, blur nền và nhúng sub...")

        speed = float(post_options.get("speed", 1.0) or 1.0)
        if original_audio_mode or mix_original_with_voice:
            timeline_inputs, timeline_graph, timeline_label, timeline_audio_label, timeline_input_count, audio_mix_raw = cls._build_direct_av_timeline_graph(
                source_video, clip_intervals,
                hook_video=hook_ready if os.path.exists(hook_ready) else "",
                hook_duration=hook_duration,
            )
        else:
            timeline_inputs, timeline_graph, timeline_label, timeline_input_count = cls._build_direct_timeline_graph(
                source_video, clip_intervals,
                hook_video=hook_ready if os.path.exists(hook_ready) else "",
                hook_duration=hook_duration,
            )
            timeline_audio_label = ""
            audio_mix_raw = float(hook_duration) + 1.0 + float(total_voice_duration)
        if mix_original_with_voice:
            audio_mix_raw = float(hook_duration) + 1.0 + float(total_voice_duration)
        audio_out = audio_mix_raw / max(speed, 0.001)
        fade_d = 1.0
        fade_st = max(0.0, audio_out - fade_d)
        if original_audio_mode:
            logger.info(
                f"⏱️ [TIẾNG GỐC] hình + tiếng timeframe/{speed:.2f} = {audio_out:.2f}s | "
                "audio luôn đi cùng cảnh, mờ dần 1s cuối."
            )
        else:
            logger.info(
                f"⏱️ [PATH A] tiếng (hook+1s+giọng)/{speed:.2f} = {audio_out:.2f}s | "
                f"hình = tiếng + clip cuối kéo dài | mờ dần 1s cuối."
            )
        banner_input_index = timeline_input_count
        audio_input_index = timeline_input_count + 1
        vf_color_grading = cls._build_vf_filter(post_options, input_label=timeline_label)

        # 5.1 Quét kiểm duyệt chính xác từng giây theo Shot Keyframes (Gemini Grid Inspector)
        timeline_segs = cls._compute_timeline_segments(
            clip_intervals,
            hook_ready=hook_ready if os.path.exists(hook_ready) else "",
            hook_duration=hook_duration,
        )

        enable_gemini_qc = bool(post_options.get("gemini_grid_inspector", True))
        watermark_blurs = []
        if enable_gemini_qc:
            try:
                from ai_processor import AIProcessor
                qc_ok, kf_items, frames_dir = cls.extract_timeline_shot_keyframes(
                    timeline_inputs, timeline_graph, vf_color_grading=vf_color_grading,
                    timeline_segs=timeline_segs, duration_sec=audio_mix_raw,
                    timeline_audio_label=timeline_audio_label,
                    post_options=post_options,
                    timeline_label=timeline_label,
                    output_dir=specific_dir
                )
                if qc_ok and kf_items:
                    api_key = str(post_options.get("api_key") or "")
                    model_name = str(post_options.get("ai_model") or "")
                    detected_items = AIProcessor.inspect_90s_grid_for_copyright(
                        api_key=api_key, model_name=model_name,
                        duration_sec=audio_mix_raw,
                        keyframes=kf_items
                    )
                    # Tinh chỉnh từng pixel và kiểm chứng máu HSV bằng OpenCV
                    if detected_items and frames_dir and os.path.isdir(frames_dir):
                        watermark_blurs = cls.refine_detection_boxes_with_opencv(
                            detected_items, frames_dir, duration_sec=audio_mix_raw
                        )
                    else:
                        watermark_blurs = detected_items or []
            except Exception as qc_err:
                logger.warning(f"⚠️ [GEMINI QC] Bỏ qua quét AI do lỗi: {qc_err}")

        # Chuẩn bị file 3D LUT chuyển màu đỏ sang xám tự nhiên (ZERO BLUR) nếu có vết máu
        has_blood = any("blood" in str(item.get("label", "")).lower() for item in watermark_blurs)
        red_to_gray_lut_path = cls.ensure_red_to_gray_lut() if has_blood else ""
        audio_input_index = timeline_input_count + 1

        curr_v_label = "vout"
        qc_filters_str, curr_v_label = cls.build_clustered_qc_filters(
            watermark_blurs, duration_sec=audio_mix_raw,
            curr_v_label=curr_v_label, output_w=cls.OUTPUT_W, output_h=cls.OUTPUT_H,
            red_to_gray_lut_path=red_to_gray_lut_path
        )

        v_chain = []
        if sub_filter_str:
            v_chain.append(sub_filter_str)
        if abs(speed - 1.0) >= 0.01:
            v_chain.append(f"setpts={1.0 / speed}*PTS")
        v_chain.append(f"fade=t=out:st={fade_st:.4f}:d={fade_d:.4f}")
        enable_title = bool(post_options.get("enable_title", True))
        if enable_title and banner_path and os.path.exists(banner_path):
            video_filter_final = (
                f"{timeline_graph};{vf_color_grading}{qc_filters_str};"
                f"[{curr_v_label}][{banner_input_index}:v]overlay={banner_x}:{banner_y}[v_banner];"
                f"[v_banner]{','.join(v_chain)}[outv]"
            )
            ffmpeg_inputs = [*timeline_inputs, "-i", banner_path]
        else:
            video_filter_final = (
                f"{timeline_graph};{vf_color_grading}{qc_filters_str};"
                f"[{curr_v_label}]{','.join(v_chain)}[outv]"
            )
            ffmpeg_inputs = [*timeline_inputs]

        scramble_audio = bool(post_options.get("scramble_original_audio", True))
        if original_audio_mode:
            audio_filter_final = cls.capcut_audio_filter(
                post_options.get("audio_boost", 20.0)
            )
            if abs(speed - 1.0) >= 0.01:
                audio_filter_final += f",atempo={speed}"
        elif abs(speed - 1.0) >= 0.01:
            audio_filter_final = f"alimiter=limit=0.89:attack=5:release=80:level=false,atempo={speed}"
        else:
            audio_filter_final = "alimiter=limit=0.89:attack=5:release=80:level=false"

        exact_max_duration_a = audio_out

        # Tách riêng thời gian dựng câu lệnh/filter graph khỏi thời gian FFmpeg
        # encode thật để log không còn gộp chung và gây hiểu nhầm.
        finish_stage("5. Dựng filter graph", stage_started)
        encode_started = time.perf_counter()
        if original_audio_mode:
            audio_graph = f"{timeline_audio_label}{audio_filter_final}[outa]"
        elif mix_original_with_voice:
            ffmpeg_inputs += ["-i", final_audio_merged]
            voice_chain = audio_filter_final
            bed_chain = []
            if scramble_audio:
                bed_chain.append(
                    "highpass=f=65,equalizer=f=3000:t=q:w=1:g=2.5,equalizer=f=600:t=q:w=1:g=-2.5,"
                    "aecho=0.8:0.88:18:0.12,asetrate=44100*1.03,atempo=0.970873786,aresample=44100"
                )
                logger.info("🛡️ [AUDIO SCRAMBLE] Áp dụng bóp méo âm thanh nền gốc (B-roll bed) chống bản quyền.")
            if abs(speed - 1.0) >= 0.01:
                bed_chain.append(f"atempo={speed}")
            mute_until = float(hook_duration) / max(speed, 0.001)
            bed_chain.append(f"volume=0:enable='lt(t,{mute_until:.4f})'")
            bed_chain.append(f"volume={original_mix_level:.4f}")
            audio_graph = (
                f"[{audio_input_index}:a]{voice_chain}[voicea];"
                f"{timeline_audio_label}{','.join(bed_chain)},apad[beda];"
                f"[voicea][beda]amix=inputs=2:duration=first:dropout_transition=0," 
                f"alimiter=limit=0.95:attack=5:release=80:level=false[outa]"
            )
            logger.info("🎚️ [AUDIO MIX] Giọng AI + tiếng B-roll gốc ở mức %.0f%%.",
                        original_mix_level * 100.0)
        else:
            ffmpeg_inputs += ["-i", final_audio_merged]
            audio_graph = f"[{audio_input_index}:a]{audio_filter_final}[outa]"

        run_ffmpeg_auto([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            *ffmpeg_inputs,
            "-filter_complex", f"{video_filter_final};{audio_graph}",
            "-map", "[outv]", "-map", "[outa]",
            "-t", f"{exact_max_duration_a:.4f}",
            "-c:v", "h264_nvenc",
            "-preset", "p4", "-rc", "vbr",
            "-cq", "17", "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M",
            "-c:a", "aac", "-b:a", "256k",
            final_output_path
        ], label="xuất video cuối", logger=logger, check=True, creationflags=CREATE_NO_WINDOW)

        encode_elapsed = finish_stage("6. FFmpeg encode video", encode_started)
        realtime_factor = exact_max_duration_a / max(encode_elapsed, 0.001)
        logger.info(
            f"📊 [ENCODE] Video {exact_max_duration_a:.2f}s | encode {encode_elapsed:.2f}s | "
            f"tốc độ {realtime_factor:.3f}x realtime | {30.0 * realtime_factor:.2f} fps hiệu dụng."
        )

        elapsed_seconds = int(time.perf_counter() - start_time_render)
        if elapsed_seconds < 60:
            time_str = f"{elapsed_seconds}s"
        else:
            m = elapsed_seconds // 60
            s = elapsed_seconds % 60
            time_str = f"{m} phút {s}s"

        logger.info(f"🎉 [SUCCESS] Xuất video Hậu kỳ hoàn chỉnh thành công tại: {final_output_path} (Tổng thời gian: {time_str})")

        export_started = time.perf_counter()
        exported_path = cls.finalize_exported_video(specific_dir, final_output_path, post_options)
        finish_stage("7. Sao chép output + dọn temp", export_started)

        total_elapsed = max(0.0, time.perf_counter() - start_time_render)
        measured = sum(stage_times.values())
        untracked = max(0.0, total_elapsed - measured)
        total_minutes, total_seconds = divmod(int(round(total_elapsed)), 60)
        logger.info("=" * 58)
        logger.info("⏱️ [CHI TIẾT HẬU KỲ]")
        for stage_name, stage_elapsed in stage_times.items():
            logger.info(f"   {stage_name:<40} {stage_elapsed:8.2f}s")
        logger.info(f"   {'Phần phụ chưa phân loại':<40} {untracked:8.2f}s")
        logger.info(f"   {'TỔNG HẬU KỲ':<40} {total_minutes} phút {total_seconds:02d} giây")
        logger.info("=" * 58)
        return exported_path

    @classmethod
    def _create_dynamic_title_banner_custom(cls, specific_dir: str, line1: str, line2: str, post_options: dict) -> tuple:
        post_options = post_options or {}
        c1 = post_options.get("title_color1", "black")
        c2 = post_options.get("title_color2", "red")
        outline_c = post_options.get("title_outline_color", "none")
        bg_c = post_options.get("title_bg_color", "white")
        stroke_w = 2 if outline_c and outline_c != "none" else 0
        stroke_col = outline_c if stroke_w else None

        raw = cls._join_banner_title_raw(line1, line2)
        line1, line2, font, font_size = cls._layout_banner_title(raw, stroke_w=stroke_w)

        pad_x = cls.BANNER_PAD_X
        pad_y = cls.BANNER_PAD_Y
        two_lines = bool(line1 and line2)
        spacing = int(font_size * cls.BANNER_LINE_GAP_RATIO) if two_lines else 0

        b1 = cls._banner_text_bbox(font, line1, stroke_w) if line1 else (0, 0, 0, 0)
        w1 = (b1[2] - b1[0]) if line1 else 0
        h1 = (b1[3] - b1[1]) if line1 else 0
        b2 = cls._banner_text_bbox(font, line2, stroke_w) if line2 else (0, 0, 0, 0)
        w2 = (b2[2] - b2[0]) if line2 else 0
        h2 = (b2[3] - b2[1]) if line2 else 0

        max_w = max(w1, w2, 1)
        total_h = (h1 if line1 else 0) + (h2 if line2 else 0) + spacing
        bw = int(max_w + pad_x * 2)
        bh = int(total_h + pad_y * 2)

        n_lines = 2 if two_lines else 1
        logger.info(f"🏷️ Banner title: {n_lines} dòng, cỡ chữ {font_size}px")

        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([(0, 0), (bw, bh)], radius=cls.BANNER_CORNER_RADIUS, fill=bg_c)

        cy = pad_y
        if line1:
            draw.text(
                ((bw - w1) / 2, cy - b1[1]),
                line1,
                fill=c1,
                font=font,
                stroke_width=stroke_w,
                stroke_fill=stroke_col,
            )
            cy += h1 + spacing
        if line2:
            draw.text(
                ((bw - w2) / 2, cy - b2[1]),
                line2,
                fill=c2,
                font=font,
                stroke_width=stroke_w,
                stroke_fill=stroke_col,
            )

        path = os.path.join(specific_dir, "title_banner_final.png")
        img.save(path)
        return path, bw, bh
