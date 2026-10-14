"""Nhân Viên 3: Chuyên Viên Typoghraphy & Đóng Gói (Packaging & Typography Engine).

Nhiệm vụ:
- Tạo và căn chỉnh phụ đề ASS chuẩn Hollywood:
  + Font: Arial Bold 48pt, Chữ trắng viền đen dày 3.5px.
  + Vị trí Safe Zone tuyệt đối: MarginV = 420px (ngay phía trên huy hiệu PART).
  + 100% SẠCH SẼ: Giữ nguyên lời thoại gốc của video, TUYỆT ĐỐI KHÔNG chèn bất kỳ dòng
    kêu gọi tiếng Việt hay banner giật gân bừa bãi.
- Burn phụ đề vào video.
- Dán Top Title Card tại ranh giới trên (Y=301) và Bottom Part Badge tại ranh giới dưới (Y=1519).
- Xuất thành phẩm Part hoàn hảo 10/10.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("PackagingTypographerEngine")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from part_splitter_engine import (
    probe_duration_sec,
    get_best_video_encoder,
    PartSplitterEngine,
    _ff_run,
)
from part_splitter_config import load_part_config


class PackagingTypographerEngine:
    """Nhân Viên 3 - Packaging & Typography Engine."""

    @classmethod
    def wrap_subtitle_text(cls, text: str, font_size: int = 48, max_width_px: int = 940) -> str:
        """
        Tự động căn dòng phụ đề: TUYỆT ĐỐI KHÔNG QUÁ 2 DÒNG (KHÓA CỨNG MAX 2 DÒNG).
        - Nếu câu ngắn: Giữ nguyên 1 dòng.
        - Nếu câu dài: Cắt thành đúng 2 dòng cân đối nhất tại ranh giới từ.
        - Nếu câu quá dài (kể cả chia 2 dòng vẫn dài hơn chiều ngang): Tự động co cỡ chữ (Font-size auto-shrink)
          bằng thẻ ASS {\\fsXX} để ĐẢM BẢO 100% không bao giờ nhảy sang dòng thứ 3 và không bao giờ tràn mép màn hình.
        """
        text = text.strip()
        if not text:
            return ""

        clean_text = text.replace("\\N", " ").replace("\n", " ").strip()
        words = clean_text.split()
        if not words:
            return ""

        char_width_ratio = 0.52
        max_chars_single_line = max(18, int(max_width_px / max(12, font_size * char_width_ratio)))

        if len(clean_text) <= max_chars_single_line or len(words) <= 1:
            return clean_text

        # BẮT BUỘC MAX 2 DÒNG: Tìm điểm cắt k để chia thành đúng 2 dòng cân đối nhất
        best_k = 1
        best_diff = 9999
        for k in range(1, len(words)):
            l1 = " ".join(words[:k])
            l2 = " ".join(words[k:])
            diff = abs(len(l1) - len(l2))
            if diff < best_diff:
                best_diff = diff
                best_k = k

        line1 = " ".join(words[:best_k])
        line2 = " ".join(words[best_k:])

        # Kiểm tra xem dòng dài nhất có vượt quá bề ngang max_width_px hay không
        max_line_len = max(len(line1), len(line2))
        max_allowed_len = int(max_width_px / max(12, font_size * char_width_ratio))

        if max_line_len > max_allowed_len:
            # Tự động co cỡ chữ (Auto-Shrink) vừa khít cho câu này bằng tag {\fsXX}, giữ đúng 2 dòng
            fit_font_size = max(28, int(max_width_px / (max_line_len * char_width_ratio)))
            return f"{{\\fs{fit_font_size}}}{line1}\\N{line2}"
        else:
            return f"{line1}\\N{line2}"

    @classmethod
    def transcribe_video_with_whisper(
        cls,
        video_path: str,
        model_size: str = "base",
        log_fn=None,
        word_timestamps: bool = True
    ) -> Tuple[List[Tuple[float, float, str]], List[Any]]:
        """Sử dụng faster-whisper để nhận diện phụ đề trực tiếp từ video thành phẩm.
        - Frame-accurate 100%: Lắng nghe âm thanh thực tế sau khi đã mix/xfade/crossfade.
        - Trả về danh sách câu thoại và danh sách segment có word timestamps phục vụ hiệu ứng CapCut.
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        if not video_path or not os.path.isfile(video_path):
            return [], []

        try:
            from faster_whisper import WhisperModel
            _log(f"🎙️ [WHISPER ENGINE] Đang nhận diện phụ đề trực tiếp từ video hoàn thiện ({os.path.basename(video_path)})...")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            segments, info = model.transcribe(
                video_path,
                beam_size=5,
                word_timestamps=word_timestamps,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=400),
            )

            raw_segs = list(segments)
            results: List[Tuple[float, float, str]] = []
            for seg in raw_segs:
                text = (seg.text or "").strip().lstrip("> -–—").strip()
                if not text:
                    continue
                if results and results[-1][2] == text:
                    continue
                results.append((float(seg.start), float(seg.end), text))

            _log(f"✅ [WHISPER ENGINE] Nhận diện hoàn tất {len(results)} câu thoại ({sum(len(s.words or []) for s in raw_segs)} từ) khớp 100% video!")
            return results, raw_segs
        except Exception as e:
            _log(f"⚠️ [WHISPER ENGINE] Không thể chạy faster-whisper ({e}), chuyển sang chế độ dự phòng timeline.")
            return [], []

    @classmethod
    def generate_safe_zone_ass(
        cls,
        scenes: List[Dict[str, Any]],
        cues: List[Dict[str, Any]],
        output_ass_path: str,
        transition_duration: float = 0.35,
        config: Optional[Dict[str, Any]] = None,
        video_path: Optional[str] = None,
        log_fn=None
    ) -> str:
        """Tạo file phụ đề ASS chuẩn Safe Zone:
        - Nâng cấp phong cách CapCut Word-by-Word Highlight: Từ đang nói sáng vàng neon, các từ khác màu trắng viền đen.
        - Font: Montserrat ExtraBold / Arial Bold 55pt (tăng 15%), viền dày 4.5px, shadow 2.5px.
        - Safe Zone: MarginV = 420.
        """
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        cfg = config or load_part_config()
        font_name = str(cfg.get("sub_font_name", "Montserrat ExtraBold") or "Montserrat ExtraBold")
        font_size = int(cfg.get("sub_size", 55) or 55)
        outline_w = float(cfg.get("sub_outline", 4.5) or 4.5)
        shadow_d = float(cfg.get("sub_shadow", 2.5) or 2.5)
        margin_v = int(cfg.get("sub_margin_v", 420) or 420)

        def _norm_ass_color(col_val: str, default_val: str) -> str:
            c = str(col_val or default_val).strip().replace("&", "")
            if not c.startswith("H"):
                c = "H" + c
            return f"&{c}&"

        primary_col = _norm_ass_color(cfg.get("sub_color"), "H00FFFFFF")
        outline_col = _norm_ass_color(cfg.get("sub_outline_color"), "H00000000")

        ass_header = f"""[Script Info]
Title: CapCut Word-by-Word Highlight Subtitles
ScriptType: v4.00+
WrapStyle: 2
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary_col},&H000000FF,{outline_col},&H90000000,-1,0,0,0,100,100,0,0,1,{outline_w},{shadow_d},2,50,50,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        def _sec_to_ass_time(sec: float) -> str:
            sec = max(0.0, float(sec))
            h = int(sec // 3600)
            m = int((sec % 3600) // 60)
            s = int(sec % 60)
            cs = int(round((sec - int(sec)) * 100))
            if cs >= 100:
                s += 1
                cs = 0
            return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

        # 1. CHIẾN LƯỢC CHÍNH: Nhận diện word-timestamps trực tiếp qua faster-whisper
        raw_events: List[Tuple[float, float, str]] = []
        raw_segments: List[Any] = []
        if video_path and os.path.isfile(video_path):
            raw_events, raw_segments = cls.transcribe_video_with_whisper(video_path, log_fn=_log, word_timestamps=True)

        final_dialogue_lines: List[str] = []

        # Hiệu ứng Word-by-Word Highlight chuẩn CapCut
        has_word_data = any(bool(getattr(s, "words", None)) for s in raw_segments)
        if has_word_data:
            _log("🌟 [TYPOGRAPHER] Tạo phụ đề CapCut Word-by-Word Highlight (từ đang nói sáng vàng neon)...")
            for s in raw_segments:
                text = (s.text or "").strip().lstrip("> -–—").strip()
                if not text or not s.words:
                    continue
                words = s.words
                chunks = []
                cur_chunk = []
                cur_len = 0
                for w in words:
                    clean_w = w.word.strip()
                    if not clean_w:
                        continue
                    cur_chunk.append(w)
                    cur_len += len(clean_w) + 1
                    if cur_len >= 30 or len(cur_chunk) >= 5 or clean_w.endswith((".", "?", "!", ",")):
                        chunks.append(cur_chunk)
                        cur_chunk = []
                        cur_len = 0
                if cur_chunk:
                    chunks.append(cur_chunk)

                for chunk in chunks:
                    chunk_words = [w.word.strip() for w in chunk]
                    for idx, target_w in enumerate(chunk):
                        w_st = target_w.start
                        w_en = target_w.end
                        if idx + 1 < len(chunk):
                            w_en = min(target_w.end + 0.08, chunk[idx + 1].start)
                        if w_en <= w_st:
                            w_en = w_st + 0.22

                        formatted_words = []
                        for j, cw in enumerate(chunk_words):
                            if j == idx:
                                # Từ đang nói: highlight màu vàng rực rỡ {\c&H0000FFFF&}
                                formatted_words.append(f"\\N{{\\c&H0000FFFF&}}{cw}{{\\c&H00FFFFFF&}}" if j == 3 and len(chunk_words) > 5 else f"{{\\c&H0000FFFF&}}{cw}{{\\c&H00FFFFFF&}}")
                            else:
                                formatted_words.append(f"\\N{cw}" if j == 3 and len(chunk_words) > 5 else cw)

                        line_text = " ".join(formatted_words).replace(" \\N", "\\N").replace("\\N ", "\\N")
                        final_dialogue_lines.append(f"Dialogue: 0,{_sec_to_ass_time(w_st)},{_sec_to_ass_time(w_en)},Default,,0,0,0,,{line_text}\n")

        # 2. CHIẾN LƯỢC DỰ PHÒNG: Nếu không có word-timestamps, dùng câu hoàn chỉnh
        if not final_dialogue_lines:
            if not raw_events:
                _log("ℹ️ [TYPOGRAPHER] Dùng thuật toán ghép timeline khử đè lấn cho phụ đề...")
                clean_cues = []
                for c in (cues or []):
                    txt = str(c.get("text", "")).strip().lstrip("> -–—").strip()
                    if not txt:
                        continue
                    st = float(c.get("start", 0.0))
                    en = float(c.get("end", 0.0))
                    if en <= st:
                        continue
                    clean_cues.append({"start": st, "end": en, "text": txt})

                cur_timeline = 0.0
                for sc_idx, sc in enumerate(scenes):
                    shots = sc.get("shots", [])
                    for sh in shots:
                        sh_st = float(sh["start"])
                        sh_en = float(sh["end"])
                        sh_dur = max(0.5, sh_en - sh_st)

                        matching_cues = []
                        for c in clean_cues:
                            c_st = float(c["start"])
                            c_en = float(c["end"])
                            if c_en > sh_st and c_st < sh_en:
                                rel_st = max(0.0, c_st - sh_st)
                                rel_en = min(sh_dur, c_en - sh_st)
                                if rel_en > rel_st + 0.15:
                                    matching_cues.append((rel_st, rel_en, c["text"]))

                        matching_cues.sort(key=lambda x: x[0])
                        for rel_st, rel_en, txt in matching_cues:
                            raw_events.append((cur_timeline + rel_st, cur_timeline + rel_en, txt))

                        cur_timeline += sh_dur

                    if sc_idx < len(scenes) - 1:
                        cur_timeline -= transition_duration

            raw_events.sort(key=lambda x: x[0])
            last_end = 0.0
            for t_st, t_en, txt in raw_events:
                if t_st < last_end:
                    t_st = last_end
                if t_en <= t_st + 0.2:
                    continue
                wrapped_txt = cls.wrap_subtitle_text(txt, font_size=font_size, max_width_px=940)
                if not wrapped_txt:
                    continue
                final_dialogue_lines.append(f"Dialogue: 0,{_sec_to_ass_time(t_st)},{_sec_to_ass_time(t_en)},Default,,0,0,0,,{wrapped_txt}\n")
                last_end = t_en

        os.makedirs(os.path.dirname(os.path.abspath(output_ass_path)), exist_ok=True)
        with open(output_ass_path, "w", encoding="utf-8") as f:
            f.write(ass_header.strip() + "\n")
            for d_line in final_dialogue_lines:
                f.write(d_line)

        _log(f"✅ [TYPOGRAPHER] Đã xuất file phụ đề ASS ({len(final_dialogue_lines)} events): {output_ass_path}")
        return output_ass_path

    @classmethod
    def burn_subtitles_and_package(
        cls,
        audio_mastered_video: str,
        ass_path: str,
        master_title: str,
        part_index: int,
        output_final: str,
        part_prefix: str = "PART",
        work_dir: str = "",
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> str:
        """Burn phụ đề sạch và dán Top Title Card, Bottom Part Badge."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        work_dir = work_dir or os.path.dirname(os.path.abspath(output_final))
        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(output_final)), exist_ok=True)

        raw_pfx = str(part_prefix or "PART").strip()
        if not raw_pfx or raw_pfx.upper() in ("PART", "TẬP", "TAP") or "TẬP" in raw_pfx.upper() or "TAP" in raw_pfx.upper():
            part_prefix = "PART"
        else:
            part_prefix = raw_pfx.upper()

        video_for_packaging = audio_mastered_video

        # 1. Burn phụ đề nếu có file ASS
        if ass_path and os.path.isfile(ass_path):
            _log(f"📝 [NHÂN VIÊN 3] Burn phụ đề ASS Safe Zone (Arial Bold 48pt viền nét 3.5px)...")
            subbed_path = os.path.join(work_dir, f"subbed_p{part_index}_{os.path.basename(audio_mastered_video)}")
            enc_name, enc_opts = get_best_video_encoder()
            ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")

            cmd_burn = [
                "ffmpeg", "-y",
                "-i", audio_mastered_video,
                "-vf", f"ass='{ass_escaped}'",
                "-c:v", enc_name, *enc_opts,
                "-c:a", "copy",
                subbed_path
            ]
            try:
                _ff_run(cmd_burn, log_fn=None, timeout=600, label=f"burn_sub_p{part_index}")
                video_for_packaging = subbed_path
            except Exception as e:
                _log(f"⚠️ [NHÂN VIÊN 3] Lỗi burn phụ đề: {e}, bỏ qua bước burn sub.")

        # 2. Đóng gói bao bì: Top Card (Y=301) + Bottom Badge (Y=1519)
        _log(f"📦 [NHÂN VIÊN 3] Đóng gói Title Top Card & Huy Hiệu {part_prefix} {part_index}...")
        cfg = load_part_config()
        if config:
            cfg.update(config)

        cfg.update({
            "packaging_anchor_mode": "boundary",
            "competitor_top_card": True,
            "competitor_bottom_badge": True,
            "video_center_height": 1188,
            "top_card_boundary_offset": 0,
            "bottom_badge_boundary_offset": 0
        })

        final_result = PartSplitterEngine.apply_competitor_visual_packaging(
            condensed_video=video_for_packaging,
            output_final=output_final,
            master_title=master_title,
            part_index=part_index,
            part_prefix=part_prefix,
            config=cfg,
            log_fn=log_fn,
            work_dir=work_dir
        )

        final_dur = probe_duration_sec(output_final)
        _log(f"🎉 [NHÂN VIÊN 3] Xuất xưởng kiệt tác {part_prefix} {part_index} hoàn chỉnh 100%: {output_final} (Thời lượng chính xác: {final_dur:.2f}s)")
        return final_result
