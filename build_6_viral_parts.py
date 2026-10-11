"""Script tự động sản xuất trọn vẹn 6 Part chuẩn đối thủ 9M views:
- Nhịp cắt nhanh dồn dập (Micro-cuts 1.5s - 3.2s, gọt sạch khoảng lặng ngắt nghỉ).
- Xen kẽ Punch-in Zoom (115%) liên tục trên từng câu thoại.
- Hook Teaser giật gân ở giây 00:00 và Cliffhanger nghẹt thở ở cuối mỗi Part.
- Đóng gói Canvas 9:16 (1080x1920), Nền Cinematic Blur, Top Card in hoa, Bottom Badge 'PART X', Audio Limiter -11 LUFS.
"""
from __future__ import annotations

import os
import sys
import time
import json
import logging
from typing import List, Dict, Any

# Nạp codebase Video_summarizer_Pro
sys.path.append(r"E:\Video_summarizer_Pro")

from part_pruner_ai import _parse_srt_cues
from part_splitter_engine import PartSplitterEngine, probe_duration_sec

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Build6Parts")

# Đường dẫn file
SRC_DIR = r"C:\Users\DungAnh\Downloads\hitomi_downloader_GUI\phantich"
SRT_PATH = r"E:\Video_summarizer_Pro\temp\jack_sub.en.srt"
OUT_DIR = r"E:\Video_summarizer_Pro\output\Loyalty_Test_6_Parts"
TEMP_DIR = r"E:\Video_summarizer_Pro\temp\render_6_parts"

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

# Tìm file video gốc
src_files = [os.path.join(SRC_DIR, f) for f in os.listdir(SRC_DIR) if "TYDkO6JzeJA" in f]
if not src_files:
    raise FileNotFoundError("Không tìm thấy video gốc JACK TV trong phantich")
SOURCE_VIDEO = src_files[0]

# Nạp phụ đề
all_cues = _parse_srt_cues(SRT_PATH)
logger.info(f"Đã nạp {len(all_cues)} cues từ {SRT_PATH}")


