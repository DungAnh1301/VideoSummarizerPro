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
            "default_size": 56,
            "default_outline": 4,
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
            "default_size": 42,
            "default_outline": 2,
            "default_shadow": 0,
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
            "default_size": 48,
            "default_outline": 3,
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


# =====================================================================
# HẰNG SỐ & TIỆN ÍCH CHO BANNER TITLE & SUBTITLE
# =====================================================================
BANNER_TEXT_MAX_W = 950       # Chiều rộng tối đa của chữ trong banner
BANNER_PAD_X = 28             # Khoảng đệm ngang bên trong pill
BANNER_PAD_Y = 18             # Khoảng đệm dọc bên trong pill
BANNER_FONT_MIN = 32          # Cỡ chữ tối thiểu
BANNER_FONT_MAX_1LINE = 76    # Cỡ chữ tối đa cho title 1 dòng
BANNER_FONT_MAX_2LINE = 64    # Cỡ chữ tối đa cho title 2 dòng
BANNER_LINE_GAP_RATIO = 0.18  # Khoảng cách giữa 2 dòng = font_size * 0.18
BANNER_CORNER_RADIUS = 20     # Bán kính bo góc của khối pill

_MUSIC_CUE_RE = re.compile(
    r"\[(?:Music|âm nhạc|applause|cheering|laughter|tiếng vỗ tay|tiếng cười).*?\]|\((?:music|âm nhạc).*?\)|[\*♫♪#]+",
    re.IGNORECASE
)


def hex_to_ass_color(hex_str: str, default: str = "&HFFFFFF&") -> str:
    """Chuyển mã màu HEX (#RRGGBB) sang thứ tự BGR chuẩn ASS (&HBBGGRR&)."""
    if not hex_str:
        return default
    clean = str(hex_str).strip().lstrip('#')
    if clean.upper().startswith("&H") and clean.endswith("&"):
        return clean.upper()
    if clean.lower().startswith("0x"):
        clean = clean[2:]
    if len(clean) == 6:
        r, g, b = clean[0:2], clean[2:4], clean[4:6]
        return f"&H{b}{g}{r}&".upper()
    return default


def clean_subtitle_cue_text(text: str) -> str:
    """Lọc sạch thẻ âm nhạc [Music], ♪, v.v. và loại bỏ cue vô nghĩa."""
    if not text:
        return ""
    cleaned = _MUSIC_CUE_RE.sub("", text)
    cleaned = " ".join(cleaned.split()).strip()
    if not re.search(r"\w", cleaned):
        return ""
    return cleaned


def deoverlap_subtitle_cues(cues: list) -> list:
    """Khử trùng lặp mốc thời gian giữa các cue liên tiếp, tránh chữ bị nhảy giật hoặc chồng 2 dòng."""
    if not cues:
        return []
    valid = []
    for c in cues:
        txt = clean_subtitle_cue_text(c.get("text", ""))
        if not txt:
            continue
        valid.append({
            "start": float(c.get("start", 0)),
            "end": float(c.get("end", 0)),
            "text": txt
        })
    valid.sort(key=lambda x: x["start"])
    for idx in range(1, len(valid)):
        prev = valid[idx - 1]
        curr = valid[idx]
        if curr["start"] < prev["end"]:
            prev["end"] = max(prev["start"] + 0.1, curr["start"] - 0.02)
    return [c for c in valid if c["end"] > c["start"]]


def _banner_text_bbox(font, text: str, stroke_w: int = 0):
    if not text:
        return (0, 0, 0, 0)
    try:
        return font.getbbox(text, stroke_width=int(stroke_w) if stroke_w else 0)
    except TypeError:
        return font.getbbox(text)


def _banner_text_size(font, text: str, stroke_w: int = 0) -> tuple[int, int]:
    bbox = _banner_text_bbox(font, text, stroke_w)
    return max(0, bbox[2] - bbox[0]), max(0, bbox[3] - bbox[1])


