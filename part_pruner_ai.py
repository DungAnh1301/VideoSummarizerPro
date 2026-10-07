"""Module Gemini AI phân tích toàn diện kịch bản cho Chế độ Chia Part.

Hỗ trợ:
1. Chế độ Biên tập Mới: ĐẠO DIỄN AI 3-PASS (Tổng Đạo Diễn Thích Ứng Mở -> Trưởng Phòng Dựng -> Kiểm Duyệt QC).
2. Chế độ Cắt Khúc Cơ Bản: Classic Linear (Tương thích ngược).
3. Kết nối qua Google Antigravity CLI (agy.exe) ưu tiên gemini-3.8-flash-high hoặc Google Gemini REST API.
4. Fallback Heuristic tự động đảm bảo không bao giờ bị dừng.
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
    """Bộ phân tích kịch bản thông minh bằng Gemini AI với kiến trúc 3-Pass."""

    @classmethod
    def analyze_and_plan(
        cls,
        video_path: str = "",
        srt_path: str = "",
        total_duration: float = 0.0,
        part_count: int = None,
        target_prune_sec: float = None,
        hook_target_sec: float = None,
        gemini_client = None,
        model_name: str = None,
        api_key: str = None,
        auto_title: bool = None,
        part_prefix: str = None,
        config: Dict[str, Any] = None,
        job_dir: str = "",
        original_title: str = "",
        log_fn = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Điểm vào linh hoạt cho mọi kiểu gọi phân tích AI."""
        cfg = dict(config or {})
        if part_count is not None:
            cfg["part_count"] = part_count
        if hook_target_sec is not None:
            cfg["hook_target_sec"] = hook_target_sec
        if model_name is not None:
            cfg["gemini_model"] = model_name
        if api_key is not None:
            cfg["gemini_api_key"] = api_key
            if str(api_key).strip():
                cfg["gemini_auth_mode"] = "api_key"
        if auto_title is not None:
            cfg["auto_regenerate_title"] = auto_title
        if part_prefix is not None:
            cfg["part_label_prefix"] = part_prefix
        if target_prune_sec is not None:
            cfg["prune_enabled"] = target_prune_sec > 0
            if total_duration > 0:
                cfg["prune_percent"] = (target_prune_sec / total_duration) * 100.0

        target_dir = job_dir or (os.path.dirname(video_path) if video_path else "")
        v_title = original_title
        if not v_title and video_path:
            v_title = os.path.splitext(os.path.basename(video_path))[0]

        return cls.analyze_video_structure(
            total_duration=total_duration,
            srt_path=srt_path,
            config=cfg,
            job_dir=target_dir,
            original_title=v_title,
            log_fn=log_fn
        )

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
        """Phân tích cấu trúc video: tự động rẽ nhánh sang 3-Pass AI hoặc Classic Linear."""
        config = config or {}
        editing_mode = str(config.get("editing_mode", "viral_condensed")).strip()

        if editing_mode == "viral_condensed":
            return cls.run_3pass_director(
                total_duration=total_duration,
                srt_path=srt_path,
                config=config,
                job_dir=job_dir,
                original_title=original_title,
                log_fn=log_fn
            )
        else:
            return cls._analyze_classic_linear(
                total_duration=total_duration,
                srt_path=srt_path,
                config=config,
                job_dir=job_dir,
                original_title=original_title,
                log_fn=log_fn
            )

    # =========================================================================
    # BỘ NÃO 3-PASS AI ĐẠO DIỄN THÍCH ỨNG MỞ (VIRAL CONDENSED)
    # =========================================================================

    @classmethod
    def run_3pass_director(
        cls,
        total_duration: float,
        srt_path: str = "",
        config: Dict[str, Any] = None,
        job_dir: str = "",
        original_title: str = "",
        log_fn = None
    ) -> Dict[str, Any]:
        """Chu trình 3 lượt (3 Passes):
        Pass 1: Tổng Đạo Diễn Thích Ứng Mở (Đọc vị thể loại, lập Director's Brief & chia N chặng)
        Pass 2: Trưởng Phòng Dựng (Chọn Hook 2-3s, tỉa micro-cuts 2-8s, gán punch-in 115%)
        Pass 3: Kiểm Duyệt QC & Lời Thoại (Rà soát mượt âm, chống nuốt từ, khóa cutlist)
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        part_count = max(2, int(config.get("part_count", 6)))
        target_dur = float(config.get("target_part_duration", 90.0))
        min_dur = float(config.get("min_part_duration", 70.0))
        max_dur = float(config.get("max_part_duration", 110.0))
        cues = _parse_srt_cues(srt_path)

        _log(f"🎬 [3-PASS AI DIRECTOR] Khởi động chu trình 3 đợt AI cho video {total_duration:.1f}s ({part_count} Parts, target {target_dur:.0f}s/part)...")

        # -------------------------------------------------------------
        # PASS 1: TỔNG ĐẠO DIỄN THÍCH ỨNG MỞ (Adaptive Showrunner)
        # -------------------------------------------------------------
        _log(f"🧠 [Pass 1/3] Tổng Đạo Diễn: Đọc vị thể loại & chia {part_count} chặng cốt truyện...")
        pass1_res = cls._pass1_adaptive_showrunner(
            total_duration=total_duration,
            cues=cues,
            part_count=part_count,
            config=config,
            job_dir=job_dir,
            original_title=original_title,
            log_fn=_log
        )

        master_title = pass1_res.get("master_title", original_title or "Full Video")
        director_brief = pass1_res.get("director_brief", "High retention storytelling")
        story_arcs = pass1_res.get("story_arcs", [])

        # Kiểm tra tính hợp lệ của story_arcs
        if len(story_arcs) != part_count:
            step = total_duration / float(part_count)
            story_arcs = [
                {
                    "part_index": i + 1,
                    "start": round(i * step, 2),
                    "end": round((i + 1) * step if i + 1 < part_count else total_duration, 2),
                    "arc_theme": f"Chapter {i + 1}"
                }
                for i in range(part_count)
            ]

        _log(f"✅ [Pass 1/3] Tổng Đạo Diễn hoàn tất:")
        _log(f"   🏷️ Master Title: {master_title}")
        _log(f"   📜 Director's Brief: {director_brief[:120]}...")
        _log(f"   📍 {len(story_arcs)} chặng cốt truyện: {[(round(a['start'], 1), round(a['end'], 1)) for a in story_arcs]}")

        # -------------------------------------------------------------
        # PASS 2 & 3: TRƯỞNG PHÒNG DỰNG & KIỂM DUYỆT QC CHO TỪNG PART
        # -------------------------------------------------------------
        final_parts = []
        for arc in story_arcs:
            p_idx = int(arc.get("part_index", len(final_parts) + 1))
            a_start = float(arc.get("start", 0.0))
            a_end = float(arc.get("end", total_duration))
            a_theme = str(arc.get("arc_theme", f"Part {p_idx}"))

            # Lọc cues thuộc về chặng này
            arc_cues = [c for c in cues if c["start"] >= max(0.0, a_start - 1.0) and c["end"] <= min(total_duration, a_end + 1.0)]

            # PASS 2: Trưởng phòng dựng
            _log(f"✂️ [Pass 2/3] Trưởng Phòng Dựng: Lọc Hook, Thoại, góc máy Part {p_idx}/{part_count} ({a_start:.1f}s -> {a_end:.1f}s)...")
            pass2_part = cls._pass2_pacing_editor(
                part_index=p_idx,
                part_count=part_count,
                arc_start=a_start,
                arc_end=a_end,
                arc_theme=a_theme,
                director_brief=director_brief,
                cues=arc_cues,
                target_part_dur=target_dur,
                min_part_dur=min_dur,
                max_part_dur=max_dur,
                config=config,
                job_dir=job_dir,
                log_fn=_log
            )

            # PASS 3: Kiểm duyệt QC & Lời thoại
            _log(f"🔍 [Pass 3/3] Kiểm Duyệt QC: Rà soát nghĩa câu thoại, chống nuốt chữ Part {p_idx}/{part_count}...")
            pass3_part = cls._pass3_dialogue_qc_auditor(
                part_data=pass2_part,
                cues=arc_cues,
                target_part_dur=target_dur,
                min_part_dur=min_dur,
                max_part_dur=max_dur,
                config=config,
                job_dir=job_dir,
                log_fn=_log
            )

            final_parts.append(pass3_part)

        # Tổng hợp kết quả chuẩn hóa
        part_titles = [p.get("title", f"Part {p['part_index']}") for p in final_parts]
        part_splits = [p["arc_end"] for p in final_parts[:-1]] if len(final_parts) > 1 else []
        ind_hooks = [p.get("hook", {"start": 0.0, "end": 3.0, "reason": "Hook"}) for p in final_parts]
        global_hook = ind_hooks[0] if ind_hooks else {"start": 0.0, "end": 3.0, "reason": "Global hook"}

        result = {
            "editing_mode": "viral_condensed",
            "master_title": master_title,
            "director_brief": director_brief,
            "part_count": part_count,
            "parts": final_parts,
            # Tương thích ngược với các hàm cũ
            "part_titles": part_titles,
            "part_splits": part_splits,
            "prune_plan": [],
            "hooks": {
                "global_hook": global_hook,
                "individual_hooks": ind_hooks
            }
        }

        _log(f"🎉 [3-PASS AI DIRECTOR] Hoàn tất 3 Passes thành công rực rỡ ({len(final_parts)} Parts chuẩn bị render)!")
        return result

    # -------------------------------------------------------------------------
    # PASS 1 THỰC THI
    # -------------------------------------------------------------------------
    @classmethod
    def _pass1_adaptive_showrunner(
        cls,
        total_duration: float,
        cues: List[Dict[str, Any]],
        part_count: int,
        config: Dict[str, Any],
        job_dir: str,
        original_title: str,
        log_fn = None
    ) -> Dict[str, Any]:
        """Pass 1: Đọc vị 3 trục bản chất (Visual vs Verbal, Energy Curve, Native Story Arc)."""
        # Lấy mẫu cues đại diện
        sample_lines = []
        if cues:
            step = max(1, len(cues) // 250)
            sampled = cues[::step]
            sample_lines = [f"[{c['start']:.1f}s - {c['end']:.1f}s]: {c['text']}" for c in sampled]
        cues_text = "\n".join(sample_lines) if sample_lines else "(No transcript available. Plan by visual time intervals.)"

        system_prompt = (
            "You are an elite Showrunner & Story Director for viral short-form serialized video.\n"
            "Your mission: Perform Pass 1 Open-Ended Narrative Analysis on the provided long video "
            "to divide it into exactly N episodic Parts.\n"
            "Analyze the video across 3 CORE AXES:\n"
            "1. Visual vs Verbal Ratio: Is it heavy dialogue/interview, physical action/challenge, documentary B-roll, or vlog commentary?\n"
            "2. Emotional Energy Curve: Locate the build-ups, major climaxes, comedic beats, and shocking revelations.\n"
            "3. Native Story Arc: Divide the continuous narrative into N distinct story chapters or mission stages.\n"
            "MANDATORY REQUIREMENTS:\n"
            "- master_title: Output 100% the clean YouTube original title or crisp curiosity-driven title in the video's ORIGINAL LANGUAGE (no 'Part 1' prefix, no fluff).\n"
            "- director_brief: 2-3 sentences of concrete Art Direction tailored specifically to this video's genre and rhythm.\n"
            f"- story_arcs: Exactly {part_count} contiguous segments covering from 0.0 to {total_duration:.1f}s. "
            "Each arc must have 'part_index' (1..N), 'start', 'end', and 'arc_theme'.\n"
            "OUTPUT FORMAT: Strict valid JSON only, no markdown or surrounding commentary."
        )

        user_prompt = f"""
