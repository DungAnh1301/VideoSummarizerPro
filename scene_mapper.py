"""Bản đồ ranh giới cảnh dùng chung cho mọi chế độ chọn B-roll.

Chỉ phân tích source low-res. Kết quả là timestamp, không tạo/encode clip.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import time
from pathlib import Path

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("SceneMapper")


MAP_VERSION = 3
DEFAULT_MAX_BROLL_SEC = 10.0
# Giữ tên cũ để các module/plugin ngoài dự án không bị lỗi import.
MAX_BROLL_SEC = DEFAULT_MAX_BROLL_SEC
MIN_BROLL_SEC = 2.5
SAFE_MARGIN_SEC = 0.35


def _fingerprint(path: str) -> str:
    stat = os.stat(path)
    h = hashlib.sha256()
    h.update(os.path.abspath(path).lower().encode("utf-8", errors="replace"))
    h.update(f"|{stat.st_size}|{stat.st_mtime_ns}".encode("ascii"))
    with open(path, "rb") as stream:
        h.update(stream.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            stream.seek(max(0, stat.st_size - 1024 * 1024))
            h.update(stream.read(1024 * 1024))
    return h.hexdigest()


def _cache_path(source_path: str, fingerprint: str, target_limit: float = DEFAULT_MAX_BROLL_SEC) -> str:
    project = Path(__file__).resolve().parent
    folder = project / "cache" / "scene_maps"
    folder.mkdir(parents=True, exist_ok=True)
    lim_tag = f"_lim{int(round(float(target_limit)))}"
    return str(folder / f"{fingerprint[:24]}{lim_tag}.json")


def _detect_with_pyscenedetect(video_path: str) -> tuple[list, float, float]:
    from scenedetect import SceneManager, open_video
    from scenedetect.detectors import AdaptiveDetector

    video = open_video(video_path)
    fps = float(video.frame_rate or 30.0)
    manager = SceneManager()
    manager.auto_downscale = True
    manager.add_detector(AdaptiveDetector(
        adaptive_threshold=3.0,
        min_scene_len=max(1, int(round(fps * 0.5))),
        window_width=2,
        min_content_val=15.0,
    ))
    manager.detect_scenes(video=video, show_progress=False, frame_skip=0)
    pairs = manager.get_scene_list(start_in_scene=True)
    scenes = [(float(start.get_seconds()), float(end.get_seconds())) for start, end in pairs]
    duration = float(video.duration.get_seconds()) if video.duration else (scenes[-1][1] if scenes else 0.0)
    return scenes, duration, fps


def _detect_fallback(video_path: str) -> tuple[list, float, float]:
    """Fallback nhẹ khi máy cũ chưa có PySceneDetect; vẫn quét tuần tự một lần."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video low-res: {video_path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total / fps if total else 0.0
    cuts = [0.0]
    previous = None
    scores = []
    frame_index = 0
    min_gap_frames = max(1, int(round(fps * 0.5)))
    last_cut = -min_gap_frames
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        lab = cv2.cvtColor(small, cv2.COLOR_BGR2LAB)
        if previous is not None:
            score = float(np.mean(cv2.absdiff(lab, previous)))
            scores.append(score)
            recent = scores[-25:]
            baseline = float(np.median(recent)) if recent else 0.0
            adaptive_limit = max(18.0, baseline * 3.0)
            if score >= adaptive_limit and frame_index - last_cut >= min_gap_frames:
                cuts.append(frame_index / fps)
                last_cut = frame_index
        previous = lab
        frame_index += 1
    cap.release()
    end_time = frame_index / fps if frame_index else duration
    cuts.append(max(end_time, duration))
    scenes = [(cuts[i], cuts[i + 1]) for i in range(len(cuts) - 1) if cuts[i + 1] > cuts[i]]
    return scenes, max(end_time, duration), fps


def cluster_contiguous_scenes(
    raw_scenes: list,
    target_limit: float = 10.0,
    safe_margin: float = 0.35,
    duration: float = 0.0,
) -> list[dict]:
    """
    Gom các cảnh con liền kề thành Cụm Cảnh Vĩ Mô (Macro-Scenes):
    - target_limit: lấy từ GUI (mặc định 10.0s).
    - Dải thời lượng vàng: min_dur = max(2.5, target_limit * 0.5), max_dur = max(5.0, target_limit * 1.5).
    - Giữ nguyên vẹn 100% dòng chảy tự nhiên giữa các cảnh con (KHÔNG gọt 0.35s tại khớp nối bên trong).
    - Chỉ áp dụng safe_margin (0.35s) ở đầu cảnh đầu tiên và đuôi cảnh cuối cùng của cụm.
    """
    if not raw_scenes:
        return []

    target_limit = max(2.0, float(target_limit or 10.0))
    min_dur = max(2.5, target_limit * 0.5)
    max_dur = max(5.0, target_limit * 1.5)

    # 1. Chuẩn hóa danh sách raw_scenes
    cleaned_shots = []
    for s, e in raw_scenes:
        s_val = max(0.0, float(s))
        e_val = min(float(duration), float(e)) if duration > 0 else float(e)
        if e_val > s_val + 0.05:
            cleaned_shots.append((s_val, e_val))

    if not cleaned_shots:
        return []

    # 2. Gom cụm các shot liền kề
    clusters = []
    current_shots = [cleaned_shots[0]]

    for next_shot in cleaned_shots[1:]:
        curr_start = current_shots[0][0]
        curr_end = current_shots[-1][1]
        curr_len = curr_end - curr_start

        next_start, next_end = next_shot

        # Kiểm tra tính liền kề (contiguous)
        is_contiguous = abs(next_start - curr_end) <= 0.15

        if is_contiguous:
            combined_len = next_end - curr_start
            # Điều kiện gộp:
            # - Cụm hiện tại chưa đủ min_dur và gộp vào không vượt quá max_dur
            # - HOẶC cụm hiện tại chưa đạt target_limit và gộp tiếp vẫn <= max_dur và giúp tiệm cận target_limit hơn
            if curr_len < min_dur and combined_len <= max_dur:
                current_shots.append(next_shot)
            elif curr_len < target_limit and combined_len <= target_limit * 1.2:
                current_shots.append(next_shot)
            elif curr_len < target_limit and combined_len <= max_dur and abs(combined_len - target_limit) < abs(curr_len - target_limit):
                current_shots.append(next_shot)
            else:
                clusters.append(current_shots)
                current_shots = [next_shot]
        else:
            clusters.append(current_shots)
            current_shots = [next_shot]

    if current_shots:
        clusters.append(current_shots)

    # Nếu cụm cuối cùng quá ngắn (< min_dur) và liền kề với cụm trước, ghép vào cụm trước nếu không vượt max_dur * 1.25
    if len(clusters) > 1:
        last_c = clusters[-1]
        prev_c = clusters[-2]
        last_len = last_c[-1][1] - last_c[0][0]
        prev_len = prev_c[-1][1] - prev_c[0][0]
        is_contig = abs(last_c[0][0] - prev_c[-1][1]) <= 0.15
        if last_len < min_dur and is_contig and (prev_len + last_len) <= max_dur * 1.25:
            prev_c.extend(last_c)
            clusters.pop()

    # 3. Tạo danh sách scenes hoàn chỉnh với safe_start/safe_end CHỈ Ở 2 ĐẦU CỤM
    final_scenes = []
    for idx, c_shots in enumerate(clusters, 1):
        c_start = c_shots[0][0]
        c_end = c_shots[-1][1]
        c_dur = c_end - c_start

        # Áp dụng safe margin 0.35s CHỈ TẠI BIÊN CỤM NGOÀI CÙNG
        safe_start = c_start + (safe_margin if c_start > 0.001 else 0.0)
        safe_end = c_end - (safe_margin if (duration <= 0 or c_end < duration - 0.001) else 0.0)
        if safe_end < safe_start + 0.1:
            safe_start = c_start
            safe_end = c_end

        usable = max(0.0, safe_end - safe_start)
        final_scenes.append({
            "id": idx,
            "start": round(c_start, 6),
            "end": round(c_end, 6),
            "duration": round(c_dur, 6),
            "safe_start": round(safe_start, 6),
            "safe_end": round(safe_end, 6),
            "usable_duration": round(usable, 6),
            "shot_count": len(c_shots),
            "sub_shots": [(round(s, 6), round(e, 6)) for s, e in c_shots],
        })

    return final_scenes


def build_scene_map(low_video_path: str, force: bool = False,
                    target_limit: float = DEFAULT_MAX_BROLL_SEC) -> dict:
    if not low_video_path or not os.path.isfile(low_video_path):
        raise FileNotFoundError("Thiếu source_video_low.mp4 để tạo bản đồ cảnh.")
    target_limit = max(2.0, float(target_limit or DEFAULT_MAX_BROLL_SEC))
    fingerprint = _fingerprint(low_video_path)
    cache_path = _cache_path(low_video_path, fingerprint, target_limit=target_limit)
    if not force and os.path.isfile(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as stream:
                cached = json.load(stream)
            if (
                cached.get("version") == MAP_VERSION
                and cached.get("source_fingerprint") == fingerprint
                and abs(float(cached.get("target_limit", 0.0)) - target_limit) < 0.1
            ):
                cached_detector = str(cached.get("detector") or "")
                pyscene_ready = importlib.util.find_spec("scenedetect") is not None
                if cached_detector == "pyscenedetect-adaptive" or not pyscene_ready:
                    logger.info(
                        "⏭️ [SCENE MAP] Đã có bản đồ hợp lệ (v%s, target=%.1fs) — bỏ qua quét lại.",
                        MAP_VERSION, target_limit,
                    )
                    return cached
                logger.info(
                    "🔄 [SCENE MAP] Cache cũ dùng fallback, nay PySceneDetect đã sẵn sàng — quét lại AdaptiveDetector."
                )
        except Exception:
            pass

    started = time.time()
    detector = "pyscenedetect-adaptive"
    try:
        raw_scenes, duration, fps = _detect_with_pyscenedetect(low_video_path)
    except Exception as exc:
        detector = "adaptive-opencv-fallback"
        logger.warning("⚠️ [SCENE MAP] PySceneDetect chưa dùng được (%s) — dùng fallback tuần tự.", exc)
        raw_scenes, duration, fps = _detect_fallback(low_video_path)

    scenes = cluster_contiguous_scenes(
        raw_scenes,
        target_limit=target_limit,
        safe_margin=SAFE_MARGIN_SEC,
        duration=duration,
    )

    payload = {
        "version": MAP_VERSION,
        "source_fingerprint": fingerprint,
        "source_path": os.path.abspath(low_video_path),
        "source_duration": round(float(duration), 6),
        "fps": round(float(fps), 6),
        "detector": detector,
        "safe_margin": SAFE_MARGIN_SEC,
        "target_limit": round(target_limit, 2),
        "default_max_broll_seconds": round(target_limit, 2),
        "created_at": time.time(),
        "scan_seconds": round(time.time() - started, 3),
        "raw_scene_count": len(raw_scenes),
        "scenes": scenes,
    }
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)
    logger.info(
        "🗺️ [SCENE MAP] %d cảnh thô → %d cụm cảnh vĩ mô (target=%.1fs) | %s | quét %.1fs | giữ nguyên 100%% cắt cảnh nội bộ.",
        len(raw_scenes), len(scenes), target_limit, detector, payload["scan_seconds"],
    )
    return payload