def _truncate_banner_line(text: str, font, max_w: int, stroke_w: int = 0) -> str:
    if not text:
        return ""
    if _banner_text_size(font, text, stroke_w)[0] <= max_w:
        return text
    ell = "..."
    if _banner_text_size(font, ell, stroke_w)[0] > max_w:
        return ell
    lo, hi = 0, len(text)
    best = ell
    while lo <= hi:
        mid = (lo + hi) // 2
        cand = text[:mid].rstrip() + ell
        if _banner_text_size(font, cand, stroke_w)[0] <= max_w:
            best = cand
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _split_banner_title_balanced(title: str, font=None) -> tuple[str, str]:
    """Tách title thành 2 dòng cân bằng theo chuẩn Video_summarizer_Pro:
    1. ':' (chỉ ngắt nếu mỗi vế >= 25% độ dài)
    2. ' - ' (trong khoảng 1/3 đến 2/3 giữa)
    3. Quét các khoảng trắng, đo pixel font thực tế để tìm điểm chia tiệm cận 50/50 nhất.
    """
    clean = " ".join(str(title or "").split()).strip()
    if not clean:
        return "", ""
    n = len(clean)

    colon = clean.find(":")
    if colon >= 0:
        line1 = clean[: colon + 1].strip()
        line2 = clean[colon + 1 :].strip()
        if line1 and line2 and len(line1) >= 0.25 * n and len(line2) >= 0.25 * n:
            return line1, line2

    lo, hi = n / 3.0, (2.0 * n) / 3.0
    dash_hits = []
    start = 0
    while True:
        i = clean.find(" - ", start)
        if i < 0:
            break
        if lo <= i <= hi:
            dash_hits.append(i)
        start = i + 1
    if dash_hits:
        center = n / 2.0
        i = min(dash_hits, key=lambda x: abs(x - center))
        line1 = clean[:i].strip()
        line2 = clean[i + 3 :].strip()
        if line1 and line2:
            return line1, line2

    spaces = [i for i, ch in enumerate(clean) if ch == " "]
    if not spaces:
        mid = max(1, n // 2)
        return clean[:mid], clean[mid:]

    measure_font = font or FontManager.get_banner_font(clean, BANNER_FONT_MIN)
    best_i = None
    best_score = None
    for i in spaces:
        line1 = clean[:i].strip()
        line2 = clean[i + 1 :].strip()
        if not line1 or not line2:
            continue
        w1 = _banner_text_size(measure_font, line1)[0]
        w2 = _banner_text_size(measure_font, line2)[0]
        tot = (w1 + w2) or 1
        r1 = w1 / tot
        score = abs(r1 - 0.5)
        if 0.40 <= r1 <= 0.60:
            score -= 0.15
        if len(line2.split()) == 1 and len(line2) <= 8:
            score += 0.25
        if len(line1.split()) == 1 and len(line1) <= 8:
            score += 0.25
        if best_score is None or score < best_score:
            best_score = score
            best_i = i

    if best_i is None:
        mid = n // 2
        return clean[:mid], clean[mid:]
    return clean[:best_i].strip(), clean[best_i + 1 :].strip()


def _largest_banner_font(texts, max_size: int, min_size: int, max_w: int, stroke_w: int = 0):
    sample_text = " ".join([str(t) for t in texts if t])
    for size in range(int(max_size), int(min_size) - 1, -1):
        font = FontManager.get_banner_font(sample_text, size)
        if font and all(_banner_text_size(font, t, stroke_w)[0] <= max_w for t in texts if t):
            return size, font
    return None, FontManager.get_banner_font(sample_text, min_size)


def _join_banner_title_raw(line1: str, line2: str) -> str:
    parts = []
    for part in (line1, line2):
        cleaned = " ".join(str(part or "").split())
        if cleaned:
            parts.append(cleaned)
    return " ".join(parts)


def _layout_banner_title(raw_title: str, stroke_w: int = 0):
    """
    Bố cục title banner: tối đa 2 dòng, không thu font dưới FONT_MIN.
    1 dòng nếu fit >= FONT_MIN; không thì tách 2 dòng cân bằng rồi chọn cỡ lớn nhất.
    Chuẩn 100% thuật toán Video_summarizer_Pro.
    """
    title = " ".join(str(raw_title or "").split()) or "TITLE"
    max_w = BANNER_TEXT_MAX_W
    min_sz = BANNER_FONT_MIN

    size1, font1 = _largest_banner_font(
        [title], BANNER_FONT_MAX_1LINE, min_sz, max_w, stroke_w
    )
    if size1 is not None:
        return title, "", font1, size1

    measure_font = FontManager.get_banner_font(title, min_sz)
    line1, line2 = _split_banner_title_balanced(title, measure_font)
    if not line2:
        return line1, "", measure_font, min_sz

    size2, font2 = _largest_banner_font(
        [line1, line2], BANNER_FONT_MAX_2LINE, min_sz, max_w, stroke_w
    )
    if size2 is None:
        # Trường hợp title dài, hạ nhẹ dần về 26px -> 20px
        for test_min in range(min_sz - 1, 19, -2):
            size2, font2 = _largest_banner_font(
                [line1, line2], test_min, test_min, max_w, stroke_w
            )
            if size2 is not None:
                break
        if size2 is None:
            size2 = 20
            font2 = FontManager.get_banner_font(f"{line1} {line2}", size2)
            # Nếu tại 20px vẫn tràn nhẹ do từ quá dài, truncate an toàn không vỡ khung
            line1 = _truncate_banner_line(line1, font2, max_w, stroke_w)
            line2 = _truncate_banner_line(line2, font2, max_w, stroke_w)

    return line1, line2, font2, size2


def create_dynamic_title_banner(
    specific_dir: str,
    line1: str = "",
    line2: str = "",
    post_options: Optional[dict] = None
) -> tuple[str, int, int]:
    """
    Tạo ảnh Pill Banner bo góc chuẩn 100% thuật toán Video_summarizer_Pro.
    Tự động chia 2 dòng cân đối, co dãn cỡ chữ Dynamic Font Scaling.
    
    Returns: (output_png_path, banner_width, banner_height)
    """
    from PIL import Image, ImageDraw, ImageColor

    opts = post_options or {}
    c1 = opts.get("title_color1", opts.get("header_text_color", "#FFFFFF"))
    c2 = opts.get("title_color2", opts.get("header_text_color2", c1))
    outline_c = opts.get("title_outline_color", "none")
    bg_c = opts.get("title_bg_color", opts.get("header_bg_color", "#FF2D55"))

    def _parse_rgb(color_val, default):
        if not color_val or str(color_val).lower() == "none":
            return default
        s = str(color_val).strip()
        if s.startswith("0x"):
            s = "#" + s[2:]
        try:
            return ImageColor.getrgb(s)
        except Exception:
            return default

    fill_bg = _parse_rgb(bg_c, (255, 45, 85))
    fill_c1 = _parse_rgb(c1, (255, 255, 255))
    fill_c2 = _parse_rgb(c2, fill_c1)
    
    stroke_w = 2 if outline_c and str(outline_c).lower() != "none" else 0
    stroke_col = _parse_rgb(outline_c, (0, 0, 0)) if stroke_w else None

    raw = _join_banner_title_raw(line1, line2)
    l1, l2, font, font_size = _layout_banner_title(raw, stroke_w=stroke_w)

    pad_x = BANNER_PAD_X
    pad_y = BANNER_PAD_Y
    two_lines = bool(l1 and l2)
    spacing = int(font_size * BANNER_LINE_GAP_RATIO) if two_lines else 0

    b1 = _banner_text_bbox(font, l1, stroke_w) if l1 else (0, 0, 0, 0)
    w1 = (b1[2] - b1[0]) if l1 else 0
    h1 = (b1[3] - b1[1]) if l1 else 0
    b2 = _banner_text_bbox(font, l2, stroke_w) if l2 else (0, 0, 0, 0)
    w2 = (b2[2] - b2[0]) if l2 else 0
    h2 = (b2[3] - b2[1]) if l2 else 0

    max_w = max(w1, w2, 1)
    total_h = (h1 if l1 else 0) + (h2 if l2 else 0) + spacing
    bw = int(max_w + pad_x * 2)
    bh = int(total_h + pad_y * 2)

    # Đảm bảo chiều rộng banner không bao giờ tràn khỏi 1080px (tối đa 1020px)
    bw = min(1020, bw)

    img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(
        [(0, 0), (bw, bh)],
        radius=BANNER_CORNER_RADIUS,
        fill=fill_bg + (255,)
    )

    cy = pad_y
    if l1:
        draw.text(
            ((bw - w1) / 2 - b1[0], cy - b1[1]),
            l1,
            fill=fill_c1 + (255,),
            font=font,
            stroke_width=stroke_w,
            stroke_fill=(stroke_col + (255,)) if stroke_col else None,
        )
        cy += h1 + spacing
    if l2:
        draw.text(
            ((bw - w2) / 2 - b2[0], cy - b2[1]),
            l2,
            fill=fill_c2 + (255,),
            font=font,
            stroke_width=stroke_w,
            stroke_fill=(stroke_col + (255,)) if stroke_col else None,
        )

    os.makedirs(specific_dir, exist_ok=True)
    out_path = os.path.join(specific_dir, "title_banner_final.png")
    img.save(out_path)
    return out_path, bw, bh
