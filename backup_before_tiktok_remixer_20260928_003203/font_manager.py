"""Quản lý Font Quốc Tế tự động phát hiện mã Unicode (FontManager).

Giải quyết triệt để lỗi ô vuông font chữ (Tofu []) cho mọi ngôn ngữ:
- Cyrillic: Nga, Ukraine, Belarus...
- Arabic: Ả Rập, Urdu, Pakistan, Ba Tư...
- Latin Extended: Việt Nam, Đức, Pháp, Tây Ban Nha, Thổ Nhĩ Kỳ...
- CJK: Trung, Nhật, Hàn.
Dùng chung cho cả Chế độ Tóm Tắt và Chế độ Chia Part.
"""
from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple
from PIL import ImageFont


# Bảng dải mã Unicode chuẩn
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0500-\u052F\u2DE0-\u2DFF\uA640-\uA69F]")
_CJK_RE = re.compile(r"[\u4E00-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]")
_VIETNAMESE_RE = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđĐ]")


def detect_script(text: str) -> str:
    """Tự động phát hiện hệ thống chữ viết dựa trên tần suất ký tự Unicode.
    
    Returns: 'arabic' | 'cyrillic' | 'cjk' | 'latin_extended'
    """
    if not text:
        return "latin_extended"

    sample = str(text)
    # Ưu tiên kiểm tra các script đặc thù
    if _ARABIC_RE.search(sample):
        return "arabic"
    if _CYRILLIC_RE.search(sample):
        return "cyrillic"
    if _CJK_RE.search(sample):
        return "cjk"
    return "latin_extended"


