# -*- coding: utf-8 -*-
"""
TITLE & SUBTITLE STUDIO (LIVE PREVIEW 9:16 TRỰC QUAN CHUẨN XÁC 100%)
Đồng bộ hoàn hảo giữa Canvas Preview và Render Video FFmpeg thực tế.
"""

from __future__ import annotations

import os
import re
import urllib.request
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

from PySide6.QtCore import Qt, QTimer, QRectF, QPointF, Signal, QThread
from PySide6.QtGui import (
    QColor, QFont, QFontMetrics, QPainter, QPen, QBrush,
    QPainterPath, QLinearGradient, QPixmap, QImage
)
from PySide6.QtWidgets import (
    QCheckBox, QColorDialog, QComboBox, QDialog, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QSizePolicy,
    QSpinBox, QVBoxLayout, QWidget, QRadioButton, QButtonGroup, QFileDialog,
    QGridLayout, QDoubleSpinBox
)

try:
    from font_manager import (
        FontManager,
        BANNER_CORNER_RADIUS,
        BANNER_PAD_X,
        BANNER_PAD_Y,
        detect_script,
        hex_to_ass_color as fm_hex_to_ass,
        create_dynamic_title_banner
    )
    _HAS_FONT_MANAGER = True
except Exception:
    FontManager = None
    BANNER_CORNER_RADIUS = 20
    BANNER_PAD_X = 28
    BANNER_PAD_Y = 18
    create_dynamic_title_banner = None
    _HAS_FONT_MANAGER = False


APP_DIR = Path(__file__).resolve().parent


def _detect_mode(cfg: dict) -> str:
    """Xác định chế độ gọi Studio: 'tiktok_remixer', 'compilation', hoặc 'summary'."""
    if not isinstance(cfg, dict):
        return "summary"
    if (
        cfg.get("is_tiktok_remixer")
        or cfg.get("app_mode") == "tiktok_remixer"
        or "auto_clean_core_crop" in cfg
        or "shuffle_broll" in cfg
    ):
        return "tiktok_remixer"
    if (
        cfg.get("is_part_splitter")
        or cfg.get("app_mode") == "compilation"
        or "compilation_clip_count" in cfg
        or "playlist_url" in cfg
    ):
        return "compilation"
    return "summary"


def _extract_frame_geom(cfg: dict) -> tuple[float, float, float, bool]:
    """Trích xuất (zoom, scale_x, scale_y, blur_bg) an toàn từ config, chống xung đột boolean."""
    is_tiktok = bool(
        cfg.get("is_tiktok_remixer")
        or cfg.get("app_mode") == "tiktok_remixer"
        or "auto_clean_core_crop" in cfg
        or "shuffle_broll" in cfg
    )
    def_zoom = 105.0 if is_tiktok else 178.0
    def_sx = 100.0
    def_sy = 100.0 if is_tiktok else 130.0
    def_blur = False if is_tiktok else True

    raw_z = cfg.get("zoom_percent")
    if raw_z is None or isinstance(raw_z, bool):
        raw_z = cfg.get("zoom_in")
    if isinstance(raw_z, bool) or raw_z is None:
        raw_z = def_zoom
    try:
        val_z = float(raw_z)
        if 0 < val_z <= 5.0:
            val_z *= 100.0
        elif val_z <= 0:
            val_z = def_zoom
    except Exception:
        val_z = def_zoom

    raw_sx = cfg.get("scale_w") if cfg.get("scale_w") is not None else cfg.get("scale_x", def_sx)
    try:
        val_sx = float(raw_sx)
        if 0 < val_sx <= 5.0:
            val_sx *= 100.0
    except Exception:
        val_sx = def_sx

    raw_sy = cfg.get("scale_h") if cfg.get("scale_h") is not None else cfg.get("scale_y", def_sy)
    try:
        val_sy = float(raw_sy)
        if 0 < val_sy <= 5.0:
            val_sy *= 100.0
    except Exception:
        val_sy = def_sy

    blur_bg = bool(cfg.get("blur_bg", def_blur))
    return val_z, val_sx, val_sy, blur_bg


# =====================================================================
# 1. HỆ THỐNG ĐỔI MÀU KÉP: WEB HEX (#RRGGBB) VS ASS HEX (&HBBGGRR&)
# =====================================================================
def hex_to_ass(hex_color: str, default: str = "&HFFFFFF&") -> str:
    """Chuyển RGB Web Hex (#RRGGBB) sang ASS Hex (&HBBGGRR&)."""
    if not hex_color:
        return default
    clean = str(hex_color).strip().lstrip("#")
    if clean.upper().startswith("&H"):
        clean_ass = clean.upper()
        if not clean_ass.endswith("&"):
            clean_ass += "&"
        return clean_ass
    if clean.lower().startswith("0x"):
        clean = clean[2:]
    if len(clean) == 6:
        r, g, b = clean[0:2], clean[2:4], clean[4:6]
        return f"&H{b}{g}{r}&".upper()
    return default


def ass_to_hex(ass_color: str, default: str = "#FFFFFF") -> str:
    """Chuyển ASS Hex (&HBBGGRR&) sang RGB Web Hex (#RRGGBB)."""
    if not ass_color:
        return default
    s = str(ass_color).strip().upper()
    if s.startswith("&H"):
        clean = s.replace("&H", "").replace("&", "")
        if len(clean) == 6:
            b, g, r = clean[0:2], clean[2:4], clean[4:6]
            return f"#{r}{g}{b}".upper()
    if s.startswith("#") and len(s) == 7:
        return s
    return default


# =====================================================================
# 2. BẢNG THÔNG SỐ 3 PHONG CÁCH PHỤ ĐỀ (SUBTITLE STYLE MATRIX)
# =====================================================================
SUBTITLE_PRESETS: Dict[str, Dict[str, Any]] = {
    "tiktok_slim": {
        "key": "tiktok_slim",
        "name": "✨ Thanh Mảnh (TikTok Slim - Arial/Segoe)",
        "sub_size": 7,
        "sub_outline": 1,
        "sub_shadow": 0,
        "sub_margin_v": 80,
        "uppercase": False,
        "description": "Nét thanh mảnh (Giống No. 1), chữ thường tự nhiên, viền mỏng tinh tế chuẩn TikTok."
    },
    "classic": {
        "key": "classic",
        "name": "🏛️ Cổ Điển (Classic - Segoe UI Black/Impact)",
        "sub_size": 14,
        "sub_outline": 3,
        "sub_shadow": 0,
        "sub_margin_v": 80,
        "uppercase": True,
        "description": "In hoa to bản, viền đen dày dặn, nổi bật và thu hút trên mọi nền video."
    },
    "standard": {
        "key": "standard",
        "name": "📺 Tiêu Chuẩn (Standard - Segoe UI)",
        "sub_size": 10,
        "sub_outline": 2,
        "sub_shadow": 0,
        "sub_margin_v": 90,
        "uppercase": False,
        "description": "Kích thước hài hòa, cân đối, dễ đọc trên cả điện thoại và máy tính bảng."
    }
}

DEFAULT_SUB_COLOR = "&HFFFFFF&"
DEFAULT_SUB_OUTLINE_COLOR = "&H000000&"


# =====================================================================
# 3. HELPER NÚT CHỌN MÀU
# =====================================================================
def _create_styled_color_btn(hex_val: str) -> QPushButton:
    btn = QPushButton()
    btn.setFixedHeight(30)
    btn.setFixedWidth(114)
    btn.setCursor(Qt.PointingHandCursor)
    _apply_color_btn_visuals(btn, hex_val)
    return btn


def _apply_color_btn_visuals(btn: QPushButton, hex_val: str):
    clean_hex = str(hex_val).strip()
    if not clean_hex.startswith("#") and not clean_hex.startswith("&H"):
        clean_hex = "#" + clean_hex
    display_hex = clean_hex
    if clean_hex.startswith("&H"):
        display_hex = ass_to_hex(clean_hex)

    try:
        col = QColor(display_hex)
        if not col.isValid():
            col = QColor("#888888")
    except Exception:
        col = QColor("#888888")

    text_color = "#000000" if col.lightness() > 135 else "#FFFFFF"
    btn.setText(display_hex.upper()[:7])
    btn.setStyleSheet(f"""
        QPushButton {{
            background-color: {display_hex};
            color: {text_color};
            font-weight: bold;
            font-size: 11px;
            border-radius: 6px;
            border: 1px solid #555568;
            padding: 2px 6px;
        }}
        QPushButton:hover {{
            border: 2px solid #89b4fa;
        }}
    """)


