"""Đạo Diễn 2: Đạo Diễn Phân Cảnh & Nhịp Điệu (Scene & Rhythm Director).

Nhiệm vụ:
- Đi sâu vào phạm vi 5-7 phút gốc của từng Part (từ Đạo diễn 1).
- Lọc ra ĐÚNG 4 - 5 phân cảnh cốt lõi đắt giá nhất (~18s - 22s mỗi cảnh).
- Tuyệt đối không để 1 cảnh đứng nói quá dài 20s-50s lê thê.
- Đảm bảo mạch phim phát triển THUẬN CHIỀU THỜI GIAN (linear chronological progression).
- Chỉ định rõ góc máy cho từng shot:
  + pan="left": Người hỏi / MC
  + pan="right": Người trả lời / Khách mời
  + pan="center": Toàn cảnh / B-roll / Không gian
  + punch=True (1.18x zoom): Khi có câu nói sốc, biểu cảm ngỡ ngàng, punchline.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SceneRhythmDirectorAI")

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


class SceneRhythmDirectorAI:
    """Đạo Diễn 2 - Đạo Diễn Phân Cảnh & Nhịp Điệu."""

    @classmethod
    def _call_llm(cls, system_prompt: str, user_prompt: str, config: Optional[Dict[str, Any]] = None) -> str:
        config = config or {}
        api_key = str(config.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY") or "").strip()

        if AntigravityProcessor and AntigravityProcessor.executable():
            try:
                res = AntigravityProcessor.generate_text(system_prompt, user_prompt)
                if res and len(res.strip()) > 30:
                    return res
            except Exception as e:
                logger.warning(f"Antigravity CLI call failed: {e}")

        if api_key and AIProcessor:
            try:
                model = str(config.get("gemini_model") or "gemini-1.5-pro")
                return AIProcessor._call_gemini(api_key, system_prompt, user_prompt)
            except Exception as e:
                logger.error(f"Gemini API call failed: {e}")

        return ""

    @classmethod
    def direct_part_scenes(
        cls,
        part_index: int,
        part_info: Dict[str, Any],
        cues: List[Dict[str, Any]],
        total_duration: float,
        target_scene_count: int = 5,
        target_part_duration: float = 95.0,
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> Dict[str, Any]:
        """Tạo kịch bản 4-5 cảnh cốt lõi (~18-22s/cảnh) cho một Part cụ thể."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        time_range = part_info.get("source_time_range", [0.0, total_duration])
        start_bound = float(time_range[0])
        end_bound = float(time_range[1])
        goal = part_info.get("narrative_goal", "Story development")
        title = part_info.get("part_title", f"Part {part_index}")

        _log(f"🎬 [ĐẠO DIỄN 2] Đạo diễn phân cảnh Part {part_index}: {title} ({start_bound:.1f}s -> {end_bound:.1f}s)...")

        # Lọc cues trong khung thời gian của Part
        part_cues = [c for c in cues if c["start"] >= max(0.0, start_bound - 2.0) and c["end"] <= min(total_duration, end_bound + 2.0)]
        if not part_cues:
            part_cues = cues

        cue_lines = []
        for c in part_cues[:120]:
            st = float(c.get("start", 0.0))
            en = float(c.get("end", 0.0))
            tx = str(c.get("text", "")).strip()
            if tx:
                cue_lines.append(f"[{st:.1f}s - {en:.1f}s] {tx}")
        transcript_text = "\n".join(cue_lines)

        system_prompt = f"""You are the Master Scene & Rhythm Director (Đạo Diễn Phân Cảnh & Nhịp Điệu) for viral episodic video.
You are directing Part {part_index}: "{title}".
Story Goal: {goal}
Timeline segment: {start_bound:.1f}s to {end_bound:.1f}s.

RULES OF THE HOLLYWOOD DIRECTOR:
1. EXACTLY 4 TO 5 CORE SCENES: You MUST divide this Part into 4 to 5 distinct scenes (~18s - 22s each).
   - NEVER let 2 people stand talking in a single scene for 40-50s!
   - Each scene must cover a distinct beat/location/moment (e.g. Setup, Confrontation, B-roll/models, Car/Travel, Climax).
2. STRICT CHRONOLOGICAL ORDER: Scene 1 < Scene 2 < Scene 3 < Scene 4 < Scene 5. Never jump backwards in time.
3. 2-4 SHOTS PER SCENE: Each shot should last 3.5s to 7.0s, with natural conversational flow (never cut mid-sentence).
4. DYNAMIC CAMERA ANGLES:
   - pan = "left": Host / Speaker 1
   - pan = "right": Guest / Speaker 2
   - pan = "center": Wide shot, B-roll, or group
   - punch = true: On dramatic reveals, confessions, or shock punchlines (triggers 1.18x face punch zoom)
5. CLIFFHANGER: Scene 5 must end on a suspenseful question or reaction right before the reveal!
"""

        user_prompt = f"""Target Part Duration: ~{target_part_duration:.0f}s (each scene ~18s - 22s)
TRANSCRIPT IN WINDOW:
{transcript_text}

OUTPUT SPECIFICATION:
Respond ONLY with a valid JSON object matching this schema:
{{
  "part_index": {part_index},
  "title": "{title}",
  "scenes": [
    {{
      "scene_id": 1,
      "name": "Scene description",
      "shots": [
        {{"start": 10.0, "end": 15.0, "speaker": "Host", "pan": "left", "punch": false, "desc": "Host asks initial question"}},
        {{"start": 15.0, "end": 20.0, "speaker": "Guest", "pan": "right", "punch": true, "desc": "Guest reveals shock statement"}}
      ]
    }}
  ]
}}
"""
        raw_output = cls._call_llm(system_prompt, user_prompt, config)
        if raw_output:
            try:
                m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw_output, re.IGNORECASE)
                text = m.group(1).strip() if m else raw_output.strip()
                s_idx = text.find("{")
                e_idx = text.rfind("}")
                if s_idx != -1 and e_idx != -1:
                    data = json.loads(text[s_idx:e_idx + 1])
                    if isinstance(data, dict) and data.get("scenes") and len(data["scenes"]) >= 3:
                        _log(f"✅ [ĐẠO DIỄN 2] Phê duyệt kịch bản {len(data['scenes'])} cảnh cho Part {part_index} thành công!")
                        data["scenes"] = cls._ensure_min_duration(data["scenes"], min_dur=65.0, max_bound=end_bound)
                        return data
            except Exception as e:
                logger.warning(f"Lỗi parse Storyboard Spec JSON: {e}")

        # Heuristic fallback: Tự động gom cụm cues thành 4-5 cảnh thuận chiều thời gian
        _log(f"ℹ️ [ĐẠO DIỄN 2] Tự động xây dựng 5 phân cảnh cốt lõi chất lượng cao từ transcript cues...")
        scenes = cls._build_heuristic_scenes(part_cues, start_bound, end_bound, target_scene_count=5)
        scenes = cls._ensure_min_duration(scenes, min_dur=65.0, max_bound=end_bound)
        return {
            "part_index": part_index,
            "title": title,
            "scenes": scenes
        }

    @classmethod
    def _ensure_min_duration(
        cls,
        scenes: List[Dict[str, Any]],
        min_dur: float = 65.0,
        max_bound: float = 99999.0
    ) -> List[Dict[str, Any]]:
        """Bảo đảm tổng thời lượng các cảnh trong Part luôn đạt >= min_dur (65s), cam kết > 1 phút."""
        tot = sum(max(0.0, float(sh.get("end", 0)) - float(sh.get("start", 0))) for sc in scenes for sh in sc.get("shots", []))
        if tot >= min_dur or not scenes:
            return scenes

        deficit = min_dur - tot
        shot_count = sum(len(sc.get("shots", [])) for sc in scenes)
        if shot_count > 0:
            add_per_shot = deficit / float(shot_count)
            for sc in scenes:
                for sh in sc.get("shots", []):
                    s = max(0.0, float(sh["start"]) - add_per_shot * 0.3)
                    e = min(max_bound, float(sh["end"]) + add_per_shot * 0.7)
                    sh["start"] = round(s, 2)
                    sh["end"] = round(e, 2)

        return scenes

    @classmethod
    def _build_heuristic_scenes(
        cls,
        cues: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        target_scene_count: int = 5
    ) -> List[Dict[str, Any]]:
        """Thuật toán gom cụm 4-5 phân cảnh thông minh tuân thủ nghiêm ngặt tiến trình thời gian."""
        valid_cues = [c for c in cues if (c.get("end", 0.0) - c.get("start", 0.0)) >= 1.2]
        if not valid_cues:
            # Nếu không có cues, chia đều timeline thành 5 cảnh
            dur = max(60.0, end_bound - start_bound)
            sc_dur = dur / float(target_scene_count)
            res = []
            for i in range(target_scene_count):
                s = start_bound + i * sc_dur
                res.append({
                    "scene_id": i + 1,
                    "name": f"Scene {i + 1}",
                    "shots": [
                        {"start": round(s, 2), "end": round(s + sc_dur * 0.5, 2), "pan": "left", "punch": False, "desc": "Setup"},
                        {"start": round(s + sc_dur * 0.5, 2), "end": round(s + sc_dur, 2), "pan": "right", "punch": True, "desc": "Peak"}
                    ]
                })
            return res

        # Chia valid_cues thành target_scene_count nhóm theo thứ tự thời gian
        k = len(valid_cues) // target_scene_count
        scenes = []
        for i in range(target_scene_count):
            sub_cues = valid_cues[i * k : (i + 1) * k] if i < target_scene_count - 1 else valid_cues[i * k :]
            if not sub_cues:
                continue

            shots = []
            # Chọn 2-4 cues tiêu biểu trong nhóm này
            step = max(1, len(sub_cues) // 3)
            for j in range(0, min(len(sub_cues), step * 3), step):
                sc = sub_cues[j]
                dur = sc["end"] - sc["start"]
                # Giới hạn shot trong khoảng 3.5s - 6.5s
                s_st = sc["start"]
                s_en = min(sc["end"], s_st + 6.5)
                pan = "left" if (j % 2 == 0) else "right"
                punch = (j == 1 or j == len(sub_cues) - 1)
                shots.append({
                    "start": round(s_st, 2),
                    "end": round(s_en, 2),
                    "speaker": f"Speaker {j+1}",
                    "pan": pan,
                    "punch": punch,
                    "desc": sc.get("text", "")[:40]
                })

            if shots:
                scenes.append({
                    "scene_id": i + 1,
                    "name": f"Phân cảnh {i + 1}",
                    "shots": shots
                })

        return scenes
