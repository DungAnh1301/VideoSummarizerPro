# -*- coding: utf-8 -*-
"""
Timesub B-roll: khớp nghĩa script TTS (summary_output.txt) với transcript_sub.srt,
rồi chấm hình — chỉ giữ đoạn ≤6s, ít chuyển cảnh.
"""
from __future__ import annotations

import math
import os
import re
import subprocess
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("BrollSemantic")

MAX_CLIP_SEC = 6.0
MIN_CLIP_SEC = 1.5
SRT_TIME_RE = re.compile(
    r"(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})"
)
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")
TOKEN_RE = re.compile(r"[^\w\s]+", re.UNICODE)

# absdiff trung bình (gray 160x90) / tương quan histogram — vượt ngưỡng = 1 cut
ABS_DIFF_CUT = 22.0
HIST_CORR_CUT = 0.72
# 6s cho tối đa 2 cut; clip ngắn hơn siết chặt hơn
CUTS_PER_3S = 1
TOP_VISUAL_PROBE = 80
OVERLAP_GAP = 0.12

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "you", "your", "we", "they", "it", "this", "that", "for", "on", "with", "at",
    "from", "but", "not", "be", "as", "by", "his", "her", "he", "she", "i", "me",
    "my", "our", "so", "if", "just", "about", "into", "out", "up", "down",
    "và", "của", "là", "các", "một", "có", "không", "được", "cho", "với", "từ",
    "những", "này", "đó", "thì", "đã", "sẽ", "trong", "để", "ra", "lại",
}


def srt_time_to_seconds(time_str: str) -> float:
    try:
        time_str = (time_str or "").strip().replace(",", ".")
        parts = time_str.split(":")
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except Exception:
        return 0.0


def parse_srt(srt_path: str) -> list:
    """Đọc SRT → [{start, end, text}, …]."""
    cues = []
    if not srt_path or not os.path.exists(srt_path):
        return cues
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        logger.error(f"❌ [TIMESUB] Không đọc được SRT: {e}")
        return cues

    blocks = re.split(r"\n\s*\n", content.strip())
    for block in blocks:
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        time_line = None
        text_start = 0
        for i, ln in enumerate(lines):
            if "-->" in ln:
                time_line = ln
                text_start = i + 1
                break
        if not time_line:
            continue
        m = SRT_TIME_RE.search(time_line)
        if not m:
            continue
        start_sec = srt_time_to_seconds(m.group(1))
        end_sec = srt_time_to_seconds(m.group(2))
        if end_sec <= start_sec:
            continue
        text = " ".join(lines[text_start:]).strip()
        cues.append({"start": start_sec, "end": end_sec, "text": text})
    return cues


