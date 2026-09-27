# -*- coding: utf-8 -*-
"""
crop_blur_studio.py: Module CapCut Pro Video Crop & Blur Mask Studio
ÁP DỤNG 100% BẢN THIẾT KẾ KỸ THUẬT CHI TIẾT TỪ PLAN_CROP_VA_BLUR_MASK.md

Chức năng:
1. Hệ quy chiếu tọa độ 2 chiều Video (Base 1920x1080) <-> Canvas tương tác chuẩn xác từng pixel.
2. Bộ điều khiển chuột tương tác:
   - 8 chốt điều khiển Khung Crop (4 chốt góc TL, TR, BL, BR + 4 chốt cạnh TOP, BOTTOM, LEFT, RIGHT).
   - 4 chốt điều khiển Vùng Làm Mờ Blur Mask (blur_tl, blur_tr, blur_bl, blur_br).
   - Di chuyển thân hộp Crop (move) và thân hộp Blur Mask (move_blur).
   - Shaded Overlay mờ tối bên ngoài khung Crop.
   - Vùng phủ mờ màu cam nhạt cho Blur Mask (che logo, watermark, sub cứng).
   - Nhận diện hit_test bán kính 14px, đổi con trỏ chuột ngữ cảnh (SizeFDiag, SizeBDiag, SizeHor, SizeVer, SizeAll).
3. Hỗ trợ các tỷ lệ khung hình: Tự do, 9:16, 16:9, 1:1, 4:5, 3:4.
4. Thuật toán Clamping an toàn chống tràn viền (MIN_CROP_SIZE = 20, MIN_BLUR_SIZE = 10).
5. Trích xuất frame mẫu giây 2.0 bằng OpenCV (tránh frame đen đầu video) hoặc chọn ảnh tùy biến.
6. Xây dựng chuỗi lệnh FFmpeg filter_complex (Background Fast Boxblur + Foreground Crop + Blur Mask Sub-stream).
"""

from __future__ import annotations

import os
import sys
import re
import json
import threading
import tempfile
import subprocess
import urllib.request
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QLabel, QPushButton, QCheckBox, QComboBox, QSpinBox, QGroupBox,
    QFileDialog, QMessageBox, QFrame, QSizePolicy, QLineEdit,
    QScrollArea, QAbstractSpinBox
)
from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, Signal, Slot, QTimer, QThread
from PySide6.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QPixmap, QImage,
    QCursor, QPolygonF, QPainterPath
)

try:
    import cv2
    import numpy as np
except ImportError:
    cv2 = None
    np = None

def _get_app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

APP_DIR = _get_app_dir()

# Hằng số mặc định chuẩn hóa theo thiết kế
DEFAULT_BASE_W = 1920
DEFAULT_BASE_H = 1080
MIN_CROP_SIZE = 20
MIN_BLUR_SIZE = 10
HIT_TOLERANCE = 14
HANDLE_SIZE = 10
PREVIEW_CACHE_FILE = APP_DIR / "system_data" / "crop_preview_cache.jpg"


# ==============================================================================
# WORKER THREAD: BỐC FRAME VIDEO BẤT ĐỒNG BỘ (CHỐNG LAG TUYỆT ĐỐI KHI MỞ CỬA SỔ)
# ==============================================================================
class AsyncVideoFrameExtractor(QThread):
    frame_ready = Signal(object, int, int) # (QImage or None, w, h)

    def __init__(self, video_path: str, timestamp_sec: float = 2.0, parent=None):
        super().__init__(parent)
        self.video_path = video_path
        self.timestamp_sec = timestamp_sec

    def run(self):
        vp = self.video_path
        if not vp or not os.path.exists(vp) or cv2 is None:
            self.frame_ready.emit(None, DEFAULT_BASE_W, DEFAULT_BASE_H)
            return

        try:
            cap = cv2.VideoCapture(vp)
            if not cap.isOpened():
                self.frame_ready.emit(None, DEFAULT_BASE_W, DEFAULT_BASE_H)
                return

            cap.set(cv2.CAP_PROP_POS_MSEC, self.timestamp_sec * 1000.0)
            ret, frame = cap.read()
            if not ret or frame is None:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ret, frame = cap.read()
            cap.release()

            if ret and frame is not None:
                h, w, ch = frame.shape
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                bytes_per_line = ch * w
                qimg = QImage(rgb.data, w, h, bytes_per_line, QImage.Format_RGB888).copy()

                try:
                    PREVIEW_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    cv2.imwrite(str(PREVIEW_CACHE_FILE), frame)
                except Exception:
                    pass

                self.frame_ready.emit(qimg, w, h)
                return
        except Exception:
            pass

        self.frame_ready.emit(None, DEFAULT_BASE_W, DEFAULT_BASE_H)


# ==============================================================================
# WORKER THREAD: TẢI THUMBNAIL HOẶC BỐC FRAME YOUTUBE (CHUẨN QT THREAD-SAFE)
# ==============================================================================
class YouTubeSampleFetcher(QThread):
    finished_sig = Signal(object, str, str)  # (QPixmap or None, status_text, color_hex)

    def __init__(self, mode: str, video_id: str, parent=None):
        super().__init__(parent)
        self.mode = mode  # "thumb" or "frame"
        self.video_id = video_id

    def run(self):
        vid = self.video_id
        if not vid:
            self.finished_sig.emit(None, "❌ Không tìm thấy ID video hợp lệ.", "#f38ba8")
            return

        if self.mode == "thumb":
            thumb_urls = [
                f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
                f"https://img.youtube.com/vi/{vid}/sddefault.jpg",
                f"https://img.youtube.com/vi/{vid}/hqdefault.jpg"
            ]
            loaded_pix = None
            for tu in thumb_urls:
                try:
                    req = urllib.request.Request(tu, headers={"User-Agent": "Mozilla/5.0"})
                    with urllib.request.urlopen(req, timeout=6) as resp:
                        data = resp.read()
                        qimg = QImage()
                        if qimg.loadFromData(data):
                            if qimg.width() > 120 and qimg.height() > 90:
                                loaded_pix = QPixmap.fromImage(qimg)
                                break
                except Exception:
                    pass

            if loaded_pix and not loaded_pix.isNull():
                self.finished_sig.emit(loaded_pix, f"✅ Đã tải Thumbnail: {loaded_pix.width()}×{loaded_pix.height()}!", "#a6e3a1")
            else:
                self.finished_sig.emit(None, "❌ Không tải được thumbnail video này.", "#f38ba8")

        elif self.mode == "frame":
            tmp_dir = Path(tempfile.gettempdir())
            tmp_video = tmp_dir / f"yt_sample_{vid}.mp4"
            if tmp_video.exists():
                try: tmp_video.unlink()
                except Exception: pass

            full_url = f"https://www.youtube.com/watch?v={vid}"
            import shutil
            ytdlp_bin = APP_DIR / "bin" / "yt-dlp.exe"
            if not ytdlp_bin.exists():
                ytdlp_bin = APP_DIR / "app" / "bin" / "yt-dlp.exe"
            if ytdlp_bin.exists():
                ytdlp_prefix = [str(ytdlp_bin)]
            else:
                which_ytdlp = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
                ytdlp_prefix = [which_ytdlp] if which_ytdlp else [sys.executable, "-m", "yt_dlp"]

            cmd = ytdlp_prefix + [
                "-f", "bestvideo[height<=1080][ext=mp4]/best[ext=mp4]/best",
                "--download-sections", "*0-4",
                "-o", str(tmp_video),
                full_url
            ]
            sub_flags = 0x08000000 if os.name == 'nt' else 0
            try:
                subprocess.run(cmd, capture_output=True, timeout=35, creationflags=sub_flags)
            except Exception:
                pass

            extracted_pix = None
            if tmp_video.exists() and tmp_video.stat().st_size > 1000:
                extracted_pix = extract_sample_frame(str(tmp_video), timestamp_sec=2.0)
                try: tmp_video.unlink()
                except Exception: pass

            if extracted_pix and not extracted_pix.isNull():
                self.finished_sig.emit(extracted_pix, f"✅ Đã bốc frame video ({extracted_pix.width()}×{extracted_pix.height()}) chuẩn 100%!", "#a6e3a1")
            else:
                self.finished_sig.emit(None, "⚠️ Thử lại với nút '⚡ Lấy Thumbnail'", "#f9e2af")


