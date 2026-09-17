# -*- coding: utf-8 -*-
"""Đọc YouTube Most replayed bằng Edge headless và cộng điểm vào scene map."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from utils.logger import logger
except ImportError:
    import logging
    logger = logging.getLogger("YouTubeHeatmap")


CACHE_VERSION = 2
NEGATIVE_CACHE_SECONDS = 7 * 24 * 60 * 60


def _video_id(value: str) -> str:
    value = str(value or "").strip().rstrip("\\")
    parsed = urlparse(value)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        candidate = parsed.path.strip("/").split("/")[0]
    elif parsed.hostname and "youtube.com" in parsed.hostname:
        if parsed.path == "/watch":
            candidate = (parse_qs(parsed.query).get("v") or [""])[0]
        else:
            match = re.match(r"/(?:shorts|embed)/([^/?]+)", parsed.path)
            candidate = match.group(1) if match else ""
    else:
        candidate = value
    return candidate if re.fullmatch(r"[A-Za-z0-9_-]{11}", candidate) else ""


def _edge_path() -> str:
    candidates = [
        os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), "")


def _cache_file(video_id: str) -> Path:
    folder = Path(__file__).resolve().parent / "cache" / "youtube_heatmaps"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{video_id}.json"


def _read_cache(video_id: str):
    path = _cache_file(video_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != CACHE_VERSION:
            return None
        if data.get("status") == "ok":
            return data.get("markers") or []
        if time.time() - float(data.get("checked_at", 0)) < NEGATIVE_CACHE_SECONDS:
            return []
    except Exception:
        pass
    return None


def _write_cache(video_id: str, status: str, markers: list) -> None:
    path = _cache_file(video_id)
    payload = {
        "version": CACHE_VERSION, "video_id": video_id, "status": status,
        "checked_at": time.time(), "markers": markers,
    }
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _svg_to_markers(paths: list, duration: float) -> list:
    points = []
    for path in paths:
        for segment in re.findall(r"[Cc]\s*([^A-Za-z]+)", str(path or "")):
            numbers = [float(item) for item in re.findall(r"-?\d+(?:\.\d+)?", segment)]
            if len(numbers) < 6:
                continue
            x, y = numbers[-2], numbers[-1]
            if 0 <= x <= 1005:
                timestamp = max(0.0, min(duration, x / 1000.0 * duration))
                score = max(0.0, min(1.0, (100.0 - y) / 100.0))
                points.append((round(timestamp, 3), score))
    unique = {}
    for timestamp, score in points:
        unique[timestamp] = max(score, unique.get(timestamp, 0.0))
    ordered = sorted(unique.items())
    result = []
    default_width = max(1.0, duration / max(1, len(ordered)))
    for index, (start, score) in enumerate(ordered):
        end = ordered[index + 1][0] if index + 1 < len(ordered) else min(duration, start + default_width)
        result.append({
            "start": start, "end": round(end, 3),
            "score": round(score, 6),
        })
    return result


def _ytdlp_heatmap(url: str, timeout: int) -> list:
    """Lấy Heatmap từ player metadata; ổn định hơn dò SVG giao diện Edge."""
    try:
        import yt_dlp
        options = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "noplaylist": True,
            "socket_timeout": max(5, int(timeout)),
        }
        try:
            from downloader_processor import DownloaderProcessor
            options = DownloaderProcessor.apply_cookies_to_opts(options)
        except Exception as e:
            logger.debug("Không thể áp dụng cookies cho heatmap: %s", e)

        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
        markers = []
        for item in (info or {}).get("heatmap") or []:
            start = float(item.get("start_time") or 0.0)
            end = float(item.get("end_time") or start)
            score = float(item.get("value") or 0.0)
            if end > start:
                markers.append({
                    "start": round(start, 3),
                    "end": round(end, 3),
                    "score": round(max(0.0, min(1.0, score)), 6),
                })
        return markers
    except Exception as exc:
        logger.info("⏭️ [HEATMAP] yt-dlp không trả Heatmap (%s) — thử Edge.", exc)
        return []


def get_youtube_heatmap(url: str, timeout: int = 15) -> list:
    """Trả marker 0..1. Mọi lỗi đều trả [] để không làm hỏng render."""
    video_id = _video_id(url)
    if not video_id:
        return []
    cached = _read_cache(video_id)
    if cached is not None:
        logger.info("⏭️ [HEATMAP] Dùng cache %s (%d điểm).", video_id, len(cached))
        return cached

    markers = _ytdlp_heatmap(url, timeout)
    if markers:
        _write_cache(video_id, "ok", markers)
        best = max(markers, key=lambda item: item["score"])
        logger.info(
            "🔥 [HEATMAP] %d điểm | đỉnh %.1fs–%.1fs | metadata yt-dlp.",
            len(markers), best["start"], best["end"],
        )
        return markers
    edge = _edge_path()
    if not edge:
        logger.info("⏭️ [HEATMAP] Máy không có Edge — bỏ qua Most replayed.")
        return []
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options
    except ImportError:
        logger.warning("⚠️ [HEATMAP] Runtime chưa có Selenium — bỏ qua, render vẫn tiếp tục.")
        return []

    driver = None
    try:
        options = Options()
        options.binary_location = edge
        options.page_load_strategy = "eager"
        for argument in (
            "--headless=new", "--mute-audio", "--autoplay-policy=no-user-gesture-required",
            "--disable-gpu", "--disable-software-rasterizer", "--disable-extensions",
            "--disable-dev-shm-usage", "--no-sandbox", "--window-size=1280,720",
        ):
            options.add_argument(argument)
        driver = webdriver.Edge(options=options)
        driver.set_page_load_timeout(max(10, timeout))
        driver.get(f"https://www.youtube.com/watch?v={video_id}&hl=en")
        deadline = time.time() + max(5, timeout)
        paths, duration = [], 0.0
        while time.time() < deadline:
            result = driver.execute_script("""
                const video = document.querySelector('video');
                if (video) { video.muted = true; video.play().catch(() => {}); }
                const bar = document.querySelector('.ytp-progress-bar');
                if (bar) {
                    const r = bar.getBoundingClientRect();
                    bar.dispatchEvent(new MouseEvent('mousemove', {
                        bubbles: true, clientX: r.left + r.width * 0.5,
                        clientY: r.top + Math.max(1, r.height / 2)
                    }));
                }
                return {
                    duration: video ? video.duration : 0,
                    paths: [...document.querySelectorAll(
                        'path.ytp-modern-heat-map, path.ytp-heat-map-path, svg.ytp-heat-map-svg path'
                    )].map(p => p.getAttribute('d')).filter(Boolean)
                };
            """) or {}
            paths = result.get("paths") or []
            duration = float(result.get("duration") or 0)
            if paths and duration > 1:
                break
            time.sleep(0.75)
        markers = _svg_to_markers(paths, duration) if paths and duration > 1 else []
        _write_cache(video_id, "ok" if markers else "missing", markers)
        if markers:
            best = max(markers, key=lambda item: item["score"])
            logger.info(
                "🔥 [HEATMAP] %d điểm | đỉnh %.1fs–%.1fs | Edge chạy ẩn.",
                len(markers), best["start"], best["end"],
            )
        else:
            logger.info("⏭️ [HEATMAP] Video không có Most replayed — dùng điểm cũ.")
        return markers
    except Exception as exc:
        logger.warning("⚠️ [HEATMAP] Không lấy được SVG (%s) — dùng điểm cũ.", exc)
        try:
            _write_cache(video_id, "error", [])
        except Exception:
            pass
        return []
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def blend_heatmap_scores(ranked_windows: list, markers: list, heat_weight: float) -> list:
    """Giữ điểm gốc, chuẩn hóa 0..1 rồi cộng Heatmap theo trọng số."""
    if not ranked_windows or not markers:
        return ranked_windows
    values = [float(item.get("score", 0.0)) for item in ranked_windows]
    low, high = min(values), max(values)
    span = high - low
    weight = max(0.0, min(1.0, float(heat_weight)))
    result = []
    for item, raw_score in zip(ranked_windows, values):
        start, end = float(item.get("start", 0)), float(item.get("end", 0))
        heat_score = max((
            float(marker.get("score", 0.0))
            for marker in markers
            if float(marker.get("end", 0)) > start and float(marker.get("start", 0)) < end
        ), default=0.0)
        base_score = (raw_score - low) / span if span > 1e-9 else 1.0
        merged = dict(item)
        merged.update({
            "base_score": round(raw_score, 6),
            "base_score_normalized": round(base_score, 6),
            "heatmap_score": round(heat_score, 6),
            "score": round(base_score * (1.0 - weight) + heat_score * weight, 6),
        })
        result.append(merged)
    result.sort(key=lambda row: row["score"], reverse=True)
    return result
