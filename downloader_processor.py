import os
import sys
import subprocess
from pathlib import Path
import cv2
import numpy as np
import re
import time
import unicodedata

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("DownloaderProcessor")

import yt_dlp

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Ưu tiên HD kiểu Hitomi: format_sort chọn res cao, KHÔNG loại hết format bằng height>=.
# height>= chỉ là lựa chọn đầu; cuối cùng luôn `best` (muxed 18/22) nếu không có DASH.
# Nhiều video/client không lộ bv* height>=1080 → selector chỉ có height>= sẽ fail cả chuỗi.
HIGH_RES_FORMAT = (
    "bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/"
    "bestvideo+bestaudio/best"
)
HIGH_RES_FORMAT_SORT = ["res:1080", "fps", "codec:h264:m4a"]
# Bổ sung thêm client 'tv' và 'mweb' để lách cơ chế bóp nghẽn 1080p của YouTube
HIGH_RES_EXTRACTOR_ARGS = {"youtube": {"player_client": ["tv", "android", "mweb", "web"]}}
HIGH_RES_EXTRACTOR_ARGS_RETRY = {"youtube": {"player_client": ["mweb", "web", "android", "tv"]}}
HIGH_RES_EXTRACTOR_ARGS_RETRY = {"youtube": {"player_client": ["ios", "web", "android"]}}

# Low-res: tải trực tiếp bản 360p từ YouTube; format 18 được ưu tiên
# nếu nó có đủ duration. Chỉ transcode từ HD sau khi mọi lần tải trực tiếp hỏng.
LOW_RES_FORMAT = (
    "18/"
    "bestvideo[height<=360][vcodec^=avc]+bestaudio/"
    "best[height<=360][vcodec^=avc]/"
    "best[height<=360]"
)
YT_HEADERS = {
    "Accept-Language": "en-US,en;q=0.9",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
}
LOW_RES_EXTRACTOR_ARGS = {"youtube": {"player_client": ["android", "ios", "web"]}}

# File hoàn chỉnh trong temp/<slug>/ — không xóa khi dọn resume junk.
FINAL_VIDEO_NAMES = frozenset({"source_video.mp4", "source_video_low.mp4"})
# High-res nhỏ hơn 1MB gần như chắc là stub/dở (format 18 stub ~572KB).
MIN_SOURCE_BYTES = 1 * 1024 * 1024
MIN_SOURCE_DURATION_SEC = 5.0
# Skip-if-valid: file 360p CŨ (stale) không skip — thử tải HD lại.
# Download MỚI ra 360p vì YouTube chỉ có vậy → chấp nhận, pipeline tiếp tục.
MIN_HIGH_SKIP_HEIGHT = 480
MIN_HIGH_PREFERRED_HEIGHT = 720
INVALID_HIGH_MAX_HEIGHT = 360
# source_video.f399.mp4 / source_video.f140.m4a — leftover DASH trước khi merge.
_YTDLP_FORMAT_LEFTOVER_RE = re.compile(r"\.f\d+\.(mp4|m4a|webm)$", re.IGNORECASE)


