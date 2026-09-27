# -*- coding: utf-8 -*-
"""
capcut_color_studio.py: Module Bảng Màu & Bộ Lọc CapCut Pro (Color Grading & Look Stack Studio)
ÁP DỤNG 100% BẢN THIẾT KỸ THUẬT CHI TIẾT TỪ PLAN_BANG_MAU_CAPCUT.md

Các tính năng cốt lõi:
1. 15 Thông Số Màu Gốc CapCut Pro:
   - temperature, tint, saturation, exposure, contrast, highlights, shadows, whites, blacks, brilliance ([-100, 100])
   - sharpen, clarity, grain, blur, vignette ([0, 100])
   - Cơ chế đồng bộ 2 chiều hoàn hảo (QSlider <-> QSpinBox không nút mũi tên).
2. Hệ Thống Xếp Chồng Bộ Lọc Look Stack (17 Presets CapCut):
   - 8K, HDR, Cinematic, Teal Orange, Vintage Film, Fade Soft, Moody Dark, Warm Golden, Cold Blue,
     Black & White, Dreamy Soft, Vibrant Punch, Night City, Sepia Classic, Bleach Bypass, Retro 80s, Food Fresh.
   - Xếp chồng nhiều layer, tùy chỉnh cường độ từng layer (0-100%), sửa cường độ, xóa layer, xóa hết.
   - Thuật toán trộn màu tuyến tính kết hợp Hue shift, Black & White mixer, Sepia mixer qua colorchannelmixer.
3. Cơ Chế Live Preview 9:16 Siêu Tốc (2-Stage Fast Pipeline & Debounce 180ms):
   - Stage 1: Trích xuất layout_base.jpg 9:16 (540x960) từ video mẫu tại start_sec (chỉ chạy khi đổi video/giây).
   - Stage 2: Đổ màu siêu tốc qua FFmpeg trực tiếp từ layout_base.jpg -> color_still.jpg trong ~60-90ms.
   - Debounce 180ms bằng QTimer chống giật lag khi kéo thanh trượt liên tục.
4. Render Clip Mẫu 6s & Trình Phát FFplay:
   - Render 6s clip 9:16 (540x960) từ giây start_sec qua FFmpeg (veryfast preset, -an).
   - Mở cửa sổ FFplay kích thước chuẩn 360x640 với cờ -autoexit để người dùng xem trực quan.
5. Hỗ trợ Palette Màu Chữ & Nền Tiêu Đề/Part tương thích 100% với radar_view.py.
"""

from __future__ import annotations

import os
import sys
import time
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, List

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QDoubleSpinBox, QComboBox,
    QListWidget, QListWidgetItem, QGroupBox, QScrollArea, QFrame,
    QFileDialog, QMessageBox, QLineEdit, QColorDialog, QSizePolicy,
    QAbstractSpinBox, QCheckBox
)
from PySide6.QtCore import Qt, QTimer, QThread, Signal, Slot, QRect, QSize
from PySide6.QtGui import QColor, QFont, QPixmap, QImage, QPainter, QPen, QBrush

import capcut_filters
from capcut_filters import (
    build_adjust_filters, apply_look, LOOKS, LOOK_NAMES, ADJUST_KEYS
)


def _get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = _get_app_dir()
TEMP_PREVIEW_DIR = APP_DIR / "temp" / "filter_preview"
TEMP_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

LAYOUT_BASE_PATH = TEMP_PREVIEW_DIR / "layout_base.jpg"
COLOR_STILL_PATH = TEMP_PREVIEW_DIR / "color_still.jpg"
PREVIEW_6S_PATH = TEMP_PREVIEW_DIR / "preview_6s.mp4"
SYSTEM_CACHE_PATH = APP_DIR / "system_data" / "crop_preview_cache.jpg"


def _build_pure_color_filter(options: dict) -> str:
    """Wrapper chuẩn gọi build_adjust_filters để tạo chuỗi FFmpeg filter 15 thông số + Look Stack"""
    return build_adjust_filters(options or {}, allow_blur_fx=True)


# ==============================================================================
# WORKER 1: TRÍCH XUẤT BASE FRAME 9:16 (STAGE 1 PIPELINE)
# ==============================================================================
class AsyncLayoutBaseExtractor(QThread):
    finished_sig = Signal(bool, str) # (success, message)

    def __init__(self, video_path: str, start_sec: float = 2.0, blur_bg: bool = True, zoom_in: float = 178.0, scale_x: float = 100.0, scale_y: float = 130.0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.start_sec = max(0.0, float(start_sec))
        self.blur_bg = bool(blur_bg)
        self.zoom_in = float(zoom_in)
        self.scale_x = float(scale_x)
        self.scale_y = float(scale_y)

    def run(self):
        try:
            TEMP_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
            ffmpeg_bin = "ffmpeg"
            dist_ffmpeg = APP_DIR / "bin" / "ffmpeg.exe"
            if dist_ffmpeg.exists():
                ffmpeg_bin = str(dist_ffmpeg)

            vp = self.video_path
            zoom_pct = float(self.zoom_in) / 100.0
            sx = float(self.scale_x) / 100.0
            sy = float(self.scale_y) / 100.0
            fg_w = int(540 * zoom_pct * sx)
            fg_h = int((540 * 1080 / 1920) * zoom_pct * sy)
            if fg_w % 2 != 0: fg_w += 1
            if fg_h % 2 != 0: fg_h += 1

            if getattr(self, "blur_bg", True):
                filter_layout_9x16 = (
                    "[0:v]split=2[orig][copy];"
                    "[copy]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,boxblur=20:10[bg];"
                    f"[orig]scale={fg_w}:{fg_h}[fg];"
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )
            else:
                filter_layout_9x16 = (
                    "color=c=black:s=540x960[bg];"
                    f"[0:v]scale={fg_w}:{fg_h}[fg];"
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )

            # Nếu có video thật hợp lệ
            if vp and os.path.exists(vp):
                cmd = [
                    ffmpeg_bin, "-hide_banner", "-y",
                    "-ss", f"{self.start_sec:.2f}",
                    "-i", str(vp),
                    "-vframes", "1",
                    "-filter_complex", filter_layout_9x16,
                    "-q:v", "2",
                    str(LAYOUT_BASE_PATH)
                ]
                sub_flags = 0x08000000 if os.name == 'nt' else 0
                ret = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=30)
                if ret.returncode == 0 and LAYOUT_BASE_PATH.exists() and LAYOUT_BASE_PATH.stat().st_size > 500:
                    self.finished_sig.emit(True, f"Đã trích xuất base frame 9:16 tại {self.start_sec:.1f}s")
                    return

            # Nếu có cache crop preview
            if SYSTEM_CACHE_PATH.exists() and SYSTEM_CACHE_PATH.stat().st_size > 500:
                cmd = [
                    ffmpeg_bin, "-hide_banner", "-y",
                    "-i", str(SYSTEM_CACHE_PATH),
                    "-filter_complex", filter_layout_9x16,
                    "-q:v", "2",
                    str(LAYOUT_BASE_PATH)
                ]
                sub_flags = 0x08000000 if os.name == 'nt' else 0
                ret = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=15)
                if ret.returncode == 0 and LAYOUT_BASE_PATH.exists():
                    self.finished_sig.emit(True, "Đã tạo base frame 9:16 từ crop cache")
                    return

            # Fallback tạo ảnh gradient mẫu 540x960
            cmd = [
                ffmpeg_bin, "-hide_banner", "-y",
                "-f", "lavfi",
                "-i", "testsrc=size=540x960:rate=1",
                "-vframes", "1",
                "-q:v", "2",
                str(LAYOUT_BASE_PATH)
            ]
            sub_flags = 0x08000000 if os.name == 'nt' else 0
            subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=15)
            if LAYOUT_BASE_PATH.exists():
                self.finished_sig.emit(True, "Đã tạo base frame mẫu tiêu chuẩn")
            else:
                self.finished_sig.emit(False, "Không thể tạo base frame")
        except Exception as e:
            self.finished_sig.emit(False, f"Lỗi trích xuất base frame: {e}")