# =====================================================================
# 4. WORKER TẢI THUMBNAIL / HÌNH ẢNH TỪ YOUTUBE (THREAD-SAFE)
# =====================================================================
class YouTubeThumbnailFetcher(QThread):
    finished_sig = Signal(object, str, str)  # (QPixmap or None, status_text, color_hex)

    def __init__(self, url_or_id: str, parent=None):
        super().__init__(parent)
        self.raw_input = url_or_id.strip()

    def _extract_video_id(self, text: str) -> str:
        if not text:
            return ""
        m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$|\/)|\A([0-9A-Za-z_-]{11})\Z", text)
        if m:
            return m.group(1) or m.group(2) or ""
        return ""

    def run(self):
        vid = self._extract_video_id(self.raw_input)
        if not vid:
            self.finished_sig.emit(None, "❌ Link YouTube hoặc Video ID không hợp lệ.", "#f38ba8")
            return

        urls = [
            f"https://img.youtube.com/vi/{vid}/maxresdefault.jpg",
            f"https://img.youtube.com/vi/{vid}/sddefault.jpg",
            f"https://img.youtube.com/vi/{vid}/hqdefault.jpg",
        ]
        loaded_pix = None
        for u in urls:
            try:
                req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
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
            self.finished_sig.emit(loaded_pix, f"✅ Đã tải ảnh YouTube ({loaded_pix.width()}×{loaded_pix.height()})!", "#a6e3a1")
        else:
            self.finished_sig.emit(None, "❌ Không thể tải ảnh từ link YouTube này.", "#f38ba8")