def seed_part_scene_map(master_map: dict, part_low_path: str,
                        part_start: float, part_end: float,
                        target_limit: float = DEFAULT_MAX_BROLL_SEC) -> dict:
    """Reuse one master scene scan for a timestamp range cut into a Part proxy."""
    fingerprint = _fingerprint(part_low_path)
    duration = max(0.0, float(part_end) - float(part_start))
    target_limit = float((master_map or {}).get("target_limit", target_limit or DEFAULT_MAX_BROLL_SEC))
    scenes = []
    for source in (master_map or {}).get("scenes", []):
        absolute_start = max(float(part_start), float(source.get("start", 0.0)))
        absolute_end = min(float(part_end), float(source.get("end", 0.0)))
        if absolute_end <= absolute_start:
            continue
        start = absolute_start - float(part_start)
        end = absolute_end - float(part_start)
        safe_start = max(start, float(source.get("safe_start", absolute_start)) - float(part_start))
        safe_end = min(end, float(source.get("safe_end", absolute_end)) - float(part_start))
        scenes.append({
            "id": len(scenes) + 1,
            "start": round(start, 6), "end": round(end, 6),
            "duration": round(end - start, 6),
            "safe_start": round(max(0.0, safe_start), 6),
            "safe_end": round(min(duration, safe_end), 6),
            "usable_duration": round(max(0.0, safe_end - safe_start), 6),
        })
    payload = {
        "version": MAP_VERSION,
        "source_fingerprint": fingerprint,
        "source_path": os.path.abspath(part_low_path),
        "source_duration": round(duration, 6),
        "fps": float((master_map or {}).get("fps") or 0.0),
        "detector": str((master_map or {}).get("detector") or "master-part-seed"),
        "safe_margin": SAFE_MARGIN_SEC,
        "target_limit": round(target_limit, 2),
        "default_max_broll_seconds": round(target_limit, 2),
        "created_at": time.time(), "scan_seconds": 0.0,
        "derived_from_master": True,
        "part_start": round(float(part_start), 6),
        "part_end": round(float(part_end), 6),
        "scenes": scenes,
    }
    cache_path = _cache_path(part_low_path, fingerprint, target_limit=target_limit)
    tmp = cache_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    os.replace(tmp, cache_path)
    logger.info("♻️ [SCENE MAP PART] Tái sử dụng %d ranh giới từ bản đồ nguồn.", len(scenes))
    return payload


def usable_scenes(scene_map: dict, safe_start: float, safe_end: float, min_duration: float = None) -> list:
    minimum = MIN_BROLL_SEC if min_duration is None else max(0.05, float(min_duration))
    result = []
    for scene in scene_map.get("scenes", []):
        start = max(float(scene.get("safe_start", 0)), float(safe_start))
        end = min(float(scene.get("safe_end", 0)), float(safe_end))
        if end - start < minimum:
            continue
        item = dict(scene)
        item["usable_start"] = start
        item["usable_end"] = end
        item["usable_duration"] = end - start
        result.append(item)
    return result


def exclude_unusable_visual_ranges(items: list, ranges: list,
                                   minimum_duration: float = 0.35) -> list:
    """Cắt bỏ tuyệt đối các timestamp Gemini đánh dấu bị overlay/che hình."""
    blocked = []
    for value in ranges or []:
        if not isinstance(value, dict):
            continue
        try:
            start = float(value.get("start_sec", value.get("start", 0.0)))
            end = float(value.get("end_sec", value.get("end", start)))
        except (TypeError, ValueError):
            continue
        if end > start:
            blocked.append((start, end))
    if not blocked:
        return [dict(item) for item in items or []]
    output = []
    for source in items or []:
        start_key = "usable_start" if "usable_start" in source else "start"
        end_key = "usable_end" if "usable_end" in source else "end"
        start, end = float(source.get(start_key, 0.0)), float(source.get(end_key, 0.0))
        pieces = [(start, end)]
        for deny_start, deny_end in blocked:
            next_pieces = []
            for lo, hi in pieces:
                if deny_end <= lo or deny_start >= hi:
                    next_pieces.append((lo, hi))
                else:
                    if deny_start - lo >= minimum_duration:
                        next_pieces.append((lo, min(hi, deny_start)))
                    if hi - deny_end >= minimum_duration:
                        next_pieces.append((max(lo, deny_end), hi))
            pieces = next_pieces
        for lo, hi in pieces:
            if hi - lo < minimum_duration:
                continue
            item = dict(source)
            item[start_key], item[end_key] = round(lo, 6), round(hi, 6)
            if start_key == "usable_start":
                item["usable_duration"] = round(hi - lo, 6)
            else:
                item["duration"] = round(hi - lo, 6)
                item["scene_safe_start"], item["scene_safe_end"] = round(lo, 6), round(hi, 6)
            output.append(item)
    return output


def scene_window(scene: dict, anchor: float | None = None, max_duration: float | None = None) -> dict:
    """Cảnh dài lấy tối đa max_duration; cảnh ngắn lấy trọn vùng an toàn."""
    lo = float(scene["usable_start"])
    hi = float(scene["usable_end"])
    limit = DEFAULT_MAX_BROLL_SEC if max_duration is None else float(max_duration)
    duration = min(max(0.1, limit), hi - lo)
    if anchor is None:
        start = lo + max(0.0, (hi - lo - duration) / 2.0)
    else:
        start = max(lo, min(float(anchor) - duration / 2.0, hi - duration))
    return {
        "scene_id": scene.get("id"),
        "start": round(start, 6),
        "end": round(start + duration, 6),
        "duration": round(duration, 6),
        "scene_safe_start": round(lo, 6),
        "scene_safe_end": round(hi, 6),
        "configured_max_duration": round(limit, 6),
    }


def expand_window_safely(clip: dict, extra_needed: float, occupied: list | None = None,
                         max_extension_ratio: float = 0.50) -> dict:
    """Nới một B-roll trong shot hiện tại, không vượt cut và không đè clip khác."""
    result = dict(clip)
    need = max(0.0, float(extra_needed))
    if need <= 0.001:
        return result
    start, end = float(result.get("start", 0)), float(result.get("end", 0))
    configured = max(0.1, float(result.get("configured_max_duration") or (end - start)))
    previous_extension = max(0.0, float(result.get("expanded_before", 0))) + max(
        0.0, float(result.get("expanded_after", 0))
    )
    extension_cap = configured * max(0.0, float(max_extension_ratio))
    allowance = min(need, max(0.0, extension_cap - previous_extension))
    safe_start = float(result.get("scene_safe_start", start))
    safe_end = float(result.get("scene_safe_end", end))
    # Các cửa sổ khác trong cùng source có thể nằm sát nhau. Không nới đè lên
    # timeframe đã thực sự đưa vào timeline.
    for other in occupied or []:
        other_start, other_end = float(other.get("start", 0)), float(other.get("end", 0))
        if other_end <= start + 0.001:
            safe_start = max(safe_start, other_end)
        elif other_start >= end - 0.001:
            safe_end = min(safe_end, other_start)
        elif other_start < end and other_end > start:
            return result
    # Chọn phía có nhiều khoảng trống trong cùng shot nhất, tức phía
    # xa điểm cut/clip kề bên hơn; không mặc định nới về sau.
    before_room = max(0.0, start - safe_start)
    after_room = max(0.0, safe_end - end)
    grow_before = grow_after = 0.0
    if before_room > after_room:
        grow_before = min(allowance, before_room)
        allowance -= grow_before
        grow_after = min(allowance, after_room)
    else:
        grow_after = min(allowance, after_room)
        allowance -= grow_after
        grow_before = min(allowance, before_room)
    start -= grow_before
    end += grow_after
    result["start"] = round(start, 6)
    result["end"] = round(end, 6)
    result["duration"] = round(end - start, 6)
    result["expanded_before"] = round(float(result.get("expanded_before", 0)) + grow_before, 6)
    result["expanded_after"] = round(float(result.get("expanded_after", 0)) + grow_after, 6)
    return result


def expand_timeline_shortage(clips: list, shortage: float,
                             occupied: list | None = None) -> list:
    """Chỉ nới sau khi kho cảnh gốc/dự phòng đã hết mà vẫn thiếu."""
    result = [dict(clip) for clip in clips]
    remaining = max(0.0, float(shortage))
    fixed = [dict(clip) for clip in (occupied or [])]
    while remaining > 0.001:
        best_index, best_clip, best_gain = None, None, 0.0
        for index, clip in enumerate(result):
            blockers = fixed + [other for pos, other in enumerate(result) if pos != index]
            expanded = expand_window_safely(clip, remaining, blockers)
            gain = (float(expanded.get("end", 0)) - float(expanded.get("start", 0))) - (
                float(clip.get("end", 0)) - float(clip.get("start", 0))
            )
            if gain > best_gain + 0.001:
                best_index, best_clip, best_gain = index, expanded, gain
        if best_index is None:
            break
        result[best_index] = best_clip
        remaining -= best_gain
    return result


def expand_if_gap_is_small(clip: dict, required_for_clip: float,
                           occupied: list | None = None) -> dict:
    """Nới đúng phần thiếu khi gap không quá 50% cấu hình cảnh."""
    available = max(0.0, float(clip.get("end", 0)) - float(clip.get("start", 0)))
    gap = max(0.0, float(required_for_clip) - available)
    configured = max(0.1, float(clip.get("configured_max_duration") or available))
    if 0.001 < gap <= configured * 0.50 + 0.001:
        return expand_window_safely(clip, gap, occupied=occupied)
    return dict(clip)


