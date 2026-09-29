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
import time
from typing import Callable, Optional, Dict, Any, List, Tuple

from capcut_filters import apply_look, build_adjust_filters, extra_ffmpeg_tail
from font_manager import FontManager
from market_profiles import get_market_profile, MARKET_PROFILES
from editor_processor import EditorProcessor

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
    def detect_scene_cuts_ffmpeg(
        cls,
        video_path: str,
        start_sec: float = 0.0,
        end_sec: Optional[float] = None,
        min_gap: float = 2.0,
        max_gap: float = 5.2
    ) -> List[float]:
        """
        Dò tất cả các điểm chuyển cảnh (scene cuts) thực tế của video bằng FFmpeg scene detection.
        Đảm bảo các phân đoạn B-roll có độ dài tự nhiên [min_gap, max_gap] (2.0s - 5.2s),
        không bị cắt vụn cũng không bị dính cục video dài của đối thủ.
        """
        total_dur = cls.get_video_duration(video_path)
        if end_sec is None or end_sec > total_dur or end_sec <= start_sec:
            end_sec = total_dur

        scan_dur = max(1.0, end_sec - start_sec)
        detected_points = []
        flags = 0x08000000 if os.name == "nt" else 0

        try:
            cmd = [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-ss", f"{start_sec:.3f}",
                "-t", f"{scan_dur:.3f}",
                "-i", video_path,
                "-vf", "scale=160:90,select='gt(scene,0.20)',metadata=print",
                "-f", "null", "-"
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
            for x in re.findall(r"pts_time:([0-9.]+)", res.stderr or ""):
                sec = round(start_sec + float(x), 3)
                if start_sec + 0.8 <= sec <= end_sec - 0.8:
                    detected_points.append(sec)
        except Exception as e:
            logger.warning(f"⚠️ [SCENE DETECT] Lỗi dò scene bằng FFmpeg: {e}")

        # Lọc các điểm quá sát nhau (< min_gap)
        clean_cuts = [round(start_sec, 3)]
        for pt in sorted(detected_points):
            if pt - clean_cuts[-1] >= min_gap:
                clean_cuts.append(pt)

        # Nếu khoảng cách giữa 2 điểm cắt quá dài (> max_gap), bổ sung các điểm cắt phụ tự nhiên
        final_cuts = [clean_cuts[0]]
        for pt in clean_cuts[1:]:
            while pt - final_cuts[-1] > max_gap:
                sub_cut = round(final_cuts[-1] + random.uniform(3.0, 4.5), 3)
                if pt - sub_cut >= min_gap:
                    final_cuts.append(sub_cut)
                else:
                    break
            final_cuts.append(pt)

        # Kiểm tra đoạn cuối cùng đến end_sec
        while end_sec - final_cuts[-1] > max_gap:
            sub_cut = round(final_cuts[-1] + random.uniform(3.0, 4.5), 3)
            if end_sec - sub_cut >= min_gap:
                final_cuts.append(sub_cut)
            else:
                break

        if end_sec - final_cuts[-1] >= 1.0:
            final_cuts.append(round(end_sec, 3))
        else:
            final_cuts[-1] = round(end_sec, 3)

        res_cuts = sorted(list(set(final_cuts)))
        logger.info(f"🎬 [SCENE CUTS FFMPEG] Đã xác định {len(res_cuts)-1} phân đoạn cảnh tự nhiên trong khoảng [{start_sec:.1f}s - {end_sec:.1f}s]")
        return res_cuts

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

4. FRAME-ACCURATE DENSE SCENE SEGMENTATION & VISUAL TAGGING:
   - Segment the remaining footage into clean B-Roll shots down to frame-accurate floating-point timestamps. Aim for AT LEAST 6 to 12 distinct individual scenes (S1, S2, S3... each 2.0s to 5.0s long) rather than a few giant chunks.
   - For EACH scene, provide:
     * "id": "S1", "S2", "S3"...
     * "clean_range": [start_sec, end_sec] (frame-accurate floating-point seconds)
     * "visual_summary": exact action, subject, facial expression, mood, objects shown
     * "transition_type": "none" (hard cut - DO NOT cut out any frames, cutout_sec: 0.0), or "white_flash"/"zoom_glitch"/"fade_black" (micro cut 0.15s - 0.22s).

5. CONTINUOUS NARRATION & MANDATORY ANTI-DUPLICATE B-ROLL RE-ORDERING:
   - CRITICAL MONETIZATION CONSTRAINT: The final video MUST be strictly LONGER THAN 60 SECONDS (Target: 62.0s to 70.0s) to qualify for TikTok Creator Rewards.
   - Word budget: Write approx 160 to 195 spoken words in {lang_name} ({locale_code}). Under NO circumstances write fewer than 155 words! The spoken duration MUST be at least 62 seconds long.
   - CRITICAL ANTI-COPYRIGHT HASH RULE (BẮT BUỘC ĐẢO CẢNH CHỐNG QUÉT BẢN QUYỀN):
     * UNDER NO CIRCUMSTANCES can you keep the original chronological scene order (e.g. S1 -> S2 -> S3 -> S4 is STRICTLY FORBIDDEN and will cause copyright strikes).
     * You MUST heavily rearrange and remix the scene order in "remix_storyboard" (e.g. S3 -> S1 -> S5 -> S2 -> S6 -> S4) so that adjacent scenes are completely scrambled from the original video.
     * Match each rewritten narration sentence to the NEAREST SEMANTIC MEANING, mood, or visual action of the assigned scene.
   - Provide "title_line1" and "title_line2" in {lang_name}.

OUTPUT FORMAT (JSON ONLY):
{{
  "layout_geometry": {{
    "has_top_title_or_blur": true,
    "core_crop_normalized": {{"ymin": 0.20, "ymax": 0.80}},
    "sub_blur_normalized": {{"ymin": 0.68, "ymax": 0.76, "xmin": 0.05, "xmax": 0.95}}
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
        return cls._create_default_fallback_plan(dur, target_market, source_video=video_path)

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
    def _create_default_fallback_plan(cls, dur: float, target_market: str, source_video: Optional[str] = None) -> Dict[str, Any]:
        """Tạo plan mặc định khi không có kết nối AI, tự động dò scene cuts thật và đảo cảnh xen kẽ triệt để."""
        market = get_market_profile(target_market)
        h_end = min(4.0, max(2.5, dur * 0.12))
        rem_dur = max(2.0, dur - h_end)

        # 1. Dò scene cuts thật từ video nếu có file nguồn
        raw_cuts = []
        if source_video and os.path.isfile(source_video):
            raw_cuts = cls.detect_scene_cuts_ffmpeg(source_video, start_sec=h_end, end_sec=dur, min_gap=2.2, max_gap=4.8)

        if not raw_cuts or len(raw_cuts) < 3:
            # Chia đều thành 5-8 cảnh tự nhiên (mỗi cảnh ~ 3.2s - 4.5s)
            step = 3.5
            cur = h_end
            raw_cuts = [cur]
            while cur + step < dur - 1.0:
                cur += step
                raw_cuts.append(round(cur, 3))
            raw_cuts.append(round(dur, 3))

        scenes = []
        for s_idx in range(len(raw_cuts) - 1):
            st = raw_cuts[s_idx]
            en = raw_cuts[s_idx + 1]
            if en - st >= 0.8:
                scenes.append({
                    "id": f"S{s_idx + 1}",
                    "clean_range": [st, en],
                    "visual_summary": f"Dramatic action sequence {s_idx + 1}",
                    "has_text": False,
                    "transition_type": "none",
                    "cutout_sec": 0.0
                })

        # 2. Đảo thứ tự cảnh xen kẽ (Interleaved Narrative Shuffle) chống quét bản quyền 100%
        # Chia thành 2 nửa và ghép xen kẽ: B[0], A[0], B[1], A[1]...
        n_scenes = len(scenes)
        mid = n_scenes // 2
        half_a = [s["id"] for s in scenes[:mid]]
        half_b = [s["id"] for s in scenes[mid:]]
        shuffled_ids = []
        for i in range(max(len(half_a), len(half_b))):
            if i < len(half_b):
                shuffled_ids.append(half_b[i])
            if i < len(half_a):
                shuffled_ids.append(half_a[i])

        if len(shuffled_ids) < n_scenes:
            remaining = [s["id"] for s in scenes if s["id"] not in shuffled_ids]
            shuffled_ids.extend(remaining)

        # Mẫu câu kịch bản kể chuyện cuốn hút
        sample_sentences = [
            "Was in diesem entscheidenden Augenblick geschah, übertraf jede kühnste Erwartung.",
            "Die Anspannung erreichte sofort den Höhepunkt, als die Beteiligten reagierten.",
            "Jede einzelne Sekunde zählte, während die dramatische Wende ihren Lauf nahm.",
            "Niemand im Raum konnte seinen Augen trauen bei diesem unerwarteten Vorfall.",
            "Die Details wurden erst im Nachhinein klar und sorgten für riesiges Aufsehen.",
            "Ein unvergesslicher Moment, der die Gemüter der Zuschauer weltweit spaltet."
        ]

        storyboard = []
        for seg_idx, sc_id in enumerate(shuffled_ids, 1):
            sent_text = sample_sentences[(seg_idx - 1) % len(sample_sentences)]
            sc_obj = next((s for s in scenes if s["id"] == sc_id), None)
            t_dur = (sc_obj["clean_range"][1] - sc_obj["clean_range"][0]) if sc_obj else 4.0
            storyboard.append({
                "segment_index": seg_idx,
                "scene_id": sc_id,
                "narration_sentence": sent_text,
                "target_duration_sec": round(t_dur, 2)
            })

        full_script = " ".join([s["narration_sentence"] for s in storyboard])

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
            "scenes": scenes,
            "remix_storyboard": storyboard,
            "rewritten_narration": {
                "language": market.get("locale", "de-DE"),
                "title_line1": "UNGESCHMINKTE WAHRHEIT",
                "title_line2": "DER REALE VORFALL",
                "script_text": full_script
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

    @staticmethod
    def _to_ass_color(raw_val: Any, default_ass: str = "&H0000FFFF") -> str:
        """Chuẩn hóa màu ASS AABBGGRR an toàn từ cả định dạng Web RGB (#RRGGBB) lẫn ASS Hex (&HBBGGRR&)."""
        if not raw_val:
            return default_ass
        s = str(raw_val).strip()
        if s.startswith("#") and len(s) == 7:
            # Web RGB #RRGGBB -> ASS AABBGGRR: &H00BBGGRR (00 = opaque)
            r, g, b = s[1:3], s[3:5], s[5:7]
            return f"&H00{b}{g}{r}".upper()
        if s.startswith("&H") or s.startswith("&h"):
            clean = s[2:].rstrip("&")
            if len(clean) == 6:
                return f"&H00{clean}".upper()
            elif len(clean) == 8:
                return f"&H{clean}".upper()
        return default_ass

    @classmethod
    def convert_srt_to_ass(
        cls,
        srt_file: str,
        ass_file: str,
        font_name: str = "Arial",
        font_size: int = 42,
        primary_color: str = "&H0000FFFF",  # TikTok Yellow (AABBGGRR: 00=opaque, 00=blue, FF=green, FF=red)
        outline_color: str = "&H00000000",
        outline_width: int = 4,
        shadow_dist: int = 1,
        margin_v: int = 560
    ) -> bool:
        """Chuyển đổi SRT sang ASS chuẩn 1080x1920 PlayRes để hiển thị phụ đề sắc nét, đúng tọa độ 100%."""
        try:
            if not srt_file or not os.path.isfile(srt_file):
                return False
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
                srt_content = f.read()

            header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_color},&H000000FF,{outline_color},&H80000000,1,0,0,0,100,100,0,0,1,{outline_width},{shadow_dist},2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
            events = []
            blocks = re.split(r'\n\s*\n', srt_content.strip())
            for b in blocks:
                lines = [l.strip() for l in b.splitlines() if l.strip()]
                if len(lines) >= 3:
                    m = re.match(r'(\d+):(\d+):(\d+),(\d+)\s*-->\s*(\d+):(\d+):(\d+),(\d+)', lines[1])
                    if m:
                        g = m.groups()
                        st = f"{int(g[0])}:{g[1]}:{g[2]}.{g[3][:2]}"
                        en = f"{int(g[4])}:{g[5]}:{g[6]}.{g[7][:2]}"
                        txt = " ".join(lines[2:])
                        events.append(f"Dialogue: 0,{st},{en},Default,,0,0,0,,{txt}")

            with open(ass_file, "w", encoding="utf-8") as f:
                f.write(header + "\n".join(events) + "\n")
            return True
        except Exception as e:
            logger.warning(f"⚠️ [ASS SUB] Không chuyển đổi được sang ASS: {e}")
            return False

    @classmethod
    def auto_detect_core_boundaries(
        cls,
        source_video: str,
        gemini_ymin: float = 0.20,
        gemini_ymax: float = 0.80
    ) -> Tuple[float, float]:
        """
        Tự động dò tìm đường biên phân cách (seam border line) của video lõi sạch 16:9
        giữa dải mờ trên và dải mờ dưới bằng OpenCV Gradient/Diff.
        Cắt bỏ chính xác 100% 2 vệt viền/đường ngang phân cách mỏng mà mắt người thấy.
        """
        try:
            import cv2
            import numpy as np

            cap = cv2.VideoCapture(source_video)
            if not cap.isOpened():
                return (max(0.0, gemini_ymin + 0.025), min(1.0, gemini_ymax - 0.025))

            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 100)
            sample_indices = [int(total_frames * 0.15), int(total_frames * 0.30), int(total_frames * 0.50)]
            
            top_seams = []
            bot_seams = []
            frame_h = 0

            for f_idx in sample_indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, f_idx)
                ret, frame = cap.read()
                if not ret or frame is None:
                    continue
                h, w, _ = frame.shape
                frame_h = h

                y_top_start, y_top_end = int(0.18 * h), int(0.38 * h)
                y_bot_start, y_bot_end = int(0.62 * h), int(0.82 * h)

                try:
                    top_cands = [(y, float(np.mean(np.abs(frame[y].astype(float) - frame[y-1].astype(float))))) for y in range(y_top_start, y_top_end)]
                    best_top = max(top_cands, key=lambda x: x[1])
                    if best_top[1] > 20.0:
                        top_seams.append(best_top[0])

                    bot_cands = [(y, float(np.mean(np.abs(frame[y].astype(float) - frame[y-1].astype(float))))) for y in range(y_bot_start, y_bot_end)]
                    best_bot = max(bot_cands, key=lambda x: x[1])
                    if best_bot[1] > 20.0:
                        bot_seams.append(best_bot[0])
                except Exception:
                    pass

            cap.release()

            if frame_h > 0 and top_seams:
                avg_top = float(np.median(top_seams))
                clean_ymin = round((avg_top + 6.0) / frame_h, 4)
            else:
                clean_ymin = round(max(0.0, gemini_ymin + 0.025), 4)

            if frame_h > 0 and bot_seams:
                avg_bot = float(np.median(bot_seams))
                clean_ymax = round((avg_bot - 6.0) / frame_h, 4)
            else:
                clean_ymax = round(min(1.0, gemini_ymax - 0.025), 4)

            logger.info(f"✂️ [CORE SEAM DETECT] Tọa độ cắt lõi triệt tiêu đường viền: ymin={clean_ymin:.4f}, ymax={clean_ymax:.4f}")
            return clean_ymin, clean_ymax
        except Exception as e:
            logger.warning(f"⚠️ [CORE SEAM DETECT] Lỗi dò đường viền: {e}")
            return (round(max(0.0, gemini_ymin + 0.025), 4), round(min(1.0, gemini_ymax - 0.025), 4))

    @classmethod
    def match_and_reorder_broll_by_semantic(
        cls,
        clean_scenes: List[Dict[str, Any]],
        gemini_plan: Dict[str, Any],
        target_body_dur: float = 58.0
    ) -> List[Dict[str, Any]]:
        """
        So sánh nội dung của từng cảnh B-Roll (visual_summary do Gemini trả về)
        với nội dung của từng câu trong giọng đọc AI mới để sắp xếp B-Roll khớp theo ngữ nghĩa,
        ĐẢO CẢNH triệt để và phá vỡ 100% thứ tự cũ của clip gốc.
        """
        if not clean_scenes:
            return []

        narration_info = gemini_plan.get("rewritten_narration", {})
        script_text = str(narration_info.get("script_text") or "").strip()
        remix_storyboard = gemini_plan.get("remix_storyboard", [])

        # 1. Trích xuất danh sách các câu thoại của giọng đọc AI mới
        sentences = []
        if remix_storyboard and isinstance(remix_storyboard, list):
            for seg in remix_storyboard:
                s_txt = str(seg.get("narration_sentence") or "").strip()
                if s_txt:
                    sentences.append(s_txt)
        if not sentences and script_text:
            sentences = [s.strip() for s in re.split(r'[.!?\n]+', script_text) if len(s.strip()) > 3]
        if not sentences:
            sentences = [s.get("visual_summary", f"Scene {i+1}") for i, s in enumerate(clean_scenes)]

        def _tokenize(text: str) -> set:
            raw = re.sub(r'[^\w\s]', ' ', (text or "").lower())
            stops = {
                "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
                "this", "that", "it", "for", "with", "on", "at", "from", "by", "about",
                "der", "die", "das", "und", "oder", "von", "zu", "in", "ist", "sind", "war",
                "ein", "eine", "einer", "für", "mit", "auf", "an", "aus", "bei", "nicht",
                "và", "của", "là", "các", "một", "có", "không", "được", "cho", "với", "từ",
                "những", "này", "đó", "thì", "đã", "sẽ", "trong", "để", "ra", "lại", "rồi"
            }
            return {w for w in raw.split() if len(w) >= 3 and w not in stops}

        THEMES = {
            "anger_fight": {"wut", "angriff", "streit", "kampf", "schlagen", "angry", "fight", "punch", "slap", "hit", "shout", "argue", "giận", "đánh", "cãi", "tát", "xô", "mắng"},
            "shock_surprise": {"schock", "überraschung", "plötzlich", "unerwartet", "shock", "surprise", "sudden", "unexpected", "astonish", "sốc", "bất ngờ", "kinh ngạc", "ngỡ ngàng", "sững sờ"},
            "sad_cry": {"traurig", "weinen", "tränen", "verlust", "sad", "cry", "tears", "loss", "grief", "khóc", "buồn", "nước mắt", "đau đớn", "tuyệt vọng"},
            "speed_motion": {"schnell", "geschwindigkeit", "auto", "rennen", "fahren", "fast", "speed", "car", "run", "drive", "drift", "chạy", "xe", "tốc độ", "lao", "phóng"},
            "authority_police": {"polizei", "offizier", "verhaftung", "gesetz", "police", "officer", "arrest", "law", "cảnh sát", "công an", "bắt", "còng tay"},
            "reveal_secret": {"geheimnis", "wahrheit", "entdecken", "secret", "truth", "reveal", "discover", "bí mật", "sự thật", "tiết lộ", "phát hiện", "vỡ lở"}
        }

        # 2. Chuẩn bị thông tin cảnh B-Roll kèm keywords
        scenes_meta = []
        for idx, sc in enumerate(clean_scenes):
            v_desc = f"{sc.get('visual_summary', '')} scene_{sc.get('id', '')}"
            scenes_meta.append({
                "orig_idx": idx,
                "scene": sc,
                "keywords": _tokenize(v_desc),
                "summary_lower": v_desc.lower()
            })

        # 3. So khớp từng câu thoại giọng đọc AI mới với cảnh B-Roll
        ordered_scenes = []
        used_ids = set()
        last_orig_idx = -999

        for s_idx, sentence in enumerate(sentences):
            sent_kw = _tokenize(sentence)
            sent_lower = sentence.lower()

            best_item = None
            best_score = -999.0

            for cand in scenes_meta:
                sc_id = cand["scene"]["id"]
                is_used = sc_id in used_ids
                # Tránh chọn cảnh kế tiếp ngay sau cảnh cũ của clip đối thủ
                is_adjacent_old = (cand["orig_idx"] == last_orig_idx + 1)
                is_same_as_last = (cand["orig_idx"] == last_orig_idx)

                # Trùng từ khóa
                kw_match = len(sent_kw & cand["keywords"])

                # Đồng cảm xúc / chủ đề
                theme_score = 0.0
                for theme, words in THEMES.items():
                    if any(w in sent_lower for w in words) and any(w in cand["summary_lower"] for w in words):
                        theme_score += 3.0

                score = (kw_match * 2.5) + theme_score
                if not is_used:
                    score += 6.0  # Ưu tiên cảnh mới chưa dùng
                if is_adjacent_old:
                    score -= 5.0  # Phạt nặng cảnh đi liền sau theo thứ tự cũ
                if is_same_as_last:
                    score -= 10.0 # Cấm lặp lại ngay cảnh vừa chiếu

                if score > best_score:
                    best_score = score
                    best_item = cand

            if best_item:
                sc_copy = dict(best_item["scene"])
                if best_item["scene"]["id"] in used_ids:
                    sc_copy["is_variant"] = True
                    sc_copy["id"] = f"{sc_copy['id']}_v{s_idx+1}"
                ordered_scenes.append(sc_copy)
                used_ids.add(best_item["scene"]["id"])
                last_orig_idx = best_item["orig_idx"]

        # Bổ sung các cảnh sạch còn lại chưa dùng xen kẽ vào
        remaining = [s for s in clean_scenes if s["id"] not in used_ids]
        if remaining:
            random.shuffle(remaining)
            for rem in remaining:
                pos = random.randint(0, len(ordered_scenes)) if ordered_scenes else 0
                ordered_scenes.insert(pos, dict(rem))

        # Kiểm tra tính tuần tự: nếu chẳng may vẫn bị chuỗi tăng dần, ép đảo xen kẽ
        def _check_sequential(seq):
            if len(seq) <= 2:
                return True
            starts = [s["start"] for s in seq]
            return all(starts[i] <= starts[i+1] for i in range(len(starts) - 1))

        if _check_sequential(ordered_scenes) and len(ordered_scenes) >= 3:
            mid = len(ordered_scenes) // 2
            h1, h2 = ordered_scenes[:mid], ordered_scenes[mid:]
            inter = []
            for i in range(max(len(h1), len(h2))):
                if i < len(h2):
                    inter.append(h2[i])
                if i < len(h1):
                    inter.append(h1[i])
            ordered_scenes = inter

        # 4. Kéo dài thời lượng bằng Variant Looping xen kẽ (khi tổng < target_body_dur)
        cur_dur = sum(s["dur"] for s in ordered_scenes)
        if cur_dur < target_body_dur:
            deficit = target_body_dur - cur_dur
            logger.info("⚡ [MONETIZATION >60s] Tổng cảnh (%.1fs) thiếu %.1fs để đạt chuẩn >60s. Thêm B-Roll biến thể xen kẽ...", cur_dur, deficit)
            pool = [s for s in ordered_scenes if not s.get("has_text", False)] or list(ordered_scenes)
            random.shuffle(pool)
            v_idx = 1
            var_clips = []
            while cur_dur < target_body_dur + 2.0 and pool:
                for sc_cand in pool:
                    v_item = dict(sc_cand)
                    v_item["id"] = f"{sc_cand['id']}_var{v_idx}"
                    v_item["is_variant"] = True
                    var_clips.append(v_item)
                    cur_dur += v_item["dur"]
                    v_idx += 1
                    if cur_dur >= target_body_dur + 2.0:
                        break

            # Chèn xen kẽ vào các vị trí lẻ
            final_body = []
            v_ptr = 0
            for idx, main_sc in enumerate(ordered_scenes):
                final_body.append(main_sc)
                if (idx % 2 == 1) and v_ptr < len(var_clips):
                    final_body.append(var_clips[v_ptr])
                    v_ptr += 1
            while v_ptr < len(var_clips):
                final_body.append(var_clips[v_ptr])
                v_ptr += 1
            ordered_scenes = final_body

        logger.info("🎬 [SEMANTIC B-ROLL] Đã so sánh nội dung giọng đọc AI với %d cảnh B-Roll, đảo lộn hoàn toàn thứ tự clip gốc!", len(ordered_scenes))
        return ordered_scenes

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
        use_manual_crop = bool(options.get("use_crop", False))
        if use_manual_crop and int(options.get("crop_w", 0)) > 0 and int(options.get("crop_h", 0)) > 0:
            base_w = float(options.get("base_w", orig_w) or orig_w)
            base_h = float(options.get("base_h", orig_h) or orig_h)
            scale_x_ratio = orig_w / base_w if base_w > 0 else 1.0
            scale_y_ratio = orig_h / base_h if base_h > 0 else 1.0

            crop_x = int(round(float(options.get("crop_x", 0)) * scale_x_ratio))
            crop_y = int(round(float(options.get("crop_y", 0)) * scale_y_ratio))
            crop_w = int(round(float(options.get("crop_w", orig_w)) * scale_x_ratio))
            crop_h = int(round(float(options.get("crop_h", orig_h)) * scale_y_ratio))

            crop_x = max(0, min(orig_w - 2, crop_x))
            crop_y = max(0, min(orig_h - 2, crop_y))
            crop_w = max(32, min(orig_w - crop_x, crop_w))
            crop_h = max(32, min(orig_h - crop_y, crop_h))
            crop_w -= crop_w % 2
            crop_h -= crop_h % 2
            ymin = crop_y / max(1.0, float(orig_h))
            ymax = (crop_y + crop_h) / max(1.0, float(orig_h))
            logger.info("✂️ [CROP STUDIO] Áp dụng thông số Crop thủ công từ Studio: x=%d, y=%d, w=%d, h=%d (ymin=%.3f, ymax=%.3f)", crop_x, crop_y, crop_w, crop_h, ymin, ymax)
        else:
            geom = gemini_plan.get("layout_geometry", {})
            crop_norm = geom.get("core_crop_normalized", {"ymin": 0.0, "ymax": 1.0})
            g_ymin = max(0.0, min(0.4, float(crop_norm.get("ymin", 0.0))))
            g_ymax = max(0.6, min(1.0, float(crop_norm.get("ymax", 1.0))))

            # Tự động dò và triệt tiêu 2 đường viền/mép ngang mỏng của video đối thủ bằng OpenCV
            if options.get("auto_clean_core_crop", True):
                ymin, ymax = cls.auto_detect_core_boundaries(source_video, g_ymin, g_ymax)
            else:
                ymin, ymax = g_ymin, g_ymax

            crop_y = int(orig_h * ymin)
            crop_h = int(orig_h * (ymax - ymin))
            crop_w = orig_w
            crop_x = 0
            logger.info("✂️ [CROP AUTO] Áp dụng AI + Seam Detector tự động: ymin=%.3f, ymax=%.3f (h=%d, y=%d)", ymin, ymax, crop_h, crop_y)

        # 2. Tính toán Tọa độ Bôi Mờ Sub Cũ (Sub Blur Zone)
        use_manual_blur = bool(options.get("use_blur_mask", False))
        auto_blur = bool(options.get("auto_blur_sub_part", True))
        has_blur_mask = False

        if use_manual_blur and int(options.get("blur_mask_w", 0)) > 0 and int(options.get("blur_mask_h", 0)) > 0:
            base_w = float(options.get("base_w", orig_w) or orig_w)
            base_h = float(options.get("base_h", orig_h) or orig_h)
            scale_x_ratio = orig_w / base_w if base_w > 0 else 1.0
            scale_y_ratio = orig_h / base_h if base_h > 0 else 1.0

            raw_bmx = float(options.get("blur_mask_x", 0)) * scale_x_ratio
            raw_bmy = float(options.get("blur_mask_y", 0)) * scale_y_ratio
            raw_bmw = float(options.get("blur_mask_w", crop_w)) * scale_x_ratio
            raw_bmh = float(options.get("blur_mask_h", 60)) * scale_y_ratio

            blur_mx = int(round(max(0, raw_bmx - crop_x)))
            blur_my = int(round(max(0, raw_bmy - crop_y)))
            blur_mw = int(round(min(crop_w - blur_mx, raw_bmw)))
            blur_mh = int(round(min(crop_h - blur_my, raw_bmh)))
            blur_mw -= blur_mw % 2
            blur_mh -= blur_mh % 2
            blur_mw = max(16, blur_mw)
            blur_mh = max(16, blur_mh)
            rel_sub_ymin = blur_my / max(1.0, float(crop_h))
            rel_sub_ymax = (blur_my + blur_mh) / max(1.0, float(crop_h))
            has_blur_mask = True
            logger.info("🌫️ [BLUR MASK STUDIO] Áp dụng vùng che mờ thủ công từ Crop Studio: x=%d, y=%d, w=%d, h=%d", blur_mx, blur_my, blur_mw, blur_mh)
        elif auto_blur:
            geom = gemini_plan.get("layout_geometry", {})
            sub_norm = geom.get("sub_blur_normalized", {"ymin": 0.68, "ymax": 0.76, "xmin": 0.05, "xmax": 0.95})
            sub_orig_ymin = float(sub_norm.get("ymin", 0.68))
            sub_orig_ymax = float(sub_norm.get("ymax", 0.76))
            crop_span = max(0.01, ymax - ymin)

            if sub_orig_ymin < ymin or (sub_orig_ymin - ymin) / crop_span < 0.60:
                rel_sub_ymin = 0.82
                rel_sub_ymax = 0.98
            else:
                rel_sub_ymin = max(0.0, min(0.95, (sub_orig_ymin - ymin) / crop_span))
                rel_sub_ymax = max(rel_sub_ymin + 0.05, min(1.0, (sub_orig_ymax - ymin) / crop_span))

            blur_mx = int(crop_w * float(sub_norm.get("xmin", 0.05)))
            blur_mw = int(crop_w * (float(sub_norm.get("xmax", 0.95)) - float(sub_norm.get("xmin", 0.05))))
            blur_my = int(round(crop_h * rel_sub_ymin))
            blur_mh = int(round(crop_h * (rel_sub_ymax - rel_sub_ymin)))
            blur_mh = max(30, blur_mh)
            blur_mw -= blur_mw % 2
            blur_mh -= blur_mh % 2
            has_blur_mask = True
            logger.info("🌫️ [BLUR MASK AUTO] Tự động che mờ sub cũ: x=%d, y=%d, w=%d, h=%d (rel=%.2f-%.2f)", blur_mx, blur_my, blur_mw, blur_mh, rel_sub_ymin, rel_sub_ymax)
        else:
            rel_sub_ymin = 0.82
            rel_sub_ymax = 0.98
            blur_mx = blur_my = 0
            blur_mw = blur_mh = 0
            has_blur_mask = False
            logger.info("🌫️ [BLUR MASK] Tắt tính năng che mờ.")

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

        # Bổ sung Scene Cuts bằng FFmpeg nếu AI không chia đủ cảnh hoặc cảnh quá dài (> 5.2s)
        total_vid_dur = cls.get_video_duration(source_video)
        needs_ffmpeg_split = (len(clean_scenes) < 4) or any(s["dur"] > 5.2 for s in clean_scenes)
        if needs_ffmpeg_split:
            body_start_sec = hook_end if (options.get("keep_original_hook", True) and hook_dur >= 0.5) else 0.0
            cuts = cls.detect_scene_cuts_ffmpeg(source_video, start_sec=body_start_sec, end_sec=total_vid_dur, min_gap=2.0, max_gap=4.8)
            if len(cuts) >= 3:
                f_scenes = []
                for s_i in range(len(cuts) - 1):
                    c_st, c_en = cuts[s_i], cuts[s_i + 1]
                    if c_en - c_st >= 1.0:
                        s_id = f"SC{s_i + 1}"
                        sc_item = {
                            "id": s_id,
                            "start": c_st,
                            "end": c_en,
                            "dur": c_en - c_st,
                            "has_text": False,
                            "visual_summary": f"Detected scene {s_i + 1}"
                        }
                        f_scenes.append(sc_item)
                        scenes_by_id[s_id] = sc_item
                if len(f_scenes) >= len(clean_scenes):
                    logger.info("🎬 [B-ROLL SPLIT] Đã phân tách video nguồn thành %d cảnh B-Roll tự nhiên bằng FFmpeg (2.0s - 4.8s)", len(f_scenes))
                    clean_scenes = f_scenes

        # 4. SO SÁNH NỘI DUNG GIỌNG ĐỌC AI VỚI TỪNG CẢNH B-ROLL ĐỂ SẮP XẾP VÀ ĐẢO CẢNH
        # Không bao giờ ghép y hệt clip gốc: Cảnh nào khớp nghĩa với câu thoại AI nhất sẽ được đưa vào, phá vỡ 100% video hash cũ
        min_monetization_total = 62.0
        required_body_dur = max(min_monetization_total - hook_dur, audio_duration, 58.0)
        target_body_dur = max(58.0, required_body_dur)

        clean_scenes = cls.match_and_reorder_broll_by_semantic(
            clean_scenes=clean_scenes,
            gemini_plan=gemini_plan,
            target_body_dur=target_body_dur
        )
        total_clean_dur = sum(s["dur"] for s in clean_scenes) or 10.0

        # 6. Điều Tốc B-Roll Đàn Hồi Ngẫu Nhiên (Stochastic Elastic Speed Matching)
        elastic_pts_map = {}
        if total_clean_dur < target_body_dur and options.get("elastic_broll_speed", True) and clean_scenes:
            sample_size = max(1, int(len(clean_scenes) * random.uniform(0.5, 0.8)))
            stretched_indices = set(random.sample(range(len(clean_scenes)), min(sample_size, len(clean_scenes))))
            for idx in range(len(clean_scenes)):
                if idx in stretched_indices:
                    elastic_pts_map[idx] = round(random.uniform(1.025, 1.075), 4)
                else:
                    elastic_pts_map[idx] = 1.0
            logger.info("⏱️ [ELASTIC SPEED] Đã chọn ngẫu nhiên %d/%d cảnh để giãn nhẹ tốc độ.", len(stretched_indices), len(clean_scenes))
        else:
            for idx in range(len(clean_scenes)):
                elastic_pts_map[idx] = 1.0

        # 7. Lật Gương Phản Chiếu Ngẫu Nhiên (Chuẩn theo Chế độ 1 Tóm tắt):
        mirror_map = {idx: False for idx in range(len(clean_scenes))}
        if options.get("mirror_broll", True) and clean_scenes:
            mirror_count = min(len(clean_scenes), max(1, int(round(len(clean_scenes) * 0.45))))
            for m_idx in random.sample(range(len(clean_scenes)), mirror_count):
                mirror_map[m_idx] = True
            for idx, sc in enumerate(clean_scenes):
                if sc.get("is_variant", False):
                    mirror_map[idx] = True
            logger.info("🪞 [MIRROR] Lật ngẫu nhiên %d/%d cảnh chuẩn như Chế độ 1 (Sub mới và Title overlay lên trên cùng).", mirror_count, len(clean_scenes))

        # 7. Xây dựng Title Banner Mới Bằng PIL
        from editor_processor import EditorProcessor
        enable_title = bool(options.get("enable_title", True) and options.get("show_title", True))
        has_banner = False
        banner_path = ""
        banner_x = 0
        banner_y = int(options.get("title_y_pos", 260))

        if enable_title:
            custom_title = str(options.get("title_text") or "").strip()
            if custom_title:
                t_line1, t_line2 = EditorProcessor.split_banner_title(custom_title)
                logger.info("🏷️ [TITLE STUDIO] Áp dụng tiêu đề tùy chỉnh từ Title Studio: '%s' / '%s'", t_line1, t_line2)
            else:
                script_info = gemini_plan.get("rewritten_narration", {})
                t_line1 = script_info.get("title_line1", options.get("title_line1", "REMIX SPECIAL"))
                t_line2 = script_info.get("title_line2", options.get("title_line2", ""))
                logger.info("🏷️ [TITLE AI] Áp dụng tiêu đề từ kịch bản AI: '%s' / '%s'", t_line1, t_line2)

            if t_line1 and not t_line2 and len(t_line1.split()) >= 3:
                words = t_line1.split()
                mid = len(words) // 2
                t_line1 = " ".join(words[:mid])
                t_line2 = " ".join(words[mid:])
                logger.info("🏷️ [TITLE BANNER 2 MÀU] Đã chia 2 dòng để hiển thị cả 2 màu chữ: '%s' (Màu 1) / '%s' (Màu 2)", t_line1, t_line2)

            b_path, banner_w, banner_h = EditorProcessor._create_dynamic_title_banner_custom(
                work_dir, t_line1, t_line2, options
            )
            if b_path and os.path.isfile(b_path):
                banner_path = b_path
                banner_x = int((cls.OUTPUT_W - banner_w) / 2)
                has_banner = True
                logger.info("✅ [TITLE BANNER] Đã vẽ banner: %s (x=%d, y=%d)", banner_path, banner_x, banner_y)
        else:
            logger.info("🚫 [TITLE BANNER] Tiêu đề bị tắt theo cấu hình (enable_title=False).")

        # 8. Tính Kích Thước Tiền Cảnh (Foreground) & Tọa Độ Subtitle Mới
        core_ar = crop_w / max(1, crop_h)
        zoom_val = float(options.get("zoom_percent", 105.0) or 105.0)
        zoom_in = bool(options.get("zoom_in", True))
        zoom_factor = (zoom_val / 100.0) if zoom_in else 1.0
        sx = (float(options.get("scale_w") or options.get("scale_x", 100.0) or 100.0)) / 100.0
        sy = (float(options.get("scale_h") or options.get("scale_y", 100.0) or 100.0)) / 100.0
        blur_bg = bool(options.get("blur_bg_916", True) if "blur_bg_916" in options else options.get("blur_bg", True))
        video_speed = float(options.get("speed") or options.get("source_speed", 1.05) or 1.05)
        audio_boost = float(options.get("audio_boost", 6.0) or 6.0)

        fg_w = int(round(cls.OUTPUT_W * zoom_factor * sx))
        fg_w += fg_w % 2
        fg_h = int(round(fg_w / core_ar * sy))
        fg_h += fg_h % 2

        # Lề Đáy (MarginV) cho Subtitle:
        # Nếu bật overlay_sub_on_blur_zone (mặc định True) và có vùng sub cũ cần che:
        # Tự động tính MarginV để Sub mới đè CHÍNH XÁC lên vết che sub cũ!
        overlay_sub_on_blur = bool(options.get("overlay_sub_on_blur_zone", True))
        if overlay_sub_on_blur and (has_blur_mask or (rel_sub_ymin is not None and rel_sub_ymax is not None)):
            scale_v = fg_h / max(1.0, float(crop_h))
            if has_blur_mask and blur_mh > 0:
                blur_center_in_crop = blur_my + (blur_mh / 2.0)
            else:
                blur_center_in_crop = crop_h * ((rel_sub_ymin + rel_sub_ymax) / 2.0)

            # Tọa độ Y tâm của vùng che sub cũ trên canvas 1080x1920 (khung hình dọc)
            fg_top_y = (cls.OUTPUT_H - fg_h) / 2.0
            blur_y_center_canvas = fg_top_y + (blur_center_in_crop * scale_v)

            # Trong ASS Subtitle với Alignment=2 (Bottom-Center), MarginV là khoảng cách từ đáy (Y=1920) lên đáy dòng chữ.
            # Với cỡ chữ sub_size (mặc định ~ 38-42px):
            sub_sz = int(options.get("sub_size", 38) or 38)
            dist_from_bottom = cls.OUTPUT_H - blur_y_center_canvas
            calc_margin_v = int(round(dist_from_bottom - (sub_sz * 0.55)))

            # Tinh chỉnh nếu người dùng có chủ động nâng hạ vị trí trong Studio (mức chuẩn = 100):
            user_margin_val = int(options.get("sub_margin_v", 100) or 100)
            margin_offset = (user_margin_val - 100) if user_margin_val != 100 else 0

            sub_margin_v = max(60, min(1600, calc_margin_v + margin_offset))
            logger.info("🎯 [SUB OVERLAY] Tự động tính toán MarginV=%d px đè CHÍNH XÁC lên dải che Sub cũ (Y_center=%.1f canvas), xóa sạch 100%% dấu vết đối thủ!", sub_margin_v, blur_y_center_canvas)
        else:
            sub_margin_v = int(options.get("sub_margin_v", 100) or 100)
            sub_margin_v = max(30, min(800, sub_margin_v))
            logger.info("💬 [SUBTITLE POSITION] Lề đáy phụ đề thủ công: MarginV=%d px", sub_margin_v)

        # 9. Bộ Lọc Màu CapCut 15 Thông Số + Look Stack
        color_filter_str = EditorProcessor._build_pure_color_filter(options)
        if not color_filter_str or color_filter_str.strip() == "":
            color_filter_str = "null"
        logger.info("🎨 [CAPCUT COLOR] Áp dụng chuỗi filter màu CapCut: %s", color_filter_str)

        # 10. Xử Lý Phân Đoạn Hook Đầu Video (Vị trí 00:00.000, giữ nguyên âm thanh gốc)
        t_hook_start = time.time()
        hook_rendered_mp4 = os.path.join(work_dir, "hook_rendered.mp4")
        has_hook_segment = False
        flags = 0x08000000 if os.name == "nt" else 0

        audio_opts = []
        if audio_boost != 0:
            audio_opts.extend(["-filter:a", EditorProcessor.capcut_audio_filter(audio_boost)])

        has_src_audio = EditorProcessor._has_audio(source_video)

        if options.get("keep_original_hook", True) and hook_dur >= 0.5:
            logger.info("🎬 [HOOK RENDER] Dựng đoạn Hook mở đầu (%.2fs - %.2fs) giữ nguyên 100%% âm thanh gốc (CapCut Limiter)...", hook_start, hook_end)
            if blur_bg:
                bg_hook_chain = (
                    f"[core_clean]split=2[c_fg][c_bg];"
                    f"[c_bg]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=25:5,scale=1080:1920:flags=bicubic[bg];"
                    f"[c_fg]scale={fg_w}:{fg_h}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v_base];"
                )
            else:
                bg_hook_chain = (
                    f"color=c=black:s=1080x1920:d={hook_dur:.3f}:r=25[bg];"
                    f"[core_clean]scale={fg_w}:{fg_h}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v_base];"
                )

            if has_blur_mask:
                core_crop_hook = (
                    f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                    f"split=2[c_orig][c_mask];"
                    f"[c_mask]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=25:20[c_blur];"
                    f"[c_orig][c_blur]overlay={blur_mx}:{blur_my}[core_clean];"
                )
            else:
                core_crop_hook = f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:{crop_y}[core_clean];"

            if has_banner:
                hook_vf = (
                    f"{core_crop_hook}"
                    f"{bg_hook_chain}"
                    f"[v_base]{color_filter_str}[v_color];"
                    f"[v_color][1:v]overlay={banner_x}:{banner_y}:shortest=1[vout]"
                )
                banner_inputs = ["-loop", "1", "-i", banner_path]
            else:
                hook_vf = (
                    f"{core_crop_hook}"
                    f"{bg_hook_chain}"
                    f"[v_base]{color_filter_str}[vout]"
                )
                banner_inputs = []

            if has_src_audio:
                cmd_hook_render = [
                    "ffmpeg", "-y",
                    "-ss", f"{hook_start:.3f}", "-t", f"{hook_dur:.3f}",
                    "-i", source_video,
                    *banner_inputs,
                    "-filter_complex", hook_vf,
                    "-map", "[vout]",
                    "-map", "0:a:0",
                    *audio_opts,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-r", "25", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-avoid_negative_ts", "make_zero",
                    "-t", f"{hook_dur:.3f}",
                    "-shortest",
                    hook_rendered_mp4
                ]
            else:
                dummy_idx = 1 if not has_banner else 2
                cmd_hook_render = [
                    "ffmpeg", "-y",
                    "-ss", f"{hook_start:.3f}", "-t", f"{hook_dur:.3f}",
                    "-i", source_video,
                    *banner_inputs,
                    "-f", "lavfi", "-t", f"{hook_dur:.3f}", "-i", "anullsrc=r=44100:cl=stereo",
                    "-filter_complex", hook_vf,
                    "-map", "[vout]",
                    "-map", f"{dummy_idx}:a",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
                    "-r", "25", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    "-avoid_negative_ts", "make_zero",
                    "-t", f"{hook_dur:.3f}",
                    "-shortest",
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

            if has_blur_mask:
                vf_clip = (
                    f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                    f"split=2[c_orig][c_mask];"
                    f"[c_mask]crop={blur_mw}:{blur_mh}:{blur_mx}:{blur_my},boxblur=25:20[c_blur];"
                    f"[c_orig][c_blur]overlay={blur_mx}:{blur_my},"
                    f"setpts={pts_speed:.4f}*PTS{mirror_filter}{zoom_var_filter}"
                )
            else:
                vf_clip = (
                    f"crop={crop_w}:{crop_h}:{crop_x}:{crop_y},"
                    f"setpts={pts_speed:.4f}*PTS{mirror_filter}{zoom_var_filter}"
                )

            cmd_c = [
                "ffmpeg", "-y", "-ss", f"{sc['start']:.3f}", "-to", f"{sc['end']:.3f}",
                "-i", source_video,
                "-vf", vf_clip,
                "-an",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
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

        # Nền blur 2 đầu từ chính lõi sạch cho phần thân video (Boxblur 25:5 mờ sâu chuẩn điện ảnh)
        if blur_bg:
            bg_stream = f"[0:v]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=25:5,scale=1080:1920:flags=bicubic[bg];"
        else:
            bg_stream = f"color=c=black:s=1080x1920:r=25[bg];"

        speed_filter = f",setpts={1.0 / video_speed:.4f}*PTS" if abs(video_speed - 1.0) >= 0.01 else ""

        if has_banner:
            banner_overlay_step = f"[v_color][1:v]overlay={banner_x}:{banner_y}:shortest=1[v_banner];"
            prev_sub_layer = "[v_banner]"
        else:
            banner_overlay_step = ""
            prev_sub_layer = "[v_color]"

        enable_sub = bool(options.get("enable_sub", True) and options.get("auto_sub", True))
        sub_filter_chain = f"{prev_sub_layer}null[vout]"

        if enable_sub and narration_srt and os.path.isfile(narration_srt):
            from font_manager import FontManager
            style_type = str(options.get("sub_style_type", "tiktok_slim"))
            try:
                with open(narration_srt, "r", encoding="utf-8", errors="ignore") as sf:
                    sample_srt_text = sf.read(2048)
            except Exception:
                sample_srt_text = ""

            style_specs = FontManager.get_subtitle_style_specs(
                style_type=style_type,
                sample_text=sample_srt_text,
                custom_overrides=options
            )

            sub_font = style_specs.get("font_name", "Arial")
            sub_size = int(options.get("sub_size", 38) or 38)
            sub_size = max(18, min(120, sub_size))

            # Chuyển đổi màu sắc an toàn (hỗ trợ cả ASS & Hex)
            raw_c = options.get("sub_color", "&H00FFFF&")
            sub_color = cls._to_ass_color(raw_c, default_ass="&H0000FFFF")

            raw_oc = options.get("sub_outline_color", "&H000000&")
            sub_outline_color = cls._to_ass_color(raw_oc, default_ass="&H00000000")

            sub_outline = int(options.get("sub_outline", 4) or 4)
            sub_shadow = int(options.get("sub_shadow", 1) or 1)

            # Nếu phong cách yêu cầu in hoa (ví dụ Classic), in hoa nội dung SRT
            srt_to_use = narration_srt
            if style_specs.get("uppercase", False):
                try:
                    upper_srt = os.path.join(work_dir, "narration_uppercase.srt")
                    with open(narration_srt, "r", encoding="utf-8", errors="ignore") as inf:
                        lines = inf.readlines()
                    with open(upper_srt, "w", encoding="utf-8") as outf:
                        for line in lines:
                            s = line.strip()
                            if s.isdigit() or "-->" in s or not s:
                                outf.write(line)
                            else:
                                outf.write(line.upper())
                    if os.path.isfile(upper_srt):
                        srt_to_use = upper_srt
                except Exception:
                    pass

            ass_file = os.path.join(work_dir, "tiktok_subtitles.ass")
            converted = cls.convert_srt_to_ass(
                srt_file=srt_to_use,
                ass_file=ass_file,
                font_name=sub_font,
                font_size=sub_size,
                primary_color=sub_color,
                outline_color=sub_outline_color,
                outline_width=sub_outline,
                shadow_dist=sub_shadow,
                margin_v=sub_margin_v
            )
            if converted and os.path.isfile(ass_file):
                ass_escaped = os.path.abspath(ass_file).replace('\\', '/')
                if ":" in ass_escaped:
                    d, p = ass_escaped.split(":", 1)
                    ass_escaped = f"{d}\\:{p}"
                sub_filter_chain = f"{prev_sub_layer}ass='{ass_escaped}'[vout]"
            else:
                abs_srt = os.path.abspath(narration_srt).replace('\\', '/')
                if ":" in abs_srt:
                    d, p = abs_srt.split(":", 1)
                    abs_srt = f"{d}\\:{p}"
                sub_filter_chain = f"{prev_sub_layer}subtitles='{abs_srt}'[vout]"
            logger.info("💬 [SUBTITLE RENDER] Đã cấu hình phụ đề: Size=%d, Màu=%s, Viền=%d, MarginV=%d", sub_size, sub_color, sub_outline, sub_margin_v)
        else:
            logger.info("🚫 [SUBTITLE RENDER] Phụ đề bị tắt hoặc không có file SRT.")

        master_vf = (
            f"{bg_stream}"
            f"[0:v]scale={fg_w}:{fg_h}[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v_base];"
            f"[v_base]{color_filter_str}{speed_filter}[v_color];"
            f"{banner_overlay_step}"
            f"{sub_filter_chain}"
        )

        audio_body_opts = []
        body_af = []
        if audio_boost != 0:
            body_af.append(EditorProcessor.capcut_audio_filter(audio_boost))
        if narration_audio and os.path.isfile(narration_audio) and audio_duration < target_body_dur:
            pad_dur = target_body_dur - audio_duration + 0.5
            body_af.append(f"apad=pad_dur={pad_dur:.2f}")
        if body_af:
            audio_body_opts = ["-filter:a", ",".join(body_af)]

        has_voice = bool(narration_audio and os.path.isfile(narration_audio))
        if has_banner:
            body_inputs = [
                "-i", body_cut_mp4,
                "-loop", "1", "-i", banner_path
            ]
            if has_voice:
                body_inputs += ["-i", narration_audio]
                audio_map_idx = "2:a"
            else:
                audio_map_idx = "0:a?"
        else:
            body_inputs = [
                "-i", body_cut_mp4
            ]
            if has_voice:
                body_inputs += ["-i", narration_audio]
                audio_map_idx = "1:a"
            else:
                audio_map_idx = "0:a?"

        cmd_body_final = [
            "ffmpeg", "-y",
            *body_inputs,
            "-filter_complex", master_vf,
            "-map", "[vout]",
            "-map", audio_map_idx,
            *audio_body_opts,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
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
        hook_valid = has_hook_segment and os.path.isfile(hook_rendered_mp4) and os.path.getsize(hook_rendered_mp4) > 10000
        if hook_valid and os.path.isfile(body_rendered_mp4):
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
            if res_merge.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) < 10000:
                # Fallback filter_complex concat
                cmd_fallback_merge = [
                    "ffmpeg", "-y",
                    "-i", hook_rendered_mp4,
                    "-i", body_rendered_mp4,
                    "-filter_complex", "[0:v][0:a][1:v][1:a]concat=n=2:v=1:a=1[vout][aout]",
                    "-map", "[vout]", "-map", "[aout]",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-c:a", "aac", "-b:a", "192k",
                    output_path
                ]
                subprocess.run(cmd_fallback_merge, capture_output=True, text=True, creationflags=flags)
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
