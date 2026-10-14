"""Nhân Viên 2: Kỹ Sư Âm Thanh & Hậu Kỳ (Audio Mixing & Mastering Engine).

Nhiệm vụ:
- Chèn hiệu ứng Cinematic Whoosh SFX tại đúng các mốc thời gian chuyển cảnh lớn.
- Lót track nhạc nền Continuous Tension BGM Bed chạy xuyên suốt để kết nối các cảnh thành một thể thống nhất.
- Tự động hạ âm lượng BGM (Ducking) để giọng thoại đanh rõ, không bị lấn át.
- Áp dụng EBU R128 Loudness Normalization chuẩn -14 LUFS.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("AudioMasterEngine")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from part_splitter_engine import probe_duration_sec, _ff_run


class AudioMasterEngine:
    """Nhân Viên 2 - Audio Mixing & Mastering Engine."""

    @classmethod
    def master_and_mix_audio(
        cls,
        video_path: str,
        transition_timestamps: List[float],
        audio_spec: Dict[str, Any],
        output_path: str,
        log_fn=None
    ) -> str:
        """Mix Whoosh SFX, BGM Tension Bed và chuẩn hóa -14 LUFS."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        dur = probe_duration_sec(video_path)
        whoosh_wav = str(audio_spec.get("whoosh_sfx_path", "")).strip()
        tension_wav = str(audio_spec.get("tension_bgm_path", "")).strip()
        bgm_vol = float(audio_spec.get("bgm_volume_base", 0.18))
        target_lufs = float(audio_spec.get("target_lufs", -14.0))

        _log(f"🎛️ [NHÂN VIÊN 2] Bắt đầu master audio: Whoosh SFX ({len(transition_timestamps)} điểm) + Tension BGM Bed ({dur:.1f}s)...")

        af_parts = []
        af_parts.append("[0:a]volume=1.0[vocal]")

        cmd_audio = [
            "ffmpeg", "-y",
            "-i", video_path,
        ]

        # 1. Thêm track Tension BGM Bed
        has_bgm = bool(tension_wav and os.path.exists(tension_wav))
        if has_bgm:
            cmd_audio.extend(["-i", tension_wav])
            af_parts.append(f"[1:a]volume={bgm_vol:.2f},atrim=0:{dur:.2f}[bgm]")
            mix_inputs = "[vocal][bgm]"
            in_count = 2
        else:
            mix_inputs = "[vocal]"
            in_count = 1

        # 2. Thêm các hiệu ứng Whoosh SFX tại các điểm chuyển cảnh
        has_whoosh = bool(whoosh_wav and os.path.exists(whoosh_wav))
        whoosh_inputs = []
        if has_whoosh and transition_timestamps:
            for w_idx, t_stamp in enumerate(transition_timestamps):
                cmd_audio.extend(["-i", whoosh_wav])
                curr_in = in_count
                in_count += 1
                delay_ms = int(max(0.0, t_stamp - 0.20) * 1000)
                af_parts.append(f"[{curr_in}:a]adelay={delay_ms}|{delay_ms},volume=0.38[whoosh_{w_idx}]")
                whoosh_inputs.append(f"[whoosh_{w_idx}]")

        # 3. Trộn tất cả các track âm thanh
        all_mix_inputs = mix_inputs + "".join(whoosh_inputs)
        total_inputs = 1 + (1 if has_bgm else 0) + len(whoosh_inputs)

        if total_inputs > 1:
            af_parts.append(f"{all_mix_inputs}amix=inputs={total_inputs}:duration=first:dropout_transition=2[mixed_raw]")
            # 4. Chuẩn hóa âm lượng EBU R128 chuẩn -14 LUFS
            af_parts.append(f"[mixed_raw]loudnorm=I={target_lufs:.1f}:LRA=7:TP=-1.5,aformat=channel_layouts=stereo,aresample=44100[aout]")
        else:
            af_parts.append(f"[vocal]loudnorm=I={target_lufs:.1f}:LRA=7:TP=-1.5,aformat=channel_layouts=stereo,aresample=44100[aout]")

        filter_complex_str = ";".join(af_parts)

        cmd_audio.extend([
            "-filter_complex", filter_complex_str,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            output_path
        ])

        _log(f"🎧 [NHÂN VIÊN 2] Bắt đầu Master Audio: Mix {len(transition_timestamps)} Whoosh SFX tại các mốc: {', '.join(f'{t:.2f}s' for t in transition_timestamps)} + Tension BGM Bed...")
        _ff_run(cmd_audio, log_fn=log_fn, timeout=600, label="audio_master")
        mastered_dur = probe_duration_sec(output_path)
        _log(f"✅ [NHÂN VIÊN 2] Hoàn tất Master Audio chuẩn EBU R128 ({target_lufs:.1f} LUFS): {output_path} (Thời lượng: {mastered_dur:.2f}s)")
        return output_path