def decompose_scene_into_windows(lo: float, hi: float, limit: float) -> list[tuple[float, float, str]]:
    """Phân rã một shot quay/cụm cảnh [lo, hi] theo trần GUI (limit) và mốc sàn 50% min:
    1. Cụm cảnh nằm trong dải thời lượng vàng (<= limit * 1.5): Giữ nguyên 100% trọn vẹn cả cụm (full_cluster).
    2. Cảnh dài hơn đáng kể (> limit * 1.5, ví dụ 20s, 25s, 40s):
       - Nếu phần dư >= 50% limit: Lấy các block đủ limit và block cuối lấy phần dư (ví dụ 10s + 10s + 5s).
       - Nếu phần dư < 50% limit: Center crop bỏ đều 2 đầu (trim = rem / 2) để chia thành các block đủ limit.
    """
    scene_dur = max(0.0, hi - lo)
    if scene_dur < 0.35:
        return []
    limit = max(1.0, float(limit))
    min_duration = max(1.0, limit * 0.5)

    # 1. Nếu cụm cảnh nằm trong dải thời lượng vàng (<= limit * 1.5): Giữ nguyên 100% trọn vẹn
    if scene_dur <= limit * 1.5 + 0.001:
        return [(round(lo, 6), round(hi, 6), "full_cluster")]

    # 2. Tính số block limit nguyên vẹn
    num_full = int(scene_dur // limit)
    rem = scene_dur - (num_full * limit)

    if rem < 0.05:
        # Chia hết trọn vẹn (ví dụ 20s / 10s = 2 block 10s)
        windows = []
        for i in range(num_full):
            w_st = lo + i * limit
            w_en = lo + (i + 1) * limit
            windows.append((round(w_st, 6), round(w_en, 6), f"part_{chr(97 + i)}"))
        return windows

    if rem >= min_duration:
        # Phần dư >= min_duration (50% limit) (ví dụ 15s = 10s + 5s)
        windows = []
        for i in range(num_full):
            w_st = lo + i * limit
            w_en = lo + (i + 1) * limit
            windows.append((round(w_st, 6), round(w_en, 6), f"part_{chr(97 + i)}"))
        # Block cuối lấy phần dư >= 50%
        windows.append((round(lo + num_full * limit, 6), round(hi, 6), f"part_{chr(97 + num_full)}"))
        return windows

    # Phần dư < min_duration (ví dụ 13s khi limit=10s, rem=3s < 5s; hoặc 23s, rem=3s)
    # Áp dụng Center Crop: bỏ đều rem/2 ở 2 đầu để các block còn lại đạt đúng chuẩn limit
    trim = rem / 2.0
    start_base = lo + trim
    windows = []
    for i in range(num_full):
        w_st = start_base + i * limit
        w_en = w_st + limit
        tag = "center_crop" if num_full == 1 else f"center_part_{chr(97 + i)}"
        windows.append((round(w_st, 6), round(w_en, 6), tag))
    return windows


def rank_action_windows(low_video_path: str, scenes: list, max_duration: float | None = None) -> list:
    """Chấm hành động/chất lượng trên scene đã sạch theo ranh giới tự nhiên và Center Crop; không băm vụn 3.5s."""
    import cv2
    import numpy as np

    if not scenes:
        return []
    cap = cv2.VideoCapture(low_video_path)
    if not cap.isOpened():
        return [scene_window(scene, max_duration=max_duration) for scene in scenes]
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    try:
        from hardware_manager import get_hardware_profile
        samples_per_second = max(1, int(get_hardware_profile().get("scene_samples_per_second", 2)))
    except Exception:
        samples_per_second = 2
    sample_step = max(1, int(round(fps / samples_per_second)))
    metrics = {int(scene["id"]): [] for scene in scenes}
    previous = {int(scene["id"]): None for scene in scenes}
    scene_index = 0
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break
        if frame_index % sample_step:
            frame_index += 1
            continue
        timestamp = frame_index / fps
        while scene_index < len(scenes) and timestamp > scenes[scene_index]["usable_end"]:
            scene_index += 1
        if scene_index >= len(scenes):
            break
        scene = scenes[scene_index]
        if timestamp < scene["usable_start"]:
            frame_index += 1
            continue
        sid = int(scene["id"])
        gray = cv2.cvtColor(cv2.resize(frame, (224, 126)), cv2.COLOR_BGR2GRAY)
        prev = previous[sid]
        motion = float(np.mean(cv2.absdiff(gray, prev))) if prev is not None else 0.0
        sharpness = min(1000.0, float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        brightness = float(np.mean(gray))
        exposure = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)
        metrics[sid].append((timestamp, motion, sharpness, exposure))
        previous[sid] = gray
        frame_index += 1
    cap.release()

    ranked = []
    limit = DEFAULT_MAX_BROLL_SEC if max_duration is None else max(0.5, float(max_duration))
    for scene in scenes:
        values = metrics.get(int(scene["id"])) or []
        lo, hi = float(scene["usable_start"]), float(scene["usable_end"])
        
        # Tách window thông minh theo shot tự nhiên, mốc trần GUI, mốc sàn 50% và Center Crop
        windows = decompose_scene_into_windows(lo, hi, limit)
        for window_index, (start, end, part_tag) in enumerate(windows, 1):
            if end - start < 0.35:
                continue
            window_values = [v for v in values if start <= v[0] < end]
            if window_values:
                motions = np.asarray([v[1] for v in window_values], dtype=float)
                motion = float(np.mean(motions))
                # Đánh nhau/va chạm thường có burst chuyển động ngắn
                motion_peak = float(np.percentile(motions, 90))
                motion_burst = float(np.std(motions))
                sharpness = sum(v[2] for v in window_values) / len(window_values)
                exposure = sum(v[3] for v in window_values) / len(window_values)
                score = (
                    motion * 0.25
                    + motion_peak * 0.55
                    + motion_burst * 0.20
                    + min(20.0, sharpness / 50.0) * 0.08
                    + exposure * 1.5
                )
            else:
                score = 0.0
            
            sub_id = f"{scene.get('id')}{chr(96 + window_index)}" if len(windows) > 1 else str(scene.get("id"))
            ranked.append({
                "scene_id": scene.get("id"),
                "sub_id": sub_id,
                "window_index": window_index,
                "part_tag": part_tag,
                "start": round(start, 6),
                "end": round(end, 6),
                "duration": round(end - start, 6),
                "score": round(score, 6),
                "reason": "action",
                "scene_safe_start": round(lo, 6),
                "scene_safe_end": round(hi, 6),
                "configured_max_duration": round(limit, 6),
            })
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def attach_subtitle_context(ranked_windows: list, srt_path: str,
                            context_seconds: float = 8.0) -> list:
    """Gắn sub trong/ngoài clip để AI hiểu Top B-roll mà không cần gửi hình."""
    try:
        from broll_semantic import parse_srt
        cues = parse_srt(srt_path) if srt_path and os.path.isfile(srt_path) else []
    except Exception:
        cues = []
    output = []
    for index, source in enumerate(ranked_windows, 1):
        item = dict(source)
        start, end = float(item["start"]), float(item["end"])
        inside, before, after = [], [], []
        for cue in cues:
            cue_start = float(cue.get("start", 0))
            cue_end = float(cue.get("end", cue_start))
            text = str(cue.get("text", "")).strip()
            if not text:
                continue
            if cue_end > start and cue_start < end:
                inside.append(text)
            elif start - context_seconds <= cue_end <= start:
                before.append(text)
            elif end <= cue_start <= end + context_seconds:
                after.append(text)
        item.update({
            "id": str(item.get("id") or f"H{index:02d}"),
            "rank": int(item.get("rank") or index),
            "subtitle": " ".join(inside).strip(),
            "context_before": " ".join(before[-4:]).strip(),
            "context_after": " ".join(after[:4]).strip(),
        })
        output.append(item)
    return output


def select_highlight_pool(ranked_windows: list, required_source_duration: float,
                          reserve_ratio: float = 1.15) -> list:
    """Trả TOÀN BỘ Top 1..N, không cắt kho theo duration.

    ``required_source_duration`` và ``reserve_ratio`` chỉ còn dùng để ghi metadata/
    cảnh báo ở caller. Timeline cuối được cắt sau khi đã đo voice thật; loại sớm
    các Top thấp ở đây từng làm mất cảnh dự phòng và khiến mapper thiếu hình.
    """
    selected = []
    seen = set()
    # Đúng nghĩa Top 1 → N: không luân phiên cưỡng bức qua mọi scene, vì cách
    # đó từng kéo cảnh tĩnh/ngồi nói chuyện vào trước cảnh đánh nhau.
    for item in ranked_windows:
        key = (round(float(item.get("start", 0)), 3), round(float(item.get("end", 0)), 3))
        if key in seen:
            continue
        seen.add(key)
        selected.append(dict(item))
    return selected


def order_highlights(clips: list, mode: str) -> list:
    ordered = [dict(item) for item in clips]
    normalized = str(mode or "heatmap_ranked").strip().lower()
    if normalized == "heatmap_chronological":
        ordered.sort(key=lambda item: (float(item.get("start", 0)), int(item.get("rank", 0))))
    for index, item in enumerate(ordered, 1):
        item["order"] = index
    return ordered


def save_highlight_candidates(specific_dir: str, clips: list, mode: str) -> str:
    path = os.path.join(specific_dir, "highlight_candidates.json")
    total_duration = sum(
        max(0.0, float(item.get("end", 0)) - float(item.get("start", 0)))
        for item in clips
    )
    with open(path, "w", encoding="utf-8") as stream:
        json.dump({
            "version": 2,
            "pool_policy": "full_ranked_pool",
            "mode": mode,
            "clip_count": len(clips),
            "total_duration": round(total_duration, 3),
            "clips": clips,
        }, stream,
                  ensure_ascii=False, indent=2)
    # Tên mới phản ánh đúng vai trò kho hình đầy đủ. Giữ file cũ để tương thích
    # editor/preset phiên bản trước trong giai đoạn chuyển đổi.
    visual_path = os.path.join(specific_dir, "visual_pool.json")
    with open(visual_path, "w", encoding="utf-8") as stream:
        json.dump({
            "version": 1,
            "pool_policy": "full_ranked_pool",
            "mode": mode,
            "clip_count": len(clips),
            "total_duration": round(total_duration, 3),
            "clips": clips,
        }, stream, ensure_ascii=False, indent=2)
    return path


def load_highlight_candidates(specific_dir: str, expected_mode: str = "") -> list:
    """Đọc đúng kho cảnh đã dùng để viết prompt, không chấm/đánh ID lại."""
    path = os.path.join(specific_dir, "highlight_candidates.json")
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        mode = str(payload.get("mode") or "")
        clips = payload.get("clips") or []
        if expected_mode and mode != expected_mode:
            raise RuntimeError(f"Kho highlight thuộc mode {mode}, không phải {expected_mode}.")
        if not clips or any(not str(item.get("id") or "") for item in clips):
            raise RuntimeError("Kho highlight thiếu cảnh hoặc thiếu ID ổn định.")
        ids = [str(item["id"]) for item in clips]
        if len(ids) != len(set(ids)):
            raise RuntimeError("Kho highlight có ID trùng nhau.")
        return [dict(item) for item in clips]
    except FileNotFoundError:
        return []


def generate_visual_storyboards(
    low_video_path: str,
    specific_dir: str,
    candidates: list | None = None,
    fps: float = 1.0,
    grid: tuple[int, int] = (8, 8),
    tile_size: tuple[int, int] = (240, 135),
    max_frames: int = 600,
    force: bool = False,
) -> list[str]:
    """Trích xuất tối đa 600 frame (mặc định 600 frame) từ 00:00 đến hết video theo thứ tự thời gian.

    - Video <= 600s: lấy đều 1 frame/giây (1 fps).
    - Video > 600s: tự động tính bước nhảy thích ứng để giữ trần tối đa 600 frames (10 sheets lưới 8x8).
    - Các frame được ghép thành contact sheet Full HD 1080p (lưới 8x8 = 64 frame/tờ -> chỉ ~10 sheets).
    - Mỗi tile được đóng dấu timestamp [mm:ss] và nhãn ứng viên [Hxx] nếu thuộc cửa sổ đó.
    - Kết quả: các file storyboard_01.jpg đến storyboard_10.jpg trong specific_dir.
    """
    import cv2
    import numpy as np

    if not low_video_path or not os.path.isfile(low_video_path):
        raise FileNotFoundError(f"Thiếu video nguồn để tạo storyboard: {low_video_path}")
    os.makedirs(specific_dir, exist_ok=True)
    manifest_path = os.path.join(specific_dir, "storyboard_manifest.json")

    # Kiểm tra cache hợp lệ (nếu cache cũ > 12 sheet thì tái tạo lại theo chuẩn mới 10 sheets Full HD)
    if not force and os.path.isfile(manifest_path):
        try:
            with open(manifest_path, "r", encoding="utf-8") as f:
                manifest = json.load(f)
            cached_sampled = int(manifest.get("total_sampled_frames") or 0)
            sheets = manifest.get("sheets") or []
            existing_files = [os.path.join(specific_dir, s["file"]) for s in sheets if isinstance(s, dict) and "file" in s]
            if len(sheets) <= 12 and cached_sampled <= max_frames + 64 and existing_files and all(os.path.isfile(p) and os.path.getsize(p) > 1024 for p in existing_files):
                logger.info("⏭️ [STORYBOARD] Đã có %d sheet storyboard hợp lệ (%d frames) — bỏ qua trích xuất lại.", len(existing_files), cached_sampled)
                return existing_files
        except Exception:
            pass

    started = time.time()
    cap = cv2.VideoCapture(low_video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Không mở được video để tạo storyboard: {low_video_path}")

    video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    video_duration = total_frames / video_fps if total_frames > 0 else 0.0

    cols, rows = grid
    tiles_per_sheet = cols * rows
    tile_w, tile_h = tile_size
    sheet_w, sheet_h = cols * tile_w, rows * tile_h

    # Tiền xử lý candidate spans để tra cứu nhanh
    candidate_spans = []
    for c in (candidates or []):
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id") or "")
        if not cid:
            continue
        try:
            start_s = float(c.get("start", 0.0))
            end_s = float(c.get("end", 0.0))
            candidate_spans.append((start_s, end_s, cid))
        except (ValueError, TypeError):
            continue

    def get_candidate_tag(t_sec: float) -> str:
        for s, e, cid in candidate_spans:
            if s <= t_sec <= e:
                return cid
        return ""

    # Tính toán bước lấy mẫu thích ứng:
    # Nếu video <= max_frames (<= 10 phút với max_frames=600): lấy 1 frame/giây (1.0s)
    # Nếu video > max_frames: lấy mẫu giãn cách video_duration / max_frames (vd. 25 phút ~ 2.5s/frame)
    target_frames = max(25, int(max_frames or 600))
    if video_duration <= float(target_frames):
        interval_sec = 1.0 / max(0.1, float(fps or 1.0))
    else:
        interval_sec = video_duration / float(target_frames)

    sample_step = max(1, int(round(video_fps * interval_sec)))
    expected_sheets = int(np.ceil(min(video_duration / interval_sec, target_frames) / tiles_per_sheet))
    logger.info(
        "🎞️ [STORYBOARD 1080P] Bắt đầu trích xuất storyboard Full HD: video ~%.1fs, bước lấy mẫu %.2fs/frame "
        "(dự kiến ~%d sheets lưới %dx%d = %d frame/sheet)...",
        video_duration, interval_sec, max(1, expected_sheets), cols, rows, tiles_per_sheet,
    )

    generated_sheets = []
    manifest_sheets = []
    current_sheet = np.zeros((sheet_h, sheet_w, 3), dtype=np.uint8)
    tile_count_in_sheet = 0
    sheet_idx = 1
    frame_idx = 0
    sampled_count = 0
    first_sec_in_sheet = 0.0
    last_sec_in_sheet = 0.0

    while True:
        ok, frame = cap.read()
        if not ok or frame is None:
            break

        if frame_idx % sample_step == 0:
            t_sec = frame_idx / video_fps
            if tile_count_in_sheet == 0:
                first_sec_in_sheet = t_sec
            last_sec_in_sheet = t_sec

            # Resize frame thành tile 16:9
            tile = cv2.resize(frame, (tile_w, tile_h), interpolation=cv2.INTER_AREA)

            # Đóng dấu timestamp [mm:ss] và candidate ID
            m, s = int(t_sec // 60), int(t_sec % 60)
            tag = get_candidate_tag(t_sec)
            label = f"[{m:02d}:{s:02d}] {tag}".strip()

            # Nền đen cho chữ dễ nhìn, kích thước vừa vặn cho tile 240x135
            box_w = 54 + (38 if tag else 0)
            cv2.rectangle(tile, (2, 2), (box_w, 20), (0, 0, 0), -1)
            text_color = (0, 255, 255) if tag else (255, 255, 255)
            cv2.putText(tile, label, (4, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.38, text_color, 1, cv2.LINE_AA)

            # Ghép vào sheet
            r = tile_count_in_sheet // cols
            c = tile_count_in_sheet % cols
            current_sheet[r * tile_h:(r + 1) * tile_h, c * tile_w:(c + 1) * tile_w] = tile

            tile_count_in_sheet += 1
            sampled_count += 1

            if tile_count_in_sheet >= tiles_per_sheet:
                file_name = f"storyboard_{sheet_idx:02d}.jpg"
                sheet_path = os.path.join(specific_dir, file_name)
                cv2.imwrite(sheet_path, current_sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
                generated_sheets.append(sheet_path)
                m_start, s_start = int(first_sec_in_sheet // 60), int(first_sec_in_sheet % 60)
                m_end, s_end = int(last_sec_in_sheet // 60), int(last_sec_in_sheet % 60)
                manifest_sheets.append({
                    "file": file_name,
                    "sheet_index": sheet_idx,
                    "time_range": f"{m_start:02d}:{s_start:02d} - {m_end:02d}:{s_end:02d}",
                    "start_sec": round(first_sec_in_sheet, 1),
                    "end_sec": round(last_sec_in_sheet, 1),
                    "tiles": tile_count_in_sheet,
                })
                sheet_idx += 1
                current_sheet = np.zeros((sheet_h, sheet_w, 3), dtype=np.uint8)
                tile_count_in_sheet = 0

        frame_idx += 1

    cap.release()

    # Lưu sheet cuối nếu còn dư tile
    if tile_count_in_sheet > 0:
        file_name = f"storyboard_{sheet_idx:02d}.jpg"
        sheet_path = os.path.join(specific_dir, file_name)
        cv2.imwrite(sheet_path, current_sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
        generated_sheets.append(sheet_path)
        m_start, s_start = int(first_sec_in_sheet // 60), int(first_sec_in_sheet % 60)
        m_end, s_end = int(last_sec_in_sheet // 60), int(last_sec_in_sheet % 60)
        manifest_sheets.append({
            "file": file_name,
            "sheet_index": sheet_idx,
            "time_range": f"{m_start:02d}:{s_start:02d} - {m_end:02d}:{s_end:02d}",
            "start_sec": round(first_sec_in_sheet, 1),
            "end_sec": round(last_sec_in_sheet, 1),
            "tiles": tile_count_in_sheet,
        })

    # Ghi manifest và timeline tóm tắt
    manifest_data = {
        "version": 1,
        "video": os.path.basename(low_video_path),
        "total_sampled_frames": sampled_count,
        "total_sheets": len(generated_sheets),
        "fps": fps,
        "grid": [cols, rows],
        "tile_size": [tile_w, tile_h],
        "sheets": manifest_sheets,
    }
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f, ensure_ascii=False, indent=2)

    timeline_txt_path = os.path.join(specific_dir, "storyboard_timeline.txt")
    with open(timeline_txt_path, "w", encoding="utf-8") as f:
        f.write(f"STORYBOARD TIMELINE ({sampled_count} frames, {len(generated_sheets)} sheets, interval ~{interval_sec:.2f}s):\n")
        for s in manifest_sheets:
            f.write(f"- {s['file']}: {s['time_range']} ({s['tiles']} frames)\n")

    elapsed = time.time() - started
    logger.info(
        "✅ [STORYBOARD 600F] Đã tạo %d sheet (%d frames, bước ~%.2fs) trong %.2fs -> %s",
        len(generated_sheets), sampled_count, interval_sec, elapsed, specific_dir,
    )
    return generated_sheets


def ensure_visual_storyboards(video_path: str, specific_dir: str, candidates: list | None = None, fps: float = 1.0, max_frames: int = 600) -> list[str]:
    """Bảo đảm các tấm storyboard (tối đa 600 frames) đã sẵn sàng trong thư mục job."""
    if not candidates:
        try:
            candidates = load_highlight_candidates(specific_dir)
        except Exception:
            candidates = []
    return generate_visual_storyboards(video_path, specific_dir, candidates=candidates, fps=fps, max_frames=max_frames)


def prepare_highlight_candidates(low_video_path: str, srt_path: str, specific_dir: str,
                                 mode: str, required_source_duration: float,
                                 max_duration: float = 10.0, youtube_url: str = "",
                                 safe_start_ratio: float = 0.02,
                                 safe_end_ratio: float = 0.98) -> list:
    """Chuẩn bị Top pool trước khi gọi AI cho TH2/TH3."""
    scene_map = build_scene_map(low_video_path, target_limit=max_duration)
    source_duration = float(scene_map.get("source_duration") or 0.0)
    scenes = usable_scenes(
        scene_map, source_duration * float(safe_start_ratio),
        source_duration * float(safe_end_ratio), min_duration=0.35,
    )
    ranked = rank_action_windows(low_video_path, scenes, max_duration=max_duration)
    if youtube_url:
        try:
            from youtube_heatmap import blend_heatmap_scores, get_youtube_heatmap
            markers = get_youtube_heatmap(youtube_url, timeout=15)
            if markers:
                ranked = blend_heatmap_scores(ranked, markers, 0.70)
        except Exception as exc:
            logger.warning("⚠️ [HEATMAP PRE-AI] Bỏ qua heatmap YouTube: %s", exc)
    for index, item in enumerate(ranked, 1):
        item["id"] = f"H{index:02d}"
        item["rank"] = index
    ranked = attach_subtitle_context(ranked, srt_path)
    # Lưu TOÀN BỘ kho cùng bộ ID. AI chỉ nhận phần Top cần thiết, còn editor
    # dùng phần còn lại làm cảnh dự phòng mà không bao giờ tạo lại Hxx.
    save_highlight_candidates(specific_dir, ranked, mode)
    # Trích xuất toàn bộ storyboard 1fps từ 00:00 đến kết thúc cho AI xem
    try:
        generate_visual_storyboards(low_video_path, specific_dir, candidates=ranked, fps=1.0)
    except Exception as exc:
        logger.warning("⚠️ [STORYBOARD] Lỗi sinh storyboard 1fps: %s", exc)
    # Gemini chỉ nhận khoảng 50% scene để giảm prompt/đối chiếu, nhưng không
    # lấy Top thuần túy vì có thể dồn vào một đoạn. 70% quota ưu tiên theo điểm,
    # 30% còn lại rải đều đầu-giữa-cuối để AI vẫn hiểu diễn biến toàn video.
    target_count = max(1, (len(ranked) + 1) // 2)
    top_quota = min(target_count, max(1, round(target_count * 0.70)))
    selected = [dict(item) for item in ranked[:top_quota]]
    selected_ids = {str(item.get("id")) for item in selected}
    chronological = sorted(ranked, key=lambda item: float(item.get("start", 0.0)))
    remaining_slots = target_count - len(selected)
    if remaining_slots > 0:
        available = [item for item in chronological if str(item.get("id")) not in selected_ids]
        for slot in range(remaining_slots):
            if not available:
                break
            position = round(slot * (len(available) - 1) / max(1, remaining_slots - 1))
            item = available.pop(max(0, min(len(available) - 1, position)))
            selected.append(dict(item))
            selected_ids.add(str(item.get("id")))
    if len(selected) < target_count:
        for item in ranked:
            if str(item.get("id")) in selected_ids:
                continue
            selected.append(dict(item))
            selected_ids.add(str(item.get("id")))
            if len(selected) >= target_count:
                break
    pool = order_highlights(selected, mode)
    pool_duration = sum(max(0.0, float(c.get("end", 0)) - float(c.get("start", 0))) for c in pool)
    desired_reserve = max(0.0, float(required_source_duration)) * 2.0
    logger.info(
        "🧠 [GEMINI VISUAL POOL] Gửi %d/%d scene (%.0f%%): %.1fs hình | "
        "70%% Top + 30%% phủ timeline | %s.",
        len(pool), len(ranked), 100.0 * len(pool) / max(1, len(ranked)),
        pool_duration, mode,
    )
    if pool_duration + 0.05 < desired_reserve:
        logger.warning(
            "⚠️ [VISUAL RESERVE] Kho đầy đủ chỉ có %.1fs, dưới mốc 200%% %.1fs; "
            "vẫn tiếp tục, mapper sẽ nới và thêm cảnh cùng ngữ cảnh sau TTS.",
            pool_duration, desired_reserve,
        )
    return pool


def load_narration_plan(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def map_plan_to_highlights(plan: dict, highlight_pool: list,
                           required_source_duration: float, mode: str,
                           max_duration: float | None = None) -> list:
    """Chia kho hình theo độ dài từng đoạn lời; thiếu ref thì nối Top dự phòng, không lặp.
    Ưu tiên cụm liền kề khi cảnh ngắn, chặn trần an toàn bản quyền (1.5x GUI limit).
    """
    chronological = str(mode or "") == "heatmap_chronological"
    limit = (
        float(max_duration)
        if max_duration is not None and float(max_duration) > 0
        else float((highlight_pool[0] if highlight_pool else {}).get("configured_max_duration") or DEFAULT_MAX_BROLL_SEC)
    )
    # Trần an toàn bản quyền: không để một cụm liên tục quá 1.5x limit (ví dụ 10s -> max 15s)
    cluster_safe_cap = max(8.0, limit * 1.5)

    working_pool = [dict(item) for item in highlight_pool]
    by_id = {str(item.get("id")): dict(item) for item in working_pool}
    selected, exhausted = [], set()
    consumed = {}
    segments = plan.get("segments") if isinstance(plan, dict) else []
    segments = [item for item in segments if isinstance(item, dict) and item.get("narration")] \
        if isinstance(segments, list) else []
    # Ưu tiên duration đo từ chính TTS. Cache cũ chưa có timing mới dùng số từ.
    weights = [
        max(0.05, float(item.get("voice_duration_raw") or 0.0))
        if float(item.get("voice_duration_raw") or 0.0) > 0
        else max(1, len(str(item.get("narration") or "").split()))
        for item in segments
    ]
    total_weight = max(1, sum(weights))
    reserve_cursor = 0

    # Chỉ dành cho cache/schema cũ không có refs. Kế hoạch mới có refs phải đi
    # qua mapper theo từng segment ở dưới, không được phân cảnh bằng số từ nữa.
    has_valid_refs = any(
        str(ref) in by_id
        for segment in segments
        for ref in (([segment.get("visual_refs")]
                     if isinstance(segment.get("visual_refs"), str)
                     else (segment.get("visual_refs") or [])))
    )
    if chronological and has_valid_refs:
        referenced_starts = []
        for segment in segments:
            refs = segment.get("visual_refs") or []
            if isinstance(refs, str):
                refs = [refs]
            starts = sorted(float(by_id[str(ref)].get("start", 0))
                            for ref in refs if str(ref) in by_id)
            if starts:
                referenced_starts.append(starts[0])
        if any(referenced_starts[i] + 0.001 < referenced_starts[i - 1]
               for i in range(1, len(referenced_starts))):
            logger.warning("⚠️ [TH3 AUTO-REPAIR] Ref đảo thứ tự; bỏ refs và dùng Top theo timeline.")
            has_valid_refs = False
    if chronological and not has_valid_refs:
        chosen, total = [], 0.0
        for source in sorted(working_pool, key=lambda c: int(c.get("rank", 999999))):
            remaining = float(required_source_duration) - total
            if remaining <= 0.001:
                break
            clip = dict(source)
            duration = min(float(clip["end"]) - float(clip["start"]), remaining)
            if duration <= 0.05:
                continue
            clip["end"] = round(float(clip["start"]) + duration, 6)
            clip["duration"] = round(duration, 6)
            chosen.append(clip)
            total += duration
        chosen.sort(key=lambda c: float(c.get("start", 0)))
        for index, clip in enumerate(chosen):
            clip["narration_segment"] = (
                str(segments[min(index, len(segments) - 1)].get("id") or "")
                if segments else "reserve"
            )
            clip["order"] = index + 1
        return chosen

    def take_clip(ref, segment_id, remaining, mapping_role="anchor", anchor_ref=""):
        nonlocal selected
        ref = str(ref)
        if ref not in by_id or ref in exhausted or remaining <= 0.001:
            return 0.0
        clip = dict(by_id[ref])
        base_start, base_end = float(clip["start"]), float(clip["end"])
        if any(base_start < float(other["end"]) - 0.001
               and base_end > float(other["start"]) + 0.001 for other in selected):
            exhausted.add(ref)
            return 0.0
        clip = expand_if_gap_is_small(clip, remaining, occupied=selected)
        original_start = float(clip["start"])
        original_end = float(clip["end"])
        offset = max(0.0, float(consumed.get(ref, 0.0)))
        clip_start = min(original_end, original_start + offset)
        available = max(0.0, original_end - clip_start)
        duration = min(available, remaining)
        if duration < 0.05:
            exhausted.add(ref)
            return 0.0
        if duration < 2.5 and available >= 2.5:
            duration = 2.5
        clip["start"] = round(clip_start, 6)
        clip["end"] = round(clip_start + duration, 6)
        clip["duration"] = round(duration, 6)
        clip["narration_segment"] = segment_id
        clip["mapping_role"] = mapping_role
        if anchor_ref:
            clip["anchor_ref"] = str(anchor_ref)
        selected.append(clip)
        # Một Top scene chỉ xuất hiện một lần trong video. Phần đuôi không dùng
        # được bỏ thay vì quay lại sau vài cảnh, tránh cảm giác lặp footage.
        consumed[ref] = offset + duration
        exhausted.add(ref)
        return duration

    if str(mode or "") == "heatmap_ranked":
        # TH2 giữ đúng nhịp: Top chính -> cụm cảnh lân cận (nếu thiếu thời lượng) -> Top tiếp theo
        protected_refs = set()
        for segment in segments:
            refs = segment.get("visual_refs") or []
            if isinstance(refs, str):
                refs = [refs]
            protected_refs.update(str(ref) for ref in refs)

        def text_tokens(value):
            return set(re.findall(r"[a-z0-9']+", str(value or "").lower()))

        def reserve_score(candidate, query_tokens, anchor, current_cluster_dur: float = 0.0):
            cand_dur = float(candidate.get("duration") or (float(candidate.get("end", 0)) - float(candidate.get("start", 0))))
            anchor_start = float(anchor.get("start", 0)) if anchor else float(candidate.get("start", 0))
            anchor_end = float(anchor.get("end", 0)) if anchor else float(candidate.get("start", 0))
            cand_start = float(candidate.get("start", 0))
            
            # Khoảng cách giữa candidate và anchor
            distance_to_end = abs(cand_start - anchor_end)
            distance_to_start = abs(float(candidate.get("end", 0)) - anchor_start)
            min_dist = min(distance_to_end, distance_to_start)
            is_directly_adjacent = min_dist <= 2.0 or (anchor and candidate.get("scene_id") == anchor.get("scene_id"))

            # BẢO VỆ BẢN QUYỀN:
            # Nếu cảnh chính đã đủ >= limit (ví dụ 10s) HOẶC gom thêm candidate làm cụm vượt cluster_safe_cap (15s):
            # Tuyệt đối không gom tiếp cảnh liền kề, ép thuật toán chuyển sang phân đoạn khác!
            if is_directly_adjacent:
                if current_cluster_dur >= limit - 0.001 or (current_cluster_dur + cand_dur > cluster_safe_cap):
                    return -999.0

            candidate_tokens = text_tokens(" ".join([
                str(candidate.get("subtitle") or ""),
                str(candidate.get("context_before") or ""),
                str(candidate.get("context_after") or ""),
            ]))
            overlap = len(query_tokens & candidate_tokens) / max(1, len(query_tokens))
            continuity = 1.0 / (1.0 + min_dist / 15.0)
            same_scene = 1.5 if anchor and candidate.get("scene_id") == anchor.get("scene_id") else 0.0
            follows = 2.0 if anchor and float(candidate.get("start", 0)) >= anchor_end - 0.05 else 0.0
            backwards = 1.0 if anchor and float(candidate.get("end", 0)) <= anchor_start + 0.05 else 0.0
            # Điểm ưu tiên cao cho cảnh liền kề khi cụm còn ngắn (< limit)
            adjacent_boost = 3.5 if is_directly_adjacent and current_cluster_dur < limit else 0.0
            quality = 1.0 / max(1.0, float(candidate.get("rank", 999)))

            return adjacent_boost + overlap * 0.6 + continuity + same_scene + follows + backwards + quality * 0.25

        def fill_near_anchor(segment_id, target, query_tokens, anchor, current_cluster_dur: float = 0.0):
            filled = 0.0
            accumulated_cluster = current_cluster_dur
            while filled < target - 0.001:
                candidates = []
                for candidate in working_pool:
                    ref = str(candidate.get("id"))
                    if ref in exhausted or ref in protected_refs:
                        continue
                    score = reserve_score(candidate, query_tokens, anchor, accumulated_cluster)
                    if score > -900.0:
                        candidates.append((score, candidate))
                if not candidates:
                    break
                _, candidate = max(candidates, key=lambda pair: pair[0])
                ref = str(candidate.get("id"))
                gained = take_clip(
                    ref, segment_id, target - filled,
                    mapping_role="reserve", anchor_ref=(anchor or {}).get("id", ""),
                )
                if gained < 0.05:
                    exhausted.add(ref)
                    continue
                filled += gained
                accumulated_cluster += gained
                anchor = candidate
            return filled

        for segment, weight in zip(segments, weights):
            segment_id = str(segment.get("id") or "")
            target = float(required_source_duration) * weight / total_weight
            refs = segment.get("visual_refs") or []
            if isinstance(refs, str):
                refs = [refs]
            refs = [str(ref) for ref in refs if str(ref) in by_id]
            query_tokens = text_tokens(" ".join([
                str(segment.get("source_hint") or ""),
                str(segment.get("visual_subject") or ""),
                str(segment.get("beat_type") or ""),
                str(segment.get("narration") or ""),
            ]))
            filled = 0.0
            if refs:
                for ref_index, ref in enumerate(refs):
                    remaining_refs = max(1, len(refs) - ref_index)
                    cluster_target = max(0.0, (target - filled) / remaining_refs)
                    anchor = by_id[ref]
                    anchor_used = take_clip(
                        ref, segment_id, cluster_target,
                        mapping_role="ai_anchor", anchor_ref=ref,
                    )
                    filled += anchor_used
                    cluster_remaining = max(0.0, cluster_target - anchor_used)
                    filled += fill_near_anchor(
                        segment_id, cluster_remaining, query_tokens, anchor,
                        current_cluster_dur=anchor_used
                    )
            if filled < target - 0.001:
                anchor = by_id[refs[-1]] if refs else None
                filled += fill_near_anchor(
                    segment_id, target - filled, query_tokens, anchor,
                    current_cluster_dur=filled
                )
    else:
        protected_refs = {
            str(ref)
            for segment in segments
            for ref in (segment.get("visual_refs") or [])
            if str(ref) in by_id
        }
        for segment_index, (segment, weight) in enumerate(zip(segments, weights)):
            segment_id = str(segment.get("id") or "")
            target = float(required_source_duration) * weight / total_weight
            filled = 0.0
            refs = segment.get("visual_refs") if isinstance(segment, dict) else []
            if isinstance(refs, str):
                refs = [refs]
            refs = [str(ref) for ref in (refs or []) if str(ref) in by_id]
            refs.sort(key=lambda ref: float(by_id[ref].get("start", 0)))
            for ref_index, ref in enumerate(refs):
                remaining_refs = max(1, len(refs) - ref_index)
                share = max(0.0, (target - filled) / remaining_refs)
                filled += take_clip(ref, segment_id, share, "ai_anchor", ref)
                if filled >= target - 0.001:
                    break

            previous_end = max(
                [float(c.get("end", 0)) for c in selected] + [-float("inf")]
            )
            next_starts = []
            for later in segments[segment_index + 1:]:
                later_refs = later.get("visual_refs") or []
                if isinstance(later_refs, str):
                    later_refs = [later_refs]
                next_starts.extend(float(by_id[str(ref)]["start"])
                                   for ref in later_refs if str(ref) in by_id)
                if next_starts:
                    break
            next_start = min(next_starts) if next_starts else float("inf")
            reserves = sorted(
                (item for item in working_pool
                 if str(item.get("id")) not in protected_refs
                 and str(item.get("id")) not in exhausted
                 and float(item.get("start", 0)) >= previous_end - 0.001
                 and float(item.get("start", 0)) < next_start - 0.001),
                key=lambda item: float(item.get("start", 0)),
            )
            for candidate in reserves:
                if filled >= target - 0.001:
                    break
                ref = str(candidate.get("id"))
                gained = take_clip(ref, segment_id, target - filled, "reserve",
                                   refs[-1] if refs else "")
                filled += gained

    # Schema cũ/AI thiếu segment hoặc do làm tròn: tiếp tục dùng kho dự phòng.
    current = sum(max(0.0, float(c["end"]) - float(c["start"])) for c in selected)
    fallback_pool = working_pool
    if chronological and selected:
        last_end = max(float(item.get("end", 0)) for item in selected)
        fallback_pool = sorted(
            (item for item in working_pool if float(item.get("start", 0)) >= last_end - 0.001),
            key=lambda item: float(item.get("start", 0)),
        )
    for item in fallback_pool:
        ref = str(item.get("id"))
        while ref not in exhausted and current < float(required_source_duration) - 0.001:
            tail_segment = str(segments[-1].get("id") or "reserve") if segments else "reserve"
            gained = take_clip(ref, tail_segment, float(required_source_duration) - current,
                               "reserve")
            if gained < 0.05:
                break
            current += gained
        if current >= float(required_source_duration) - 0.001:
            break
    if current < float(required_source_duration) - 0.001:
        selected = expand_timeline_shortage(
            selected, float(required_source_duration) - current
        )
        current = sum(max(0.0, float(c["end"]) - float(c["start"])) for c in selected)
    # CHỐT CHẶN BẢO VỆ THỜI LƯỢNG (EMERGENCY VISUAL FILL):
    # Nếu kho cảnh độc nhất cạn mà vẫn thiếu hình so với yêu cầu:
    # Tự động lấy thêm các cảnh hành động đẹp nhất (Top action) từ working_pool lấp đầy 100% thời lượng,
    # tuyệt đối không bao giờ để video bị thiếu hình dẫn đến đứng hình.
    if current < float(required_source_duration) - 0.001 and working_pool:
        shortage = float(required_source_duration) - current
        logger.info(
            "🔄 [EMERGENCY VISUAL FILL] Kho cảnh độc nhất cạn; tự động bù thêm %.2fs B-roll phụ từ Top action.",
            shortage,
        )
        ranked_by_quality = sorted(working_pool, key=lambda c: int(c.get("rank", 999999)))
        while current < float(required_source_duration) - 0.001:
            prev_current = current
            for item in ranked_by_quality:
                if current >= float(required_source_duration) - 0.001:
                    break
                remaining = float(required_source_duration) - current
                duration = min(max(0.0, float(item.get("end", 0)) - float(item.get("start", 0))), remaining)
                if duration < 0.05:
                    continue
                clip = dict(item)
                clip["end"] = round(float(clip["start"]) + duration, 6)
                clip["duration"] = round(duration, 6)
                clip["narration_segment"] = str(segments[-1].get("id") or "reserve") if segments else "reserve"
                clip["mapping_role"] = "emergency_reserve"
                clip["order"] = len(selected) + 1
                selected.append(clip)
                current += duration
            if current <= prev_current + 0.001:
                break
    if has_valid_refs:
        selected_anchor_ids = {
            str(item.get("id")) for item in selected
            if item.get("mapping_role") == "ai_anchor"
        }
        required_anchor_ids = {
            str(ref)
            for segment in segments
            for ref in (([segment.get("visual_refs")]
                         if isinstance(segment.get("visual_refs"), str)
                         else (segment.get("visual_refs") or [])))
            if str(ref) in by_id
        }
        missing = sorted(required_anchor_ids - selected_anchor_ids)
        if missing:
            logger.warning(
                "⚠️ [MAPPING AUTO-FALLBACK] Ref chồng/không dùng được %s; "
                "giữ ref khả dụng và bù bằng cảnh phụ.",
                ", ".join(missing),
            )
    # TH3 giữ diễn biến nguồn; TH2 giữ đúng thứ tự segment/ref cao trào mà AI nhận.
    if chronological:
        selected.sort(key=lambda item: (float(item.get("start", 0)), int(item.get("rank", 0))))
    # Timeline chỉ dùng đúng lượng nguồn cần thiết; clip cuối được cắt linh hoạt.
    output, accumulated = [], 0.0
    for item in selected:
        remaining = float(required_source_duration) - accumulated
        if remaining <= 0.001:
            break
        clip = dict(item)
        duration = min(max(0.0, float(clip["end"]) - float(clip["start"])), remaining)
        if duration < 0.05:
            continue
        clip["end"] = round(float(clip["start"]) + duration, 6)
        clip["duration"] = round(duration, 6)
        if (output and str(output[-1].get("id")) == str(clip.get("id"))
                and abs(float(output[-1].get("end", 0)) - float(clip.get("start", 0))) < 0.002):
            # Hai phần liền nhau của cùng một scene thực chất là một cảnh liên tục,
            # gộp lại để không tạo cut giả giữa hai story beat.
            output[-1]["end"] = clip["end"]
            output[-1]["duration"] = round(
                float(output[-1]["end"]) - float(output[-1]["start"]), 6
            )
            accumulated += duration
            continue
        clip["order"] = len(output) + 1
        output.append(clip)
        accumulated += duration
    return output


def validate_broll_timeline(clips: list, required_source_duration: float,
                            mode: str, tolerance: float = 0.05) -> None:
    """Chặn timeline hỏng; thiếu thời lượng được chuyển cho fallback tự cứu."""
    if not clips:
        raise RuntimeError("Timeline B-roll rỗng.")
    total = 0.0
    identities = set()
    previous_start = -float("inf")
    previous_segment = ""
    intervals = []
    chronological = mode in {"timesub_content", "heatmap_chronological"}
    for index, clip in enumerate(clips, 1):
        start = float(clip.get("start", 0))
        end = float(clip.get("end", 0))
        if end <= start:
            raise RuntimeError(f"Cảnh #{index} có timeframe không hợp lệ: {start:.2f}-{end:.2f}.")
        identity = clip.get("id") or (
            clip.get("scene_id"), round(start, 3), round(end, 3)
        )
        if identity is not None and identity in identities:
            raise RuntimeError(f"Timeline lặp scene {identity}.")
        identities.add(identity)
        if any(start < other_end - 0.001 and end > other_start + 0.001
               for other_start, other_end in intervals):
            raise RuntimeError(f"Cảnh #{index} chồng timeframe đã có trong timeline.")
        intervals.append((start, end))
        if chronological and start + 0.001 < previous_start:
            raise RuntimeError(f"Timeline {mode} bị đảo thời gian tại cảnh #{index}.")
        segment = str(clip.get("narration_segment") or "")
        if mode == "timesub_content" and previous_segment and segment and segment < previous_segment:
            raise RuntimeError(f"Timeline Timesub bị đảo story beat tại {segment}.")
        previous_start = start
        previous_segment = segment or previous_segment
        total += end - start
    if total + tolerance < float(required_source_duration):
        logger.warning(
            "⚠️ [TIMELINE SHORTAGE] Thiếu %.2fs hình (%.2fs/%.2fs); "
            "không dừng pipeline, chuyển sang kho cảnh phụ/nới an toàn.",
            float(required_source_duration) - total, total, float(required_source_duration),
        )


def map_timesub_story(plan: dict, ranked_windows: list,
                      required_source_duration: float,
                      minimum_final_clip: float = 1.25) -> list:
    """Dựng TH1 theo thứ tự story beat và thời gian nguồn.

    ``rank_semantic_story_windows`` đặt một anchor có ``narration_segment`` cho
    mỗi beat ở đầu danh sách, sau đó mới tới kho semantic dự phòng. Hàm này
    chia source thành các vùng quanh anchor, cấp đủ hình cho từng đoạn voice và
    chỉ mở rộng sang vùng lân cận khi vùng chính thiếu. Vì vậy lời V01..Vn và
    hình đều tiến về phía trước, thay vì lấy điểm semantic toàn cục rồi nhảy
    qua lại giữa các vụ việc.
    """
    required = max(0.0, float(required_source_duration))
    if required <= 0.001:
        return []
    segments = plan.get("segments") if isinstance(plan, dict) else []
    segments = [s for s in segments if isinstance(s, dict) and s.get("narration")] \
        if isinstance(segments, list) else []
    if not segments:
        ordered = sorted((dict(c) for c in ranked_windows), key=lambda c: float(c.get("start", 0)))
        return fill_timeline(ordered, required, allow_repeat=False)

    segment_ids = [str(s.get("id") or f"V{i + 1:02d}") for i, s in enumerate(segments)]
    anchors = {}
    for clip in ranked_windows:
        sid = str(clip.get("narration_segment") or "")
        if sid in segment_ids and sid not in anchors:
            anchors[sid] = dict(clip)
    # Nếu thiếu anchor, không giả vờ rằng Timesub vẫn khớp nội dung.
    if len(anchors) != len(segment_ids):
        missing = [sid for sid in segment_ids if sid not in anchors]
        logger.warning(
            "⚠️ [TIMESUB AUTO-FALLBACK] Thiếu anchor %s; dùng toàn bộ cảnh theo source time.",
            ", ".join(missing),
        )
        ordered = sorted((dict(c) for c in ranked_windows), key=lambda c: float(c.get("start", 0)))
        return fill_timeline(ordered, required, allow_repeat=False)

    anchor_starts = [float(anchors[sid]["start"]) for sid in segment_ids]
    if any(anchor_starts[i] + 0.001 < anchor_starts[i - 1] for i in range(1, len(anchor_starts))):
        logger.warning("⚠️ [TIMESUB AUTO-FALLBACK] Anchor đảo thứ tự; dùng cảnh source tuần tự.")
        ordered = sorted((dict(c) for c in ranked_windows), key=lambda c: float(c.get("start", 0)))
        return fill_timeline(ordered, required, allow_repeat=False)

    all_clips = []
    seen_keys = set()
    for source in ranked_windows:
        clip = dict(source)
        key = (clip.get("scene_id", clip.get("id")), round(float(clip.get("start", 0)), 3),
               round(float(clip.get("end", 0)), 3))
        if key not in seen_keys:
            seen_keys.add(key)
            all_clips.append(clip)

    weights = [max(1, len(str(s.get("narration") or "").split())) for s in segments]
    total_weight = max(1, sum(weights))
    boundaries = [-float("inf")]
    boundaries.extend((anchor_starts[i - 1] + anchor_starts[i]) / 2.0
                      for i in range(1, len(anchor_starts)))
    boundaries.append(float("inf"))
    used = set()
    output = []

    def clip_key(c):
        return (c.get("scene_id", c.get("id")), round(float(c.get("start", 0)), 3),
                round(float(c.get("end", 0)), 3))

    for index, (segment, weight) in enumerate(zip(segments, weights)):
        segment_id = segment_ids[index]
        target = required * weight / total_weight
        anchor = anchors[segment_id]
        lo, hi = boundaries[index], boundaries[index + 1]
        regional = [c for c in all_clips if lo <= float(c.get("start", 0)) < hi]
        # Anchor trước, sau đó semantic mạnh trong đúng vùng; khi xuất timeline
        # sẽ sort lại theo source time để chuyển cảnh tự nhiên.
        if index == 0:
            # Beat mở đầu dùng footage sớm nhất trong vùng setup. Nếu cứ
            # ghim anchor semantic muộn, beat dài ngay sau đó không còn đủ
            # khoảng timeline dù source phía trước còn rất nhiều hình.
            candidates = sorted(regional, key=lambda c: float(c.get("start", 0)))
        else:
            candidates = [anchor]
            candidates.extend(c for c in regional if clip_key(c) != clip_key(anchor))
        chosen, filled = [], 0.0
        for candidate in candidates:
            key = clip_key(candidate)
            if key in used:
                continue
            candidate_start = float(candidate.get("start", 0))
            candidate_end = float(candidate.get("end", 0))
            if any(candidate_start < float(other.get("end", 0)) - 0.001
                   and candidate_end > float(other.get("start", 0)) + 0.001
                   for other in output + chosen):
                continue
            available = max(0.0, float(candidate.get("end", 0)) - float(candidate.get("start", 0)))
            if available < 0.05:
                continue
            remaining = target - filled
            if remaining <= 0.001:
                break
            available = max(
                0.0, float(candidate.get("end", 0)) - float(candidate.get("start", 0))
            )
            duration = min(available, remaining)
            # Cảnh cuối của beat phải cắt đúng phần còn thiếu; không
            # tự nâng lên minimum rồi làm tổng hình dài hơn voice.
            clip = dict(candidate)
            clip["end"] = round(float(clip["start"]) + duration, 6)
            clip["duration"] = round(duration, 6)
            clip["narration_segment"] = segment_id
            chosen.append(clip)
            used.add(key)
            filled += duration
        # Quy tắc đã chốt: nếu phần thiếu không quá 50% độ dài cảnh cấu hình,
        # thử nới các cảnh chính trong cùng shot trước. Nếu thiếu quá 50% thì
        # không kéo một cảnh quá dài; chuyển thẳng sang cảnh phụ.
        shortage = max(0.0, target - filled)
        configured = max(
            0.1, float(anchor.get("configured_max_duration") or max(
                0.1, float(anchor.get("end", 0)) - float(anchor.get("start", 0))
            ))
        )
        if 0.001 < shortage <= configured * 0.50 + 0.001:
            chosen = expand_timeline_shortage(chosen, shortage, occupied=output)
            filled = sum(float(c["end"]) - float(c["start"]) for c in chosen)
        # Vùng midpoint có thể ngắn hơn phần voice (các sự kiện nằm sát
        # nhau). Khi đó mượn cửa sổ lân cận trong khoảng sau cảnh đã
        # giao cho beat trước và trước anchor beat kế tiếp. Như vậy không
        # ăn mất cảnh chính của beat sau và không đảo timeline.
        if filled < target - 0.001 and index < len(segment_ids) - 1:
            prior_end = max(
                [float(c.get("end", 0)) for c in output] + [-float("inf")]
            )
            next_anchor_start = float(anchors[segment_ids[index + 1]]["start"])
            # Một cửa sổ có thể vắt qua mép vùng. Không loại cả cửa sổ như bản
            # cũ: cắt phần giao nằm giữa cảnh trước và anchor kế tiếp rồi dùng
            # nó như B-roll phụ. Việc cắt này vẫn không vượt shot boundary.
            borrow_pool = []
            for source in all_clips:
                clipped_start = max(float(source.get("start", 0)), prior_end)
                clipped_end = min(float(source.get("end", 0)), next_anchor_start)
                if clipped_end - clipped_start < 0.05:
                    continue
                candidate = dict(source)
                candidate["start"] = round(clipped_start, 6)
                candidate["end"] = round(clipped_end, 6)
                candidate["duration"] = round(clipped_end - clipped_start, 6)
                candidate["mapping_role"] = "reserve"
                borrow_pool.append(candidate)
            borrow_pool.sort(key=lambda c: float(c.get("start", 0)))
            for candidate in borrow_pool:
                if filled >= target - 0.001:
                    break
                key = clip_key(candidate)
                if key in used:
                    continue
                start = float(candidate.get("start", 0))
                end = float(candidate.get("end", 0))
                if any(start < float(other.get("end", 0)) - 0.001
                       and end > float(other.get("start", 0)) + 0.001
                       for other in output + chosen):
                    continue
                remaining = target - filled
                available = float(candidate.get("end", 0)) - float(candidate.get("start", 0))
                duration = min(available, remaining)
                if duration < 0.05:
                    continue
                clip = dict(candidate)
                clip["end"] = round(float(clip["start"]) + duration, 6)
                clip["duration"] = round(duration, 6)
                clip["narration_segment"] = segment_id
                chosen.append(clip)
                used.add(key)
                filled += duration
        # Chỉ khi đã thêm/cắt cảnh phụ mà vẫn còn thiếu mới nới lần cuối.
        # Nếu cảnh phụ dài hơn phần thiếu, duration ở trên đã cắt đúng phần cần.
        if filled < target - 0.001:
            chosen = expand_timeline_shortage(
                chosen, target - filled, occupied=output
            )
            filled = sum(float(c["end"]) - float(c["start"]) for c in chosen)
        if filled < target - 0.05:
            # Không giết pipeline chỉ vì một story beat có vùng hình ngắn.
            # Tổng timeline sẽ được bù bằng cảnh phụ chưa dùng ở bước dưới.
            logger.warning(
                "⚠️ [TIMESUB RESERVE] %s thiếu %.2fs; tự bù cảnh phụ toàn cục.",
                segment_id, target - filled,
            )
        chosen.sort(key=lambda c: float(c.get("start", 0)))
        output.extend(chosen)

    # Bù thiếu tổng bằng mọi cảnh chưa dùng, giữ đúng thứ tự source. Cảnh phụ
    # được gắn vào story beat gần nhất theo timestamp; độ khớp tương đối được
    # chấp nhận để ưu tiên video luôn đủ hình và không đóng băng.
    total_output = sum(float(c["end"]) - float(c["start"]) for c in output)
    if total_output < required - 0.001:
        reserves = sorted(all_clips, key=lambda c: float(c.get("start", 0)))
        for candidate in reserves:
            if total_output >= required - 0.001:
                break
            key = clip_key(candidate)
            if key in used:
                continue
            start, end = float(candidate.get("start", 0)), float(candidate.get("end", 0))
            if end - start < 0.05 or any(
                start < float(other.get("end", 0)) - 0.001
                and end > float(other.get("start", 0)) + 0.001 for other in output
            ):
                continue
            duration = min(end - start, required - total_output)
            clip = dict(candidate)
            clip["end"] = round(start + duration, 6)
            clip["duration"] = round(duration, 6)
            nearest_index = min(
                range(len(anchor_starts)), key=lambda i: abs(start - anchor_starts[i])
            )
            clip["narration_segment"] = segment_ids[nearest_index]
            clip["mapping_role"] = "global_reserve"
            output.append(clip)
            used.add(key)
            total_output += duration
        output.sort(key=lambda c: float(c.get("start", 0)))
    if total_output < required - 0.05:
        output = expand_timeline_shortage(output, required - total_output)
        total_output = sum(float(c["end"]) - float(c["start"]) for c in output)
    if total_output < required - 0.05 and all_clips:
        shortage = required - total_output
        logger.info(
            "🔄 [TIMESUB EMERGENCY FILL] Tự động bù thêm %.2fs B-roll phụ từ kho cảnh để đảm bảo không đứng hình.",
            shortage,
        )
        ranked_by_quality = sorted(all_clips, key=lambda c: int(c.get("rank", 999999)))
        while total_output < required - 0.001:
            prev_total = total_output
            for candidate in ranked_by_quality:
                if total_output >= required - 0.001:
                    break
                start, end = float(candidate.get("start", 0)), float(candidate.get("end", 0))
                if end - start < 0.05:
                    continue
                duration = min(end - start, required - total_output)
                clip = dict(candidate)
                clip["end"] = round(start + duration, 6)
                clip["duration"] = round(duration, 6)
                clip["narration_segment"] = str(segments[-1].get("id") or "reserve") if segments else "reserve"
                clip["mapping_role"] = "emergency_reserve"
                clip["order"] = len(output) + 1
                output.append(clip)
                total_output += duration
            if total_output <= prev_total + 0.001:
                break
    # Các cửa sổ liền nhau trong cùng shot/story beat không cần tạo cut giả.
    # Gộp chúng để TH1 bớt vụn nhưng vẫn giữ nguyên nội dung và thời lượng.
    merged = []
    for clip in output:
        if (merged
                and clip.get("scene_id") == merged[-1].get("scene_id")
                and clip.get("narration_segment") == merged[-1].get("narration_segment")
                and float(clip.get("start", 0)) - float(merged[-1].get("end", 0)) <= 0.12):
            merged[-1]["end"] = max(float(merged[-1]["end"]), float(clip["end"]))
            merged[-1]["duration"] = round(
                float(merged[-1]["end"]) - float(merged[-1]["start"]), 6
            )
        else:
            merged.append(dict(clip))
    output = merged
    for order, clip in enumerate(output, 1):
        clip["order"] = order
    return output


def rank_semantic_windows(scenes: list, srt_path: str, script_path: str, max_duration: float | None = None) -> list:
    """Khớp nghĩa theo từng scene; cue SRT không còn được dùng làm ranh giới hình."""
    from broll_semantic import chunk_script, parse_srt, semantic_scores

    with open(script_path, "r", encoding="utf-8") as stream:
        script = stream.read()
    cues = parse_srt(srt_path)
    chunks = chunk_script(script)
    scene_texts = []
    anchors = []
    for scene in scenes:
        relevant = [
            cue for cue in cues
            if float(cue.get("end", 0)) > scene["usable_start"]
            and float(cue.get("start", 0)) < scene["usable_end"]
        ]
        scene_texts.append(" ".join(str(cue.get("text", "")) for cue in relevant).strip())
        if relevant:
            anchors.append(sum(float(cue.get("start", 0)) for cue in relevant) / len(relevant))
        else:
            anchors.append((scene["usable_start"] + scene["usable_end"]) / 2.0)
    scores = semantic_scores(chunks, scene_texts) if chunks and scene_texts else [0.0] * len(scenes)
    ranked = []
    limit = DEFAULT_MAX_BROLL_SEC if max_duration is None else max(0.5, float(max_duration))
    for scene, anchor, score in zip(scenes, anchors, scores):
        lo, hi = float(scene["usable_start"]), float(scene["usable_end"])
        cursor, window_index = lo, 1
        while cursor < hi - 0.05:
            end = min(hi, cursor + limit)
            if end - cursor >= 0.35:
                window = {
                    "scene_id": scene.get("id"), "window_index": window_index,
                    "start": round(cursor, 6), "end": round(end, 6),
                    "duration": round(end - cursor, 6),
                    "scene_safe_start": round(lo, 6), "scene_safe_end": round(hi, 6),
                    "configured_max_duration": round(limit, 6),
                    "score": round(float(score), 6), "reason": "semantic",
                }
                # Cửa sổ chứa tâm lời thoại được ưu tiên trong cùng scene.
                window["score"] = round(
                    float(score) + (0.0001 if cursor <= anchor < end else 0.0), 6
                )
                ranked.append(window)
            cursor += limit
            window_index += 1
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked


def rank_semantic_story_windows(scenes: list, srt_path: str, script_path: str,
                                plan_path: str = "", max_duration: float | None = None) -> list:
    """Ghép từng story beat theo thứ tự thay vì xếp hạng toàn bộ script một lần."""
    from broll_semantic import chunk_script, parse_srt, semantic_scores

    cues = parse_srt(srt_path)
    plan = load_narration_plan(plan_path) if plan_path else {}
    segments = plan.get("segments") if isinstance(plan, dict) else []
    beat_texts = []
    if isinstance(segments, list):
        for segment in segments:
            if not isinstance(segment, dict):
                continue
            text = " ".join(filter(None, [
                str(segment.get("source_hint") or "").strip(),
                str(segment.get("narration") or "").strip(),
            ])).strip()
            if text:
                beat_texts.append((str(segment.get("id") or ""), text))
    if not beat_texts:
        with open(script_path, "r", encoding="utf-8") as stream:
            beat_texts = [(f"V{index:02d}", text) for index, text in
                          enumerate(chunk_script(stream.read()), 1)]

    scene_texts, anchors = [], []
    for scene in scenes:
        relevant = [cue for cue in cues
                    if float(cue.get("end", 0)) > scene["usable_start"]
                    and float(cue.get("start", 0)) < scene["usable_end"]]
        scene_texts.append(" ".join(str(cue.get("text", "")) for cue in relevant).strip())
        anchors.append((sum(float(cue.get("start", 0)) for cue in relevant) / len(relevant))
                       if relevant else (scene["usable_start"] + scene["usable_end"]) / 2.0)

    chosen, used_ids, last_start = [], set(), -1.0
    for segment_id, beat_text in beat_texts:
        scores = semantic_scores([beat_text], scene_texts) if scene_texts else []
        candidates = []
        for index, score in enumerate(scores):
            scene = scenes[index]
            sid = int(scene.get("id", index))
            start = float(scene.get("usable_start", 0))
            if sid in used_ids or start + 0.001 < last_start:
                continue
            candidates.append((float(score), index))
        if not candidates:
            continue
        score, index = max(candidates, key=lambda pair: pair[0])
        scene = scenes[index]
        window = scene_window(scene, anchor=anchors[index], max_duration=max_duration)
        window.update({
            "score": round(score, 6), "reason": "semantic_story",
            "narration_segment": segment_id,
        })
        chosen.append(window)
        used_ids.add(int(scene.get("id", index)))
        last_start = float(window["start"])

    # Các scene còn lại là kho dự phòng, ưu tiên nghĩa toàn cục nhưng không làm
    # thay đổi thứ tự các beat đã ghép.
    fallback = rank_semantic_windows(scenes, srt_path, script_path, max_duration=max_duration)
    chosen_keys = {
        (item.get("scene_id"), round(float(item.get("start", 0)), 3),
         round(float(item.get("end", 0)), 3)) for item in chosen
    }
    chosen.extend(item for item in fallback if (
        item.get("scene_id"), round(float(item.get("start", 0)), 3),
        round(float(item.get("end", 0)), 3)
    ) not in chosen_keys)
    return chosen


def fill_timeline(ranked_windows: list, required_duration: float, allow_repeat: bool = True) -> list:
    if not ranked_windows:
        return []
    chosen = []
    accumulated = 0.0
    index = 0
    while accumulated < required_duration - 0.001:
        if not allow_repeat and index >= len(ranked_windows):
            break
        base = dict(ranked_windows[index % len(ranked_windows)])
        remaining = required_duration - accumulated
        duration = min(float(base["end"]) - float(base["start"]), remaining)
        if duration < 0.05:
            break
        base["end"] = round(float(base["start"]) + duration, 6)
        base["duration"] = round(duration, 6)
        base["order"] = len(chosen) + 1
        chosen.append(base)
        accumulated += duration
        index += 1
        if index > 1000:
            break
    return chosen


def save_broll_timeline(specific_dir: str, mode: str, scene_map: dict,
                        clips: list, required_duration: float) -> str:
    path = os.path.join(specific_dir, "broll_timeline.json")
    payload = {
        "version": 1,
        "mode": mode,
        "scene_map_fingerprint": scene_map.get("source_fingerprint", ""),
        "required_body_duration": round(float(required_duration), 6),
        "total_selected_duration": round(sum(float(c["end"]) - float(c["start"]) for c in clips), 6),
        "clips": clips,
    }
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2)
    logger.info("🧾 [TIMELINE] Đã lưu %d timeframe, không render B-roll trung gian.", len(clips))
    return path