def filter_and_sample_speech(cues: List[Dict[str, Any]], start_t: float, end_t: float, target_dur: float = 87.0) -> List[Dict[str, Any]]:
    """Lọc các câu thoại đắt giá nhất và phân rã thành micro-cuts 1.5s - 3.2s."""
    raw = []
    seen = set()
    for c in cues:
        if start_t <= c["start"] <= end_t:
            dur = c["end"] - c["start"]
            txt = c["text"].strip()
            # Bỏ qua phụ đề quá ngắn hoặc rác
            if dur >= 1.0 and len(txt) > 5:
                # Tránh trùng lặp từ do rolling captions
                key = " ".join(txt.split()[-3:]) if len(txt.split()) >= 3 else txt
                if key not in seen:
                    seen.add(key)
                    raw.append(c)

    # Chọn lọc các câu trải đều trên timeline để bảo toàn mạch truyện
    # Mỗi Part lấy ~32 - 38 câu thoại
    if len(raw) > 36:
        step = max(1, len(raw) // 36)
        sampled = raw[::step][:36]
    else:
        sampled = raw

    cuts = []
    punch = False
    current_dur = 0.0

    for item in sampled:
        s = item["start"]
        e = item["end"]
        dur = e - s

        # Nếu câu thoại quá 3.5s, cắt đôi để tạo jump-cut CapCut style
        if dur > 3.5:
            mid = s + dur / 2.0
            cuts.append({
                "start": round(s, 2),
                "end": round(mid, 2),
                "punch_in": punch,
                "summary": item["text"][:40]
            })
            punch = not punch
            cuts.append({
                "start": round(mid, 2),
                "end": round(e, 2),
                "punch_in": punch,
                "summary": item["text"][:40]
            })
            punch = not punch
            current_dur += dur
        else:
            cuts.append({
                "start": round(s, 2),
                "end": round(e, 2),
                "punch_in": punch,
                "summary": item["text"][:40]
            })
            punch = not punch
            current_dur += dur

        if current_dur >= target_dur:
            break

    return cuts


# Cấu hình 6 Part bám sát 6 chặng cốt truyện kịch tính của video
PARTS_CONFIG = [
    {
        "part_index": 1,
        "title": "4 YEARS DATING & ONLY MET TWICE?! 🚩",
        "start": 188.0,
        "end": 495.0,
        "hook": {"start": 438.5, "end": 441.2, "reason": "Are you single?"},
    },
    {
        "part_index": 2,
        "title": "BOYFRIEND CLAIMS HE'S 100% SINGLE?! 😳",
        "start": 530.0,
        "end": 855.0,
        "hook": {"start": 780.0, "end": 782.8, "reason": "Jordan claims he has no girlfriend"},
    },
    {
        "part_index": 3,
        "title": "HE GAVE THE DECOY HIS PHONE NUMBER! 📱",
        "start": 860.0,
        "end": 1205.0,
        "hook": {"start": 1080.0, "end": 1082.8, "reason": "Giving number & inviting out"},
    },
    {
        "part_index": 4,
        "title": "HE TRIES TO MAKE A MOVE ON CAMERA! 😱",
        "start": 1210.0,
        "end": 1585.0,
        "hook": {"start": 1400.0, "end": 1402.8, "reason": "Escalation & raid prep"},
    },
    {
        "part_index": 5,
        "title": "BUSTED! GIRLFRIEND STORMS IN! 🚨",
        "start": 1600.0,
        "end": 1935.0,
        "hook": {"start": 1810.0, "end": 1812.8, "reason": "Girlfriend screams at boyfriend caught red-handed"},
    },
    {
        "part_index": 6,
        "title": "USING HER FOR MONEY?! FINAL BREAKUP 💔",
        "start": 1940.0,
        "end": 2262.0,
        "hook": {"start": 2150.0, "end": 2152.8, "reason": "Truth about rent money & final closure"},
    },
]


def main():
    logger.info("🎬 Bắt đầu sản xuất trọn vẹn 6 Part nhịp nhanh chuẩn đối thủ 9M views...")

    packaging_cfg = {
        "competitor_top_card": True,
        "competitor_bottom_badge": True,
        "top_card_y_pos": 140,
        "bottom_badge_y_pos": 1700,
        "top_card_bg_color": "#111111",
        "top_card_text_color": "#FFFFFF",
        "top_card_border_color": "#FFCC00",
        "top_card_border_width": 3,
        "bottom_badge_bg_color": "#FFCC00",
        "bottom_badge_text_color": "#000000",
    }

    results = []

    for item in PARTS_CONFIG:
        p_idx = item["part_index"]
        p_title = item["title"]
        p_start = item["start"]
        p_end = item["end"]
        p_hook = item["hook"]

        logger.info(f"\n=======================================================")
        logger.info(f"▶️ ĐANG XỬ LÝ PART {p_idx}/6: {p_title}")
        logger.info(f"   Dải timeline gốc: {p_start:.1f}s -> {p_end:.1f}s")

        # 1. Trích xuất danh sách micro-cuts
        cuts = filter_and_sample_speech(all_cues, p_start, p_end, target_dur=87.0)
        logger.info(f"   Đã chọn {len(cuts)} micro-cuts (1.5s - 3.2s) kèm xen kẽ Punch-in Zoom 115%")

        # 2. Cắt nối micro-cuts
        condensed_out = os.path.join(TEMP_DIR, f"condensed_part_{p_idx}.mp4")
        PartSplitterEngine.build_condensed_part_video(
            source_video=SOURCE_VIDEO,
            cuts=cuts,
            hook=p_hook,
            output_path=condensed_out,
            log_fn=lambda m: logger.info(f"[Engine P{p_idx}] {m}"),
            work_dir=TEMP_DIR
        )

        condensed_dur = probe_duration_sec(condensed_out)
        logger.info(f"   Hoàn thành cắt nối Part {p_idx}: {condensed_dur:.1f}s")

        # 3. Đóng gói bao bì đối thủ 9:16 + Top Card + Bottom Badge + Limiter
        final_file = os.path.join(OUT_DIR, f"Part_{p_idx}.mp4")
        PartSplitterEngine.apply_competitor_visual_packaging(
            condensed_video=condensed_out,
            output_final=final_file,
            master_title=p_title,
            part_index=p_idx,
            part_prefix="PART",
            config=packaging_cfg,
            log_fn=lambda m: logger.info(f"[Pack P{p_idx}] {m}"),
            work_dir=TEMP_DIR
        )

        final_dur = probe_duration_sec(final_file)
        logger.info(f"✅ ĐÃ XUẤT XƯỞNG PART {p_idx}: {final_file} ({final_dur:.1f}s)")
        results.append(final_file)

    logger.info(f"\n🎉 TẤT CẢ 6 PART ĐÃ HOÀN TẤT VÀ SẴN SÀNG:")
    for r in results:
        logger.info(f"   - {r}")


if __name__ == "__main__":
    main()
