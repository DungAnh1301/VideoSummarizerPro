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

        # Tốc độ phát video cơ bản theo config (VD: 1.2x)
        base_speed = float(config.get("speed") or config.get("source_speed") or config.get("playback_speed") or 1.0)
        base_speed = max(0.5, min(3.0, base_speed))

        # Thời lượng video xuất xưởng mục tiêu (chuẩn ~90s)
        target_final_duration = float(config.get("target_part_duration") or target_part_duration or 90.0)

        # Ngân sách thời lượng thô (Raw Budget) gom từ video gốc để sau khi tăng tốc đạt đúng ~90s
        target_raw_budget = round(target_final_duration * base_speed, 2)

        # Tự động chọn 4 hoặc 5 cảnh tùy theo ngân sách thô
        window_duration = max(30.0, end_bound - start_bound)
        if target_raw_budget <= 115.0 or window_duration < 130.0:
            target_scene_count = 4
        else:
            target_scene_count = 5

        min_dur = round(target_raw_budget * 0.94, 2)
        max_dur = round(target_raw_budget * 1.02, 2)

        _log(f"🎬 [ĐẠO DIỄN 2] Đạo diễn {target_scene_count} phân cảnh LIỀN MẠCH (Ngân sách thô: {target_raw_budget:.1f}s -> Xuất xưởng ~{target_final_duration:.0f}s @ {base_speed}x) cho Part {part_index}: {title}...")

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
        use_cloud_llm = bool(config.get("use_cloud_llm_storyboard", False))
        if use_cloud_llm:
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
                            _log(f"✅ [ĐẠO DIỄN 2] AI Cloud đã phê duyệt {len(data['scenes'])} phân cảnh liền mạch (~20s/cảnh) cho Part {part_index}!")
                            sanitized = cls._sanitize_continuous_scenes(data["scenes"], start_bound, end_bound, min_dur=min_dur, max_dur=max_dur, target_scene_count=target_scene_count)
                            sanitized = cls._apply_footage_swapping_matrix(sanitized, cues, total_duration, start_bound=start_bound, end_bound=end_bound, log_fn=_log)
                            return {
                                "part_index": part_index,
                                "title": title,
                                "scenes": sanitized
                            }
                except Exception as e:
                    logger.warning(f"Lỗi parse Storyboard Spec JSON: {e}")

        # Đạo Diễn Nội Dung Cốt Truyện Bản Địa Tốc Độ Cao (Complete Thought Narrative Engine - 0.01s)
        _log(f"⚡ [ĐẠO DIỄN 2] Phân tích cốt truyện trọn vẹn ý tứ (Zero Mid-Sentence Cut) cho Part {part_index}...")
        scenes = cls._build_heuristic_continuous_scenes(
            cues=part_cues,
            start_bound=start_bound,
            end_bound=end_bound,
            target_scene_count=target_scene_count,
            target_part_dur=target_raw_budget
        )
        sanitized = cls._sanitize_continuous_scenes(scenes, start_bound, end_bound, min_dur=min_dur, max_dur=max_dur, target_scene_count=target_scene_count)
        # ÁP DỤNG MA TRẬN ĐẠO DIỄN: 50% SOURCE GỐC, 50% B-ROLL CÙNG NGỮ CẢNH PART
        sanitized = cls._apply_footage_swapping_matrix(sanitized, cues, total_duration, start_bound=start_bound, end_bound=end_bound, log_fn=_log)

        tot_dur = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in sanitized if sc.get("shots"))
        tot_shots = sum(len(sc.get("shots", [])) for sc in sanitized)
        _log(f"📋 [KỊCH BẢN CHI TIẾT PART {part_index}] Dự kiến: {tot_dur:.2f}s ({len(sanitized)} Cảnh, {tot_shots} Cú máy):")
        for sc in sanitized:
            shots = sc.get("shots", [])
            if not shots:
                continue
            sc_st = shots[0]["start"]
            sc_en = shots[-1]["end"]
            sc_dur = sc_en - sc_st
            _log(f"   🎬 Cảnh {sc.get('scene_id')}: [{sc_st:.2f}s -> {sc_en:.2f}s] ({sc_dur:.2f}s | {len(shots)} cú máy):")
            for k, sh in enumerate(shots):
                sh_dur = float(sh["end"]) - float(sh["start"])
                punch_str = " 🔥 [PUNCH ZOOM 1.18x]" if sh.get("punch") else ""
                role = str(sh.get("camera_role", "shot"))
                pan = str(sh.get("pan", "center")).upper()
                v_st = float(sh.get("video_start", sh["start"]))
                v_en = float(sh.get("video_end", sh["end"]))
                is_anc = bool(sh.get("is_anchor", True))
                tag = "🌟 [GỐC]" if is_anc else f"🔄 [B-ROLL: {v_st:.1f}s]"
                _log(f"      • Cú máy {k+1}: Audio=[{sh['start']:.2f}s -> {sh['end']:.2f}s] | Video=[{v_st:.2f}s -> {v_en:.2f}s] ({sh_dur:.2f}s) | Góc: {pan} ({role}){punch_str} {tag}")

        return {
            "part_index": part_index,
            "title": title,
            "scenes": sanitized
        }

    @classmethod
    def _apply_footage_swapping_matrix(
        cls,
        scenes: List[Dict[str, Any]],
        cues: List[Dict[str, Any]],
        total_duration: float,
        start_bound: float = 0.0,
        end_bound: float = 0.0,
        log_fn=None
    ) -> List[Dict[str, Any]]:
        """Quy tắc Ma Trận Cân Bằng 50:50 & B-Roll Cùng Ngữ Cảnh (Context-Aware 50:50 Matrix):
        - ÂM THANH: 100% chuẩn liền mạch theo từng phân cảnh (Zero Audio Cut).
        - HÌNH ẢNH (Tỷ lệ 50:50 Điện Ảnh):
          + 50% cú máy giữ SOURCE GỐC (Anchor Shot): Thiết lập bối cảnh, mặt người nói/hành động chính.
          + 50% cú máy tráo đổi HÌNH ẢNH B-ROLL MINH HỌA (Cutaway Shots) xen kẽ nhịp nhàng.
            • Cảnh 2 shot: Shot 1 GỐC, Shot 2 B-ROLL (50% : 50%)
            • Cảnh 3 shot: Shot 1 GỐC, Shot 2 B-ROLL, Shot 3 GỐC (Kẹp B-roll ở giữa, mở và kết bằng người nói)
            • Cảnh 4 shot: Shot 1 GỐC, Shot 2 B-ROLL, Shot 3 GỐC, Shot 4 B-ROLL (50% : 50%)
        - ĐẠO DIỄN CHỌN B-ROLL CÙNG NGỮ CẢNH (Context-Aware Localized Pool):
          + Toàn bộ B-roll được ưu tiên lấy NGAY TRONG KHUNG THỜI GIAN CỦA PART ĐÓ (hoặc phân đoạn lân cận +-45s).
          + Nhờ vậy, hình ảnh B-roll minh họa 100% đồng nhất: cùng trang phục, cùng bối cảnh, cùng chi tiết máy/xe đang làm!
          + Tuyệt đối không bao giờ lấy B-roll xa lạ ở tận cuối video (phút 30).
          + Cách ly an toàn >= 20s so với câu thoại đang nói, và không trùng lặp các mốc B-roll giữa các cảnh.
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        # 1. Xây dựng Kho B-Roll cùng ngữ cảnh của Part (Local Context Pool)
        w_st = max(25.0, start_bound - 30.0) if start_bound > 0 else 25.0
        w_en = min(total_duration - 5.0, end_bound + 45.0) if (end_bound > 0 and total_duration > 0) else total_duration

        local_brolls = []
        for i in range(len(cues) - 1):
            g_st = float(cues[i]["end"])
            g_en = float(cues[i + 1]["start"])
            gap = g_en - g_st
            # Ưu tiên các khoảng lặng không lời thoại ngay trong khung thời gian của Part
            if gap >= 2.0 and g_st >= w_st and g_en <= w_en:
                local_brolls.append({
                    "start": round(g_st, 2),
                    "end": round(g_en, 2),
                    "dur": round(gap, 2),
                    "type": "local_gap"
                })

        local_brolls.sort(key=lambda x: x["dur"], reverse=True)

        # Lấy thêm các mốc visual sampling trong khung Part để làm phong phú nguồn B-roll
        span = w_en - w_st
        if span > 20.0:
            step = max(18.0, span / 18.0)
            curr = w_st + 12.0
            while curr < w_en - 6.0:
                local_brolls.append({
                    "start": round(curr, 2),
                    "end": round(curr + 6.0, 2),
                    "dur": 6.0,
                    "type": "local_sample"
                })
                curr += step

        # Kho mở rộng lân cận (Extended Window) nếu Part quá ít cảnh
        ext_st = max(25.0, start_bound - 90.0) if start_bound > 0 else 25.0
        ext_en = min(total_duration - 5.0, end_bound + 120.0) if (end_bound > 0 and total_duration > 0) else total_duration
        ext_brolls = []
        for i in range(len(cues) - 1):
            g_st = float(cues[i]["end"])
            g_en = float(cues[i + 1]["start"])
            gap = g_en - g_st
            if gap >= 2.0 and g_st >= ext_st and g_en <= ext_en:
                ext_brolls.append({
                    "start": round(g_st, 2),
                    "end": round(g_en, 2),
                    "dur": round(gap, 2),
                    "type": "ext_gap"
                })

        used_broll_ranges = []

        for sc_idx, sc in enumerate(scenes):
            shots = sc.get("shots", [])
            num_shots = len(shots)
            if not shots:
                continue

            sc_st = float(shots[0]["start"])
            sc_en = float(shots[-1]["end"])

            # Đạo diễn áp dụng tỷ lệ 50:50 xen kẽ (Shot chẵn: Gốc, Shot lẻ: B-roll)
            num_brolls = num_shots // 2
            num_anchors = num_shots - num_brolls

            _log(f"🎬 [ĐẠO DIỄN CẢNH {sc.get('scene_id', sc_idx+1)}] {num_shots} cú máy | Tỷ lệ 50:50: {num_anchors} cú máy GỐC CHUẨN, {num_brolls} cú máy B-ROLL CÙNG NGỮ CẢNH ({start_bound:.0f}s - {end_bound:.0f}s):")

            for k, sh in enumerate(shots):
                st = float(sh["start"])
                en = float(sh["end"])
                dur = max(0.5, en - st)

                sh["audio_start"] = round(st, 2)
                sh["audio_end"] = round(en, 2)

                # TỶ LỆ 50:50 XEN KẼ
                is_anchor = (k % 2 == 0)

                if is_anchor:
                    # CÚ MÁY SOURCE GỐC (Khớp mặt người nói & bối cảnh câu chuyện)
                    sh["is_anchor"] = True
                    sh["video_start"] = round(st, 2)
                    sh["video_end"] = round(en, 2)
                    sh["desc"] = f"{sh.get('desc', 'Shot')} [SOURCE GỐC CHUẨN]"
                    _log(f"   🌟 Cú máy {k+1}: Audio=[{st:.2f}s -> {en:.2f}s] | Video=[{st:.2f}s -> {en:.2f}s] (SOURCE GỐC CHUẨN)")
                else:
                    # CÚ MÁY B-ROLL MINH HỌA (Lấy trong cùng Part, cách ly >= 20s, không trùng lặp)
                    sh["is_anchor"] = False
                    cand = None

                    # Ưu tiên 1: Kho B-roll cùng ngữ cảnh Part
                    for c in local_brolls:
                        c_st = float(c["start"])
                        # Cách ly an toàn với cảnh hiện tại
                        if abs(c_st - sc_st) >= 20.0 and not (sc_st - 5.0 <= c_st <= sc_en + 5.0):
                            if not any(abs(c_st - u_st) < 6.0 for u_st, u_en in used_broll_ranges):
                                if c_st + dur < total_duration - 1.0:
                                    cand = c
                                    break

                    # Ưu tiên 2: Kho lân cận mở rộng
                    if cand is None:
                        for c in ext_brolls:
                            c_st = float(c["start"])
                            if abs(c_st - sc_st) >= 25.0 and not (sc_st - 5.0 <= c_st <= sc_en + 5.0):
                                if not any(abs(c_st - u_st) < 6.0 for u_st, u_en in used_broll_ranges):
                                    if c_st + dur < total_duration - 1.0:
                                        cand = c
                                        break

                    if cand is not None:
                        v_st = round(float(cand["start"]), 2)
                    else:
                        # Dự phòng cục bộ thông minh trong chính Part đó
                        offset = 35.0 + float(len(used_broll_ranges)) * 18.0
                        v_st = round((sc_st + offset - w_st) % max(1.0, w_en - w_st - dur) + w_st, 2)

                    v_en = round(v_st + dur, 2)
                    used_broll_ranges.append((v_st, v_en))

                    sh["video_start"] = v_st
                    sh["video_end"] = v_en
                    sh["desc"] = f"{sh.get('desc', 'Shot')} [B-ROLL CÙNG PART: {v_st:.1f}s -> {v_en:.1f}s]"
                    _log(f"   🔄 Cú máy {k+1}: Audio=[{st:.2f}s -> {en:.2f}s] | Video=[{v_st:.2f}s -> {v_en:.2f}s] (B-ROLL CÙNG PART {start_bound:.0f}s-{end_bound:.0f}s)")

        return scenes

    @classmethod
    def _subdivide_long_shots(cls, shots: List[Dict[str, Any]], max_shot_dur: float = 12.0) -> List[Dict[str, Any]]:
        """Chỉ chia nhỏ những shot quá dài (> 12.0s) để giữ nhịp tự nhiên 5s - 8s,
        tuyệt đối không băm vụn cú máy xuống 1-2s gây giật mắt!
        """
        camera_roles_cycle = ["wide_zoom_out", "speaker_left", "closeup_zoom_in", "speaker_right"]
        final_shots = []
        for sh_idx, sh in enumerate(shots):
            st = float(sh["start"])
            en = float(sh["end"])
            dur = en - st
            if dur <= max_shot_dur:
                final_shots.append(sh)
                continue

            # Chia thành N sub-shots cân đối ~6s - 7.5s
            num_sub = max(2, int(round(dur / 6.5)))
            sub_step = dur / float(num_sub)
            for k in range(num_sub):
                sub_st = round(st + k * sub_step, 2)
                sub_en = round(st + (k + 1) * sub_step if k + 1 < num_sub else en, 2)
                role = camera_roles_cycle[(sh_idx + k) % len(camera_roles_cycle)]
                pan = "center" if role == "wide_zoom_out" else ("left" if k % 2 == 0 else "right")
                punch = (role == "closeup_zoom_in")
                final_shots.append({
                    "start": sub_st,
                    "end": sub_en,
                    "pan": pan,
                    "punch": punch,
                    "camera_role": role,
                    "desc": f"{sh.get('desc', 'Shot')} (Cut {k+1}/{num_sub})"
                })
        return final_shots

    @classmethod
    def _sanitize_continuous_scenes(
        cls,
        scenes: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        min_dur: float = 75.0,
        max_dur: float = 115.0,
        target_scene_count: int = 4
    ) -> List[Dict[str, Any]]:
        """Chuẩn hóa và khóa cứng 100% tính liền mạch (Strict Continuity):
        1. Bên trong mỗi cảnh (~20s): shot[k+1].start == shot[k].end (KHÔNG HỞ KHOẢNG TRỐNG NÀO).
        2. Giữa các cảnh: Scene[i+1].start >= Scene[i].end + 2.5s (TRIỆT TIÊU 100% TRÙNG LẶP FOOTAGE).
        3. Tổng thời lượng đảm bảo trong khung chuẩn [min_dur, max_dur] (chuẩn ~90s).
        4. TUYỆT ĐỐI KHÔNG để bất kỳ shot đơn lẻ nào vượt quá 12.0s (tự động cắt góc máy linh hoạt).
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

        # Kiểm tra tổng thời lượng Part (Cam kết trong khoảng [min_dur, max_dur])
        tot = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in cleaned_scenes if sc.get("shots"))

        # Cắt bớt cảnh thừa CHỈ KHI số cảnh vượt quá target_scene_count (ví dụ sinh ra 5 cảnh mà target chỉ cần 4)
        while len(cleaned_scenes) > target_scene_count and tot > max_dur:
            cleaned_scenes.pop()
            tot = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in cleaned_scenes if sc.get("shots"))

        # Nếu tổng thời lượng vẫn vượt max_dur, tỉa bớt shot cuối của cảnh cuối để khớp khung ngân sách
        if tot > max_dur and cleaned_scenes:
            excess = tot - max_dur
            last_sc_shots = cleaned_scenes[-1]["shots"]
            if last_sc_shots:
                cur_end = float(last_sc_shots[-1]["end"])
                cur_st = float(last_sc_shots[-1]["start"])
                new_end = max(cur_st + 3.0, cur_end - excess)
                last_sc_shots[-1]["end"] = round(new_end, 2)
                tot = sum(sc["shots"][-1]["end"] - sc["shots"][0]["start"] for sc in cleaned_scenes if sc.get("shots"))

        # Bù thêm nếu thiếu dưới min_dur
        if tot < min_dur and cleaned_scenes:
            deficit = min_dur - tot
            last_sc_shots = cleaned_scenes[-1]["shots"]
            if last_sc_shots:
                last_sc_shots[-1]["end"] = round(min(end_bound, last_sc_shots[-1]["end"] + deficit + 0.5), 2)

        # KHÓA CỨNG: Tự động chia nhỏ mọi shot > 12.0s
        for sc in cleaned_scenes:
            if sc.get("shots"):
                sc["shots"] = cls._subdivide_long_shots(sc["shots"], max_shot_dur=12.0)

        return cleaned_scenes

    DANGLING_WORDS = {
        'so', 'and', 'but', 'because', 'for', 'or', 'that', 'to', 'with', 'as',
        'in', 'if', 'while', 'the', 'a', 'an', 'is', 'are', 'was', 'were', 'it',
        'its', 'my', 'your', 'his', 'her', 'our', 'their', 'of', 'at', 'by', 'from'
    }
    TERMINAL_REGEX = re.compile(r'[\.\!\?]+["\')]*$')

    @classmethod
    def is_complete_sentence_end(cls, text: str) -> bool:
        """Kiểm tra câu nói đã kết thúc hoàn chỉnh về ngữ nghĩa chưa (không dừng ở liên từ dở dang)."""
        txt = text.strip()
        if not txt:
            return False
        words = [re.sub(r'[^a-zA-Z0-9]', '', w).lower() for w in txt.split()]
        words = [w for w in words if w]
        if not words:
            return False
        last_word = words[-1]
        if last_word in cls.DANGLING_WORDS:
            return False
        return bool(cls.TERMINAL_REGEX.search(txt)) or len(words) >= 4

    @classmethod
    def group_complete_sentences(cls, cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Nhóm các cues vụn thành các câu nói trọn vẹn ngữ nghĩa (Complete Sentences)."""
        sentences: List[Dict[str, Any]] = []
        cur_cues: List[Dict[str, Any]] = []

        for i, c in enumerate(cues):
            cur_cues.append(c)
            txt = str(c.get("text", "")).strip()
            is_term = bool(cls.TERMINAL_REGEX.search(txt))
            gap = float(cues[i + 1]["start"]) - float(c["end"]) if i < len(cues) - 1 else 0.0

            last_word = txt.split()[-1].lower() if txt.split() else ""
            last_word_clean = re.sub(r"[^a-z]", "", last_word)

            is_boundary = False
            if is_term and last_word_clean not in cls.DANGLING_WORDS:
                is_boundary = True
            elif gap >= 0.85 and last_word_clean not in cls.DANGLING_WORDS:
                is_boundary = True
            elif gap >= 1.8:
                is_boundary = True

            if is_boundary:
                s_text = " ".join(c_item.get("text", "").strip() for c_item in cur_cues)
                sentences.append({
                    "start": float(cur_cues[0]["start"]),
                    "end": float(cur_cues[-1]["end"]),
                    "text": s_text,
                    "cues": cur_cues
                })
                cur_cues = []

        if cur_cues:
            s_text = " ".join(c_item.get("text", "").strip() for c_item in cur_cues)
            sentences.append({
                "start": float(cur_cues[0]["start"]),
                "end": float(cur_cues[-1]["end"]),
                "text": s_text,
                "cues": cur_cues
            })

        return sentences

    @classmethod
    def _split_scene_by_dialogue_turns(
        cls,
        cues: List[Dict[str, Any]],
        sc_start: float,
        sc_end: float,
        sc_idx: int,
        scene_sents: Optional[List[Dict[str, Any]]] = None
    ) -> List[Dict[str, Any]]:
        """Chia phân cảnh liền mạch thành 3-4 shots khớp chính xác với nhịp bắt đầu câu nói
        hoặc lượt đổi người nói ('>>') để người xem hiểu trọn vẹn từng câu:
        - Không cắt giữa chừng câu nói!
        - Shot 1: Mở đầu câu chuyện / Bối cảnh (wide_zoom_out)
        - Shot 2: Người đối thoại / Phân tích tiếp theo (speaker_left)
        - Shot 3: Câu nói cao trào / Biểu cảm sốc (closeup_zoom_in)
        - Shot 4: Phản ứng đối phương / Câu kết luận (speaker_right)
        """
        sc_dur = max(6.0, sc_end - sc_start)
        # Đạo diễn chỉ định số shot linh hoạt dựa trên thời lượng và số câu:
        if sc_dur < 14.0:
            target_num_shots = 2
        elif sc_dur < 22.0:
            target_num_shots = 3
        else:
            target_num_shots = 4 if sc_dur >= 26.0 else 3

        # Nếu có danh sách câu hoàn chỉnh trong cảnh, sử dụng ranh giới câu làm điểm cắt shot
        cut_points = [sc_start]
        if scene_sents and len(scene_sents) >= 2:
            s_starts = [s["start"] for s in scene_sents if sc_start + 4.5 < s["start"] < sc_end - 4.0]
            if s_starts:
                # Lấy các điểm cắt câu cách nhau tối thiểu 4.8s theo chỉ định của Đạo diễn
                last_p = sc_start
                for st in s_starts:
                    if st - last_p >= 4.8 and sc_end - st >= 4.0:
                        cut_points.append(round(st, 2))
                        last_p = st
                        if len(cut_points) >= target_num_shots:
                            break

        if len(cut_points) == 1:
            # Fallback nếu cảnh ít câu: tìm mốc đổi người nói '>>' hoặc chia đều theo chỉ định của Đạo diễn
            ideal_step = sc_dur / float(target_num_shots)
            scene_cues = [
                c for c in cues
                if float(c.get("start", 0.0)) > sc_start + 4.0 and float(c.get("start", 0.0)) < sc_end - 4.0
            ]
            for k in range(1, target_num_shots):
                target_t = sc_start + k * ideal_step
                best_t = target_t
                best_cost = 999.0
                for c in scene_cues:
                    c_st = float(c.get("start", 0.0))
                    if c_st <= cut_points[-1] + 4.2 or c_st >= sc_end - 4.0:
                        continue
                    dist = abs(c_st - target_t)
                    txt = str(c.get("text", "")).strip()
                    speaker_bonus = -0.9 if txt.startswith(">>") else 0.0
                    cost = dist + speaker_bonus
                    if cost < best_cost:
                        best_cost = cost
                        best_t = c_st
                cut_points.append(round(best_t, 2))

        cut_points.append(round(sc_end, 2))

        roles_pool = ["wide_zoom_out", "speaker_left", "closeup_zoom_in", "speaker_right"]
        shots = []
        for sh_i in range(len(cut_points) - 1):
            st = cut_points[sh_i]
            en = cut_points[sh_i + 1]
            role = roles_pool[sh_i % len(roles_pool)]
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
        return cls._subdivide_long_shots(shots, max_shot_dur=12.0)

    @classmethod
    def _build_heuristic_continuous_scenes(
        cls,
        cues: List[Dict[str, Any]],
        start_bound: float,
        end_bound: float,
        target_scene_count: int = 5,
        target_part_dur: float = 95.0
    ) -> List[Dict[str, Any]]:
        """Thuật toán Hollywood Complete Thought Narrative:
        - Gom các câu nói thành cụm ý niệm hoàn chỉnh (Setup -> Conflict/Action -> Conclusion).
        - Tuyệt đối KHÔNG cắt cụt câu nói đang dở dang (Zero Mid-Sentence Cut).
        - Mỗi cảnh dài ~18s - 30s (cho phép nới rộng để câu nói kết thúc trọn vẹn ở dấu chấm câu).
        - Nếu là phân đoạn hành động không lời (cưa xe, hàn xe, trailer), giữ gọn gàng 6s-10s tạo nhịp kịch tính.
        """
        all_sentences = cls.group_complete_sentences(cues)
        part_sentences = [
            s for s in all_sentences
            if s["start"] >= max(0.0, start_bound - 2.0) and s["end"] <= end_bound + 15.0
        ]
        # BỘ LỌC CHỐNG RÁC THỊ GIÁC (Anti-Choppy Teaser Guard):
        # Đoạn 0s - 22s thường là trailer teaser cắt cảnh chớp nhoáng (3-8 cuts trong 2s).
        # Tự động bỏ qua đoạn teaser giật này để cảnh mở đầu luôn bắt đầu từ mốc video ổn định, êm mượt!
        if start_bound < 15.0:
            part_sentences = [
                s for s in part_sentences
                if s["start"] >= 22.0 or (s["end"] - s["start"] >= 10.0 and s["start"] >= 15.0)
            ]

        window_len = max(60.0, end_bound - start_bound)
        if target_part_dur <= 115.0 or window_len < 130.0:
            target_scene_count = 4
        else:
            target_scene_count = min(5, target_scene_count)

        window_size = window_len / float(target_scene_count)
        scenes = []
        current_min_start = start_bound

        for sc_idx in range(target_scene_count):
            w_st = max(current_min_start, start_bound + sc_idx * window_size)
            w_en = start_bound + (sc_idx + 1) * window_size
            if sc_idx == target_scene_count - 1:
                w_en = end_bound

            cand_sents = [s for s in part_sentences if s["start"] >= w_st and s["end"] <= w_en + 20.0]
            best_cluster = []
            best_score = -999.0

            ideal_sc_dur = max(16.0, min(28.0, target_part_dur / float(target_scene_count)))
            min_sc_dur = max(12.0, ideal_sc_dur - 4.5)
            max_sc_dur = ideal_sc_dur + 3.5

            if cand_sents:
                for i in range(len(cand_sents)):
                    cluster = []
                    for j in range(i, len(cand_sents)):
                        cluster.append(cand_sents[j])
                        dur = cluster[-1]["end"] - cluster[0]["start"]
                        last_text = cluster[-1]["text"]

                        if min_sc_dur <= dur <= max_sc_dur and cls.is_complete_sentence_end(last_text):
                            words = sum(len(s["text"].split()) for s in cluster)
                            diff = abs(dur - ideal_sc_dur)
                            has_dialogue = any(">>" in s["text"] for s in cluster)
                            score = words - diff * 2.5 + (15.0 if has_dialogue else 0.0)
                            if score > best_score:
                                best_score = score
                                best_cluster = list(cluster)
                        elif dur > max_sc_dur:
                            break

                if not best_cluster:
                    # Fallback lấy cụm câu hợp lý nhất kết thúc trọn vẹn
                    for k in range(len(cand_sents)):
                        dur = cand_sents[k]["end"] - cand_sents[0]["start"]
                        if dur >= min_sc_dur and cls.is_complete_sentence_end(cand_sents[k]["text"]):
                            best_cluster = cand_sents[:k + 1]
                            break
                    if not best_cluster:
                        best_cluster = [cand_sents[0]]

            if best_cluster:
                sc_start = round(best_cluster[0]["start"], 2)
                # Thêm khoảng đệm thở reaction hold 0.35s sau khi dứt câu cuối, tuyệt đối không chạm vào câu kế tiếp
                last_end = float(best_cluster[-1]["end"])
                next_sents = [s for s in part_sentences if float(s["start"]) > last_end + 0.05]
                next_st = float(next_sents[0]["start"]) if next_sents else end_bound
                hold = max(last_end, min(last_end + 0.35, next_st - 0.15))
                sc_end = round(hold, 2)
            else:
                # Trường hợp đoạn video là Action Montage / Trailer (không có lời thoại)
                sc_start = round(w_st, 2)
                sc_end = round(min(end_bound, sc_start + ideal_sc_dur), 2)

            shots = cls._split_scene_by_dialogue_turns(
                cues=cues,
                sc_start=sc_start,
                sc_end=sc_end,
                sc_idx=sc_idx,
                scene_sents=best_cluster
            )

            scenes.append({
                "scene_id": sc_idx + 1,
                "name": f"Phân cảnh Hoàn Chỉnh {sc_idx + 1}",
                "shots": shots
            })

            # Đảm bảo cảnh tiếp theo cách cảnh này tối thiểu 2.5s (0% LẶP FOOTAGE)
            current_min_start = sc_end + 2.5

        return scenes
