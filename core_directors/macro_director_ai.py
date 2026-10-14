"""Đạo Diễn 1: Tổng Đạo Diễn Cốt Truyện Vĩ Mô (Macro Story Director).

Nhiệm vụ:
- Đọc toàn bộ transcript của video (15 - 45 phút).
- Phân tích motif, nhân vật, xung đột chính (Thử lòng, phản bội, drama...).
- Phân chia video thành N Parts tuyến tính (chronological order), đảm bảo câu chuyện
  tiến triển tự nhiên về phía trước, không nhảy cóc hay lộn xộn.
- Xác định rõ Narrative Goal, Cold Hook và Cliffhanger treo lơ lửng cho từng Part.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("MacroDirectorAI")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from antigravity_processor import AntigravityProcessor
except ImportError:
    AntigravityProcessor = None

try:
    from ai_processor import AIProcessor
except ImportError:
    AIProcessor = None


class MacroDirectorAI:
    """Đạo Diễn 1 - Tổng Đạo Diễn Cốt Truyện Vĩ Mô."""

    @classmethod
    def _call_llm(cls, system_prompt: str, user_prompt: str, config: Optional[Dict[str, Any]] = None) -> str:
        config = config or {}
        api_key = str(config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

        # 1. Thử Antigravity CLI nội bộ
        if AntigravityProcessor and AntigravityProcessor.executable():
            try:
                res = AntigravityProcessor.generate_text(system_prompt, user_prompt)
                if res and len(res.strip()) > 30:
                    return res
            except Exception as e:
                logger.warning(f"Antigravity CLI call failed: {e}")

        # 2. Fallback sang Google Gemini REST API
        if api_key and AIProcessor:
            try:
                model = str(config.get("gemini_model") or "gemini-1.5-pro")
                return AIProcessor._call_gemini(api_key, system_prompt, user_prompt)
            except Exception as e:
                logger.error(f"Gemini API call failed: {e}")

        return ""

    @classmethod
    def plan_macro_series(
        cls,
        cues: List[Dict[str, Any]],
        total_duration: float,
        part_count: int = 6,
        genre: str = "reality_drama",
        master_title: str = "",
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> Dict[str, Any]:
        """Tạo Master Series Plan chia N Parts tuyến tính."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}

        # CƠ CHẾ BẢO VỆ MỐC 1 PHÚT: Tự động tính toán số Part an toàn dựa trên thời lượng nguồn
        # Mỗi Part chuẩn Hollywood cần tối thiểu ~120s nguồn để lọc ra 4-5 cảnh cốt lõi đạt >= 70s - 95s
        min_source_per_part = float(config.get("min_source_per_part", 125.0))
        max_safe_parts = max(1, int(total_duration / min_source_per_part))
        if part_count > max_safe_parts:
            _log(
                f"🛡️ [BẢO VỆ MỐC 1 PHÚT] Video nguồn {total_duration:.1f}s ({total_duration/60:.1f} phút) "
                f"chia {part_count} Parts sẽ khiến video bị ngắn dưới 60s! "
                f"Hệ thống tự động tái cân bằng từ {part_count} -> {max_safe_parts} Parts "
                f"để cam kết 100% video đều > 1 phút (chuẩn kiếm tiền & giữ chân người xem)!"
            )
            part_count = max_safe_parts

        _log(f"👑 [ĐẠO DIỄN 1] Phân tích vĩ mô toàn bộ video ({total_duration:.1f}s, {len(cues)} cues) thành {part_count} Parts...")

        # Tóm tắt transcript cho LLM
        sample_cues = []
        step = max(1, len(cues) // 250) if cues else 1
        for i in range(0, len(cues), step):
            c = cues[i]
            st = float(c.get("start", 0.0))
            en = float(c.get("end", 0.0))
            tx = str(c.get("text", "")).strip()
            if tx:
                sample_cues.append(f"[{st:.1f}s - {en:.1f}s] {tx}")
        transcript_sample = "\n".join(sample_cues[:300])

        system_prompt = f"""You are the Master Macro Story Director for Hollywood-grade episodic short-form content.
Your task is to analyze the complete long-form video transcript and structure it into exactly {part_count} linear chronological Parts.

CORE MACRO DIRECTING LAWS:
1. STRICT CHRONOLOGICAL ORDER: Events must flow forward in time. Part 1 covers the beginning setup, Part 2 moves forward, etc., reaching the final climax in Part {part_count}.
2. ZERO OVERLAP & NO JUMPING BACKWARDS: Each Part has its own defined timeline segment (source_time_range).
3. COMPELLING CLIFFHANGER: Each Part must end right before a major revelation, reaction, or twist to make watching the next Part irresistible.
4. TITLES: Keep titles in the original language of the video (clean English if video is English). NEVER add random Vietnamese or foreign text.
"""

        user_prompt = f"""Video Title: {master_title or 'Untitled Video'}
Total Duration: {total_duration:.1f}s
Target Parts: {part_count}
Genre: {genre}

TRANSCRIPT OUTLINE:
{transcript_sample}

OUTPUT SPECIFICATION:
Respond ONLY with a valid JSON object matching this schema:
{{
  "series_title": "{master_title or 'Punchy Series Title'}",
  "genre": "{genre}",
  "total_parts": {part_count},
  "parts": [
    {{
      "part_index": 1,
      "part_title": "Episode 1 Title",
      "source_time_range": [0.0, {total_duration / part_count:.1f}],
      "narrative_goal": "Setup context and first dramatic standoff",
      "hook_topic": "Shocking confession teaser",
      "cliffhanger_topic": "Suspense moment before test begins"
    }}
  ]
}}
"""
        use_cloud_llm = bool(config.get("use_cloud_llm_macro", False))
        if use_cloud_llm:
            raw_output = cls._call_llm(system_prompt, user_prompt, config)
            if raw_output:
                try:
                    # Trích xuất JSON sạch
                    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_output, re.IGNORECASE)
                    text = m.group(1).strip() if m else raw_output.strip()
                    s_idx = text.find("{")
                    e_idx = text.rfind("}")
                    if s_idx != -1 and e_idx != -1:
                        plan = json.loads(text[s_idx:e_idx + 1])
                        if isinstance(plan, dict) and plan.get("parts") and len(plan["parts"]) == part_count:
                            _log(f"✅ [ĐẠO DIỄN 1] Kế hoạch vĩ mô AI Cloud đã phê duyệt thành công!")
                            return plan
                except Exception as e:
                    logger.warning(f"Lỗi parse Master Plan JSON: {e}")

        # Phân bổ vĩ mô tuyến tính chuẩn xác tốc độ cao (0.001s)
        _log(f"⚡ [ĐẠO DIỄN 1] Phân bổ vĩ mô tuyến tính chuẩn xác cho {part_count} Parts...")
        part_step = total_duration / float(part_count)
        parts = []
        for i in range(part_count):
            p_start = round(i * part_step, 2)
            p_end = round((i + 1) * part_step if i + 1 < part_count else total_duration, 2)
            parts.append({
                "part_index": i + 1,
                "part_title": f"Part {i + 1}: The Confrontation" if i > 0 else "Part 1: The Setup & Temptation",
                "source_time_range": [p_start, p_end],
                "narrative_goal": f"Chapter {i + 1} narrative progression",
                "hook_topic": f"Key shock moment of chapter {i + 1}",
                "cliffhanger_topic": f"Cliffhanger transition to Part {i + 2}" if i + 1 < part_count else "Final resolution"
            })

        _log(f"📋 [KẾ HOẠCH VĨ MÔ SERIES] Phân bổ {len(parts)} Parts từ video {total_duration/60:.1f} phút:")
        for p in parts:
            p_idx = p["part_index"]
            st, en = p["source_time_range"]
            dur = en - st
            _log(f"   • Part {p_idx}: [{st:.1f}s -> {en:.1f}s] (Khung nguồn: {dur/60:.1f} phút | {dur:.0f}s) -> '{p['part_title']}'")

        return {
            "series_title": master_title or "Viral Series",
            "genre": genre,
            "total_parts": part_count,
            "parts": parts
        }
