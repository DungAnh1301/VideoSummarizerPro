"""
Editor dành riêng cho Path B (Custom Hook AI).

Tách hẳn khỏi editor_processor.py để Path A (hook YouTube native) không bị đụng.
- Hook AI: scale/pad 9:16, chỉnh màu, audio hook (nếu có), title. KHÔNG zoom / speed / blur / crop.
  KHÔNG TTS, KHÔNG burn sub trên đoạn hook.
- B-roll: pipeline đầy đủ (crop, blur nền, zoom, speed, màu, title, sub + giọng TTS).
- Sub Path B: bốc từ file giọng (đã atempo nếu có speed), rồi shift timestamp += hook_duration
  để khớp timeline video cuối (hook | B-roll). Cue đầu phải >= duration hook đã render.
"""
import os
import re
import subprocess
import time

from editor_processor import EditorProcessor
from hardware_manager import run_ffmpeg_auto

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("EditorAI")

CREATE_NO_WINDOW = 0x08000000
VIDEO_EXTS = (".mp4", ".mov", ".avi", ".mkv", ".webm")
# Khớp _build_audio: [hook][silence 1s][voice]. Offset sub Path B = hook_duration
# (không cộng silence) để B-roll giữ nhịp đã oke; cue đầu >= hết hook.
HOOK_TO_VOICE_SILENCE_SEC = 1.0
SRT_ARROW_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)


