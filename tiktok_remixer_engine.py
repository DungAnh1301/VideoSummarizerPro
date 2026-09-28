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

        prompt = f"""You are an elite master short-form director, video editor, and viral scriptwriter for TikTok and YouTube Shorts.
Perform a DENSE, FRAME-ACCURATE visual and audio inspection of this {dur:.1f}s full native video.
You are given the full native video file directly. Take as much time and attention as needed to inspect every micro-frame, subtle motion, facial expression, and audio beat thoroughly. Return ONLY valid JSON.

TASKS:
1. GEOMETRY CLEANING:
   - Identify top/bottom blurred bars, textures, or burned-in competitor Title banners.
   - Provide "core_crop_normalized": {{"ymin": float, "ymax": float}} to slice off competitor titles and UI, isolating only the clean core action footage.
   - Provide "sub_blur_normalized": {{"ymin": float, "ymax": float, "xmin": float, "xmax": float}} precisely bounding burned-in source subtitles and "Part" badges at the bottom to blur.

2. INTRO/OUTRO WATERMARK INSPECTION:
   - Inspect the first 0.5s and last 1.0s frame-by-frame for bouncing TikTok logos or transition wipes.
   - Only set trim if actual animated watermarks appear. If real action/speech starts at 0.0s, keep "trim_start_sec": 0.0.

3. HOOK DETECTION:
   - Pinpoint the climax hook at the beginning (0.0s to 3.5s - 5.0s) featuring the most dramatic reaction, expression, or suspenseful move down to the exact frame.
   - Set "keep_original_audio": true.

4. FRAME-ACCURATE SCENE SEGMENTATION & VISUAL TAGGING:
   - Segment the remaining footage into clean B-Roll scenes down to frame-accurate floating-point timestamps (e.g. [3.25, 8.42] rather than coarse seconds).
   - For EACH scene, provide:
     * "id": "S1", "S2", "S3"...
     * "clean_range": [start_sec, end_sec] (frame-accurate floating-point seconds)
     * "visual_summary": exact action, subject, facial expression, mood, objects shown
     * "transition_type": "none" (hard cut - DO NOT cut out any frames, cutout_sec: 0.0), or "white_flash"/"zoom_glitch"/"fade_black" (micro cut 0.15s - 0.22s).

5. CONTINUOUS NARRATION & NEAREST-SEMANTIC B-ROLL MATCHING (GHÉP CẢNH CÓ SẴN THEO NGỮ NGHĨA GẦN NHẤT):
   - CRITICAL MONETIZATION CONSTRAINT: The final video MUST be strictly LONGER THAN 60 SECONDS (Target: 61.5s to 68.0s) to qualify for TikTok Creator Rewards.
   - Word budget: Write approx 140 to 175 spoken words in {lang_name} ({locale_code}). NEVER write fewer than 135 words!
   - You only have these existing raw B-Roll scenes extracted from the source video (no external replacement footage). DO NOT chop them into awkward micro fragments; keep their natural camera motion and emotion intact.
   - Write a smooth, continuous viral narration story that reads seamlessly from beginning to end without artificial pauses or waiting for cuts.
   - Re-arrange and sequence the available existing B-Roll scenes in "remix_storyboard" so that each scene visually matches the NEAREST SEMANTIC MEANING, mood, or action of that part of the continuous voiceover/subtitles.
   - Ensure the re-ordered scene sequence breaks the competitor's original hash while fitting the new narrative progression as coherently as possible.
   - Provide "title_line1" and "title_line2" in {lang_name}.

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
    "keep_original_audio": true,
    "visual_summary": "Shocked facial expression of the protagonist"
  }},
  "scenes": [
    {{
      "id": "S1",
      "clean_range": [3.8, 8.5],
      "visual_summary": "Red sports car accelerating fast on highway",
      "transition_type": "none",
      "cutout_sec": 0.0
    }},
    {{
      "id": "S2",
      "clean_range": [8.5, 13.8],
      "visual_summary": "Close-up of speedometer revving to redline",
      "transition_type": "white_flash",
      "cutout_sec": 0.18
    }},
    {{
      "id": "S3",
      "clean_range": [13.8, 19.2],
      "visual_summary": "Driver smiling confidently holding the steering wheel",
      "transition_type": "none",
      "cutout_sec": 0.0
    }}
  ],
  "remix_storyboard": [
    {{
      "segment_index": 1,
      "scene_id": "S2",
      "semantic_matching_reason": "Matches the tension and urgency described in the narration",
      "target_duration_sec": 5.1
    }},
    {{
      "segment_index": 2,
      "scene_id": "S1",
      "semantic_matching_reason": "Matches the high-speed motion described in the narration",
      "target_duration_sec": 4.7
    }},
    {{
      "segment_index": 3,
      "scene_id": "S3",
      "semantic_matching_reason": "Matches the confidence and unexpected twist in the story",
      "target_duration_sec": 5.4
    }}
  ],
  "rewritten_narration": {{
    "language": "{locale_code}",
    "title_line1": "DER UNGLAUBLICHE",
    "title_line2": "MOMENT DER ENTSCHEIDUNG",
    "script_text": "Der Zeiger erreichte die absolute Höchstgrenze. Mit atemberaubender Geschwindigkeit raste der Wagen durch die finstere Nacht. Doch was er vorhatte, ahnte zu diesem Zeitpunkt noch absolut niemand."
  }}
}}
"""
        # Gọi Gemini qua AntigravityProcessor hoặc API
        try:
            from antigravity_processor import AntigravityProcessor
            if AntigravityProcessor.executable():
                logger.info("🤖 [GEMINI CLI] Gửi FULL NATIVE VIDEO sang Antigravity CLI để soi chi tiết từng frame nhỏ...")
                raw_out = AntigravityProcessor.inspect_video_prompt(video_path, prompt)
                if raw_out:
                    parsed = cls._parse_json_from_text(raw_out)
                    if parsed:
                        logger.info("✅ [GEMINI CLI] Đã nhận phân tích phân cảnh frame-accurate, storyboard logic và kịch bản thành công!")
                        return parsed
        except Exception as e:
            logger.warning(f"⚠️ [GEMINI CLI] Lỗi Antigravity CLI: {e}")

        # Fallback Gemini API trực tiếp nếu có key
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        if api_key:
            try:
                from ai_processor import AIProcessor
                raw_out = AIProcessor.call_gemini_native_video(video_path, prompt, api_key=api_key)
                if raw_out:
                    parsed = cls._parse_json_from_text(raw_out)
                    if parsed:
                        logger.info("✅ [GEMINI API] Đã nhận kết quả phân tích full native video từ Gemini API thành công!")
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
        h_end = min(4.0, dur * 0.15)
        rem_dur = max(2.0, dur - h_end)
        half_dur = rem_dur / 2.0
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
                "end_sec": h_end,
                "keep_original_audio": True,
                "visual_summary": "Opening highlight"
            },
            "scenes": [
                {
                    "id": "S1",
                    "clean_range": [h_end, h_end + half_dur],
                    "visual_summary": "Action progression scene 1",
                    "has_text": False,
                    "transition_type": "none",
                    "cutout_sec": 0.0
                },
                {
                    "id": "S2",
                    "clean_range": [h_end + half_dur, dur],
                    "visual_summary": "Climax resolution scene 2",
                    "has_text": False,
                    "transition_type": "none",
                    "cutout_sec": 0.0
                }
            ],
            "remix_storyboard": [
                {
                    "segment_index": 1,
                    "scene_id": "S2",
                    "narration_sentence": "Unglaubliche Momente, die man einfach gesehen haben muss.",
                    "target_duration_sec": half_dur
                },
                {
                    "segment_index": 2,
                    "scene_id": "S1",
                    "narration_sentence": "Niemand hatte mit dieser unerwarteten Wendung gerechnet.",
                    "target_duration_sec": half_dur
                }
            ],
            "rewritten_narration": {
                "language": market.get("locale", "de-DE"),
                "title_line1": "REMIX HIGHLIGHT",
                "title_line2": "TIKTOK SPECIAL",
                "script_text": "Unglaubliche Momente, die man einfach gesehen haben muss. Niemand hatte mit dieser unerwarteten Wendung gerechnet."
            }
        }

    @classmethod
    def format_srt_time(cls, seconds: float) -> str:
        """Định dạng số giây sang chuẩn SRT hh:mm:ss,mmm."""
        millis = int(round((seconds - int(seconds)) * 1000))
        secs_int = int(seconds)
        mins, secs = divmod(secs_int, 60)
        hours, mins = divmod(mins, 60)
        return f"{hours:02d}:{mins:02d}:{secs:02d},{millis:03d}"

    @classmethod
    def create_storyboard_srt(
        cls,
        sentences: List[str],
        total_duration: float,
        output_srt_path: str
    ):
        """Tạo file SRT từ danh sách câu phân đoạn kịch bản khớp với tổng thời lượng audio."""
        if not sentences:
            sentences = ["..."]
        
        # Đếm số từ của từng câu để phân bổ thời gian tương ứng
        word_counts = [max(1, len(s.strip().split())) for s in sentences]
        total_words = max(1, sum(word_counts))
        
        cur_time = 0.0
        with open(output_srt_path, "w", encoding="utf-8") as f:
            for idx, (sent, w_cnt) in enumerate(zip(sentences, word_counts), 1):
                dur_sent = (w_cnt / total_words) * total_duration
                start_str = cls.format_srt_time(cur_time)
                end_str = cls.format_srt_time(min(total_duration, cur_time + dur_sent))
                f.write(f"{idx}\n{start_str} --> {end_str}\n{sent.strip()}\n\n")
                cur_time += dur_sent

    @classmethod
    def generate_narration_audio_and_sub(
        cls,
        script_text: str,
        engine_name: str = "CapCut TTS",
        voice_name: str = "Jessie (DiT_en_female_jessie)",
        locale: str = "en-US",
        speed_str: str = "+0%",
        storyboard: Optional[List[Dict[str, Any]]] = None,
        output_dir: str = "temp"
    ) -> Tuple[Optional[str], Optional[str], float]:
        """Tạo giọng đọc AI thuyết minh và file phụ đề .srt tương ứng khớp từng phân đoạn câu."""
        os.makedirs(output_dir, exist_ok=True)
        audio_out = os.path.join(output_dir, "tiktok_narration.mp3")
        srt_out = os.path.join(output_dir, "tiktok_subtitles.srt")

        # Lấy danh sách câu từ storyboard nếu có
        sentences = []
        if storyboard and isinstance(storyboard, list):
            for seg in storyboard:
                sent = seg.get("narration_sentence", "").strip()
                if sent:
                    sentences.append(sent)
        
        if not script_text and sentences:
            script_text = " ".join(sentences)

        from ai_processor import AIProcessor
        try:
            # Tạo audio qua AIProcessor (hỗ trợ cả CapCut TTS, Edge-TTS, Google Translate TTS)
            audio_ok = AIProcessor.generate_voice(
                voice_option=voice_name,
                text=script_text,
                output_path=audio_out,
                locale=locale
            )

            if audio_ok and os.path.isfile(audio_out):
                dur = cls.get_video_duration(audio_out)
                srt_created = False
                
                # Thử tạo SRT chuẩn bằng Faster-Whisper nếu có sẵn trong hệ thống
                try:
                    if hasattr(AIProcessor, "write_exact_script_srt"):
                        exact_srt = AIProcessor.write_exact_script_srt(script_text, audio_out, output_dir)
                        if exact_srt and os.path.isfile(exact_srt):
                            shutil.copy2(exact_srt, srt_out)
                            srt_created = True
                except Exception as w_err:
                    logger.warning("⚠️ [TTS SRT] Whisper fallback: %s", w_err)

                if not srt_created:
                    # Tạo SRT từ storyboard sentences hoặc tách câu từ script_text
                    if not sentences:
                        sentences = [s.strip() for s in re.split(r'[.!?]+', script_text) if s.strip()]
                    cls.create_storyboard_srt(sentences, dur, srt_out)

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
        progress_cb: Optional[Callable] = None,
        timing_stats: Optional[Dict[str, float]] = None
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
        scenes_by_id = {}
        clean_scenes = []
        for sc in raw_scenes:
            c_range = sc.get("clean_range", sc.get("raw_range", [0, 0]))
            st, en = float(c_range[0]), float(c_range[1])
            # Tránh trùng lặp cảnh Hook trong thân video: Hook đã đặt riêng ở 00:00
            if options.get("keep_original_hook", True) and hook_dur >= 0.5:
                if en <= hook_end + 0.2:
                    continue
                if st < hook_end:
                    st = hook_end
            if en > st + 0.4:
                sc_dict = {
                    "id": sc.get("id"),
                    "start": st,
                    "end": en,
                    "dur": en - st,
                    "has_text": bool(sc.get("has_text", False)),
                    "visual_summary": sc.get("visual_summary", "")
                }
                clean_scenes.append(sc_dict)
                if sc.get("id"):
                    scenes_by_id[sc.get("id")] = sc_dict

        # 4. Sắp xếp lại thứ tự cảnh: Ưu tiên Storyboard của AI (khớp nội dung mới) hoặc Shuffle
        remix_storyboard = gemini_plan.get("remix_storyboard", [])
        ordered_scenes = []
        if remix_storyboard and isinstance(remix_storyboard, list):
            for seg in remix_storyboard:
                sc_id = seg.get("scene_id")
                if sc_id and sc_id in scenes_by_id:
                    ordered_scenes.append(dict(scenes_by_id[sc_id]))
            logger.info("🎬 [STORYBOARD AI] Đã sắp xếp %d cảnh theo kịch bản logic của AI.", len(ordered_scenes))

        # Nếu không có storyboard hoặc còn cảnh thừa, sắp xếp fallback
        if not ordered_scenes:
            ordered_scenes = list(clean_scenes)
            if options.get("shuffle_broll", True) and len(ordered_scenes) > 2:
                first = ordered_scenes[0]
                rest = ordered_scenes[1:]
                random.shuffle(rest)
                ordered_scenes = [first] + rest
        else:
            # Bổ sung các cảnh chưa dùng vào cuối
            used_ids = {s["id"] for s in ordered_scenes}
            remaining = [s for s in clean_scenes if s["id"] not in used_ids]
            if remaining:
                ordered_scenes.extend(remaining)

        clean_scenes = ordered_scenes

        # 5. Đảm Bảo Chuẩn Thời Lượng Kiếm Tiền TikTok (Bắt buộc > 60s, mục tiêu >= 61.5s)
        # Hook + Body >= 61.5s
        min_monetization_total = 61.5
        required_body_dur = max(min_monetization_total - hook_dur, audio_duration)
        target_body_dur = max(5.0, required_body_dur)
        total_clean_dur = sum(s["dur"] for s in clean_scenes) or 10.0

        # Nếu video gốc quá ngắn (thiếu > 4s để đạt chuẩn 1 phút):
        # Kích hoạt Variant Looping (tái sử dụng các B-Roll hành động với biến thể góc quay/zoom mới)
        if total_clean_dur < target_body_dur and clean_scenes:
            deficit = target_body_dur - total_clean_dur
            if deficit > 3.0:
                logger.info("⚡ [MONETIZATION >60s] Video nguồn thiếu %.1fs để đạt 1 phút. Kích hoạt Variant B-Roll Looping...", deficit)
                loop_pool = [s for s in clean_scenes if not s.get("has_text", False)] or clean_scenes
                added_dur = 0.0
                variant_idx = 1
                while total_clean_dur + added_dur < target_body_dur - 2.0 and loop_pool:
                    for sc_cand in loop_pool:
                        sc_variant = dict(sc_cand)
                        sc_variant["id"] = f"{sc_cand['id']}_var{variant_idx}"
                        sc_variant["is_variant"] = True
                        clean_scenes.append(sc_variant)
                        added_dur += sc_variant["dur"]
                        variant_idx += 1
                        if total_clean_dur + added_dur >= target_body_dur - 2.0:
                            break
                total_clean_dur += added_dur
                logger.info("✅ [MONETIZATION >60s] Đã bổ sung B-Roll biến thể, tổng thời lượng cảnh: %.1fs (Mục tiêu: %.1fs)", total_clean_dur, target_body_dur)

        # 6. Điều Tốc B-Roll Đàn Hồi Ngẫu Nhiên (Stochastic Elastic Speed Matching)
        elastic_pts_map = {}
        if total_clean_dur < target_body_dur and options.get("elastic_broll_speed", True) and clean_scenes:
            # Chọn ngẫu nhiên 50% đến 80% số cảnh để giãn thời lượng
            sample_size = max(1, int(len(clean_scenes) * random.uniform(0.5, 0.8)))
            stretched_indices = set(random.sample(range(len(clean_scenes)), min(sample_size, len(clean_scenes))))
            for idx in range(len(clean_scenes)):
                if idx in stretched_indices:
                    # Random dao động nhẹ tốc độ an toàn: 1.025 đến 1.075 (chậm lại 0.93x - 0.975x)
                    elastic_pts_map[idx] = round(random.uniform(1.025, 1.075), 4)
                else:
                    elastic_pts_map[idx] = 1.0
            logger.info("⏱️ [ELASTIC SPEED] Đã chọn ngẫu nhiên %d/%d cảnh để giãn nhẹ tốc độ.", len(stretched_indices), len(clean_scenes))
        else:
            for idx in range(len(clean_scenes)):
                elastic_pts_map[idx] = 1.0

        # 7. Lật Gương Phản Chiếu Ngẫu Nhiên (Chuẩn theo Chế độ 1 Tóm tắt):
        # Lật ngẫu nhiên 40% - 50% số cảnh trực tiếp, không dò chữ, sau đó Sub mới và Title mới overlay lên trên cùng.
        mirror_map = {idx: False for idx in range(len(clean_scenes))}
        if options.get("mirror_broll", True) and clean_scenes:
            mirror_count = min(len(clean_scenes), max(1, int(round(len(clean_scenes) * 0.45))))
            for m_idx in random.sample(range(len(clean_scenes)), mirror_count):
                mirror_map[m_idx] = True
            # Cảnh biến thể luôn lật gương để đổi góc quay
            for idx, sc in enumerate(clean_scenes):
                if sc.get("is_variant", False):
                    mirror_map[idx] = True
            logger.info("🪞 [MIRROR] Lật ngẫu nhiên %d/%d cảnh chuẩn như Chế độ 1 (Sub mới và Title overlay lên trên cùng).", mirror_count, len(clean_scenes))

        # 7. Xây dựng Title Banner Mới Bằng PIL
        from editor_processor import EditorProcessor
        script_info = gemini_plan.get("rewritten_narration", {})
        t_line1 = script_info.get("title_line1", options.get("title_line1", "REMIX SPECIAL"))
        t_line2 = script_info.get("title_line2", options.get("title_line2", ""))
        
        banner_path, banner_w, banner_h = EditorProcessor._create_dynamic_title_banner_custom(
            work_dir, t_line1, t_line2, options
        )
        banner_x = int((cls.OUTPUT_W - banner_w) / 2)
        banner_y = int(options.get("title_y_pos", 260))

        # 8. Tính Kích Thước Tiền Cảnh (Foreground) & Tọa Độ Subtitle Mới
        # Đảm bảo giữ nguyên tỷ lệ khung hình gốc của lõi video sạch (không bị kéo giãn dọc thành sợi bún)
        core_ar = crop_w / max(1, crop_h)
        zoom_val = float(options.get("zoom_percent", 105.0) or 105.0)
        zoom_in = bool(options.get("zoom_in", True))
        zoom_factor = (zoom_val / 100.0) if zoom_in else 1.0
        sx = (float(options.get("scale_w") or options.get("scale_x", 100.0) or 100.0)) / 100.0
        sy = (float(options.get("scale_h") or options.get("scale_y", 100.0) or 100.0)) / 100.0
        blur_bg = bool(options.get("blur_bg", True))
        video_speed = float(options.get("speed") or options.get("source_speed", 1.05) or 1.05)
        audio_boost = float(options.get("audio_boost", 6.0) or 6.0)

        # Kích thước tiền cảnh (lõi video) trên khung 1080x1920
        fg_w = int(round(cls.OUTPUT_W * zoom_factor * sx))
        fg_w += fg_w % 2
        fg_h = int(round(fg_w / core_ar * sy))
        fg_h += fg_h % 2

        # Tọa độ Subtitle đè chính xác lên dải sub cũ đã bôi mờ
        sub_center_orig = (float(sub_norm.get("ymin", 0.74)) + float(sub_norm.get("ymax", 0.86))) / 2.0
        core_rel_y = (sub_center_orig - ymin) / max(0.01, (ymax - ymin))
        top_y_canvas = (cls.OUTPUT_H - fg_h) / 2.0
        sub_y_canvas = top_y_canvas + (core_rel_y * fg_h)
        calculated_margin_v = int(cls.OUTPUT_H - sub_y_canvas)
        sub_margin_v = calculated_margin_v if options.get("overlay_sub_on_blur_zone", True) else int(options.get("sub_margin_v", 100))
        sub_margin_v = max(30, min(800, sub_margin_v))

        # 9. Bộ Lọc Màu CapCut 15 Thông Số
        color_filter_str = EditorProcessor._build_pure_color_filter(options)

        # 10. Xử Lý Phân Đoạn Hook Đầu Video (Vị trí 00:00.000, giữ nguyên âm thanh gốc)
        t_hook_start = time.time()
        hook_rendered_mp4 = os.path.join(work_dir, "hook_rendered.mp4")
        has_hook_segment = False
        flags = 0x08000000 if os.name == "nt" else 0

        audio_opts = []
        if audio_boost != 0:
            audio_opts.extend(["-filter:a", f"volume={audio_boost:.1f}dB"])

        has_src_audio = EditorProcessor._has_audio(source_video)

        if options.get("keep_original_hook", True) and hook_dur >= 0.5:
            logger.info("🎬 [HOOK RENDER] Dựng đoạn Hook mở đầu (%.2fs - %.2fs) giữ nguyên 100%% âm thanh gốc...", hook_start, hook_end)
            if blur_bg:
                bg_hook_chain = (
                    f"[core_clean]split=2[c_fg][c_bg];"
                    f"[c_bg]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,avgblur=10[bg];"
                    f"[c_fg]scale={fg_w}:{fg_h}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v_base];"
                )
            else:
                bg_hook_chain = (
                    f"color=c=black:s=1080x1920[bg];"
                    f"[core_clean]scale={fg_w}:{fg_h}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v_base];"
                )

            hook_vf = (
                f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"split=2[c_orig][c_mask];"
                f"[c_mask]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=12:3[c_blur];"
                f"[c_orig][c_blur]overlay={blur_mx}:{blur_my}[core_clean];"
                f"{bg_hook_chain}"
                f"[v_base]{color_filter_str}[v_color];"
                f"[v_color][1:v]overlay={banner_x}:{banner_y}[vout]"
            )

            if has_src_audio:
                cmd_hook_render = [
                    "ffmpeg", "-y",
                    "-ss", f"{hook_start:.3f}", "-t", f"{hook_dur:.3f}",
                    "-i", source_video,
                    "-i", banner_path,
                    "-filter_complex", hook_vf,
                    "-map", "[vout]",
                    "-map", "0:a:0",
                    *audio_opts,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-r", "25", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-avoid_negative_ts", "make_zero",
                    hook_rendered_mp4
                ]
            else:
                cmd_hook_render = [
                    "ffmpeg", "-y",
                    "-ss", f"{hook_start:.3f}", "-t", f"{hook_dur:.3f}",
                    "-i", source_video,
                    "-i", banner_path,
                    "-f", "lavfi", "-t", f"{hook_dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                    "-filter_complex", hook_vf,
                    "-map", "[vout]",
                    "-map", "2:a",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-r", "25", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    hook_rendered_mp4
                ]
            res_hook = subprocess.run(cmd_hook_render, capture_output=True, text=True, creationflags=flags)
            if res_hook.returncode == 0 and os.path.isfile(hook_rendered_mp4):
                has_hook_segment = True
                logger.info("✅ [HOOK RENDER] Đã dựng thành công đoạn Hook đầu tiên kèm âm thanh gốc: %s", hook_rendered_mp4)
            else:
                logger.warning("⚠️ [HOOK RENDER] Lỗi dựng Hook: %s", res_hook.stderr)
        if timing_stats is not None:
            timing_stats["t_hook"] = round(time.time() - t_hook_start, 2)

        # 11. Cắt và Ghép Các Đoạn B-Roll Thân Video
        t_broll_start = time.time()
        body_cut_mp4 = os.path.join(work_dir, "cut_body.mp4")
        body_concat_list = os.path.join(work_dir, "body_clips.txt")

        clip_files = []
        for idx, sc in enumerate(clean_scenes):
            c_file = os.path.join(work_dir, f"broll_{idx:03d}.mp4")
            pts_speed = elastic_pts_map.get(idx, 1.0)
            mirror_filter = ",hflip" if mirror_map.get(idx, False) else ""
            zoom_var_filter = ",crop=iw*0.90:ih*0.90,scale=iw:ih" if sc.get("is_variant", False) else ""

            vf_clip = (
                f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                f"split=2[c_orig][c_mask];"
                f"[c_mask]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=12:3[c_blur];"
                f"[c_orig][c_blur]overlay={blur_mx}:{blur_my},"
                f"setpts={pts_speed:.4f}*PTS{mirror_filter}{zoom_var_filter}"
            )
            cmd_c = [
                "ffmpeg", "-y", "-ss", f"{sc['start']:.3f}", "-to", f"{sc['end']:.3f}",
                "-i", source_video,
                "-vf", vf_clip,
                "-an",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-r", "25", "-pix_fmt", "yuv420p",
                c_file
            ]
            subprocess.run(cmd_c, capture_output=True, creationflags=flags)
            if os.path.isfile(c_file):
                clip_files.append(c_file)

        # Ghi danh sách ghép B-Roll
        with open(body_concat_list, "w", encoding="utf-8") as bf:
            for cf in clip_files:
                bf.write(f"file '{os.path.abspath(cf).replace(chr(92), '/')}'\n")

        # Ghép thân video thô (dạng lõi sạch)
        cmd_concat_body = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", body_concat_list,
            "-c", "copy", body_cut_mp4
        ]
        subprocess.run(cmd_concat_body, capture_output=True, creationflags=flags)
        if timing_stats is not None:
            timing_stats["t_broll"] = round(time.time() - t_broll_start, 2)

        # 12. Dựng Thân Video Hoàn Chỉnh Lên Khung 9:16 + Nền Blur + Voice AI + Title Banner + Subtitle Mới
        t_body_start = time.time()
        body_rendered_mp4 = os.path.join(work_dir, "body_rendered.mp4")
        if options.get("enable_sub", True) and narration_srt and os.path.isfile(narration_srt):
            abs_srt = os.path.abspath(narration_srt).replace('\\', '/')
            if ":" in abs_srt:
                d, p = abs_srt.split(":", 1)
                srt_to_embed = f"{d}\\:{p}"
            else:
                srt_to_embed = abs_srt

            sub_font = options.get("sub_font") or options.get("font_name", "Segoe UI")
            sub_size = options.get("sub_size", 16)
            sub_color = options.get("sub_color", "&HFFFFFF&")
            sub_outline_color = options.get("sub_outline_color", "&H000000&")
            sub_outline = options.get("sub_outline", 3)
            sub_shadow = options.get("sub_shadow", 1)

            basic_sub_style = (
                f"FontName={sub_font},FontSize={sub_size},"
                f"PrimaryColour={sub_color},OutlineColour={sub_outline_color},"
                f"BorderStyle=1,Outline={sub_outline},Shadow={sub_shadow},"
                f"Alignment=2,MarginV={sub_margin_v}"
            )
            sub_filter_chain = f"[v_banner]subtitles='{srt_to_embed}':force_style='{basic_sub_style}'[vout]"
        else:
            sub_filter_chain = "[v_banner]null[vout]"

        # Nền blur 2 đầu từ chính lõi sạch cho phần thân video
        if blur_bg:
            bg_stream = f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,avgblur=10[bg];"
        else:
            bg_stream = f"color=c=black:s=1080x1920[bg];"

        speed_filter = f",setpts={1.0 / video_speed:.4f}*PTS" if abs(video_speed - 1.0) >= 0.01 else ""

        master_vf = (
            f"{bg_stream}"
            f"[0:v]scale={fg_w}:{fg_h}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v_base];"
            f"[v_base]{color_filter_str}{speed_filter}[v_color];"
            f"[v_color][1:v]overlay={banner_x}:{banner_y}[v_banner];"
            f"{sub_filter_chain}"
        )

        cmd_body_final = [
            "ffmpeg", "-y",
            "-i", body_cut_mp4,
            "-i", banner_path,
            "-i", narration_audio if (narration_audio and os.path.isfile(narration_audio)) else body_cut_mp4,
            "-filter_complex", master_vf,
            "-map", "[vout]",
            "-map", "2:a" if (narration_audio and os.path.isfile(narration_audio)) else "0:a?",
            *audio_opts,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
            "-r", "25", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
            "-shortest",
            body_rendered_mp4
        ]
        logger.info(f"🚀 [BODY RENDER] Thực thi đóng gói thân video 9:16: {' '.join(cmd_body_final)}")
        subprocess.run(cmd_body_final, capture_output=True, text=True, creationflags=flags)
        if timing_stats is not None:
            timing_stats["t_body"] = round(time.time() - t_body_start, 2)

        # 13. NỐI HOÀN CHỈNH: HOOK Ở ĐẦU TIÊN (00:00) + THÂN VIDEO TIẾP THEO
        t_concat_start = time.time()
        if has_hook_segment and os.path.isfile(hook_rendered_mp4) and os.path.isfile(body_rendered_mp4):
            logger.info("🔗 [MASTER CONCAT] Nối Hook gốc ở ĐẦU TIÊN (00:00) + Thân video B-Roll thuyết minh tiếp theo...")
            final_concat_list = os.path.join(work_dir, "final_segments.txt")
            with open(final_concat_list, "w", encoding="utf-8") as ff:
                ff.write(f"file '{os.path.abspath(hook_rendered_mp4).replace(chr(92), '/')}'\n")
                ff.write(f"file '{os.path.abspath(body_rendered_mp4).replace(chr(92), '/')}'\n")

            cmd_final_merge = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", final_concat_list,
                "-c", "copy",
                output_path
            ]
            res_merge = subprocess.run(cmd_final_merge, capture_output=True, text=True, creationflags=flags)
            if res_merge.returncode != 0 or not os.path.isfile(output_path):
                # Fallback filter_complex concat
                cmd_fallback_merge = [
                    "ffmpeg", "-y",
                    "-i", hook_rendered_mp4,
                    "-i", body_rendered_mp4,
                    "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                    "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-c:a", "aac", "-b:a", "192k",
                    output_path
                ]
                res_merge = subprocess.run(cmd_fallback_merge, capture_output=True, text=True, creationflags=flags)
        elif os.path.isfile(body_rendered_mp4):
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except Exception:
                    pass
            shutil.copy2(body_rendered_mp4, output_path)

        if timing_stats is not None:
            timing_stats["t_concat"] = round(time.time() - t_concat_start, 2)

        if os.path.isfile(output_path):
            logger.info(f"🎉 [MASTER RENDER] XUẤT VIDEO THÀNH CÔNG: {output_path}")
            if progress_cb:
                progress_cb("done", f"Xuất video hoàn tất: {os.path.basename(output_path)}")
            return True
        else:
            logger.error("❌ [MASTER RENDER] Không tìm thấy file thành phẩm sau render.")
            return False