VIDEO SPECIFICATIONS:
- Original Title: {original_title or "Untitled"}
- Total Duration: {total_duration:.1f}s ({total_duration/60.0:.2f} mins)
- Requested Part Count: {part_count}

TRANSCRIPT EXCERPT:
{cues_text}

JSON OUTPUT SCHEMA:
{{
  "master_title": "Original video title in native language",
  "director_brief": "Art direction brief specifying editing rhythm, shot priorities, and dialogue style",
  "story_arcs": [
    {{"part_index": 1, "start": 0.0, "end": 350.0, "arc_theme": "The Initial Challenge"}},
    {{"part_index": 2, "start": 350.0, "end": 720.0, "arc_theme": "The Turning Point"}}
  ]
}}
Ensure 'story_arcs' has exactly {part_count} items and spans from 0.0 to {total_duration:.1f}.
"""
        raw = cls._invoke_ai(system_prompt, user_prompt, config, job_dir)
        if raw:
            try:
                data = json.loads(_clean_json_text(raw))
                if isinstance(data, dict) and data.get("story_arcs"):
                    arcs = data.get("story_arcs", [])
                    if len(arcs) == part_count:
                        return data
            except Exception as e:
                logger.warning(f"Pass 1 JSON parse failed: {e}")

        # Fallback Pass 1
        step = total_duration / float(part_count)
        fallback_arcs = []
        for i in range(part_count):
            fallback_arcs.append({
                "part_index": i + 1,
                "start": round(i * step, 2),
                "end": round((i + 1) * step if i + 1 < part_count else total_duration, 2),
                "arc_theme": f"Chặng {i + 1}: Diễn biến then chốt"
            })
        return {
            "master_title": original_title or "Full Video",
            "director_brief": "Nhịp cắt dồn dập, giữ chặt đối thoại then chốt và đẩy cao trào thị giác.",
            "story_arcs": fallback_arcs
        }

    # -------------------------------------------------------------------------
    # PASS 2 THỰC THI
    # -------------------------------------------------------------------------
    @classmethod
    def _pass2_pacing_editor(
        cls,
        part_index: int,
        part_count: int,
        arc_start: float,
        arc_end: float,
        arc_theme: str,
        director_brief: str,
        cues: List[Dict[str, Any]],
        target_part_dur: float,
        min_part_dur: float,
        max_part_dur: float,
        config: Dict[str, Any],
        job_dir: str,
        log_fn = None
    ) -> Dict[str, Any]:
        """Pass 2: Trưởng phòng dựng - Chọn Hook 2-3s, tỉa micro-cuts 2-8s, gán punch-in 115%."""
        cues_text = "\n".join([f"[{c['start']:.1f}s - {c['end']:.1f}s]: {c['text']}" for c in cues[:120]])
        if not cues_text:
            cues_text = f"(No dialogue transcript in this window from {arc_start:.1f}s to {arc_end:.1f}s.)"

        system_prompt = (
            "You are the Lead Pacing & Continuity Film Editor.\n"
            f"You are editing Part {part_index}/{part_count} based on the Director's Brief:\n"
            f"'{director_brief}'\n"
            f"Source range for this Part: {arc_start:.1f}s to {arc_end:.1f}s.\n"
            f"TARGET CONDENSED DURATION: {target_part_dur:.1f}s (Allowed range: {min_part_dur:.1f}s - {max_part_dur:.1f}s).\n\n"
            "MANDATORY EDITING RULES:\n"
            "1. HOOK (2.0s - 3.0s): Select the single most dramatic, high-stakes moment in this range to place at 00:00. "
            "Never select ads, sponsor logos, or boring intros. Must show peak action/climax.\n"
            "2. MICRO-CUTS CONDENSATION (Prune 75%-85% fluff): Keep ONLY high-value dialogue lines and visual action.\n"
            "   - Each cut must be between 2.0s and 8.0s.\n"
            "   - Never cut mid-word or mid-syllable; always snap cuts to natural sentence/breath pauses.\n"
            "   - Total sum of all cuts (excluding hook) should equal approximately 80s - 90s.\n"
            "3. PUNCH-IN ZOOM (115%): Set 'punch_in': true on alternating monologue shots or talking head shots "
            "to transition from medium shot to close-up, eliminating jump cuts.\n"
            "4. CLIFFHANGER: Ensure the last cut ends abruptly on a cliffhanger question or tense moment.\n"
            "OUTPUT FORMAT: Strict valid JSON only."
        )

        user_prompt = f"""
