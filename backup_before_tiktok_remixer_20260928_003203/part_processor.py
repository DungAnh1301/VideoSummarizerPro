"""Split one prepared HD source and its 360p proxy into independent part jobs."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path


class PartProcessor:
    _SRT_TIME_RE = re.compile(
        r"(?P<h>\d{1,2}):(?P<m>\d{2}):(?P<s>\d{2})[,.](?P<ms>\d{3})"
    )

    @classmethod
    def _srt_seconds(cls, value: str) -> float:
        match = cls._SRT_TIME_RE.fullmatch(str(value or "").strip())
        if not match:
            raise ValueError(value)
        return (int(match.group("h")) * 3600 + int(match.group("m")) * 60
                + int(match.group("s")) + int(match.group("ms")) / 1000.0)

    @staticmethod
    def _srt_stamp(value: float) -> str:
        total_ms = max(0, int(round(float(value) * 1000)))
        hours, remain = divmod(total_ms, 3_600_000)
        minutes, remain = divmod(remain, 60_000)
        seconds, millis = divmod(remain, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    @classmethod
    def split_srt(cls, source_srt: str, destination: str,
                  start: float, end: float) -> str:
        """Keep cues intersecting a Part and translate them to Part-local time."""
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        if not source_srt or not os.path.isfile(source_srt):
            Path(destination).write_text("", encoding="utf-8")
            return ""
        raw = Path(source_srt).read_text(encoding="utf-8-sig", errors="replace")
        blocks = re.split(r"\r?\n\s*\r?\n", raw.strip()) if raw.strip() else []
        output = []
        timing = re.compile(
            r"(?P<a>\d{1,2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
            r"(?P<b>\d{1,2}:\d{2}:\d{2}[,.]\d{3})"
        )
        for block in blocks:
            lines = block.splitlines()
            match = next((timing.search(line) for line in lines if timing.search(line)), None)
            if not match:
                continue
            cue_start = cls._srt_seconds(match.group("a"))
            cue_end = cls._srt_seconds(match.group("b"))
            if cue_end <= start or cue_start >= end:
                continue
            local_start = max(0.0, cue_start - start)
            local_end = min(end - start, cue_end - start)
            text_lines = [line.strip() for line in lines
                          if line.strip() and not line.strip().isdigit()
                          and "-->" not in line]
            if local_end > local_start and text_lines:
                output.append(
                    f"{len(output)+1}\n{cls._srt_stamp(local_start)} --> "
                    f"{cls._srt_stamp(local_end)}\n{' '.join(text_lines)}"
                )
        rendered = "\n\n".join(output)
        Path(destination).write_text(rendered, encoding="utf-8")
        return rendered

    @staticmethod
    def _safe_stem(value: str) -> str:
        stem = re.sub(r'[<>:"/\\|?*]+', " ", str(value or "video"))
        stem = re.sub(r"\s+", " ", stem).strip(" .-")
        return (stem or "video")[:90]

    @staticmethod
    def _duration(path: str) -> float:
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", path],
                capture_output=True, text=True, check=False,
                creationflags=0x08000000 if os.name == "nt" else 0,
            )
            return float((result.stdout or "0").strip() or 0.0)
        except (OSError, TypeError, ValueError):
            return 0.0

    @staticmethod
    def validate_ranges(video_duration: float, cut_points: list[float]) -> list[tuple[float, float]]:
        duration = float(video_duration or 0.0)
        if duration <= 0:
            raise ValueError("Không đọc được thời lượng video để chia Part.")
        points = [float(value) for value in cut_points]
        if any(value <= 0 or value >= duration for value in points):
            raise ValueError("Mốc chia Part phải lớn hơn 00:00:00 và nhỏ hơn thời lượng video.")
        if points != sorted(points) or len(points) != len(set(points)):
            raise ValueError("Các mốc chia Part phải tăng dần và không được trùng nhau.")
        boundaries = [0.0, *points, duration]
        return [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1)]

    @staticmethod
    def _cut(source: str, destination: str, start: float, end: float) -> None:
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        if not os.path.isfile(source) or os.path.getsize(source) < 1024:
            raise FileNotFoundError(f"Nguồn cắt Part không hợp lệ: {source}")
        expected = end - start
        temporary = destination + ".cutting.mp4"
        try:
            if os.path.exists(temporary):
                os.remove(temporary)
        except OSError:
            pass

        # Cắt packet trước: nhanh, không giải mã AV1/HEVC dài hàng chục phút.
        copy_command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{expected:.3f}", "-i", source,
            "-map", "0:v:0", "-map", "0:a?", "-c", "copy",
            "-avoid_negative_ts", "make_zero", "-reset_timestamps", "1",
            "-movflags", "+faststart", temporary,
        ]
        flags = 0x08000000 if os.name == "nt" else 0
        result = subprocess.run(copy_command, capture_output=True, text=True, check=False,
                                creationflags=flags)
        actual = PartProcessor._duration(temporary)

        # Chỉ re-encode khi stream-copy thực sự không dùng được hoặc lệch quá mức.
        if result.returncode != 0 or actual <= 0 or abs(actual - expected) > 3.0:
            try:
                if os.path.exists(temporary):
                    os.remove(temporary)
            except OSError:
                pass
            encode_command = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{start:.3f}", "-t", f"{expected:.3f}", "-i", source,
                "-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264",
                "-preset", "ultrafast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", temporary,
            ]
            retry = subprocess.run(encode_command, capture_output=True, text=True, check=False,
                                   creationflags=flags)
            actual = PartProcessor._duration(temporary)
            if retry.returncode != 0 or actual <= 0:
                error = (retry.stderr or result.stderr or "FFmpeg không trả stderr").strip()
                raise RuntimeError(
                    f"Cắt Part thất bại [{start:.1f}s–{end:.1f}s]: {error[-1800:]}"
                )
        try:
            os.replace(temporary, destination)
        except OSError as exc:
            raise RuntimeError(f"Không thể ghi file Part (có thể đang bị chương trình khác mở): {destination}: {exc}") from exc
        if os.path.getsize(destination) < 1024:
            raise RuntimeError(f"Cắt Part tạo file rỗng: {destination}")

    @classmethod
    def split_sources(cls, source_hd: str, source_low: str, video_duration: float,
                      cut_points: list[float], output_dir: str,
                      source_title: str = "video",
                      source_gemini: str = "") -> list[dict]:
        ranges = cls.validate_ranges(video_duration, cut_points)
        safe_title = cls._safe_stem(source_title)
        results = []
        for index, (start, end) in enumerate(ranges, 1):
            part_dir = os.path.join(output_dir, f"part_{index:02d}_source")
            # LocalSourceProcessor derives its cache directory from only the first
            # 20 normalized filename characters. Keep the part identity at the
            # beginning so a long title cannot truncate "Part 01/02" and make
            # every child reuse the same working directory.
            hd_path = os.path.join(part_dir, f"Part {index:02d} - {safe_title}.mp4")
            low_path = os.path.join(part_dir, "source_video_low.mp4")
            gemini_path = os.path.join(part_dir, "gemini_preview.mp4") if source_gemini else ""
            cls._cut(source_hd, hd_path, start, end)
            cls._cut(source_low, low_path, start, end)
            if source_gemini:
                cls._cut(source_gemini, gemini_path, start, end)
            results.append({
                "index": index, "start": start, "end": end,
                "duration": end - start, "video_path": hd_path,
                "low_res_path": low_path, "gemini_preview_path": gemini_path,
            })
        return results
