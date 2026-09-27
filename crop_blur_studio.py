"""
CROP & ZOOM SCALE & BLUR MASK STUDIO (WYSIWYG 1:1)
Bố cục hình học, cắt viền, tỷ lệ co giãn ngang/dọc và nền mờ chuẩn 1080x1920 (9:16).
Hệ quy chiếu chuẩn đích: 1080 x 1920.
"""
import os
import json
import math
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Optional, Dict, Any, Callable
from PIL import Image, ImageTk, ImageFilter, ImageDraw
import cv2
import numpy as np

# Thư mục lưu cache & phôi ảnh 3 tầng
SYSTEM_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_data")
os.makedirs(SYSTEM_DATA_DIR, exist_ok=True)
CACHE_FRAME_PATH = os.path.join(SYSTEM_DATA_DIR, "crop_preview_cache.jpg")
LAYOUT_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "layout_base.jpg")


def generate_vibrant_sample_frame(output_path: str = CACHE_FRAME_PATH) -> str:
    """Tạo ảnh mẫu 1920x1080 rực rỡ, tương phản cao để test Crop, Zoom, Blur & Màu sắc."""
    w, h = 1920, 1080
    base = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(base)
    # Gradient màu sắc từ chàm đậm sang cam hoàng hôn
    for y in range(h):
        r = int(24 + (230 - 24) * (y / h))
        g = int(24 + (70 - 24) * (y / h))
        b = int(70 + (25 - 70) * (y / h))
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    
    # Dải màu Color Bars chuẩn ở đáy
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

    draw.text((cx, cy - 100), "🎬 SOURCE VIDEO SAMPLE (16:9)", fill=(255, 230, 0), anchor="mm", font=font_large)
    draw.text((cx, cy - 30), "Khung Hình Mẫu Trực Quan 1920 x 1080", fill=(255, 255, 255), anchor="mm", font=font_sub)
    draw.text((cx, cy + 50), "Hỗ trợ xem trước Crop, Zoom Scale, Blur Background & Màu CapCut", fill=(200, 230, 255), anchor="mm", font=font_sub)

    draw.rectangle([60, 50, 280, 105], fill=(239, 68, 68))
    draw.text((170, 77), "LIVE PREVIEW", fill=(255, 255, 255), anchor="mm", font=font_tag)

    draw.rectangle([w - 300, 50, w - 60, 105], fill=(59, 130, 246))
    draw.text((w - 180, 77), "FHD 1080x1920", fill=(255, 255, 255), anchor="mm", font=font_tag)

    base.save(output_path, quality=95)
    return output_path


def get_sample_or_fallback_image(video_path: Optional[str] = None) -> str:
    """Lấy frame từ video hoặc tìm ảnh mẫu trong dự án, fallback sang ảnh rực rỡ sinh tự động."""
    if video_path and os.path.isfile(video_path):
        extracted = extract_sample_frame(video_path, timestamp_sec=2.0)
        if extracted and os.path.isfile(extracted):
            return extracted

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


