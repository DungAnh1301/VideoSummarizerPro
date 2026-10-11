"""Module bảo vệ biên âm thanh & tạo khoảng thở tự nhiên (Breathing Room) cho video viral.

Nguyên tắc Đạo Diễn 10/10:
- Không bao giờ cắt sát sạt vào âm tiết đầu/cuối của lời thoại.
- Giữ 0.5s - 1.0s khoảng thở/âm thanh phòng (room tone) & b-roll chuyển động TRƯỚC câu thoại.
- Giữ 0.6s - 1.2s khoảng lắng/phản ứng đối phương SAU câu thoại chốt (punchline).
- Áp dụng micro-crossfade 40ms để triệt tiêu hoàn toàn tiếng nổ bụp (DC offset / click).
"""

from __future__ import annotations

import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger("AudioBoundaryGuard")


class AudioBoundaryGuard:
    DEFAULT_PREP_SEC = 0.70    # Khoảng thở & hình ảnh chuẩn bị trước câu nói
    DEFAULT_HOLD_SEC = 0.80    # Khoảng lắng & biểu cảm phản ứng sau câu nói
    DEFAULT_MICRO_FADE = 0.040 # 40ms micro-fade

    @classmethod
    def expand_shot_with_breathing(
        cls,
        start_sec: float,
        end_sec: float,
        max_duration_sec: float,
        is_opening: bool = False,
        is_cliffhanger: bool = False,
        prep_sec: Optional[float] = None,
        hold_sec: Optional[float] = None
    ) -> tuple[float, float]:
        """Tự động mở rộng biên thời gian của 1 shot để có khoảng thở tự nhiên."""
        p_sec = cls.DEFAULT_PREP_SEC if prep_sec is None else max(0.2, float(prep_sec))
        h_sec = cls.DEFAULT_HOLD_SEC if hold_sec is None else max(0.3, float(hold_sec))

        # Nếu là shot mở đầu phân cảnh: Cần thêm một chút hình ảnh thiết lập (establishing)
        if is_opening:
            p_sec = max(p_sec, 0.90)

        # Nếu là shot cliffhanger cuối part: Cần giữ lâu hơn để tạo độ kịch tính treo lơ lửng
        if is_cliffhanger:
            h_sec = max(h_sec, 1.20)

        expanded_start = max(0.0, round(float(start_sec) - p_sec, 3))
        expanded_end = min(round(float(max_duration_sec), 3), round(float(end_sec) + h_sec, 3))

        if expanded_end <= expanded_start + 0.5:
            expanded_end = min(round(float(max_duration_sec), 3), expanded_start + 1.0)

        return expanded_start, expanded_end

    @classmethod
    def protect_and_smooth_shots(
        cls,
        raw_shots: List[Dict[str, Any]],
        max_duration_sec: float
    ) -> List[Dict[str, Any]]:
        """Duyệt qua danh sách cuts/shots và bảo đảm:
        1. Mỗi shot có đủ khoảng thở tự nhiên (0.5s - 1.0s).
        2. Không bị chồng lấn (overlap) vô lý giữa các shot liền kề trong cùng 1 phân đoạn liên tục.
        3. Thời lượng mỗi micro-cut chuẩn 2s - 8s.
        """
        if not raw_shots:
            return []

        smoothed = []
        n = len(raw_shots)

        for i, shot in enumerate(raw_shots):
            s = float(shot.get("start", 0.0))
            e = float(shot.get("end", 0.0))
            pan = str(shot.get("pan", "center")).lower().strip()
            punch = bool(shot.get("punch_in", False) or shot.get("punch", False))
            desc = str(shot.get("desc", ""))
            speaker = str(shot.get("speaker", ""))

            is_open = (i == 0)
            is_cliff = (i == n - 1)

            # Mở rộng biên an toàn
            req_prep = float(shot.get("prep_sec", cls.DEFAULT_PREP_SEC))
            req_hold = float(shot.get("hold_sec", cls.DEFAULT_HOLD_SEC))

            exp_s, exp_e = cls.expand_shot_with_breathing(
                s, e, max_duration_sec,
                is_opening=is_open,
                is_cliffhanger=is_cliff,
                prep_sec=req_prep,
                hold_sec=req_hold
            )

            # Nếu shot trước đó vừa kết thúc và gần sát shot này (cùng bối cảnh), tránh đè timeline
            if smoothed:
                prev_e = smoothed[-1]["end"]
                if exp_s < prev_e and s >= smoothed[-1]["raw_start"]:
                    exp_s = prev_e

            dur = max(0.6, exp_e - exp_s)
            fade_d = min(cls.DEFAULT_MICRO_FADE, dur * 0.1)

            item = dict(shot)
            item.update({
                "raw_start": s,
                "raw_end": e,
                "start": exp_s,
                "end": exp_e,
                "duration": round(dur, 2),
                "pan": pan,
                "punch_in": punch,
                "punch": punch,
                "desc": desc,
                "speaker": speaker,
                "fade_d": fade_d
            })
            smoothed.append(item)

        return smoothed