def chunk_script(script_text: str, max_words: int = 36) -> list:
    """Tách kịch bản TTS thành các câu/đoạn ngắn để so khớp SRT."""
    text = (script_text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    raw = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
    if not raw:
        raw = [text]
    chunks = []
    buf = []
    buf_words = 0
    for sent in raw:
        n = len(sent.split())
        if buf and buf_words + n > max_words:
            chunks.append(" ".join(buf).strip())
            buf, buf_words = [], 0
        buf.append(sent)
        buf_words += n
        if buf_words >= max_words:
            chunks.append(" ".join(buf).strip())
            buf, buf_words = [], 0
    if buf:
        chunks.append(" ".join(buf).strip())
    return [c for c in chunks if c]


def clamp_window(start: float, end: float, match_t: float = None,
                 max_dur: float = MAX_CLIP_SEC, min_dur: float = MIN_CLIP_SEC,
                 safe_start: float = 0.0, safe_end: float = None) -> tuple:
    """Cắt/căng cửa sổ về [min_dur, max_dur], neo quanh điểm khớp."""
    start = float(start)
    end = float(end)
    if end <= start:
        end = start + min_dur
    if safe_end is None:
        safe_end = end
    safe_start = float(safe_start)
    safe_end = float(safe_end)
    match_t = float(match_t if match_t is not None else start)
    match_t = min(max(match_t, start), end)

    dur = end - start
    if dur > max_dur:
        half = max_dur / 2.0
        start = match_t - half
        end = match_t + half
        if start < safe_start:
            start = safe_start
            end = start + max_dur
        if end > safe_end:
            end = safe_end
            start = max(safe_start, end - max_dur)
        if end - start > max_dur:
            end = start + max_dur
    elif dur < min_dur:
        end = min(safe_end, start + min_dur)
        if end - start < min_dur:
            start = max(safe_start, end - min_dur)

    start = max(safe_start, start)
    end = min(safe_end, end)
    if end <= start:
        return start, min(safe_end, start + min(max_dur, min_dur))
    if end - start > max_dur + 1e-6:
        end = start + max_dur
    return round(start, 3), round(end, 3)


def _tokenize(text: str) -> list:
    text = TOKEN_RE.sub(" ", (text or "").lower())
    return [t for t in text.split() if len(t) >= 2 and t not in STOPWORDS]


def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / float(len(sa | sb))


def _tfidf_cosine_rows(docs_tokens: list):
    """Ma trận TF-IDF đã L2-normalize (numpy). Hàng trống → vector 0."""
    import numpy as np

    df = Counter()
    for toks in docs_tokens:
        df.update(set(toks))
    vocab = list(df.keys())
    if not vocab:
        n = len(docs_tokens)
        return np.zeros((n, 1), dtype=np.float64)
    n_docs = len(docs_tokens)
    idf = {t: math.log((n_docs + 1.0) / (df[t] + 1.0)) + 1.0 for t in vocab}
    index = {t: i for i, t in enumerate(vocab)}
    mat = np.zeros((n_docs, len(vocab)), dtype=np.float64)
    for i, toks in enumerate(docs_tokens):
        if not toks:
            continue
        tf = Counter(toks)
        inv_len = 1.0 / max(len(toks), 1)
        for t, c in tf.items():
            j = index.get(t)
            if j is not None:
                mat[i, j] = (c * inv_len) * idf[t]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def semantic_scores(script_chunks: list, window_texts: list) -> list:
    """Mỗi cửa sổ: max cosine TF-IDF với mọi chunk script; fallback Jaccard."""
    import numpy as np

    if not script_chunks or not window_texts:
        return [0.0] * len(window_texts)

    q_toks = [_tokenize(c) for c in script_chunks]
    d_toks = [_tokenize(t) for t in window_texts]
    all_toks = q_toks + d_toks
    try:
        mat = _tfidf_cosine_rows(all_toks)
        q = mat[: len(q_toks)]
        d = mat[len(q_toks) :]
        sim = q @ d.T
        tfidf_best = sim.max(axis=0) if sim.size else np.zeros(len(window_texts))
    except Exception as e:
        logger.warning(f"⚠️ [TIMESUB] TF-IDF lỗi ({e}) — dùng Jaccard.")
        tfidf_best = np.zeros(len(window_texts))

    scores = []
    for j, dtok in enumerate(d_toks):
        jac = max((_jaccard(qt, dtok) for qt in q_toks), default=0.0)
        tf = float(tfidf_best[j]) if j < len(tfidf_best) else 0.0
        scores.append(max(0.0, 0.75 * tf + 0.25 * jac))
    return scores


def _max_allowed_cuts(duration: float) -> int:
    return max(1, int(round(max(duration, 0.1) / 3.0) * CUTS_PER_3S))


def _configure_opencv_ffmpeg_quiet() -> None:
    """OpenCV dùng FFmpeg nội bộ — 'partial file' là AV_LOG_ERROR, không làm hỏng job."""
    os.environ["OPENCV_FFMPEG_LOGLEVEL"] = "8"  # AV_LOG_FATAL — ẩn partial file (error=16)
    try:
        import cv2
        if hasattr(cv2, "utils") and hasattr(cv2.utils, "logging"):
            cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
        elif hasattr(cv2, "setLogLevel"):
            quiet = getattr(cv2, "LOG_LEVEL_SILENT", None) or getattr(cv2, "LOG_LEVEL_ERROR", 1)
            cv2.setLogLevel(quiet)
    except Exception:
        pass


@contextmanager
def _quiet_ffmpeg_stdio():
    """Chặn stderr tiến trình (log FFmpeg/OpenCV) trong lúc seek/probe — không nuốt logger Python sau khối này."""
    saved = None
    try:
        try:
            devnull = os.open(os.devnull, os.O_WRONLY)
            saved = os.dup(2)
            os.dup2(devnull, 2)
            os.close(devnull)
        except Exception:
            saved = None
        yield
    finally:
        if saved is not None:
            try:
                os.dup2(saved, 2)
                os.close(saved)
            except Exception:
                pass


def _run_ffmpeg_quiet(cmd: list, check: bool = True):
    kwargs = dict(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if CREATE_NO_WINDOW:
        kwargs["creationflags"] = CREATE_NO_WINDOW
    return subprocess.run(cmd, check=check, **kwargs)


def _remux_faststart(src: str) -> str:
    """
    Một lần `ffmpeg -c copy -movflags +faststart` rồi mới score.
    Không đổi khung hình → lịch clip (Timesub) giữ nguyên.
    """
    src = os.path.abspath(src)
    parent = os.path.dirname(src)
    stem, ext = os.path.splitext(os.path.basename(src))
    dst = os.path.join(parent, f"{stem}.timesub_probe{ext or '.mp4'}")
    try:
        if (
            os.path.isfile(dst)
            and os.path.getsize(dst) > 1024
            and os.path.getmtime(dst) >= os.path.getmtime(src)
        ):
            return dst
    except OSError:
        pass
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", src,
        "-c", "copy", "-map", "0:v:0",
        "-movflags", "+faststart",
        dst,
    ]
    try:
        _run_ffmpeg_quiet(cmd, check=True)
        if os.path.isfile(dst) and os.path.getsize(dst) > 1024:
            logger.info("📦 [TIMESUB] Đã remux +faststart (1 lần) để seek không spam 'partial file'.")
            return dst
    except Exception as e:
        logger.warning(f"⚠️ [TIMESUB] Remux faststart thất bại ({e}) — chấm trên file gốc, log đã tắt.")
        try:
            if os.path.isfile(dst):
                os.remove(dst)
        except OSError:
            pass
    return src


def _low_file_too_small(path: str, hint_duration: float = 0.0) -> bool:
    """Stub YouTube (format 18 ~572KB) không đủ duration để seek."""
    try:
        from downloader_processor import DownloaderProcessor
        return not DownloaderProcessor.low_res_is_usable(path, hint_duration)
    except Exception:
        try:
            return os.path.getsize(path) < 2 * 1024 * 1024
        except OSError:
            return True


def _resolve_probe_video(source_video: str) -> str:
    """
    Chấm hình chỉ trên source_video_low.mp4 (nhẹ, ít seek lỗi).
    File stub/rác → bỏ visual (log rõ), không remux junk.
    """
    if not source_video:
        return ""
    src = os.path.abspath(source_video)
    parent = os.path.dirname(src)
    low = os.path.join(parent, "source_video_low.mp4")
    base = os.path.basename(src).lower()

    probe = ""
    if base == "source_video_low.mp4" and os.path.isfile(src):
        probe = src
    elif os.path.isfile(low):
        if os.path.normcase(src) != os.path.normcase(os.path.abspath(low)):
            logger.info("🔎 [TIMESUB] Chấm hình trên source_video_low.mp4 — không probe source high.")
        probe = os.path.abspath(low)
    elif os.path.isfile(src):
        logger.info("🔎 [TIMESUB] Không có source_video_low.mp4 — remux source high +faststart 1 lần rồi mới score.")
        return _remux_faststart(src)
    else:
        return ""

    if probe and os.path.isfile(probe):
        hint_dur = 0.0
        high = os.path.join(parent, "source_video.mp4")
        try:
            from downloader_processor import DownloaderProcessor
            if os.path.isfile(high):
                hint_dur = DownloaderProcessor.probe_duration_sec(high)
        except Exception:
            hint_dur = 0.0
        if _low_file_too_small(probe, hint_dur):
            size = os.path.getsize(probe)
            logger.warning(
                f"⚠️ [TIMESUB] source_video_low.mp4 quá nhỏ/hỏng ({size} bytes, có thể là stub format 18) "
                "— bỏ chấm hình, chỉ dùng điểm nghĩa."
            )
            return ""

    # Low từ yt-dlp thường chưa faststart → remux copy 1 lần, cùng khung hình.
    return _remux_faststart(probe)


def probe_visual_stability(video_path: str, start: float, end: float, cap=None) -> dict:
    """
    Đếm cut (absdiff + nhảy histogram) trên đoạn start–end.
    Ổn định cao = ít cut. rejected=True nếu quá nhiều chuyển cảnh.
    """
    import cv2
    import numpy as np

    duration = max(0.01, float(end) - float(start))
    max_cuts = _max_allowed_cuts(duration)
    empty = {"cuts": 0, "stability": 1.0, "rejected": False, "duration": duration}

    own_cap = cap is None
    if own_cap:
        if not video_path or not os.path.exists(video_path):
            return empty
        _configure_opencv_ffmpeg_quiet()
        with _quiet_ffmpeg_stdio():
            cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning(f"⚠️ [TIMESUB] Không mở được video chấm hình: {video_path}")
            return empty
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        sample_step = max(1, int(round(fps / 4.0)))  # ~4 khung/giây
        start_f = max(0, int(start * fps))
        end_f = min(total_frames, int(end * fps)) if total_frames else int(end * fps)
        prev_gray = None
        prev_hist = None
        cuts = 0
        # Một seek/cửa sổ rồi đọc tuần tự — cùng chỉ số khung ~4fps, tránh hàng trăm -ss trên MP4.
        with _quiet_ffmpeg_stdio():
            cap.set(cv2.CAP_PROP_POS_FRAMES, start_f)
            f_idx = start_f
            next_sample = start_f
            end_limit = max(start_f + 1, end_f)
            while f_idx < end_limit:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break
                if f_idx == next_sample:
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = cv2.resize(gray, (160, 90))
                    hist = cv2.calcHist([gray], [0], None, [32], [0, 256])
                    hist = cv2.normalize(hist, hist).flatten()
                    if prev_gray is not None:
                        diff = float(np.mean(cv2.absdiff(gray, prev_gray)))
                        corr = float(cv2.compareHist(
                            prev_hist.reshape(-1, 1), hist.reshape(-1, 1), cv2.HISTCMP_CORREL
                        ))
                        if diff >= ABS_DIFF_CUT or corr < HIST_CORR_CUT:
                            cuts += 1
                    prev_gray = gray
                    prev_hist = hist
                    next_sample += sample_step
                f_idx += 1
    except Exception as e:
        logger.warning(f"⚠️ [TIMESUB] Lỗi probe hình [{start:.1f}–{end:.1f}]: {e}")
        if own_cap:
            cap.release()
        return empty
    if own_cap:
        cap.release()

    rejected = cuts > max_cuts
    stability = 1.0 / (1.0 + float(cuts))
    return {
        "cuts": int(cuts),
        "stability": float(stability),
        "rejected": bool(rejected),
        "duration": duration,
        "max_cuts": max_cuts,
    }


def _build_windows(cues: list, safe_start: float, safe_end: float) -> list:
    """Mỗi cue làm mốc: ghép cue kế tiếp đến ≤6s, clamp quanh điểm khớp."""
    windows = []
    n = len(cues)
    for i, cue in enumerate(cues):
        if cue["end"] <= safe_start or cue["start"] >= safe_end:
            continue
        texts = [cue.get("text") or ""]
        w_end = cue["end"]
        j = i + 1
        while j < n and (cues[j]["end"] - cue["start"]) <= MAX_CLIP_SEC + 0.05:
            texts.append(cues[j].get("text") or "")
            w_end = cues[j]["end"]
            j += 1
        start, end = clamp_window(
            cue["start"], w_end, match_t=cue["start"],
            max_dur=MAX_CLIP_SEC, min_dur=MIN_CLIP_SEC,
            safe_start=safe_start, safe_end=safe_end,
        )
        if end - start < 0.4:
            continue
        windows.append({
            "start": start,
            "end": end,
            "text": " ".join(t for t in texts if t).strip(),
            "match_t": cue["start"],
            "seed": i,
        })
    return windows


def _overlaps(a: dict, b: dict, gap: float = OVERLAP_GAP) -> bool:
    return not (a["end"] + gap <= b["start"] or b["end"] + gap <= a["start"])


def _fill_required(ranked: list, required_duration: float) -> list:
    """Chọn không chồng lấn, đủ thời lượng; thiếu thì lặp lại như OpenCV."""
    chosen = []
    used_idx = []
    accumulated = 0.0

    for i, clip in enumerate(ranked):
        if accumulated >= required_duration:
            break
        if any(_overlaps(clip, c) for c in chosen):
            continue
        dur = clip["end"] - clip["start"]
        if accumulated + dur > required_duration + 0.05:
            end_t = clip["start"] + max(MIN_CLIP_SEC, required_duration - accumulated)
            end_t = min(end_t, clip["end"])
            if end_t - clip["start"] < 0.4:
                continue
            chosen.append({"start": clip["start"], "end": end_t})
            used_idx.append(i)
            accumulated += end_t - clip["start"]
        else:
            chosen.append({"start": clip["start"], "end": clip["end"]})
            used_idx.append(i)
            accumulated += dur

    idx = 0
    pool = chosen if chosen else [{"start": c["start"], "end": c["end"]} for c in ranked[:1]]
    while accumulated < required_duration and pool:
        extra = pool[idx % len(pool)]
        dur = extra["end"] - extra["start"]
        if dur <= 0:
            break
        if accumulated + dur > required_duration:
            end_t = extra["start"] + (required_duration - accumulated)
            chosen.append({"start": extra["start"], "end": end_t})
            accumulated += end_t - extra["start"]
            break
        chosen.append({"start": extra["start"], "end": extra["end"]})
        accumulated += dur
        idx += 1
        if idx > 400:
            break
    return [{"start": c["start"], "end": c["end"]} for c in chosen]


def score_and_map_clips_semantic(
    source_video: str,
    srt_path: str,
    script_path: str,
    safe_start: float,
    safe_end: float,
    required_duration: float,
    script_text: str = None,
) -> list:
    """
    Trả về [{start, end}, …] cùng format OpenCV.
    Thiếu SRT/script → [] để caller fallback.
    """
    if not srt_path or not os.path.exists(srt_path):
        logger.warning("⚠️ [TIMESUB] Thiếu transcript_sub.srt — không chạy được chế độ sát nghĩa.")
        return []

    if script_text is None:
        if not script_path or not os.path.exists(script_path):
            logger.warning("⚠️ [TIMESUB] Thiếu summary_output.txt — không có kịch bản TTS để khớp.")
            return []
        try:
            with open(script_path, "r", encoding="utf-8") as f:
                script_text = f.read()
        except Exception as e:
            logger.warning(f"⚠️ [TIMESUB] Không đọc được script: {e}")
            return []

    cues = parse_srt(srt_path)
    chunks = chunk_script(script_text)
    logger.info(f"📄 [TIMESUB] SRT: {len(cues)} cue | script TTS: {len(chunks)} đoạn.")
    if not cues or not chunks:
        logger.warning("⚠️ [TIMESUB] SRT hoặc script rỗng.")
        return []

    windows = _build_windows(cues, safe_start, safe_end)
    if not windows:
        logger.warning("⚠️ [TIMESUB] Không dựng được cửa sổ cue trong vùng an toàn.")
        return []

    sem = semantic_scores(chunks, [w["text"] for w in windows])
    for w, s in zip(windows, sem):
        w["semantic"] = float(s)

    windows.sort(key=lambda w: w["semantic"], reverse=True)
    probe_n = min(len(windows), TOP_VISUAL_PROBE)
    to_probe = windows[:probe_n]

    probe_video = _resolve_probe_video(source_video) if source_video else ""
    has_probe_file = bool(probe_video and os.path.exists(probe_video))
    logger.info(
        f"🔎 [TIMESUB] Chấm hình {probe_n}/{len(windows)} ứng viên "
        f"(video: {'có — ' + os.path.basename(probe_video) if has_probe_file else 'không — bỏ qua cut'})."
    )

    import gc

    _configure_opencv_ffmpeg_quiet()
    cap = None
    has_video = has_probe_file
    if has_video:
        import cv2
        with _quiet_ffmpeg_stdio():
            cap = cv2.VideoCapture(probe_video)
            opened = bool(cap is not None and cap.isOpened())
        if not opened:
            logger.warning("⚠️ [TIMESUB] Mở video thất bại — không lọc cut, chỉ dùng điểm nghĩa.")
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
            cap = None
            has_video = False

    ranked = []
    n_reject_cut = 0
    n_clamp = 0
    try:
        order = sorted(range(len(to_probe)), key=lambda k: to_probe[k]["start"])
        for k in order:
            w = to_probe[k]
            raw_dur = w["end"] - w["start"]
            if raw_dur > MAX_CLIP_SEC + 0.05:
                w["start"], w["end"] = clamp_window(
                    w["start"], w["end"], match_t=w.get("match_t", w["start"]),
                    safe_start=safe_start, safe_end=safe_end,
                )
                n_clamp += 1
            vis = probe_visual_stability(probe_video, w["start"], w["end"], cap=cap) if has_video else {
                "cuts": 0, "stability": 1.0, "rejected": False, "duration": w["end"] - w["start"],
            }
            if vis.get("rejected"):
                n_reject_cut += 1
                continue
            if (w["end"] - w["start"]) > MAX_CLIP_SEC + 0.05:
                n_reject_cut += 1
                continue
            w["cuts"] = vis.get("cuts", 0)
            w["stability"] = float(vis.get("stability", 1.0))
            w["score"] = float(w["semantic"]) * float(w["stability"])
            ranked.append(w)
    finally:
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass
            try:
                import cv2
                cv2.destroyAllWindows()
            except Exception:
                pass
        gc.collect()

    if n_clamp:
        logger.info(f"✂️ [TIMESUB] Đã cắt {n_clamp} cửa sổ dài >{MAX_CLIP_SEC:.0f}s về ≤{MAX_CLIP_SEC:.0f}s.")
    logger.info(
        f"🎞️ [TIMESUB] Giữ {len(ranked)} đoạn hình ổn định, loại {n_reject_cut} vì nhảy scene / quá dài."
    )
    if not ranked:
        return []

    ranked.sort(key=lambda w: w["score"], reverse=True)
    for w in ranked[:8]:
        logger.info(
            f"   • [{w['start']:.1f}–{w['end']:.1f}]s dur={w['end']-w['start']:.1f}s "
            f"nghĩa={w['semantic']:.3f} cut={w.get('cuts', 0)} ổn={w['stability']:.2f} tổng={w['score']:.3f}"
        )

    chosen = _fill_required(ranked, float(required_duration))
    total = sum(c["end"] - c["start"] for c in chosen)
    logger.info(
        f"✅ [TIMESUB] Lịch B-roll: {len(chosen)} clip, {total:.1f}s / cần {required_duration:.1f}s "
        f"(anti-overlap, ≤{MAX_CLIP_SEC:.0f}s)."
    )
    return chosen