# ==============================================================================
# 1. TIỆN ÍCH BỐC FRAME MẪU QUA OPENCV
# ==============================================================================
def extract_sample_frame(video_path: str, output_path: str = "", timestamp_sec: float = 2.0) -> Optional[QPixmap]:
    """
    Trích xuất frame mẫu tại giây thứ 2.0 bằng OpenCV để tránh frame đen ở đầu video.
    Trả về QPixmap để vẽ trực tiếp lên Canvas.
    """
    if not video_path or not os.path.exists(video_path) or cv2 is None:
        return create_fallback_preview_pixmap(DEFAULT_BASE_W, DEFAULT_BASE_H)

    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return create_fallback_preview_pixmap(DEFAULT_BASE_W, DEFAULT_BASE_H)

        # Tua tới giây 2.0 (2000 ms)
        cap.set(cv2.CAP_PROP_POS_MSEC, timestamp_sec * 1000.0)
        ret, frame = cap.read()
        if not ret or frame is None:
            # Nếu video ngắn hơn 2 giây, đọc ngay frame đầu tiên
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()

        cap.release()

        if ret and frame is not None:
            # OpenCV trả về BGR -> chuyển sang RGB
            h, w, ch = frame.shape
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            bytes_per_line = ch * w
            qimg = QImage(rgb_frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
            pix = QPixmap.fromImage(qimg)

            if output_path:
                try:
                    cv2.imwrite(output_path, frame)
                except Exception:
                    pass
            return pix
    except Exception:
        pass

    return create_fallback_preview_pixmap(DEFAULT_BASE_W, DEFAULT_BASE_H)


def create_fallback_preview_pixmap(width: int = 1920, height: int = 1080) -> QPixmap:
    """Tạo một frame placeholder thẩm mỹ độ phân giải 1920x1080 khi chưa có video tải về"""
    pix = QPixmap(width, height)
    pix.fill(QColor("#181825"))

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)

    # Vẽ lưới nền nhẹ
    pen_grid = QPen(QColor("#313244"), 1, Qt.DotLine)
    painter.setPen(pen_grid)
    for x in range(0, width, 120):
        painter.drawLine(x, 0, x, height)
    for y in range(0, height, 120):
        painter.drawLine(0, y, width, y)

    # Khung hướng dẫn trung tâm
    pen_box = QPen(QColor("#89b4fa"), 2)
    painter.setPen(pen_box)
    painter.drawRect(200, 150, width - 400, height - 300)

    font_title = QFont("Segoe UI", 28, QFont.Bold)
    painter.setFont(font_title)
    painter.setPen(QColor("#cdd6f4"))
    painter.drawText(QRect(0, height // 2 - 80, width, 60), Qt.AlignCenter, "🎬 KHUNG HÌNH VIDEO MẪU (1920 × 1080)")

    font_sub = QFont("Segoe UI", 16)
    painter.setFont(font_sub)
    painter.setPen(QColor("#a6adc8"))
    painter.drawText(QRect(0, height // 2 - 10, width, 40), Qt.AlignCenter, "Bấm '📂 Tải ảnh mẫu từ máy...' hoặc '🔄 Load lại khung hình' để nạp ảnh thực tế")
    painter.drawText(QRect(0, height // 2 + 35, width, 40), Qt.AlignCenter, "Kéo 8 chốt trắng để Crop | Kéo khung cam để làm mờ Logo/Watermark")

    painter.end()
    return pix


# ==============================================================================
# 2. CROP INTERACTIVE CANVAS: CANVAS TƯƠNG TÁC CHUỘT 2 CHIỀU ĐA ĐIỂM
# ==============================================================================
class CropInteractiveCanvas(QWidget):
    """
    Canvas tương tác trực quan 2 chiều:
    - Hiển thị video preview theo thuật toán Aspect-Ratio Fitting.
    - Shaded Overlay che tối ngoài vùng Crop.
    - Khung Crop viền trắng với 8 chốt kéo (TL, TR, BL, BR, TOP, BOTTOM, LEFT, RIGHT).
    - Khung Blur Mask viền cam với 4 chốt góc (blur_tl, blur_tr, blur_bl, blur_br) + thân hộp move_blur.
    - Hit testing độ nhạy 14px, con trỏ ngữ cảnh, khóa tỉ lệ và Clamping chống tràn viền.
    """
    crop_changed = Signal(dict)
    blur_mask_changed = Signal(dict)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(480, 270)

        # Kích thước gốc của video (Base Coordinate System)
        self.base_w = DEFAULT_BASE_W
        self.base_h = DEFAULT_BASE_H

        # Tọa độ Crop (theo hệ tọa độ video gốc)
        self.use_crop = True
        self.crop_ratio = "Tự do"
        self.crop_x = 0
        self.crop_y = 0
        self.crop_w = DEFAULT_BASE_W
        self.crop_h = DEFAULT_BASE_H

        # Tọa độ Blur Mask (theo hệ tọa độ video gốc, mặc định tắt, chỉ bật khi tích chọn)
        self.use_blur_mask = False
        self.blur_shape = "Hình chữ nhật"
        self.blur_mask_x = 20
        self.blur_mask_y = 861
        self.blur_mask_w = 932
        self.blur_mask_h = 210

        # Thông số Zoom-in & Scale (đồng bộ 100% từ cấu hình render)
        self.zoom_in: float = 178.0
        self.scale_x: float = 100.0
        self.scale_y: float = 130.0
        self.blur_bg: bool = True

        # Frame preview & nền mờ 9:16
        self.preview_pixmap = create_fallback_preview_pixmap(self.base_w, self.base_h)
        self.blurred_bg_pixmap: Optional[QPixmap] = None
        self.show_live_output: bool = False
        self._generate_blurred_bg()

        # Trạng thái kéo chuột tương tác
        self.active_handle = None
        self.drag_start_mouse = QPoint()
        self.drag_start_rect = {}
        self.drag_start_blur = {}

    def set_preview_pixmap(self, pix: Optional[QPixmap]):
        """Cập nhật frame preview và tự động tái tạo nền mờ 9:16 chuẩn xác 100%"""
        if pix is not None and not pix.isNull():
            self.preview_pixmap = pix
            self.base_w = pix.width()
            self.base_h = pix.height()
        else:
            self.preview_pixmap = create_fallback_preview_pixmap(self.base_w, self.base_h)
        self._generate_blurred_bg()
        self.clamp_crop()
        self.clamp_blur_mask()
        self.update()

    def _generate_blurred_bg(self):
        """Tạo nền mờ 9:16 (540x960) từ frame video mô phỏng chuẩn xác filter boxblur của FFmpeg"""
        if not self.preview_pixmap or self.preview_pixmap.isNull():
            self.blurred_bg_pixmap = None
            return
        try:
            if cv2 is not None and np is not None:
                qimg = self.preview_pixmap.toImage().convertToFormat(QImage.Format_RGB888)
                w, h = qimg.width(), qimg.height()
                ptr = qimg.constBits()
                arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, w, 3))
                
                target_w, target_h = 540, 960
                scale_ratio = max(target_w / float(w), target_h / float(h))
                nw = max(1, int(round(w * scale_ratio)))
                nh = max(1, int(round(h * scale_ratio)))
                resized = cv2.resize(arr, (nw, nh), interpolation=cv2.INTER_LINEAR)
                
                cx_start = max(0, (nw - target_w) // 2)
                cy_start = max(0, (nh - target_h) // 2)
                cropped = resized[cy_start:cy_start + target_h, cx_start:cx_start + target_w]
                
                # Boxblur giống FFmpeg boxblur=20:10
                blurred = cv2.blur(cropped, (41, 41))
                blurred = (blurred * 0.85).astype(np.uint8)
                
                bh, bw, bch = blurred.shape
                res_img = QImage(blurred.data, bw, bh, bch * bw, QImage.Format_RGB888).copy()
                self.blurred_bg_pixmap = QPixmap.fromImage(res_img)
                return
        except Exception:
            pass

        try:
            scaled_small = self.preview_pixmap.scaled(54, 96, Qt.IgnoreAspectRatio, Qt.FastTransformation)
            self.blurred_bg_pixmap = scaled_small.scaled(540, 960, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        except Exception:
            self.blurred_bg_pixmap = None

    # --------------------------------------------------------------------------
    # THUẬT TOÁN HỆ QUY CHIẾU TỌA ĐỘ 2 CHIỀU 9:16 (CHUẨN XÁC 100% ĐẦU RA RENDER)
    # --------------------------------------------------------------------------
    def calculate_display_size(self) -> Tuple[int, int, int, int]:
        """
        Khớp tỉ lệ khung hình chuẩn 9:16 (1080x1920) vào Canvas với padding 10px mỗi bên.
        """
        cw = max(50, self.width())
        ch = max(50, self.height())
        target_ratio = 9.0 / 16.0
        aw, ah = max(50, cw - 20), max(50, ch - 20)

        if aw / float(ah) > target_ratio:
            disp_h = int(ah)
            disp_w = int(disp_h * target_ratio)
        else:
            disp_w = int(aw)
            disp_h = int(disp_w / target_ratio)

        disp_w = max(10, disp_w)
        disp_h = max(10, disp_h)
        offset_x = int((cw - disp_w) / 2)
        offset_y = int((ch - disp_h) / 2)
        return disp_w, disp_h, offset_x, offset_y

    def get_fg_canvas_rect(self) -> Tuple[int, int, int, int]:
        """Tính toán vị trí và kích thước khung video tiền cảnh theo zoom_in, scale_x, scale_y nằm giữa khung 9:16 trên Canvas"""
        disp_w, disp_h, offset_x, offset_y = self.calculate_display_size()
        bw = max(1, self.base_w)
        bh = max(1, self.base_h)

        zoom_factor = float(getattr(self, "zoom_in", 178.0)) / 100.0
        sx = float(getattr(self, "scale_x", 100.0)) / 100.0
        sy = float(getattr(self, "scale_y", 130.0)) / 100.0

        # Trong không gian chuẩn 1080x1920: cơ sở vừa khít chiều ngang 1080
        base_w_1080 = 1080.0
        base_h_1080 = 1080.0 * float(bh) / float(bw)

        # Áp dụng Zoom-in và Scale X/Y
        fg_target_w = base_w_1080 * zoom_factor * sx
        fg_target_h = base_h_1080 * zoom_factor * sy

        fg_target_x = (1080.0 - fg_target_w) / 2.0
        fg_target_y = (1920.0 - fg_target_h) / 2.0

        scale_x = disp_w / 1080.0
        scale_y = disp_h / 1920.0

        cx = int(round(offset_x + fg_target_x * scale_x))
        cy = int(round(offset_y + fg_target_y * scale_y))
        cw = int(round(fg_target_w * scale_x))
        ch = int(round(fg_target_h * scale_y))
        return cx, cy, cw, ch

    def video_to_canvas(self, vx: float, vy: float) -> Tuple[int, int]:
        """Chuyển từ Tọa độ Video Gốc -> Tọa độ Canvas (vẽ lên video tiền cảnh)"""
        fg_x, fg_y, fg_w, fg_h = self.get_fg_canvas_rect()
        cx = fg_x + vx * (float(fg_w) / max(1.0, float(self.base_w)))
        cy = fg_y + vy * (float(fg_h) / max(1.0, float(self.base_h)))
        return int(round(cx)), int(round(cy))

    def canvas_to_video(self, cx: float, cy: float) -> Tuple[float, float]:
        """Chuyển từ Tọa độ Chuột Canvas -> Tọa độ Video Gốc"""
        fg_x, fg_y, fg_w, fg_h = self.get_fg_canvas_rect()
        vx = (cx - fg_x) * (float(self.base_w) / max(1.0, float(fg_w)))
        vy = (cy - fg_y) * (float(self.base_h) / max(1.0, float(fg_h)))
        return vx, vy

    # --------------------------------------------------------------------------
    # THUẬT TOÁN CLAMPING CHỐNG TRÀN VIỀN (Mục 4.4 trong PLAN)
    # --------------------------------------------------------------------------
    def clamp_crop(self):
        """Khống chế tuyệt đối không cho khung cắt vượt quá biên ảnh hoặc nhỏ hơn MIN_CROP_SIZE"""
        w = max(MIN_CROP_SIZE, min(self.crop_w, self.base_w))
        h = max(MIN_CROP_SIZE, min(self.crop_h, self.base_h))
        x = max(0, min(self.crop_x, self.base_w - w))
        y = max(0, min(self.crop_y, self.base_h - h))
        self.crop_x = int(round(x))
        self.crop_y = int(round(y))
        self.crop_w = int(round(w))
        self.crop_h = int(round(h))

    def clamp_blur_mask(self):
        """Khống chế vùng làm mờ Blur Mask luôn nằm trong video và đạt kích thước tối thiểu"""
        w = max(MIN_BLUR_SIZE, min(self.blur_mask_w, self.base_w))
        h = max(MIN_BLUR_SIZE, min(self.blur_mask_h, self.base_h))
        x = max(0, min(self.blur_mask_x, self.base_w - w))
        y = max(0, min(self.blur_mask_y, self.base_h - h))
        self.blur_mask_x = int(round(x))
        self.blur_mask_y = int(round(y))
        self.blur_mask_w = int(round(w))
        self.blur_mask_h = int(round(h))

    # --------------------------------------------------------------------------
    # NHẬN DIỆN VỊ TRÍ BẤM CHUỘT (HIT TESTING - Mục 4.1 trong PLAN)
    # --------------------------------------------------------------------------
    def hit_test(self, event_x: int, event_y: int) -> Optional[str]:
        """
        Nhận diện vị trí chuột với bán kính bắt dính hit_tolerance = 14px.
        Thứ tự ưu tiên:
        1. 4 chốt góc Blur Mask (blur_tl, blur_tr, blur_bl, blur_br)
        2. Thân hộp Blur Mask (move_blur)
        3. 4 chốt góc Khung Crop (tl, tr, bl, br)
        4. 4 chốt cạnh Khung Crop (top, bottom, left, right)
        5. Thân hộp Khung Crop (move)
        """
        if self.show_live_output:
            return None

        hit = HIT_TOLERANCE

        # 1. Kiểm tra Blur Mask (nếu đang bật)
        if self.use_blur_mask:
            bx1, by1 = self.video_to_canvas(self.blur_mask_x, self.blur_mask_y)
            bx2, by2 = self.video_to_canvas(self.blur_mask_x + self.blur_mask_w, self.blur_mask_y + self.blur_mask_h)
            if abs(event_x - bx1) <= hit and abs(event_y - by1) <= hit: return "blur_tl"
            if abs(event_x - bx2) <= hit and abs(event_y - by1) <= hit: return "blur_tr"
            if abs(event_x - bx1) <= hit and abs(event_y - by2) <= hit: return "blur_bl"
            if abs(event_x - bx2) <= hit and abs(event_y - by2) <= hit: return "blur_br"
            if bx1 < event_x < bx2 and by1 < event_y < by2: return "move_blur"

        # 2. Kiểm tra Khung Crop (nếu đang bật)
        if self.use_crop:
            cx1, cy1 = self.video_to_canvas(self.crop_x, self.crop_y)
            cx2, cy2 = self.video_to_canvas(self.crop_x + self.crop_w, self.crop_y + self.crop_h)
            if abs(event_x - cx1) <= hit and abs(event_y - cy1) <= hit: return "tl"
            if abs(event_x - cx2) <= hit and abs(event_y - cy1) <= hit: return "tr"
            if abs(event_x - cx1) <= hit and abs(event_y - cy2) <= hit: return "bl"
            if abs(event_x - cx2) <= hit and abs(event_y - cy2) <= hit: return "br"
            if abs(event_y - cy1) <= hit and cx1 <= event_x <= cx2: return "top"
            if abs(event_y - cy2) <= hit and cx1 <= event_x <= cx2: return "bottom"
            if abs(event_x - cx1) <= hit and cy1 <= event_x <= cy2: return "left"
            if abs(event_x - cx2) <= hit and cy1 <= event_x <= cy2: return "right"
            if cx1 < event_x < cx2 and cy1 < event_y < cy2: return "move"

        return None

    # --------------------------------------------------------------------------
    # ĐỔI CON TRỎ CHUỘT THEO NGỮ CẢNH (Mục 4.2 trong PLAN)
    # --------------------------------------------------------------------------
    def update_cursor(self, handle: Optional[str]):
        if self.show_live_output:
            self.setCursor(Qt.ArrowCursor)
            return
        if handle in ("tl", "br", "blur_tl", "blur_br"):
            self.setCursor(Qt.SizeFDiagCursor)
        elif handle in ("tr", "bl", "blur_tr", "blur_bl"):
            self.setCursor(Qt.SizeBDiagCursor)
        elif handle in ("left", "right"):
            self.setCursor(Qt.SizeHorCursor)
        elif handle in ("top", "bottom"):
            self.setCursor(Qt.SizeVerCursor)
        elif handle in ("move", "move_blur"):
            self.setCursor(Qt.SizeAllCursor)
        else:
            self.setCursor(Qt.ArrowCursor)

    # --------------------------------------------------------------------------
    # XỬ LÝ SỰ KIỆN CHUỘT (MOUSE EVENTS)
    # --------------------------------------------------------------------------
    def mousePressEvent(self, event):
        if self.show_live_output:
            return
        if event.button() == Qt.LeftButton:
            handle = self.hit_test(event.position().x(), event.position().y())
            if handle:
                self.active_handle = handle
                self.drag_start_mouse = event.position().toPoint()
                self.drag_start_rect = {
                    "x": self.crop_x, "y": self.crop_y,
                    "w": self.crop_w, "h": self.crop_h
                }
                self.drag_start_blur = {
                    "x": self.blur_mask_x, "y": self.blur_mask_y,
                    "w": self.blur_mask_w, "h": self.blur_mask_h
                }
                self.update_cursor(handle)

    def mouseMoveEvent(self, event):
        if self.show_live_output:
            self.setCursor(Qt.ArrowCursor)
            return

        mx = event.position().x()
        my = event.position().y()

        if self.active_handle is None:
            hover_handle = self.hit_test(mx, my)
            self.update_cursor(hover_handle)
            return

        handle = self.active_handle
        vx, vy = self.canvas_to_video(mx, my)

        # A. XỬ LÝ KÉO DI CHUYỂN HOẶC CO BÓP BLUR MASK
        if handle.startswith("blur_") or handle == "move_blur":
            sb = self.drag_start_blur
            bx2, by2 = sb["x"] + sb["w"], sb["y"] + sb["h"]

            if handle == "move_blur":
                fg_x, fg_y, fg_w, fg_h = self.get_fg_canvas_rect()
                scale_factor_x = float(self.base_w) / max(1.0, float(fg_w))
                scale_factor_y = float(self.base_h) / max(1.0, float(fg_h))
                dx = (mx - self.drag_start_mouse.x()) * scale_factor_x
                dy = (my - self.drag_start_mouse.y()) * scale_factor_y
                self.blur_mask_x = int(round(sb["x"] + dx))
                self.blur_mask_y = int(round(sb["y"] + dy))
            elif handle == "blur_tl":
                nx = min(max(0, vx), bx2 - MIN_BLUR_SIZE)
                ny = min(max(0, vy), by2 - MIN_BLUR_SIZE)
                self.blur_mask_x, self.blur_mask_y = int(nx), int(ny)
                self.blur_mask_w, self.blur_mask_h = int(bx2 - nx), int(by2 - ny)
            elif handle == "blur_tr":
                nx2 = max(min(self.base_w, vx), sb["x"] + MIN_BLUR_SIZE)
                ny = min(max(0, vy), by2 - MIN_BLUR_SIZE)
                self.blur_mask_x, self.blur_mask_y = int(sb["x"]), int(ny)
                self.blur_mask_w, self.blur_mask_h = int(nx2 - sb["x"]), int(by2 - ny)
            elif handle == "blur_bl":
                nx = min(max(0, vx), bx2 - MIN_BLUR_SIZE)
                ny2 = max(min(self.base_h, vy), sb["y"] + MIN_BLUR_SIZE)
                self.blur_mask_x, self.blur_mask_y = int(nx), int(sb["y"])
                self.blur_mask_w, self.blur_mask_h = int(bx2 - nx), int(ny2 - sb["y"])
            elif handle == "blur_br":
                nx2 = max(min(self.base_w, vx), sb["x"] + MIN_BLUR_SIZE)
                ny2 = max(min(self.base_h, vy), sb["y"] + MIN_BLUR_SIZE)
                self.blur_mask_x, self.blur_mask_y = int(sb["x"]), int(sb["y"])
                self.blur_mask_w, self.blur_mask_h = int(nx2 - sb["x"]), int(ny2 - sb["y"])

            self.clamp_blur_mask()
            self.blur_mask_changed.emit({
                "x": self.blur_mask_x, "y": self.blur_mask_y,
                "w": self.blur_mask_w, "h": self.blur_mask_h
            })
            self.update()
            return

        # B. XỬ LÝ KÉO DI CHUYỂN HOẶC CO BÓP KHUNG CROP
        sr = self.drag_start_rect
        x2, y2 = sr["x"] + sr["w"], sr["y"] + sr["h"]

        if handle == "move":
            fg_x, fg_y, fg_w, fg_h = self.get_fg_canvas_rect()
            scale_factor_x = float(self.base_w) / max(1.0, float(fg_w))
            scale_factor_y = float(self.base_h) / max(1.0, float(fg_h))
            dx = (mx - self.drag_start_mouse.x()) * scale_factor_x
            dy = (my - self.drag_start_mouse.y()) * scale_factor_y
            self.crop_x = int(round(sr["x"] + dx))
            self.crop_y = int(round(sr["y"] + dy))
        else:
            ratio_val = self._get_ratio_aspect()

            if ratio_val is None or self.crop_ratio == "Tự do":
                if handle == "tl":
                    nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                    ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                    self.crop_x, self.crop_y = int(nx), int(ny)
                    self.crop_w, self.crop_h = int(x2 - nx), int(y2 - ny)
                elif handle == "tr":
                    nx2 = max(min(self.base_w, vx), sr["x"] + MIN_CROP_SIZE)
                    ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                    self.crop_x, self.crop_y = int(sr["x"]), int(ny)
                    self.crop_w, self.crop_h = int(nx2 - sr["x"]), int(y2 - ny)
                elif handle == "bl":
                    nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                    ny2 = max(min(self.base_h, vy), sr["y"] + MIN_CROP_SIZE)
                    self.crop_x, self.crop_y = int(nx), int(sr["y"])
                    self.crop_w, self.crop_h = int(x2 - nx), int(ny2 - sr["y"])
                elif handle == "br":
                    nx2 = max(min(self.base_w, vx), sr["x"] + MIN_CROP_SIZE)
                    ny2 = max(min(self.base_h, vy), sr["y"] + MIN_CROP_SIZE)
                    self.crop_x, self.crop_y = int(sr["x"]), int(sr["y"])
                    self.crop_w, self.crop_h = int(nx2 - sr["x"]), int(ny2 - sr["y"])
                elif handle == "top":
                    ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                    self.crop_y, self.crop_h = int(ny), int(y2 - ny)
                elif handle == "bottom":
                    ny2 = max(min(self.base_h, vy), sr["y"] + MIN_CROP_SIZE)
                    self.crop_h = int(ny2 - sr["y"])
                elif handle == "left":
                    nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                    self.crop_x, self.crop_w = int(nx), int(x2 - nx)
                elif handle == "right":
                    nx2 = max(min(self.base_w, vx), sr["x"] + MIN_CROP_SIZE)
                    self.crop_w = int(nx2 - sr["x"])
            else:
                if handle in ("br", "right", "bottom"):
                    nw = max(MIN_CROP_SIZE, min(self.base_w - sr["x"], vx - sr["x"]))
                    nh = nw / ratio_val
                    if sr["y"] + nh > self.base_h:
                        nh = self.base_h - sr["y"]
                        nw = nh * ratio_val
                    self.crop_w, self.crop_h = int(nw), int(nh)
                elif handle in ("tl", "left", "top"):
                    nw = max(MIN_CROP_SIZE, min(x2, x2 - vx))
                    nh = nw / ratio_val
                    if y2 - nh < 0:
                        nh = y2
                        nw = nh * ratio_val
                    self.crop_x, self.crop_y = int(x2 - nw), int(y2 - nh)
                    self.crop_w, self.crop_h = int(nw), int(nh)
                elif handle == "tr":
                    nw = max(MIN_CROP_SIZE, min(self.base_w - sr["x"], vx - sr["x"]))
                    nh = nw / ratio_val
                    if y2 - nh < 0:
                        nh = y2
                        nw = nh * ratio_val
                    self.crop_y = int(y2 - nh)
                    self.crop_w, self.crop_h = int(nw), int(nh)
                elif handle == "bl":
                    nw = max(MIN_CROP_SIZE, min(x2, x2 - vx))
                    nh = nw / ratio_val
                    if sr["y"] + nh > self.base_h:
                        nh = self.base_h - sr["y"]
                        nw = nh * ratio_val
                    self.crop_x = int(x2 - nw)
                    self.crop_w, self.crop_h = int(nw), int(nh)

        self.clamp_crop()
        self.crop_changed.emit({
            "x": self.crop_x, "y": self.crop_y,
            "w": self.crop_w, "h": self.crop_h
        })
        self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.active_handle = None
            hover_handle = self.hit_test(event.position().x(), event.position().y())
            self.update_cursor(hover_handle)

    def _get_ratio_aspect(self) -> Optional[float]:
        r = self.crop_ratio
        if r == "9:16": return 9.0 / 16.0
        if r == "16:9": return 16.0 / 9.0
        if r == "1:1": return 1.0
        if r == "4:5": return 4.0 / 5.0
        if r == "3:4": return 3.0 / 4.0
        return None

    # --------------------------------------------------------------------------
    # VẼ CANVAS TRỰC QUAN (PAINT EVENT - Mục 1 & Mục 4 trong PLAN)
    # --------------------------------------------------------------------------
    # --------------------------------------------------------------------------
    # VẼ CANVAS TRỰC QUAN (PAINT EVENT - 100% KHỚP RENDER ĐẦU RA 9:16)
    # --------------------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)

        disp_w, disp_h, off_x, off_y = self.calculate_display_size()

        # 1. Vẽ nền Canvas ngoài (màu xám đen sang trọng)
        painter.fillRect(self.rect(), QColor("#11111b"))

        # 2. Khung hình 9:16 (1080x1920)
        frame_rect = QRect(off_x, off_y, disp_w, disp_h)

        # 3. Vẽ nền 9:16 (Background Blur hoặc Nền Đen Tuyền #000000 theo config)
        if getattr(self, "blur_bg", True) and self.blurred_bg_pixmap and not self.blurred_bg_pixmap.isNull():
            scaled_bg = self.blurred_bg_pixmap.scaled(disp_w, disp_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(off_x, off_y, scaled_bg)
        else:
            painter.fillRect(frame_rect, QColor("#000000"))

        # ======================================================================
        # CHẾ ĐỘ 1: XEM TRƯỚC ĐẦU RA (LIVE OUTPUT PREVIEW 9:16 - CHUẨN XÁC 100%)
        # ======================================================================
        if self.show_live_output:
            if self.preview_pixmap and not self.preview_pixmap.isNull():
                cw = max(MIN_CROP_SIZE, min(self.crop_w, self.base_w))
                ch = max(MIN_CROP_SIZE, min(self.crop_h, self.base_h))
                cx = max(0, min(self.crop_x, self.base_w - cw))
                cy = max(0, min(self.crop_y, self.base_h - ch))

                # Trích xuất đúng phần video đã crop
                cropped_pix = self.preview_pixmap.copy(cx, cy, cw, ch)

                # CHỈ LÀM MỜ BÊN TRONG KHI NGƯỜI DÙNG TÍCH CHỌN BLUR MASK
                if self.use_blur_mask:
                    rel_x = self.blur_mask_x - cx
                    rel_y = self.blur_mask_y - cy
                    ix1 = max(0, rel_x)
                    iy1 = max(0, rel_y)
                    ix2 = min(cw, rel_x + self.blur_mask_w)
                    iy2 = min(ch, rel_y + self.blur_mask_h)
                    if ix2 > ix1 + 4 and iy2 > iy1 + 4:
                        chunk_w = ix2 - ix1
                        chunk_h = iy2 - iy1
                        chunk = cropped_pix.copy(ix1, iy1, chunk_w, chunk_h)
                        s_w = max(4, chunk_w // 8)
                        s_h = max(4, chunk_h // 8)
                        blurred_chunk = chunk.scaled(s_w, s_h, Qt.IgnoreAspectRatio, Qt.FastTransformation).scaled(chunk_w, chunk_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                        p_chunk = QPainter(cropped_pix)
                        p_chunk.drawPixmap(ix1, iy1, blurred_chunk)
                        p_chunk.end()

                # Áp dụng Zoom-in và Scale X/Y cho video theo đúng cấu hình render
                zoom_factor = float(getattr(self, "zoom_in", 178.0)) / 100.0
                sx = float(getattr(self, "scale_x", 100.0)) / 100.0
                sy = float(getattr(self, "scale_y", 130.0)) / 100.0

                base_w_1080 = 1080.0
                base_h_1080 = 1080.0 * float(ch) / float(cw)

                target_w = base_w_1080 * zoom_factor * sx
                target_h = base_h_1080 * zoom_factor * sy

                target_x = (1080.0 - target_w) / 2.0
                target_y = (1920.0 - target_h) / 2.0

                scale_x = disp_w / 1080.0
                scale_y = disp_h / 1920.0
                out_x = int(round(off_x + target_x * scale_x))
                out_y = int(round(off_y + target_y * scale_y))
                out_w = int(round(target_w * scale_x))
                out_h = int(round(target_h * scale_y))

                painter.save()
                painter.setClipRect(frame_rect)
                scaled_crop = cropped_pix.scaled(out_w, out_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                painter.drawPixmap(out_x, out_y, scaled_crop)
                painter.restore()

            # Viền xanh lá mô phỏng Render Output
            painter.setPen(QPen(QColor("#a6e3a1"), 2))
            painter.drawRect(off_x, off_y, disp_w, disp_h)

            # Badge tiêu đề
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 190)))
            painter.drawRoundedRect(off_x + 8, off_y + 8, 205, 24, 4, 4)
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(QColor("#a6e3a1"))
            painter.drawText(QRect(off_x + 12, off_y + 8, 195, 24), Qt.AlignVCenter, "👁️ ĐẦU RA RENDER (9:16)")

            # Badge thông số dưới đáy
            blur_status = "Bật" if self.use_blur_mask else "Tắt"
            info_tag = f"📐 Video Cắt Xén: {self.crop_w}×{self.crop_h} | Zoom: {int(round(getattr(self, 'zoom_in', 178)))}% | Blur: {blur_status}"
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 190)))
            painter.drawRoundedRect(off_x + 8, off_y + disp_h - 32, disp_w - 16, 24, 4, 4)
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(QColor("#cdd6f4"))
            painter.drawText(QRect(off_x + 14, off_y + disp_h - 32, disp_w - 28, 24), Qt.AlignCenter, info_tag)

            painter.end()
            return

        # ======================================================================
        # CHẾ ĐỘ 2: CHẾ ĐỘ CHỈNH SỬA (EDIT MODE - KÉO CROP & BLUR TRÊN KHUNG 9:16)
        # ======================================================================
        fg_x, fg_y, fg_w, fg_h = self.get_fg_canvas_rect()

        painter.save()
        painter.setClipRect(frame_rect)

        # Vẽ video tiền cảnh ở giữa khung 9:16 (theo zoom_in & scale từ config)
        if self.preview_pixmap and not self.preview_pixmap.isNull():
            scaled_pix = self.preview_pixmap.scaled(fg_w, fg_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap(fg_x, fg_y, scaled_pix)
        else:
            painter.fillRect(fg_x, fg_y, fg_w, fg_h, QColor("#181825"))

        # Viền mờ cho video gốc
        painter.setPen(QPen(QColor("#89b4fa"), 1, Qt.DashLine))
        painter.drawRect(fg_x, fg_y, fg_w, fg_h)

        # LỚP PHỦ MỜ (SHADED OVERLAY) NGOÀI VÙNG CROP TRÊN VIDEO TIỀN CẢNH
        if self.use_crop:
            cx1, cy1 = self.video_to_canvas(self.crop_x, self.crop_y)
            cx2, cy2 = self.video_to_canvas(self.crop_x + self.crop_w, self.crop_y + self.crop_h)
            crop_rect = QRect(cx1, cy1, cx2 - cx1, cy2 - cy1)

            full_path = QPainterPath()
            full_path.addRect(fg_x, fg_y, fg_w, fg_h)
            crop_path = QPainterPath()
            crop_path.addRect(crop_rect)
            shade_path = full_path.subtracted(crop_path)

            painter.fillPath(shade_path, QColor(0, 0, 0, 165))

            # VẼ KHUNG CROP VIỀN TRẮNG (2px) & 8 CHỐT KÉO CO BÓP
            pen_crop = QPen(QColor("#ffffff"), 2, Qt.SolidLine)
            painter.setPen(pen_crop)
            painter.drawRect(crop_rect)

            # Lưới 3x3 Rule of Thirds
            pen_third = QPen(QColor(255, 255, 255, 60), 1, Qt.DashLine)
            painter.setPen(pen_third)
            cw_third = (cx2 - cx1) / 3.0
            ch_third = (cy2 - cy1) / 3.0
            painter.drawLine(int(cx1 + cw_third), cy1, int(cx1 + cw_third), cy2)
            painter.drawLine(int(cx1 + 2 * cw_third), cy1, int(cx1 + 2 * cw_third), cy2)
            painter.drawLine(cx1, int(cy1 + ch_third), cx2, int(cy1 + ch_third))
            painter.drawLine(cx1, int(cy1 + 2 * ch_third), cx2, int(cy1 + 2 * ch_third))

            # 8 Chốt kéo (Handles)
            hs = HANDLE_SIZE
            half = hs // 2
            painter.setBrush(QBrush(QColor("#ffffff")))
            painter.setPen(QPen(QColor("#11111b"), 1.5))

            painter.drawRect(cx1 - half, cy1 - half, hs, hs)  # TL
            painter.drawRect(cx2 - half, cy1 - half, hs, hs)  # TR
            painter.drawRect(cx1 - half, cy2 - half, hs, hs)  # BL
            painter.drawRect(cx2 - half, cy2 - half, hs, hs)  # BR

            mid_x = (cx1 + cx2) // 2
            mid_y = (cy1 + cy2) // 2
            bw_len, bh_thick = 16, 6
            painter.drawRect(mid_x - bw_len // 2, cy1 - bh_thick // 2, bw_len, bh_thick)  # TOP
            painter.drawRect(mid_x - bw_len // 2, cy2 - bh_thick // 2, bw_len, bh_thick)  # BOTTOM
            painter.drawRect(cx1 - bh_thick // 2, mid_y - bw_len // 2, bh_thick, bw_len)  # LEFT
            painter.drawRect(cx2 - bh_thick // 2, mid_y - bw_len // 2, bh_thick, bw_len)  # RIGHT

            # Nhãn thông số Crop
            tag_text = f"✂️ Crop: {self.crop_w}×{self.crop_h} ({self.crop_ratio})"
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
            painter.drawRoundedRect(cx1 + 4, cy1 + 4, 150, 20, 3, 3)
            painter.setPen(QColor("#a6e3a1"))
            painter.drawText(QRect(cx1 + 8, cy1 + 4, 145, 20), Qt.AlignVCenter, tag_text)

        # VẼ KHUNG BLUR MASK & LÀM MỜ THỰC TẾ (CHỈ KHI ĐƯỢC TÍCH CHỌN)
        if self.use_blur_mask:
            bx1, by1 = self.video_to_canvas(self.blur_mask_x, self.blur_mask_y)
            bx2, by2 = self.video_to_canvas(self.blur_mask_x + self.blur_mask_w, self.blur_mask_y + self.blur_mask_h)
            blur_rect = QRect(bx1, by1, bx2 - bx1, by2 - by1)

            is_ellipse = any(k in str(self.blur_shape).lower() for k in ["tròn", "tron", "elip", "ellipse", "circle"])
            # 1. Làm mờ thực tế bên trong vùng Blur Mask
            if self.preview_pixmap and not self.preview_pixmap.isNull():
                bm_w = max(4, min(self.blur_mask_w, self.base_w - self.blur_mask_x))
                bm_h = max(4, min(self.blur_mask_h, self.base_h - self.blur_mask_y))
                bm_x = max(0, min(self.blur_mask_x, self.base_w - bm_w))
                bm_y = max(0, min(self.blur_mask_y, self.base_h - bm_h))
                chunk = self.preview_pixmap.copy(bm_x, bm_y, bm_w, bm_h)
                s_w = max(4, bm_w // 8)
                s_h = max(4, bm_h // 8)
                blurred_chunk = chunk.scaled(s_w, s_h, Qt.IgnoreAspectRatio, Qt.FastTransformation).scaled(blur_rect.width(), blur_rect.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                if is_ellipse:
                    path = QPainterPath()
                    path.addEllipse(float(blur_rect.x()), float(blur_rect.y()), float(blur_rect.width()), float(blur_rect.height()))
                    painter.save()
                    painter.setClipPath(path)
                    painter.drawPixmap(blur_rect.topLeft(), blurred_chunk)
                    painter.restore()
                else:
                    painter.drawPixmap(blur_rect.topLeft(), blurred_chunk)

            # 2. Viền cam và 4 chốt góc
            painter.setBrush(QBrush(QColor(250, 179, 135, 45)))
            pen_blur = QPen(QColor("#fab387"), 2, Qt.DashLine)
            painter.setPen(pen_blur)
            if is_ellipse:
                painter.drawEllipse(blur_rect)
            else:
                painter.drawRect(blur_rect)

            hs_b = 9
            half_b = hs_b // 2
            painter.setBrush(QBrush(QColor("#fab387")))
            painter.setPen(QPen(QColor("#11111b"), 1))
            painter.drawRect(bx1 - half_b, by1 - half_b, hs_b, hs_b)  # blur_tl
            painter.drawRect(bx2 - half_b, by1 - half_b, hs_b, hs_b)  # blur_tr
            painter.drawRect(bx1 - half_b, by2 - half_b, hs_b, hs_b)  # blur_bl
            painter.drawRect(bx2 - half_b, by2 - half_b, hs_b, hs_b)  # blur_br

            blur_tag = f"🛡️ Blur Mask ({self.blur_mask_w}×{self.blur_mask_h})"
            painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
            painter.drawRoundedRect(bx1 + 4, by1 + 4, 130, 20, 3, 3)
            painter.setPen(QColor("#fab387"))
            painter.drawText(QRect(bx1 + 8, by1 + 4, 125, 20), Qt.AlignVCenter, blur_tag)

        painter.restore()

        # Viền khung 9:16 tổng thể
        painter.setPen(QPen(QColor("#45475a"), 2))
        painter.drawRect(off_x, off_y, disp_w, disp_h)

        # Badge tiêu đề Chế độ chỉnh sửa
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(QColor(0, 0, 0, 180)))
        painter.drawRoundedRect(off_x + 8, off_y + 8, 170, 22, 4, 4)
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.setPen(QColor("#89b4fa"))
        painter.drawText(QRect(off_x + 12, off_y + 8, 160, 22), Qt.AlignVCenter, "📐 CHỈNH SỬA (9:16)")

        painter.end()


# ==============================================================================
# 3. CAPCUT CROP STUDIO DIALOG: POPUP GIAO DIỆN MODAL HOÀN CHỈNH
# ==============================================================================
class CapCutCropStudioDialog(QDialog):
    """
    Cửa sổ Popup độc lập CapCut Pro Video Crop & Blur Mask Studio (Fixed Resolution Edition)
    Triển khai 100% bố cục và tính năng theo PLAN_CROP_VA_BLUR_MASK.md.
    """
    def __init__(self, current_data: Optional[Dict[str, Any]] = None, sample_video_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("CapCut Pro Video Crop Studio (Fixed Resolution Edition)")
        self.resize(1260, 780)
        self.setMinimumSize(1020, 640)
        self.setStyleSheet("background-color: #1e1e2e; color: #cdd6f4;")

        self.sample_video_path = sample_video_path
        self.config_data = dict(current_data or {})

        self._build_ui()
        self._load_data_to_ui()

    def _build_ui(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(14)

        # ----------------------------------------------------------------------
        # HÀM TẠO SPINBOX SẠCH: BỎ ICON MŨI TÊN [▲▼], CĂN GIỮA, GÕ TAY TIỆN LỢI
        # ----------------------------------------------------------------------
        def _create_clean_spin(min_v: int, max_v: int, def_v: int) -> QSpinBox:
            sp = QSpinBox()
            sp.setRange(min_v, max_v)
            sp.setValue(def_v)
            sp.setButtonSymbols(QAbstractSpinBox.NoButtons)
            sp.setAlignment(Qt.AlignCenter)
            sp.setFixedHeight(30)
            sp.setStyleSheet("""
                QSpinBox {
                    background: #181825; color: #cdd6f4;
                    border: 1px solid #45475a; border-radius: 5px;
                    padding: 2px 4px; font-weight: bold; font-size: 13px;
                }
                QSpinBox:focus {
                    border: 1px solid #89b4fa;
                    background: #1e1e2e;
                }
                QSpinBox::up-button, QSpinBox::down-button {
                    width: 0px; height: 0px; border: none;
                }
            """)
            return sp

        # ----------------------------------------------------------------------
        # CỘT TRÁI: [⚙ CẤU HÌNH & HỆ TỌA ĐỘ GỐC]
        # ----------------------------------------------------------------------
        left_panel = QVBoxLayout()
        left_panel.setSpacing(10)
        left_panel.setContentsMargins(0, 0, 6, 0)

        # KHỐI 1: CẤU HÌNH CROP CHÍNH
        gb_crop = QGroupBox("✂️ CẤU HÌNH CẮT XÉN (CROP)")
        gb_crop.setStyleSheet("""
            QGroupBox {
                font-weight: bold; color: #89b4fa;
                border: 1px solid #45475a; border-radius: 6px;
                margin-top: 6px; padding-top: 10px;
            }
        """)
        l_crop = QVBoxLayout(gb_crop)
        l_crop.setSpacing(8)
        l_crop.setContentsMargins(10, 10, 10, 10)

        self.chk_use_crop = QCheckBox("Bật tính năng Cắt xén (Crop)")
        self.chk_use_crop.setStyleSheet("font-weight: bold; color: #a6e3a1; font-size: 13px;")
        self.chk_use_crop.setChecked(True)
        self.chk_use_crop.toggled.connect(self._on_use_crop_toggled)
        l_crop.addWidget(self.chk_use_crop)

        # Dòng chọn Tỷ lệ
        h_ratio = QHBoxLayout()
        lbl_ratio = QLabel("Tỷ lệ khung:")
        lbl_ratio.setStyleSheet("color: #cdd6f4; font-weight: bold; font-size: 12px;")
        self.cb_ratio = QComboBox()
        self.cb_ratio.addItems(["Tự do", "9:16", "16:9", "1:1", "4:5", "3:4"])
        self.cb_ratio.setFixedHeight(30)
        self.cb_ratio.setStyleSheet("background: #181825; color: #f9e2af; font-weight: bold; font-size: 12px; border: 1px solid #45475a; border-radius: 5px; padding: 2px 8px;")
        self.cb_ratio.currentTextChanged.connect(self._on_ratio_changed)
        h_ratio.addWidget(lbl_ratio)
        h_ratio.addWidget(self.cb_ratio, 1)
        l_crop.addLayout(h_ratio)

        # Lưới gọn gàng cho Crop: Gốc WxH, Tọa độ X,Y, Khung WxH
        grid_crop = QGridLayout()
        grid_crop.setHorizontalSpacing(8)
        grid_crop.setVerticalSpacing(8)

        lbl_base = QLabel("Gốc (W × H):")
        lbl_base.setStyleSheet("color: #94e2d5; font-size: 11px;")
        self.sp_base_w = _create_clean_spin(100, 7680, 1920)
        self.sp_base_h = _create_clean_spin(100, 4320, 1080)
        lbl_x_mul = QLabel("×")
        lbl_x_mul.setAlignment(Qt.AlignCenter)
        lbl_x_mul.setStyleSheet("color: #6c7086; font-weight: bold;")

        grid_crop.addWidget(lbl_base, 0, 0)
        grid_crop.addWidget(self.sp_base_w, 0, 1)
        grid_crop.addWidget(lbl_x_mul, 0, 2)
        grid_crop.addWidget(self.sp_base_h, 0, 3)

        lbl_xy = QLabel("Tọa độ (X, Y):")
        lbl_xy.setStyleSheet("color: #fab387; font-size: 11px;")
        self.sp_crop_x = _create_clean_spin(0, 7680, 0)
        self.sp_crop_y = _create_clean_spin(0, 4320, 0)
        lbl_comma = QLabel(",")
        lbl_comma.setAlignment(Qt.AlignCenter)
        lbl_comma.setStyleSheet("color: #6c7086; font-weight: bold;")

        grid_crop.addWidget(lbl_xy, 1, 0)
        grid_crop.addWidget(self.sp_crop_x, 1, 1)
        grid_crop.addWidget(lbl_comma, 1, 2)
        grid_crop.addWidget(self.sp_crop_y, 1, 3)

        lbl_wh = QLabel("Khung (W × H):")
        lbl_wh.setStyleSheet("color: #a6e3a1; font-size: 11px; font-weight: bold;")
        self.sp_crop_w = _create_clean_spin(MIN_CROP_SIZE, 7680, 1920)
        self.sp_crop_h = _create_clean_spin(MIN_CROP_SIZE, 4320, 1080)
        lbl_wh_mul = QLabel("×")
        lbl_wh_mul.setAlignment(Qt.AlignCenter)
        lbl_wh_mul.setStyleSheet("color: #6c7086; font-weight: bold;")

        grid_crop.addWidget(lbl_wh, 2, 0)
        grid_crop.addWidget(self.sp_crop_w, 2, 1)
        grid_crop.addWidget(lbl_wh_mul, 2, 2)
        grid_crop.addWidget(self.sp_crop_h, 2, 3)

        grid_crop.setColumnStretch(1, 1)
        grid_crop.setColumnStretch(3, 1)
        l_crop.addLayout(grid_crop)
        left_panel.addWidget(gb_crop)

        # KHỐI 1.5: 🔍 TỈ LỆ ZOOM & SCALE (ĐỒNG BỘ CẤU HÌNH RENDER)
        gb_zoom = QGroupBox("🔍 TỈ LỆ ZOOM & SCALE (KHỚP RENDER)")
        gb_zoom.setStyleSheet("""
            QGroupBox {
                font-weight: bold; color: #89b4fa;
                border: 1px solid #45475a; border-radius: 6px;
                margin-top: 4px; padding-top: 10px;
            }
        """)
        l_zoom = QHBoxLayout(gb_zoom)
        l_zoom.setSpacing(8)
        l_zoom.setContentsMargins(10, 8, 10, 8)

        lbl_zm = QLabel("Zoom:")
        lbl_zm.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        self.sp_zoom_in = _create_clean_spin(50, 300, 178)
        self.sp_zoom_in.setSuffix(" %")
        self.sp_zoom_in.valueChanged.connect(self._on_zoom_scale_changed)

        lbl_sx = QLabel("↔️ Scale X:")
        lbl_sx.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        self.sp_scale_x = _create_clean_spin(50, 200, 100)
        self.sp_scale_x.setSuffix(" %")
        self.sp_scale_x.valueChanged.connect(self._on_zoom_scale_changed)

        lbl_sy = QLabel("↕️ Scale Y:")
        lbl_sy.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        self.sp_scale_y = _create_clean_spin(50, 200, 130)
        self.sp_scale_y.setSuffix(" %")
        self.sp_scale_y.valueChanged.connect(self._on_zoom_scale_changed)

        l_zoom.addWidget(lbl_zm)
        l_zoom.addWidget(self.sp_zoom_in)
        l_zoom.addWidget(lbl_sx)
        l_zoom.addWidget(self.sp_scale_x)
        l_zoom.addWidget(lbl_sy)
        l_zoom.addWidget(self.sp_scale_y)
        left_panel.addWidget(gb_zoom)

        self.chk_blur_bg = QCheckBox("🌫️ Làm mờ nền 2 đầu (Blur Background)")
        self.chk_blur_bg.setStyleSheet("color: #a6e3a1; font-weight: bold; font-size: 11px; margin-top: 2px; margin-bottom: 2px;")
        self.chk_blur_bg.setToolTip("Tích chọn: Nền 2 đầu trên/dưới bị làm mờ (Blur)\nBỏ tích: Nền 2 đầu màu đen tuyền (#000000) không bị mờ")
        self.chk_blur_bg.setChecked(True)
        self.chk_blur_bg.toggled.connect(self._on_blur_bg_toggled)
        left_panel.addWidget(self.chk_blur_bg)

        # KHỐI 2: 🛡️ LÀM MỜ VÙNG CHỌN (BLUR MASK)
        gb_blur = QGroupBox("🛡️ LÀM MỜ VÙNG CHỌN (BLUR MASK)")
        gb_blur.setStyleSheet("""
            QGroupBox {
                font-weight: bold; color: #fab387;
                border: 1px solid #45475a; border-radius: 6px;
                margin-top: 4px; padding-top: 10px;
            }
        """)
        l_blur = QVBoxLayout(gb_blur)
        l_blur.setSpacing(8)
        l_blur.setContentsMargins(10, 10, 10, 10)

        self.chk_use_blur = QCheckBox("Bật làm mờ 1 phần video (Che logo / sub)")
        self.chk_use_blur.setStyleSheet("font-weight: bold; color: #fab387; font-size: 12px;")
        self.chk_use_blur.setChecked(False)
        self.chk_use_blur.toggled.connect(self._on_use_blur_toggled)
        l_blur.addWidget(self.chk_use_blur)

        h_shape = QHBoxLayout()
        lbl_shape = QLabel("Hình dạng:")
        lbl_shape.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        self.cb_blur_shape = QComboBox()
        self.cb_blur_shape.addItem("Hình chữ nhật")
        self.cb_blur_shape.addItem("Hình tròn / Elip")
        self.cb_blur_shape.setFixedHeight(28)
        self.cb_blur_shape.setStyleSheet("background: #181825; color: #cdd6f4; border: 1px solid #45475a; border-radius: 4px; padding: 2px 6px;")
        self.cb_blur_shape.currentTextChanged.connect(self._on_blur_shape_changed)
        h_shape.addWidget(lbl_shape)
        h_shape.addWidget(self.cb_blur_shape, 1)
        l_blur.addLayout(h_shape)

        grid_blur = QGridLayout()
        grid_blur.setHorizontalSpacing(8)
        grid_blur.setVerticalSpacing(8)

        lbl_b_xy = QLabel("Tọa độ (X, Y):")
        lbl_b_xy.setStyleSheet("color: #cdd6f4; font-size: 11px;")
        self.sp_blur_x = _create_clean_spin(0, 7680, 20)
        self.sp_blur_y = _create_clean_spin(0, 4320, 861)
        lbl_b_comma = QLabel(",")
        lbl_b_comma.setAlignment(Qt.AlignCenter)
        lbl_b_comma.setStyleSheet("color: #6c7086; font-weight: bold;")

        grid_blur.addWidget(lbl_b_xy, 0, 0)
        grid_blur.addWidget(self.sp_blur_x, 0, 1)
        grid_blur.addWidget(lbl_b_comma, 0, 2)
        grid_blur.addWidget(self.sp_blur_y, 0, 3)

        lbl_b_wh = QLabel("Khung (W × H):")
        lbl_b_wh.setStyleSheet("color: #fab387; font-size: 11px; font-weight: bold;")
        self.sp_blur_w = _create_clean_spin(MIN_BLUR_SIZE, 7680, 932)
        self.sp_blur_h = _create_clean_spin(MIN_BLUR_SIZE, 4320, 210)
        lbl_b_wh_mul = QLabel("×")
        lbl_b_wh_mul.setAlignment(Qt.AlignCenter)
        lbl_b_wh_mul.setStyleSheet("color: #6c7086; font-weight: bold;")

        grid_blur.addWidget(lbl_b_wh, 1, 0)
        grid_blur.addWidget(self.sp_blur_w, 1, 1)
        grid_blur.addWidget(lbl_b_wh_mul, 1, 2)
        grid_blur.addWidget(self.sp_blur_h, 1, 3)

        grid_blur.setColumnStretch(1, 1)
        grid_blur.setColumnStretch(3, 1)
        l_blur.addLayout(grid_blur)
        left_panel.addWidget(gb_blur)

        # KHỐI 3: THÔNG TIN QUY CHIẾU
        gb_info = QGroupBox("📋 THÔNG TIN QUY CHIẾU")
        gb_info.setStyleSheet("QGroupBox { font-weight: bold; color: #a6adc8; border: 1px solid #313244; border-radius: 6px; margin-top: 4px; padding-top: 8px; }")
        l_info = QVBoxLayout(gb_info)
        l_info.setSpacing(4)
        l_info.setContentsMargins(10, 8, 10, 8)
        self.lbl_info_base = QLabel("Base Canvas: 1920 × 1080")
        self.lbl_info_base.setStyleSheet("color: #94e2d5; font-size: 11px; font-weight: bold;")
        self.lbl_info_crop = QLabel("Crop: X=0, Y=0 | W=1920 × H=1080")
        self.lbl_info_crop.setStyleSheet("color: #f9e2af; font-size: 11px;")
        l_info.addWidget(self.lbl_info_base)
        l_info.addWidget(self.lbl_info_crop)
        left_panel.addWidget(gb_info)

        # KHỐI 3.5: 🔗 LẤY ẢNH MẪU TỪ LINK YOUTUBE
        gb_yt = QGroupBox("🔗 LẤY ẢNH MẪU TỪ LINK YOUTUBE")
        gb_yt.setStyleSheet("""
            QGroupBox {
                font-weight: bold; color: #f38ba8;
                border: 1px solid #45475a; border-radius: 6px;
                margin-top: 4px; padding-top: 10px;
            }
        """)
        l_yt = QVBoxLayout(gb_yt)
        l_yt.setSpacing(8)
        l_yt.setContentsMargins(10, 10, 10, 10)

        self.txt_yt_url = QLineEdit()
        self.txt_yt_url.setPlaceholderText("Dán link YouTube (Watch, Shorts, ID)...")
        self.txt_yt_url.setFixedHeight(30)
        self.txt_yt_url.setStyleSheet("background: #181825; color: #cdd6f4; border: 1px solid #45475a; border-radius: 5px; padding: 4px 8px; font-size: 11px;")
        l_yt.addWidget(self.txt_yt_url)

        h_yt_btns = QHBoxLayout()
        h_yt_btns.setSpacing(8)

        self.btn_fetch_yt_thumb = QPushButton("⚡ Lấy Thumbnail")
        self.btn_fetch_yt_thumb.setFixedHeight(32)
        self.btn_fetch_yt_thumb.setStyleSheet("background: #89b4fa; color: #11111b; font-weight: bold; padding: 4px; border-radius: 5px; font-size: 11px;")
        self.btn_fetch_yt_thumb.setToolTip("Tải nhanh ảnh bìa HD (Maxres 1920x1080) trong 0.5 giây")
        self.btn_fetch_yt_thumb.clicked.connect(self.action_fetch_youtube_thumbnail)
        h_yt_btns.addWidget(self.btn_fetch_yt_thumb, 1)

        self.btn_fetch_yt_frame = QPushButton("🎬 Bốc Frame (2s)")
        self.btn_fetch_yt_frame.setFixedHeight(32)
        self.btn_fetch_yt_frame.setStyleSheet("background: #fab387; color: #11111b; font-weight: bold; padding: 4px; border-radius: 5px; font-size: 11px;")
        self.btn_fetch_yt_frame.setToolTip("Tải nhanh 3 giây video bằng yt-dlp và trích xuất frame tại giây 2.0 (Chân thực 100%)")
        self.btn_fetch_yt_frame.clicked.connect(self.action_fetch_youtube_video_frame)
        h_yt_btns.addWidget(self.btn_fetch_yt_frame, 1)

        l_yt.addLayout(h_yt_btns)

        self.lbl_yt_status = QLabel("")
        self.lbl_yt_status.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self.lbl_yt_status.setWordWrap(True)
        l_yt.addWidget(self.lbl_yt_status)

        left_panel.addWidget(gb_yt)

        # CÁC NÚT BẤM THAO TÁC
        h_tools = QHBoxLayout()
        h_tools.setSpacing(8)
        self.btn_reload_frame = QPushButton("🔄 Load lại frame")
        self.btn_reload_frame.setFixedHeight(32)
        self.btn_reload_frame.setStyleSheet("background: #313244; color: #cdd6f4; font-weight: bold; border-radius: 5px; font-size: 11px;")
        self.btn_reload_frame.clicked.connect(self.action_reload_frame)
        h_tools.addWidget(self.btn_reload_frame)

        self.btn_load_custom_img = QPushButton("📂 Tải ảnh mẫu...")
        self.btn_load_custom_img.setFixedHeight(32)
        self.btn_load_custom_img.setStyleSheet("background: #313244; color: #89b4fa; font-weight: bold; border-radius: 5px; font-size: 11px;")
        self.btn_load_custom_img.clicked.connect(self.action_pick_custom_image)
        h_tools.addWidget(self.btn_load_custom_img)
        left_panel.addLayout(h_tools)

        self.btn_reset_crop = QPushButton("↩ Reset Về Toàn Màn Hình")
        self.btn_reset_crop.setFixedHeight(32)
        self.btn_reset_crop.setStyleSheet("background: #313244; color: #f38ba8; font-weight: bold; border-radius: 5px; font-size: 11px;")
        self.btn_reset_crop.clicked.connect(self.action_reset_crop)
        left_panel.addWidget(self.btn_reset_crop)

        left_panel.addStretch(1)

        # Bọc toàn bộ nội dung cấu hình cột trái vào QScrollArea mượt mà
        left_scroll_content = QWidget()
        left_scroll_content.setLayout(left_panel)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: #181825;
                width: 8px;
                margin: 0px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #45475a;
                min-height: 25px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #585b70;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)
        scroll_area.setWidget(left_scroll_content)

        # Cột trái cố định bề rộng 385px, nút Lưu cấu hình cố định ở đáy
        left_container = QWidget()
        left_container.setFixedWidth(385)
        left_outer_layout = QVBoxLayout(left_container)
        left_outer_layout.setContentsMargins(0, 0, 0, 0)
        left_outer_layout.setSpacing(8)

        left_outer_layout.addWidget(scroll_area, 1)

        # Nút Lưu cấu hình luôn luôn nằm cố định dưới đáy
        self.btn_save_config = QPushButton("💾 Lưu Cấu Hình Cắt Xén & Làm Mờ")
        self.btn_save_config.setFixedHeight(42)
        self.btn_save_config.setCursor(Qt.PointingHandCursor)
        self.btn_save_config.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #a6e3a1, stop:1 #94e2d5);
                color: #11111b; font-weight: bold; font-size: 13px;
                border-radius: 6px;
                border: none;
            }
            QPushButton:hover {
                background: #a6e3a1;
            }
        """)
        self.btn_save_config.clicked.connect(self.action_save_and_close)
        left_outer_layout.addWidget(self.btn_save_config)

        root.addWidget(left_container)

        # ----------------------------------------------------------------------
        # CỘT PHẢI: [✂ PREVIEW & KÉO CO BÓP TRỰC QUAN]
        # ----------------------------------------------------------------------
        right_panel = QVBoxLayout()
        right_panel.setSpacing(6)

        header_layout = QHBoxLayout()
        header_lbl = QLabel("✂️ MÔ PHỎNG ĐẦU RA 9:16 (CHUẨN XÁC 100% KHI RENDER)")
        header_lbl.setStyleSheet("font-weight: bold; color: #a6e3a1; font-size: 13px;")
        header_layout.addWidget(header_lbl)
        header_layout.addStretch()

        self.btn_mode_edit = QPushButton("✏️ Chỉnh Sửa Crop & Blur")
        self.btn_mode_edit.setFixedHeight(30)
        self.btn_mode_edit.setCursor(Qt.PointingHandCursor)
        self.btn_mode_edit.setStyleSheet("""
            QPushButton {
                background: #89b4fa; color: #11111b; font-weight: bold;
                border-radius: 5px; padding: 4px 12px; font-size: 11px;
            }
            QPushButton:hover { background: #b4befe; }
        """)
        self.btn_mode_edit.clicked.connect(lambda: self._set_view_mode("edit"))
        header_layout.addWidget(self.btn_mode_edit)

        self.btn_mode_preview = QPushButton("👁️ Xem Trước Đầu Ra (9:16)")
        self.btn_mode_preview.setFixedHeight(30)
        self.btn_mode_preview.setCursor(Qt.PointingHandCursor)
        self.btn_mode_preview.setStyleSheet("""
            QPushButton {
                background: #313244; color: #cdd6f4; font-weight: bold;
                border-radius: 5px; padding: 4px 12px; font-size: 11px;
            }
            QPushButton:hover { background: #45475a; }
        """)
        self.btn_mode_preview.clicked.connect(lambda: self._set_view_mode("preview"))
        header_layout.addWidget(self.btn_mode_preview)

        right_panel.addLayout(header_layout)

        self.canvas = CropInteractiveCanvas(self)
        right_panel.addWidget(self.canvas, 1)

        root.addLayout(right_panel, 1)

        # KẾT NỐI SIGNALS ĐỒNG BỘ 2 CHIỀU GIỮA CANVAS VÀ SPINBOX
        self.canvas.crop_changed.connect(self._on_canvas_crop_changed)
        self.canvas.blur_mask_changed.connect(self._on_canvas_blur_changed)

        self.sp_crop_x.valueChanged.connect(self._on_spinbox_crop_changed)
        self.sp_crop_y.valueChanged.connect(self._on_spinbox_crop_changed)
        self.sp_crop_w.valueChanged.connect(self._on_spinbox_crop_changed)
        self.sp_crop_h.valueChanged.connect(self._on_spinbox_crop_changed)

        self.sp_blur_x.valueChanged.connect(self._on_spinbox_blur_changed)
        self.sp_blur_y.valueChanged.connect(self._on_spinbox_blur_changed)
        self.sp_blur_w.valueChanged.connect(self._on_spinbox_blur_changed)
        self.sp_blur_h.valueChanged.connect(self._on_spinbox_blur_changed)

    # --------------------------------------------------------------------------
    # NẠP DỮ LIỆU CẤU HÌNH BAN ĐẦU
    # --------------------------------------------------------------------------
    def _load_data_to_ui(self):
        cfg = self.config_data
        self.chk_use_crop.setChecked(cfg.get("use_crop", True))
        self.cb_ratio.setCurrentText(cfg.get("crop_ratio", "Tự do"))

        self.sp_base_w.setValue(int(cfg.get("base_w", DEFAULT_BASE_W)))
        self.sp_base_h.setValue(int(cfg.get("base_h", DEFAULT_BASE_H)))
        self.sp_crop_w.setValue(int(cfg.get("crop_w", DEFAULT_BASE_W)))
        self.sp_crop_h.setValue(int(cfg.get("crop_h", DEFAULT_BASE_H)))
        self.sp_crop_x.setValue(int(cfg.get("crop_x", 0)))
        self.sp_crop_y.setValue(int(cfg.get("crop_y", 0)))

        self.chk_use_blur.setChecked(bool(cfg.get("use_blur_mask", False)))
        self.cb_blur_shape.setCurrentText(cfg.get("blur_shape", "Hình chữ nhật"))
        self.sp_blur_x.setValue(int(cfg.get("blur_mask_x", 20)))
        self.sp_blur_y.setValue(int(cfg.get("blur_mask_y", 861)))
        self.sp_blur_w.setValue(int(cfg.get("blur_mask_w", 932)))
        self.sp_blur_h.setValue(int(cfg.get("blur_mask_h", 210)))

        self.sp_zoom_in.setValue(int(round(float(cfg.get("zoom_in", 178.0)))))
        self.sp_scale_x.setValue(int(round(float(cfg.get("scale_x", 100.0)))))
        self.sp_scale_y.setValue(int(round(float(cfg.get("scale_y", 130.0)))))
        self.chk_blur_bg.setChecked(bool(cfg.get("blur_bg", True)))

        # Nạp vào Canvas
        self.canvas.base_w = self.sp_base_w.value()
        self.canvas.base_h = self.sp_base_h.value()
        self.canvas.use_crop = self.chk_use_crop.isChecked()
        self.canvas.crop_ratio = self.cb_ratio.currentText()
        self.canvas.crop_x = self.sp_crop_x.value()
        self.canvas.crop_y = self.sp_crop_y.value()
        self.canvas.crop_w = self.sp_crop_w.value()
        self.canvas.crop_h = self.sp_crop_h.value()

        self.canvas.use_blur_mask = self.chk_use_blur.isChecked()
        self.canvas.blur_shape = self.cb_blur_shape.currentText()
        self.canvas.blur_mask_x = self.sp_blur_x.value()
        self.canvas.blur_mask_y = self.sp_blur_y.value()
        self.canvas.blur_mask_w = self.sp_blur_w.value()
        self.canvas.blur_mask_h = self.sp_blur_h.value()

        self.canvas.zoom_in = float(self.sp_zoom_in.value())
        self.canvas.scale_x = float(self.sp_scale_x.value())
        self.canvas.scale_y = float(self.sp_scale_y.value())
        self.canvas.blur_bg = self.chk_blur_bg.isChecked()

        # Nạp ảnh từ Cache nếu có (mở popup tức thì 0.001s, Zero Delay!)
        has_cache = False
        if PREVIEW_CACHE_FILE.exists():
            try:
                cache_pix = QPixmap(str(PREVIEW_CACHE_FILE))
                if cache_pix and not cache_pix.isNull():
                    self.canvas.set_preview_pixmap(cache_pix)
                    self.sp_base_w.setValue(cache_pix.width())
                    self.sp_base_h.setValue(cache_pix.height())
                    has_cache = True
            except Exception:
                pass

        if not has_cache:
            self.canvas.set_preview_pixmap(None)

        self._update_info_labels()

        # Bốc frame video ngầm bằng QThread sau khi cửa sổ đã hiện lên để tuyệt đối không block UI
        QTimer.singleShot(25, self._start_async_frame_reload)

    def _set_view_mode(self, mode: str):
        if mode == "preview":
            self.canvas.show_live_output = True
            self.btn_mode_preview.setStyleSheet("""
                QPushButton {
                    background: #a6e3a1; color: #11111b; font-weight: bold;
                    border-radius: 5px; padding: 4px 12px; font-size: 11px;
                }
                QPushButton:hover { background: #94e2d5; }
            """)
            self.btn_mode_edit.setStyleSheet("""
                QPushButton {
                    background: #313244; color: #cdd6f4; font-weight: bold;
                    border-radius: 5px; padding: 4px 12px; font-size: 11px;
                }
                QPushButton:hover { background: #45475a; }
            """)
        else:
            self.canvas.show_live_output = False
            self.btn_mode_edit.setStyleSheet("""
                QPushButton {
                    background: #89b4fa; color: #11111b; font-weight: bold;
                    border-radius: 5px; padding: 4px 12px; font-size: 11px;
                }
                QPushButton:hover { background: #b4befe; }
            """)
            self.btn_mode_preview.setStyleSheet("""
                QPushButton {
                    background: #313244; color: #cdd6f4; font-weight: bold;
                    border-radius: 5px; padding: 4px 12px; font-size: 11px;
                }
                QPushButton:hover { background: #45475a; }
            """)
        self.canvas.update()

    # --------------------------------------------------------------------------
    # ĐỒNG BỘ 2 CHIỀU (CANVAS <-> SPINBOX)
    # --------------------------------------------------------------------------
    def _on_canvas_crop_changed(self, data: dict):
        self.sp_crop_x.blockSignals(True)
        self.sp_crop_y.blockSignals(True)
        self.sp_crop_w.blockSignals(True)
        self.sp_crop_h.blockSignals(True)

        self.sp_crop_x.setValue(data["x"])
        self.sp_crop_y.setValue(data["y"])
        self.sp_crop_w.setValue(data["w"])
        self.sp_crop_h.setValue(data["h"])

        self.sp_crop_x.blockSignals(False)
        self.sp_crop_y.blockSignals(False)
        self.sp_crop_w.blockSignals(False)
        self.sp_crop_h.blockSignals(False)

        self._update_info_labels()

    def _on_canvas_blur_changed(self, data: dict):
        self.sp_blur_x.blockSignals(True)
        self.sp_blur_y.blockSignals(True)
        self.sp_blur_w.blockSignals(True)
        self.sp_blur_h.blockSignals(True)

        self.sp_blur_x.setValue(data["x"])
        self.sp_blur_y.setValue(data["y"])
        self.sp_blur_w.setValue(data["w"])
        self.sp_blur_h.setValue(data["h"])

        self.sp_blur_x.blockSignals(False)
        self.sp_blur_y.blockSignals(False)
        self.sp_blur_w.blockSignals(False)
        self.sp_blur_h.blockSignals(False)

    def _on_spinbox_crop_changed(self):
        self.canvas.crop_x = self.sp_crop_x.value()
        self.canvas.crop_y = self.sp_crop_y.value()
        self.canvas.crop_w = self.sp_crop_w.value()
        self.canvas.crop_h = self.sp_crop_h.value()
        self.canvas.clamp_crop()
        self.canvas.update()
        self._update_info_labels()

    def _on_spinbox_blur_changed(self):
        self.canvas.blur_mask_x = self.sp_blur_x.value()
        self.canvas.blur_mask_y = self.sp_blur_y.value()
        self.canvas.blur_mask_w = self.sp_blur_w.value()
        self.canvas.blur_mask_h = self.sp_blur_h.value()
        self.canvas.clamp_blur_mask()
        self.canvas.update()

    def _on_use_crop_toggled(self, checked: bool):
        self.canvas.use_crop = checked
        self.canvas.update()

    def _on_use_blur_toggled(self, checked: bool):
        self.canvas.use_blur_mask = checked
        self.canvas.update()

    def _on_blur_shape_changed(self, shape_text: str):
        self.canvas.blur_shape = shape_text
        self.canvas.update()

    def _on_ratio_changed(self, ratio_text: str):
        self.canvas.crop_ratio = ratio_text
        ratio_val = self.canvas._get_ratio_aspect()
        if ratio_val is not None:
            nw = self.canvas.crop_w
            nh = int(nw / ratio_val)
            if self.canvas.crop_y + nh > self.canvas.base_h:
                nh = self.canvas.base_h - self.canvas.crop_y
                nw = int(nh * ratio_val)
            self.canvas.crop_w = nw
            self.canvas.crop_h = nh
            self.canvas.clamp_crop()
            self._on_canvas_crop_changed({
                "x": self.canvas.crop_x, "y": self.canvas.crop_y,
                "w": self.canvas.crop_w, "h": self.canvas.crop_h
            })
        self.canvas.update()

    def _on_zoom_scale_changed(self):
        self.canvas.zoom_in = float(self.sp_zoom_in.value())
        self.canvas.scale_x = float(self.sp_scale_x.value())
        self.canvas.scale_y = float(self.sp_scale_y.value())
        self.canvas.update()

    def _on_blur_bg_toggled(self, checked: bool):
        self.canvas.blur_bg = checked
        self.canvas.update()

    def _update_info_labels(self):
        self.lbl_info_base.setText(f"Base Canvas: {self.canvas.base_w} × {self.canvas.base_h}")
        self.lbl_info_crop.setText(f"Crop: X={self.canvas.crop_x}, Y={self.canvas.crop_y} | W={self.canvas.crop_w} × H={self.canvas.crop_h}")

    # --------------------------------------------------------------------------
    # THAO TÁC NẠP FRAME MẪU & RESET
    # --------------------------------------------------------------------------
    def _find_best_sample_video(self) -> str:
        target = self.sample_video_path
        if target and os.path.exists(target):
            return target
        dl_dir = APP_DIR / "downloads"
        if dl_dir.exists():
            mp4s = [f for f in dl_dir.glob("*.mp4") if not ".tmp." in f.name and f.stat().st_size > 100000]
            if mp4s:
                mp4s.sort(key=lambda f: f.stat().st_mtime, reverse=True)
                return str(mp4s[0])
        return ""

    def _start_async_frame_reload(self):
        video_path = self._find_best_sample_video()
        if not video_path:
            return

        # Nếu file cache đã có và mới hơn file video thì không cần bốc lại
        if PREVIEW_CACHE_FILE.exists():
            try:
                v_mtime = os.path.getmtime(video_path)
                c_mtime = PREVIEW_CACHE_FILE.stat().st_mtime
                if c_mtime >= v_mtime and self.canvas.preview_pixmap and not self.canvas.preview_pixmap.isNull():
                    return
            except Exception:
                pass

        self._extractor_thread = AsyncVideoFrameExtractor(video_path, timestamp_sec=2.0, parent=self)
        self._extractor_thread.frame_ready.connect(self._on_async_frame_ready)
        self._extractor_thread.start()

    def _on_async_frame_ready(self, qimg, w, h):
        if qimg is not None and not qimg.isNull():
            pix = QPixmap.fromImage(qimg)
            self.canvas.set_preview_pixmap(pix)
            self.sp_base_w.setValue(w)
            self.sp_base_h.setValue(h)
            self._update_info_labels()

    def action_reload_frame(self):
        video_path = self._find_best_sample_video()
        if not video_path:
            self.lbl_yt_status.setText("⚠️ Chưa tìm thấy video MP4 hợp lệ trong thư mục downloads!")
            self.lbl_yt_status.setStyleSheet("color: #f9e2af; font-size: 11px;")
            return

        self.btn_reload_frame.setEnabled(False)
        self.btn_reload_frame.setText("⏳ Đang đọc...")

        def _on_done(qimg, w, h):
            self.btn_reload_frame.setEnabled(True)
            self.btn_reload_frame.setText("🔄 Load lại frame")
            self._on_async_frame_ready(qimg, w, h)
            if qimg:
                self.lbl_yt_status.setText(f"✅ Đã nạp frame video ({w}×{h})!")
                self.lbl_yt_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
            else:
                self.lbl_yt_status.setText("❌ Không đọc được frame từ video này.")
                self.lbl_yt_status.setStyleSheet("color: #f38ba8; font-size: 11px;")

        self._manual_extractor = AsyncVideoFrameExtractor(video_path, timestamp_sec=2.0, parent=self)
        self._manual_extractor.frame_ready.connect(_on_done)
        self._manual_extractor.start()

    def action_pick_custom_image(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh mẫu đại diện", "", "Image Files (*.png *.jpg *.jpeg *.bmp)")
        if fp and os.path.exists(fp):
            pix = QPixmap(fp)
            if not pix.isNull():
                self.canvas.set_preview_pixmap(pix)
                self.sp_base_w.setValue(pix.width())
                self.sp_base_h.setValue(pix.height())
                self._update_info_labels()

    # --------------------------------------------------------------------------
    # TRÍCH XUẤT ẢNH MẪU TỪ LINK YOUTUBE
    # --------------------------------------------------------------------------
    def _extract_youtube_id(self, url: str) -> str:
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)',
            r'(?:shorts\/)([0-9A-Za-z_-]{11})(?:[&?]|$)',
            r'youtu\.be\/([0-9A-Za-z_-]{11})(?:[&?]|$)',
            r'^([0-9A-Za-z_-]{11})$'
        ]
        for p in patterns:
            m = re.search(p, url.strip())
            if m:
                return m.group(1)
        return ""

    def action_fetch_youtube_thumbnail(self):
        url = self.txt_yt_url.text().strip()
        vid = self._extract_youtube_id(url)
        if not vid:
            self.lbl_yt_status.setText("❌ Link không hợp lệ! Hãy dán link YouTube.")
            self.lbl_yt_status.setStyleSheet("color: #f38ba8; font-size: 10px;")
            return

        self.lbl_yt_status.setText("⏳ Đang tải thumbnail YouTube...")
        self.lbl_yt_status.setStyleSheet("color: #89b4fa; font-size: 10px;")
        self.btn_fetch_yt_thumb.setEnabled(False)
        self.btn_fetch_yt_frame.setEnabled(False)

        self._yt_worker = YouTubeSampleFetcher("thumb", vid, self)
        self._yt_worker.finished_sig.connect(self._on_yt_sample_fetched)
        self._yt_worker.start()

    def action_fetch_youtube_video_frame(self):
        url = self.txt_yt_url.text().strip()
        vid = self._extract_youtube_id(url)
        if not vid:
            self.lbl_yt_status.setText("❌ Link không hợp lệ! Hãy dán link YouTube.")
            self.lbl_yt_status.setStyleSheet("color: #f38ba8; font-size: 10px;")
            return

        self.lbl_yt_status.setText("⏳ Đang bốc frame video bằng yt-dlp...")
        self.lbl_yt_status.setStyleSheet("color: #fab387; font-size: 10px;")
        self.btn_fetch_yt_thumb.setEnabled(False)
        self.btn_fetch_yt_frame.setEnabled(False)

        self._yt_worker = YouTubeSampleFetcher("frame", vid, self)
        self._yt_worker.finished_sig.connect(self._on_yt_sample_fetched)
        self._yt_worker.start()

    def _on_yt_sample_fetched(self, pix: Optional[QPixmap], msg: str, color_hex: str):
        self.btn_fetch_yt_thumb.setEnabled(True)
        self.btn_fetch_yt_frame.setEnabled(True)
        self.lbl_yt_status.setText(msg)
        self.lbl_yt_status.setStyleSheet(f"color: {color_hex}; font-size: 10px;")
        if pix and not pix.isNull():
            self.canvas.set_preview_pixmap(pix)
            self.sp_base_w.setValue(pix.width())
            self.sp_base_h.setValue(pix.height())
            self._update_info_labels()

    def action_reset_crop(self):
        self.sp_crop_x.setValue(0)
        self.sp_crop_y.setValue(0)
        self.sp_crop_w.setValue(self.canvas.base_w)
        self.sp_crop_h.setValue(self.canvas.base_h)
        self.cb_ratio.setCurrentText("Tự do")
        self.canvas.crop_x = 0
        self.canvas.crop_y = 0
        self.canvas.crop_w = self.canvas.base_w
        self.canvas.crop_h = self.canvas.base_h
        self.canvas.update()
        self._update_info_labels()

    def action_save_and_close(self):
        self.accept()

    def get_values(self) -> dict:
        """Trả về toàn bộ thông số cấu hình theo đúng Schema mục 2 trong PLAN"""
        return {
            "use_crop": self.chk_use_crop.isChecked(),
            "crop_ratio": self.cb_ratio.currentText(),
            "base_w": self.sp_base_w.value(),
            "base_h": self.sp_base_h.value(),
            "crop_w": self.sp_crop_w.value(),
            "crop_h": self.sp_crop_h.value(),
            "crop_x": self.sp_crop_x.value(),
            "crop_y": self.sp_crop_y.value(),

            "use_blur_mask": self.chk_use_blur.isChecked(),
            "blur_shape": self.cb_blur_shape.currentText(),
            "blur_mask_x": self.sp_blur_x.value(),
            "blur_mask_y": self.sp_blur_y.value(),
            "blur_mask_w": self.sp_blur_w.value(),
            "blur_mask_h": self.sp_blur_h.value(),
            "zoom_in": self.sp_zoom_in.value(),
            "scale_x": self.sp_scale_x.value(),
            "scale_y": self.sp_scale_y.value(),
            "blur_bg": self.chk_blur_bg.isChecked()
        }


# ==============================================================================
# 4. THUẬT TOÁN XÂY DỰNG FFMPEG FILTER COMPLEX (Mục 6 trong PLAN)
# ==============================================================================
def build_crop_and_blur_filters(crop_cfg: dict, fg_w: int, fg_h: int, blur_bg: bool = True) -> Tuple[str, str]:
    """
    Xây dựng chuỗi FFmpeg filter_complex theo mục 6 trong PLAN_CROP_VA_BLUR_MASK.md:
    
    1. Chuẩn hóa Canvas đầu vào (Mục 6.1):
    2. Background:
       - Nếu blur_bg=True (Tích chọn làm mờ): Fast Boxblur 360p -> 1080x1920 (Mục 6.2)
       - Nếu blur_bg=False (Không tích chọn): Nền đen tuyền chuẩn color=c=black:s=1080x1920[bg]
    3. Foreground Crop (Mục 6.3):
       [v_ref]crop=crop_w:crop_h:crop_x:crop_y[v_cropped]
    4. Blur Mask Sub-stream (Mục 6.4):
       Cắt phân đoạn nhỏ logo/watermark -> boxblur -> overlay đè lại -> scale fg_w:fg_h[fg]
    5. Ghép Foreground lên Background:
       [bg][fg]overlay=(W-w)/2:(H-h)/2[v_canvas]
    """
    use_crop = crop_cfg.get("use_crop", True)
    crop_w = int(crop_cfg.get("crop_w", 1920))
    crop_h = int(crop_cfg.get("crop_h", 1080))
    crop_x = int(crop_cfg.get("crop_x", 0))
    crop_y = int(crop_cfg.get("crop_y", 0))

    # Đảm bảo tọa độ crop luôn nằm trọn trong khung 1920x1080
    crop_x = max(0, min(1918, crop_x))
    crop_y = max(0, min(1078, crop_y))
    if crop_x + crop_w > 1920:
        crop_w = 1920 - crop_x
    if crop_y + crop_h > 1080:
        crop_h = 1080 - crop_y
    if crop_w < 64: crop_w = 64
    if crop_h < 64: crop_h = 64

    use_blur = crop_cfg.get("use_blur_mask", False)
    bm_x = int(crop_cfg.get("blur_mask_x", 20))
    bm_y = int(crop_cfg.get("blur_mask_y", 861))
    bm_w = int(crop_cfg.get("blur_mask_w", 932))
    bm_h = int(crop_cfg.get("blur_mask_h", 210))

    is_blur_bg = bool(crop_cfg.get("blur_bg", blur_bg))

    if crop_w % 2 != 0: crop_w -= 1
    if crop_h % 2 != 0: crop_h -= 1
    if bm_w % 2 != 0: bm_w -= 1
    if bm_h % 2 != 0: bm_h -= 1
    if fg_w % 2 != 0: fg_w -= 1
    if fg_h % 2 != 0: fg_h -= 1

    filters = []

    if is_blur_bg:
        # 6.1 Chuẩn hóa Canvas đầu vào (1920x1080) và phân tách nhánh nền mờ
        filters.append(
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1,split=2[v_ref][v_ref_bg]"
        )
        # 6.2 Nhánh Nền Mờ Siêu Tốc (Background Blur 360p -> 1080x1920)
        filters.append(
            "[v_ref_bg]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,"
            "boxblur=12:6,scale=1080:1920:flags=bilinear[bg]"
        )
    else:
        # Khi KHÔNG tích chọn blur: Nền đen tuyền 1080x1920 (#000000), 2 đầu đen chuẩn không bị mờ!
        filters.append(
            "color=c=black:s=1080x1920[bg];"
            "[0:v]scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black,setsar=1[v_ref]"
        )

    # 6.3 Nhánh Tiền Cảnh Cắt Xén (Foreground Crop)
    if use_crop:
        filters.append(f"[v_ref]crop={crop_w}:{crop_h}:{crop_x}:{crop_y}[v_cropped]")
    else:
        filters.append("[v_ref]null[v_cropped]")

    # 6.4 Kỹ thuật Làm Mờ Vùng Chọn Siêu Nhẹ (Blur Mask Sub-Stream)
    if use_blur and bm_w >= MIN_BLUR_SIZE and bm_h >= MIN_BLUR_SIZE:
        rel_bm_x = max(0, bm_x - crop_x) if use_crop else bm_x
        rel_bm_y = max(0, bm_y - crop_y) if use_crop else bm_y

        limit_w = min(bm_w, max(MIN_BLUR_SIZE, crop_w - rel_bm_x if use_crop else 1920 - rel_bm_x))
        limit_h = min(bm_h, max(MIN_BLUR_SIZE, crop_h - rel_bm_y if use_crop else 1080 - rel_bm_y))
        if limit_w % 2 != 0: limit_w -= 1
        if limit_h % 2 != 0: limit_h -= 1

        b_shape = str(crop_cfg.get("blur_shape", "Hình chữ nhật")).lower()
        is_ellipse = any(k in b_shape for k in ["tròn", "tron", "elip", "ellipse", "circle"])

        if is_ellipse:
            filters.append(
                f"[v_cropped]split=2[v_orig1][v_orig2];"
                f"[v_orig2]crop={limit_w}:{limit_h}:{rel_bm_x}:{rel_bm_y},boxblur=10:2,format=yuva420p,"
                f"geq=lum='p(X,Y)':a='if(lte(pow((X-W/2)/(W/2),2)+pow((Y-H/2)/(H/2),2),1),255,0)'[blur_chunk];"
                f"[v_orig1][blur_chunk]overlay={rel_bm_x}:{rel_bm_y}[v_masked];"
                f"[v_masked]scale={fg_w}:{fg_h},setsar=1[fg]"
            )
        else:
            filters.append(
                f"[v_cropped]split=2[v_orig1][v_orig2];"
                f"[v_orig2]crop={limit_w}:{limit_h}:{rel_bm_x}:{rel_bm_y},boxblur=10:2[blur_chunk];"
                f"[v_orig1][blur_chunk]overlay={rel_bm_x}:{rel_bm_y}[v_masked];"
                f"[v_masked]scale={fg_w}:{fg_h},setsar=1[fg]"
            )
    else:
        filters.append(f"[v_cropped]scale={fg_w}:{fg_h},setsar=1[fg]")

    # 6.5 Ghép Tiền Cảnh Lên Nền Mờ Canvas 9:16
    filters.append("[bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v_canvas]")

    filter_complex_str = ";".join(filters)
    return filter_complex_str, "[v_canvas]"


def open_crop_blur_studio_popup(
    parent=None,
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """Hàm tiện ích mở nhanh Studio Cắt xén, Tỉ lệ Co Giãn & Nền mờ CapCut Pro"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    cfg = dict(current_config or {})
    vpath = video_source or cfg.get("video_source") or cfg.get("source_url_or_path") or cfg.get("youtube_url") or ""
    dlg = CapCutCropStudioDialog(current_data=cfg, sample_video_path=vpath, parent=None)
    if dlg.exec() == QDialog.Accepted:
        res = dlg.get_values()
        if on_save_callback:
            on_save_callback(res)
        return res
    return cfg
