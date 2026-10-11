"""Script sản xuất 6 Part chuẩn 10/10 theo đúng cốt truyện kịch tính thực tế:
LOYALTY TEST *BACKFIRES* GIRLFRIEND GETS EXPOSED INSTEAD!

Quy tắc biên tập:
1. Mỗi Part là 1 TRƯỜNG ĐOẠN LIỀN MẠCH, TẬP TRUNG (Không lan man, không nhảy cóc).
2. Câu thoại TRÒN VÀNH RÕ CHỮ, KHÔNG BAO GIỜ BỊ ĐỨT SUB hay cắt giữa chừng.
3. Chuyển cảnh thị giác nhanh theo góc quay tự nhiên của máy quay (Multi-camera cuts).
4. Xen kẽ Punch-in Zoom (115%) tạo nhịp độ dồn dập.
5. Đóng gói Canvas 9:16 (1080x1920), Nền Cinematic Blur, Top Card viền vàng, Bottom Badge, -14 LUFS Audio.
"""
from __future__ import annotations

import os
import sys
import logging

sys.path.append(r"E:\Video_summarizer_Pro")

from part_splitter_engine import PartSplitterEngine, probe_duration_sec

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("Build6Parts10Out10")

SRC_DIR = r"C:\Users\DungAnh\Downloads\hitomi_downloader_GUI\phantich"
OUT_DIR = r"E:\Video_summarizer_Pro\output\Loyalty_Test_6_Parts"
TEMP_DIR = r"E:\Video_summarizer_Pro\temp\render_6_parts"

os.makedirs(OUT_DIR, exist_ok=True)
os.makedirs(TEMP_DIR, exist_ok=True)

src_files = [os.path.join(SRC_DIR, f) for f in os.listdir(SRC_DIR) if "TYDkO6JzeJA" in f]
SOURCE_VIDEO = src_files[0]