class DownloaderProcessor:
    """
    Module xử lý toàn diện: Tải video YouTube chuẩn chống chặn, trích xuất audio WAV, 
    thuật toán phát hiện xô xát/vật lộn/rung lắc máy quay thực tế (Wrestle & Shake Intensity),
    tự động nhận diện ranh giới cảnh (Scene Boundary) để cắt mượt mà không bị lẹm sang cảnh mới,
    và cơ chế kiểm tra bắt buộc Audio Stream bằng ffprobe đảm bảo 100% có tiếng trước khi xử lý.
    """
    
    TEMP_DIR = "temp"

    @classmethod
    def get_cookie_file_path(cls) -> str:
        """
        Tìm đường dẫn file cookie YouTube hợp lệ.
        Ưu tiên:
        1. Cấu hình 'youtube_cookie_file' trong config.json nếu 'use_youtube_cookies' bật và file tồn tại.
        2. File 'cookies.txt' nằm ngay thư mục gốc của project (ROOT_DIR / "cookies.txt").
        3. File 'data/cookies.txt' nếu có.
        """
        try:
            from config_manager import load_config
            cfg = load_config()
            use_cookies = cfg.get("use_youtube_cookies", True)
            if not use_cookies:
                return ""
            configured_path = cfg.get("youtube_cookie_file", "")
            if configured_path and os.path.isfile(configured_path):
                return os.path.abspath(configured_path)
        except Exception:
            pass

        candidates = [
            ROOT_DIR / "cookies.txt",
            ROOT_DIR / "data" / "cookies.txt",
            Path("cookies.txt"),
        ]
        for c in candidates:
            if c.is_file():
                return str(c.resolve())
        return ""

    @classmethod
    def apply_cookies_to_opts(cls, opts: dict = None) -> dict:
        """Thêm cấu hình cookiefile vào dict options của yt-dlp nếu tìm thấy file cookie hợp lệ."""
        opts_copy = dict(opts or {})
        cookie_path = cls.get_cookie_file_path()
        if cookie_path and os.path.isfile(cookie_path):
            opts_copy["cookiefile"] = cookie_path
            logger.info(f"🍪 [YOUTUBE COOKIE] Áp dụng cookie: {cookie_path}")

        # Tự động bỏ qua authcheck của youtubetab khi quét/tải playlist YouTube
        ext_args = dict(opts_copy.get("extractor_args") or {})
        yt_tab = dict(ext_args.get("youtubetab") or {})
        skip_list = list(yt_tab.get("skip") or [])
        if "authcheck" not in skip_list:
            skip_list.append("authcheck")
        yt_tab["skip"] = skip_list
        ext_args["youtubetab"] = yt_tab
        opts_copy["extractor_args"] = ext_args

        return opts_copy

    @classmethod
    def test_cookie_file(cls, cookie_path: str = None) -> tuple:
        """
        Kiểm tra tính hợp lệ của file cookie bằng cách query thử video giới hạn độ tuổi.
        Trả về (success: bool, message: str).
        """
        target_path = cookie_path or cls.get_cookie_file_path()
        if not target_path or not os.path.isfile(target_path):
            return False, "File cookie không tồn tại hoặc chưa được chọn."

        try:
            with open(target_path, "r", encoding="utf-8", errors="ignore") as f:
                head = f.read(2048)
                if not ("youtube.com" in head or "google.com" in head or "# Netscape HTTP Cookie File" in head):
                    return False, "File không đúng định dạng Netscape HTTP Cookie File của YouTube."
        except Exception as e:
            return False, f"Không thể đọc file cookie: {e}"

        test_url = "https://www.youtube.com/watch?v=IWpc6txw11c"
        opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiefile': target_path,
            'skip_download': True,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(test_url, download=False)
                title = info.get("title", "Video") if info else "Video"
                return True, f"Cookie hoạt động rất tốt! Đã xác thực thành công qua video 18+: '{title}'"
        except Exception as e:
            err_msg = str(e)
            if "Sign in to confirm your age" in err_msg:
                return False, "Tài khoản trong cookie chưa xác nhận đủ 18 tuổi hoặc cookie đã hết hạn."
            return False, f"Lỗi xác thực: {err_msg}"

    @classmethod
    def get_video_info(cls, url: str) -> dict:
        """Trích xuất metadata video an toàn kèm cookie nếu có."""
        opts = {'quiet': True, 'no_warnings': True}
        opts = cls.apply_cookies_to_opts(opts)
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False) or {}

    @staticmethod
    def _ff_run(cmd, check=True, capture=False):
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
    def probe_duration_sec(cls, path: str) -> float:
        if not path or not os.path.exists(path):
            return 0.0
        try:
            res = cls._ff_run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                check=False,
                capture=True,
            )
            val = (res.stdout or "").strip()
            if val and res.returncode == 0:
                dur = float(val)
                if dur > 0:
                    return dur
        except Exception:
            pass
        try:
            res = cls._ff_run(
                ["ffmpeg", "-hide_banner", "-i", path],
                check=False,
                capture=True,
            )
            text = (res.stderr or "") + (res.stdout or "")
            m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", text)
            if m:
                return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
        except Exception:
            pass
        return 0.0

    @classmethod
    def probe_video_size(cls, path: str) -> tuple:
        """Trả về (width, height); (0, 0) nếu probe thất bại."""
        if not path or not os.path.exists(path):
            return 0, 0
        try:
            res = cls._ff_run(
                [
                    "ffprobe", "-v", "error",
                    "-select_streams", "v:0",
                    "-show_entries", "stream=width,height",
                    "-of", "csv=p=0:s=x",
                    path,
                ],
                check=False,
                capture=True,
            )
            parts = (res.stdout or "").strip().split("x")
            if len(parts) == 2:
                return int(parts[0]), int(parts[1])
        except Exception:
            pass
        return 0, 0

    @classmethod
    def video_decodes_at_checkpoints(cls, path: str, duration: float = 0.0) -> bool:
        """Giải mã thử đầu/giữa/cuối để không tái sử dụng MP4 có metadata tốt nhưng data hỏng."""
        if not path or not os.path.isfile(path):
            return False
        dur = float(duration or cls.probe_duration_sec(path) or 0.0)
        if dur <= 0:
            return False

        # Seek trước input giúp phép thử nhanh ngay cả với video dài. Tránh đúng EOF.
        checkpoints = [0.0]
        if dur >= 12:
            checkpoints.append(dur * 0.5)
        if dur >= 30:
            checkpoints.append(max(0.0, dur - min(3.0, dur * 0.05)))
        for second in checkpoints:
            try:
                res = cls._ff_run(
                    [
                        "ffmpeg", "-hide_banner", "-loglevel", "error",
                        "-ss", f"{second:.3f}", "-i", path,
                        "-map", "0:v:0", "-frames:v", "1", "-f", "null", "-",
                    ],
                    check=False,
                    capture=True,
                )
                if res.returncode != 0:
                    return False
            except Exception:
                return False
        return True

    @staticmethod
    def _chosen_format_label(info_dict: dict) -> str:
        if not info_dict:
            return "?"
        req = info_dict.get("requested_formats")
        if isinstance(req, list) and req:
            ids = [str(x.get("format_id") or "") for x in req if isinstance(x, dict)]
            ids = [i for i in ids if i]
            if ids:
                return "+".join(ids)
        fid = info_dict.get("format_id")
        return str(fid) if fid else "?"

    @staticmethod
    def _is_progressive_18(info_dict: dict) -> bool:
        """True nếu yt-dlp chọn muxed format 18 (360p progressive)."""
        if not info_dict:
            return False
        fid = str(info_dict.get("format_id") or "").strip()
        if fid == "18":
            return True
        req = info_dict.get("requested_formats") or []
        if isinstance(req, list) and req:
            ids = [str(x.get("format_id") or "") for x in req if isinstance(x, dict)]
            if ids and all(i == "18" for i in ids):
                return True
        return False

    @staticmethod
    def _is_http_416(exc) -> bool:
        msg = str(exc).lower()
        return "416" in msg and ("range" in msg or "satisfiable" in msg)

    @staticmethod
    def _is_format_unavailable(exc) -> bool:
        """yt-dlp: selector không khớp bất kỳ format nào — đừng retry cùng height>=."""
        return "requested format is not available" in str(exc).lower()

    @classmethod
    def source_is_usable(cls, path: str) -> bool:
        """Source đủ lớn/dài, có video stream và giải mã được ở đầu/giữa/cuối."""
        if not path or not os.path.exists(path):
            return False
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size < MIN_SOURCE_BYTES:
            return False
        dur = cls.probe_duration_sec(path)
        if dur <= MIN_SOURCE_DURATION_SEC:
            return False
        width, height = cls.probe_video_size(path)
        if width <= 0 or height <= 0:
            return False
        return cls.video_decodes_at_checkpoints(path, dur)

    @classmethod
    def high_res_is_usable(cls, path: str) -> bool:
        """
        Skip-if-valid: file cũ đủ size/duration VÀ height >= 480.
        File 360p stale KHÔNG skip — thử tải HD lại. Download mới ra 360p thì chấp nhận ở download_high_res_video.
        """
        if not cls.source_is_usable(path):
            return False
        _w, h = cls.probe_video_size(path)
        if h <= INVALID_HIGH_MAX_HEIGHT:
            return False
        return h >= MIN_HIGH_SKIP_HEIGHT

    @classmethod
    def cleanup_ytdlp_resume_junk(cls, folder: str, log_fn=None) -> int:
        """
        Xóa leftover resume của yt-dlp trong job folder:
        *.part, *.ytdl, source_video.f399.mp4 / *.f*.m4a.
        Không đụng source_video.mp4 / source_video_low.mp4 (kể cả khi chưa validate).
        """
        info = log_fn or logger.info
        if not folder or not os.path.isdir(folder):
            return 0
        try:
            names = os.listdir(folder)
        except OSError:
            return 0
        removed = 0
        for name in names:
            if name in FINAL_VIDEO_NAMES:
                continue
            lower = name.lower()
            is_junk = (
                lower.endswith(".part")
                or lower.endswith(".ytdl")
                or lower.endswith(".not_hd.tmp.mp4")
                or ".part-frag" in lower
                or _YTDLP_FORMAT_LEFTOVER_RE.search(name) is not None
            )
            if not is_junk:
                continue
            path = os.path.join(folder, name)
            if not os.path.isfile(path):
                continue
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
        if removed:
            info(f"🧹 [NHÁNH 1] Đã xóa {removed} file dở (*.part / *.ytdl / *.f*.mp4) để tránh lỗi 416.")
        return removed

    @classmethod
    def _remove_invalid_final(cls, path: str, validator, log_fn=None) -> None:
        """Xóa final mp4 dở/hỏng để yt-dlp không gửi Range vào file lệch. Không xóa file đã valid."""
        if not path or not os.path.exists(path):
            return
        if validator(path):
            return
        info = log_fn or logger.info
        try:
            os.remove(path)
            info(f"🗑️ [NHÁNH 1] Xóa file không hợp lệ (dở/hỏng) trước khi tải lại: {os.path.basename(path)}")
        except OSError:
            pass

    @classmethod
    def _ytdlp_download_to(cls, url: str, dest_path: str, ydl_opts: dict) -> dict:
        """
        Hook tải video: Chỉ cập nhật duy nhất 1 dòng trạng thái ở dưới cùng, 
        giữ nguyên toàn bộ lịch sử log hệ thống phía trên.
        """
        import sys

        def my_hook(d):
            if d['status'] == 'downloading':
                p = d.get('_percent_str', '0.0%').strip()
                s = d.get('_speed_str', '0 B/s').strip()
                eta = d.get('_eta_str', 'Unknown').strip()
                
                msg = f"📥 [ĐANG TẢI 16 LUỒNG] Tiến trình: {p} | Tốc độ: {s} | Còn lại: {eta}"
                
                stdout_obj = getattr(sys, 'stdout', None)
                if stdout_obj and hasattr(stdout_obj, 'text_widget'):
                    w = stdout_obj.text_widget
                    try:
                        w.configure(state='normal')
                        
                        # Nếu đã đánh dấu vị trí dòng tải trước đó, chỉ xóa đúng dòng đó, không đụng log trên
                        if hasattr(cls, "_dl_line_index") and cls._dl_line_index:
                            try:
                                # Xóa từ vị trí dòng tải cũ đến hết
                                w.delete(cls._dl_line_index, "end-1c")
                            except Exception:
                                pass
                        else:
                            # Nếu chưa có, tạo dòng mới và lưu lại vị trí
                            w.insert('end', "\n")
                        
                        cls._dl_line_index = w.index("end-1c linestart")
                        w.insert('end', msg)
                        w.see('end')
                        w.configure(state='normal')
                        return
                    except Exception:
                        pass
                
                sys.stdout.write(f"\r{msg}   ")
                sys.stdout.flush()

            elif d['status'] == 'finished':
                if hasattr(cls, "_dl_line_index"):
                    delattr(cls, "_dl_line_index")
                print("\n✨ [Tải xong phân mảnh, đang tiến hành gộp file MP4...]")

        opts = cls.apply_cookies_to_opts(ydl_opts)
        opts['progress_hooks'] = [my_hook]
        opts['quiet'] = True
        opts['no_warnings'] = True

        with yt_dlp.YoutubeDL(opts) as ydl:
            info_dict = ydl.extract_info(url, download=True) or {}
            if not os.path.exists(dest_path):
                prepared = ydl.prepare_filename(info_dict)
                if prepared and os.path.exists(prepared) and os.path.abspath(prepared) != os.path.abspath(dest_path):
                    if os.path.exists(dest_path):
                        os.remove(dest_path)
                    os.replace(prepared, dest_path)
            return info_dict

    @classmethod
    def ytdlp_fetch(cls, url: str, dest_path: str, ydl_opts: dict, validator, skip_log: str, log_fn=None) -> dict:
        """
        Skip-if-valid trước. Dọn resume junk. Nếu 416: dọn lại, skip nếu file đã hợp lệ,
        không thì retry 1 lần với continuedl=False.
        """
        info = log_fn or logger.info
        folder = os.path.dirname(dest_path) or "."
        os.makedirs(folder, exist_ok=True)
        cls.cleanup_ytdlp_resume_junk(folder, log_fn=info)

        if validator(dest_path):
            info(skip_log)
            return {"duration": cls.probe_duration_sec(dest_path), "_skipped": True}

        cls._remove_invalid_final(dest_path, validator, log_fn=info)

        opts = dict(ydl_opts)
        try:
            info_dict = cls._ytdlp_download_to(url, dest_path, opts)
        except Exception as e:
            if not cls._is_http_416(e):
                raise
            info("⚠️ [NHÁNH 1] HTTP 416 (Requested range not satisfiable) — dọn file dở và xử lý lại.")
            cls.cleanup_ytdlp_resume_junk(folder, log_fn=info)
            if validator(dest_path):
                info(skip_log)
                return {"duration": cls.probe_duration_sec(dest_path), "_skipped": True}
            cls._remove_invalid_final(dest_path, validator, log_fn=info)
            retry_opts = dict(opts)
            retry_opts["continuedl"] = False
            retry_opts["nopart"] = True
            retry_opts["overwrites"] = True
            info("🔁 [NHÁNH 1] Tải lại từ đầu (continuedl=False) sau lỗi 416...")
            info_dict = cls._ytdlp_download_to(url, dest_path, retry_opts)

        if not info_dict.get("duration"):
            info_dict["duration"] = cls.probe_duration_sec(dest_path)
        info_dict["_skipped"] = False
        return info_dict

    @classmethod
    def _force_remove_file(cls, path: str, log_fn=None, reason: str = "") -> None:
        if not path or not os.path.exists(path):
            return
        info = log_fn or logger.info
        try:
            os.remove(path)
            extra = f" ({reason})" if reason else ""
            info(f"🗑️ [NHÁNH 1] Đã xóa {os.path.basename(path)}{extra}")
        except OSError:
            pass

    @classmethod
    def _log_high_result(cls, dest_path: str, info_dict: dict) -> tuple:
        """Gắn width/height/format vào info_dict; trả (w, h, format_label)."""
        w, h = cls.probe_video_size(dest_path)
        if (not w or not h) and info_dict:
            w = int(info_dict.get("width") or w or 0)
            h = int(info_dict.get("height") or h or 0)
        label = cls._chosen_format_label(info_dict)
        info_dict["_width"] = w
        info_dict["_height"] = h
        info_dict["_format_id"] = label
        return w, h, label

    @classmethod
    def download_video_and_lowres(cls, url: str, folder: str) -> tuple[str, str]:
        """Hàm alias tương thích ngược nếu có module gọi."""
        target = os.path.join(folder, "source_video.mp4")
        cls.download_high_res_video(url, target)
        return target, target

    @classmethod
    def download_high_res_video(cls, url: str, dest_path: str, ydl_opts: dict = None, log_fn=None) -> dict:
        """
        Tải source_video.mp4 — Cơ chế 3 lần thử (16 -> 8 -> 1 luồng) kèm log gọn gàng, trực quan.
        """
        info = log_fn or logger.info
        opts = dict(ydl_opts or {})
        folder = os.path.dirname(dest_path) or "."
        opts.setdefault("outtmpl", os.path.join(folder, "source_video.%(ext)s"))
        opts.setdefault("merge_output_format", "mp4")
        opts.setdefault("noplaylist", True)
        opts.setdefault("nocheckcertificate", True)
        opts.setdefault("nopart", True)
        opts.setdefault("headers", YT_HEADERS)
        
        # Chuẩn format 1080p
        opts["format"] = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
        
        if "format_sort" in opts:
            del opts["format_sort"]
        if "extractor_args" in opts:
            del opts["extractor_args"]

        if cls.high_res_is_usable(dest_path):
            info("⏭️ [NHÁNH 1] Đã có source_video.mp4 hợp lệ (HD) — bỏ qua tải lại.")
            info_dict = {
                "duration": cls.probe_duration_sec(dest_path),
                "_skipped": True,
            }
            w, h, fmt_label = cls._log_high_result(dest_path, info_dict)
            info_dict["_high_ok"] = True
            return info_dict

        info_dict = {}
        
        # --- CƠ CHẾ 3 LẦN THỬ KÈM LOG HIỂN THỊ SỐ LUỒNG RÕ RÀNG ---
        attempts = [
            (16, "🚀 [TẢI HD] Đang khởi chạy 16 luồng song song (Tốc độ tối đa)..."),
            (8,  "🔄 [TẢI HD] Thử lại với 8 luồng song song (Cân bằng băng thông)..."),
            (1,  "🛡️ [TẢI HD] Thử lại với 1 luồng đơn (Chế độ an toàn tuyệt đối)...")
        ]

        success = False
        for threads, log_msg in attempts:
            info(log_msg)
            try:
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                
                attempt_opts = dict(opts)
                if threads > 1:
                    attempt_opts["concurrent_fragment_downloads"] = threads
                    # Giảm bớt log rác bằng cách tắt quiet=False nhưng cấu hình progress_hooks hoặc dùng chế độ chuẩn
                    attempt_opts["quiet"] = False
                    attempt_opts["no_warnings"] = True
                else:
                    attempt_opts.pop("concurrent_fragment_downloads", None)
                    attempt_opts["quiet"] = False
                
                attempt_opts["retries"] = 5
                attempt_opts["fragment_retries"] = 5

                info_dict = cls._ytdlp_download_to(url, dest_path, attempt_opts)
                
                if cls.source_is_usable(dest_path):
                    success = True
                    info(f"✅ Tải thành công mỹ mãn với {threads} luồng!")
                    break
            except Exception as e:
                info(f"⚠️ Thất bại ở cấu hình {threads} luồng (Đang chuyển cấp dự phòng...): {e}")
                if "Sign in to confirm your age" in str(e):
                    info("💡 [GỢI Ý] Video này bị giới hạn độ tuổi (18+) của YouTube. Vui lòng bấm nút [🍪 Cookie YouTube] trên thanh tiêu đề để nạp file cookies.txt!")

        if not success:
            raise RuntimeError("❌ Cả 3 lần thử tải video (16 -> 8 -> 1 luồng) đều không thành công!")

        if not info_dict.get("duration"):
            info_dict["duration"] = cls.probe_duration_sec(dest_path)
        info_dict["_skipped"] = False

        w, h, fmt_label = cls._log_high_result(dest_path, info_dict)
        info(f"📊 [NHÁNH 1] Hoàn tất — Định dạng: {fmt_label} → {w}×{h}")
        info_dict["_high_ok"] = True
        return info_dict

    @classmethod
    def low_res_is_usable(cls, path: str, expected_duration: float = 0.0) -> bool:
        """Từ chối stub YouTube (vd. format 18 ~572KB cho video 20 phút)."""
        if not path or not os.path.exists(path):
            return False
        try:
            size = os.path.getsize(path)
        except OSError:
            return False
        if size < 200 * 1024:
            return False
        exp = float(expected_duration or 0.0)
        dur = cls.probe_duration_sec(path)
        if size < 1024 * 1024:
            if exp >= 45 or dur >= 45:
                return False
            if exp <= 0 and dur <= 5:
                return False
        if exp >= 60 and size < 2 * 1024 * 1024:
            return False
        if exp >= 180 and size < 4 * 1024 * 1024:
            return False
        if dur >= 30 and (size / max(dur, 1.0)) < 20_000:
            return False
        if exp >= 30 and (size / max(exp, 1.0)) < 15_000:
            return False
        if exp >= 20 and dur > 0 and dur < exp * 0.5:
            return False
        if exp >= 20 and dur <= 0.5:
            return False
        width, height = cls.probe_video_size(path)
        if width <= 0 or height <= 0:
            return False
        return cls.video_decodes_at_checkpoints(path, dur)

    @classmethod
    def remux_faststart(cls, path: str) -> None:
        """Đưa moov lên đầu file để OpenCV seek không spam 'partial file'."""
        if not path or not os.path.exists(path):
            return
        tmp = path + ".faststart.mp4"
        try:
            cls._ff_run(
                [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", path, "-c", "copy", "-movflags", "+faststart", tmp,
                ],
                check=True,
            )
            if os.path.exists(tmp) and os.path.getsize(tmp) > 0:
                os.replace(tmp, path)
        except Exception as e:
            logger.warning(f"⚠️ Remux +faststart bỏ qua: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception:
                    pass

    @classmethod
    def render_low_res_360p(cls, source_path: str, dest_path: str) -> str:
        """Transcode 360p đủ duration từ source_video.mp4 (không phụ thuộc YouTube 360p)."""
        dest_dir = os.path.dirname(dest_path)
        if dest_dir:
            os.makedirs(dest_dir, exist_ok=True)
        tmp = dest_path + ".tmp.mp4"
        cls._ff_run(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-i", source_path,
                "-vf", "scale=-2:360",
                "-r", "20",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
                "-an",
                "-movflags", "+faststart",
                tmp,
            ],
            check=True,
        )
        if not os.path.exists(tmp) or os.path.getsize(tmp) <= 0:
            raise RuntimeError("FFmpeg render 360p ra file rỗng.")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except Exception:
                pass
        os.replace(tmp, dest_path)
        return dest_path

    @classmethod
    def create_gemini_preview(cls, source_low: str, dest_path: str,
                              fps: float = 2.0) -> str:
        """Create a full-duration 240p/2 FPS proxy for Gemini only."""
        preview_started = time.perf_counter()
        if not source_low or not os.path.isfile(source_low):
            raise FileNotFoundError(f"Thiếu proxy nguồn để tạo Gemini preview: {source_low}")
        source_duration = cls.probe_duration_sec(source_low)
        if source_duration <= 0:
            raise RuntimeError("Không đọc được thời lượng proxy trước khi tạo Gemini preview.")
        fps = max(2.0, min(10.0, float(fps or 6.0)))
        if os.path.isfile(dest_path):
            preview_duration = cls.probe_duration_sec(dest_path)
            preview_capture = cv2.VideoCapture(dest_path)
            preview_fps = float(preview_capture.get(cv2.CAP_PROP_FPS) or 0.0)
            preview_height = int(preview_capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
            preview_capture.release()
            try:
                fresh = os.path.getmtime(dest_path) >= os.path.getmtime(source_low)
            except OSError:
                fresh = False
            if (fresh and abs(preview_duration - source_duration) <= 2.0
                    and abs(preview_fps - fps) <= 0.25 and preview_height == 240):
                logger.info("⏭️ [GEMINI PREVIEW] Đã có bản 240p/%.0f FPS hợp lệ.", fps)
                logger.info("⏱️ [CHI TIẾT] 🎞️ Tạo Gemini preview: %.2fs (dùng cache)",
                            time.perf_counter() - preview_started)
                return dest_path

        os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
        temporary = dest_path + ".tmp.mp4"
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass
        logger.info("⚡ [GEMINI PREVIEW] Tạo bản 240p/%.0f FPS, giữ toàn bộ video/timestamp...", fps)
        result = cls._ff_run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", source_low,
            "-vf", f"fps={fps:g},scale=-2:240",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
            "-g", str(max(12, int(round(fps * 2)))),
            "-c:a", "aac", "-ac", "1", "-b:a", "32k", "-ar", "16000",
            "-movflags", "+faststart", temporary,
        ], check=False, capture=True)
        preview_duration = cls.probe_duration_sec(temporary)
        if result.returncode != 0 or abs(preview_duration - source_duration) > 2.0:
            detail = ((result.stderr or "") + "\n" + (result.stdout or "")).strip()
            raise RuntimeError(f"Tạo Gemini preview thất bại: {detail[-1200:]}")
        os.replace(temporary, dest_path)
        size_mb = os.path.getsize(dest_path) / (1024 * 1024)
        logger.info("✅ [GEMINI PREVIEW] 240p | %.1f MB | %.0f FPS | %.1fs.",
                    size_mb, fps, preview_duration)
        logger.info("⏱️ [CHI TIẾT] 🎞️ Tạo Gemini preview: %.2fs",
                    time.perf_counter() - preview_started)
        return dest_path

    @classmethod
    def extract_pcm_wav(cls, video_path: str, audio_path: str, log_fn=None) -> str:
        """
        Trích PCM WAV cho Whisper. Chỉ chạy khi source_video.mp4 tồn tại và ffprobe OK.
        Nếu ffmpeg fail: chạy lại không ẩn log, in stderr — không để lại exit 4294967294 bí ẩn.
        """
        info = log_fn or logger.info
        if not video_path or not os.path.exists(video_path):
            raise FileNotFoundError(
                f"❌ Không tìm thấy source_video.mp4 để trích audio: {video_path}"
            )
        dur = cls.probe_duration_sec(video_path)
        w, h = cls.probe_video_size(video_path)
        if not cls.source_is_usable(video_path) or dur <= 0:
            raise RuntimeError(
                f"❌ source_video.mp4 không dùng được trước khi extract PCM "
                f"({w}×{h}, {dur:.1f}s, path={video_path})"
            )
        if w <= 0 or h <= 0:
            raise RuntimeError(
                f"❌ ffprobe không đọc được video stream ({w}×{h}) — bỏ qua extract PCM: {video_path}"
            )

        info("⏳ Đang dùng FFmpeg trích xuất file Audio WAV riêng biệt...")
        cmd_quiet = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            audio_path,
        ]
        res = cls._ff_run(cmd_quiet, check=False, capture=True)
        if res.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            info(f"✅ Trích xuất audio riêng thành công tại: {audio_path}")
            return audio_path

        quiet_err = ((res.stderr or "") + (res.stdout or "")).strip()
        info(
            f"⚠️ [NHÁNH 1] ffmpeg extract PCM lần 1 thất bại "
            f"(exit {res.returncode & 0xFFFFFFFF}) — chạy lại không ẩn stderr..."
        )
        if quiet_err:
            info(quiet_err)

        cmd_verbose = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            audio_path,
        ]
        res2 = cls._ff_run(cmd_verbose, check=False, capture=True)
        verbose_err = ((res2.stderr or "") + (res2.stdout or "")).strip()
        if verbose_err:
            info(verbose_err)
        if res2.returncode == 0 and os.path.exists(audio_path) and os.path.getsize(audio_path) > 0:
            info(f"✅ Trích xuất audio riêng thành công tại: {audio_path}")
            return audio_path

        raise RuntimeError(
            f"❌ ffmpeg extract audio thất bại (exit {res2.returncode & 0xFFFFFFFF}). "
            f"source={video_path} ({w}×{h}, {dur:.1f}s).\n{verbose_err or quiet_err or '(không có stderr)'}"
        )

    extract_audio = extract_pcm_wav

    @classmethod
    def download_low_res_video(
        cls,
        url: str,
        dest_path: str,
        expected_duration: float = 0.0,
        source_video: str = None,
        log_fn=None,
    ) -> str:
        """
        Tải 360p chuẩn H.264 — Tích hợp cơ chế 3 lần thử thông minh (16 -> 8 -> 1 luồng) 
        giống hệt nhánh HD, không bị dính lỗi AV1.
        """
        info = log_fn or logger.info
        folder = os.path.dirname(dest_path) or "."
        os.makedirs(folder, exist_ok=True)
        cls.cleanup_ytdlp_resume_junk(folder, log_fn=info)

        def _ok(path=None):
            return cls.low_res_is_usable(path or dest_path, expected_duration)

        if _ok():
            info("⏭️ [NHÁNH 1] Đã có source_video_low.mp4 hợp lệ — bỏ qua tải lại.")
            return dest_path

        cls._remove_invalid_final(dest_path, _ok, log_fn=info)

        info("⏳ Đang tải bản video low-res 360p đa luồng cho OpenCV/Timesub...")
        try:
            base_low_opts = {
                "outtmpl": os.path.join(folder, "source_video_low.%(ext)s"),
                "format": LOW_RES_FORMAT,
                "merge_output_format": "mp4",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "nocheckcertificate": True,
                "nopart": True,
                "headers": YT_HEADERS,
            }

            low_info = {}
            low_attempts = [
                (16, "🚀 [TẢI LOW] Đang khởi chạy 16 luồng song song (Tốc độ tối đa)..."),
                (8,  "🔄 [TẢI LOW] Thử lại với 8 luồng song song..."),
                (1,  "🛡️ [TẢI LOW] Thử lại với 1 luồng đơn an toàn...")
            ]

            success_low = False
            for threads, log_msg in low_attempts:
                info(log_msg)
                try:
                    try:
                        os.remove(dest_path)
                    except OSError:
                        pass

                    attempt_opts = dict(base_low_opts)
                    if threads > 1:
                        attempt_opts["concurrent_fragment_downloads"] = threads
                    else:
                        attempt_opts.pop("concurrent_fragment_downloads", None)

                    attempt_opts["retries"] = 5
                    attempt_opts["fragment_retries"] = 5

                    low_info = cls._ytdlp_download_to(url, dest_path, attempt_opts)
                    if _ok():
                        success_low = True
                        info(f"✅ Tải low-res thành công mỹ mãn với {threads} luồng!")
                        break
                except Exception as e:
                    info(f"⚠️ Low-res thất bại ở {threads} luồng: {e}")

            if success_low and _ok():
                cls.remux_faststart(dest_path)
                size_mb = os.path.getsize(dest_path) / (1024 * 1024)
                dur = cls.probe_duration_sec(dest_path)
                lw, lh = cls.probe_video_size(dest_path)
                info(
                    f"✅ Tải low-res 360p thành công ({lw}×{lh}, {size_mb:.1f} MB, {dur:.0f}s) tại: {dest_path}"
                )
                return dest_path
        except Exception as e:
            logger.warning(f"⚠️ Không tải được low-res qua yt-dlp ({e}). Đang chuyển sang render FFmpeg dự phòng...")

        if source_video and os.path.exists(source_video):
            try:
                cls.render_low_res_360p(source_video, dest_path)
                if _ok():
                    size_mb = os.path.getsize(dest_path) / (1024 * 1024)
                    info(f"✅ Đã render low-res 360p dự phòng ({size_mb:.1f} MB) tại: {dest_path}")
                    return dest_path
            except Exception as render_err:
                logger.warning(f"⚠️ Render 360p dự phòng thất bại: {render_err}")
        return dest_path if os.path.exists(dest_path) else ""

    @staticmethod
    def get_safe_video_folder(title: str) -> str:
        """Hàm tạo thư mục riêng biệt cho video lấy ~20 ký tự đầu của tiêu đề đã chuẩn hóa."""
        nfkd_form = unicodedata.normalize('NFKD', title)
        no_accents = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        clean_str = re.sub(r'[^a-zA-Z0-9\s]', '', no_accents).strip().lower()
        clean_str = re.sub(r'\s+', '_', clean_str)
        short_name = clean_str[:20].rstrip('_')
        if not short_name:
            short_name = "video_task"
        folder_path = os.path.join(DownloaderProcessor.TEMP_DIR, short_name)
        os.makedirs(folder_path, exist_ok=True)
        return folder_path

    @staticmethod
    def clean_source_title(title: str, uploader: str = "") -> str:
        """Làm sạch title metadata nhưng không viết lại/nén nội dung gốc."""
        title = re.sub(r"\s+", " ", str(title or "").replace("\r", " ").replace("\n", " ")).strip()
        title = re.sub(r"https?://\S+|www\.\S+", "", title, flags=re.IGNORECASE).strip()

        # Tên kênh thường bị chèn lặp sau dấu phân cách.
        uploader = re.sub(r"\s+", " ", str(uploader or "")).strip()
        if uploader:
            title = re.sub(
                rf"\s*(?:[-–—|]\s*){re.escape(uploader)}\s*$",
                "", title, flags=re.IGNORECASE,
            ).strip()

        # Bỏ cụm hashtag quảng bá ở đầu/cuối; hashtag nằm giữa câu vẫn giữ.
        hashtag = r"#[^\s#]+"
        title = re.sub(rf"^(?:{hashtag}\s*)+", "", title).strip()
        title = re.sub(rf"(?:\s*{hashtag})+\s*$", "", title).strip()
        title = re.sub(r"\s+", " ", title).strip(" -–—|•")
        return title or "Video"

    @classmethod
    def download_and_process(cls, url: str) -> dict:
        """
        Tải video gốc HD (DASH) rồi trích xuất WAV cho Whisper.
        Đảm bảo luôn có sẵn các tài nguyên trong thư mục riêng biệt của video.
        """
        logger.info(f"🚀 Bắt đầu quá trình tải video từ URL: {url}")
        os.makedirs(cls.TEMP_DIR, exist_ok=True)
        
        # 1. Trích xuất metadata trước để lấy tiêu đề đặt tên thư mục riêng
        try:
            info_meta = cls.get_video_info(url)
            video_title = cls.clean_source_title(
                info_meta.get("title", "Unknown Title"), info_meta.get("uploader", "")
            )
        except Exception:
            video_title = "video_task"

        # TẠO THƯ MỤC ĐỘC LẬP CHO VIDEO NÀY
        specific_folder = cls.get_safe_video_folder(video_title)
        logger.info(f"📁 Thư mục lưu trữ riêng cho video: {specific_folder}")

        # --- [BỔ SUNG THEO YÊU CẦU]: LƯU LẠI TIÊU ĐỀ GỐC CHUẨN XÁC VÀO FILE ---
        try:
            title_file_path = os.path.join(specific_folder, "original_title.txt")
            with open(title_file_path, "w", encoding="utf-8") as f_title:
                f_title.write(video_title)
            logger.info(f"📝 Đã lưu tiêu đề gốc vào: {title_file_path}")
        except Exception as e:
            logger.warning(f"⚠️ Không thể lưu file tiêu đề gốc: {e}")
        # ------------------------------------------------------------------

        output_template = os.path.join(specific_folder, "source_video.%(ext)s")
        video_filename = os.path.join(specific_folder, "source_video.mp4")
        
        ydl_opts = {
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
            'noplaylist': True,
            'quiet': False,
            'no_warnings': True,
            'nocheckcertificate': True,
            'nopart': True,
            'retries': 10,
            'fragment_retries': 10,
            'concurrent_fragment_downloads': 16,
            'buffersize': 16384,
            'headers': YT_HEADERS,
        }
        
        try:
            info_dict = cls.download_high_res_video(url, video_filename, ydl_opts=ydl_opts)
            if not info_dict.get("_skipped") and not info_dict.get("_high_ok"):
                logger.warning(
                    "⚠️ Video gốc không tải được file dùng được — xem log format/độ phân giải phía trên."
                )

            # --- TẢI BẢN LOW-RES 360p (DASH đủ duration; stub format 18 → transcode từ bản gốc) ---
            low_res_path = os.path.join(specific_folder, "source_video_low.mp4")
            expected_dur = float(info_dict.get("duration") or cls.probe_duration_sec(video_filename) or 0)
            cls.download_low_res_video(
                url, low_res_path, expected_duration=expected_dur, source_video=video_filename
            )

            audio_path = os.path.join(specific_folder, "extracted_audio.wav")
            cls.extract_pcm_wav(video_filename, audio_path)
            
            return {
                "video_path": video_filename,
                "audio_path": audio_path,
                "title": cls.clean_source_title(
                    info_dict.get("title", video_title), info_dict.get("uploader", "")
                ),
                "duration": info_dict.get("duration", 0),
                "specific_dir": specific_folder
            }
            
        except Exception as e:
            logger.error(f"❌ Lỗi trong quá trình download/xử lý video: {e}")
            raise e

    @classmethod
    def get_spike_score(cls, video_path: str, t: float, duration: float) -> tuple:
        """
        Đo Motion Spike và Audio Spike có kích hoạt tăng tốc phần cứng GPU (CUDA) 
        giúp xử lý giải mã video siêu tốc, không tốn tài nguyên CPU.
        """
        try:
            # 1. Đo chuyển động khung hình bằng FFmpeg với GPU Acceleration (NVDEC/CUDA)
            cmd_motion = [
                "ffmpeg", "-y",
                "-ss", str(t), "-i", video_path, "-t", str(duration),
                "-vf", "select='gt(scene,0.12)',showinfo", "-f", "null", "-"
            ]
            try:
                from hardware_manager import get_hardware_profile
                if get_hardware_profile().get("encoder") == "h264_nvenc":
                    cmd_motion[2:2] = ["-hwaccel", "cuda"]
            except Exception:
                pass
            res_motion = subprocess.run(cmd_motion, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, creationflags=0x08000000)
            scene_count = res_motion.stderr.count("n:")
            motion_score = float(scene_count * 8.0)

            # 2. Đo năng lượng âm thanh thực tế (Audio Spike / Volume)
            cmd_audio = [
                "ffmpeg", "-y",
                "-ss", str(t), "-i", video_path, "-t", str(duration),
                "-af", "volumedetect", "-f", "null", "-"
            ]
            res_audio = subprocess.run(cmd_audio, stderr=subprocess.PIPE, stdout=subprocess.PIPE, text=True, creationflags=0x08000000)
            
            max_vol = -50.0
            mean_vol = -40.0
            for line in res_audio.stderr.split('\n'):
                if "max_volume:" in line:
                    try: 
                        max_vol = float(line.split("max_volume:")[1].split("dB")[0].strip())
                    except: 
                        pass
                if "mean_volume:" in line:
                    try: 
                        mean_vol = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                    except: 
                        pass

            audio_spike = max(0.0, max_vol - mean_vol)
            audio_loudness_score = (max_vol + 30) * 2.5

            return motion_score, audio_spike, audio_loudness_score
        except Exception:
            return 0.0, 0.0, 0.0

    @classmethod
    def find_best_hook_timestamp(cls, video_path: str, video_duration: float, hook_duration: float = 9.0) -> tuple:
        """
        Môtíp chấm điểm chuyên sâu kết hợp Ranh giới Cảnh (Scene-Aware Boundary):
        - Đo độ rung lắc máy quay và hỗn loạn vật lộn qua phương sai ma trận lệch khung hình (Variance of Difference).
        - Ghi nhận đầy đủ các mốc có nhát cắt cảnh (Scene Cut).
        - Tự động co ngắn thời lượng (nếu xuất hiện chuyển cảnh giữa chừng) để giữ trọn vẹn một cảnh quay duy nhất.
        """
        logger.info(f"🎯 [SHAKE & SCENE MATRIX] Đang quét phân cảnh xô xát/rung lắc và phân tích ranh giới cảnh...")
        
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.error("❌ Không thể mở video để quét!")
            return float(video_duration * 0.15), float(hook_duration)

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        
        # Quét vùng an toàn thân bài (15% đến 80% video)
        scan_start = video_duration * 0.15
        scan_end = video_duration * 0.80
        
        timestamps = []
        wrestle_scores = []
        scene_cuts = []  # Lưu các mốc thời gian xuất hiện nhát chuyển cảnh đột ngột
        
        step_seconds = 6.0  # Quét chi tiết từng giây
        current_time = scan_start
        
        cap.set(cv2.CAP_PROP_POS_MSEC, current_time * 1000.0)
        ret, prev_frame = cap.read()
        if not ret:
            cap.release()
            return float(video_duration * 0.15), float(hook_duration)
            
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        prev_gray = cv2.resize(prev_gray, (320, 180))  # Resize tối ưu tốc độ tính toán
        
        while current_time < scan_end:
            current_time += step_seconds
            cap.set(cv2.CAP_PROP_POS_MSEC, current_time * 1000.0)
            ret, frame = cap.read()
            if not ret:
                break
                
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            gray = cv2.resize(gray, (320, 180))
            
            # 1. Tính độ lệch ma trận khung hình
            diff = cv2.absdiff(prev_gray, gray)
            
            # 2. Đo độ biến thiên cục bộ (Variance)
            # Chuyển cảnh thông thường -> sai số đồng đều (variance thấp).
            # Xô xát, vật lộn, máy quay rung lắc -> sai số hỗn loạn (variance cực cao).
            _, var = cv2.meanStdDev(diff)
            wrestle_intensity = float(var[0][0])
            
            # Nếu biến thiên vượt ngưỡng cực đại -> Xác nhận có nhát chuyển cảnh đột ngột
            if wrestle_intensity > 45.0:
                scene_cuts.append(current_time)
            
            # Hiển thị trực quan từng mốc thời gian
            bar = "█" * int(max(0, wrestle_intensity / 3))
            logger.info(f"   ⏱️ Mốc {int(current_time):04d}s | MotionIntensity: {wrestle_intensity:4.1f} | {bar}")
            
            timestamps.append(current_time)
            wrestle_scores.append(wrestle_intensity)
            prev_gray = gray
            
        cap.release()
        
        if not timestamps:
            return float(video_duration * 0.15), float(hook_duration)

        timestamps = np.array(timestamps)
        wrestle_scores = np.array(wrestle_scores)
        
        # Tìm mốc bắt đầu có điểm số hỗn loạn/vật lộn cao nhất
        best_idx = np.argmax(wrestle_scores)
        start_time = timestamps[best_idx]
        
        # Tôn trọng ranh giới cảnh: Kiểm tra xem trong khoảng thời lượng dự kiến có bị dính cảnh mới không
        end_target = start_time + hook_duration
        actual_duration = hook_duration
        
        for cut_time in scene_cuts:
            if start_time < cut_time < end_target:
                # Nếu có nhát chuyển cảnh ở giây thứ 7 thay vì 9 -> Co ngắn về đúng 7 giây
                actual_duration = cut_time - start_time
                if actual_duration < 4.0:  # Đảm bảo thời lượng tối thiểu không quá ngắn
                    actual_duration = hook_duration
                break
        
        logger.info(f"🏆 [WRESTLE WINNER] Bắt đầu từ giây: {start_time:.1f}s | Thời lượng thực tế chuẩn ranh giới cảnh: {actual_duration:.1f}s")
        return float(start_time), float(actual_duration)

    @classmethod
    def get_hook_segment(cls, video_path: str, video_duration: float, duration: float = 9.0, manual_start_time: float = None) -> str:
        """
        Cắt đoạn Hook thủ công chuẩn xác 100% CÓ TIẾNG (Lấy trực tiếp từ source_video.mp4 đã được kiểm duyệt audio):
        - Lấy mốc start_time từ GUI.
        - Cắt đồng thời hình (0:v:0) và tiếng gốc (0:a:0) trực tiếp từ source_video.mp4.
        """
        video_dir = os.path.dirname(video_path)
        hook_output = os.path.join(video_dir, "hook_segment.mp4")

        # 0 giây là lựa chọn tắt Hook trên GUI. Tuyệt đối không truyền `-t 0`
        # cho FFmpeg vì một số demuxer coi giá trị này như không giới hạn và
        # xuất nguyên video nguồn thành hook.
        if float(duration or 0.0) <= 0.0:
            logger.info("⏭️ [HOOK TẮT] Thời lượng Hook = 0s — bỏ qua hoàn toàn bước cắt Hook.")
            return ""
        
        start_time = manual_start_time if manual_start_time is not None else (video_duration * 0.20)
        
        logger.info(f"🎬 [MANUAL HOOK] Đã chốt mốc thời gian từ giây {start_time:.1f} đến {start_time + duration:.1f}.")
        logger.info("⏳ Đang tiến hành cắt trực tiếp hình và tiếng từ source_video.mp4...")
        
        try:
            # Lệnh FFmpeg giữ nguyên bản tỷ lệ gốc của video (không ép đen 2 đầu ở bước này)
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", str(start_time), "-t", str(duration), "-i", video_path,
                "-map", "0:v:0",
                "-map", "0:a:0",
                "-c:v", "libx264", 
                "-preset", "medium",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                # Giữ nguyên khung hình gốc, chỉ chuẩn hóa chẵn pixel để tránh lỗi codec
                "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:a", "aac",
                "-b:a", "192k",
                "-ac", "2",
                "-ar", "44100",
                "-shortest",
                hook_output
            ], check=True, creationflags=0x08000000)
            
            if os.path.exists(hook_output) and os.path.getsize(hook_output) > 0:
                logger.info(f"✅ [SUCCESS] Xuất thành công file Hook CÓ ĐẦY ĐỦ ÂM THANH tại: {hook_output}")
                return hook_output
            else:
                raise Exception("File hook_segment.mp4 tạo ra bị rỗng.")
                
        except Exception as e:
            logger.error(f"❌ [ERROR] Lỗi khi cắt Hook: {e}")
            raise e
