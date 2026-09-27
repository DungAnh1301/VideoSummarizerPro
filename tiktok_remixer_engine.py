"""
ĐỘNG CƠ XỬ LÝ ĐỘC LẬP: CHẾ ĐỘ 3 - BIÊN TẬP & RE-MIX VIDEO TIKTOK ĐỐI THỦ (TIKTOK REMIXER ENGINE)
"""
import os
import sys
import json
import re
import random
import subprocess
import logging
import shutil
from typing import Callable, Optional, Dict, Any, List, Tuple

from capcut_filters import apply_look, build_adjust_filters, extra_ffmpeg_tail
from font_manager import FontManager
from market_profiles import get_market_profile, MARKET_PROFILES

logger = logging.getLogger("TikTokRemixerEngine")
logger.setLevel(logging.INFO)
if not logger.handlers:
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s"))
    logger.addHandler(ch)


class TikTokRemixerEngine:
    OUTPUT_W = 1080
    OUTPUT_H = 1920

    @classmethod
    def download_tiktok_no_watermark(cls, url: str, output_dir: str, progress_cb: Optional[Callable] = None) -> Optional[str]:
        """Tải video TikTok sạch (không dính watermark) bằng yt-dlp."""
        os.makedirs(output_dir, exist_ok=True)
        url = url.strip()
        if not url:
            return None
        
        # Nếu đã là đường dẫn file cục bộ hợp lệ
        if os.path.isfile(url):
            return url
        
        if progress_cb:
            progress_cb("download", "Đang tải video TikTok sạch qua yt-dlp (No-Watermark)...")

        out_template = os.path.join(output_dir, "tiktok_source_%(id)s.%(ext)s")
        cmd = [
            "yt-dlp",
            "-f", "bestvideo+bestaudio/best",
            "--no-playlist",
            "--add-header", "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "-o", out_template,
            url
        ]

        flags = 0x08000000 if os.name == "nt" else 0
        try:
            logger.info(f"📥 [TIKTOK DOWNLOAD] Thực thi: {' '.join(cmd)}")
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            if res.returncode == 0:
                # Tìm file video vừa tải trong output_dir
                for f in os.listdir(output_dir):
                    if f.startswith("tiktok_source_") and f.endswith((".mp4", ".mov", ".mkv", ".webm")):
                        f_path = os.path.join(output_dir, f)
                        logger.info(f"✅ [TIKTOK DOWNLOAD] Tải thành công: {f_path}")
                        return f_path
            logger.error(f"❌ [TIKTOK DOWNLOAD] Lỗi tải: {res.stderr}")
        except Exception as e:
            logger.error(f"❌ [TIKTOK DOWNLOAD] Ngoại lệ: {e}")
        return None

    @classmethod
    def get_video_duration(cls, video_path: str) -> float:
        """Đo thời lượng video bằng ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path
            ]
            flags = 0x08000000 if os.name == "nt" else 0
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            return float(res.stdout.strip() or 0.0)
        except Exception:
            return 60.0

    @classmethod
    def get_video_dimensions(cls, video_path: str) -> Tuple[int, int]:
        """Lấy chiều rộng và chiều cao gốc của video."""
        try:
            cmd = [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height",
                "-of", "csv=s=x:p=0",
                video_path
            ]
            flags = 0x08000000 if os.name == "nt" else 0
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            w, h = res.stdout.strip().split("x")
            return int(w), int(h)
        except Exception:
            return 1080, 1920

    @classmethod
    def inspect_and_remix_with_gemini(
        cls,
        video_path: str,
        target_market: str = "DE",
        story_style: str = "Kịch tính / Giật gân",
        progress_cb: Optional[Callable] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Gửi toàn bộ video ngắn vào Gemini CLI phân tích 1 lần duy nhất:
        - Nhận diện Crop lõi sạch & Blur sub/part cũ
        - Kiểm tra logo TikTok 2 đầu
        - Tách Hook + Tách cảnh thông minh (Hard cut 0.0s vs Transition 0.15s)
        - Viết lại kịch bản mới chuẩn ngôn ngữ thị trường
        """
        if progress_cb:
            progress_cb("gemini_inspect", "Gemini AI đang xem toàn bộ video để phân cảnh, đo tọa độ dọn rác và viết kịch bản...")

        market = get_market_profile(target_market)
        lang_name = market.get("language", "German")
        locale_code = market.get("locale", "de-DE")
        dur = cls.get_video_duration(video_path)

        prompt = f"""You are an elite short-form video editor and scriptwriter for TikTok and YouTube Shorts.
Analyze this {dur:.1f}s short video comprehensively and return ONLY a valid JSON object.

TASKS:
1. GEOMETRY CLEANING:
   - Identify if the video has top/bottom blurred borders, textures, or a burned-in Title Banner at the top (like typical competitor reposts).
   - Provide "core_crop_normalized": {{"ymin": float, "ymax": float}} to crop out the top title and bottom TikTok UI/border, keeping only the clean middle core actor/action footage (0.0 to 1.0).
   - Provide "sub_blur_normalized": {{"ymin": float, "ymax": float, "xmin": float, "xmax": float}} marking the burned-in source subtitle text and "Part 2" badge at the bottom to blur.

2. INTRO/OUTRO INSPECTION:
   - Check if the first 0.5s or last 1.0s has bouncing TikTok logo transitions.
   - If clean dialogue/action is present from 0.0s, set "trim_start_sec": 0.0. Only trim if actual watermark animation exists.

3. HOOK DETECTION:
   - Identify the opening hook climax segment (usually 0.0s to 3.5s - 5.0s) with suspenseful facial reaction or action.

4. SMART SCENE SEGMENTATION & TRANSITIONS:
   - Split the remaining footage into clean scenes.
   - For each cut, determine "transition_type":
     * "none" (hard cut): set "cutout_sec": 0.0. DO NOT trim hard cuts!
     * "white_flash", "zoom_glitch", "fade_black": specify exact micro cutout duration (0.15 to 0.25s).
   - Provide "clean_range": [start, end] for each scene.

5. SCRIPT REWRITING:
   - Write an engaging, viral, climax-first short narration in {lang_name} ({locale_code}).
   - Target word count: 80 to 140 words (paced to match ~35s-50s of clean footage).
   - Provide a catchy 2-line title in {lang_name}.

OUTPUT FORMAT (JSON ONLY):
{{
  "layout_geometry": {{
    "has_top_title_or_blur": true,
    "core_crop_normalized": {{"ymin": 0.16, "ymax": 0.84}},
    "sub_blur_normalized": {{"ymin": 0.72, "ymax": 0.86, "xmin": 0.10, "xmax": 0.90}}
  }},
  "intro_outro_inspection": {{
    "has_intro_tiktok_logo": false,
    "trim_start_sec": 0.0,
    "has_outro_tiktok_logo": false,
    "trim_end_sec": 0.0
  }},
  "hook": {{
    "has_hook": true,
    "start_sec": 0.0,
    "end_sec": 3.8,
    "keep_original_audio": true
  }},
  "scenes": [
    {{
      "id": "S1",
      "raw_range": [3.8, 8.5],
      "transition_type": "none",
      "cutout_sec": 0.0,
      "clean_range": [3.8, 8.5]
    }},
    {{
      "id": "S2",
      "raw_range": [8.5, 14.0],
      "transition_type": "white_flash",
      "cutout_sec": 0.18,
      "clean_range": [8.68, 14.0]
    }}
  ],
  "rewritten_narration": {{
    "language": "{locale_code}",
    "title_line1": "TITEL ZEILE 1",
    "title_line2": "TITEL ZEILE 2",
    "script_text": "Spoken narration in {lang_name}..."
  }}
}}
"""
        # Gọi Gemini qua AntigravityProcessor hoặc API
        try:
            from antigravity_processor import AntigravityProcessor
            if AntigravityProcessor.executable():
                logger.info("🤖 [GEMINI CLI] Gửi video TikTok sang Antigravity CLI...")
                raw_out = AntigravityProcessor.inspect_video_prompt(video_path, prompt)
                if raw_out:
                    parsed = cls._parse_json_from_text(raw_out)
                    if parsed:
                        logger.info("✅ [GEMINI CLI] Đã nhận phân tích phân cảnh và kịch bản thành công!")
                        return parsed
        except Exception as e:
            logger.warning(f"⚠️ [GEMINI CLI] Lỗi Antigravity CLI: {e}")

        # Fallback Gemini API trực tiếp nếu có key
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        if api_key:
            try:
                from ai_processor import AIProcessor
                raw_out = AIProcessor.call_gemini_vision_file(video_path, prompt, api_key=api_key)
                if raw_out:
                    parsed = cls._parse_json_from_text(raw_out)
                    if parsed:
                        return parsed
            except Exception as e:
                logger.error(f"❌ [GEMINI API] Lỗi gọi trực tiếp API: {e}")

        # Fallback dữ liệu mặc định an toàn nếu không có AI
        logger.warning("⚠️ [GEMINI FALLBACK] Sử dụng thông số phân cảnh mặc định an toàn.")
        return cls._create_default_fallback_plan(dur, target_market)

    @classmethod
    def _parse_json_from_text(cls, text: str) -> Optional[Dict[str, Any]]:
        """Trích xuất và nạp JSON an toàn từ phản hồi AI."""
        t = text.strip()
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", t, re.I | re.S)
        if fence:
            t = fence.group(1).strip()
        try:
            return json.loads(t)
        except Exception:
            start, end = t.find("{"), t.rfind("}")
            if start >= 0 and end > start:
                try:
                    return json.loads(t[start:end+1])
                except Exception:
                    pass
        return None

    @classmethod
    def _create_default_fallback_plan(cls, dur: float, target_market: str) -> Dict[str, Any]:
        """Tạo plan mặc định khi không có kết nối AI."""
        market = get_market_profile(target_market)
        return {
            "layout_geometry": {
                "has_top_title_or_blur": True,
                "core_crop_normalized": {"ymin": 0.15, "ymax": 0.85},
                "sub_blur_normalized": {"ymin": 0.74, "ymax": 0.86, "xmin": 0.10, "xmax": 0.90}
            },
            "intro_outro_inspection": {
                "has_intro_tiktok_logo": False,
                "trim_start_sec": 0.0,
                "has_outro_tiktok_logo": False,
                "trim_end_sec": 0.0
            },
            "hook": {
                "has_hook": True,
                "start_sec": 0.0,
                "end_sec": min(4.0, dur * 0.15),
                "keep_original_audio": True
            },
            "scenes": [
                {
                    "id": "S1",
                    "raw_range": [min(4.0, dur * 0.15), dur],
                    "transition_type": "none",
                    "cutout_sec": 0.0,
                    "clean_range": [min(4.0, dur * 0.15), dur]
                }
            ],
            "rewritten_narration": {
                "language": market.get("locale", "de-DE"),
                "title_line1": "REMIX HIGHLIGHT",
                "title_line2": "TIKTOK SPECIAL",
                "script_text": "Unglaubliche Momente, die man einfach gesehen haben muss."
            }
        }

    @classmethod
    def generate_narration_audio_and_sub(
        cls,
        script_text: str,
        engine_name: str = "CapCut TTS",
        voice_name: str = "de-DE-ConradNeural",
        speed_str: str = "+0%",
        output_dir: str = "temp"
    ) -> Tuple[Optional[str], Optional[str], float]:
        """Tạo giọng đọc AI thuyết minh và file phụ đề .srt tương ứng."""
        os.makedirs(output_dir, exist_ok=True)
        audio_out = os.path.join(output_dir, "tiktok_narration.mp3")
        srt_out = os.path.join(output_dir, "tiktok_subtitles.srt")

        from ai_processor import AIProcessor
        try:
            # Tạo audio qua EdgeTTS hoặc CapCutTTS
            if "CapCut" in engine_name:
                audio_ok = AIProcessor.generate_capcut_tts(script_text, voice_name, audio_out)
            else:
                audio_ok = AIProcessor.generate_edge_tts(script_text, voice_name, audio_out, rate=speed_str)

            if audio_ok and os.path.isfile(audio_out):
                dur = cls.get_video_duration(audio_out)
                # Tạo file sub srt khớp âm thanh
                AIProcessor.generate_srt_from_text(script_text, srt_out, total_duration=dur)
                return audio_out, srt_out, dur
        except Exception as e:
            logger.error(f"❌ [TTS ERROR] Lỗi tạo giọng đọc: {e}")
        return None, None, 0.0

    @classmethod
    def render_tiktok_remix(
        cls,
        source_video: str,
        gemini_plan: Dict[str, Any],
        narration_audio: Optional[str],
        narration_srt: Optional[str],
        audio_duration: float,
        options: Dict[str, Any],
        output_path: str,
        progress_cb: Optional[Callable] = None
    ) -> bool:
        """
        Động cơ Dựng Hoàn Chỉnh (Master Renderer):
        1. Cắt lõi sạch theo core_crop_normalized (loại bỏ 2 đầu + Title cũ)
        2. Bôi mờ sub cũ theo sub_blur_normalized
        3. Cắt cảnh sạch thông minh (0.0s vs 0.2s)
        4. Điều tốc đàn hồi B-Roll 0.95x trên cảnh lẻ khớp khít âm thanh
        5. Đảo cảnh (Shuffle) & Lật gương (Mirror)
        6. Đổ màu CapCut 8K/HDR/Cinematic 15 thông số
        7. Đè Title Banner 2 dòng mới ở trên + Đè Subtitle mới đè kín lên vết mờ
        """
        if progress_cb:
            progress_cb("render", "Đang cắt lõi sạch, bôi mờ sub cũ, điều tốc đàn hồi và đóng gói video 9:16...")

        work_dir = os.path.dirname(output_path)
        os.makedirs(work_dir, exist_ok=True)
        orig_w, orig_h = cls.get_video_dimensions(source_video)

        # 1. Tính toán Tọa độ Cắt Lõi Sạch (Clean Core Crop)
        geom = gemini_plan.get("layout_geometry", {})
        crop_norm = geom.get("core_crop_normalized", {"ymin": 0.0, "ymax": 1.0})
        ymin = max(0.0, min(0.4, float(crop_norm.get("ymin", 0.0))))
        ymax = max(0.6, min(1.0, float(crop_norm.get("ymax", 1.0))))
        
        crop_y = int(orig_h * ymin)
        crop_h = int(orig_h * (ymax - ymin))
        crop_w = orig_w
        crop_x = 0

        # 2. Tính toán Tọa độ Bôi Mờ Sub Cũ (Sub Blur Zone)
        sub_norm = geom.get("sub_blur_normalized", {"ymin": 0.74, "ymax": 0.86, "xmin": 0.1, "xmax": 0.9})
        blur_mx = int(crop_w * float(sub_norm.get("xmin", 0.1)))
        blur_mw = int(crop_w * (float(sub_norm.get("xmax", 0.9)) - float(sub_norm.get("xmin", 0.1))))
        blur_my = int(crop_h * float(sub_norm.get("ymin", 0.74)))
        blur_mh = int(crop_h * (float(sub_norm.get("ymax", 0.86)) - float(sub_norm.get("ymin", 0.74))))

        # 3. Phân Đoạn Hook & Scenes
        hook_info = gemini_plan.get("hook", {})
        hook_start = float(hook_info.get("start_sec", 0.0))
        hook_end = float(hook_info.get("end_sec", 3.5))
        hook_dur = max(0.0, hook_end - hook_start)

        raw_scenes = gemini_plan.get("scenes", [])
        clean_scenes = []
        for sc in raw_scenes:
            c_range = sc.get("clean_range", sc.get("raw_range", [0, 0]))
            st, en = float(c_range[0]), float(c_range[1])
            if en > st + 0.4:
                clean_scenes.append({"id": sc.get("id"), "start": st, "end": en, "dur": en - st})

        # 4. Điều Tốc B-Roll Đàn Hồi (Elastic B-Roll Speed Matching)
        target_body_dur = max(5.0, audio_duration)
        total_clean_dur = sum(s["dur"] for s in clean_scenes) or 10.0

        # Nếu xáo trộn cảnh được bật
        if options.get("shuffle_broll", True) and len(clean_scenes) > 2:
            # Giữ cảnh đầu của thân, đảo ngẫu nhiên các cảnh sau
            first = clean_scenes[0]
            rest = clean_scenes[1:]
            random.shuffle(rest)
            clean_scenes = [first] + rest

        # 5. Xây dựng Title Banner Mới Bằng PIL
        from editor_processor import EditorProcessor
        script_info = gemini_plan.get("rewritten_narration", {})
        t_line1 = script_info.get("title_line1", options.get("title_line1", "REMIX SPECIAL"))
        t_line2 = script_info.get("title_line2", options.get("title_line2", ""))
        
        banner_path, banner_w, banner_h = EditorProcessor._create_dynamic_title_banner_custom(
            work_dir, t_line1, t_line2, options
        )
        banner_x = int((cls.OUTPUT_W - banner_w) / 2)
        banner_y = int(options.get("title_y_pos", 260))

        # 6. Tính Tọa Độ Subtitle Mới Đè Lên Vùng Sub Cũ Đã Blur
        # Gán MarginV tự động từ sub_norm
        sub_center_rel = (float(sub_norm.get("ymin", 0.74)) + float(sub_norm.get("ymax", 0.86))) / 2.0
        calculated_margin_v = int(cls.OUTPUT_H * (1.0 - sub_center_rel))
        sub_margin_v = calculated_margin_v if options.get("overlay_sub_on_blur_zone", True) else int(options.get("sub_margin_v", 100))

        # 7. Bộ Lọc Màu CapCut 15 Thông Số
        color_filter_str = EditorProcessor._build_pure_color_filter(options)

        # 8. Xây Dựng Lệnh FFmpeg Filter Complex Hoàn Chỉnh
        # Nhánh 1: Cắt Hook (Giữ âm thanh gốc)
        hook_cut_mp4 = os.path.join(work_dir, "cut_hook.mp4")
        flags = 0x08000000 if os.name == "nt" else 0

        # Cắt Hook trực tiếp
        cmd_hook = [
            "ffmpeg", "-y", "-ss", f"{hook_start:.3f}", "-to", f"{hook_end:.3f}",
            "-i", source_video,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            hook_cut_mp4
        ]
        subprocess.run(cmd_hook, capture_output=True, creationflags=flags)

        # Cắt và ghép các đoạn B-Roll thân video
        body_cut_mp4 = os.path.join(work_dir, "cut_body.mp4")
        body_concat_list = os.path.join(work_dir, "body_clips.txt")

        clip_files = []
        for idx, sc in enumerate(clean_scenes):
            c_file = os.path.join(work_dir, f"broll_{idx:03d}.mp4")
            # Nếu thiếu hình, cảnh lẻ áp dụng 0.95x
            pts_speed = 1.0
            if total_clean_dur < target_body_dur and (idx % 2 == 1) and options.get("elastic_broll_speed", True):
                pts_speed = 1.0526 # 0.95x chậm hơn để kéo dài thời lượng
            
            mirror_filter = ",hflip" if (options.get("mirror_broll", True) and idx % 2 == 0) else ""

            vf_clip = (
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"split=2[c_orig][c_mask];"
                f"[c_mask]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=12:3[c_blur];"
                f"[c_orig][c_blur]overlay={blur_mx}:{blur_my},"
                f"setpts={pts_speed:.4f}*PTS{mirror_filter}"
            )
            cmd_c = [
                "ffmpeg", "-y", "-ss", f"{sc['start']:.3f}", "-to", f"{sc['end']:.3f}",
                "-i", source_video,
                "-vf", vf_clip,
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                c_file
            ]
            subprocess.run(cmd_c, capture_output=True, creationflags=flags)
            if os.path.isfile(c_file):
                clip_files.append(c_file)

        # Ghi danh sách ghép B-Roll
        with open(body_concat_list, "w", encoding="utf-8") as bf:
            for cf in clip_files:
                bf.write(f"file '{os.path.abspath(cf).replace(chr(92), '/')}'\n")

        # Ghép thân video thô
        cmd_concat_body = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", body_concat_list,
            "-c", "copy", body_cut_mp4
        ]
        subprocess.run(cmd_concat_body, capture_output=True, creationflags=flags)

        # 9. Tổng hợp hoàn chỉnh lên khung 9:16 + Nền Blur + Voice AI + Title Banner + Subtitle Mới
        sub_style_str = (
            f"subtitles='{os.path.abspath(narration_srt).replace(chr(92), '/')}:force_style="
            f"Fontname=Segoe UI,FontSize={options.get('sub_size', 16)},"
            f"PrimaryColour={options.get('sub_color', '&HFFFFFF&')},"
            f"OutlineColour={options.get('sub_outline_color', '&H000000&')},"
            f"Outline={options.get('sub_outline', 3)},Shadow={options.get('sub_shadow', 1)},"
            f"MarginV={sub_margin_v}'"
        ) if (options.get("enable_sub", True) and narration_srt and os.path.isfile(narration_srt)) else "null"

        # Filter complex master
        master_vf = (
            f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,avgblur=8[bg];"
            f"[0:v]scale=1080:-2[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v_mid];"
            f"[v_mid]{color_filter_str}[v_color];"
            f"[v_color][1:v]overlay={banner_x}:{banner_y}[v_banner];"
            f"[v_banner]{sub_style_str}[vout]"
        )

        cmd_final = [
            "ffmpeg", "-y",
            "-i", body_cut_mp4,
            "-i", banner_path,
            "-i", narration_audio if (narration_audio and os.path.isfile(narration_audio)) else body_cut_mp4,
            "-filter_complex", master_vf,
            "-map", "[vout]",
            "-map", "2:a" if (narration_audio and os.path.isfile(narration_audio)) else "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            output_path
        ]

        logger.info(f"🚀 [MASTER RENDER] Thực thi đóng gói video 9:16: {' '.join(cmd_final)}")
        res_final = subprocess.run(cmd_final, capture_output=True, text=True, creationflags=flags)
        
        if res_final.returncode == 0 and os.path.isfile(output_path):
            logger.info(f"🎉 [MASTER RENDER] XUẤT VIDEO THÀNH CÔNG: {output_path}")
            if progress_cb:
                progress_cb("done", f"Xuất video hoàn tất: {os.path.basename(output_path)}")
            return True
        else:
            logger.error(f"❌ [MASTER RENDER] Lỗi đóng gói: {res_final.stderr}")
            return False
