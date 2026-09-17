"""Module Gemini AI phân tích toàn diện kịch bản, lược thừa, cliffhanger và hook cho Chế độ Chia Part.

Hỗ trợ kết nối qua:
1. Google Antigravity CLI (Google OAuth Local / Không cần API Key)
2. Google Gemini REST API (với Gemini API Key người dùng cung cấp)
3. Local Heuristic Fallback (Luôn đảm bảo hệ thống không bao giờ bị dừng)
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger("PartPrunerAI")
if not logger.handlers:
    logging.basicConfig(level=logging.INFO)


def _clean_json_text(raw_text: str) -> str:
    """Loại bỏ markdown code block ```json ... ``` và tìm khối JSON hợp lệ."""
    if not raw_text:
        return ""
    text = raw_text.strip()
    # Tìm khối code block
    code_match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if code_match:
        return code_match.group(1).strip()
    # Tìm từ ngoặc mở { đầu tiên đến ngoặc đóng } cuối cùng
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        return text[first_brace : last_brace + 1].strip()
    return text


def _parse_srt_cues(srt_path: str) -> List[Dict[str, Any]]:
    """Đọc file SRT và trích xuất các cue có timestamp start, end, text."""
    if not srt_path or not os.path.isfile(srt_path):
        return []
    try:
        content = Path(srt_path).read_text(encoding="utf-8-sig", errors="replace")
    except Exception:
        return []

    cues = []
    time_re = re.compile(
        r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{3})"
    )
    blocks = re.split(r"\r?\n\s*\r?\n", content.strip())
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        match = None
        text_lines = []
        for line in lines:
            m = time_re.search(line)
            if m:
                match = m
            elif not line.isdigit() and "-->" not in line:
                text_lines.append(line)

        if match and text_lines:
            s_h, s_m, s_s, s_ms, e_h, e_m, e_s, e_ms = [int(x) for x in match.groups()]
            start_sec = s_h * 3600 + s_m * 60 + s_s + s_ms / 1000.0
            end_sec = e_h * 3600 + e_m * 60 + e_s + e_ms / 1000.0
            cues.append({
                "start": start_sec,
                "end": end_sec,
                "text": " ".join(text_lines)
            })
    return cues


class PartPrunerAI:
    """Bộ phân tích kịch bản thông minh bằng Gemini AI."""

    @classmethod
    def analyze_video_structure(
        cls,
        total_duration: float,
        srt_path: str = "",
        config: Dict[str, Any] = None,
        job_dir: str = "",
        original_title: str = "",
        log_fn=None
    ) -> Dict[str, Any]:
        """Điểm vào chính: Phân tích toàn bộ video để lập kế hoạch tinh lược và chia Part.
        
        Trả về dictionary chuẩn:
        - master_title: str
        - part_titles: List[str]
        - prune_plan: List[dict] (start, end, reason)
        - part_splits: List[float] (N-1 cut points)
        - hooks: { global_hook: dict, individual_hooks: list[dict] }
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        part_count = max(2, int(config.get("part_count", 4)))
        min_part_dur = max(10.0, float(config.get("min_part_duration_sec", 60.0)))
        prune_enabled = bool(config.get("prune_enabled", True))
        prune_mode = str(config.get("prune_mode", "percent"))
        prune_percent = float(config.get("prune_percent", 20.0))
        prune_minutes = float(config.get("prune_minutes", 15.0))
        prune_tol = float(config.get("prune_tolerance", 0.20))
        hook_target_sec = float(config.get("hook_target_sec", 6.0))
        hook_tol = float(config.get("hook_tolerance", 0.50))
        hook_strategy = str(config.get("hook_strategy", "individual"))
        part_prefix = str(config.get("part_label_prefix", "Part"))
        auth_mode = str(config.get("gemini_auth_mode", "antigravity"))
        api_key = str(config.get("gemini_api_key", "")).strip()
        model_name = str(config.get("gemini_model", "gemini-2.5-flash")).strip()

        _log(f"🧠 [AI CHIA PART] Bắt đầu phân tích video: thời lượng {total_duration:.1f}s, mục tiêu {part_count} Parts...")

        # 1. Đọc cues transcript
        cues = _parse_srt_cues(srt_path)
        transcript_sample = ""
        if cues:
            # Tạo bản tóm tắt các dòng thoại kèm mốc thời gian
            step = max(1, len(cues) // 300)  # Giới hạn ~300 cues đại diện để vừa token
            sampled_cues = cues[::step]
            transcript_sample = "\n".join([
                f"[{c['start']:.1f}s - {c['end']:.1f}s]: {c['text']}"
                for c in sampled_cues
            ])
            _log(f"📝 [AI CHIA PART] Nạp transcript: {len(cues)} câu thoại (lấy mẫu {len(sampled_cues)} mốc).")
        else:
            _log("⚠️ [AI CHIA PART] Không có transcript SRT, AI sẽ tính toán theo mốc thời gian video.")

        # 2. Tính toán hạn ngạch prune mục tiêu
        target_prune_sec = 0.0
        prune_min_sec = 0.0
        prune_max_sec = 0.0
        if prune_enabled and total_duration > min_part_dur * 2:
            if prune_mode == "minutes":
                target_prune_sec = min(total_duration * 0.70, prune_minutes * 60.0)
            else:
                target_prune_sec = total_duration * (prune_percent / 100.0)
            prune_min_sec = target_prune_sec * (1.0 - prune_tol)
            prune_max_sec = target_prune_sec * (1.0 + prune_tol)

        hook_min_sec = hook_target_sec * (1.0 - hook_tol)
        hook_max_sec = hook_target_sec * (1.0 + hook_tol)

        # 3. Xây dựng System & User Prompt
        system_prompt = (
            "You are a world-class viral video editor and storytelling director. "
            "Your mission is to analyze a long video and split it into compelling, viral episodic Parts "
            "while removing boring filler segments (Pruning), creating irresistible Cliffhangers at part boundaries, "
            "and hunting high-retention Hook teasers.\n"
            "CRITICAL RULES:\n"
            "1. MASTER & PART TITLES: Must be in the ORIGINAL LANGUAGE of the video (clean, viral, curiosity-driven). "
            "Do NOT include prefixes like 'Part 1:' in part_titles—just the title text.\n"
            "2. PRUNING: Identify filler segments (opening intro bumpers/title cards starting from 0.0s, sponsor segments, boring repetitive parts). "
            "If the video starts with a show intro, channel bumper, or logo sequence, ALWAYS create a prune segment starting from 0.0s to the end of that intro. "
            f"Target total pruned duration: {target_prune_sec:.1f}s (Allowed range: {prune_min_sec:.1f}s to {prune_max_sec:.1f}s). "
            "If pruning is disabled or video is short, return empty prune_plan.\n"
            "3. CLIFFHANGER PART SPLITS: Provide exactly {part_count - 1} cut timestamps. Each split MUST land on a "
            "high-tension Cliffhanger or curiosity trap (e.g. right before a reveal, decision, or shocking event) "
            "so the viewer is desperate to watch the next Part. "
            f"Each Part must be at least {min_part_dur:.1f}s long.\n"
            f"4. HOOKS: A Hook must be {hook_min_sec:.1f}s ~ {hook_max_sec:.1f}s long, showing the absolute peak moment "
            "cut abruptly right before the payoff.\n"
            "5. OUTPUT FORMAT: Output strictly valid JSON without explanation, markdown fences or extra text."
        )

        user_prompt = f"""
VIDEO SPECIFICATIONS:
- Original Title: {original_title or "Untitled"}
- Total Duration: {total_duration:.1f} seconds ({total_duration/60.0:.2f} minutes)
- Requested Part Count: {part_count}
- Minimum Part Duration: {min_part_dur:.1f} seconds
- Pruning Enabled: {prune_enabled}
- Target Prune Duration: {target_prune_sec:.1f}s (Range: {prune_min_sec:.1f}s ~ {prune_max_sec:.1f}s)
- Hook Target Duration: {hook_target_sec:.1f}s (Range: {hook_min_sec:.1f}s ~ {hook_max_sec:.1f}s)

TRANSCRIPT EXCERPT WITH TIMESTAMPS:
{transcript_sample if transcript_sample else "(No spoken transcript available. Plan splits evenly across visual beats.)"}

REQUIRED JSON OUTPUT SCHEMA:
{{
  "master_title": "Short punchy viral title in video's original language",
  "part_titles": [
    "Catchy title for Part 1 in original language",
    "Catchy title for Part 2 in original language",
    "..."
  ],
  "prune_plan": [
    {{"start": 10.5, "end": 45.0, "reason": "Sponsor intro"}},
    {{"start": 400.0, "end": 520.0, "reason": "Repetitive filler discussion"}}
  ],
  "part_splits": [
    350.0,
    720.5,
    1100.0
  ],
  "hooks": {{
    "global_hook": {{"start": 340.0, "end": 346.5, "reason": "Most dramatic climax"}},
    "individual_hooks": [
      {{"part_index": 1, "start": 340.0, "end": 346.0, "reason": "Part 1 cliffhanger peek"}},
      {{"part_index": 2, "start": 710.0, "end": 716.5, "reason": "Part 2 suspense peak"}}
    ]
  }}
}}
Note: part_splits must contain exactly {part_count - 1} sorted ascending cut timestamps.
part_titles must contain exactly {part_count} items.
"""

        raw_response = ""
        # 4. Gửi yêu cầu qua Antigravity CLI hoặc Gemini REST API
        if auth_mode == "api_key" and api_key:
            _log("🌐 [AI CHIA PART] Gửi yêu cầu qua Google Gemini REST API...")
            try:
                raw_response = cls._call_gemini_api(system_prompt, user_prompt, api_key, model_name)
            except Exception as err:
                _log(f"⚠️ [AI CHIA PART] Gemini API gặp lỗi: {err}. Thử chuyển sang Antigravity CLI...")
                raw_response = ""

        if not raw_response:
            _log("⚡ [AI CHIA PART] Gửi yêu cầu qua Antigravity CLI (OAuth Google local)...")
            try:
                from antigravity_processor import AntigravityProcessor
                raw_response = AntigravityProcessor.analyze_prompt(system_prompt, user_prompt, job_dir=job_dir)
            except Exception as err:
                _log(f"⚠️ [AI CHIA PART] Antigravity CLI chưa sẵn sàng hoặc lỗi: {err}")

        # 5. Parse JSON kết quả
        parsed_result = None
        if raw_response:
            try:
                cleaned = _clean_json_text(raw_response)
                data = json.loads(cleaned)
                if isinstance(data, dict):
                    parsed_result = cls._validate_and_sanitize(
                        data, total_duration, part_count, min_part_dur, original_title
                    )
            except Exception as parse_err:
                _log(f"⚠️ [AI CHIA PART] Lỗi parse JSON AI: {parse_err}. Dữ liệu thô: {raw_response[:200]}")

        # 6. Fallback thuật toán nội bộ nếu AI không trả lời hoặc lỗi
        if not parsed_result:
            _log("🛠️ [AI CHIA PART] Kích hoạt Heuristic Fallback Planner (Chia part thông minh theo toán học & cảnh)...")
            parsed_result = cls._fallback_heuristic_plan(
                total_duration=total_duration,
                cues=cues,
                part_count=part_count,
                target_prune_sec=target_prune_sec if prune_enabled else 0.0,
                hook_target_sec=hook_target_sec,
                original_title=original_title,
                part_prefix=part_prefix
            )

        _log(f"✅ [AI CHIA PART] Phân tích hoàn tất:")
        _log(f"   🏷️ Master Title: {parsed_result['master_title']}")
        _log(f"   ✂️ Số đoạn tinh lược: {len(parsed_result['prune_plan'])} đoạn")
        _log(f"   📍 Mốc chia {part_count} Parts: {[round(p, 1) for p in parsed_result['part_splits']]}")
        _log(f"   🎣 Hook Toàn Cảnh: {parsed_result['hooks']['global_hook']['start']:.1f}s -> {parsed_result['hooks']['global_hook']['end']:.1f}s")
        return parsed_result

    @classmethod
    def _call_gemini_api(cls, system_prompt: str, user_prompt: str, api_key: str, model_name: str) -> str:
        """Gọi Gemini REST API với danh sách model ưu tiên."""
        import requests
        models = [model_name]
        for fallback in ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-1.5-pro"]:
            if fallback not in models:
                models.append(fallback)

        last_error = None
        for m in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent?key={api_key}"
            payload = {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "responseMimeType": "application/json",
                    "temperature": 0.2
                }
            }
            try:
                resp = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=180)
                if resp.status_code == 200:
                    data = resp.json()
                    candidates = data.get("candidates", [])
                    if candidates:
                        parts = candidates[0].get("content", {}).get("parts", [])
                        if parts:
                            return parts[0].get("text", "")
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:300]}"
            except Exception as ex:
                last_error = str(ex)

        raise RuntimeError(f"Tất cả Gemini models đều thất bại. Lỗi cuối: {last_error}")

    @classmethod
    def _validate_and_sanitize(
        cls,
        data: Dict[str, Any],
        total_duration: float,
        part_count: int,
        min_part_dur: float,
        original_title: str
    ) -> Dict[str, Any]:
        """Chuẩn hoá và kiểm tra tính hợp lệ toán học của kết quả AI."""
        master_title = str(data.get("master_title", "")).strip() or (original_title or "Full Video")
        
        # Part titles
        part_titles = data.get("part_titles", [])
        if not isinstance(part_titles, list):
            part_titles = []
        while len(part_titles) < part_count:
            part_titles.append(f"Chapter {len(part_titles) + 1}")
        part_titles = [str(t).strip() for t in part_titles[:part_count]]

        # Prune plan
        raw_prune = data.get("prune_plan", [])
        valid_prune = []
        if isinstance(raw_prune, list):
            for item in raw_prune:
                try:
                    s = max(0.0, float(item.get("start", 0.0)))
                    e = min(total_duration, float(item.get("end", 0.0)))
                    if e > s + 1.0 and (e - s) < total_duration * 0.8:
                        valid_prune.append({
                            "start": round(s, 2),
                            "end": round(e, 2),
                            "reason": str(item.get("reason", "Filler segment"))
                        })
                except Exception:
                    pass

        # Sort and merge overlapping prune ranges
        valid_prune.sort(key=lambda x: x["start"])
        merged_prune = []
        for p in valid_prune:
            if not merged_prune:
                merged_prune.append(p)
            else:
                last = merged_prune[-1]
                if p["start"] <= last["end"]:
                    last["end"] = max(last["end"], p["end"])
                else:
                    merged_prune.append(p)

        # Part splits
        raw_splits = data.get("part_splits", [])
        clean_splits = []
        if isinstance(raw_splits, list):
            for sp in raw_splits:
                try:
                    val = float(sp)
                    if 0.0 < val < total_duration:
                        clean_splits.append(val)
                except Exception:
                    pass
        clean_splits = sorted(list(set(clean_splits)))

        # Nếu số điểm cắt không khớp part_count - 1 hoặc khoảng cách quá ngắn, tái phân phối
        needed_splits = part_count - 1
        if len(clean_splits) != needed_splits:
            step = total_duration / float(part_count)
            clean_splits = [round(step * i, 2) for i in range(1, part_count)]

        # Hooks
        hooks = data.get("hooks", {})
        global_hook = hooks.get("global_hook", {})
        g_s = float(global_hook.get("start", 0.0))
        g_e = float(global_hook.get("end", min(total_duration, g_s + 6.0)))
        if g_e <= g_s or g_e > total_duration:
            g_s = max(0.0, total_duration * 0.3)
            g_e = min(total_duration, g_s + 6.0)

        clean_global_hook = {
            "start": round(g_s, 2),
            "end": round(g_e, 2),
            "reason": str(global_hook.get("reason", "Climax scene"))
        }

        # Individual hooks
        ind_hooks = []
        raw_ind = hooks.get("individual_hooks", [])
        if isinstance(raw_ind, list):
            for h in raw_ind:
                try:
                    s = max(0.0, float(h.get("start", 0.0)))
                    e = min(total_duration, float(h.get("end", s + 6.0)))
                    ind_hooks.append({
                        "part_index": int(h.get("part_index", len(ind_hooks) + 1)),
                        "start": round(s, 2),
                        "end": round(e, 2),
                        "reason": str(h.get("reason", "Part climax"))
                    })
                except Exception:
                    pass

        # Đảm bảo đủ individual hook cho mỗi part
        all_splits = [0.0] + clean_splits + [total_duration]
        while len(ind_hooks) < part_count:
            idx = len(ind_hooks)
            p_start = all_splits[idx]
            p_end = all_splits[idx + 1]
            h_s = max(p_start, p_end - 8.0)
            h_e = min(p_end, h_s + 6.0)
            ind_hooks.append({
                "part_index": idx + 1,
                "start": round(h_s, 2),
                "end": round(h_e, 2),
                "reason": f"Part {idx + 1} Cliffhanger"
            })

        return {
            "master_title": master_title,
            "part_titles": part_titles,
            "prune_plan": merged_prune,
            "part_splits": [round(s, 2) for s in clean_splits],
            "hooks": {
                "global_hook": clean_global_hook,
                "individual_hooks": ind_hooks[:part_count]
            }
        }

    @classmethod
    def _fallback_heuristic_plan(
        cls,
        total_duration: float,
        cues: List[Dict[str, Any]],
        part_count: int,
        target_prune_sec: float,
        hook_target_sec: float,
        original_title: str,
        part_prefix: str = "Part"
    ) -> Dict[str, Any]:
        """Thuật toán phân tích local khi không có mạng AI:
        - Tìm khoảng lặng hoặc chuyển câu để đặt mốc chia.
        - Lược bớt phần intro đầu video (nếu target_prune_sec > 0).
        - Chọn hook ở 1/3 cuối mỗi Part để tạo cliffhanger.
        """
        master_title = original_title or "Full Video"
        part_titles = [f"{part_prefix} {i}" for i in range(1, part_count + 1)]

        # 1. Đoạn tinh lược tự động (Intro nếu có)
        prune_plan = []
        if target_prune_sec > 1.0 and total_duration > target_prune_sec * 2:
            # Lược bỏ đoạn giới thiệu/bumper đầu (bắt đầu từ 0.0s đến hết đoạn intro)
            prune_plan.append({
                "start": 0.0,
                "end": round(min(total_duration * 0.25, target_prune_sec), 2),
                "reason": "Intro & opening sponsor cut"
            })

        # 2. Mốc chia Parts
        ideal_step = total_duration / float(part_count)
        part_splits = []
        for i in range(1, part_count):
            target_t = ideal_step * i
            # Tìm cue kết thúc gần target_t nhất trong khoảng +-15s
            best_t = target_t
            min_dist = float("inf")
            for c in cues:
                dist = abs(c["end"] - target_t)
                if dist < min_dist and dist <= 15.0:
                    min_dist = dist
                    best_t = c["end"]
            part_splits.append(round(best_t, 2))

        # 3. Hooks
        g_s = max(0.0, total_duration * 0.45)
        g_e = min(total_duration, g_s + hook_target_sec)
        global_hook = {
            "start": round(g_s, 2),
            "end": round(g_e, 2),
            "reason": "Dramatic peak moment"
        }

        all_splits = [0.0] + part_splits + [total_duration]
        ind_hooks = []
        for idx in range(part_count):
            p_start = all_splits[idx]
            p_end = all_splits[idx + 1]
            p_dur = p_end - p_start
            h_s = max(p_start, p_end - min(p_dur * 0.2, hook_target_sec + 2.0))
            h_e = min(p_end, h_s + hook_target_sec)
            ind_hooks.append({
                "part_index": idx + 1,
                "start": round(h_s, 2),
                "end": round(h_e, 2),
                "reason": f"Part {idx + 1} Climax teaser"
            })

        return {
            "master_title": master_title,
            "part_titles": part_titles,
            "prune_plan": prune_plan,
            "part_splits": part_splits,
            "hooks": {
                "global_hook": global_hook,
                "individual_hooks": ind_hooks
            }
        }