class FontManager:
    """Bộ nạp và quản lý Font thông minh đa ngôn ngữ."""

    _FONT_CACHE: Dict[Tuple[str, int], ImageFont.FreeTypeFont] = {}
    
    # Danh sách đường dẫn Font chuẩn cho từng script trên Windows & Fallback
    WINDOWS_FONT_DIR = os.environ.get("WINDIR", "C:\\Windows") + "\\Fonts"

    BANNER_FONT_CANDIDATES: Dict[str, List[str]] = {
        "cyrillic": [
            os.path.join(WINDOWS_FONT_DIR, "seguibl.ttf"),  # Segoe UI Black (Hỗ trợ Cyrillic cực đẹp)
            os.path.join(WINDOWS_FONT_DIR, "ariblk.ttf"),   # Arial Black
            os.path.join(WINDOWS_FONT_DIR, "arialbd.ttf"),  # Arial Bold
            os.path.join(WINDOWS_FONT_DIR, "arial.ttf"),
        ],
        "arabic": [
            os.path.join(WINDOWS_FONT_DIR, "tahomabd.ttf"), # Tahoma Bold
            os.path.join(WINDOWS_FONT_DIR, "tahoma.ttf"),   # Tahoma chuẩn tiếng Ả Rập/Urdu
            os.path.join(WINDOWS_FONT_DIR, "arialbd.ttf"),
            os.path.join(WINDOWS_FONT_DIR, "arial.ttf"),
        ],
        "cjk": [
            os.path.join(WINDOWS_FONT_DIR, "msyhbd.ttc"),   # Microsoft YaHei Bold
            os.path.join(WINDOWS_FONT_DIR, "msyh.ttc"),     # Microsoft YaHei
            os.path.join(WINDOWS_FONT_DIR, "malgunbd.ttf"), # Malgun Gothic Bold
            os.path.join(WINDOWS_FONT_DIR, "malgun.ttf"),
            os.path.join(WINDOWS_FONT_DIR, "arialbd.ttf"),
        ],
        "latin_extended": [
            os.path.join(WINDOWS_FONT_DIR, "impact.ttf"),   # Impact đậm viral
            os.path.join(WINDOWS_FONT_DIR, "seguibl.ttf"),  # Segoe UI Black (rất đầy đủ dấu tiếng Việt)
            os.path.join(WINDOWS_FONT_DIR, "ariblk.ttf"),   # Arial Black
            os.path.join(WINDOWS_FONT_DIR, "arialbd.ttf"),
            os.path.join(WINDOWS_FONT_DIR, "arial.ttf"),
        ]
    }

    SUBTITLE_FONT_NAMES: Dict[str, str] = {
        "cyrillic": "Segoe UI Black",
        "arabic": "Tahoma",
        "cjk": "Microsoft YaHei",
        "latin_extended": "Segoe UI Black",  # Segoe UI Black không bao giờ lỗi dấu tiếng Việt như Impact cũ
    }

    @classmethod
    def get_banner_font_path(cls, text: str = "") -> str:
        """Trả về đường dẫn font TTF phù hợp nhất cho text đầu vào."""
        script = detect_script(text)
        candidates = cls.BANNER_FONT_CANDIDATES.get(script, cls.BANNER_FONT_CANDIDATES["latin_extended"])
        
        # Nếu là tiếng Việt có dấu, Segoe UI Black hiển thị dấu đẹp và tròn trịa hơn Impact
        if script == "latin_extended" and _VIETNAMESE_RE.search(text or ""):
            candidates = [
                os.path.join(cls.WINDOWS_FONT_DIR, "seguibl.ttf"),
                os.path.join(cls.WINDOWS_FONT_DIR, "impact.ttf"),
                os.path.join(cls.WINDOWS_FONT_DIR, "ariblk.ttf"),
                os.path.join(cls.WINDOWS_FONT_DIR, "arialbd.ttf"),
            ]

        for path in candidates:
            if os.path.isfile(path):
                return path

        # Fallback chung
        for fallback in [
            os.path.join(cls.WINDOWS_FONT_DIR, "arialbd.ttf"),
            os.path.join(cls.WINDOWS_FONT_DIR, "arial.ttf"),
            os.path.join(cls.WINDOWS_FONT_DIR, "tahoma.ttf"),
        ]:
            if os.path.isfile(fallback):
                return fallback
        return ""

    @classmethod
    def get_banner_font(cls, text: str, size: int) -> ImageFont.FreeTypeFont:
        """Nạp font PIL ImageFont hỗ trợ hiển thị đầy đủ text không bị tofu []."""
        font_path = cls.get_banner_font_path(text)
        size = max(12, int(size))
        cache_key = (font_path, size)

        if cache_key in cls._FONT_CACHE:
            return cls._FONT_CACHE[cache_key]

        if font_path and os.path.isfile(font_path):
            try:
                font = ImageFont.truetype(font_path, size)
                cls._FONT_CACHE[cache_key] = font
                return font
            except Exception:
                pass

        # Fallback về default của PIL nếu không nạp được
        try:
            return ImageFont.load_default()
        except Exception:
            return None

    # 3 Kiểu Style phụ đề chính quy (Cổ điển in hoa to, Thanh mảnh giống No.1, Bình thường)
    SUBTITLE_STYLE_MATRIX: Dict[str, Dict] = {
        "classic": {
            "name": "Cổ điển (In hoa to)",
            "default_size": 26,
            "default_outline": 3,
            "default_shadow": 0,
            "uppercase": True,
            "bold": 1,
            "fonts": {
                "latin_extended": "Impact",
                "cyrillic": "Segoe UI Black",
                "arabic": "Tahoma",
                "cjk": "Microsoft YaHei",
            }
        },
        "tiktok_slim": {
            "name": "Thanh mảnh (Giống No.1)",
            "default_size": 16,
            "default_outline": 2,
            "default_shadow": 1,
            "uppercase": False,
            "bold": 1,
            "fonts": {
                "latin_extended": "Arial",
                "cyrillic": "Arial",
                "arabic": "Tahoma",
                "cjk": "Microsoft YaHei",
            }
        },
        "standard": {
            "name": "Bình thường (Tiêu chuẩn)",
            "default_size": 21,
            "default_outline": 2,
            "default_shadow": 0,
            "uppercase": False,
            "bold": 0,
            "fonts": {
                "latin_extended": "Segoe UI",
                "cyrillic": "Segoe UI",
                "arabic": "Tahoma",
                "cjk": "Microsoft YaHei",
            }
        }
    }

    @classmethod
    def get_subtitle_font_name(cls, sample_text: str = "") -> str:
        """Lấy tên Font Family cho Subtitle (ASS / SRT / FFmpeg drawtext)."""
        script = detect_script(sample_text)
        return cls.SUBTITLE_FONT_NAMES.get(script, "Segoe UI Black")

    @classmethod
    def get_subtitle_style_specs(
        cls,
        style_type: str = "tiktok_slim",
        sample_text: str = "",
        custom_overrides: Optional[Dict] = None
    ) -> Dict:
        """
        Trả về toàn bộ thông số chuẩn cho Subtitle theo 3 Style và tự động tương thích quốc tế (0% Tofu):
        - style_type: 'classic' | 'tiktok_slim' | 'standard'
        - sample_text: mẫu text phụ đề để tự động phát hiện ngôn ngữ quốc gia (Nga, Đức, Pháp, Ả Rập...)
        - custom_overrides: các giá trị người dùng tùy chỉnh từ GUI (sub_size, sub_outline, sub_shadow, v.v.)
        """
        style_key = str(style_type or "tiktok_slim").lower().strip()
        if style_key not in cls.SUBTITLE_STYLE_MATRIX:
            if any(k in style_key for k in ["classic", "co_dien", "hoa", "bold"]):
                style_key = "classic"
            elif any(k in style_key for k in ["standard", "binh_thuong", "normal"]):
                style_key = "standard"
            else:
                style_key = "tiktok_slim"

        meta = cls.SUBTITLE_STYLE_MATRIX[style_key]
        script = detect_script(sample_text)
        font_family = meta["fonts"].get(script, meta["fonts"]["latin_extended"])

        # Nếu là tiếng Việt và phong cách classic, Segoe UI Black thể hiện dấu hỏi ngã nặng tròn trịa không lệch
        if style_key == "classic" and script == "latin_extended" and _VIETNAMESE_RE.search(sample_text or ""):
            font_family = "Segoe UI Black"

        overrides = custom_overrides or {}

        # Đọc giá trị tùy chỉnh từ người dùng (nếu có) hoặc dùng mặc định theo style
        size = int(overrides.get("sub_size") or meta["default_size"])
        outline = int(overrides.get("sub_outline") if overrides.get("sub_outline") is not None else meta["default_outline"])
        shadow = int(overrides.get("sub_shadow") if overrides.get("sub_shadow") is not None else meta["default_shadow"])

        return {
            "style_type": style_key,
            "style_name": meta["name"],
            "script": script,
            "font_name": font_family,
            "font_size": size,
            "outline": outline,
            "shadow": shadow,
            "uppercase": meta["uppercase"],
            "bold": meta["bold"],
        }