# Danh sách 6 Part với các trường đoạn hội thoại liền mạch 100% không đứt chữ
PARTS = [
    {
        "part_index": 1,
        "title": "4 YEARS DATING & ONLY MET TWICE?! 🚩",
        "hook": {"start": 438.0, "end": 441.8, "reason": "Girl flirts with Host: You single?"},
        "cuts": [
            {"start": 188.8, "end": 203.0, "punch_in": False, "summary": "Host asks why test him; Girl says 4 yrs dating, wont live together"},
            {"start": 203.2, "end": 211.0, "punch_in": True, "summary": "Host disbelief: He pays rent elsewhere?! Girl: cant stay >2 weeks"},
            {"start": 215.0, "end": 223.0, "punch_in": False, "summary": "He says I dont trust you; only meet at hotels"},
            {"start": 223.5, "end": 234.0, "punch_in": True, "summary": "Bombshell: Saw each other TWICE in 4 years! This is your man?!"},
            {"start": 270.5, "end": 286.0, "punch_in": False, "summary": "Girl moving to Florida; He offered apartment down the street!"},
            {"start": 398.0, "end": 415.0, "punch_in": True, "summary": "Decoy Alyssa trap setup for photoshoot"},
            {"start": 487.0, "end": 495.5, "punch_in": False, "summary": "Cliffhanger: Host calls it toxic & weird as f***"}
        ]
    },
    {
        "part_index": 2,
        "title": "BOYFRIEND AT THE BIKINI PHOTOSHOOT! 👙",
        "hook": {"start": 748.0, "end": 751.5, "reason": "My heart is beating, haven't seen him in 3 years"},
        "cuts": [
            {"start": 720.5, "end": 735.0, "punch_in": False, "summary": "Host gives photoshoot theme: hot girl summer, feeling sexy"},
            {"start": 747.5, "end": 754.5, "punch_in": True, "summary": "Girlfriend in surveillance van: My heart is beating, he is downstairs!"},
            {"start": 764.0, "end": 779.0, "punch_in": False, "summary": "Jordan directing models to the pool and hot tub"},
            {"start": 798.5, "end": 815.0, "punch_in": True, "summary": "Jordan coaching poses: Hourglass figure, sexy shots, gorgeous!"},
            {"start": 823.5, "end": 836.0, "punch_in": False, "summary": "Girlfriend watching monitor jealous: She cute, he like dark skin!"},
            {"start": 838.0, "end": 846.5, "punch_in": True, "summary": "Cliffhanger: The bestie shoot begins at the pool"}
        ]
    },
    {
        "part_index": 3,
        "title": "GIRLFRIEND WATCHES DECOY TEMPT HIM! 🤫",
        "hook": {"start": 952.5, "end": 956.5, "reason": "You think he would do something with his clients?"},
        "cuts": [
            {"start": 883.0, "end": 901.0, "punch_in": False, "summary": "Jordan positioning models by the water on floaties"},
            {"start": 952.0, "end": 962.0, "punch_in": True, "summary": "Host asks girlfriend: You feel like he would mess with clients? Girlfriend: I thought so!"},
            {"start": 988.0, "end": 1000.0, "punch_in": False, "summary": "Jordan directing models: Look at each other like in love!"},
            {"start": 1082.0, "end": 1090.0, "punch_in": True, "summary": "Host narration: Alyssa orchestrating, keeping it professional so far"},
            {"start": 1134.0, "end": 1142.0, "punch_in": False, "summary": "Host: Hopefully Alyssa turns it up a notch one-on-one inside"},
            {"start": 1160.0, "end": 1175.0, "punch_in": True, "summary": "One-on-one inside: Girlfriend panics: Why is she sitting so close to him?!"},
            {"start": 1189.5, "end": 1201.0, "punch_in": False, "summary": "Girlfriend: She gotta get a bit more aggressive, let's see!"}
        ]
    },
    {
        "part_index": 4,
        "title": "HE PASSED THE TEST! ...OR DID HE?! 😱",
        "hook": {"start": 1410.5, "end": 1414.5, "reason": "I put you in a loyalty test baby, you passed!"},
        "cuts": [
            {"start": 1221.0, "end": 1232.0, "punch_in": False, "summary": "Jordan talking to Alyssa about Charlotte, keeping boundaries"},
            {"start": 1243.5, "end": 1252.0, "punch_in": True, "summary": "Jordan: I try to stay away from outside, nothing out there for me"},
            {"start": 1350.0, "end": 1362.0, "punch_in": False, "summary": "Host & girlfriend agree: Jordan doesn't look interested at all!"},
            {"start": 1406.0, "end": 1421.0, "punch_in": True, "summary": "Girlfriend storms in hugging Jordan: I love you, you were in a loyalty test!"},
            {"start": 1421.5, "end": 1435.0, "punch_in": False, "summary": "Jordan confused: What's this all about? I missed you too but what is this?!"},
            {"start": 1456.0, "end": 1470.0, "punch_in": True, "summary": "Host enters: You were on a loyalty test. Jordan: So you don't trust me?!"},
            {"start": 1475.0, "end": 1485.0, "punch_in": False, "summary": "Girlfriend: I'm sorry baby, you passed! Jordan: Why would you do this?"},
            {"start": 1509.5, "end": 1519.0, "punch_in": True, "summary": "Cliffhanger: Host Jack: Bro, sit down. I want to show you a clip of me with HER!"}
        ]
    },
    {
        "part_index": 5,
        "title": "THE BACKFIRE! HOST EXPOSES GIRLFRIEND! 🚨",
        "hook": {"start": 1548.0, "end": 1552.0, "reason": "Before we tested him, I tested HER and she had no idea!"},
        "cuts": [
            {"start": 1510.0, "end": 1526.0, "punch_in": False, "summary": "Host asks Jordan to sit: I went to eat dinner with her, see the interaction"},
            {"start": 1533.0, "end": 1551.5, "punch_in": True, "summary": "Host reveals: Something was off about her, so I tested HER before testing Jordan!"},
            {"start": 1551.5, "end": 1559.0, "punch_in": False, "summary": "Girlfriend panics: Talk to me outside! Host: No, let him watch the video!"},
            {"start": 1756.0, "end": 1770.0, "punch_in": True, "summary": "Restaurant recording plays: Host calls out her bad conduct"},
            {"start": 1776.0, "end": 1788.0, "punch_in": False, "summary": "BOMBSHELL: Host asks her: What does that have to do with you wanting to suck my d***?!"},
            {"start": 1788.5, "end": 1795.0, "punch_in": True, "summary": "Girlfriend on tape: Like I said, I be horny! Host tells her to leave"},
            {"start": 1840.5, "end": 1855.0, "punch_in": False, "summary": "Cliffhanger: Video ends. Jordan sits in utter disbelief: What the f***?!"}
        ]
    },
    {
        "part_index": 6,
        "title": "DID YOU SAY THAT?! THE FINAL BREAKUP! 💔",
        "hook": {"start": 1860.0, "end": 1864.0, "reason": "What did you say bro?! You going like that?!"},
        "cuts": [
            {"start": 1860.0, "end": 1870.0, "punch_in": False, "summary": "Jordan confronts girlfriend: What did you say?! Girlfriend lies: I never said that!"},
            {"start": 1870.0, "end": 1882.0, "punch_in": True, "summary": "Jordan snaps: What you mean you never said that?! We just watched you say that!"},
            {"start": 1884.0, "end": 1894.5, "punch_in": False, "summary": "Jordan: Did you say that in the video, YES OR NO?! Girlfriend: Maybe no..."},
            {"start": 1907.0, "end": 1917.0, "punch_in": True, "summary": "Host calls her out for zero accountability; Jordan: Bro this is crazy!"},
            {"start": 2114.0, "end": 2125.0, "punch_in": False, "summary": "Jordan furious: I'm over here being loyal to you and you doing this?!"},
            {"start": 2125.0, "end": 2138.0, "punch_in": True, "summary": "Final breakup: Jordan walks out on camera, refusing to deal with a cheater"},
            {"start": 2240.0, "end": 2255.0, "punch_in": False, "summary": "Closing reality check by Host Jack on loyalty and toxic setups"}
        ]
    }
]