# ==============================================================================
# WORKER 2: ĐỔ MÀU SIÊU TỐC LÊN ẢNH TĨNH (STAGE 2 PIPELINE - ~60-90ms)
# ==============================================================================
class AsyncColorGradeWorker(QThread):
    finished_sig = Signal(object, int, str) # (QPixmap or None, elapsed_ms, status_text)

    def __init__(self, color_opts: dict, parent=None):
        super().__init__(parent)
        self.color_opts = dict(color_opts or {})

    def run(self):
        t0 = time.time()
        try:
            if not LAYOUT_BASE_PATH.exists() or LAYOUT_BASE_PATH.stat().st_size < 500:
                self.finished_sig.emit(None, 0, "Chưa có layout base frame")
                return

            color_filter = _build_pure_color_filter(self.color_opts)
            ffmpeg_bin = "ffmpeg"
            dist_ffmpeg = APP_DIR / "bin" / "ffmpeg.exe"
            if dist_ffmpeg.exists():
                ffmpeg_bin = str(dist_ffmpeg)

            vf = color_filter if color_filter and color_filter != "null" else "null"
            cmd = [
                ffmpeg_bin, "-hide_banner", "-y",
                "-i", str(LAYOUT_BASE_PATH),
                "-vf", vf,
                "-q:v", "2",
                str(COLOR_STILL_PATH)
            ]
            sub_flags = 0x08000000 if os.name == 'nt' else 0
            ret = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=10)

            elapsed_ms = int((time.time() - t0) * 1000)
            if ret.returncode == 0 and COLOR_STILL_PATH.exists():
                pix = QPixmap(str(COLOR_STILL_PATH))
                if not pix.isNull():
                    self.finished_sig.emit(pix, elapsed_ms, f"🟢 Đã đổ màu tức thì ({elapsed_ms}ms)")
                    return

            self.finished_sig.emit(None, elapsed_ms, f"⚠️ Lỗi FFmpeg đổ màu ({elapsed_ms}ms)")
        except Exception as e:
            elapsed_ms = int((time.time() - t0) * 1000)
            self.finished_sig.emit(None, elapsed_ms, f"❌ Lỗi đổ màu: {e}")


# ==============================================================================
# WORKER 3: RENDER MẪU 6S VIDEO (FFMPEG)
# ==============================================================================
class Async6sSampleWorker(QThread):
    finished_sig = Signal(bool, str, str) # (success, video_path, message)

    def __init__(self, video_path: str, color_opts: dict, start_sec: float = 2.0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.color_opts = dict(color_opts or {})
        self.start_sec = max(0.0, float(start_sec))

    def run(self):
        try:
            vp = self.video_path
            if not vp or not os.path.exists(vp):
                self.finished_sig.emit(False, "", "Không tìm thấy file video mẫu")
                return

            color_filter = _build_pure_color_filter(self.color_opts)
            ffmpeg_bin = "ffmpeg"
            dist_ffmpeg = APP_DIR / "bin" / "ffmpeg.exe"
            if dist_ffmpeg.exists():
                ffmpeg_bin = str(dist_ffmpeg)

            zoom_pct = float(self.color_opts.get("zoom_in", 178.0)) / 100.0
            sx = float(self.color_opts.get("scale_x", 100.0)) / 100.0
            sy = float(self.color_opts.get("scale_y", 130.0)) / 100.0
            fg_w = int(540 * zoom_pct * sx)
            fg_h = int((540 * 1080 / 1920) * zoom_pct * sy)
            if fg_w % 2 != 0: fg_w += 1
            if fg_h % 2 != 0: fg_h += 1

            blur_bg = bool(self.color_opts.get("blur_bg", True))
            if blur_bg:
                filter_layout_9x16 = (
                    "[0:v]split=2[orig][copy];"
                    "[copy]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,boxblur=20:10[bg];"
                    f"[orig]scale={fg_w}:{fg_h}[fg];"
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )
            else:
                filter_layout_9x16 = (
                    "color=c=black:s=540x960[bg];"
                    f"[0:v]scale={fg_w}:{fg_h}[fg];"
                    "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                )
            if color_filter and color_filter != "null":
                fc_full = f"{filter_layout_9x16}[vlayout];[vlayout]{color_filter}[vout]"
            else:
                fc_full = f"{filter_layout_9x16}[vout]"

            try:
                from gpu_detector import get_preferred_video_encoder
                enc_type, enc_args = get_preferred_video_encoder(ffmpeg_bin)
            except Exception:
                enc_type, enc_args = "cpu", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "22"]

            cmd = [
                ffmpeg_bin, "-hide_banner", "-y",
                "-ss", f"{self.start_sec:.2f}",
                "-i", str(vp),
                "-t", "6",
                "-filter_complex", fc_full,
                "-map", "[vout]",
            ]
            cmd.extend(enc_args)
            cmd.extend([
                "-an",
                str(PREVIEW_6S_PATH)
            ])
            sub_flags = 0x08000000 if os.name == 'nt' else 0
            ret = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=120)

            # Fallback về CPU nếu GPU NVENC bị lỗi
            if (ret.returncode != 0 or not PREVIEW_6S_PATH.exists() or PREVIEW_6S_PATH.stat().st_size < 1000) and enc_type == "nvenc":
                cpu_cmd = [
                    ffmpeg_bin, "-hide_banner", "-y",
                    "-ss", f"{self.start_sec:.2f}",
                    "-i", str(vp),
                    "-t", "6",
                    "-filter_complex", fc_full,
                    "-map", "[vout]",
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-crf", "22",
                    "-an",
                    str(PREVIEW_6S_PATH)
                ]
                ret = subprocess.run(cpu_cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=sub_flags, timeout=120)

            if ret.returncode == 0 and PREVIEW_6S_PATH.exists() and PREVIEW_6S_PATH.stat().st_size > 1000:
                self.finished_sig.emit(True, str(PREVIEW_6S_PATH), "Render clip mẫu 6s thành công!")
            else:
                self.finished_sig.emit(False, "", f"Lỗi render mẫu 6s: {ret.stderr[-200:] if ret.stderr else ''}")
        except Exception as e:
            self.finished_sig.emit(False, "", f"Lỗi ngoại lệ render 6s: {e}")