def extract_sample_frame(video_path: str, timestamp_sec: float = 2.0) -> Optional[str]:
    """Bốc frame mẫu ở giây thứ 2.0s (tránh màn hình đen 0.0s) và lưu vào cache."""
    if not video_path or not os.path.isfile(video_path):
        return None
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        frame_num = int(timestamp_sec * fps)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
        ret, frame = cap.read()
        if not ret or frame is None:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ret, frame = cap.read()
        cap.release()
        if ret and frame is not None:
            cv2.imwrite(CACHE_FRAME_PATH, frame)
            return CACHE_FRAME_PATH
    except Exception as e:
        print(f"[CROP STUDIO] cv2 extract frame error: {e}")

    # Fallback FFmpeg
    try:
        cmd = [
            "ffmpeg", "-y", "-ss", str(timestamp_sec), "-i", video_path,
            "-vframes", "1", "-q:v", "2", CACHE_FRAME_PATH
        ]
        subprocess.run(cmd, capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
        if os.path.isfile(CACHE_FRAME_PATH):
            return CACHE_FRAME_PATH
    except Exception:
        pass
    return None


def calculate_fg_dimensions(
    crop_w: int, crop_h: int,
    zoom_pct: float, scale_x: float, scale_y: float,
    target_w: int = 1080
) -> tuple:
    """
    Tính kích thước Foreground (fg_w, fg_h, pos_x, pos_y) chuẩn xác 100% với FFmpeg:
    TW = 1080
    R_crop = crop_h / crop_w
    base_h = TW * R_crop
    fg_w = round(TW * zoom_pct * scale_x) -> chẵn
    fg_h = round(base_h * zoom_pct * scale_y) -> chẵn
    pos_x = (1080 - fg_w) // 2
    pos_y = (1920 - fg_h) // 2
    """
    if crop_w <= 0:
        crop_w = 1920
    if crop_h <= 0:
        crop_h = 1080
    
    r_crop = crop_h / crop_w
    base_h = target_w * r_crop

    fg_w = int(round(target_w * zoom_pct * scale_x))
    fg_h = int(round(base_h * zoom_pct * scale_y))

    if fg_w % 2 != 0:
        fg_w += 1
    if fg_h % 2 != 0:
        fg_h += 1

    pos_x = (1080 - fg_w) // 2
    pos_y = (1920 - fg_h) // 2
    return fg_w, fg_h, pos_x, pos_y


def render_layout_image(
    source_img_path: str,
    crop_x: int, crop_y: int, crop_w: int, crop_h: int,
    zoom_pct: float, scale_x: float, scale_y: float,
    blur_bg: bool = True,
    blur_mask: Optional[Dict[str, Any]] = None,
    output_path: Optional[str] = LAYOUT_BASE_PATH
) -> Image.Image:
    """
    Dựng ảnh Layout 1080x1920 (WYSIWYG 1:1) theo đúng công thức FFmpeg filter:
    [0:v]split=2[orig][copy];
    [copy]scale=360:640:force_original_aspect_ratio=increase,crop=360:640,boxblur=12:6,scale=1080:1920:flags=bilinear[bg];
    [orig]crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale={fg_w}:{fg_h}[fg];
    [bg][fg]overlay=(W-w)/2:(H-h)/2:shortest=1[v_layout]
    """
    if os.path.isfile(source_img_path):
        src_img = Image.open(source_img_path).convert("RGB")
    else:
        # Ảnh mẫu mặc định
        src_img = Image.new("RGB", (1920, 1080), color=(40, 50, 70))
        d = ImageDraw.Draw(src_img)
        d.rectangle([100, 100, 1820, 980], outline=(255, 255, 255), width=4)
        d.text((800, 500), "SAMPLE VIDEO FRAME (1920x1080)", fill=(255, 255, 255))

    orig_w, orig_h = src_img.size

    # Validate crop box
    crop_x = max(0, min(orig_w - 10, crop_x))
    crop_y = max(0, min(orig_h - 10, crop_y))
    crop_w = max(10, min(orig_w - crop_x, crop_w))
    crop_h = max(10, min(orig_h - crop_y, crop_h))

    # 1. Tạo Background 1080x1920
    if blur_bg:
        # Scale 360x640 cover -> Boxblur -> Scale 1080x1920
        bg_scale = max(360 / orig_w, 640 / orig_h)
        bg_w = int(orig_w * bg_scale)
        bg_h = int(orig_h * bg_scale)
        bg_img = src_img.resize((bg_w, bg_h), Image.BILINEAR)
        # Crop 360x640 center
        left = (bg_w - 360) // 2
        top = (bg_h - 640) // 2
        bg_img = bg_img.crop((left, top, left + 360, top + 640))
        # Blur
        bg_img = bg_img.filter(ImageFilter.GaussianBlur(radius=8))
        # Scale up to 1080x1920
        bg_canvas = bg_img.resize((1080, 1920), Image.BILINEAR)
    else:
        bg_canvas = Image.new("RGB", (1080, 1920), color=(0, 0, 0))

    # 2. Cắt Foreground & Biến dạng
    fg_cropped = src_img.crop((crop_x, crop_y, crop_x + crop_w, crop_y + crop_h))
    fg_w, fg_h, pos_x, pos_y = calculate_fg_dimensions(crop_w, crop_h, zoom_pct, scale_x, scale_y, target_w=1080)
    fg_scaled = fg_cropped.resize((fg_w, fg_h), Image.BILINEAR)

    # 3. Ghép Foreground lên Background
    bg_canvas.paste(fg_scaled, (pos_x, pos_y))

    # 4. Áp Blur Mask (nếu có)
    if blur_mask and blur_mask.get("enabled"):
        bx = int(blur_mask.get("x", 100))
        by = int(blur_mask.get("y", 100))
        bw = int(blur_mask.get("w", 300))
        bh = int(blur_mask.get("h", 150))
        shape = blur_mask.get("shape", "rectangle")
        if bw > 0 and bh > 0 and bx < 1080 and by < 1920:
            bx = max(0, bx)
            by = max(0, by)
            bw = min(1080 - bx, bw)
            bh = min(1920 - by, bh)
            mask_region = bg_canvas.crop((bx, by, bx + bw, by + bh))
            blurred_region = mask_region.filter(ImageFilter.GaussianBlur(radius=15))
            if shape == "circle":
                mask_circle = Image.new("L", (bw, bh), 0)
                draw_c = ImageDraw.Draw(mask_circle)
                draw_c.ellipse((0, 0, bw, bh), fill=255)
                bg_canvas.paste(blurred_region, (bx, by), mask_circle)
            else:
                bg_canvas.paste(blurred_region, (bx, by))

    if output_path:
        try:
            bg_canvas.save(output_path, quality=95)
        except Exception:
            pass
    return bg_canvas


def open_crop_blur_studio_popup(
    parent,
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Cửa sổ Studio Cắt xén, Tỷ lệ Co Giãn & Nền mờ 9:16 WYSIWYG 1:1.
    """
    cfg = dict(current_config or {})
    video_path = video_source or cfg.get("video_source") or cfg.get("source_url_or_path") or ""

    # Trích xuất hoặc lấy ảnh mẫu chất lượng cao
    frame_path = get_sample_or_fallback_image(video_path)

    # Load kích thước ảnh gốc
    src_img = Image.open(frame_path).convert("RGB")
    orig_w, orig_h = src_img.size

    popup = tk.Toplevel(parent)
    popup.title("🎨 Chế Độ 3: Crop, Zoom Scale & Blur Mask Studio (WYSIWYG 1:1)")
    popup.geometry("1180x880")
    popup.minsize(980, 720)
    popup.transient(parent)
    popup.grab_set()

    # Bố cục 2 cột: Trái = Controls, Phải = Canvas Live 9:16
    left_frame = ttk.LabelFrame(popup, text=" ⚙ Thông Số Bố Cục Hình Học & Nền Mờ ", padding=12)
    left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

    right_frame = ttk.LabelFrame(popup, text=" 📱 Khung Hình Mẫu 9:16 (WYSIWYG 1:1 Chuẩn Đích) ", padding=10)
    right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    # Variables
    use_crop_var = tk.BooleanVar(value=bool(cfg.get("use_crop", False)))
    ratio_var = tk.StringVar(value=cfg.get("crop_ratio", "Tự do"))
    
    crop_x_var = tk.IntVar(value=int(cfg.get("crop_x", 0)))
    crop_y_var = tk.IntVar(value=int(cfg.get("crop_y", 0)))
    crop_w_var = tk.IntVar(value=int(cfg.get("crop_w", orig_w)))
    crop_h_var = tk.IntVar(value=int(cfg.get("crop_h", orig_h)))

    zoom_in_var = tk.DoubleVar(value=float(cfg.get("zoom_in", cfg.get("zoom_percent", 168.0))))
    scale_x_var = tk.DoubleVar(value=float(cfg.get("scale_x", cfg.get("scale_w_percent", 100.0))))
    scale_y_var = tk.DoubleVar(value=float(cfg.get("scale_y", cfg.get("scale_h_percent", 125.0))))

    blur_bg_var = tk.BooleanVar(value=bool(cfg.get("blur_bg", True)))

    blur_mask_cfg = cfg.get("blur_mask", {})
    use_blur_mask_var = tk.BooleanVar(value=bool(cfg.get("use_blur_mask", blur_mask_cfg.get("enabled", False))))
    blur_shape_var = tk.StringVar(value=cfg.get("blur_shape", blur_mask_cfg.get("shape", "Hình chữ nhật")))
    blur_x_var = tk.IntVar(value=int(cfg.get("blur_mask_x", blur_mask_cfg.get("x", 100))))
    blur_y_var = tk.IntVar(value=int(cfg.get("blur_mask_y", blur_mask_cfg.get("y", 1550))))
    blur_w_var = tk.IntVar(value=int(cfg.get("blur_mask_w", blur_mask_cfg.get("w", 880))))
    blur_h_var = tk.IntVar(value=int(cfg.get("blur_mask_h", blur_mask_cfg.get("h", 180))))

    # --- WIDGETS LEFT PANEL ---
    ttk.Checkbutton(left_frame, text="✅ Bật Cắt Xén (Crop)", variable=use_crop_var, command=lambda: redraw_preview()).pack(anchor=tk.W, pady=(0, 4))

    # Tỷ lệ Crop
    r_row = ttk.Frame(left_frame)
    r_row.pack(fill=tk.X, pady=(2, 6))
    ttk.Label(r_row, text="Tỷ lệ:").pack(side=tk.LEFT)
    ratio_cb = ttk.Combobox(r_row, textvariable=ratio_var, values=["Tự do", "9:16", "16:9", "1:1", "4:5", "3:4"], state="readonly", width=12)
    ratio_cb.pack(side=tk.LEFT, padx=4)

    # Crop Coords
    c_box = ttk.LabelFrame(left_frame, text=" Vùng Crop (Hệ tọa độ gốc) ", padding=6)
    c_box.pack(fill=tk.X, pady=(0, 6))
    
    grid_c = ttk.Frame(c_box)
    grid_c.pack(fill=tk.X)
    ttk.Label(grid_c, text="X:").grid(row=0, column=0, sticky=tk.W)
    sp_cx = ttk.Spinbox(grid_c, from_=0, to=orig_w, increment=10, textvariable=crop_x_var, width=6)
    sp_cx.grid(row=0, column=1, padx=2, pady=2)
    ttk.Label(grid_c, text="Y:").grid(row=0, column=2, sticky=tk.W, padx=(4, 0))
    sp_cy = ttk.Spinbox(grid_c, from_=0, to=orig_h, increment=10, textvariable=crop_y_var, width=6)
    sp_cy.grid(row=0, column=3, padx=2, pady=2)

    ttk.Label(grid_c, text="W:").grid(row=1, column=0, sticky=tk.W)
    sp_cw = ttk.Spinbox(grid_c, from_=20, to=orig_w, increment=10, textvariable=crop_w_var, width=6)
    sp_cw.grid(row=1, column=1, padx=2, pady=2)
    ttk.Label(grid_c, text="H:").grid(row=1, column=2, sticky=tk.W, padx=(4, 0))
    sp_ch = ttk.Spinbox(grid_c, from_=20, to=orig_h, increment=10, textvariable=crop_h_var, width=6)
    sp_ch.grid(row=1, column=3, padx=2, pady=2)

    # Zoom & Scale Controls
    geo_box = ttk.LabelFrame(left_frame, text=" Phóng to & Co Giãn (9:16) ", padding=6)
    geo_box.pack(fill=tk.X, pady=(0, 6))

    ttk.Label(geo_box, text="Phóng to (Zoom-in %):").pack(anchor=tk.W)
    row_z = ttk.Frame(geo_box)
    row_z.pack(fill=tk.X, pady=(1, 4))
    scale_zoom = ttk.Scale(row_z, from_=100.0, to=250.0, variable=zoom_in_var, orient=tk.HORIZONTAL)
    scale_zoom.pack(side=tk.LEFT, fill=tk.X, expand=True)
    spin_zoom = ttk.Spinbox(row_z, from_=100.0, to=250.0, increment=1.0, textvariable=zoom_in_var, width=6)
    spin_zoom.pack(side=tk.LEFT, padx=(4, 0))

    ttk.Label(geo_box, text="Co giãn ngang (Scale X %):").pack(anchor=tk.W)
    row_sx = ttk.Frame(geo_box)
    row_sx.pack(fill=tk.X, pady=(1, 4))
    scale_sx = ttk.Scale(row_sx, from_=80.0, to=150.0, variable=scale_x_var, orient=tk.HORIZONTAL)
    scale_sx.pack(side=tk.LEFT, fill=tk.X, expand=True)
    spin_sx = ttk.Spinbox(row_sx, from_=80.0, to=150.0, increment=1.0, textvariable=scale_x_var, width=6)
    spin_sx.pack(side=tk.LEFT, padx=(4, 0))

    ttk.Label(geo_box, text="Co giãn dọc (Scale Y %):").pack(anchor=tk.W)
    row_sy = ttk.Frame(geo_box)
    row_sy.pack(fill=tk.X, pady=(1, 4))
    scale_sy = ttk.Scale(row_sy, from_=80.0, to=150.0, variable=scale_y_var, orient=tk.HORIZONTAL)
    scale_sy.pack(side=tk.LEFT, fill=tk.X, expand=True)
    spin_sy = ttk.Spinbox(row_sy, from_=80.0, to=150.0, increment=1.0, textvariable=scale_y_var, width=6)
    spin_sy.pack(side=tk.LEFT, padx=(4, 0))

    # Background Mode
    ttk.Checkbutton(left_frame, text="🌌 Bật nền mờ 9:16 (Fast Boxblur)", variable=blur_bg_var, command=lambda: redraw_preview()).pack(anchor=tk.W, pady=(2, 6))

    # Blur Mask Group
    bm_group = ttk.LabelFrame(left_frame, text=" 🌫️ Làm mờ Vùng Chọn (Che Sub/Logo) ", padding=6)
    bm_group.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(bm_group, text="Bật che mờ Sub cũ / Watermark", variable=use_blur_mask_var, command=lambda: redraw_preview()).pack(anchor=tk.W, pady=(0, 2))
    row_bm_shape = ttk.Frame(bm_group)
    row_bm_shape.pack(fill=tk.X, pady=(1, 4))
    ttk.Label(row_bm_shape, text="Hình dạng:").pack(side=tk.LEFT)
    cb_shape = ttk.Combobox(row_bm_shape, textvariable=blur_shape_var, values=["Hình chữ nhật", "Hình tròn"], state="readonly", width=14)
    cb_shape.pack(side=tk.LEFT, padx=4)

    grid_bm = ttk.Frame(bm_group)
    grid_bm.pack(fill=tk.X)
    ttk.Label(grid_bm, text="X:").grid(row=0, column=0, sticky=tk.W)
    sp_bx = ttk.Spinbox(grid_bm, from_=0, to=1080, increment=10, textvariable=blur_x_var, width=6)
    sp_bx.grid(row=0, column=1, padx=2, pady=2)
    ttk.Label(grid_bm, text="Y:").grid(row=0, column=2, sticky=tk.W, padx=(4, 0))
    sp_by = ttk.Spinbox(grid_bm, from_=0, to=1920, increment=10, textvariable=blur_y_var, width=6)
    sp_by.grid(row=0, column=3, padx=2, pady=2)

    ttk.Label(grid_bm, text="W:").grid(row=1, column=0, sticky=tk.W)
    sp_bw = ttk.Spinbox(grid_bm, from_=20, to=1080, increment=10, textvariable=blur_w_var, width=6)
    sp_bw.grid(row=1, column=1, padx=2, pady=2)
    ttk.Label(grid_bm, text="H:").grid(row=1, column=2, sticky=tk.W, padx=(4, 0))
    sp_bh = ttk.Spinbox(grid_bm, from_=20, to=1920, increment=10, textvariable=blur_h_var, width=6)
    sp_bh.grid(row=1, column=3, padx=2, pady=2)

    # Action Buttons (Bottom Left)
    btn_box = ttk.Frame(left_frame)
    btn_box.pack(fill=tk.X, pady=(10, 0))

    # --- RIGHT PANEL (CANVAS LIVE 9:16 WYSIWYG 1:1) ---
    preview_canvas = tk.Canvas(right_frame, bg="#111827", highlightthickness=0)
    preview_canvas.pack(fill=tk.BOTH, expand=True)

    current_preview_img = None
    photo_cache = None

    def on_ratio_change(event=None):
        r = ratio_var.get()
        if r == "9:16":
            new_h = orig_h
            new_w = int(orig_h * 9 / 16)
            crop_w_var.set(min(orig_w, new_w))
            crop_h_var.set(new_h)
        elif r == "16:9":
            new_w = orig_w
            new_h = int(orig_w * 9 / 16)
            crop_w_var.set(new_w)
            crop_h_var.set(min(orig_h, new_h))
        elif r == "1:1":
            side = min(orig_w, orig_h)
            crop_w_var.set(side)
            crop_h_var.set(side)
        elif r == "4:5":
            new_h = orig_h
            new_w = int(orig_h * 4 / 5)
            crop_w_var.set(min(orig_w, new_w))
            crop_h_var.set(new_h)
        elif r == "3:4":
            new_h = orig_h
            new_w = int(orig_h * 3 / 4)
            crop_w_var.set(min(orig_w, new_w))
            crop_h_var.set(new_h)
        redraw_preview()

    ratio_cb.bind("<<ComboboxSelected>>", on_ratio_change)
    cb_shape.bind("<<ComboboxSelected>>", lambda e: redraw_preview())

    def redraw_preview():
        nonlocal current_preview_img, photo_cache
        c_w = preview_canvas.winfo_width()
        c_h = preview_canvas.winfo_height()
        if c_w < 50 or c_h < 50:
            c_w, c_h = 420, 740

        # Thuật toán co giãn hiển thị chuẩn WYSIWYG 1:1
        scale = min((c_w - 20) / 1080.0, (c_h - 20) / 1920.0)
        offset_x = (c_w - 1080.0 * scale) / 2.0
        offset_y = (c_h - 1920.0 * scale) / 2.0

        cx = crop_x_var.get() if use_crop_var.get() else 0
        cy = crop_y_var.get() if use_crop_var.get() else 0
        cw = crop_w_var.get() if use_crop_var.get() else orig_w
        ch = crop_h_var.get() if use_crop_var.get() else orig_h

        z_pct = zoom_in_var.get() / 100.0
        s_x = scale_x_var.get() / 100.0
        s_y = scale_y_var.get() / 100.0
        b_bg = blur_bg_var.get()

        bm_dict = {
            "enabled": use_blur_mask_var.get(),
            "shape": "circle" if blur_shape_var.get() == "Hình tròn" else "rectangle",
            "x": blur_x_var.get(),
            "y": blur_y_var.get(),
            "w": blur_w_var.get(),
            "h": blur_h_var.get()
        }

        # Dựng ảnh 1080x1920
        rendered_1080 = render_layout_image(
            source_img_path=frame_path,
            crop_x=cx, crop_y=cy, crop_w=cw, crop_h=ch,
            zoom_pct=z_pct, scale_x=s_x, scale_y=s_y,
            blur_bg=b_bg,
            blur_mask=bm_dict,
            output_path=LAYOUT_BASE_PATH
        )

        # Scale vừa khít widget Canvas
        disp_w = int(1080 * scale)
        disp_h = int(1920 * scale)
        preview_disp = rendered_1080.resize((disp_w, disp_h), Image.BILINEAR)

        photo_cache = ImageTk.PhotoImage(preview_disp)
        preview_canvas.delete("all")
        preview_canvas.create_image(int(offset_x), int(offset_y), anchor="nw", image=photo_cache)

        # Vẽ viền Canvas 9:16
        preview_canvas.create_rectangle(
            offset_x, offset_y, offset_x + disp_w, offset_y + disp_h,
            outline="#3B82F6", width=2
        )

        # Vẽ đường chỉ dẫn an toàn (Safe Zone)
        preview_canvas.create_line(offset_x, offset_y + 260 * scale, offset_x + disp_w, offset_y + 260 * scale, fill="#EF4444", dash=(4, 4))
        preview_canvas.create_text(offset_x + 10, offset_y + 250 * scale, text="Title Safe Zone", fill="#EF4444", anchor="w", font=("Segoe UI", 8))

        preview_canvas.create_line(offset_x, offset_y + 1600 * scale, offset_x + disp_w, offset_y + 1600 * scale, fill="#10B981", dash=(4, 4))
        preview_canvas.create_text(offset_x + 10, offset_y + 1610 * scale, text="Subtitle Safe Zone", fill="#10B981", anchor="w", font=("Segoe UI", 8))

    for var in [zoom_in_var, scale_x_var, scale_y_var, crop_x_var, crop_y_var, crop_w_var, crop_h_var, blur_x_var, blur_y_var, blur_w_var, blur_h_var]:
        var.trace_add("write", lambda *a: redraw_preview())

    preview_canvas.bind("<Configure>", lambda e: redraw_preview())

    saved_result = {}

    def on_save():
        nonlocal saved_result
        saved_result = {
            "use_crop": bool(use_crop_var.get()),
            "crop_ratio": ratio_var.get(),
            "crop_x": int(crop_x_var.get()),
            "crop_y": int(crop_y_var.get()),
            "crop_w": int(crop_w_var.get()),
            "crop_h": int(crop_h_var.get()),
            "zoom_in": float(zoom_in_var.get()),
            "zoom_percent": float(zoom_in_var.get()),
            "scale_x": float(scale_x_var.get()),
            "scale_w_percent": float(scale_x_var.get()),
            "scale_y": float(scale_y_var.get()),
            "scale_h_percent": float(scale_y_var.get()),
            "blur_bg": bool(blur_bg_var.get()),
            "use_blur_mask": bool(use_blur_mask_var.get()),
            "blur_shape": blur_shape_var.get(),
            "blur_mask_x": int(blur_x_var.get()),
            "blur_mask_y": int(blur_y_var.get()),
            "blur_mask_w": int(blur_w_var.get()),
            "blur_mask_h": int(blur_h_var.get()),
            "blur_mask": {
                "enabled": bool(use_blur_mask_var.get()),
                "shape": "circle" if blur_shape_var.get() == "Hình tròn" else "rectangle",
                "x": int(blur_x_var.get()),
                "y": int(blur_y_var.get()),
                "w": int(blur_w_var.get()),
                "h": int(blur_h_var.get())
            }
        }
        # Lưu phôi layout_base.jpg
        redraw_preview()
        if on_save_callback:
            on_save_callback(saved_result)
        popup.destroy()

    def on_reset():
        use_crop_var.set(False)
        ratio_var.set("Tự do")
        crop_x_var.set(0)
        crop_y_var.set(0)
        crop_w_var.set(orig_w)
        crop_h_var.set(orig_h)
        zoom_in_var.set(168.0)
        scale_x_var.set(100.0)
        scale_y_var.set(125.0)
        blur_bg_var.set(True)
        use_blur_mask_var.set(False)
        redraw_preview()

    btn_save = ttk.Button(btn_box, text="💾 Lưu Bố Cục (1:1)", command=on_save, style="Primary.TButton")
    btn_save.pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

    btn_reset = ttk.Button(btn_box, text="🔄 Mặc Định", command=on_reset, style="Tool.TButton")
    btn_reset.pack(side=tk.LEFT, padx=3)

    btn_cancel = ttk.Button(btn_box, text="❌ Hủy", command=popup.destroy, style="Tool.TButton")
    btn_cancel.pack(side=tk.LEFT, padx=3)

    popup.after(100, redraw_preview)
    popup.wait_window()
    return saved_result