# =====================================================================
# 5. KHUNG XEM TRƯỚC 9:16 TRỰC QUAN (RIGHT LIVE PREVIEW 9:16 CANVAS)
# =====================================================================
class PreviewCanvas9x16(QWidget):
    """
    Canvas Live Preview 9:16 mô phỏng chính xác video 1080x1920:
    - Hiển thị hình ảnh nền video thực tế.
    - Title Banner: LUÔN HIỂN THỊ khi có text mẫu hoặc khi bật.
    - Subtitle: Quy đổi tọa độ và cỡ chữ chuẩn xác 1:1 theo video render thật.
    - Số Part: Chế độ 1 (Sau Title) và Chế độ 2 (Ở dưới chỉnh tọa độ Y).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setStyleSheet("background-color: #11111b; border-radius: 8px;")

        self.cfg: Dict[str, Any] = {}
        self.title_sample_text: str = "TIÊU ĐỀ VIDEO MẪU\nDÒNG PHỤ BANNER"
        self.sub_sample_text: str = "Đây là phụ đề mẫu đang hiển thị thử nghiệm..."
        self.part_sample_number: str = "1"
        self.bg_pixmap: Optional[QPixmap] = None

    def set_background_pixmap(self, pix: Optional[QPixmap]):
        self.bg_pixmap = pix
        self.update()

    def update_data(self, cfg: Dict[str, Any], title_sample: str, sub_sample: str, part_num: str = "1"):
        merged = dict(self.cfg)
        merged.update(cfg or {})
        self.cfg = merged
        self.title_sample_text = title_sample if title_sample is not None else ""
        self.sub_sample_text = sub_sample if sub_sample is not None else ""
        self.part_sample_number = str(part_num or "1").strip()
        self.update()

    def _calc_viewport(self) -> Tuple[float, float, float, float, float]:
        cw = max(50.0, float(self.width()))
        ch = max(90.0, float(self.height()))
        scale_x = (cw - 24.0) / 1080.0
        scale_y = (ch - 24.0) / 1920.0
        scale = min(scale_x, scale_y)
        disp_w = 1080.0 * scale
        disp_h = 1920.0 * scale
        ox = (cw - disp_w) / 2.0
        oy = (ch - disp_h) / 2.0
        return scale, ox, oy, disp_w, disp_h

    def paintEvent(self, event):
        scale, ox, oy, disp_w, disp_h = self._calc_viewport()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)

        # 1. Nền canvas ngoài
        p.fillRect(self.rect(), QColor("#11111b"))
        video_rect = QRectF(ox, oy, disp_w, disp_h)

        # 2. Vẽ hình ảnh nền video mẫu (hoặc gradient tối nếu chưa có ảnh)
        if self.bg_pixmap and not self.bg_pixmap.isNull():
            clip_path = QPainterPath()
            clip_path.addRoundedRect(video_rect, 10, 10)
            p.save()
            p.setClipPath(clip_path)

            pw = float(self.bg_pixmap.width())
            ph = float(self.bg_pixmap.height())
            aspect = pw / max(1.0, ph)

            zoom_val, sx_val, sy_val, blur_bg_val = _extract_frame_geom(self.cfg)
            zoom_factor = zoom_val / 100.0
            sx = sx_val / 100.0
            sy = sy_val / 100.0

            if aspect > 0.65:
                # Video ngang 16:9 -> Nền mờ 9:16 hoặc Nền đen tuyền #000000 theo config blur_bg
                blur_bg = bool(self.cfg.get("blur_bg", blur_bg_val))
                if blur_bg:
                    small_bg = self.bg_pixmap.scaled(54, 96, Qt.IgnoreAspectRatio, Qt.FastTransformation)
                    blurred_bg = small_bg.scaled(int(disp_w), int(disp_h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    p.drawPixmap(QRectF(ox, oy, disp_w, disp_h), blurred_bg, QRectF(0, 0, disp_w, disp_h))
                    p.fillRect(video_rect, QColor(0, 0, 0, 45))
                else:
                    p.fillRect(video_rect, QColor("#000000"))

                # Video tiền cảnh sắc nét căn chính giữa khung 9:16 (Áp dụng Zoom-in & Scale X/Y từ config)
                fg_w = disp_w * zoom_factor * sx
                fg_h = (disp_w * (ph / pw)) * zoom_factor * sy
                fg_x = ox + (disp_w - fg_w) / 2.0
                fg_y = oy + (disp_h - fg_h) / 2.0

                p.save()
                p.setClipRect(video_rect)
                p.drawPixmap(QRectF(fg_x, fg_y, fg_w, fg_h), self.bg_pixmap, QRectF(0, 0, pw, ph))
                p.setPen(QPen(QColor("#89b4fa"), 1, Qt.DashLine))
                p.drawRect(QRectF(fg_x, fg_y, fg_w, fg_h))
                p.restore()
            else:
                # Video dọc 9:16 (TikTok) -> Áp dụng Zoom in/out & Scale (nhẹ) căn chính giữa
                blur_bg = bool(self.cfg.get("blur_bg", blur_bg_val))
                if blur_bg or zoom_factor < 1.0 or sx < 1.0 or sy < 1.0:
                    small_bg = self.bg_pixmap.scaled(54, 96, Qt.IgnoreAspectRatio, Qt.FastTransformation)
                    blurred_bg = small_bg.scaled(int(disp_w), int(disp_h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                    p.drawPixmap(QRectF(ox, oy, disp_w, disp_h), blurred_bg, QRectF(0, 0, disp_w, disp_h))
                    p.fillRect(video_rect, QColor(0, 0, 0, 45))
                else:
                    p.fillRect(video_rect, QColor("#000000"))

                fg_w = disp_w * zoom_factor * sx
                fg_h = disp_h * zoom_factor * sy
                fg_x = ox + (disp_w - fg_w) / 2.0
                fg_y = oy + (disp_h - fg_h) / 2.0

                p.save()
                p.setClipRect(video_rect)
                p.drawPixmap(QRectF(fg_x, fg_y, fg_w, fg_h), self.bg_pixmap, QRectF(0, 0, pw, ph))
                if zoom_factor != 1.0 or sx != 1.0 or sy != 1.0:
                    p.setPen(QPen(QColor("#89b4fa"), 1, Qt.DashLine))
                    p.drawRect(QRectF(fg_x, fg_y, fg_w, fg_h))
                p.restore()

            p.restore()

            p.setPen(QPen(QColor("#45475a"), 2))
            p.setBrush(Qt.NoBrush)
            p.drawRoundedRect(video_rect, 10, 10)
        else:
            grad = QLinearGradient(ox, oy, ox, oy + disp_h)
            grad.setColorAt(0.0, QColor("#1e1e28"))
            grad.setColorAt(1.0, QColor("#16161e"))
            p.setBrush(QBrush(grad))
            p.setPen(QPen(QColor("#45475a"), 2))
            p.drawRoundedRect(video_rect, 10, 10)

        # Vạch an toàn nét đứt (Safe Area)
        p.setPen(QPen(QColor(200, 200, 220, 40), 1, Qt.DashLine))
        safe_top_y = oy + 150.0 * scale
        p.drawLine(QPointF(ox, safe_top_y), QPointF(ox + disp_w, safe_top_y))
        safe_bot_y = oy + (1920.0 - 250.0) * scale
        p.drawLine(QPointF(ox, safe_bot_y), QPointF(ox + disp_w, safe_bot_y))

        # 3. Vẽ Title Banner (LUÔN VẼ NẾU CÓ TEXT HOẶC ĐƯỢC BẬT ĐỂ NGƯỜI DÙNG XEM TRƯỚC TRỰC QUAN)
        has_title_text = bool((self.title_sample_text or "").strip()) or bool(self.cfg.get("title_text", "").strip())
        is_title_enabled = self.cfg.get("show_title", False) or self.cfg.get("enable_title", False)
        if is_title_enabled or has_title_text:
            self._render_title_banner(p, scale, ox, oy, disp_w)

        # 4. Vẽ Số Part ở bên dưới (nếu chọn chế độ bottom)
        if self.cfg.get("show_part", True) and self.cfg.get("part_position") == "bottom":
            self._render_bottom_part(p, scale, ox, oy, disp_w)

        # 5. Vẽ Subtitle nếu được bật hoặc có text mẫu
        has_sub_text = bool((self.sub_sample_text or "").strip())
        is_sub_enabled = self.cfg.get("auto_sub", True) or self.cfg.get("enable_sub", True)
        if is_sub_enabled or has_sub_text:
            self._render_subtitle(p, scale, ox, oy, disp_w, disp_h)

        # Huy hiệu badge 9:16 PRO
        badge_rect = QRectF(ox + disp_w - 74, oy + 8, 66, 20)
        p.setBrush(QBrush(QColor(0, 0, 0, 170)))
        p.setPen(QPen(QColor("#89b4fa"), 1))
        p.drawRoundedRect(badge_rect, 4, 4)
        p.setFont(QFont("Segoe UI", 8, QFont.Bold))
        p.setPen(QColor("#89b4fa"))
        p.drawText(badge_rect, Qt.AlignCenter, "9:16 PRO")

        p.end()

    def _render_title_banner(self, p: QPainter, scale: float, ox: float, oy: float, disp_w: float):
        """Vẽ Title Banner bo góc nổi bật chuẩn tỉ lệ 1080x1920 khớp 100% render."""
        cfg = self.cfg
        title_y_pos = float(cfg.get("title_y_pos", 320))
        bg_hex = cfg.get("title_bg_color", "#FF2D55")
        c1_hex = cfg.get("title_color1", "#FFFFFF")
        c2_hex = cfg.get("title_color2", "#FFFFFF")
        outline_hex = cfg.get("title_outline_color", "none")

        raw = (self.title_sample_text or "").strip()
        if not raw:
            raw = cfg.get("title_text", "").strip() or "TIÊU ĐỀ VIDEO MẪU"

        # Nếu bật hiện Part ở sau Title trên cùng -> Format chuẩn Part X: Tiêu đề
        show_part = cfg.get("show_part", True)
        part_pos = cfg.get("part_position", "after_title")
        part_fmt = cfg.get("part_format", "Part")
        part_str = f"{part_fmt} {self.part_sample_number}".strip()

        if show_part and part_pos == "after_title" and part_str:
            if not raw.lower().endswith(part_str.lower()):
                raw = f"{raw} {part_str}"

        banner_opts = {
            "title_color1": c1_hex,
            "title_color2": c2_hex,
            "title_bg_color": bg_hex,
            "title_outline_color": outline_hex
        }

        # Ưu tiên tạo banner chuẩn 100% bằng FontManager PIL giống hệt FFmpeg render
        if _HAS_FONT_MANAGER and create_dynamic_title_banner:
            try:
                import tempfile
                out_png, bw, bh = create_dynamic_title_banner(
                    tempfile.gettempdir(), raw, "", banner_opts
                )
                pix = QPixmap(out_png)
                if pix and not pix.isNull():
                    bw_c = bw * scale
                    bh_c = bh * scale
                    bx = ox + (disp_w - bw_c) / 2.0
                    by = oy + title_y_pos * scale
                    p.drawPixmap(QRectF(bx, by, bw_c, bh_c), pix, QRectF(pix.rect()))
                    return
            except Exception:
                pass

        # Fallback vẽ trực tiếp bằng QPainter nếu không có PIL
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        if len(lines) == 1 and "\\n" in raw:
            lines = [line.strip() for line in raw.split("\\n") if line.strip()]

        if len(lines) >= 2:
            l1, l2 = lines[0], lines[1]
        elif ":" in raw:
            parts = raw.split(":", 1)
            l1, l2 = parts[0].strip(), parts[1].strip()
        elif " - " in raw:
            parts = raw.split(" - ", 1)
            l1, l2 = parts[0].strip(), parts[1].strip()
        elif " " in raw and len(raw) > 20:
            words = raw.split()
            mid = max(1, len(words) // 2)
            l1 = " ".join(words[:mid])
            l2 = " ".join(words[mid:])
        else:
            l1, l2 = raw, ""

        bw_real = 920.0
        bh_real = 156.0 if l2 else 92.0
        bw_c = bw_real * scale
        bh_c = bh_real * scale
        bx1 = ox + (disp_w - bw_c) / 2.0
        by1 = oy + title_y_pos * scale
        banner_box = QRectF(bx1, by1, bw_c, bh_c)

        radius = max(6.0, 24.0 * scale)
        p.setBrush(QBrush(QColor(bg_hex)))
        if outline_hex and str(outline_hex).lower() != "none":
            stroke_w = max(1.0, 2.5 * scale)
            p.setPen(QPen(QColor(outline_hex), stroke_w))
        else:
            p.setPen(Qt.NoPen)
        p.drawRoundedRect(banner_box, radius, radius)

        font_size = max(10, int(30.0 * scale * 1.5))
        font = QFont("Segoe UI", font_size, QFont.Bold)
        p.setFont(font)

        if l2:
            rect_l1 = QRectF(bx1 + 10 * scale, by1 + 6 * scale, bw_c - 20 * scale, bh_c * 0.46)
            p.setPen(QColor(c1_hex))
            p.drawText(rect_l1, Qt.AlignCenter, l1)

            rect_l2 = QRectF(bx1 + 10 * scale, by1 + bh_c * 0.48, bw_c - 20 * scale, bh_c * 0.46)
            p.setPen(QColor(c2_hex))
            p.drawText(rect_l2, Qt.AlignCenter, l2)
        else:
            p.setPen(QColor(c1_hex))
            p.drawText(banner_box, Qt.AlignCenter, l1)

    def _render_bottom_part(self, p: QPainter, scale: float, ox: float, oy: float, disp_w: float):
        """Vẽ số Part ở bên dưới, chung màu với Title khớp 100% render."""
        cfg = self.cfg
        part_y_pos = float(cfg.get("part_y_pos", 1600))
        part_fmt = cfg.get("part_format", "Part")
        part_text = f"{part_fmt} {self.part_sample_number}".strip()

        if not part_text:
            return

        bg_hex = cfg.get("title_bg_color", "#FF2D55")
        text_hex = cfg.get("title_color1", "#FFFFFF")
        outline_hex = cfg.get("title_outline_color", "none")

        banner_opts = {
            "title_color1": text_hex,
            "title_color2": text_hex,
            "title_bg_color": bg_hex,
            "title_outline_color": outline_hex
        }

        # Ưu tiên tạo banner chuẩn 100% bằng FontManager PIL giống hệt FFmpeg render
        if _HAS_FONT_MANAGER and create_dynamic_title_banner:
            try:
                import tempfile
                part_png, pbw, pbh = create_dynamic_title_banner(
                    tempfile.gettempdir(), part_text, "", banner_opts
                )
                pix = QPixmap(part_png)
                if pix and not pix.isNull():
                    pbw_c = pbw * scale
                    pbh_c = pbh * scale
                    pbx = ox + (disp_w - pbw_c) / 2.0
                    pby = oy + part_y_pos * scale
                    p.drawPixmap(QRectF(pbx, pby, pbw_c, pbh_c), pix, QRectF(pix.rect()))
                    return
            except Exception:
                pass

        # Fallback vẽ trực tiếp
        part_size = int(cfg.get("part_size", 22))
        s_font_size = max(9, int(part_size * scale * 1.8))
        font = QFont("Segoe UI", s_font_size, QFont.Bold)
        p.setFont(font)
        fm = QFontMetrics(font)

        pad_x = 22.0 * scale
        pad_y = 10.0 * scale
        text_w = float(fm.horizontalAdvance(part_text))
        text_h = float(fm.height())

        badge_w = text_w + pad_x * 2.0
        badge_h = text_h + pad_y * 1.2
        bx = ox + (disp_w - badge_w) / 2.0
        by = oy + part_y_pos * scale

        badge_rect = QRectF(bx, by, badge_w, badge_h)
        radius = max(5.0, badge_h / 2.0)

        p.setBrush(QBrush(QColor(bg_hex)))
        if outline_hex and str(outline_hex).lower() != "none":
            p.setPen(QPen(QColor(outline_hex), max(1.0, 1.5 * scale)))
        else:
            p.setPen(Qt.NoPen)
        p.drawRoundedRect(badge_rect, radius, radius)

        p.setPen(QColor(text_hex))
        p.drawText(badge_rect, Qt.AlignCenter, part_text)

    def _render_subtitle(self, p: QPainter, scale: float, ox: float, oy: float, disp_w: float, disp_h: float):
        """
        Vẽ Subtitle mô phỏng chuẩn xác 1:1 theo render thực tế của FFmpeg libass:
        - Sử dụng font.setPixelSize() triệt tiêu 100% độ lệch DPI màn hình.
        - Tự động ngắt dòng thông minh (Word Wrap) theo độ rộng chuẩn video (MarginL=70, MarginR=70).
        - Quy đổi MarginV theo hệ số PlayResY=288 chuẩn xác tuyệt đối so với đáy video.
        """
        cfg = self.cfg
        sub_size = int(cfg.get("sub_size", 7))
        sub_outline = int(cfg.get("sub_outline", 1))
        sub_shadow = int(cfg.get("sub_shadow", 0))
        sub_margin_v = int(cfg.get("sub_margin_v", 80))

        raw_sub_c = cfg.get("sub_color", DEFAULT_SUB_COLOR)
        raw_sub_ol = cfg.get("sub_outline_color", DEFAULT_SUB_OUTLINE_COLOR)
        sub_hex = ass_to_hex(raw_sub_c) if str(raw_sub_c).startswith("&H") else (raw_sub_c or "#FFFFFF")
        ol_hex = ass_to_hex(raw_sub_ol) if str(raw_sub_ol).startswith("&H") else (raw_sub_ol or "#000000")

        text = (self.sub_sample_text or "").strip()
        if cfg.get("sub_uppercase", False):
            text = text.upper()

        if not text:
            return

        # Cỡ chữ chuẩn theo FFmpeg libass (PlayResY=288) trên khung 1080x1920:
        # 1920 / 288 = 6.6667 px per libass unit. Dùng pixelSize để tránh phóng to DPI.
        canvas_font_size = max(6, int(round(sub_size * (1920.0 / 288.0) * scale)))
        style_key = cfg.get("sub_style_type", "tiktok_slim")
        font_family = "Segoe UI Black" if style_key == "classic" else ("Arial" if style_key == "tiktok_slim" else "Segoe UI")

        font = QFont(font_family)
        font.setPixelSize(canvas_font_size)
        if style_key == "classic":
            font.setBold(True)
        else:
            font.setBold(False)
        p.setFont(font)
        fm = QFontMetrics(font)

        # Word wrap theo lề trái phải MarginL=70, MarginR=70 (tối đa 940px trên video 1080px)
        max_w_canvas = 940.0 * scale
        words = text.split()
        lines = []
        curr_line = ""
        for w in words:
            cand = f"{curr_line} {w}".strip() if curr_line else w
            if fm.horizontalAdvance(cand) <= max_w_canvas:
                curr_line = cand
            else:
                if curr_line:
                    lines.append(curr_line)
                curr_line = w
        if curr_line:
            lines.append(curr_line)
        if not lines:
            lines = [text]

        line_h = fm.height()
        total_text_h = len(lines) * line_h

        # Tọa độ Y: Khoảng cách từ đáy video theo MarginV chuẩn libass (PlayResY=288)
        # dist_from_bottom = sub_margin_v * (1920 / 288) * scale
        dist_from_bottom = float(sub_margin_v) * (1920.0 / 288.0) * scale
        base_bottom_y = oy + disp_h - dist_from_bottom
        top_y = base_bottom_y - total_text_h

        for i, line_str in enumerate(lines):
            lw = fm.horizontalAdvance(line_str)
            lx = ox + (disp_w - lw) / 2.0
            ly = top_y + i * line_h + fm.ascent()

            # 1. Vẽ bóng đổ 3D
            if sub_shadow > 0:
                shadow_off = max(1, int(round(sub_shadow * (1920.0 / 288.0) * 0.35 * scale)))
                p.setPen(QColor(0, 0, 0, 220))
                p.drawText(QPointF(lx + shadow_off, ly + shadow_off), line_str)

            # 2. Vẽ viền chữ 8 hướng (Outline)
            if sub_outline > 0:
                ol_col = QColor(ol_hex)
                p.setPen(ol_col)
                step = max(1, int(round(sub_outline * (1920.0 / 288.0) * 0.3 * scale)))
                for dx in range(-step, step + 1, step):
                    for dy in range(-step, step + 1, step):
                        if dx == 0 and dy == 0:
                            continue
                        p.drawText(QPointF(lx + dx, ly + dy), line_str)

            # 3. Vẽ chữ chính
            p.setPen(QColor(sub_hex))
            p.drawText(QPointF(lx, ly), line_str)


# =====================================================================
# 6. CỬA SỔ POPUP STUDIO CHÍNH (TITLE & SUBTITLE STUDIO DIALOG - 1260x860)
# =====================================================================
class TitleSubStudioDialog(QDialog):
    """Popup Studio Title & Subtitle 2 Bảng Đối Xứng (1260x860)."""

    def __init__(self, current_data: Optional[Dict[str, Any]] = None, sample_video_path: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("🏷️ Title & Subtitle Studio (Live 9:16 Interactive Preview)")
        self.setMinimumSize(1080, 720)
        self.resize(1260, 860)
        self.setModal(True)

        self.ts_data: Dict[str, Any] = dict(current_data or {})
        self.sample_video_path = sample_video_path or ""
        self.app_mode = _detect_mode(self.ts_data)
        self.is_tiktok_mode = (self.app_mode == "tiktok_remixer")
        self.system_cache_path = APP_DIR / "system_data" / f"crop_preview_cache_{self.app_mode}.jpg"
        self.layout_base_path = APP_DIR / "temp" / "filter_preview" / f"layout_base_{self.app_mode}.jpg"

        # Dữ liệu màu sắc nội bộ
        self._title_color1 = self.ts_data.get("title_color1", "#FFFFFF")
        self._title_color2 = self.ts_data.get("title_color2", "#FFFFFF")
        self._title_outline = self.ts_data.get("title_outline_color", "none")
        self._title_bg = self.ts_data.get("title_bg_color", "#FF2D55")

        raw_sc = self.ts_data.get("sub_color", DEFAULT_SUB_COLOR)
        raw_so = self.ts_data.get("sub_outline_color", DEFAULT_SUB_OUTLINE_COLOR)
        self._sub_color_ass = raw_sc if str(raw_sc).startswith("&H") else hex_to_ass(raw_sc)
        self._sub_ol_ass = raw_so if str(raw_so).startswith("&H") else hex_to_ass(raw_so)

        self._yt_fetcher: Optional[YouTubeThumbnailFetcher] = None

        # Debounce timer chống lag giật canvas
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(50)
        self._debounce_timer.timeout.connect(self._sync_canvas_preview)

        self._setup_theme()
        self._init_layout()
        self._sync_canvas_preview()

        # Tự động nạp ảnh nền ban đầu từ cache hoặc video mẫu
        QTimer.singleShot(40, self._load_initial_background)
        QTimer.singleShot(80, self._sync_canvas_preview)

    def _setup_theme(self):
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
            QLineEdit, QSpinBox, QComboBox {
                background-color: #313244;
                color: #cdd6f4;
                border: 1px solid #45475a;
                border-radius: 6px;
                padding: 5px 8px;
                font-size: 12px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 16px;
                background-color: #45475a;
                border-radius: 3px;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QCheckBox, QRadioButton {
                color: #cdd6f4;
                font-size: 12px;
                spacing: 6px;
            }
            QScrollArea {
                border: 1px solid #313244;
                background: transparent;
            }
        """)

    def _init_layout(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(14)

        # =====================================================================
        # BẢNG TRÁI: BỘ ĐIỀU KHIỂN THÔNG SỐ & MÀU SẮC (LEFT PANEL - 530px)
        # =====================================================================
        scroll = QScrollArea()
        scroll.setFixedWidth(530)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(4, 4, 4, 4)
        left_layout.setSpacing(12)
        scroll.setWidget(left_widget)
        root.addWidget(scroll)

        self.is_tiktok_mode = bool(
            self.ts_data.get("is_tiktok_remixer")
            or self.ts_data.get("app_mode") == "tiktok_remixer"
            or "auto_clean_core_crop" in self.ts_data
            or "shuffle_broll" in self.ts_data
        )

        # --- NHÓM 1: CẤU HÌNH TIÊU ĐỀ BANNER 2 DÒNG ---
        gb_title = QGroupBox("🏷️ CẤU HÌNH TIÊU ĐỀ BANNER (PILL BANNER)")
        gb_title.setStyleSheet("QGroupBox { color: #a6e3a1; }")
        fl_title = QFormLayout(gb_title)
        fl_title.setSpacing(9)
        fl_title.setContentsMargins(12, 16, 12, 12)

        # Mặc định BẬT để người dùng thấy ngay Title Banner nổi bật khi mở
        self.chk_title = QCheckBox("Bật Hiển Thị Tiêu Đề Banner Trên Video")
        init_title_on = self.ts_data.get("show_title", self.ts_data.get("enable_title", True))
        self.chk_title.setChecked(bool(init_title_on))
        self.chk_title.stateChanged.connect(self._schedule_refresh)
        fl_title.addRow(self.chk_title)

        self.txt_title = QLineEdit(self.ts_data.get("title_text", ""))
        self.txt_title.setPlaceholderText("Để trống = Tự động lấy tên video YouTube...")
        self.txt_title.textChanged.connect(self._schedule_refresh)
        fl_title.addRow("Tùy Chỉnh Chữ:", self.txt_title)

        self.sp_title_y = QSpinBox()
        self.sp_title_y.setRange(0, 1900)
        self.sp_title_y.setValue(int(self.ts_data.get("title_y_pos", 320)))
        self.sp_title_y.setSuffix(" px")
        self.sp_title_y.setToolTip("Tọa độ Y tính từ mép trên cùng (0 = đỉnh, 1920 = đáy)")
        self.sp_title_y.valueChanged.connect(self._schedule_refresh)
        fl_title.addRow("Tọa Độ Y (px):", self.sp_title_y)

        # 4 Nút Màu Banner
        self.btn_c1 = _create_styled_color_btn(self._title_color1)
        self.btn_c1.clicked.connect(lambda: self._choose_title_color("c1"))
        fl_title.addRow("Màu Chữ Dòng 1:", self.btn_c1)

        self.btn_c2 = _create_styled_color_btn(self._title_color2)
        self.btn_c2.clicked.connect(lambda: self._choose_title_color("c2"))
        fl_title.addRow("Màu Chữ Dòng 2:", self.btn_c2)

        h_outline = QHBoxLayout()
        h_outline.setSpacing(8)
        _is_no_ol = not self._title_outline or str(self._title_outline).lower() == "none"
        self.chk_no_outline = QCheckBox("Không Viền")
        self.chk_no_outline.setChecked(_is_no_ol)
        self.chk_no_outline.stateChanged.connect(self._toggle_no_outline)
        self.btn_outline = _create_styled_color_btn(self._title_outline if not _is_no_ol else "#000000")
        self.btn_outline.setEnabled(not _is_no_ol)
        self.btn_outline.clicked.connect(lambda: self._choose_title_color("outline"))
        h_outline.addWidget(self.chk_no_outline)
        h_outline.addWidget(self.btn_outline)
        h_outline.addStretch()
        fl_title.addRow("Màu Viền Chữ:", h_outline)

        self.btn_bg = _create_styled_color_btn(self._title_bg)
        self.btn_bg.clicked.connect(lambda: self._choose_title_color("bg"))
        fl_title.addRow("Màu Nền Banner:", self.btn_bg)

        left_layout.addWidget(gb_title)

        # --- NHÓM 2: CẤU HÌNH SỐ PART (NẰM NGAY TRÊN SUBTITLE) ---
        gb_part = QGroupBox("🔢 CẤU HÌNH SỐ PART (CHUNG MÀU VỚI TIÊU ĐỀ)")
        gb_part.setStyleSheet("QGroupBox { color: #cba6f7; }")
        fl_part = QFormLayout(gb_part)
        fl_part.setSpacing(9)
        fl_part.setContentsMargins(12, 16, 12, 12)

        self.chk_part = QCheckBox("Bật Hiển Thị Số Part Trên Video")
        self.chk_part.setChecked(self.ts_data.get("show_part", True))
        self.chk_part.stateChanged.connect(self._schedule_refresh)
        fl_part.addRow(self.chk_part)

        h_fmt = QHBoxLayout()
        self.txt_part_format = QLineEdit(self.ts_data.get("part_format", "Part"))
        self.txt_part_format.setFixedWidth(80)
        self.txt_part_format.textChanged.connect(self._schedule_refresh)
        h_fmt.addWidget(QLabel("Định Dạng:"))
        h_fmt.addWidget(self.txt_part_format)

        self.txt_part_test_num = QLineEdit("1")
        self.txt_part_test_num.setFixedWidth(50)
        self.txt_part_test_num.setToolTip("Số part mẫu hiển thị thử nghiệm trên canvas")
        self.txt_part_test_num.textChanged.connect(self._schedule_refresh)
        h_fmt.addWidget(QLabel("Số Thử Nghiệm:"))
        h_fmt.addWidget(self.txt_part_test_num)
        h_fmt.addStretch()
        fl_part.addRow("Ký Hiệu Part:", h_fmt)

        # 2 Tùy chọn vị trí Part
        curr_part_pos = self.ts_data.get("part_position", "after_title")
        self.rb_part_after_title = QRadioButton("1. Hiện ở sau Title ở trên cùng (Ghép chung Banner)")
        self.rb_part_bottom = QRadioButton("2. Hiện ở bên dưới (Tùy chỉnh tọa độ Y như Sub)")

        self.btn_grp_part_pos = QButtonGroup(self)
        self.btn_grp_part_pos.addButton(self.rb_part_after_title, 1)
        self.btn_grp_part_pos.addButton(self.rb_part_bottom, 2)

        if curr_part_pos == "bottom":
            self.rb_part_bottom.setChecked(True)
        else:
            self.rb_part_after_title.setChecked(True)

        self.rb_part_after_title.toggled.connect(self._on_part_pos_changed)
        self.rb_part_bottom.toggled.connect(self._on_part_pos_changed)

        fl_part.addRow("Vị Trí Hiển Thị:", self.rb_part_after_title)
        fl_part.addRow("", self.rb_part_bottom)

        # Khối chỉnh tọa độ cho lựa chọn 2 (hiện ở dưới)
        self.w_bottom_part_coords = QWidget()
        h_b_coords = QHBoxLayout(self.w_bottom_part_coords)
        h_b_coords.setContentsMargins(0, 0, 0, 0)
        h_b_coords.setSpacing(8)

        self.sp_part_y = QSpinBox()
        self.sp_part_y.setRange(0, 1920)
        self.sp_part_y.setValue(int(self.ts_data.get("part_y_pos", 1600)))
        self.sp_part_y.setSuffix(" px")
        self.sp_part_y.setToolTip("Tọa độ Y của khối Part phía dưới")
        self.sp_part_y.valueChanged.connect(self._schedule_refresh)

        self.sp_part_size = QSpinBox()
        self.sp_part_size.setRange(10, 60)
        self.sp_part_size.setValue(int(self.ts_data.get("part_size", 22)))
        self.sp_part_size.setSuffix(" px")
        self.sp_part_size.valueChanged.connect(self._schedule_refresh)

        h_b_coords.addWidget(QLabel("Tọa Độ Y:"))
        h_b_coords.addWidget(self.sp_part_y)
        h_b_coords.addWidget(QLabel("Cỡ Chữ:"))
        h_b_coords.addWidget(self.sp_part_size)
        h_b_coords.addStretch()
        fl_part.addRow("Thông Số Khi Ở Dưới:", self.w_bottom_part_coords)

        self.w_bottom_part_coords.setVisible(self.rb_part_bottom.isChecked())

        lbl_part_color_hint = QLabel("🎨 Số Part tự động dùng chung Màu Chữ, Nền và Viền với Tiêu Đề Banner.")
        lbl_part_color_hint.setStyleSheet("color: #a6adc8; font-size: 11px; font-style: italic;")
        lbl_part_color_hint.setWordWrap(True)
        fl_part.addRow(lbl_part_color_hint)

        left_layout.addWidget(gb_part)

        # --- NHÓM 3: CẤU HÌNH PHỤ ĐỀ SUBTITLE ĐA PHONG CÁCH ---
        gb_sub = QGroupBox("💬 CẤU HÌNH PHỤ ĐỀ SUBTITLE (0% LỖI TOFU)")
        gb_sub.setStyleSheet("QGroupBox { color: #89b4fa; }")
        fl_sub = QFormLayout(gb_sub)
        fl_sub.setSpacing(9)
        fl_sub.setContentsMargins(12, 16, 12, 12)

        self.chk_sub = QCheckBox("Tự Động Tạo & Nhúng Phụ Đề (Whisper AI)")
        self.chk_sub.setChecked(self.ts_data.get("auto_sub", self.ts_data.get("enable_sub", True)))
        self.chk_sub.stateChanged.connect(self._schedule_refresh)
        fl_sub.addRow(self.chk_sub)

        # ComboBox 3 Style Presets
        self.combo_sub_style = QComboBox()
        for k, v in SUBTITLE_PRESETS.items():
            self.combo_sub_style.addItem(v["name"], k)
        curr_style = self.ts_data.get("sub_style_type", "tiktok_slim")
        idx_style = self.combo_sub_style.findData(curr_style)
        if idx_style >= 0:
            self.combo_sub_style.setCurrentIndex(idx_style)
        self.combo_sub_style.currentIndexChanged.connect(self._on_preset_selected)
        fl_sub.addRow("Phong Cách (Style):", self.combo_sub_style)

        # 4 Spinbox Thông số Phụ Đề
        self.sp_sub_size = QSpinBox()
        self.sp_sub_size.setRange(2, 60)
        self.sp_sub_size.setValue(int(self.ts_data.get("sub_size", 7)))
        self.sp_sub_size.setSuffix(" px")
        self.sp_sub_size.valueChanged.connect(self._schedule_refresh)
        fl_sub.addRow("Cỡ Chữ (FontSize):", self.sp_sub_size)

        self.sp_sub_outline = QSpinBox()
        self.sp_sub_outline.setRange(0, 10)
        self.sp_sub_outline.setValue(int(self.ts_data.get("sub_outline", 1)))
        self.sp_sub_outline.setSuffix(" px")
        self.sp_sub_outline.setToolTip("Độ dày viền nét chữ (Outline)")
        self.sp_sub_outline.valueChanged.connect(self._schedule_refresh)
        fl_sub.addRow("Độ Dày Viền (Outline):", self.sp_sub_outline)

        self.sp_sub_shadow = QSpinBox()
        self.sp_sub_shadow.setRange(0, 6)
        self.sp_sub_shadow.setValue(int(self.ts_data.get("sub_shadow", 0)))
        self.sp_sub_shadow.setSuffix(" px")
        self.sp_sub_shadow.setToolTip("Bóng mờ 3D đổ xuống (Shadow)")
        self.sp_sub_shadow.valueChanged.connect(self._schedule_refresh)
        fl_sub.addRow("Bóng 3D (Shadow):", self.sp_sub_shadow)

        self.sp_sub_margin_v = QSpinBox()
        self.sp_sub_margin_v.setRange(10, 500)
        self.sp_sub_margin_v.setValue(int(self.ts_data.get("sub_margin_v", 80)))
        self.sp_sub_margin_v.setSuffix(" px")
        self.sp_sub_margin_v.setToolTip("Khoảng cách từ mép đáy màn hình đến chữ phụ đề (MarginV)")
        self.sp_sub_margin_v.valueChanged.connect(self._schedule_refresh)
        fl_sub.addRow("Lề Đáy (MarginV):", self.sp_sub_margin_v)

        # 2 Ô Màu ASS
        self.btn_sub_color = _create_styled_color_btn(ass_to_hex(self._sub_color_ass))
        self.btn_sub_color.clicked.connect(lambda: self._choose_sub_color("color"))
        fl_sub.addRow("Màu Chữ Phụ Đề:", self.btn_sub_color)

        self.btn_sub_outline_color = _create_styled_color_btn(ass_to_hex(self._sub_ol_ass))
        self.btn_sub_outline_color.clicked.connect(lambda: self._choose_sub_color("outline"))
        fl_sub.addRow("Màu Viền Phụ Đề:", self.btn_sub_outline_color)

        lbl_ass_hint = QLabel("💡 Lưu ý: Màu Sub tự động chuyển sang BGR chuẩn ASS (&HBBGGRR&) cho FFmpeg.")
        lbl_ass_hint.setStyleSheet("color: #6c7086; font-size: 11px; font-style: italic;")
        lbl_ass_hint.setWordWrap(True)
        fl_sub.addRow(lbl_ass_hint)

        left_layout.addWidget(gb_sub)

        # --- NÚT THAO TÁC (LƯU / RESET / HỦY) ---
        h_btns = QHBoxLayout()
        h_btns.setSpacing(10)

        btn_save = QPushButton("💾 Lưu Cấu Hình")
        btn_save.setFixedHeight(36)
        btn_save.setCursor(Qt.PointingHandCursor)
        btn_save.setStyleSheet("""
            QPushButton {
                background-color: #a6e3a1; color: #11111b;
                font-weight: bold; font-size: 13px;
                border-radius: 8px; padding: 0 18px;
            }
            QPushButton:hover { background-color: #94d3a0; }
        """)
        btn_save.clicked.connect(self.accept)

        btn_reset = QPushButton("↩ Reset Mặc Định")
        btn_reset.setFixedHeight(36)
        btn_reset.setCursor(Qt.PointingHandCursor)
        btn_reset.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #f9e2af;
                border: 1px solid #45475a; border-radius: 8px;
                font-size: 13px; padding: 0 14px;
            }
            QPushButton:hover { background-color: #45475a; }
        """)
        btn_reset.clicked.connect(self._reset_to_defaults)

        btn_cancel = QPushButton("✖ Hủy Bỏ")
        btn_cancel.setFixedHeight(36)
        btn_cancel.setCursor(Qt.PointingHandCursor)
        btn_cancel.setStyleSheet("""
            QPushButton {
                background-color: #313244; color: #f38ba8;
                border: 1px solid #45475a; border-radius: 8px;
                font-size: 13px; padding: 0 14px;
            }
            QPushButton:hover { background-color: #45475a; }
        """)
        btn_cancel.clicked.connect(self.reject)

        h_btns.addWidget(btn_save)
        h_btns.addWidget(btn_reset)
        h_btns.addStretch()
        h_btns.addWidget(btn_cancel)
        left_layout.addLayout(h_btns)
        left_layout.addStretch()

        # =====================================================================
        # BẢNG PHẢI: KHUNG XEM TRƯỚC 9:16 TRỰC QUAN (RIGHT LIVE PREVIEW 9:16)
        # =====================================================================
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)
        root.addWidget(right_widget, 1)

        # 1. Thanh Công Cụ Nạp Hình Ảnh Mẫu (Video File, Cache, YouTube Link)
        gb_source = QGroupBox("🖼️ HÌNH ẢNH MẪU VIDEO (NỀN CANVAS 9:16)")
        gb_source.setStyleSheet("QGroupBox { color: #89b4fa; }")
        v_source = QVBoxLayout(gb_source)
        v_source.setContentsMargins(10, 14, 10, 8)
        v_source.setSpacing(6)

        h_src_row = QHBoxLayout()
        h_src_row.setSpacing(8)

        self.btn_pick_video = QPushButton("📁 Chọn Video")
        self.btn_pick_video.setFixedHeight(28)
        self.btn_pick_video.setCursor(Qt.PointingHandCursor)
        self.btn_pick_video.setStyleSheet("background-color: #313244; color: #cdd6f4; border: 1px solid #45475a; border-radius: 5px; padding: 0 10px;")
        self.btn_pick_video.clicked.connect(self._on_choose_video_file)
        h_src_row.addWidget(self.btn_pick_video)

        self.btn_load_cache = QPushButton("🔄 Nạp Lại Frame")
        self.btn_load_cache.setFixedHeight(28)
        self.btn_load_cache.setCursor(Qt.PointingHandCursor)
        self.btn_load_cache.setStyleSheet("background-color: #313244; color: #f9e2af; border: 1px solid #45475a; border-radius: 5px; padding: 0 10px;")
        self.btn_load_cache.clicked.connect(self._load_initial_background)
        h_src_row.addWidget(self.btn_load_cache)

        # Ô nhập link YouTube
        self.txt_yt_url = QLineEdit()
        self.txt_yt_url.setPlaceholderText("Dán Link YouTube hoặc Video ID vào đây để bốc ảnh mẫu...")
        h_src_row.addWidget(self.txt_yt_url, 1)

        self.btn_fetch_yt = QPushButton("🌐 Lấy Mẫu YouTube")
        self.btn_fetch_yt.setFixedHeight(28)
        self.btn_fetch_yt.setCursor(Qt.PointingHandCursor)
        self.btn_fetch_yt.setStyleSheet("background-color: #fab387; color: #11111b; font-weight: bold; border-radius: 5px; padding: 0 12px;")
        self.btn_fetch_yt.clicked.connect(self._fetch_youtube_sample)
        h_src_row.addWidget(self.btn_fetch_yt)

        v_source.addLayout(h_src_row)

        self.lbl_src_status = QLabel("Trạng thái ảnh: Đang nạp...")
        self.lbl_src_status.setStyleSheet("color: #a6adc8; font-size: 11px;")
        v_source.addWidget(self.lbl_src_status)

        right_layout.addWidget(gb_source)

        # 2. Canvas 9:16 Preview
        self.canvas = PreviewCanvas9x16()
        self.canvas.cfg = dict(self.ts_data)
        right_layout.addWidget(self.canvas, 1)

        # 3. 2 Ô Nhập Mẫu Thử Nghiệm
        gb_sample = QGroupBox("📝 Nhập Văn Bản Thử Nghiệm Xem Ngay")
        gb_sample.setStyleSheet("QGroupBox { color: #f9e2af; }")
        fl_sample = QFormLayout(gb_sample)
        fl_sample.setContentsMargins(12, 14, 12, 8)
        fl_sample.setSpacing(6)

        self.txt_sample_title = QLineEdit()
        self.txt_sample_title.setPlaceholderText("Nhập tiêu đề mẫu...")
        self.txt_sample_title.setText("TIÊU ĐỀ VIDEO MẪU\nDÒNG PHỤ BANNER")
        self.txt_sample_title.textChanged.connect(self._schedule_refresh)
        fl_sample.addRow("Title test:", self.txt_sample_title)

        self.txt_sample_sub = QLineEdit()
        self.txt_sample_sub.setPlaceholderText("Nhập phụ đề mẫu...")
        self.txt_sample_sub.setText("Đây là phụ đề mẫu đang hiển thị thử nghiệm...")
        self.txt_sample_sub.textChanged.connect(self._schedule_refresh)
        fl_sample.addRow("Sub test:", self.txt_sample_sub)

        right_layout.addWidget(gb_sample)

        # Label trạng thái thông số
        self.lbl_specs = QLabel("")
        self.lbl_specs.setStyleSheet("color: #a6adc8; font-size: 11px;")
        self.lbl_specs.setAlignment(Qt.AlignCenter)
        right_layout.addWidget(self.lbl_specs)

    # -----------------------------------------------------------------
    # CƠ CHẾ NẠP HÌNH ẢNH MẪU (VIDEO / CACHE / YOUTUBE)
    # -----------------------------------------------------------------
    def _load_initial_background(self):
        # 1. Nếu có sample_video_path được truyền vào trực tiếp từ chế độ hiện tại -> BỐC NGAY TỪ VIDEO NÀY
        if self.sample_video_path:
            if os.path.exists(self.sample_video_path) and os.path.isfile(self.sample_video_path):
                if self._extract_frame_from_video(self.sample_video_path):
                    return
            elif "youtube.com" in self.sample_video_path or "youtu.be" in self.sample_video_path:
                self.txt_yt_url.setText(self.sample_video_path)
                self._fetch_youtube_sample()
                return

        # 2. Thử lấy từ cache RIÊNG BIỆT của chế độ hiện tại (tuyệt đối không đọc cache chế độ khác)
        if hasattr(self, "system_cache_path") and self.system_cache_path.exists():
            pix = QPixmap(str(self.system_cache_path))
            if not pix.isNull():
                self.canvas.set_background_pixmap(pix)
                self.lbl_src_status.setText(f"✅ Đã nạp ảnh nền từ Cache [{self.app_mode}] ({pix.width()}×{pix.height()})")
                self.lbl_src_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
                return

        # 3. Thử tìm video theo đúng chế độ hiện tại:
        if getattr(self, "is_tiktok_mode", False):
            # Chế độ TikTok: tìm video trong output_tiktok_remix hoặc temp/tiktok_cache
            for t_dir in [APP_DIR / "output_tiktok_remix", APP_DIR / "temp" / "tiktok_cache"]:
                if t_dir.exists():
                    mp4s = sorted(t_dir.glob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)
                    if mp4s and self._extract_frame_from_video(str(mp4s[0])):
                        return
        else:
            # Chế độ 1 và 2: tìm trong thư mục downloads
            dl_dir = APP_DIR / "downloads"
            if dl_dir.exists():
                mp4s = sorted([f for f in dl_dir.glob("*.mp4") if not ".tmp." in f.name and f.stat().st_size > 100000], key=lambda f: f.stat().st_mtime, reverse=True)
                if mp4s and self._extract_frame_from_video(str(mp4s[0])):
                    return

        # 4. Dự phòng: Thử lấy từ layout_base riêng của chế độ hiện tại
        if hasattr(self, "layout_base_path") and self.layout_base_path.exists():
            pix = QPixmap(str(self.layout_base_path))
            if not pix.isNull():
                self.canvas.set_background_pixmap(pix)
                self.lbl_src_status.setText(f"✅ Đã nạp ảnh nền từ Cache Layout Base [{self.app_mode}] ({pix.width()}×{pix.height()})")
                self.lbl_src_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
                return

        if getattr(self, "is_tiktok_mode", False):
            self.lbl_src_status.setText("ℹ️ Chưa có video TikTok mẫu. Chọn video hoặc nhập link TikTok ở màn hình chính.")
        else:
            self.lbl_src_status.setText("ℹ️ Chưa có ảnh mẫu. Dán link YouTube hoặc bấm 'Chọn Video' để xem thử trên video thật.")
        self.lbl_src_status.setStyleSheet("color: #f9e2af; font-size: 11px;")

    def _extract_frame_from_video(self, video_path: str) -> bool:
        try:
            import cv2
            cap = cv2.VideoCapture(video_path)
            if cap.isOpened():
                cap.set(cv2.CAP_PROP_POS_MSEC, 2000.0)
                ret, frame = cap.read()
                if not ret or frame is None:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    ret, frame = cap.read()
                cap.release()
                if ret and frame is not None:
                    h, w, ch = frame.shape
                    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    qimg = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
                    pix = QPixmap.fromImage(qimg)
                    self.canvas.set_background_pixmap(pix)
                    self.lbl_src_status.setText(f"✅ Đã trích xuất frame từ: {Path(video_path).name} ({w}×{h})")
                    self.lbl_src_status.setStyleSheet("color: #a6e3a1; font-size: 11px;")
                    # Lưu cache riêng của chế độ hiện tại
                    if hasattr(self, "system_cache_path"):
                        try:
                            self.system_cache_path.parent.mkdir(parents=True, exist_ok=True)
                            cv2.imwrite(str(self.system_cache_path), frame)
                        except Exception:
                            pass
                    return True
        except Exception:
            pass
        self.lbl_src_status.setText(f"⚠️ Không đọc được frame từ: {Path(video_path).name}")
        self.lbl_src_status.setStyleSheet("color: #f38ba8; font-size: 11px;")
        return False

    def _on_choose_video_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Chọn Video Làm Mẫu",
            str(APP_DIR / "downloads"),
            "Video Files (*.mp4 *.mkv *.mov *.avi *.webm)"
        )
        if file_path:
            self._extract_frame_from_video(file_path)

    def _fetch_youtube_sample(self):
        url = self.txt_yt_url.text().strip()
        if not url:
            self.lbl_src_status.setText("⚠️ Hãy dán link YouTube hoặc Video ID vào ô bên cạnh.")
            self.lbl_src_status.setStyleSheet("color: #f9e2af; font-size: 11px;")
            return

        self.lbl_src_status.setText("⏳ Đang tải ảnh mẫu từ YouTube...")
        self.lbl_src_status.setStyleSheet("color: #fab387; font-size: 11px;")
        self.btn_fetch_yt.setEnabled(False)

        self._yt_fetcher = YouTubeThumbnailFetcher(url, self)
        self._yt_fetcher.finished_sig.connect(self._on_youtube_fetched)
        self._yt_fetcher.start()

    def _on_youtube_fetched(self, pixmap, status_text, color_hex):
        self.btn_fetch_yt.setEnabled(True)
        self.lbl_src_status.setText(status_text)
        self.lbl_src_status.setStyleSheet(f"color: {color_hex}; font-size: 11px;")
        if pixmap:
            self.canvas.set_background_pixmap(pixmap)

    # -----------------------------------------------------------------
    # CƠ CHẾ ĐỒNG BỘ VÀ XỬ LÝ SỰ KIỆN
    # -----------------------------------------------------------------
    def _schedule_refresh(self, *_):
        self._debounce_timer.start()

    def _on_part_pos_changed(self):
        is_bottom = self.rb_part_bottom.isChecked()
        self.w_bottom_part_coords.setVisible(is_bottom)
        self._schedule_refresh()

    def _sync_canvas_preview(self):
        cfg = self.get_values()
        part_num = self.txt_part_test_num.text().strip() or "1"
        self.canvas.update_data(
            cfg,
            self.txt_sample_title.text(),
            self.txt_sample_sub.text(),
            part_num=part_num
        )
        sk = self.combo_sub_style.currentData() or "tiktok_slim"
        preset_info = SUBTITLE_PRESETS.get(sk, {})
        part_pos_desc = "Sau Title (Trên)" if cfg.get("part_position") == "after_title" else f"Dưới Y={cfg.get('part_y_pos')}px"
        self.lbl_specs.setText(
            f"Phong cách: {preset_info.get('name', sk)}  |  "
            f"Cỡ Sub: {self.sp_sub_size.value()}px (Lề {self.sp_sub_margin_v.value()}px)  |  "
            f"Title Y: {self.sp_title_y.value()}px  |  "
            f"Vị trí Part: {part_pos_desc}"
        )

    def _on_preset_selected(self, _):
        sk = self.combo_sub_style.currentData() or "tiktok_slim"
        preset = SUBTITLE_PRESETS.get(sk, SUBTITLE_PRESETS["tiktok_slim"])

        widgets = [self.sp_sub_size, self.sp_sub_outline, self.sp_sub_shadow, self.sp_sub_margin_v]
        for w in widgets:
            w.blockSignals(True)

        self.sp_sub_size.setValue(preset["sub_size"])
        self.sp_sub_outline.setValue(preset["sub_outline"])
        self.sp_sub_shadow.setValue(preset["sub_shadow"])
        self.sp_sub_margin_v.setValue(preset["sub_margin_v"])

        for w in widgets:
            w.blockSignals(False)

        self._schedule_refresh()

    def _toggle_no_outline(self, state):
        is_no = bool(state)
        self.btn_outline.setEnabled(not is_no)
        if is_no:
            self._title_outline = "none"
        else:
            if str(self._title_outline).lower() == "none":
                self._title_outline = "#000000"
            _apply_color_btn_visuals(self.btn_outline, self._title_outline)
        self._schedule_refresh()

    def _choose_title_color(self, target: str):
        mapping = {
            "c1": (self._title_color1, "Chọn Màu Chữ Dòng 1", self.btn_c1),
            "c2": (self._title_color2, "Chọn Màu Chữ Dòng 2", self.btn_c2),
            "outline": (
                self._title_outline if str(self._title_outline).lower() != "none" else "#000000",
                "Chọn Màu Viền Chữ", self.btn_outline
            ),
            "bg": (self._title_bg, "Chọn Màu Nền Banner", self.btn_bg),
        }
        if target not in mapping:
            return
        cur_hex, title, btn = mapping[target]
        picked = QColorDialog.getColor(QColor(cur_hex), self, title)
        if not picked.isValid():
            return
        new_hex = picked.name().upper()
        if target == "c1":
            self._title_color1 = new_hex
        elif target == "c2":
            self._title_color2 = new_hex
        elif target == "outline":
            self._title_outline = new_hex
        elif target == "bg":
            self._title_bg = new_hex
        _apply_color_btn_visuals(btn, new_hex)
        self._schedule_refresh()

    def _choose_sub_color(self, target: str):
        if target == "color":
            cur_hex = ass_to_hex(self._sub_color_ass)
            title = "Chọn Màu Chữ Phụ Đề (Sẽ tự đổi sang ASS)"
            btn = self.btn_sub_color
        else:
            cur_hex = ass_to_hex(self._sub_ol_ass)
            title = "Chọn Màu Viền Phụ Đề (Sẽ tự đổi sang ASS)"
            btn = self.btn_sub_outline_color

        picked = QColorDialog.getColor(QColor(cur_hex), self, title)
        if not picked.isValid():
            return
        new_hex = picked.name().upper()
        new_ass = hex_to_ass(new_hex)
        if target == "color":
            self._sub_color_ass = new_ass
        else:
            self._sub_ol_ass = new_ass
        _apply_color_btn_visuals(btn, new_hex)
        self._schedule_refresh()

    def _reset_to_defaults(self):
        """Khôi phục về cấu hình TikTok Slim tối ưu mặc định."""
        self.chk_title.setChecked(True)
        self.txt_title.clear()
        self.sp_title_y.setValue(320)
        self._title_color1 = "#000000"
        self._title_color2 = "#ff0000"
        self._title_outline = "none"
        self._title_bg = "#ffffff"

        _apply_color_btn_visuals(self.btn_c1, "#000000")
        _apply_color_btn_visuals(self.btn_c2, "#ff0000")
        _apply_color_btn_visuals(self.btn_outline, "#000000")
        _apply_color_btn_visuals(self.btn_bg, "#ffffff")
        self.chk_no_outline.setChecked(True)

        self.chk_part.setChecked(True)
        self.txt_part_format.setText("Part")
        self.txt_part_test_num.setText("1")
        self.rb_part_after_title.setChecked(True)
        self.sp_part_y.setValue(1600)
        self.sp_part_size.setValue(22)

        idx_slim = self.combo_sub_style.findData("tiktok_slim")
        self.combo_sub_style.setCurrentIndex(idx_slim if idx_slim >= 0 else 0)
        self.chk_sub.setChecked(True)

        self._sub_color_ass = DEFAULT_SUB_COLOR
        self._sub_ol_ass = DEFAULT_SUB_OUTLINE_COLOR
        _apply_color_btn_visuals(self.btn_sub_color, ass_to_hex(DEFAULT_SUB_COLOR))
        _apply_color_btn_visuals(self.btn_sub_outline_color, ass_to_hex(DEFAULT_SUB_OUTLINE_COLOR))

        self._schedule_refresh()

    def get_values(self) -> Dict[str, Any]:
        """Thu thập toàn bộ thông số cấu hình Title, Part & Subtitle."""
        is_title = self.chk_title.isChecked()
        is_sub = self.chk_sub.isChecked()
        style_key = self.combo_sub_style.currentData() or "tiktok_slim"
        preset = SUBTITLE_PRESETS.get(style_key, SUBTITLE_PRESETS["tiktok_slim"])
        outline_val = "none" if self.chk_no_outline.isChecked() else self._title_outline

        part_pos = "bottom" if self.rb_part_bottom.isChecked() else "after_title"

        result = {
            # Tiêu đề Banner
            "show_title": is_title,
            "enable_title": is_title,
            "title_text": self.txt_title.text().strip(),
            "title_y_pos": self.sp_title_y.value(),
            "title_color1": self._title_color1,
            "title_color2": self._title_color2,
            "title_outline_color": outline_val,
            "title_bg_color": self._title_bg,
            # Cấu hình Số Part
            "show_part": self.chk_part.isChecked(),
            "part_format": self.txt_part_format.text().strip() or "Part",
            "part_position": part_pos,  # "after_title" hoặc "bottom"
            "part_y_pos": self.sp_part_y.value(),
            "part_size": self.sp_part_size.value(),
            # Phụ đề Subtitle
            "auto_sub": is_sub,
            "enable_sub": is_sub,
            "sub_style_type": style_key,
            "sub_size": self.sp_sub_size.value(),
            "sub_outline": self.sp_sub_outline.value(),
            "sub_shadow": self.sp_sub_shadow.value(),
            "sub_margin_v": self.sp_sub_margin_v.value(),
            "sub_color": self._sub_color_ass,
            "sub_outline_color": self._sub_ol_ass,
            "sub_uppercase": preset["uppercase"],
        }
        return result


# =====================================================================
# 7. HÀM MỞ POPUP CONVENIENCE (ENTRY POINT)
# =====================================================================
def open_title_sub_studio_popup(
    parent=None,
    current_data: Optional[Dict[str, Any]] = None,
    sample_video_path: str = "",
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    cfg = dict(current_config or current_data or {})
    vpath = video_source or sample_video_path or cfg.get("video_source") or cfg.get("source_url_or_path") or cfg.get("youtube_url") or ""
    dlg = TitleSubStudioDialog(cfg, sample_video_path=vpath, parent=None)
    if dlg.exec() == QDialog.Accepted:
        res = dlg.get_values()
        if on_save_callback:
            on_save_callback(res)
        return res
    return cfg
