"""
STUDIO HELPERS - Bộ công cụ dùng chung cho 3 Studio WYSIWYG 1:1
Hỗ trợ:
1. Bốc Frame từ Video cục bộ (OpenCV / FFmpeg).
2. Lấy Thumbnail / Bốc Frame từ Link YouTube hoặc Video ID trực tiếp.
3. Sinh ảnh mẫu rực rỡ 1920x1080 trực quan (0% màn hình đen).
4. Render thử nghiệm chuyển động 6s (FFmpeg) & Xem lại với FFplay.
"""
import os
import re
import sys
import time
import urllib.request
import subprocess
from typing import Optional, Tuple
from PIL import Image, ImageDraw, ImageFont
import cv2

SYSTEM_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_data")
os.makedirs(SYSTEM_DATA_DIR, exist_ok=True)

CACHE_FRAME_PATH = os.path.join(SYSTEM_DATA_DIR, "crop_preview_cache.jpg")
LAYOUT_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "layout_base.jpg")
COLOR_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "color_base.jpg")


def extract_youtube_id(url_or_id: str) -> Optional[str]:
    """Trích xuất 11 ký tự YouTube Video ID từ link hoặc chuỗi nhập vào."""
    if not url_or_id:
        return None
    url = url_or_id.strip()
    if re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url
    patterns = [
        r"(?:v=|\/|vi=)([a-zA-Z0-9_-]{11})",
        r"(?:shorts\/)([a-zA-Z0-9_-]{11})",
        r"(?:youtu\.be\/)([a-zA-Z0-9_-]{11})",
        r"(?:embed\/)([a-zA-Z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def fetch_youtube_sample(url_or_id: str, output_path: str = CACHE_FRAME_PATH) -> Optional[str]:
    """Tải thumbnail chất lượng cao hoặc bốc frame từ link YouTube."""
    vid_id = extract_youtube_id(url_or_id)
    if not vid_id:
        return None

    # 1. Thử tải ảnh bìa maxresdefault / hqdefault
    thumb_urls = [
        f"https://img.youtube.com/vi/{vid_id}/maxresdefault.jpg",
        f"https://img.youtube.com/vi/{vid_id}/sddefault.jpg",
        f"https://img.youtube.com/vi/{vid_id}/hqdefault.jpg"
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for t_url in thumb_urls:
        try:
            req = urllib.request.Request(t_url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = resp.read()
                if len(data) > 5000: # Đảm bảo không phải ảnh 1x1 placeholder
                    with open(output_path, "wb") as f:
                        f.write(data)
                    return output_path
        except Exception:
            continue

    # 2. Thử dùng yt-dlp + ffmpeg bốc frame tại 2s
    try:
        cmd = [
            "yt-dlp", "-g", "-f", "bestvideo[ext=mp4]/best[ext=mp4]/best",
            f"https://www.youtube.com/watch?v={vid_id}"
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10, creationflags=0x08000000 if os.name == "nt" else 0)
        if res.returncode == 0 and res.stdout.strip():
            stream_url = res.stdout.strip().split("\n")[0]
            ff_cmd = [
                "ffmpeg", "-y", "-ss", "00:00:02", "-i", stream_url,
                "-vframes", "1", "-q:v", "2", output_path
            ]
            subprocess.run(ff_cmd, capture_output=True, timeout=10, creationflags=0x08000000 if os.name == "nt" else 0)
            if os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
                return output_path
    except Exception:
        pass

    return None


def extract_frame_from_video(video_path: str, timestamp_sec: float = 2.0, output_path: str = CACHE_FRAME_PATH) -> Optional[str]:
    """Bốc frame từ file video MP4/MKV cục bộ tại giây timestamp_sec."""
    if not video_path or not os.path.isfile(video_path):
        return None
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            frame_num = int(timestamp_sec * fps)
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
            cap.release()
            if ret and frame is not None:
                cv2.imwrite(output_path, frame)
                return output_path
    except Exception as e:
        print(f"[STUDIO] OpenCV frame error: {e}")

    try:
        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp_sec), "-i", video_path,
            "-vframes", "1", "-q:v", "2", output_path
        ]
        subprocess.run(cmd, capture_output=True, timeout=5, creationflags=0x08000000 if os.name == "nt" else 0)
        if os.path.isfile(output_path) and os.path.getsize(output_path) > 1000:
            return output_path
    except Exception:
        pass
    return None


def generate_vibrant_sample_frame(output_path: str = CACHE_FRAME_PATH) -> str:
    """Tạo ảnh phôi mẫu FHD 1920x1080 trực quan, tương phản cao, có thanh đo màu."""
    w, h = 1920, 1080
    base = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(base)

    # Gradient hoàng hôn điện ảnh
    for y in range(h):
        r = int(24 + (230 - 24) * (y / h))
        g = int(24 + (70 - 24) * (y / h))
        b = int(70 + (25 - 70) * (y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b))

    # Dải màu Color Bars chuẩn ở chân
    bar_colors = [
        (255, 40, 40), (255, 140, 0), (255, 230, 0), (34, 197, 94),
        (6, 182, 212), (59, 130, 246), (168, 85, 247), (255, 255, 255), (100, 116, 139)
    ]
    bar_w = w // len(bar_colors)
    for i, col in enumerate(bar_colors):
        draw.rectangle([i * bar_w, h - 90, (i + 1) * bar_w, h], fill=col)

    # Khung trung tâm
    cx, cy = w // 2, h // 2
    draw.rounded_rectangle([cx - 440, cy - 250, cx + 440, cy + 250], radius=24, fill=(15, 23, 42), outline=(255, 255, 255), width=3)

    try:
        font_large = ImageFont.truetype("arialbd.ttf", 48)
        font_sub = ImageFont.truetype("arial.ttf", 28)
        font_tag = ImageFont.truetype("arialbd.ttf", 22)
    except Exception:
        font_large = ImageFont.load_default()
        font_sub = font_large
        font_tag = font_large

    draw.text((cx, cy - 100), "🎬 KHUNG HÌNH MẪU VIDEO GỐC (16:9)", fill=(255, 230, 0), anchor="mm", font=font_large)
    draw.text((cx, cy - 30), "Khung Hình Mẫu Trực Quan 1920 x 1080", fill=(255, 255, 255), anchor="mm", font=font_sub)
    draw.text((cx, cy + 50), "Xem trước tức thì Crop, Zoom Scale, Blur Background & Màu CapCut", fill=(200, 230, 255), anchor="mm", font=font_sub)

    draw.rectangle([60, 50, 280, 105], fill=(239, 68, 68))
    draw.text((170, 77), "LIVE PREVIEW", fill=(255, 255, 255), anchor="mm", font=font_tag)

    draw.rectangle([w - 300, 50, w - 60, 105], fill=(59, 130, 246))
    draw.text((w - 180, 77), "FHD 1080x1920", fill=(255, 255, 255), anchor="mm", font=font_tag)

    base.save(output_path, quality=95)
    return output_path


def get_sample_or_fallback_image(video_source: Optional[str] = None) -> str:
    """Hàm lấy ảnh phôi chuẩn: Video cục bộ -> Link YouTube -> anhmau.jpg -> Sinh mới."""
    if video_source:
        src = str(video_source).strip()
        if os.path.isfile(src):
            res = extract_frame_from_video(src, timestamp_sec=2.0)
            if res and os.path.isfile(res):
                return res
        elif "youtube.com" in src or "youtu.be" in src or re.match(r"^[a-zA-Z0-9_-]{11}$", src):
            res = fetch_youtube_sample(src)
            if res and os.path.isfile(res):
                return res

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(base_dir, "anhmau.jpg"),
        os.path.join(base_dir, "extracted_source_frame.jpg"),
        os.path.join(base_dir, "frame_test.jpg"),
        CACHE_FRAME_PATH
    ]
    for p in candidates:
        if os.path.isfile(p):
            try:
                im = Image.open(p)
                if im.size[0] >= 300 and im.size[1] >= 300:
                    return p
            except Exception:
                pass

    return generate_vibrant_sample_frame(CACHE_FRAME_PATH)
