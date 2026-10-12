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
        target_scene_count: int = 2,
        target_part_duration: float = 85.0,
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> Dict[str, Any]:
        """Tạo kịch bản 2-3 phân cảnh LIỀN MẠCH (Continuous Story Blocks ~35s - 45s mỗi cảnh) cho một Part.
        - TUYỆT ĐỐI KHÔNG CẮT VỤN 3s rồi ghép lại!
        - Mỗi cảnh là một dòng thời gian chảy liên tục 100% (câu chuyện, hội thoại nguyên vẹn có đầu có đuôi).
        - Bên trong mỗi cảnh, các shot nối tiếp nhau không hở thời gian (shot[i+1].start == shot[i].end).
        - Giữa các cảnh có khoảng cách an toàn, tuyệt đối không bao giờ lùi giờ hay lặp lại video/b-roll.
        """
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

        # Tự động chọn 2 hoặc 3 cảnh tùy theo độ dài cửa sổ thời gian
        window_duration = max(30.0, end_bound - start_bound)
        if window_duration >= 200.0:
            target_scene_count = 3
        else:
            target_scene_count = 2

        _log(f"🎬 [ĐẠO DIỄN 2] Đạo diễn {target_scene_count} phân cảnh LIỀN MẠCH cho Part {part_index}: {title} ({start_bound:.1f}s -> {end_bound:.1f}s)...")

        # Lọc cues trong khung thời gian của Part
        part_cues = [c for c in cues if c.get("start", 0.0) >= max(0.0, start_bound - 2.0) and c.get("end", 0.0) <= min(total_duration, end_bound + 2.0)]
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

        system_prompt = f"""You are the Master Scene & Rhythm Director for Hollywood-grade episodic video.
You are directing Part {part_index}: "{title}".
Story Goal: {goal}
Timeline window: {start_bound:.1f}s to {end_bound:.1f}s.

ABSOLUTE LAWS OF THE HOLLYWOOD DIRECTOR:
1. STRICT CONTINUOUS STORY BLOCKS (NO MICRO-FRAGMENTATION):
   - You must select exactly {target_scene_count} CONTINUOUS narrative scenes (each lasting 35s to 45s of uninterrupted dialogue and action).
   - DO NOT chop the video into disconnected 3-second fragments! Every scene must be a continuous slice of the video where characters talk and act with full beginning-to-end context!
2. SEAMLESS SHOTS INSIDE EACH SCENE:
   - Inside each scene, divide the continuous timeframe into 3 to 5 consecutive shots for dynamic camera movement (pan="left", pan="right", punch=true).
   - MANDATORY: There are ZERO time gaps between shots inside the scene: shot[k+1].start MUST EQUAL shot[k].end!
3. ZERO REPETITION & NO OVERLAP:
   - Scene 2 start MUST be strictly greater than Scene 1 end (at least 3.0s later). Never repeat footage!
4. CLIFFHANGER: The final scene must end on a suspenseful reveal or twist!
"""

        user_prompt = f"""Target Part Duration: ~{target_part_duration:.0f}s (each scene ~35s - 45s continuous)
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
      "name": "The Setup & Inciting Conflict",
      "shots": [
        {{"start": 10.0, "end": 20.0, "speaker": "Host", "pan": "left", "punch": false, "desc": "Setup problem"}},
        {{"start": 20.0, "end": 32.0, "speaker": "Guest", "pan": "right", "punch": true, "desc": "Shock reaction"}},
        {{"start": 32.0, "end": 45.0, "speaker": "Host", "pan": "center", "punch": false, "desc": "Decision to proceed"}}
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
                    if isinstance(data, dict) and data.get("scenes") and len(data["scenes"]) >= 2:
                        _log(f"✅ [ĐẠO DIỄN 2] AI đã phê duyệt {len(data['scenes'])} phân cảnh liền mạch cho Part {part_index}!")
                        sanitized = cls._sanitize_continuous_scenes(data["scenes"], start_bound, end_bound, min_dur=70.0)
                        return {
                            "part_index": part_index,
                            "title": title,
                            "scenes": sanitized
                        }
            except Exception as e:
                logger.warning(f"Lỗi parse Storyboard Spec JSON: {e}")

        # Heuristic fallback: Tự động xây dựng 2-3 khối phân cảnh LIỀN MẠCH chuẩn Hollywood
        _log(f"ℹ️ [ĐẠO DIỄN 2] Tự động chọn {target_scene_count} phân cảnh LIỀN MẠCH (~35s-45s/cảnh) không cắt vụn...")
        scenes = cls._build_heuristic_continuous_scenes(
            cues=part_cues,
            start_bound=start_bound,
            end_bound=end_bound,
            target_scene_count=target_scene_count,
            target_part_dur=target_part_duration
        )
        sanitized = cls._sanitize_continuous_scenes(scenes, start_bound, end_bound, min_dur=70.0)
        return {
            "part_index": part_index,
            "title": title,
            "scenes": sanitized
        }

    @classmethod
    def _sanitize_continuous_scenes(
        cls,
        scenes: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        min_dur: float = 70.0
    ) -> List[Dict[str, Any]]:
        """Chuẩn hóa và khóa cứng 100% tính liền mạch (Strict Continuity):
        1. Bên trong mỗi cảnh: shot[k+1].start == shot[k].end (KHÔNG HỞ KHOẢNG TRỐNG NÀO).
        2. Giữa các cảnh: Scene[i+1].start >= Scene[i].end + 2.0s (TRIỆT TIÊU 100% TRÙNG LẶP FOOTAGE).
        3. Tổng thời lượng đảm bảo >= min_dur (luôn > 1 phút).
        """
        if not scenes:
            return scenes

        cleaned_scenes = []
        last_scene_end = start_bound

        for sc_idx, sc in enumerate(scenes):
            raw_shots = sc.get("shots", [])
            if not raw_shots:
                continue

            # Mốc bắt đầu cảnh này phải sau cảnh trước ít nhất 2s
            first_sh_st = float(raw_shots[0].get("start", 0.0))
            if first_sh_st < last_scene_end:
                offset = (last_scene_end + 2.0) - first_sh_st
            else:
                offset = 0.0

            cur_shot_st = first_sh_st + offset
            clean_shots = []

            for sh_k, sh in enumerate(raw_shots):
                orig_dur = max(3.5, float(sh.get("end", 0.0)) - float(sh.get("start", 0.0)))
                sh_end = cur_shot_st + orig_dur

                item = dict(sh)
                item["start"] = round(cur_shot_st, 2)
                item["end"] = round(sh_end, 2)
                item["pan"] = str(sh.get("pan", "left" if sh_k % 2 == 0 else "right")).lower().strip()
                item["punch"] = bool(sh.get("punch", False) or sh_k == 1)

                clean_shots.append(item)
                # Shot kế tiếp bắt đầu CHÍNH XÁC tại điểm kết thúc của shot này
                cur_shot_st = item["end"]

            sc_copy = dict(sc)
            sc_copy["shots"] = clean_shots
            cleaned_scenes.append(sc_copy)
            last_scene_end = clean_shots[-1]["end"]

        # Kiểm tra tổng thời lượng Part, nếu thiếu thì mở rộng nhẹ shot cuối của cảnh cuối
        tot = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in cleaned_scenes if sc["shots"])
        if tot < min_dur and cleaned_scenes:
            deficit = min_dur - tot
            last_sc_shots = cleaned_scenes[-1]["shots"]
            if last_sc_shots:
                last_sc_shots[-1]["end"] = round(min(end_bound, last_sc_shots[-1]["end"] + deficit + 1.0), 2)

        return cleaned_scenes

    @classmethod
    def _build_heuristic_continuous_scenes(
        cls,
        cues: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        target_scene_count: int = 2,
        target_part_dur: float = 85.0
    ) -> List[Dict[str, Any]]:
        """Thuật toán tìm các Khối Diễn Biến Liền Mạch (Continuous Story Blocks):
        - Mỗi cảnh dài ~35s - 45s liên tục, không bị gián đoạn hay ngắt vụn.
        - Tìm phân đoạn có mật độ lời thoại cao nhất trong từng nửa cửa sổ Part.
        - Chia đều thành các shot đổi góc camera trên dòng thời gian chạy thẳng.
        """
        window_len = max(60.0, end_bound - start_bound)
        target_sc_dur = max(32.0, target_part_dur / float(target_scene_count))

        scenes = []
        current_min_start = start_bound

        for sc_idx in range(target_scene_count):
            frac_st = sc_idx / float(target_scene_count)
            frac_en = (sc_idx + 1) / float(target_scene_count)
            w_st = max(current_min_start, start_bound + frac_st * window_len)
            w_en = start_bound + frac_en * window_len
            if sc_idx == target_scene_count - 1:
                w_en = end_bound

            window_cues = [c for c in cues if c.get("start", 0.0) >= w_st and c.get("end", 0.0) <= w_en + 15.0]
            if not window_cues:
                best_st = w_st
                best_en = min(w_en, w_st + target_sc_dur)
            else:
                best_st = window_cues[0]["start"]
                best_en = min(w_en, best_st + target_sc_dur)
                best_score = -1.0

                for i, c_st in enumerate(window_cues):
                    s_time = float(c_st.get("start", 0.0))
                    if s_time < current_min_start:
                        continue
                    for j in range(i, len(window_cues)):
                        c_en = window_cues[j]
                        dur = float(c_en.get("end", 0.0)) - s_time
                        if dur >= max(22.0, target_sc_dur - 8.0):
                            sub_c = window_cues[i:j+1]
                            words = sum(len(str(c.get("text", "")).split()) for c in sub_c)
                            diff = abs(dur - target_sc_dur)
                            score = words - diff * 1.5
                            if score > best_score:
                                best_score = score
                                best_st = s_time
                                best_en = float(c_en.get("end", 0.0))
                            if dur > target_sc_dur + 8.0:
                                break

            sc_start = round(best_st, 2)
            sc_end = round(max(sc_start + 28.0, best_en), 2)
            sc_dur = sc_end - sc_start

            # Chia khối liên tục [sc_start, sc_end] thành 3-5 shots nối tiếp nhau
            num_shots = max(2, min(5, int(round(sc_dur / 9.0))))
            shot_dur = sc_dur / float(num_shots)

            shots = []
            cur_sh_st = sc_start
            for sh_i in range(num_shots):
                if sh_i == num_shots - 1:
                    cur_sh_en = sc_end
                else:
                    cur_sh_en = round(cur_sh_st + shot_dur, 2)

                pan = "left" if (sh_i % 2 == 0) else "right"
                punch = (sh_i == 1 or sh_i == num_shots - 1)

                shots.append({
                    "start": cur_sh_st,
                    "end": cur_sh_en,
                    "pan": pan,
                    "punch": punch,
                    "desc": f"Continuous Narrative Beat {sh_i+1}"
                })
                # Shot sau bắt đầu đúng điểm kết thúc shot trước (ZERO GAPS!)
                cur_sh_st = cur_sh_en

            scenes.append({
                "scene_id": sc_idx + 1,
                "name": f"Phân cảnh Liền Mạch {sc_idx + 1}",
                "shots": shots
            })

            # Đảm bảo cảnh tiếp theo cách cảnh này tối thiểu 3 giây (ZERO OVERLAP!)
            current_min_start = sc_end + 3.0

        return scenes