# ==============================================================================
# WORKER 4: TẢI ẢNH MẪU YOUTUBE (NẾU DÁN LINK YOUTUBE)
# ==============================================================================
class YouTubeSampleFetcher(QThread):
    finished_sig = Signal(bool, str) # (success, message)

    def __init__(self, url_or_id: str, blur_bg: bool = True, zoom_in: float = 178.0, scale_x: float = 100.0, scale_y: float = 130.0, parent=None):
        super().__init__(parent)
        self.url_or_id = url_or_id.strip()
        self.blur_bg = bool(blur_bg)
        self.zoom_in = float(zoom_in)
        self.scale_x = float(scale_x)
        self.scale_y = float(scale_y)

    def run(self):
        try:
            import re
            m = re.search(r"(?:v=|\/|youtu\.be\/)([0-9A-Za-z_-]{11})", self.url_or_id)
            vid = m.group(1) if m else self.url_or_id
            if len(vid) != 11:
                self.finished_sig.emit(False, "Link YouTube không đúng định dạng")
                return

            thumb_urls = [
                f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
                f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
            ]
            loaded_data = None
            for tu in thumb_urls:
                try:
                    req = urllib.request.Request(tu, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        if resp.status == 200:
                            loaded_data = resp.read()
                            if len(loaded_data) > 5000:
                                break
                except Exception:
                    continue

            if loaded_data:
                temp_thumb = TEMP_PREVIEW_DIR / "yt_thumb_raw.jpg"
                with open(temp_thumb, "wb") as f:
                    f.write(loaded_data)

                ffmpeg_bin = "ffmpeg"
                dist_ffmpeg = APP_DIR / "bin" / "ffmpeg.exe"
                if dist_ffmpeg.exists():
                    ffmpeg_bin = str(dist_ffmpeg)

                zoom_pct = float(self.zoom_in) / 100.0
                sx = float(self.scale_x) / 100.0
                sy = float(self.scale_y) / 100.0
                fg_w = int(540 * zoom_pct * sx)
                fg_h = int((540 * 1080 / 1920) * zoom_pct * sy)
                if fg_w % 2 != 0: fg_w += 1
                if fg_h % 2 != 0: fg_h += 1

                if getattr(self, "blur_bg", True):
                    filter_layout_9x16 = (
                        "[0:v]split=2[orig][copy];"
                        "[copy]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,boxblur=20:10[bg];"
                        f"[orig]scale={fg_w}:{fg_h}[fg];"
                        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                    )
                else:
                    filter_layout_9x16 = (
                        "color=c=black:s=540x960[bg];"
                        f"[0:v]scale={fg_w}:{fg_h}[fg];"
                        "[bg][fg]overlay=(W-w)/2:(H-h)/2"
                    )
                cmd = [
                    ffmpeg_bin, "-hide_banner", "-y",
                    "-i", str(temp_thumb),
                    "-filter_complex", filter_layout_9x16,
                    "-q:v", "2",
                    str(LAYOUT_BASE_PATH)
                ]
                sub_flags = 0x08000000 if os.name == 'nt' else 0
                subprocess.run(cmd, capture_output=True, text=True, creationflags=sub_flags, timeout=10)

                if LAYOUT_BASE_PATH.exists():
                    self.finished_sig.emit(True, f"Đã nạp frame mẫu từ YouTube: {vid}")
                    return

            self.finished_sig.emit(False, "Không thể tải thumbnail YouTube")
        except Exception as e:
            self.finished_sig.emit(False, f"Lỗi tải YouTube: {e}")


# ==============================================================================
# CANVARS HIỂN THỊ PREVIEW 9:16 (RIGHT PANEL)
# ==============================================================================
class ColorCanvasWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap: Optional[QPixmap] = None
        self.setMinimumSize(320, 540)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #11111b; border-radius: 8px;")

    def set_pixmap(self, pix: Optional[QPixmap]):
        self.pixmap = pix
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        # Vẽ nền tối
        painter.fillRect(self.rect(), QColor("#11111b"))

        # Tính toán khung chữ nhật chuẩn tỉ lệ 9:16
        w_avail = self.width() - 16
        h_avail = self.height() - 16
        target_ratio = 9.0 / 16.0

        if w_avail / h_avail > target_ratio:
            draw_h = int(h_avail)
            draw_w = int(draw_h * target_ratio)
        else:
            draw_w = int(w_avail)
            draw_h = int(draw_w / target_ratio)

        ox = (self.width() - draw_w) // 2
        oy = (self.height() - draw_h) // 2
        dest_rect = QRect(ox, oy, draw_w, draw_h)

        if self.pixmap and not self.pixmap.isNull():
            painter.drawPixmap(dest_rect, self.pixmap)
        else:
            painter.fillRect(dest_rect, QColor("#1e1e2e"))
            painter.setPen(QPen(QColor("#6c7086"), 1, Qt.DashLine))
            painter.drawRect(dest_rect)
            painter.setPen(QColor("#a6adc8"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(dest_rect, Qt.AlignCenter, "Chưa có ảnh xem trước 9:16\n(Vui lòng chọn video mẫu hoặc nạp frame)")

        # Bo viền neon tinh tế
        painter.setPen(QPen(QColor("#89b4fa"), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(dest_rect, 4, 4)

        # Huy hiệu tỉ lệ 9:16
        badge_w, badge_h = 74, 24
        badge_rect = QRect(ox + 8, oy + 8, badge_w, badge_h)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(17, 17, 27, 200))
        painter.drawRoundedRect(badge_rect, 4, 4)
        painter.setPen(QColor("#f9e2af"))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(badge_rect, Qt.AlignCenter, "9:16 PRO")


# ==============================================================================
# HỘP THOẠI CHÍNH: CAPCUT COLOR STUDIO & LOOK STACK (100% PLAN_BANG_MAU_CAPCUT.md)
# ==============================================================================
class CapCutColorStudioDialog(QDialog):
    """
    Studio Bảng Màu & Bộ Lọc CapCut Pro (Color Grading & Look Stack)
    Chuẩn xác 100% tài liệu PLAN_BANG_MAU_CAPCUT.md
    """
    def __init__(self, current_data: dict = None, sample_video_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("🎨 CapCut Pro — Studio Bảng Màu & Bộ Lọc (Color Grading & Look Stack)")
        self.resize(1260, 860)
        self.setMinimumSize(1100, 750)

        self.current_colors = dict(current_data or {})
        self.sample_video_path = sample_video_path or ""
        self.look_stack: List[dict] = capcut_filters.normalize_stack(self.current_colors.get("color_look_stack", []))

        # Nếu chưa có stack nhưng có color_look cũ thì khởi tạo layer đầu tiên
        if not self.look_stack:
            old_look = self.current_colors.get("color_look", "8K")
            old_int = self.current_colors.get("color_look_intensity", 70)
            if old_look and old_look != "Không lọc" and old_look in LOOKS:
                self.look_stack = [{"name": old_look, "intensity": float(old_int)}]

        # 15 thông số màu gốc
        self.sliders: Dict[str, QSlider] = {}
        self.spinboxes: Dict[str, QSpinBox] = {}

        # Danh sách định nghĩa 15 thông số màu
        self.param_defs = [
            ("temperature", "🌡️ Nhiệt độ (Temperature)", -100, 100, 0),
            ("tint", "🎨 Tông màu (Tint)", -100, 100, 0),
            ("saturation", "🌈 Độ bão hòa (Saturation)", -100, 100, 0),
            ("exposure", "☀️ Phơi sáng (Exposure)", -100, 100, 0),
            ("contrast", "🌓 Tương phản (Contrast)", -100, 100, 0),
            ("highlights", "💡 Vùng sáng (Highlights)", -100, 100, 0),
            ("shadows", "👤 Bóng (Shadows)", -100, 100, 0),
            ("whites", "⚪ Vùng trắng (Whites)", -100, 100, 0),
            ("blacks", "⚫ Vùng đen (Blacks)", -100, 100, 0),
            ("brilliance", "✨ Độ chói (Brilliance)", -100, 100, 0),
            ("sharpen", "🔪 Sắc nét (Sharpen)", 0, 100, 0),
            ("clarity", "🔍 Rõ nét (Clarity)", 0, 100, 0),
            ("grain", "🎞️ Hạt nhỏ (Grain)", 0, 100, 0),
            ("blur", "💧 Làm mờ (Blur)", 0, 100, 0),
            ("vignette", "🔘 Viền mờ dần (Vignette)", 0, 100, 0),
        ]

        # Khởi tạo giá trị ban đầu cho 15 keys
        for key, name, min_v, max_v, def_v in self.param_defs:
            if key not in self.current_colors:
                self.current_colors[key] = def_v

        # Palette màu chữ/nền Tiêu đề và Part được giữ pass-through từ current_colors
        # (Chỉnh màu tiêu đề/part qua nút riêng trong toolbar chính)

        # Debounce Timer 180ms
        self.debounce_timer = QTimer(self)
        self.debounce_timer.setSingleShot(True)
        self.debounce_timer.timeout.connect(self._execute_color_grading_preview)

        # Debounce Timer 250ms cho việc cập nhật khung hình (Zoom/Scale/Blur)
        self._frame_debounce = QTimer(self)
        self._frame_debounce.setSingleShot(True)
        self._frame_debounce.setInterval(250)
        self._frame_debounce.timeout.connect(self._extract_base_frame)

        # Worker references
        self._base_worker: Optional[AsyncLayoutBaseExtractor] = None
        self._color_worker: Optional[AsyncColorGradeWorker] = None
        self._sample6s_worker: Optional[Async6sSampleWorker] = None
        self._yt_worker: Optional[YouTubeSampleFetcher] = None

        # Build UI
        self._setup_dark_theme()
        self._init_ui()

        # Nạp Base Frame & Đổ Màu Lần Đầu Tiên
        QTimer.singleShot(100, self._initial_load)

    # --------------------------------------------------------------------------
    # GIAO DIỆN DARK THEME CHUẨN HIỆN ĐẠI
    # --------------------------------------------------------------------------
    def _setup_dark_theme(self):
        self.setStyleSheet("""
            QDialog {
                background-color: #1e1e2e;
                color: #cdd6f4;
                font-family: 'Segoe UI', Arial, sans-serif;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #45475a;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 14px;
                background-color: #181825;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                padding: 0 8px;
                background-color: #181825;
            }
            QLabel {
                color: #cdd6f4;
                font-size: 12px;
            }
            QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 12px;
            }
            /* Bỏ nút mũi tên lên xuống trên QSpinBox chống bóp méo */
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                height: 0px;
                border: none;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QSlider::groove:horizontal {
                height: 6px;
                background: #313244;
                border-radius: 3px;
            }
            QSlider::sub-page:horizontal {
                background: #89b4fa;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #f5e0dc;
                width: 14px;
                margin-top: -4px;
                margin-bottom: -4px;
                border-radius: 7px;
            }
            QSlider::handle:horizontal:hover {
                background: #f9e2af;
            }
            QListWidget {
                background-color: #11111b;
                border: 1px solid #45475a;
                border-radius: 6px;
                color: #cdd6f4;
                padding: 4px;
            }
            QListWidget::item {
                padding: 6px 10px;
                border-radius: 4px;
                margin-bottom: 2px;
            }
            QListWidget::item:selected {
                background-color: #45475a;
                color: #89b4fa;
                font-weight: bold;
            }
            QScrollArea {
                border: 1px solid #313244;
                background: transparent;
            }
        """)

    # --------------------------------------------------------------------------
    # KHỞI TẠO BỐ CỤC 2 CỘT (LEFT / RIGHT)
    # --------------------------------------------------------------------------
    def _init_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(14)

        # ======================================================================
        # CỘT TRÁI (LEFT PANEL: 520px)
        # ======================================================================
        left_widget = QWidget()
        left_widget.setFixedWidth(520)
        l_layout = QVBoxLayout(left_widget)
        l_layout.setContentsMargins(0, 0, 0, 0)
        l_layout.setSpacing(10)

        # KHỐI 1: THÔNG SỐ KHUNG HÌNH (ĐỒNG BỘ CONFIG RENDER)
        gb_frame = QGroupBox("🔍 THÔNG SỐ KHUNG HÌNH (ĐỒNG BỘ CONFIG RENDER)")
        gb_frame.setStyleSheet("QGroupBox { color: #89dceb; font-weight: bold; }")
        fl_frame = QFormLayout(gb_frame)
        fl_frame.setContentsMargins(10, 10, 10, 6)
        fl_frame.setSpacing(6)

        h_zoom = QHBoxLayout()
        self.sp_zoom_in = QDoubleSpinBox()
        self.sp_zoom_in.setRange(50.0, 300.0)
        self.sp_zoom_in.setSingleStep(1.0)
        self.sp_zoom_in.setDecimals(1)
        self.sp_zoom_in.setSuffix("%")
        self.sp_zoom_in.setValue(float(self.current_colors.get("zoom_in", 178.0)))
        self.sp_zoom_in.valueChanged.connect(self._frame_debounce.start)
        h_zoom.addWidget(self.sp_zoom_in)

        self.chk_blur_bg = QCheckBox("Làm mờ nền 2 đầu (Blur BG)")
        self.chk_blur_bg.setChecked(bool(self.current_colors.get("blur_bg", True)))
        self.chk_blur_bg.toggled.connect(self._frame_debounce.start)
        h_zoom.addWidget(self.chk_blur_bg)
        fl_frame.addRow("Zoom-in & Nền:", h_zoom)

        h_scale = QHBoxLayout()
        self.sp_scale_x = QDoubleSpinBox()
        self.sp_scale_x.setRange(50.0, 200.0)
        self.sp_scale_x.setSingleStep(1.0)
        self.sp_scale_x.setDecimals(1)
        self.sp_scale_x.setSuffix("%")
        self.sp_scale_x.setValue(float(self.current_colors.get("scale_x", 100.0)))
        self.sp_scale_x.valueChanged.connect(self._frame_debounce.start)
        h_scale.addWidget(QLabel("Rộng (X):"))
        h_scale.addWidget(self.sp_scale_x)

        self.sp_scale_y = QDoubleSpinBox()
        self.sp_scale_y.setRange(50.0, 200.0)
        self.sp_scale_y.setSingleStep(1.0)
        self.sp_scale_y.setDecimals(1)
        self.sp_scale_y.setSuffix("%")
        self.sp_scale_y.setValue(float(self.current_colors.get("scale_y", 130.0)))
        self.sp_scale_y.valueChanged.connect(self._frame_debounce.start)
        h_scale.addWidget(QLabel("Dài (Y):"))
        h_scale.addWidget(self.sp_scale_y)
        fl_frame.addRow("Tỉ lệ X / Y:", h_scale)

        l_layout.addWidget(gb_frame)

        # KHỐI 2 (trước là khối 2): QUẢN LÝ BỘ LỌC LOOK STACK (17 PRESETS)
        gb_look = QGroupBox("✨ BỘ LỌC XẾP CHỒNG (LOOK STACK ENGINE - 17 PRESETS)")
        gb_look.setStyleSheet("QGroupBox { color: #89b4fa; }")
        v_look = QVBoxLayout(gb_look)
        v_look.setSpacing(8)

        # Hàng chọn Look & Cường độ
        h_add = QHBoxLayout()
        h_add.addWidget(QLabel("Preset:"))
        self.combo_look = QComboBox()
        self.combo_look.setFixedWidth(140)
        self.combo_look.addItems([k for k in LOOK_NAMES if k != "Không lọc"])
        h_add.addWidget(self.combo_look)

        h_add.addWidget(QLabel("Cường độ:"))
        self.slider_look_int = QSlider(Qt.Horizontal)
        self.slider_look_int.setRange(10, 100)
        self.slider_look_int.setValue(70)
        self.sp_look_int = QSpinBox()
        self.sp_look_int.setRange(10, 100)
        self.sp_look_int.setValue(70)
        self.sp_look_int.setFixedWidth(46)
        self.sp_look_int.setSuffix("%")

        # Đồng bộ cường độ add
        self.slider_look_int.valueChanged.connect(self.sp_look_int.setValue)
        self.sp_look_int.valueChanged.connect(self.slider_look_int.setValue)

        h_add.addWidget(self.slider_look_int)
        h_add.addWidget(self.sp_look_int)

        self.btn_add_look = QPushButton("➕ Thêm")
        self.btn_add_look.setFixedHeight(28)
        self.btn_add_look.setCursor(Qt.PointingHandCursor)
        self.btn_add_look.setStyleSheet("background-color: #a6e3a1; color: #11111b; font-weight: bold; border-radius: 4px; padding: 0 10px;")
        self.btn_add_look.clicked.connect(self._add_look_layer)
        h_add.addWidget(self.btn_add_look)
        v_look.addLayout(h_add)

        # Danh sách layer đang xếp chồng
        self.list_stack = QListWidget()
        self.list_stack.setFixedHeight(84)
        v_look.addWidget(self.list_stack)

        # Hàng nút thao tác danh sách stack
        h_stack_btns = QHBoxLayout()
        self.btn_edit_look = QPushButton("✏️ Đổi Cường Độ")
        self.btn_edit_look.setStyleSheet("background-color: #313244; color: #f9e2af; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px;")
        self.btn_edit_look.clicked.connect(self._edit_selected_look)
        h_stack_btns.addWidget(self.btn_edit_look)

        self.btn_del_look = QPushButton("🗑️ Xóa Layer")
        self.btn_del_look.setStyleSheet("background-color: #313244; color: #f38ba8; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px;")
        self.btn_del_look.clicked.connect(self._delete_selected_look)
        h_stack_btns.addWidget(self.btn_del_look)

        self.btn_clear_look = QPushButton("🧹 Xóa Hết")
        self.btn_clear_look.setStyleSheet("background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 4px 8px;")
        self.btn_clear_look.clicked.connect(self._clear_all_looks)
        h_stack_btns.addWidget(self.btn_clear_look)
        v_look.addLayout(h_stack_btns)

        l_layout.addWidget(gb_look)

        # KHỐI 3: 15 THÔNG SỐ MÀU GỐC TRONG VÙNG CUỘN (QSCROLLAREA)
        gb_params = QGroupBox("🎛️ 15 THÔNG SỐ MÀU GỐC CAPCUT PRO (ĐỒNG BỘ 2 CHIỀU)")
        gb_params.setStyleSheet("QGroupBox { color: #fab387; }")
        v_params_box = QVBoxLayout(gb_params)
        v_params_box.setContentsMargins(6, 12, 6, 6)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(4, 4, 8, 4)
        scroll_layout.setSpacing(6)

        for key, title, min_val, max_val, def_val in self.param_defs:
            row_w = self._create_param_row(key, title, min_val, max_val, self.current_colors.get(key, def_val))
            scroll_layout.addWidget(row_w)

        scroll_layout.addStretch()
        scroll_area.setWidget(scroll_content)
        v_params_box.addWidget(scroll_area)
        l_layout.addWidget(gb_params, 1)

        # KHỐI 4: HÀNG NÚT LƯU & RESET
        h_bottom_btns = QHBoxLayout()
        self.btn_reset = QPushButton("↩ Reset Mặc Định")
        self.btn_reset.setFixedHeight(38)
        self.btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #cdd6f4; font-weight: bold;
                border: 1px solid #45475a; border-radius: 6px; font-size: 13px;
            }
            QPushButton:hover { background-color: #45475a; }
        """)
        self.btn_reset.clicked.connect(self._reset_to_defaults)
        h_bottom_btns.addWidget(self.btn_reset, 1)

        self.btn_save = QPushButton("💾 LƯU CẤU HÌNH MÀU")
        self.btn_save.setFixedHeight(38)
        self.btn_save.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a6e3a1, stop:1 #94e2d5);
                color: #11111b; font-weight: bold; border-radius: 6px; font-size: 13px;
            }
            QPushButton:hover { background: #b4befe; }
        """)
        self.btn_save.clicked.connect(self.accept)
        h_bottom_btns.addWidget(self.btn_save, 2)
        l_layout.addLayout(h_bottom_btns)

        main_layout.addWidget(left_widget)

        # ======================================================================
        # CỘT PHẢI (RIGHT PANEL: PREVIEW 9:16 & KIỂM THỬ)
        # ======================================================================
        right_widget = QWidget()
        r_layout = QVBoxLayout(right_widget)
        r_layout.setContentsMargins(0, 0, 0, 0)
        r_layout.setSpacing(10)

        # KHUNG CANVAS PREVIEW 9:16
        self.canvas = ColorCanvasWidget()
        r_layout.addWidget(self.canvas, 1)

        # KHỐI ĐIỀU KHIỂN NGUỒN VIDEO & THỜI ĐIỂM
        gb_source = QGroupBox("🎬 NGUỒN VIDEO MẪU & THỜI ĐIỂM BỐC FRAME")
        gb_source.setStyleSheet("QGroupBox { color: #cba6f7; }")
        v_source = QVBoxLayout(gb_source)
        v_source.setSpacing(8)

        # File video mẫu
        h_f = QHBoxLayout()
        h_f.addWidget(QLabel("File Video:"))
        self.txt_video_path = QLineEdit(self.sample_video_path)
        self.txt_video_path.setPlaceholderText("Đường dẫn file video mp4...")
        h_f.addWidget(self.txt_video_path, 1)
        self.btn_browse = QPushButton("📁 Chọn File...")
        self.btn_browse.setStyleSheet("background-color: #313244; border: 1px solid #45475a; padding: 4px 10px; border-radius: 4px;")
        self.btn_browse.clicked.connect(self._browse_video)
        h_f.addWidget(self.btn_browse)
        v_source.addLayout(h_f)

        # Dán link YouTube mẫu
        h_yt = QHBoxLayout()
        h_yt.addWidget(QLabel("Link YouTube:"))
        self.txt_yt_link = QLineEdit()
        self.txt_yt_link.setPlaceholderText("Dán link YouTube (https://youtu.be/...) để bốc mẫu...")
        h_yt.addWidget(self.txt_yt_link, 1)
        self.btn_fetch_yt = QPushButton("⚡ Bốc Mẫu YouTube")
        self.btn_fetch_yt.setStyleSheet("background-color: #fab387; color: #11111b; font-weight: bold; border-radius: 4px; padding: 4px 10px;")
        self.btn_fetch_yt.clicked.connect(self._fetch_youtube_sample)
        h_yt.addWidget(self.btn_fetch_yt)
        v_source.addLayout(h_yt)

        # Giây bắt đầu & Nút trích xuất lại
        h_sec = QHBoxLayout()
        h_sec.addWidget(QLabel("Giây bắt đầu (start_sec):"))
        self.sp_start_sec = QDoubleSpinBox()
        self.sp_start_sec.setRange(0.0, 3600.0)
        self.sp_start_sec.setValue(2.0)
        self.sp_start_sec.setSingleStep(0.5)
        self.sp_start_sec.setFixedWidth(80)
        self.sp_start_sec.setSuffix("s")
        h_sec.addWidget(self.sp_start_sec)

        self.btn_reload_base = QPushButton("🔄 Nạp Lại Base Frame")
        self.btn_reload_base.setStyleSheet("background-color: #89b4fa; color: #11111b; font-weight: bold; border-radius: 4px; padding: 4px 12px;")
        self.btn_reload_base.clicked.connect(self._extract_base_frame)
        h_sec.addWidget(self.btn_reload_base)
        h_sec.addStretch()
        v_source.addLayout(h_sec)

        r_layout.addWidget(gb_source)

        # KHỐI KIỂM THỬ: STATUS + RENDER 6S + FFPLAY
        gb_test = QGroupBox("🚀 KIỂM THỬ CHUYỂN ĐỘNG & ĐỘ NÉT THỰC TẾ")
        gb_test.setStyleSheet("QGroupBox { color: #94e2d5; }")
        v_test = QVBoxLayout(gb_test)
        v_test.setSpacing(8)

        self.lbl_status = QLabel("🟢 Sẵn sàng")
        self.lbl_status.setStyleSheet("color: #a6e3a1; font-weight: bold;")
        v_test.addWidget(self.lbl_status)

        h_test_btns = QHBoxLayout()
        self.btn_render_6s = QPushButton("▶ RENDER MẪU 6S (FFMPEG)")
        self.btn_render_6s.setFixedHeight(38)
        self.btn_render_6s.setCursor(Qt.PointingHandCursor)
        self.btn_render_6s.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #89b4fa, stop:1 #b4befe);
                color: #11111b; font-weight: bold; border-radius: 6px; font-size: 12px;
            }
            QPushButton:hover { background: #cdd6f4; }
        """)
        self.btn_render_6s.clicked.connect(self._render_6s_sample)
        h_test_btns.addWidget(self.btn_render_6s, 1)

        self.btn_play_6s = QPushButton("⏯ PHÁT CLIP 6S (FFPLAY)")
        self.btn_play_6s.setFixedHeight(38)
        self.btn_play_6s.setCursor(Qt.PointingHandCursor)
        self.btn_play_6s.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #cba6f7, stop:1 #f5c2e7);
                color: #11111b; font-weight: bold; border-radius: 6px; font-size: 12px;
            }
            QPushButton:hover { background: #f5e0dc; }
        """)
        self.btn_play_6s.clicked.connect(self._play_6s_sample)
        h_test_btns.addWidget(self.btn_play_6s, 1)
        v_test.addLayout(h_test_btns)

        r_layout.addWidget(gb_test)
        main_layout.addWidget(right_widget, 1)

        # Cập nhật danh sách stack lên UI
        self._refresh_stack_list_ui()

    # --------------------------------------------------------------------------
    # KHỞI TẠO TỪNG DÒNG THÔNG SỐ (SLIDER <-> SPINBOX BI-DIRECTIONAL)
    # --------------------------------------------------------------------------
    def _create_param_row(self, key: str, title: str, min_val: int, max_val: int, init_val: int) -> QWidget:
        w = QWidget()
        layout = QHBoxLayout(w)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(8)

        lbl = QLabel(title)
        lbl.setFixedWidth(190)
        layout.addWidget(lbl)

        slider = QSlider(Qt.Horizontal)
        slider.setRange(min_val, max_val)
        slider.setValue(int(init_val))
        self.sliders[key] = slider

        spinbox = QSpinBox()
        spinbox.setRange(min_val, max_val)
        spinbox.setValue(int(init_val))
        spinbox.setFixedWidth(54)
        spinbox.setAlignment(Qt.AlignCenter)
        self.spinboxes[key] = spinbox

        # Đồng bộ 2 chiều (Bi-directional Binding)
        def on_slider(val):
            spinbox.blockSignals(True)
            spinbox.setValue(val)
            spinbox.blockSignals(False)
            self._schedule_color_refresh()

        def on_spin(val):
            slider.blockSignals(True)
            slider.setValue(val)
            slider.blockSignals(False)
            self._schedule_color_refresh()

        slider.valueChanged.connect(on_slider)
        spinbox.valueChanged.connect(on_spin)

        layout.addWidget(slider, 1)
        layout.addWidget(spinbox)
        return w

    # --------------------------------------------------------------------------
    # KHỞI TẠO NÚT CHỌN MÀU PALETTE (HEX PICKER)
    # --------------------------------------------------------------------------
    def _create_color_picker_btn(self, hex_val: str, key_name: str) -> QPushButton:
        btn = QPushButton(hex_val)
        btn.setFixedHeight(28)
        self._update_color_btn_style(btn, hex_val)
        btn.clicked.connect(lambda: self._pick_color(btn, key_name))
        return btn

    def _update_color_btn_style(self, btn: QPushButton, hex_val: str):
        col = QColor(hex_val)
        text_col = "#000000" if col.lightness() > 128 else "#FFFFFF"
        btn.setText(hex_val)
        btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {hex_val}; color: {text_col};
                font-weight: bold; border-radius: 4px; border: 1px solid #45475a;
            }}
        """)

    def _pick_color(self, btn: QPushButton, key_name: str):
        init_col = QColor(self.palette_colors.get(key_name, "#FFFFFF"))
        picked = QColorDialog.getColor(init_col, self, f"Chọn màu cho {key_name}")
        if picked.isValid():
            hex_str = picked.name().upper()
            self.palette_colors[key_name] = hex_str
            self._update_color_btn_style(btn, hex_str)

    # --------------------------------------------------------------------------
    # QUẢN LÝ LOOK STACK (THÊM / SỬA / XÓA)
    # --------------------------------------------------------------------------
    def _refresh_stack_list_ui(self):
        self.list_stack.clear()
        if not self.look_stack:
            item = QListWidgetItem("⚪ Chưa có lớp Look nào (Không lọc)")
            item.setFlags(Qt.NoItemFlags)
            self.list_stack.addItem(item)
            return

        for idx, layer in enumerate(self.look_stack):
            name = layer.get("name", "8K")
            intensity = int(layer.get("intensity", 70))
            text = f"✨ Lớp #{idx+1}: {name} ({intensity}%)"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, idx)
            self.list_stack.addItem(item)

    def _add_look_layer(self):
        name = self.combo_look.currentText()
        intensity = self.slider_look_int.value()

        # Kiểm tra xem Look này đã có trong stack chưa, nếu có thì cộng dồn hoặc update
        existing = [l for l in self.look_stack if l["name"] == name]
        if existing:
            existing[0]["intensity"] = intensity
        else:
            self.look_stack.append({"name": name, "intensity": intensity})

        self._refresh_stack_list_ui()
        self._schedule_color_refresh()

    def _edit_selected_look(self):
        curr_row = self.list_stack.currentRow()
        if curr_row < 0 or curr_row >= len(self.look_stack):
            return
        layer = self.look_stack[curr_row]
        new_int = self.slider_look_int.value()
        layer["intensity"] = new_int
        self._refresh_stack_list_ui()
        self._schedule_color_refresh()

    def _delete_selected_look(self):
        curr_row = self.list_stack.currentRow()
        if curr_row < 0 or curr_row >= len(self.look_stack):
            return
        del self.look_stack[curr_row]
        self._refresh_stack_list_ui()
        self._schedule_color_refresh()

    def _clear_all_looks(self):
        self.look_stack.clear()
        self._refresh_stack_list_ui()
        self._schedule_color_refresh()

    # --------------------------------------------------------------------------
    # THU THẬP TẤT CẢ OPTIONS CẤU HÌNH HIỆN TẠI
    # --------------------------------------------------------------------------
    def get_values(self) -> dict:
        # Pass-through palette màu từ current_colors (không thay đổi)
        result = {
            "title_txt": self.current_colors.get("title_txt", "#FFFFFF"),
            "title_bg": self.current_colors.get("title_bg", "#FF2D55"),
            "part_txt": self.current_colors.get("part_txt", "#FFFFFF"),
            "part_bg": self.current_colors.get("part_bg", "#000000"),
        }
        for key, s in self.sliders.items():
            result[key] = s.value()

        result["color_look_stack"] = list(self.look_stack)
        result["blur_bg"] = self.chk_blur_bg.isChecked() if hasattr(self, "chk_blur_bg") else bool(self.current_colors.get("blur_bg", True))
        result["zoom_in"] = self.sp_zoom_in.value() if hasattr(self, "sp_zoom_in") else float(self.current_colors.get("zoom_in", 178.0))
        result["scale_x"] = self.sp_scale_x.value() if hasattr(self, "sp_scale_x") else float(self.current_colors.get("scale_x", 100.0))
        result["scale_y"] = self.sp_scale_y.value() if hasattr(self, "sp_scale_y") else float(self.current_colors.get("scale_y", 130.0))
        # Giữ tương thích ngược với hệ thống cũ
        if self.look_stack:
            result["color_look"] = self.look_stack[0]["name"]
            result["color_look_intensity"] = self.look_stack[0]["intensity"]
        else:
            result["color_look"] = "Không lọc"
            result["color_look_intensity"] = 0

        return result

    def _reset_to_defaults(self):
        for key, title, min_val, max_val, def_val in self.param_defs:
            if key in self.sliders:
                self.sliders[key].setValue(def_val)
            if key in self.spinboxes:
                self.spinboxes[key].setValue(def_val)

        self.look_stack = [{"name": "8K", "intensity": 70}]
        self._refresh_stack_list_ui()
        self._schedule_color_refresh()

    # --------------------------------------------------------------------------
    # TIẾN TRÌNH LIVE PREVIEW 2-STAGE & DEBOUNCE 180ms
    # --------------------------------------------------------------------------
    def _initial_load(self):
        self._extract_base_frame()

    def _browse_video(self):
        fpath, _ = QFileDialog.getOpenFileName(
            self, "Chọn Video Mẫu", str(APP_DIR),
            "Video Files (*.mp4 *.mkv *.mov *.avi *.webm);;All Files (*.*)"
        )
        if fpath:
            self.sample_video_path = fpath
            self.txt_video_path.setText(fpath)
            self._extract_base_frame()

    def _fetch_youtube_sample(self):
        url = self.txt_yt_link.text().strip()
        if not url:
            QMessageBox.warning(self, "Thiếu link", "Vui lòng dán link YouTube để bốc frame mẫu!")
            return

        self.lbl_status.setText("⏳ Đang tải thumbnail YouTube...")
        self.btn_fetch_yt.setEnabled(False)

        blur_bg = self.chk_blur_bg.isChecked() if hasattr(self, "chk_blur_bg") else bool(self.current_colors.get("blur_bg", True))
        zoom_in = self.sp_zoom_in.value() if hasattr(self, "sp_zoom_in") else float(self.current_colors.get("zoom_in", 178.0))
        scale_x = self.sp_scale_x.value() if hasattr(self, "sp_scale_x") else float(self.current_colors.get("scale_x", 100.0))
        scale_y = self.sp_scale_y.value() if hasattr(self, "sp_scale_y") else float(self.current_colors.get("scale_y", 130.0))
        self._yt_worker = YouTubeSampleFetcher(
            url, blur_bg=blur_bg,
            zoom_in=zoom_in, scale_x=scale_x, scale_y=scale_y,
            parent=self
        )
        self._yt_worker.finished_sig.connect(self._on_youtube_fetched)
        self._yt_worker.start()

    def _on_youtube_fetched(self, success: bool, message: str):
        self.btn_fetch_yt.setEnabled(True)
        if success:
            self.lbl_status.setText(f"🟢 {message}")
            self._execute_color_grading_preview()
        else:
            self.lbl_status.setText(f"❌ {message}")
            QMessageBox.warning(self, "Lỗi tải YouTube", message)

    def _extract_base_frame(self):
        vp = self.txt_video_path.text().strip() or self.sample_video_path
        start_sec = self.sp_start_sec.value()

        self.lbl_status.setText("⏳ Đang trích xuất base frame 9:16...")
        self.btn_reload_base.setEnabled(False)

        blur_bg = self.chk_blur_bg.isChecked() if hasattr(self, "chk_blur_bg") else bool(self.current_colors.get("blur_bg", True))
        zoom_in = self.sp_zoom_in.value() if hasattr(self, "sp_zoom_in") else float(self.current_colors.get("zoom_in", 178.0))
        scale_x = self.sp_scale_x.value() if hasattr(self, "sp_scale_x") else float(self.current_colors.get("scale_x", 100.0))
        scale_y = self.sp_scale_y.value() if hasattr(self, "sp_scale_y") else float(self.current_colors.get("scale_y", 130.0))
        self._base_worker = AsyncLayoutBaseExtractor(
            vp, start_sec=start_sec, blur_bg=blur_bg,
            zoom_in=zoom_in, scale_x=scale_x, scale_y=scale_y,
            parent=self
        )
        self._base_worker.finished_sig.connect(self._on_base_frame_extracted)
        self._base_worker.start()

    def _on_base_frame_extracted(self, success: bool, message: str):
        self.btn_reload_base.setEnabled(True)
        if success:
            self.lbl_status.setText(f"🟢 {message}")
            # Ngay sau khi có base frame, kích hoạt đổ màu Stage 2
            self._execute_color_grading_preview()
        else:
            self.lbl_status.setText(f"⚠️ {message}")

    def _schedule_color_refresh(self):
        # Debounce 180ms chống spam khi kéo thanh trượt
        self.debounce_timer.stop()
        self.debounce_timer.start(180)

    def _execute_color_grading_preview(self):
        if self._color_worker and self._color_worker.isRunning():
            return

        opts = self.get_values()
        self._color_worker = AsyncColorGradeWorker(opts, parent=self)
        self._color_worker.finished_sig.connect(self._on_color_graded)
        self._color_worker.start()

    def _on_color_graded(self, pixmap: Optional[QPixmap], elapsed_ms: int, status_text: str):
        if pixmap and not pixmap.isNull():
            self.canvas.set_pixmap(pixmap)
            self.lbl_status.setText(status_text)
        else:
            self.lbl_status.setText(status_text)

    # --------------------------------------------------------------------------
    # RENDER CLIP MẪU 6S & XEM TRỰC TIẾP BẰNG FFPLAY
    # --------------------------------------------------------------------------
    def _render_6s_sample(self):
        vp = self.txt_video_path.text().strip() or self.sample_video_path
        if not vp or not os.path.exists(vp):
            QMessageBox.warning(self, "Chưa có video", "Vui lòng chọn 1 file video mẫu hợp lệ trên ổ cứng để render 6s!")
            return

        self.btn_render_6s.setEnabled(False)
        self.lbl_status.setText("⏳ Đang render clip mẫu 6s qua FFmpeg (veryfast)...")

        opts = self.get_values()
        start_sec = self.sp_start_sec.value()

        self._sample6s_worker = Async6sSampleWorker(vp, opts, start_sec=start_sec, parent=self)
        self._sample6s_worker.finished_sig.connect(self._on_sample_6s_finished)
        self._sample6s_worker.start()

    def _on_sample_6s_finished(self, success: bool, video_path: str, message: str):
        self.btn_render_6s.setEnabled(True)
        if success and video_path and os.path.exists(video_path):
            self.lbl_status.setText(f"🟢 {message}")
            QMessageBox.information(
                self, "Render Hoàn Tất",
                f"Đã render xong clip mẫu 6s!\nBấm 'Phát Clip 6s (FFplay)' để xem ngay."
            )
        else:
            self.lbl_status.setText(f"❌ {message}")
            QMessageBox.warning(self, "Lỗi Render 6s", message)

    def _play_6s_sample(self):
        if not PREVIEW_6S_PATH.exists() or PREVIEW_6S_PATH.stat().st_size < 1000:
            QMessageBox.information(
                self, "Chưa render",
                "Chưa có clip mẫu 6s! Vui lòng bấm 'Render Mẫu 6s' trước."
            )
            return

        try:
            cmd = [
                "ffplay", "-autoexit",
                "-window_title", "CapCut Color Studio — Preview 6s (360x640)",
                "-x", "360", "-y", "640",
                str(PREVIEW_6S_PATH)
            ]
            sub_flags = 0x08000000 if os.name == 'nt' else 0
            subprocess.Popen(cmd, creationflags=sub_flags)
            self.lbl_status.setText("▶ Đang phát clip 6s qua FFplay...")
        except Exception as e:
            QMessageBox.warning(self, "Lỗi mở FFplay", f"Không thể mở ffplay: {e}")


def open_capcut_color_popup(
    parent=None,
    current_colors: Optional[Dict[str, Any]] = None,
    sample_video_path: str = "",
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """Hàm tiện ích mở nhanh Studio Màu Sắc CapCut Pro"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    cfg = dict(current_config or current_colors or {})
    vpath = video_source or sample_video_path or cfg.get("video_source") or cfg.get("source_url_or_path") or cfg.get("youtube_url") or ""
    dlg = CapCutColorStudioDialog(current_data=cfg, sample_video_path=vpath, parent=None)
    if dlg.exec() == QDialog.Accepted:
        res = dlg.get_values()
        if on_save_callback:
            on_save_callback(res)
        return res
    return cfg