class EditorAI:
    """Hậu kỳ Path B: video hook AI + B-roll, mỗi phần một bộ filter riêng."""

    OUTPUT_W = 1080
    OUTPUT_H = 1920
    FPS = 30

    # --- Probe / resolve -------------------------------------------------

    @staticmethod
    def resolve_hook_source(path: str) -> str:
        """Nhận file video hoặc thư mục; nếu là thư mục thì lấy video mới nhất."""
        path = (path or "").strip().strip('"')
        if not path:
            return ""
        if os.path.isdir(path):
            videos = [
                os.path.join(path, name)
                for name in os.listdir(path)
                if name.lower().endswith(VIDEO_EXTS)
            ]
            if not videos:
                return ""
            return max(videos, key=os.path.getmtime)
        if os.path.isfile(path):
            return path
        return ""

    @staticmethod
    def probe_duration(video_path: str) -> float:
        """Đo duration thật (format, fallback stream) — tránh xfade/audio = 0."""
        if not video_path or not os.path.exists(video_path):
            return 0.0

        def _run_probe(entries: str) -> float:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", entries,
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ]
            res = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                creationflags=CREATE_NO_WINDOW,
            )
            raw = (res.stdout or "").strip().splitlines()
            for line in raw:
                line = line.strip()
                if not line or line.lower() in ("n/a", "nan"):
                    continue
                try:
                    val = float(line)
                    if val > 0.05:
                        return val
                except ValueError:
                    continue
            return 0.0

        duration = _run_probe("format=duration")
        if duration <= 0.05:
            duration = _run_probe("stream=duration")
        return duration

    @staticmethod
    def _has_audio(video_path: str) -> bool:
        cmd = [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0", video_path,
        ]
        res = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        return "audio" in (res.stdout or "").lower()

    @staticmethod
    def _run_ffmpeg(cmd: list, label: str = "ffmpeg") -> None:
        cmd = list(cmd)
        if cmd and "ffmpeg" in os.path.basename(str(cmd[0])).lower() and "-loglevel" not in cmd:
            cmd[1:1] = ["-hide_banner", "-loglevel", "error"]
        logger.info(f"▶️ [{label}] {' '.join(str(c) for c in cmd[:8])} ...")
        if "-c:v" in cmd:
            run_ffmpeg_auto(
                cmd, label=label, logger=logger, check=True,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            subprocess.run(cmd, check=True, creationflags=CREATE_NO_WINDOW)

    # --- Filter builders (dùng cho render + unit test) -------------------

    @classmethod
    def _color_chain(cls, options: dict, allow_blur_fx: bool = False) -> str:
        """Chuỗi chỉnh màu CapCut. Hook: không gblur/vignette. B-roll dùng graph cũ."""
        temp_val = options.get("temperature", 0)
        tint_val = options.get("tint", 0)
        sat_val = options.get("saturation", 0)
        exposure_val = options.get("exposure", 0)
        contrast_val = options.get("contrast", 0)
        highlights_val = options.get("highlights", 0)
        shadows_val = options.get("shadows", 0)
        whites_val = options.get("whites", 0)
        blacks_val = options.get("blacks", 0)
        brilliance_val = options.get("brilliance", 0)
        sharpen_val = options.get("sharpen", 0)
        clarity_val = options.get("clarity", 0)
        grain_val = options.get("grain", 0)
        blur_val = options.get("blur", 0)
        vignette_val = options.get("vignette", 0)

        brightness = exposure_val / 100.0
        contrast = max(0.0, 1.0 + (contrast_val / 40.0))
        saturation = max(0.0, 1.0 + (sat_val / 25.0))
        r_mod = temp_val / 80.0
        b_mod = -temp_val / 80.0
        g_mod = tint_val / 80.0

        color_filters = f"eq=contrast={contrast}:brightness={brightness}:saturation={saturation}"

        if temp_val != 0 or tint_val != 0:
            color_filters += f",colorbalance=rh={r_mod}:bh={b_mod}:gh={g_mod}"

        if highlights_val != 0 or shadows_val != 0:
            h_shift = highlights_val / 200.0
            sh_shift = shadows_val / 200.0
            p1_x, p1_y = 0.25, max(0.01, 0.25 + sh_shift)
            p2_x, p2_y = 0.75, min(0.99, 0.75 + h_shift)
            color_filters += f",curves=all='0/0 {p1_x:.2f}/{p1_y:.2f} {p2_x:.2f}/{p2_y:.2f} 1/1'"

        if whites_val != 0 or blacks_val != 0:
            w_mod = min(0.95, 1.0 - (whites_val / 300.0))
            b_blk = max(0.05, 0.0 + (blacks_val / 300.0))
            color_filters += f",curves=all='{b_blk:.2f}/0.0 0.5/0.5 {w_mod:.2f}/1.0'"

        if brilliance_val != 0:
            gamma_val = 1.0 + (brilliance_val / 100.0)
            color_filters += f",eq=gamma={max(0.1, gamma_val)}"

        if sharpen_val > 0:
            amount = (sharpen_val / 100.0) * 1.5
            color_filters += f",unsharp=5:5:{amount}:5:5:0.0"

        if clarity_val > 0:
            c_amount = (clarity_val / 100.0) * 1.2
            color_filters += f",unsharp=13:13:{c_amount}:13:13:0.0"

        if allow_blur_fx and blur_val > 0:
            b_radius = max(1, int(blur_val / 4))
            color_filters += f",gblur=sigma={b_radius}"

        if grain_val > 0:
            noise_v = int((grain_val / 100.0) * 40)
            color_filters += f",noise=alls={noise_v}:allf=t+u"

        if allow_blur_fx and vignette_val > 0:
            v_angle = 0.4 + ((100 - vignette_val) / 200.0)
            color_filters += f",vignette=PI/{v_angle}"

        return color_filters

    @classmethod
    def build_hook_filter(cls, options: dict, input_label: str = "[0:v]") -> str:
        """
        Filter Hook AI:
        - Scale/pad canvas 1080x1920 (9:16)
        - Chỉnh màu
        - KHÔNG zoom, speed, blur nền, blur mask, crop
        """
        color = cls._color_chain(options, allow_blur_fx=False)
        return (
            f"{input_label}fps={cls.FPS},"
            f"scale={cls.OUTPUT_W}:{cls.OUTPUT_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={cls.OUTPUT_W}:{cls.OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
            f"{color}[vout]"
        )

    @classmethod
    def build_broll_filter(cls, options: dict, input_label: str = "[0:v]") -> str:
        """Filter B-roll = pipeline Path A đầy đủ (crop/zoom/blur/màu)."""
        broll_opts = dict(options or {})
        broll_opts["use_custom_hook"] = False
        broll_opts["zoom_in"] = bool(broll_opts.get("zoom_in", False))
        return EditorProcessor._build_vf_filter(broll_opts, input_label=input_label)

    @staticmethod
    def hook_filter_forbids_fx(vf: str) -> list:
        """Trả về danh sách FX cấm nếu còn sót trong filter hook (phục vụ test)."""
        forbidden = []
        low = vf.lower()
        if "boxblur" in low:
            forbidden.append("boxblur")
        if "gblur" in low:
            forbidden.append("gblur")
        if "setpts" in low:
            forbidden.append("setpts")
        if "atempo" in low:
            forbidden.append("atempo")
        if re.search(r"scale=\d{4}:-2", vf):
            forbidden.append("zoom_scale")
        if "force_original_aspect_ratio=increase" in vf and "crop=1080:1920" in vf:
            forbidden.append("cover_crop_blur_bg")
        return forbidden

    # --- Sub / title helpers ---------------------------------------------

    @staticmethod
    def _format_srt_path(srt_path: str) -> str:
        abs_srt = os.path.abspath(srt_path).replace("\\", "/")
        if ":" in abs_srt:
            drive, path_part = abs_srt.split(":", 1)
            return f"{drive}\\:{path_part}"
        return abs_srt

    @staticmethod
    def _srt_time_to_seconds(time_str: str) -> float:
        """Parse HH:MM:SS,mmm hoặc HH:MM:SS.mmm → giây."""
        raw = (time_str or "").strip().replace(",", ".")
        parts = raw.split(":")
        if len(parts) != 3:
            return 0.0
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _seconds_to_srt_time(sec: float) -> str:
        """Giây → HH:MM:SS,mmm (SRT)."""
        if sec < 0:
            sec = 0.0
        total_ms = int(round(sec * 1000.0))
        h, rem = divmod(total_ms, 3600 * 1000)
        m, rem = divmod(rem, 60 * 1000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @classmethod
    def shift_srt_timestamps(cls, srt_path: str, offset_sec: float, out_path: str = None) -> str:
        """
        Dịch toàn bộ cue SRT sang phải `offset_sec` giây (Path B: offset = hook_duration).
        Cue đầu 00:00:00,00 + hook 5s → 00:00:05,00. Không đụng Path A.
        """
        if not srt_path or not os.path.exists(srt_path):
            return ""
        out_path = out_path or srt_path
        offset_sec = float(offset_sec or 0.0)
        with open(srt_path, "r", encoding="utf-8") as f_in:
            content = f_in.read()
        if offset_sec <= 0.001:
            if os.path.abspath(out_path) != os.path.abspath(srt_path):
                with open(out_path, "w", encoding="utf-8") as f_out:
                    f_out.write(content)
            return out_path

        def _repl(match) -> str:
            start = cls._srt_time_to_seconds(match.group(1)) + offset_sec
            end = cls._srt_time_to_seconds(match.group(2)) + offset_sec
            return f"{cls._seconds_to_srt_time(start)} --> {cls._seconds_to_srt_time(end)}"

        shifted = SRT_ARROW_RE.sub(_repl, content)
        with open(out_path, "w", encoding="utf-8") as f_out:
            f_out.write(shifted)
        return out_path

    @classmethod
    def scale_srt_timestamps(cls, srt_path: str, factor: float, out_path: str) -> str:
        """Co/giãn timeline SRT, dùng khi voice được áp dụng atempo."""
        if not srt_path or not os.path.exists(srt_path):
            return ""
        factor = max(0.001, float(factor or 1.0))
        with open(srt_path, "r", encoding="utf-8") as f_in:
            content = f_in.read()

        def _repl(match) -> str:
            start = cls._srt_time_to_seconds(match.group(1)) * factor
            end = cls._srt_time_to_seconds(match.group(2)) * factor
            return f"{cls._seconds_to_srt_time(start)} --> {cls._seconds_to_srt_time(end)}"

        with open(out_path, "w", encoding="utf-8") as f_out:
            f_out.write(SRT_ARROW_RE.sub(_repl, content))
        return out_path

    @classmethod
    def first_srt_cue_start(cls, srt_path: str) -> float:
        """Giây bắt đầu của cue SRT đầu tiên; -1 nếu không có."""
        if not srt_path or not os.path.exists(srt_path):
            return -1.0
        with open(srt_path, "r", encoding="utf-8") as f_in:
            match = SRT_ARROW_RE.search(f_in.read())
        if not match:
            return -1.0
        return cls._srt_time_to_seconds(match.group(1))

    @classmethod
    def align_srt_to_hook_timeline(cls, srt_path: str, hook_duration: float, specific_dir: str) -> str:
        """
        Path B: sub đang theo t=0 của file giọng TTS. Burn lên video cuối (hook|B-roll)
        thì phải cộng hook_duration — hook không sub, B-roll giữ khoảng cách cue (đã khớp miệng).
        """
        if not srt_path or not os.path.exists(srt_path):
            return ""
        hook_duration = max(0.0, float(hook_duration or 0.0))
        aligned = os.path.join(specific_dir, "transcript_sub_path_b.srt")
        first_before = cls.first_srt_cue_start(srt_path)
        cls.shift_srt_timestamps(srt_path, hook_duration, aligned)
        first_after = cls.first_srt_cue_start(aligned)
        logger.info(
            f"📝 [SUB PATH B] Shift SRT += {hook_duration:.3f}s "
            f"(cue đầu {cls._seconds_to_srt_time(max(0.0, first_before))} "
            f"→ {cls._seconds_to_srt_time(max(0.0, first_after))}). "
            "Hook không sub; sub+TTS bắt đầu từ B-roll."
        )
        if first_after >= 0 and first_after + 0.001 < hook_duration:
            logger.warning(
                f"⚠️ [SUB PATH B] Cue đầu ({first_after:.3f}s) vẫn < hook_duration "
                f"({hook_duration:.3f}s) — kiểm tra file SRT."
            )
        return aligned

    @classmethod
    def _build_sub_filter(cls, srt_path: str, options: dict) -> str:
        if not srt_path or not os.path.exists(srt_path):
            return ""
        sub_size = options.get("sub_size", 22)
        sub_outline = options.get("sub_outline", 3)
        sub_margin_v = int(options.get("sub_margin_v", 100))
        primary_color = options.get("sub_color", "&HFFFFFF&")
        outline_color = options.get("sub_outline_color", "&H000000&")
        formatted = cls._format_srt_path(srt_path)
        style = (
            f"FontName=Impact,FontSize={sub_size},PrimaryColour={primary_color},"
            f"OutlineColour={outline_color},BorderStyle=1,Outline={sub_outline},"
            f"Alignment=2,MarginL=75,MarginR=75,MarginV={sub_margin_v},WrapStyle=1"
        )
        return f"subtitles='{formatted}':force_style='{style}'"

    @classmethod
    def _title_lines(cls, specific_dir: str) -> tuple:
        return EditorProcessor.read_banner_title_lines(specific_dir)

    # --- Render steps ----------------------------------------------------

    @classmethod
    def _normalize_hook_canvas(cls, hook_src: str, out_path: str) -> None:
        """Scale/pad hook về 1080x1920, giữ audio gốc nếu có."""
        vf = (
            f"fps={cls.FPS},"
            f"scale={cls.OUTPUT_W}:{cls.OUTPUT_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={cls.OUTPUT_W}:{cls.OUTPUT_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
        )
        cmd = [
            "ffmpeg", "-y", "-i", hook_src,
            "-vf", vf,
            "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "17", "-pix_fmt", "yuv420p",
            "-r", str(cls.FPS),
        ]
        if cls._has_audio(hook_src):
            cmd += ["-c:a", "aac", "-b:a", "256k", "-ar", "44100", "-ac", "2"]
        else:
            cmd += ["-an"]
        cmd.append(out_path)
        cls._run_ffmpeg(cmd, "normalize-hook")

    @classmethod
    def _render_hook_visual(cls, hook_norm: str, banner_path: str, banner_x: int, banner_y: int,
                            options: dict, out_path: str) -> None:
        hook_vf = cls.build_hook_filter(options, "[0:v]")
        fc = f"{hook_vf};[vout][1:v]overlay={banner_x}:{banner_y}[outv]"
        cmd = [
            "ffmpeg", "-y", "-i", hook_norm, "-i", banner_path,
            "-filter_complex", fc,
            "-map", "[outv]", "-an",
            "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "16",
            "-pix_fmt", "yuv420p", "-r", str(cls.FPS),
            out_path,
        ]
        try:
            cls._run_ffmpeg(cmd, "render-hook")
        except subprocess.CalledProcessError:
            cmd[cmd.index("h264_nvenc")] = "libx264"
            if "-preset" in cmd:
                cmd[cmd.index("-preset") + 1] = "veryfast"
            cls._run_ffmpeg(cmd, "render-hook-cpu")

    @classmethod
    def _render_broll_visual(cls, broll_concat: str, banner_path: str, banner_x: int, banner_y: int,
                             options: dict, out_path: str) -> None:
        speed = float(options.get("speed", 1.0) or 1.0)
        broll_vf = cls.build_broll_filter(options, "[0:v]")
        if speed != 1.0:
            pts = 1.0 / speed
            fc = (
                f"{broll_vf};[vout][1:v]overlay={banner_x}:{banner_y}[v_banner];"
                f"[v_banner]setpts={pts}*PTS,fps={cls.FPS}[outv]"
            )
        else:
            fc = f"{broll_vf};[vout][1:v]overlay={banner_x}:{banner_y},fps={cls.FPS}[outv]"
        cmd = [
            "ffmpeg", "-y", "-i", broll_concat, "-i", banner_path,
            "-filter_complex", fc,
            "-map", "[outv]", "-an",
            "-c:v", "h264_nvenc", "-preset", "p3", "-cq", "16",
            "-pix_fmt", "yuv420p", "-r", str(cls.FPS),
            out_path,
        ]
        try:
            cls._run_ffmpeg(cmd, "render-broll")
        except subprocess.CalledProcessError:
            cmd[cmd.index("h264_nvenc")] = "libx264"
            if "-preset" in cmd:
                cmd[cmd.index("-preset") + 1] = "veryfast"
            cls._run_ffmpeg(cmd, "render-broll-cpu")

    @classmethod
    def _concat_hook_broll(cls, hook_v: str, broll_v: str, hook_duration: float, out_path: str) -> None:
        if hook_duration >= 0.8:
            offset = max(0.1, hook_duration - 0.5)
            fc = f"[0:v][1:v]xfade=transition=fadewhite:duration=0.5:offset={offset}[outv]"
            cmd = [
                "ffmpeg", "-y", "-i", hook_v, "-i", broll_v,
                "-filter_complex", fc, "-map", "[outv]",
                "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "17", "-pix_fmt", "yuv420p",
                "-r", str(cls.FPS), out_path,
            ]
            try:
                cls._run_ffmpeg(cmd, "xfade-hook-broll")
                return
            except subprocess.CalledProcessError:
                logger.warning("⚠️ xfade thất bại, fallback concat thẳng hook + B-roll.")
        concat_list = out_path + ".concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            f.write(f"file '{os.path.abspath(hook_v).replace(chr(92), '/')}'\n")
            f.write(f"file '{os.path.abspath(broll_v).replace(chr(92), '/')}'\n")
        cls._run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "h264_nvenc", "-preset", "p1", "-cq", "17", "-pix_fmt", "yuv420p",
            "-r", str(cls.FPS), out_path,
        ], "concat-hook-broll")

    @classmethod
    def _build_audio(cls, hook_src: str, hook_duration: float, voice_path: str,
                     options: dict, temp_dir: str, specific_dir: str) -> tuple:
        hook_boost = float(options.get("hook_audio_boost", options.get("audio_boost", 20.0)))
        voice_boost = float(options.get("audio_boost", 6.0))
        speed = float(options.get("speed", 1.0) or 1.0)

        hook_audio = os.path.join(temp_dir, "hook_audio_fixed.wav")
        if cls._has_audio(hook_src) and hook_duration > 0.05:
            cls._run_ffmpeg([
                "ffmpeg", "-y", "-i", hook_src, "-vn",
                "-t", f"{hook_duration:.4f}",
                "-ar", "44100", "-ac", "2",
                "-af", EditorProcessor.capcut_audio_filter(hook_boost),
                hook_audio,
            ], "hook-audio")
        else:
            logger.warning("⚠️ Hook AI không có audio — tạo silence đúng duration.")
            cls._run_ffmpeg([
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", f"{max(0.1, hook_duration):.4f}",
                hook_audio,
            ], "hook-silence")

        silence = os.path.join(temp_dir, "silence_1s.wav")
        cls._run_ffmpeg([
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", "anullsrc=r=44100:cl=stereo",
            "-t", f"{HOOK_TO_VOICE_SILENCE_SEC:.4f}",
            "-c:a", "pcm_s16le", silence,
        ], "hook-voice-silence")
        logger.info("🔇 [CHUYỂN CẢNH B] Giữ 1s im lặng giữa Hook và Voice; không dùng whoosh.")

        voice_wav = os.path.join(temp_dir, "voice_normalized.wav")
        voice_af = EditorProcessor.capcut_audio_filter(voice_boost)
        cls._run_ffmpeg([
            "ffmpeg", "-y", "-i", voice_path, "-ar", "44100", "-ac", "2",
            "-af", voice_af, voice_wav,
        ], "voice-audio")

        body_concat = os.path.join(temp_dir, "body_audio_concat.txt")
        body_raw = os.path.join(temp_dir, "body_audio_raw.wav")
        with open(body_concat, "w", encoding="utf-8") as f_body:
            f_body.write(f"file '{os.path.abspath(silence).replace(chr(92), '/')}'\n")
            f_body.write(f"file '{os.path.abspath(voice_wav).replace(chr(92), '/')}'\n")
        cls._run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", body_concat,
            "-c", "copy", body_raw,
        ], "body-audio-raw")
        body_sped = os.path.join(temp_dir, "body_audio_sped.wav")
        if abs(speed - 1.0) >= 0.01:
            tempo = speed
            tempo_filters = []
            remain = tempo
            while remain > 2.0:
                tempo_filters.append("atempo=2.0")
                remain /= 2.0
            while remain < 0.5:
                tempo_filters.append("atempo=0.5")
                remain /= 0.5
            tempo_filters.append(f"atempo={remain:.5f}")
            cls._run_ffmpeg([
                "ffmpeg", "-y", "-i", body_raw, "-af", ",".join(tempo_filters),
                body_sped,
            ], "body-audio-atempo")
        else:
            body_sped = body_raw

        concat_list = os.path.join(specific_dir, "audio_concat_ai.txt")
        with open(concat_list, "w", encoding="utf-8") as f_ac:
            f_ac.write(f"file '{os.path.abspath(hook_audio).replace(chr(92), '/')}'\n")
            f_ac.write(f"file '{os.path.abspath(body_sped).replace(chr(92), '/')}'\n")

        merged = os.path.join(specific_dir, "final_audio_ai.aac")
        cls._run_ffmpeg([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:a", "aac", "-b:a", "256k", merged,
        ], "merge-audio")
        return merged, body_sped

    @classmethod
    def build_final_video(cls, specific_dir: str, post_options: dict = None) -> str:
        start_time_render = time.time()
        logger.info("🎬 [EDITOR AI] Bắt đầu hậu kỳ Path B (Hook AI + B-roll)...")

        post_options = post_options or {}
        source_video = os.path.join(specific_dir, "source_video.mp4")
        voice_path = os.path.join(specific_dir, "voice_output.mp3")
        srt_path = os.path.join(specific_dir, "transcript_sub.srt")

        if not os.path.exists(source_video) or not os.path.exists(voice_path):
            raise FileNotFoundError("❌ Thiếu source_video.mp4 hoặc voice_output.mp3 trong thư mục video!")

        hook_src = cls.resolve_hook_source(
            post_options.get("custom_hook_path") or post_options.get("ai_video_path") or ""
        )
        if not hook_src:
            raise FileNotFoundError("❌ Chưa trỏ file/thư mục video Hook AI hợp lệ!")

        logger.info(f"🎬 [HOOK AI] Nguồn: {hook_src}")
        hook_duration_raw = cls.probe_duration(hook_src)
        logger.info(f"⏱️ [HOOK AI] Duration probe (file gốc): {hook_duration_raw:.3f}s")

        temp_clips_dir = os.path.join(specific_dir, "temp_clips")
        if os.path.exists(temp_clips_dir):
            for f in os.listdir(temp_clips_dir):
                try:
                    os.remove(os.path.join(temp_clips_dir, f))
                except Exception:
                    pass
        os.makedirs(temp_clips_dir, exist_ok=True)

        # V2 không render/normalize Hook thành file trung gian. Hook sẽ được
        # scale/pad/màu trực tiếp trong filter graph cuối.
        hook_duration = hook_duration_raw
        if hook_duration <= 0.05:
            raise RuntimeError("❌ Không probe được duration video Hook AI (tránh xfade/audio = 0).")
        logger.info(f"⏱️ [HOOK AI] Duration dùng cho timeline: {hook_duration:.3f}s")

        cmd_voice = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", voice_path,
        ]
        res_vd = subprocess.run(
            cmd_voice, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            creationflags=CREATE_NO_WINDOW,
        )
        total_voice_duration = float(res_vd.stdout.strip()) if (res_vd.stdout or "").strip() else 60.0
        speed = float(post_options.get("speed", 1.0) or 1.0)
        try:
            original_mix_level = max(0.0, min(
                1.0, float(post_options.get("original_audio_mix_percent", 50.0) or 0.0) / 100.0
            ))
        except (TypeError, ValueError):
            original_mix_level = 0.5
        mix_original_with_voice = bool(
            post_options.get("mix_original_audio_with_voice", False)
            and original_mix_level > 0.0 and cls._has_audio(source_video)
        )

        required_body_duration = (
            (total_voice_duration + HOOK_TO_VOICE_SILENCE_SEC) / max(speed, 0.001)
            + EditorProcessor.VISUAL_TAIL_AFTER_AUDIO_SEC
        )
        logger.info(
            f"⏱️ [B-ROLL] (giọng {total_voice_duration:.2f}s + "
            f"{HOOK_TO_VOICE_SILENCE_SEC:.0f}s) / {speed:.2f}x + "
            f"{EditorProcessor.VISUAL_TAIL_AFTER_AUDIO_SEC:.0f}s = {required_body_duration:.2f}s timeline."
        )

        opencv_target, total_source_duration = EditorProcessor.resolve_low_res_for_visual(
            specific_dir, source_video
        )
        if total_source_duration <= 1.0:
            total_source_duration = 60.0

        safe_start_limit = total_source_duration * 0.15
        safe_end_limit = total_source_duration * 0.95

        logger.info("🔍 Đang lập lịch cắt B-roll (theo chế độ đã chọn)...")
        clip_intervals = EditorProcessor._resolve_broll_intervals(
            opencv_target, safe_start_limit, safe_end_limit, required_body_duration,
            post_options=post_options, specific_dir=specific_dir,
        )

        if not clip_intervals:
            raise RuntimeError("❌ Scene map không tạo được timeline B-roll!")
        selected_source_duration = sum(
            max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
            for item in clip_intervals
        )
        required_source_duration = required_body_duration * max(speed, 0.001)
        if selected_source_duration + 0.05 < required_source_duration:
            logger.warning(
                f"⚠️ [FINAL VISUAL SAFETY] Path B chỉ đủ "
                f"{selected_source_duration:.2f}s/{required_source_duration:.2f}s nguồn; "
                "vẫn tiếp tục render thay vì dừng toàn bộ pipeline."
            )
        logger.info(
            f"🧾 [B-ROLL V2] Hook AI dùng {len(clip_intervals)} timeframe trực tiếp từ source gốc; "
            "không tạo clip B-roll trung gian."
        )

        line1, line2 = cls._title_lines(specific_dir)
        banner_path, banner_w, banner_h = EditorProcessor._create_dynamic_title_banner_custom(
            specific_dir, line1, line2, post_options
        )
        banner_x = int((cls.OUTPUT_W - banner_w) / 2)
        banner_y = int(post_options.get("title_y_pos", 260))

        logger.info("🎵 Ghép audio: Hook (boost, không speed) + 1s silence + Voice AI...")
        final_audio, voice_wav = cls._build_audio(
            hook_src, hook_duration, voice_path, post_options, temp_clips_dir, specific_dir
        )

        audio_dur = cls.probe_duration(final_audio)
        logger.info(f"⏱️ [PATH B] Audio timeline cuối: {audio_dur:.3f}s")

        # Whisper trên (1s + giọng) đã atempo; chỉ cộng hook (hook không speed).
        exact_srt = os.path.join(specific_dir, "voice_script_exact.srt")
        if os.path.exists(exact_srt):
            speed = float(post_options.get("speed", 1.0) or 1.0)
            scaled_exact = os.path.join(specific_dir, "voice_script_exact_scaled.srt")
            raw_srt = cls.scale_srt_timestamps(exact_srt, 1.0 / max(speed, 0.01), scaled_exact)
            total_sub_offset = hook_duration + (HOOK_TO_VOICE_SILENCE_SEC / max(speed, 0.01))
            logger.info("📝 [SUB EXACT] Path B dùng đúng nội dung script TTS, không dùng transcript nguồn.")
        else:
            raise FileNotFoundError("Thiếu voice_script_exact.srt; dừng render để tránh nhúng sai subtitle.")
        used_srt = cls.align_srt_to_hook_timeline(raw_srt, total_sub_offset, specific_dir)
        first_cue = cls.first_srt_cue_start(used_srt)
        if first_cue >= 0:
            logger.info(
                f"✅ [SUB PATH B] Cue đầu = {first_cue:.3f}s, hook = {hook_duration:.3f}s "
                f"(hook không sub: {first_cue >= hook_duration - 0.001})."
            )
        sub_filter = cls._build_sub_filter(used_srt, post_options)

        final_output_path = os.path.join(specific_dir, "final_video.mp4")
        logger.info("⏳ Đóng gói final: visual (hook+B-roll) + audio + sub...")
        
        # Tính chính xác thời lượng tối đa = duration của audio + đúng 1 giây đuôi an toàn
        audio_duration_final = cls.probe_duration(final_audio)
        exact_max_duration = audio_duration_final + 1.0

        # Một filter graph duy nhất: Hook AI + timeframe source gốc + toàn bộ FX.
        # Hook giữ nguyên speed/crop/blur; chỉ body và voice phía sau nhận speed.
        direct_inputs = ["-i", hook_src]
        segment_filters = []
        segment_labels = []
        segment_audio_labels = []
        for idx, interval in enumerate(clip_intervals):
            start = max(0.0, float(interval.get("start", 0)))
            duration = max(0.05, float(interval.get("end", start)) - start)
            direct_inputs += ["-ss", f"{start:.6f}", "-t", f"{duration:.6f}", "-i", source_video]
            label = f"seg{idx}"
            mirror = ",hflip" if interval.get("mirror") else ""
            segment_filters.append(
                f"[{idx + 1}:v]fps={cls.FPS}{mirror},setpts=PTS-STARTPTS[{label}]"
            )
            segment_labels.append(label)
            if mix_original_with_voice:
                audio_label = f"seg{idx}a"
                segment_filters.append(
                    f"[{idx + 1}:a]aresample=44100,aformat=sample_fmts=fltp:"
                    f"channel_layouts=stereo,asetpts=PTS-STARTPTS[{audio_label}]"
                )
                segment_audio_labels.append(audio_label)

        banner_index = 1 + len(clip_intervals)
        audio_index = banner_index + 1
        direct_inputs += ["-i", banner_path, "-i", final_audio]

        if len(segment_labels) == 1:
            segment_filters.append(f"[{segment_labels[0]}]null[braw]")
        else:
            joined = "".join(f"[{label}]" for label in segment_labels)
            segment_filters.append(f"{joined}concat=n={len(segment_labels)}:v=1:a=0[braw]")
        if mix_original_with_voice:
            if len(segment_audio_labels) == 1:
                segment_filters.append(f"[{segment_audio_labels[0]}]anull[originalbed]")
            else:
                joined_audio = "".join(f"[{label}]" for label in segment_audio_labels)
                segment_filters.append(
                    f"{joined_audio}concat=n={len(segment_audio_labels)}:v=0:a=1[originalbed]"
                )

        hook_filter = cls.build_hook_filter(post_options, "[0:v]").replace("[vout]", "[hookbase]")
        broll_filter = cls.build_broll_filter(post_options, "[braw]").replace("[vout]", "[brollbase]")
        speed_filter = (
            f"setpts={1.0 / speed:.8f}*PTS,fps={cls.FPS}"
            if abs(speed - 1.0) >= 0.01 else f"fps={cls.FPS}"
        )
        fade_duration = min(0.5, max(0.1, hook_duration / 2.0))
        fade_offset = max(0.0, hook_duration - fade_duration)
        visual_filters = [
            *segment_filters,
            hook_filter,
            f"[{banner_index}:v]split=2[bannerh][bannerb]",
            f"[hookbase][bannerh]overlay={banner_x}:{banner_y}[hookfinal]",
            broll_filter,
            f"[brollbase][bannerb]overlay={banner_x}:{banner_y},"
            f"{speed_filter}[brollfinal]",
            f"[hookfinal][brollfinal]xfade=transition=fadewhite:"
            f"duration={fade_duration:.4f}:offset={fade_offset:.4f}[visual]",
        ]
        if sub_filter:
            visual_filters.append(f"[visual]{sub_filter}[outv]")
        else:
            visual_filters.append("[visual]null[outv]")
        if mix_original_with_voice:
            bed_chain = []
            if abs(speed - 1.0) >= 0.01:
                bed_chain.append(f"atempo={speed:.5f}")
            bed_chain.extend([
                f"volume={original_mix_level:.4f}",
                f"adelay={int(round(hook_duration * 1000))}|{int(round(hook_duration * 1000))}",
                "apad",
            ])
            visual_filters.append(f"[originalbed]{','.join(bed_chain)}[beda]")
            visual_filters.append(
                f"[{audio_index}:a]apad[voicea];[voicea][beda]"
                "amix=inputs=2:duration=first:dropout_transition=0,"
                "alimiter=limit=0.95:attack=5:release=80:level=false[outa]"
            )
            logger.info("🎚️ [AUDIO MIX] Hook AI + voice và tiếng B-roll gốc ở mức %.0f%%.",
                        original_mix_level * 100.0)
        else:
            visual_filters.append(f"[{audio_index}:a]apad[outa]")
        fc = ";".join(visual_filters)
        cmd_final = [
            "ffmpeg", "-y", *direct_inputs,
            "-filter_complex", fc,
            "-map", "[outv]", "-map", "[outa]",
            "-t", f"{exact_max_duration:.4f}",
            "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr",
            "-cq", "17", "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M",
            "-c:a", "aac", "-b:a", "256k",
            final_output_path,
        ]
        try:
            cls._run_ffmpeg(cmd_final, "final-pack")
        except subprocess.CalledProcessError:
            cmd_final = [c if c != "h264_nvenc" else "libx264" for c in cmd_final]
            if "-preset" in cmd_final:
                cmd_final[cmd_final.index("-preset") + 1] = "veryfast"
            cls._run_ffmpeg(cmd_final, "final-pack-cpu")

        elapsed = int(time.time() - start_time_render)
        time_str = f"{elapsed}s" if elapsed < 60 else f"{elapsed // 60} phút {elapsed % 60}s"
        logger.info(f"🎉 [EDITOR AI] Xong: {final_output_path} ({time_str})")
        return EditorProcessor.finalize_exported_video(specific_dir, final_output_path, post_options)


# Alias theo tên user hay gọi
Editor_Ai = EditorAI