def main():
    cfg = {
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

    logger.info("🎬 Bắt đầu render lại toàn bộ 6 Part với cốt truyện liền mạch 100% không đứt chữ...")

    for p in PARTS:
        p_idx = p["part_index"]
        p_title = p["title"]
        p_hook = p["hook"]
        p_cuts = p["cuts"]

        logger.info(f"\n=======================================================")
        logger.info(f"▶️ RENDER PART {p_idx}/6: {p_title}")

        # 1. Cắt nối
        condensed_out = os.path.join(TEMP_DIR, f"condensed_p{p_idx}_perfect.mp4")
        PartSplitterEngine.build_condensed_part_video(
            source_video=SOURCE_VIDEO,
            cuts=p_cuts,
            hook=p_hook,
            output_path=condensed_out,
            log_fn=lambda m: logger.info(f"[P{p_idx}] {m}"),
            work_dir=TEMP_DIR
        )

        # 2. Đóng gói bao bì 9:16
        final_out = os.path.join(OUT_DIR, f"Part_{p_idx}.mp4")
        PartSplitterEngine.apply_competitor_visual_packaging(
            condensed_video=condensed_out,
            output_final=final_out,
            master_title=p_title,
            part_index=p_idx,
            part_prefix="PART",
            config=cfg,
            log_fn=lambda m: logger.info(f"[P{p_idx} Pack] {m}"),
            work_dir=TEMP_DIR
        )

        dur = probe_duration_sec(final_out)
        logger.info(f"✅ HOÀN TẤT PART {p_idx}: {final_out} ({dur:.1f}s)")

    logger.info("🎉 TẤT CẢ 6 PART ĐÃ ĐƯỢC NÂNG CẤP LÊN CHUẨN 10/10 HOÀN HẢO!")

if __name__ == "__main__":
    main()