PART CONTEXT:
- Part Index: {part_index} of {part_count}
- Arc Theme: {arc_theme}
- Timeline Window: {arc_start:.1f}s -> {arc_end:.1f}s
- Target Condensed Total: {target_part_dur:.1f}s

TRANSCRIPT IN THIS WINDOW:
{cues_text}

JSON OUTPUT SCHEMA:
{{
  "part_index": {part_index},
  "title": "Short punchy title for this part in original language",
  "hook": {{
    "start": 320.5,
    "end": 323.5,
    "reason": "Most dramatic climax teaser"
  }},
  "cuts": [
    {{"start": {arc_start + 5.0:.1f}, "end": {arc_start + 10.5:.1f}, "punch_in": false, "summary": "Key revelation"}},
    {{"start": {arc_start + 25.0:.1f}, "end": {arc_start + 31.0:.1f}, "punch_in": true, "summary": "Reaction close-up"}}
  ],
  "cliffhanger": "Ending with an unsolved mystery before reveal"
}}
Ensure the cuts sum up to roughly {target_part_dur:.1f}s.
"""
        raw = cls._invoke_ai(system_prompt, user_prompt, config, job_dir)
        if raw:
            try:
                data = json.loads(_clean_json_text(raw))
                if isinstance(data, dict) and data.get("cuts"):
                    data["part_index"] = part_index
                    data["arc_start"] = arc_start
                    data["arc_end"] = arc_end
                    return cls._sanitize_part_cuts(data, arc_start, arc_end, target_part_dur, cues)
            except Exception as e:
                logger.warning(f"Pass 2 Part {part_index} parse error: {e}")

        # Fallback Pass 2 cho Part này
        return cls._fallback_pacing_editor(part_index, arc_start, arc_end, arc_theme, cues, target_part_dur)

    # -------------------------------------------------------------------------
    # PASS 3 THỰC THI
    # -------------------------------------------------------------------------
    @classmethod
    def _pass3_dialogue_qc_auditor(
        cls,
        part_data: Dict[str, Any],
        cues: List[Dict[str, Any]],
        target_part_dur: float,
        min_part_dur: float,
        max_part_dur: float,
        config: Dict[str, Any],
        job_dir: str,
        log_fn = None
    ) -> Dict[str, Any]:
        """Pass 3: Kiểm duyệt QC & Lời thoại - Đảm bảo các câu nối không bị nuốt âm, què ngữ pháp."""
        cuts = part_data.get("cuts", [])
        if not cuts:
            return part_data

        p_idx = part_data.get("part_index", 1)
        arc_start = float(part_data.get("arc_start", 0.0))
        arc_end = float(part_data.get("arc_end", arc_start + 300.0))

        # Soát lại danh sách cuts với transcript cues để triệt tiêu nuốt chữ
        fine_tuned_cuts = []
        for cut in cuts:
            c_s = float(cut.get("start", 0.0))
            c_e = float(cut.get("end", c_s + 4.0))
            p_in = bool(cut.get("punch_in", False))
            summary = str(cut.get("summary", ""))

            # Hít vào ranh giới cue nếu c_s hoặc c_e rơi vào giữa từ
            best_s = c_s
            best_e = c_e
            for cue in cues:
                # Nếu c_s nằm rất gần điểm bắt đầu của cue (+-0.5s), neo vào cue.start
                if abs(cue["start"] - c_s) <= 0.6:
                    best_s = cue["start"]
                # Nếu c_e nằm rất gần điểm kết thúc của cue (+-0.5s), neo vào cue.end
                if abs(cue["end"] - c_e) <= 0.6:
                    best_e = cue["end"]

            # Ràng buộc độ dài shot từ 2.0s đến 8.5s
            if best_e < best_s + 2.0:
                best_e = best_s + 2.0
            if (best_e - best_s) > 8.5:
                best_e = best_s + 8.5

            best_s = max(arc_start, best_s)
            best_e = min(arc_end, best_e)

            if best_e > best_s + 1.5:
                fine_tuned_cuts.append({
                    "start": round(best_s, 2),
                    "end": round(best_e, 2),
                    "punch_in": p_in,
                    "summary": summary
                })

        # Sắp xếp và triệt tiêu chồng chéo
        fine_tuned_cuts.sort(key=lambda x: x["start"])
        merged_cuts = []
        for c in fine_tuned_cuts:
            if not merged_cuts:
                merged_cuts.append(c)
            else:
                last = merged_cuts[-1]
                if c["start"] < last["end"]:
                    # Nếu chồng chéo, kéo dài last hoặc dời start
                    last["end"] = max(last["end"], c["end"])
                else:
                    merged_cuts.append(c)

        # Tính tổng thời lượng cuts
        total_cuts_dur = sum(c["end"] - c["start"] for c in merged_cuts)

        # Kiểm tra Hook
        hook = part_data.get("hook", {})
        h_s = float(hook.get("start", arc_start))
        h_e = float(hook.get("end", h_s + 2.5))
        if h_e <= h_s or (h_e - h_s) < 1.5 or (h_e - h_s) > 4.5:
            h_s = max(arc_start, arc_end - 15.0)
            h_e = min(arc_end, h_s + 2.8)

        clean_hook = {
            "start": round(h_s, 2),
            "end": round(h_e, 2),
            "reason": str(hook.get("reason", f"Part {p_idx} Hook Climax"))
        }

        part_data["hook"] = clean_hook
        part_data["cuts"] = merged_cuts
        part_data["target_duration"] = round(total_cuts_dur + (h_e - h_s), 1)

        return part_data

    # -------------------------------------------------------------------------
    # HÀM BỔ TRỢ & FALLBACK
    # -------------------------------------------------------------------------
    @classmethod
    def _invoke_ai(cls, system_prompt: str, user_prompt: str, config: Dict[str, Any], job_dir: str) -> str:
        """Gửi prompt tới Antigravity CLI (agy.exe) hoặc Gemini API."""
        auth_mode = str(config.get("gemini_auth_mode", "antigravity"))
        api_key = str(config.get("gemini_api_key", "")).strip()
        model_name = str(config.get("gemini_model", "gemini-3.8-flash-high")).strip()

        # 1. Thử Gemini API nếu người dùng chỉ định api_key
        if auth_mode == "api_key" and api_key:
            try:
                return cls._call_gemini_api(system_prompt, user_prompt, api_key, model_name)
            except Exception as e:
                logger.warning(f"Gemini API call failed: {e}")

        # 2. Thử Antigravity CLI (OAuth Google Local)
        try:
            from antigravity_processor import AntigravityProcessor
            return AntigravityProcessor.analyze_prompt(system_prompt, user_prompt, job_dir=job_dir)
        except Exception as e:
            logger.warning(f"Antigravity CLI call failed: {e}")

        # 3. Fallback Gemini API nếu có key
        if api_key:
            try:
                return cls._call_gemini_api(system_prompt, user_prompt, api_key, model_name)
            except Exception:
                pass
        return ""

    @classmethod
    def _sanitize_part_cuts(
        cls,
        data: Dict[str, Any],
        arc_start: float,
        arc_end: float,
        target_dur: float,
        cues: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Chuẩn hóa danh sách cuts của một Part."""
        raw_cuts = data.get("cuts", [])
        valid_cuts = []
        for item in raw_cuts:
            try:
                s = float(item.get("start", 0.0))
                e = float(item.get("end", 0.0))
                if s < arc_start:
                    s = arc_start
                if e > arc_end:
                    e = arc_end
                if e >= s + 1.8:
                    valid_cuts.append({
                        "start": round(s, 2),
                        "end": round(e, 2),
                        "punch_in": bool(item.get("punch_in", False)),
                        "summary": str(item.get("summary", ""))
                    })
            except Exception:
                pass

        if not valid_cuts:
            return cls._fallback_pacing_editor(
                data.get("part_index", 1), arc_start, arc_end, data.get("title", ""), cues, target_dur
            )

        data["cuts"] = valid_cuts
        return data

    @classmethod
    def _fallback_pacing_editor(
        cls,
        part_index: int,
        arc_start: float,
        arc_end: float,
        arc_theme: str,
        cues: List[Dict[str, Any]],
        target_dur: float
    ) -> Dict[str, Any]:
        """Thuật toán local tự tạo danh sách micro-cuts 2-8s vừa khít target_dur."""
        part_dur = arc_end - arc_start
        cuts = []
        accumulated = 0.0

        if cues and len(cues) >= 8:
            # Chọn các cues đối thoại hay nhất
            step = max(1, len(cues) // 18)
            punch_toggle = False
            for c in cues[::step]:
                if accumulated >= target_dur - 5.0:
                    break
                s = max(arc_start, float(c["start"]))
                e = min(arc_end, max(s + 2.5, float(c["end"])))
                dur = e - s
                if dur > 8.0:
                    e = s + 7.5
                    dur = 7.5
                if dur >= 2.0:
                    cuts.append({
                        "start": round(s, 2),
                        "end": round(e, 2),
                        "punch_in": punch_toggle,
                        "summary": c.get("text", "")[:40]
                    })
                    punch_toggle = not punch_toggle
                    accumulated += dur
        else:
            # Chia lát cắt cơ học theo thời gian
            shot_len = 4.0
            n_shots = int(target_dur / shot_len)
            time_step = part_dur / float(max(1, n_shots + 1))
            punch_toggle = False
            for i in range(n_shots):
                s = arc_start + time_step * (i + 0.5)
                e = min(arc_end, s + shot_len)
                cuts.append({
                    "start": round(s, 2),
                    "end": round(e, 2),
                    "punch_in": punch_toggle,
                    "summary": f"Scene beat {i+1}"
                })
                punch_toggle = not punch_toggle

        # Hook 2.5s lấy ở cuối chặng
        h_s = max(arc_start, arc_end - 8.0)
        h_e = min(arc_end, h_s + 2.8)

        return {
            "part_index": part_index,
            "title": arc_theme or f"Part {part_index}",
            "arc_start": arc_start,
            "arc_end": arc_end,
            "hook": {
                "start": round(h_s, 2),
                "end": round(h_e, 2),
                "reason": f"Part {part_index} Climax Teaser"
            },
            "cuts": cuts,
            "cliffhanger": f"End of Part {part_index}",
            "target_duration": target_dur
        }

    # =========================================================================
    # CHẾ ĐỘ CẮT KHÚC CƠ BẢN (CLASSIC LINEAR - KIỂU CŨ DỰ PHÒNG)
    # =========================================================================

    @classmethod
    def _analyze_classic_linear(
        cls,
        total_duration: float,
        srt_path: str = "",
        config: Dict[str, Any] = None,
        job_dir: str = "",
        original_title: str = "",
        log_fn = None
    ) -> Dict[str, Any]:
        """Quy trình phân tích cắt khúc truyền thống kiểu cũ."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        config = config or {}
        part_count = max(2, int(config.get("part_count", 6)))
        speed = max(1.0, float(config.get("speed", config.get("source_speed", 1.0))))
        min_final_dur = max(61.0, float(config.get("min_part_duration_sec", 60.0)))
        min_part_dur = min_final_dur * speed
        prune_enabled = bool(config.get("prune_enabled", True))
        prune_mode = str(config.get("prune_mode", "percent"))
        prune_percent = float(config.get("prune_percent", 20.0))
        prune_minutes = float(config.get("prune_minutes", 15.0))
        prune_tol = float(config.get("prune_tolerance", 0.20))
        hook_target_sec = float(config.get("hook_target_sec", 6.0))
        hook_tol = float(config.get("hook_tolerance", 0.50))
        part_prefix = str(config.get("part_label_prefix", "Part"))

        _log(f"🧠 [AI CHIA PART] Chế độ Classic Linear: {total_duration:.1f}s, {part_count} Parts...")

        cues = _parse_srt_cues(srt_path)
        transcript_sample = ""
        if cues:
            step = max(1, len(cues) // 300)
            sampled_cues = cues[::step]
            transcript_sample = "\n".join([
                f"[{c['start']:.1f}s - {c['end']:.1f}s]: {c['text']}"
                for c in sampled_cues
            ])

        target_prune_sec = 0.0
        prune_min_sec = 0.0
        prune_max_sec = 0.0
        min_clean_dur_needed = part_count * min_part_dur
        max_allowable_prune_sec = max(0.0, total_duration - min_clean_dur_needed)

        if prune_enabled and total_duration > min_part_dur:
            if prune_mode == "minutes":
                nominal_prune = min(total_duration * 0.70, prune_minutes * 60.0)
            else:
                nominal_prune = total_duration * (prune_percent / 100.0)

            if nominal_prune > max_allowable_prune_sec:
                target_prune_sec = max_allowable_prune_sec
            else:
                target_prune_sec = nominal_prune

            prune_min_sec = max(0.0, target_prune_sec * (1.0 - prune_tol))
            prune_max_sec = min(max_allowable_prune_sec, target_prune_sec * (1.0 + prune_tol))

        hook_min_sec = hook_target_sec * (1.0 - hook_tol)
        hook_max_sec = hook_target_sec * (1.0 + hook_tol)

        system_prompt = (
            "You are a viral video editor. Analyze a long video and split it into compelling episodic Parts "
            "with cliffhangers at part boundaries and hooks.\n"
            "OUTPUT FORMAT: Strict valid JSON only."
        )

        user_prompt = f"""
VIDEO SPECIFICATIONS:
- Original Title: {original_title or "Untitled"}
- Total Duration: {total_duration:.1f}s
- Part Count: {part_count}
- Target Prune Duration: {target_prune_sec:.1f}s
- Hook Target Duration: {hook_target_sec:.1f}s

TRANSCRIPT:
{transcript_sample if transcript_sample else "(No spoken transcript)"}

REQUIRED JSON:
{{
  "master_title": "Viral title in native language",
  "part_titles": ["Title 1", "Title 2"],
  "prune_plan": [{{"start": 0.0, "end": 20.0, "reason": "Intro"}}],
  "part_splits": [300.0, 600.0],
  "hooks": {{
    "global_hook": {{"start": 290.0, "end": 296.0, "reason": "Climax"}},
    "individual_hooks": [{{"part_index": 1, "start": 290.0, "end": 296.0, "reason": "Peak"}}]
  }}
}}
"""
        raw_response = cls._invoke_ai(system_prompt, user_prompt, config, job_dir)
        parsed_result = None
        if raw_response:
            try:
                data = json.loads(_clean_json_text(raw_response))
                if isinstance(data, dict):
                    parsed_result = cls._validate_and_sanitize(
                        data, total_duration, part_count, min_part_dur, original_title
                    )
            except Exception:
                pass

        if not parsed_result:
            parsed_result = cls._fallback_heuristic_plan(
                total_duration=total_duration,
                cues=cues,
                part_count=part_count,
                target_prune_sec=target_prune_sec if prune_enabled else 0.0,
                hook_target_sec=hook_target_sec,
                original_title=original_title,
                part_prefix=part_prefix
            )

        parsed_result["editing_mode"] = "classic_linear"
        return parsed_result

    @classmethod
    def _call_gemini_api(cls, system_prompt: str, user_prompt: str, api_key: str, model_name: str) -> str:
        """Gọi Gemini REST API với danh sách model ưu tiên."""
        import requests
        models = [model_name]
        for fallback in ["gemini-3.8-flash-high", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]:
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
        part_titles = data.get("part_titles", [])
        if not isinstance(part_titles, list):
            part_titles = []
        while len(part_titles) < part_count:
            part_titles.append(f"Chapter {len(part_titles) + 1}")
        part_titles = [str(t).strip() for t in part_titles[:part_count]]

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

        needed_splits = part_count - 1
        if len(clean_splits) != needed_splits:
            step = total_duration / float(part_count)
            clean_splits = [round(step * i, 2) for i in range(1, part_count)]

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
        """Thuật toán phân tích local khi không có mạng AI cho chế độ Classic."""
        master_title = original_title or "Full Video"
        part_titles = [f"{part_prefix} {i}" for i in range(1, part_count + 1)]

        prune_plan = []
        if target_prune_sec > 1.0 and total_duration > target_prune_sec * 2:
            prune_plan.append({
                "start": 0.0,
                "end": round(min(total_duration * 0.25, target_prune_sec), 2),
                "reason": "Intro & opening sponsor cut"
            })

        ideal_step = total_duration / float(part_count)
        part_splits = []
        for i in range(1, part_count):
            target_t = ideal_step * i
            best_t = target_t
            min_dist = float("inf")
            for c in cues:
                dist = abs(c["end"] - target_t)
                if dist < min_dist and dist <= 15.0:
                    min_dist = dist
                    best_t = c["end"]
            part_splits.append(round(best_t, 2))

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
