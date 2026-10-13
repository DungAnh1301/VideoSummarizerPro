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
        """Tạo kịch bản 4-5 phân cảnh LIỀN MẠCH (~18s - 22s mỗi cảnh, trung bình ~20s) cho một Part.
        - TUYỆT ĐỐI KHÔNG CẮT VỤN 2-3s nhảy cóc bên trong cảnh!
        - Mỗi cảnh 20s là một dòng thời gian chảy liên tục 100% (câu chuyện, hội thoại nguyên vẹn có đầu có đuôi).
        - Bên trong mỗi cảnh 20s, chia thành 2-3 góc máy nối tiếp nhau không hở thời gian (shot[i+1].start == shot[i].end).
        - Giữa 4-5 cảnh có khoảng cách an toàn (>= 2.5s), tuyệt đối không bao giờ lùi giờ hay lặp lại video/b-roll.
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
        start_bound = max(float(time_range[0]), float(part_info.get("start_sec", 0.0)))
        end_bound = float(time_range[1])
        goal = part_info.get("narrative_goal", "Story development")
        title = part_info.get("part_title", f"Part {part_index}")

        # Tự động chọn 4 hoặc 5 cảnh (~20s/cảnh) tùy theo độ dài cửa sổ thời gian
        window_duration = max(30.0, end_bound - start_bound)
        if window_duration < 130.0:
            target_scene_count = 4
        else:
            target_scene_count = 5

        _log(f"🎬 [ĐẠO DIỄN 2] Đạo diễn {target_scene_count} phân cảnh LIỀN MẠCH (~20s/cảnh) cho Part {part_index}: {title} ({start_bound:.1f}s -> {end_bound:.1f}s)...")

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
1. 4 TO 5 CONTINUOUS 20-SECOND SCENES (NO MICRO-FRAGMENTATION):
   - You must select exactly {target_scene_count} CONTINUOUS narrative scenes (each lasting ~18s to 22s of uninterrupted dialogue and action).
   - DO NOT chop a scene into disconnected 3-second fragments! Every 20s scene must be a continuous slice of the video where characters talk and act with full beginning-to-end context!
2. SEAMLESS SHOTS INSIDE EACH 20s SCENE:
   - Inside each 20s scene, divide the continuous timeframe into 2 to 3 consecutive shots (~6.5s - 7.5s each) for dynamic camera movement (pan="left", pan="right", punch=true).
   - MANDATORY: There are ZERO time gaps between shots inside the scene: shot[k+1].start MUST EQUAL shot[k].end!
3. ZERO REPETITION & NO OVERLAP BETWEEN SCENES:
   - Scene[i+1] start MUST be strictly greater than Scene[i] end (at least 2.5s later). Never repeat footage!
4. CLIFFHANGER: The final scene must end on a suspenseful reveal or twist!
"""

        user_prompt = f"""Target Part Duration: ~{target_part_duration:.0f}s ({target_scene_count} scenes, each ~20s continuous)
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
      "name": "Scene 1 Description",
      "shots": [
        {{"start": 10.0, "end": 16.5, "speaker": "Host", "pan": "left", "punch": false, "desc": "Setup"}},
        {{"start": 16.5, "end": 23.5, "speaker": "Guest", "pan": "right", "punch": true, "desc": "Reaction"}},
        {{"start": 23.5, "end": 30.0, "speaker": "Host", "pan": "left", "punch": false, "desc": "Follow-up"}}
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
                    if isinstance(data, dict) and data.get("scenes") and len(data["scenes"]) >= 4:
                        _log(f"✅ [ĐẠO DIỄN 2] AI đã phê duyệt {len(data['scenes'])} phân cảnh liền mạch (~20s/cảnh) cho Part {part_index}!")
                        sanitized = cls._sanitize_continuous_scenes(data["scenes"], start_bound, end_bound, min_dur=75.0)
                        return {
                            "part_index": part_index,
                            "title": title,
                            "scenes": sanitized
                        }
            except Exception as e:
                logger.warning(f"Lỗi parse Storyboard Spec JSON: {e}")

        # Heuristic fallback: Tự động xây dựng 4-5 khối phân cảnh LIỀN MẠCH (~20s/cảnh) chuẩn Hollywood
        _log(f"ℹ️ [ĐẠO DIỄN 2] Tự động chọn {target_scene_count} phân cảnh LIỀN MẠCH (~20s/cảnh) không cắt vụn...")
        scenes = cls._build_heuristic_continuous_scenes(
            cues=part_cues,
            start_bound=start_bound,
            end_bound=end_bound,
            target_scene_count=target_scene_count,
            target_part_dur=target_part_duration
        )
        sanitized = cls._sanitize_continuous_scenes(scenes, start_bound, end_bound, min_dur=75.0)
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
        min_dur: float = 75.0
    ) -> List[Dict[str, Any]]:
        """Chuẩn hóa và khóa cứng 100% tính liền mạch (Strict Continuity):
        1. Bên trong mỗi cảnh (~20s): shot[k+1].start == shot[k].end (KHÔNG HỞ KHOẢNG TRỐNG NÀO).
        2. Giữa các cảnh: Scene[i+1].start >= Scene[i].end + 2.5s (TRIỆT TIÊU 100% TRÙNG LẶP FOOTAGE).
        3. Tổng thời lượng đảm bảo >= min_dur (luôn > 1 phút).
        """
        if not scenes:
            return scenes

        cleaned_scenes = []
        last_scene_end = start_bound
        camera_roles_cycle = ["wide_zoom_out", "speaker_left", "closeup_zoom_in", "speaker_right"]

        for sc_idx, sc in enumerate(scenes):
            raw_shots = sc.get("shots", [])
            if not raw_shots:
                continue

            # Mốc bắt đầu cảnh này phải sau cảnh trước ít nhất 2.5s
            first_sh_st = float(raw_shots[0].get("start", 0.0))
            if sc_idx > 0 and first_sh_st < last_scene_end + 2.5:
                offset = (last_scene_end + 2.5) - first_sh_st
            else:
                offset = 0.0

            cur_shot_st = first_sh_st + offset
            clean_shots = []

            for sh_k, sh in enumerate(raw_shots):
                orig_dur = max(3.5, float(sh.get("end", 0.0)) - float(sh.get("start", 0.0)))
                sh_end = cur_shot_st + orig_dur

                role = str(sh.get("camera_role") or camera_roles_cycle[sh_k % len(camera_roles_cycle)])
                pan_default = "center" if role == "wide_zoom_out" else ("left" if sh_k % 2 == 0 else "right")

                item = dict(sh)
                item["start"] = round(cur_shot_st, 2)
                item["end"] = round(sh_end, 2)
                item["pan"] = str(sh.get("pan", pan_default)).lower().strip()
                item["punch"] = bool(sh.get("punch", False) or role == "closeup_zoom_in")
                item["camera_role"] = role

                clean_shots.append(item)
                # Shot kế tiếp bắt đầu CHÍNH XÁC tại điểm kết thúc của shot này
                cur_shot_st = item["end"]

            sc_copy = dict(sc)
            sc_copy["shots"] = clean_shots
            cleaned_scenes.append(sc_copy)
            last_scene_end = clean_shots[-1]["end"]

        # Kiểm tra tổng thời lượng Part
        tot = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in cleaned_scenes if sc["shots"])
        if tot < min_dur and cleaned_scenes:
            deficit = min_dur - tot
            last_sc_shots = cleaned_scenes[-1]["shots"]
            if last_sc_shots:
                last_sc_shots[-1]["end"] = round(min(end_bound, last_sc_shots[-1]["end"] + deficit + 1.0), 2)

        return cleaned_scenes

    @classmethod
    def _split_scene_by_dialogue_turns(
        cls,
        cues: List[Dict[str, Any]],
        sc_start: float,
        sc_end: float,
        sc_idx: int
    ) -> List[Dict[str, Any]]:
        """Chia một phân cảnh liền mạch ~20s [sc_start, sc_end] thành 3-4 góc máy (shots)
        khớp chính xác với nhịp ngắt câu / đổi người nói ('>>') trong phụ đề:
        - Shot 1: Wide / Zoom Out (hoặc Speaker Left) mở bối cảnh
        - Shot 2: Speaker Shift (Cận trung người đối thoại)
        - Shot 3: Emotion Zoom In Close-Up (Phóng cận mặt bắt biểu cảm cao trào)
        - Shot 4: Reaction / Speaker Shift (Phản ứng đối phương / Zoom Out kết cảnh)
        Đảm bảo 100% liền mạch: shot[k+1].start == shot[k].end.
        """
        sc_dur = max(6.0, sc_end - sc_start)
        num_shots = 4 if sc_dur >= 19.0 else 3
        ideal_step = sc_dur / float(num_shots)

        # Tìm các điểm ngắt câu / đổi người nói ('>>') trong khoảng [sc_start + 3.2, sc_end - 3.2]
        scene_cues = [
            c for c in cues
            if float(c.get("start", 0.0)) > sc_start + 2.8 and float(c.get("start", 0.0)) < sc_end - 2.8
        ]

        cut_points = [sc_start]
        for k in range(1, num_shots):
            target_t = sc_start + k * ideal_step
            best_t = target_t
            best_cost = 999.0
            for c in scene_cues:
                c_st = float(c.get("start", 0.0))
                if c_st <= cut_points[-1] + 3.2 or c_st >= sc_end - 3.0:
                    continue
                dist = abs(c_st - target_t)
                if dist <= 2.4:
                    txt = str(c.get("text", "")).strip()
                    # Ưu tiên cao cho điểm đổi người nói '>>'
                    speaker_bonus = -0.9 if txt.startswith(">>") else 0.0
                    cost = dist + speaker_bonus
                    if cost < best_cost:
                        best_cost = cost
                        best_t = c_st
            cut_points.append(round(best_t, 2))
        cut_points.append(round(sc_end, 2))

        roles_4 = ["wide_zoom_out", "speaker_left", "closeup_zoom_in", "speaker_right"]
        roles_3 = ["wide_zoom_out", "closeup_zoom_in", "speaker_right"]
        roles = roles_4 if num_shots == 4 else roles_3

        shots = []
        for sh_i in range(len(cut_points) - 1):
            st = cut_points[sh_i]
            en = cut_points[sh_i + 1]
            role = roles[sh_i % len(roles)]
            if role == "wide_zoom_out":
                pan = "center"
                punch = False
            elif role == "speaker_left":
                pan = "left"
                punch = False
            elif role == "closeup_zoom_in":
                pan = "right" if (sc_idx % 2 == 0) else "left"
                punch = True
            else:
                pan = "right"
                punch = False

            shots.append({
                "start": st,
                "end": en,
                "pan": pan,
                "punch": punch,
                "camera_role": role,
                "desc": f"Scene {sc_idx+1} Shot {sh_i+1} ({role})"
            })
        return shots

    @classmethod
    def _build_heuristic_continuous_scenes(
        cls,
        cues: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        target_scene_count: int = 5,
        target_part_dur: float = 95.0
    ) -> List[Dict[str, Any]]:
        """Thuật toán tìm 4-5 Khối Diễn Biến Liền Mạch (Continuous Story Blocks ~20s/cảnh):
        - Mỗi cảnh dài ~18s - 22s liên tục, không bị gián đoạn hay ngắt vụn.
        - Tìm phân đoạn 20s có mật độ lời thoại cao nhất trong từng phân vùng của Part.
        - Chia 20s đó thành 3-4 shots theo đúng nhịp đổi người nói (Wide Zoom Out -> Speaker Shift -> Close-Up Zoom In -> Reaction).
        """
        window_len = max(60.0, end_bound - start_bound)
        if window_len < 130.0:
            target_scene_count = 4
        else:
            target_scene_count = 5

        target_sc_dur = max(18.5, min(22.0, target_part_dur / float(target_scene_count)))

        scenes = []
        current_min_start = start_bound

        for sc_idx in range(target_scene_count):
            frac_st = sc_idx / float(target_scene_count)
            frac_en = (sc_idx + 1) / float(target_scene_count)
            w_st = max(current_min_start, start_bound + frac_st * window_len)
            w_en = start_bound + frac_en * window_len
            if sc_idx == target_scene_count - 1:
                w_en = end_bound

            window_cues = [c for c in cues if c.get("start", 0.0) >= w_st and c.get("end", 0.0) <= w_en + 8.0]
            if not window_cues:
                best_st = w_st
                best_en = min(end_bound, w_st + target_sc_dur)
            else:
                best_st = max(current_min_start, float(window_cues[0]["start"]))
                best_en = min(end_bound, best_st + target_sc_dur)
                best_score = -999.0

                for i, c_st in enumerate(window_cues):
                    s_time = float(c_st.get("start", 0.0))
                    if s_time < current_min_start:
                        continue
                    # Đảm bảo còn đủ thời gian cho 1 cảnh ~18s trước w_en
                    if s_time > w_en - 17.5 and s_time > current_min_start:
                        break
                    for j in range(i, len(window_cues)):
                        c_en = window_cues[j]
                        dur = float(c_en.get("end", 0.0)) - s_time
                        if dur >= 16.5:
                            sub_c = window_cues[i:j+1]
                            words = sum(len(str(c.get("text", "")).split()) for c in sub_c)
                            diff = abs(dur - target_sc_dur)
                            score = words - diff * 2.5
                            if score > best_score:
                                best_score = score
                                best_st = s_time
                                best_en = float(c_en.get("end", 0.0))
                            if dur > target_sc_dur + 4.5:
                                break

            sc_start = round(max(current_min_start, best_st), 2)
            if end_bound - sc_start < 18.0 and end_bound - 19.5 >= current_min_start:
                sc_start = round(end_bound - 19.5, 2)

            sc_end = round(min(end_bound, max(sc_start + 18.0, min(sc_start + 23.0, best_en))), 2)

            shots = cls._split_scene_by_dialogue_turns(cues, sc_start, sc_end, sc_idx)

            scenes.append({
                "scene_id": sc_idx + 1,
                "name": f"Phân cảnh Liền Mạch {sc_idx + 1}",
                "shots": shots
            })

            # Đảm bảo cảnh tiếp theo cách cảnh này tối thiểu 2.5 giây (ZERO OVERLAP!)
            current_min_start = sc_end + 2.5

        return scenes
