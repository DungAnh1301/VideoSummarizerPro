# -*- coding: utf-8 -*-
"""Kiểm tra dữ liệu Most replayed/HEATSEEKER của một video YouTube."""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from urllib.parse import parse_qs, urlparse

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def video_id_from_url(value: str) -> str:
    value = value.strip().rstrip("\\")
    markdown = re.fullmatch(r"\[[^]]+\]\((https?://[^)]+)\)", value)
    if markdown:
        value = markdown.group(1)
    parsed = urlparse(value)
    if parsed.hostname in {"youtu.be", "www.youtu.be"}:
        return parsed.path.strip("/").split("/")[0]
    if parsed.hostname and "youtube.com" in parsed.hostname:
        if parsed.path == "/watch":
            video_id = (parse_qs(parsed.query).get("v") or [""])[0]
            if re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
                return video_id
            raise ValueError("Video ID trong link YouTube không hợp lệ.")
        match = re.match(r"/(?:shorts|embed)/([^/?]+)", parsed.path)
        if match:
            return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        return value
    raise ValueError("Link hoặc video ID YouTube không hợp lệ.")


def extract_player_response(html: str) -> dict:
    decoder = json.JSONDecoder()
    markers = (
        "ytInitialPlayerResponse =",
        "ytInitialPlayerResponse = ",
        '"PLAYER_VARS":{"video_id"',
    )
    for marker in markers[:2]:
        position = html.find(marker)
        if position < 0:
            continue
        position += len(marker)
        while position < len(html) and html[position].isspace():
            position += 1
        try:
            payload, _ = decoder.raw_decode(html[position:])
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            continue

    # Một số response nhúng JSON dưới dạng thuộc tính thay vì phép gán JS.
    match = re.search(r'"ytInitialPlayerResponse"\s*:\s*', html)
    if match:
        payload, _ = decoder.raw_decode(html[match.end():])
        if isinstance(payload, dict):
            return payload
    raise RuntimeError("Không tìm thấy ytInitialPlayerResponse trong trang YouTube.")


def collect_heat_markers(value, output: list) -> None:
    if isinstance(value, dict):
        renderer = value.get("heatMarkerRenderer")
        if isinstance(renderer, dict):
            try:
                output.append({
                    "start": int(renderer["timeRangeStartMillis"]) / 1000.0,
                    "duration": int(renderer["markerDurationMillis"]) / 1000.0,
                    "score": float(renderer["heatMarkerIntensityScoreNormalized"]),
                })
            except (KeyError, TypeError, ValueError):
                pass
        for child in value.values():
            collect_heat_markers(child, output)
    elif isinstance(value, list):
        for child in value:
            collect_heat_markers(child, output)


def format_time(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


def strongest_non_overlapping(markers: list, limit: int) -> list:
    selected = []
    for item in sorted(markers, key=lambda row: row["score"], reverse=True):
        center = item["start"] + item["duration"] / 2.0
        if any(abs(center - other["center"]) < max(10.0, item["duration"] * 1.5) for other in selected):
            continue
        selected.append({**item, "center": center})
        if len(selected) >= limit:
            break
    return selected


def collect_from_hidden_edge(url: str) -> list:
    """Chạy Edge headless, kích hoạt player rồi đọc đường SVG heatmap."""
    try:
        from selenium import webdriver
        from selenium.webdriver.edge.options import Options as EdgeOptions
        from selenium.webdriver.chrome.options import Options as ChromeOptions
    except ImportError as exc:
        raise RuntimeError(
            "Cần Selenium. Cài bằng: python -m pip install selenium"
        ) from exc

    common_args = (
        "--headless=new", "--mute-audio",
        "--autoplay-policy=no-user-gesture-required", "--disable-gpu",
        "--disable-software-rasterizer", "--disable-extensions",
        "--disable-dev-shm-usage", "--no-sandbox", "--window-size=1280,720",
    )
    edge_options = EdgeOptions()
    for argument in common_args:
        edge_options.add_argument(argument)
    try:
        driver = webdriver.Edge(options=edge_options)
        browser_name = "Edge"
    except Exception as edge_error:
        print(f"Edge headless không mở được ({edge_error.__class__.__name__}) — thử Chrome ẩn...")
        chrome_options = ChromeOptions()
        for argument in common_args:
            chrome_options.add_argument(argument)
        driver = webdriver.Chrome(options=chrome_options)
        browser_name = "Chrome"
    print(f"Đã mở {browser_name} headless (không hiện cửa sổ).")
    try:
        driver.get(url)
        deadline = time.time() + 20
        paths = []
        duration = 0.0
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
                const values = [...document.querySelectorAll(
                    'path.ytp-modern-heat-map, path.ytp-heat-map-path, svg.ytp-heat-map-svg path'
                )].map(p => p.getAttribute('d')).filter(Boolean);
                return {paths: values, duration: video ? video.duration : 0};
            """) or {}
            paths = result.get("paths") or []
            duration = float(result.get("duration") or 0)
            if paths and duration > 1:
                break
            time.sleep(1)
        if not paths or duration <= 1:
            return []

        points = []
        for path in paths:
            for segment in re.findall(r"[Cc]\s*([^A-Za-z]+)", path):
                numbers = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", segment)]
                if len(numbers) < 6:
                    continue
                x, y = numbers[-2], numbers[-1]
                if 0 <= x <= 1005:
                    points.append((max(0.0, min(duration, x / 1000.0 * duration)), max(0.0, min(1.0, (100.0 - y) / 100.0))))
        unique = {}
        for timestamp, score in points:
            unique[round(timestamp, 3)] = max(score, unique.get(round(timestamp, 3), 0.0))
        ordered = sorted(unique.items())
        markers = []
        for index, (start, score) in enumerate(ordered):
            end = ordered[index + 1][0] if index + 1 < len(ordered) else min(duration, start + duration / 100.0)
            markers.append({"start": start, "duration": max(0.1, end - start), "score": score})
        return markers
    finally:
        driver.quit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Đọc biểu đồ Most replayed của YouTube")
    parser.add_argument("url", help="Link YouTube hoặc video ID")
    parser.add_argument("--top", type=int, default=10, help="Số mốc mạnh nhất (mặc định 10)")
    args = parser.parse_args()

    video_id = video_id_from_url(args.url)
    url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
    markers = []
    try:
        response = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }, timeout=25)
        response.raise_for_status()
        player = extract_player_response(response.text)
        collect_heat_markers(player, markers)
    except Exception as exc:
        print(f"Metadata tĩnh không dùng được ({exc})")
    if not markers:
        print("Metadata tĩnh không có heatmap — đang thử Edge chạy ẩn...")
        markers = collect_from_hidden_edge(url)
    if not markers:
        print("KHÔNG CÓ HEATMAP: Edge ẩn cũng không đọc được biểu đồ.")
        return 2

    top = strongest_non_overlapping(markers, max(1, args.top))
    print(f"Video ID: {video_id}")
    print(f"Đã đọc {len(markers)} điểm heatmap. TOP {len(top)} mốc mạnh nhất:\n")
    for index, item in enumerate(top, 1):
        end = item["start"] + item["duration"]
        print(
            f"{index:2d}. {format_time(item['start'])} - {format_time(end)}"
            f" | điểm {item['score'] * 100:6.2f}%"
        )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        raise SystemExit(1)
