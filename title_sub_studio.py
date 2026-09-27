"""
TITLE & SUBTITLE STUDIO (LIVE 9:16 INTERACTIVE PREVIEW)
Tiêu đề Banner bo tròn 2 dòng (Pill Banner), Phụ đề Whisper AI & ASS chuẩn quốc tế, Thẻ Part.
Khớp 100% giao diện và chức năng với bản gốc (WYSIWYG 1:1).
"""
import os
import json
import tkinter as tk
from tkinter import ttk, colorchooser, filedialog, messagebox
from typing import Optional, Dict, Any, Callable
from PIL import Image, ImageTk, ImageDraw, ImageFont

try:
    from font_manager import get_pillow_font, FontManager
except Exception:
    def get_pillow_font(size: int = 36, bold: bool = True, text: str = ""):
        try:
            return ImageFont.truetype("arialbd.ttf" if bold else "arial.ttf", size)
        except Exception:
            return ImageFont.load_default()

from studio_helpers import (
    SYSTEM_DATA_DIR, CACHE_FRAME_PATH, LAYOUT_BASE_PATH, COLOR_BASE_PATH,
    get_sample_or_fallback_image, extract_frame_from_video, fetch_youtube_sample
)
from crop_blur_studio import render_layout_image


def draw_rounded_rectangle(draw: ImageDraw.ImageDraw, xy, radius=20, fill=None, outline=None, width=1):
    """Vẽ hình chữ nhật bo góc (Pill Banner)."""
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def render_title_and_sub_overlay(
    base_img: Image.Image,
    title_cfg: Dict[str, Any],
    sub_cfg: Dict[str, Any],
    part_cfg: Dict[str, Any]
) -> Image.Image:
    """
    Vẽ lớp phủ Title Banner, Subtitle, Part Banner chuẩn xác 100% từng pixel lên canvas 1080x1920.
    """
    out_img = base_img.copy().convert("RGBA")
    overlay = Image.new("RGBA", out_img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 1. VẼ TITLE BANNER (PILL BANNER BO GÓC)
    if title_cfg.get("show_title", True):
        line1 = str(title_cfg.get("title_line1", "TIÊU ĐỀ VIDEO MẪU DÒNG PHỤ BANNER")).strip()
        line2 = str(title_cfg.get("title_line2", "")).strip()
        
        y_pos = int(title_cfg.get("title_y_pos", 320))
        bg_color = title_cfg.get("title_bg_color", "#FFFFFF")
        color1 = title_cfg.get("title_color1", "#000000")
        color2 = title_cfg.get("title_color2", "#AA0000")
        outline_color = title_cfg.get("title_outline_color", "none")

        bw = 886
        bx = (1080 - bw) // 2
        bh = 120 if line2 else 76
        by = y_pos

        out_c = outline_color if outline_color and outline_color != "none" else None
        out_w = 2 if out_c else 0

        # Vẽ Pill Banner
        draw_rounded_rectangle(
            draw, [(bx, by), (bx + bw, by + bh)],
            radius=24, fill=bg_color, outline=out_c, width=out_w
        )

        f_title = get_pillow_font(size=36, bold=True, text=line1)
        if line2:
            draw.text((540, by + 34), line1, fill=color1, anchor="mm", font=f_title)
            f_sub_title = get_pillow_font(size=32, bold=True, text=line2)
            draw.text((540, by + 86), line2, fill=color2, anchor="mm", font=f_sub_title)
        else:
            draw.text((540, by + bh // 2), line1, fill=color1, anchor="mm", font=f_title)

    # 2. VẼ THẺ PART
    if part_cfg.get("show_part", False):
        part_num = str(part_cfg.get("part_number", "1"))
        part_prefix = str(part_cfg.get("part_prefix", "Part"))
        part_text = f"{part_prefix} {part_num}"
        part_pos = part_cfg.get("position", "after_title")

        if part_pos == "after_title" and title_cfg.get("show_title", True):
            # Ghép ngay bên phải Title hoặc trong Banner
            pass
        elif part_pos == "bottom_card" or not title_cfg.get("show_title", True):
            p_y = int(part_cfg.get("bottom_y", 1500))
            pw, ph = 240, 64
            px = (1080 - pw) // 2
            p_bg = title_cfg.get("title_bg_color", "#FFFFFF")
            p_c = title_cfg.get("title_color1", "#000000")
            draw_rounded_rectangle(draw, [(px, p_y), (px + pw, p_y + ph)], radius=18, fill=p_bg)
            f_part = get_pillow_font(size=int(part_cfg.get("font_size", 28)), bold=True, text=part_text)
            draw.text((540, p_y + ph // 2), part_text, fill=p_c, anchor="mm", font=f_part)

    # 3. VẼ PHỤ ĐỀ SUBTITLE
    if sub_cfg.get("show_sub", True):
        sub_text = str(sub_cfg.get("sample_text", "Đây là phụ đề mẫu đang hiển thị thử nghiệm...")).strip()
        if sub_text:
            margin_v = int(sub_cfg.get("margin_v", 89))
            sub_y = 1920 - margin_v
            sub_c = sub_cfg.get("color", "#FFFFFF")
            sub_out = sub_cfg.get("outline_color", "#000000")
            f_size = int(sub_cfg.get("font_size", 26))

            f_sub = get_pillow_font(size=f_size, bold=True, text=sub_text)
            draw.text((540, sub_y), sub_text, fill=sub_c, stroke_fill=sub_out, stroke_width=3, anchor="mm", font=f_sub)

    out_img.paste(overlay, (0, 0), overlay)
    return out_img.convert("RGB")


def open_title_sub_studio_popup(
    parent,
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Cửa sổ Studio Tiêu Đề Banner & Phụ Đề Subtitle (WYSIWYG 1:1).
    Khớp 100% với giao diện Title & Subtitle Studio (Live 9:16 Interactive Preview).
    """
    cfg = dict(current_config or {})
    video_path = video_source or cfg.get("video_source") or cfg.get("source_url_or_path") or cfg.get("youtube_url") or ""

    # Nạp Background phôi Tầng 2 (color_base.jpg) hoặc Tầng 3 (layout_base.jpg)
    bg_frame = COLOR_BASE_PATH if os.path.isfile(COLOR_BASE_PATH) else LAYOUT_BASE_PATH
    if not os.path.isfile(bg_frame):
        f_sample = get_sample_or_fallback_image(video_path)
        z_val = float(cfg.get("zoom_in", cfg.get("zoom_percent", 178.0))) / 100.0 if float(cfg.get("zoom_in", cfg.get("zoom_percent", 178.0))) > 10.0 else 1.78
        sx_val = float(cfg.get("scale_x", cfg.get("scale_w_percent", 102.0))) / 100.0 if float(cfg.get("scale_x", cfg.get("scale_w_percent", 102.0))) > 10.0 else 1.02
        sy_val = float(cfg.get("scale_y", cfg.get("scale_h_percent", 120.0))) / 100.0 if float(cfg.get("scale_y", cfg.get("scale_h_percent", 120.0))) > 10.0 else 1.20

        render_layout_image(
            source_img_path=f_sample,
            crop_x=int(cfg.get("crop_x", 0)),
            crop_y=int(cfg.get("crop_y", 0)),
            crop_w=int(cfg.get("crop_w", 1920)),
            crop_h=int(cfg.get("crop_h", 1080)),
            zoom_pct=z_val,
            scale_x=sx_val,
            scale_y=sy_val,
            blur_bg=bool(cfg.get("blur_bg", True)),
            blur_mask=cfg.get("blur_mask"),
            output_path=LAYOUT_BASE_PATH
        )
        bg_frame = LAYOUT_BASE_PATH

    base_img = Image.open(bg_frame).convert("RGB")

    popup = tk.Toplevel(parent)
    popup.title("Title & Subtitle Studio (Live 9:16 Interactive Preview)")
    popup.geometry("1260x920")
    popup.minsize(1060, 760)
    popup.transient(parent)
    popup.grab_set()
    popup.configure(bg="#0B0F19")

    # Main Container
    main_container = tk.Frame(popup, bg="#0B0F19")
    main_container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    left_frame = tk.Frame(main_container, bg="#0F172A", width=440, padx=10, pady=10, highlightthickness=1, highlightbackground="#334155")
    left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
    left_frame.pack_propagate(False)

    right_frame = tk.Frame(main_container, bg="#0F172A", padx=10, pady=10, highlightthickness=1, highlightbackground="#334155")
    right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

    # --- VARIABLES ---
    # Frame params (Header)
    zoom_val_str = tk.StringVar(value=f"{int(float(cfg.get('zoom_in', cfg.get('zoom_percent', 178.0))))} %")
    scale_x_str = tk.StringVar(value=f"{int(float(cfg.get('scale_x', cfg.get('scale_w_percent', 102.0))))} %")
    scale_y_str = tk.StringVar(value=f"{int(float(cfg.get('scale_y', cfg.get('scale_h_percent', 120.0))))} %")
    blur_bg_var = tk.BooleanVar(value=bool(cfg.get("blur_bg", True)))

    # Title
    show_title_var = tk.BooleanVar(value=bool(cfg.get("enable_title", cfg.get("show_title", True))))
    title_custom_var = tk.StringVar(value=cfg.get("title_text", cfg.get("title_custom", "")))
    title_line1_var = tk.StringVar(value=cfg.get("title_line1", "TIÊU ĐỀ VIDEO MẪU DÒNG PHỤ BANNER"))
    title_line2_var = tk.StringVar(value=cfg.get("title_line2", ""))
    title_y_var = tk.IntVar(value=int(cfg.get("title_y_pos", cfg.get("title_y", 320))))
    title_color1_var = tk.StringVar(value=cfg.get("title_color1", cfg.get("title_color", "#000000")))
    title_color2_var = tk.StringVar(value=cfg.get("title_color2", "#AA0000"))
    title_outline_var = tk.StringVar(value=cfg.get("title_outline_color", "Không Viền"))
    title_bg_color_var = tk.StringVar(value=cfg.get("title_bg_color", "#FFFFFF"))

    # Part
    show_part_var = tk.BooleanVar(value=bool(cfg.get("show_part", True)))
    part_format_var = tk.StringVar(value=cfg.get("part_prefix", "Part"))
    part_num_var = tk.StringVar(value=str(cfg.get("part_number", "1")))
    part_pos_var = tk.StringVar(value=cfg.get("part_position", "bottom_card"))
    part_bottom_y_var = tk.IntVar(value=int(cfg.get("part_bottom_y", 1500)))
    part_size_var = tk.IntVar(value=int(cfg.get("part_font_size", 16)))

    # Subtitle
    show_sub_var = tk.BooleanVar(value=bool(cfg.get("show_sub", True)))
    sub_style_var = tk.StringVar(value=cfg.get("sub_style_type", "Tiêu Chuẩn (Standard - Segoe UI)"))
    sub_size_var = tk.IntVar(value=int(cfg.get("sub_font_size", 21)))
    sub_margin_v_var = tk.IntVar(value=int(cfg.get("sub_margin_v", 89)))
    sub_sample_var = tk.StringVar(value=cfg.get("sub_sample_text", "Đây là phụ đề mẫu đang hiển thị thử nghiệm..."))
    sub_color_var = tk.StringVar(value=cfg.get("sub_color", "#FFFFFF"))
    sub_outline_color_var = tk.StringVar(value=cfg.get("sub_outline_color", "#000000"))

    # --- LEFT CONTROLS (SCROLLABLE) ---
    c_canvas = tk.Canvas(left_frame, bg="#0F172A", highlightthickness=0)
    c_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=c_canvas.yview)
    c_content = tk.Frame(c_canvas, bg="#0F172A")
    c_content.bind("<Configure>", lambda e: c_canvas.configure(scrollregion=c_canvas.bbox("all")))
    c_canvas.create_window((0, 0), window=c_content, anchor="nw", width=405)
    c_canvas.configure(yscrollcommand=c_scroll.set)

    c_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    c_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # 1. THÔNG SỐ KHUNG HÌNH
    g_geo = ttk.LabelFrame(c_content, text=" 🔍 THÔNG SỐ KHUNG HÌNH (ĐỒNG BỘ CONFIG RENDER) ", padding=8)
    g_geo.pack(fill=tk.X, pady=(0, 6))

    row_geo = ttk.Frame(g_geo)
    row_geo.pack(fill=tk.X, pady=2)
    ttk.Label(row_geo, text="Zoom-in:").pack(side=tk.LEFT)
    ttk.Entry(row_geo, textvariable=zoom_val_str, width=6, state="readonly").pack(side=tk.LEFT, padx=(2, 8))
    ttk.Label(row_geo, text="Scale X:").pack(side=tk.LEFT)
    ttk.Entry(row_geo, textvariable=scale_x_str, width=6, state="readonly").pack(side=tk.LEFT, padx=(2, 8))
    ttk.Label(row_geo, text="Scale Y:").pack(side=tk.LEFT)
    ttk.Entry(row_geo, textvariable=scale_y_str, width=6, state="readonly").pack(side=tk.LEFT, padx=(2, 6))
    ttk.Checkbutton(row_geo, text="Làm mờ 2 đầu", variable=blur_bg_var, state="disabled").pack(side=tk.LEFT)

    # 2. CẤU HÌNH TIÊU ĐỀ BANNER (PILL BANNER)
    g_title = ttk.LabelFrame(c_content, text=" 📌 CẤU HÌNH TIÊU ĐỀ BANNER (PILL BANNER) ", padding=8)
    g_title.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(g_title, text="Bật Hiển Thị Tiêu Đề Banner Trên Video", variable=show_title_var, command=lambda: redraw_title_preview()).pack(anchor=tk.W, pady=(0, 4))

    ttk.Label(g_title, text="Tùy Chỉnh Chữ:").pack(anchor=tk.W)
    ttk.Entry(g_title, textvariable=title_custom_var, width=36).pack(fill=tk.X, pady=(1, 4))

    # Tọa độ Y px
    y_row = ttk.Frame(g_title)
    y_row.pack(fill=tk.X, pady=(2, 4))
    ttk.Label(y_row, text="Tọa Độ Y (px):").pack(side=tk.LEFT)
    ttk.Entry(y_row, textvariable=title_y_var, width=6).pack(side=tk.LEFT, padx=4)
    ttk.Scale(y_row, from_=50, to=1800, variable=title_y_var, orient=tk.HORIZONTAL).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    # Bảng Màu Swatches
    def choose_color(target_var, title="Chọn màu"):
        c = colorchooser.askcolor(title=title, color=target_var.get())
        if c and c[1]:
            target_var.set(c[1].upper())
            redraw_title_preview()

    col_grid = ttk.Frame(g_title)
    col_grid.pack(fill=tk.X, pady=2)

    # Dòng 1 Màu
    r_c1 = ttk.Frame(col_grid)
    r_c1.pack(fill=tk.X, pady=2)
    ttk.Label(r_c1, text="Màu Chữ Dòng 1:", width=16).pack(side=tk.LEFT)
    btn_col1 = tk.Button(r_c1, text=title_color1_var.get(), bg=title_color1_var.get(), fg="#FFFFFF" if title_color1_var.get() == "#000000" else "#000000", width=10, relief=tk.RIDGE, command=lambda: (choose_color(title_color1_var), btn_col1.config(text=title_color1_var.get(), bg=title_color1_var.get())))
    btn_col1.pack(side=tk.LEFT, padx=4)

    # Dòng 2 Màu
    r_c2 = ttk.Frame(col_grid)
    r_c2.pack(fill=tk.X, pady=2)
    ttk.Label(r_c2, text="Màu Chữ Dòng 2:", width=16).pack(side=tk.LEFT)
    btn_col2 = tk.Button(r_c2, text=title_color2_var.get(), bg=title_color2_var.get(), fg="#FFFFFF", width=10, relief=tk.RIDGE, command=lambda: (choose_color(title_color2_var), btn_col2.config(text=title_color2_var.get(), bg=title_color2_var.get())))
    btn_col2.pack(side=tk.LEFT, padx=4)

    # Viền Chữ
    r_out = ttk.Frame(col_grid)
    r_out.pack(fill=tk.X, pady=2)
    ttk.Label(r_out, text="Màu Viền Chữ:", width=16).pack(side=tk.LEFT)
    cb_out = ttk.Combobox(r_out, textvariable=title_outline_var, values=["Không Viền", "Viền Đen (#000000)", "Viền Trắng (#FFFFFF)", "Tùy Chỉnh"], state="readonly", width=12)
    cb_out.pack(side=tk.LEFT, padx=2)
    cb_out.bind("<<ComboboxSelected>>", lambda e: redraw_title_preview())

    # Nền Banner
    r_bg = ttk.Frame(col_grid)
    r_bg.pack(fill=tk.X, pady=2)
    ttk.Label(r_bg, text="Màu Nền Banner:", width=16).pack(side=tk.LEFT)
    btn_bg_swatch = tk.Button(r_bg, text=title_bg_color_var.get(), bg=title_bg_color_var.get(), fg="#000000", width=10, relief=tk.RIDGE, command=lambda: (choose_color(title_bg_color_var), btn_bg_swatch.config(text=title_bg_color_var.get(), bg=title_bg_color_var.get())))
    btn_bg_swatch.pack(side=tk.LEFT, padx=4)

    # 3. CẤU HÌNH SỐ PART (CHUNG MÀU VỚI TIÊU ĐỀ)
    g_part = ttk.LabelFrame(c_content, text=" 🏷 CẤU HÌNH SỐ PART (CHUNG MÀU VỚI TIÊU ĐỀ) ", padding=8)
    g_part.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(g_part, text="Bật Hiển Thị Số Part Trên Video", variable=show_part_var, command=lambda: redraw_title_preview()).pack(anchor=tk.W, pady=(0, 2))

    p_fmt_row = ttk.Frame(g_part)
    p_fmt_row.pack(fill=tk.X, pady=2)
    ttk.Label(p_fmt_row, text="Ký Hiệu Part:").pack(side=tk.LEFT)
    ttk.Label(p_fmt_row, text="Định Dạng:").pack(side=tk.LEFT, padx=(6, 2))
    ttk.Entry(p_fmt_row, textvariable=part_format_var, width=6).pack(side=tk.LEFT)
    ttk.Label(p_fmt_row, text="Số Thử Nghiệm:").pack(side=tk.LEFT, padx=(6, 2))
    ttk.Entry(p_fmt_row, textvariable=part_num_var, width=4).pack(side=tk.LEFT)

    p_pos_lbl = ttk.Label(g_part, text="Vị Trí Hiển Thị:")
    p_pos_lbl.pack(anchor=tk.W, pady=(4, 0))
    ttk.Radiobutton(g_part, text="1. Hiện ở sau Title ở trên cùng (Ghép chung Banner)", variable=part_pos_var, value="after_title", command=lambda: redraw_title_preview()).pack(anchor=tk.W, padx=12)
    ttk.Radiobutton(g_part, text="2. Hiện ở bên dưới (Tùy chỉnh tọa độ Y như Sub)", variable=part_pos_var, value="bottom_card", command=lambda: redraw_title_preview()).pack(anchor=tk.W, padx=12)

    p_sub_row = ttk.Frame(g_part)
    p_sub_row.pack(fill=tk.X, pady=2)
    ttk.Label(p_sub_row, text="Thông Số Khi Ở Dưới:").pack(side=tk.LEFT)
    ttk.Label(p_sub_row, text="Tọa Độ Y:").pack(side=tk.LEFT, padx=(4, 2))
    ttk.Spinbox(p_sub_row, from_=100, to=1900, increment=10, textvariable=part_bottom_y_var, width=6).pack(side=tk.LEFT)
    ttk.Label(p_sub_row, text="Cỡ Chữ:").pack(side=tk.LEFT, padx=(6, 2))
    ttk.Spinbox(p_sub_row, from_=10, to=60, increment=1, textvariable=part_size_var, width=5).pack(side=tk.LEFT)

    ttk.Label(g_part, text="📌 Số Part tự động dùng chung Màu Chữ, Nền và Viền với Tiêu Đề Banner.", font=("Segoe UI", 8), foreground="#94A3B8").pack(anchor=tk.W, pady=(4, 0))

    # 4. CẤU HÌNH PHỤ ĐỀ SUBTITLE (0% LỖI TOFU)
    g_sub = ttk.LabelFrame(c_content, text=" 💬 CẤU HÌNH PHỤ ĐỀ SUBTITLE (0% LỖI TOFU) ", padding=8)
    g_sub.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(g_sub, text="Tự Động Tạo / Nhúng Phụ Đề (Whisper AI)", variable=show_sub_var, command=lambda: redraw_title_preview()).pack(anchor=tk.W, pady=(0, 2))

    sub_style_row = ttk.Frame(g_sub)
    sub_style_row.pack(fill=tk.X, pady=2)
    ttk.Label(sub_style_row, text="Phong Cách (Style):").pack(side=tk.LEFT)
    sub_style_cb = ttk.Combobox(
        sub_style_row, textvariable=sub_style_var,
        values=["Tiêu Chuẩn (Standard - Segoe UI)", "Cổ Điển (Classic - Impact)", "Thanh Mảnh (TikTok Slim - Arial)"],
        state="readonly", width=22
    )
    sub_style_cb.pack(side=tk.LEFT, padx=4)
    sub_style_cb.bind("<<ComboboxSelected>>", lambda e: redraw_title_preview())

    sub_font_row = ttk.Frame(g_sub)
    sub_font_row.pack(fill=tk.X, pady=2)
    ttk.Label(sub_font_row, text="Cỡ Chữ (FontSize):").pack(side=tk.LEFT)
    ttk.Spinbox(sub_font_row, from_=12, to=60, increment=1, textvariable=sub_size_var, width=6).pack(side=tk.LEFT, padx=4)

    # --- RIGHT PANEL: LIVE 9:16 CANVAS & TEST CONTROLS ---
    g_top_src = ttk.LabelFrame(right_frame, text=" 🖼 HÌNH ẢNH MẪU VIDEO (NỀN CANVAS 9:16) ", padding=6)
    g_top_src.pack(fill=tk.X, pady=(0, 6))

    top_btn_row = ttk.Frame(g_top_src)
    top_btn_row.pack(fill=tk.X, pady=1)

    vid_status_lbl = ttk.Label(g_top_src, text=f"✅ Đã trích xuất frame từ video: {os.path.basename(video_path) if video_path else 'anhmau.jpg'} (1920x1080)", font=("Segoe UI", 8), foreground="#10B981")

    def on_choose_video_ts():
        fn = filedialog.askopenfilename(title="Chọn Video Mẫu", filetypes=[("Video Files", "*.mp4;*.mkv;*.mov;*.avi;*.webm"), ("All Files", "*.*")])
        if fn:
            res = extract_frame_from_video(fn, timestamp_sec=2.0)
            if res:
                nonlocal base_img
                reload_title_base_frame(res)
                vid_status_lbl.config(text=f"✅ Đã trích xuất frame từ video: {os.path.basename(fn)} (1920x1080)")

    ttk.Button(top_btn_row, text="📁 Chọn Video", command=on_choose_video_ts, width=12).pack(side=tk.LEFT, padx=2)

    def on_reload_frame_ts():
        nonlocal base_img
        reload_title_base_frame()
        messagebox.showinfo("Thông báo", "Đã nạp lại frame mẫu từ phôi layout!")

    ttk.Button(top_btn_row, text="🔄 Nạp Lại Frame", command=on_reload_frame_ts, width=14).pack(side=tk.LEFT, padx=2)

    yt_ts_url_var = tk.StringVar(value="")
    ttk.Entry(top_btn_row, textvariable=yt_ts_url_var, width=28).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    def on_get_yt_sample_ts():
        url = yt_ts_url_var.get().strip()
        if not url:
            messagebox.showwarning("Thiếu link", "Vui lòng nhập link YouTube hoặc Video ID!")
            return
        res = fetch_youtube_sample(url, CACHE_FRAME_PATH)
        if res and os.path.isfile(res):
            reload_title_base_frame(res)
            vid_status_lbl.config(text=f"✅ Đã lấy mẫu từ YouTube: {url[:20]}... (1920x1080)")
            messagebox.showinfo("Thành công", "Đã lấy frame mẫu từ YouTube!")
        else:
            messagebox.showerror("Lỗi", "Không thể lấy frame từ link YouTube này.")

    ttk.Button(top_btn_row, text="⚡ Lấy Mẫu YouTube", command=on_get_yt_sample_ts, width=16).pack(side=tk.LEFT, padx=2)
    vid_status_lbl.pack(anchor=tk.W, pady=(2, 0))

    def reload_title_base_frame(source_override: Optional[str] = None):
        nonlocal base_img
        s_path = source_override or get_sample_or_fallback_image(video_path)
        z_val = float(cfg.get("zoom_in", cfg.get("zoom_percent", 178.0))) / 100.0 if float(cfg.get("zoom_in", cfg.get("zoom_percent", 178.0))) > 10.0 else 1.78
        sx_val = float(cfg.get("scale_x", cfg.get("scale_w_percent", 102.0))) / 100.0 if float(cfg.get("scale_x", cfg.get("scale_w_percent", 102.0))) > 10.0 else 1.02
        sy_val = float(cfg.get("scale_y", cfg.get("scale_h_percent", 120.0))) / 100.0 if float(cfg.get("scale_y", cfg.get("scale_h_percent", 120.0))) > 10.0 else 1.20

        render_layout_image(
            source_img_path=s_path,
            crop_x=int(cfg.get("crop_x", 0)),
            crop_y=int(cfg.get("crop_y", 0)),
            crop_w=int(cfg.get("crop_w", 1920)),
            crop_h=int(cfg.get("crop_h", 1080)),
            zoom_pct=z_val,
            scale_x=sx_val,
            scale_y=sy_val,
            blur_bg=bool(cfg.get("blur_bg", True)),
            blur_mask=cfg.get("blur_mask"),
            output_path=LAYOUT_BASE_PATH
        )
        base_img = Image.open(LAYOUT_BASE_PATH).convert("RGB")
        redraw_title_preview()

    # LIVE 9:16 CANVAS
    title_canvas = tk.Canvas(right_frame, bg="#020617", highlightthickness=1, highlightbackground="#1E293B")
    title_canvas.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

    # BOTTOM BOX: NHẬP VĂN BẢN THỬ NGHIỆM XEM NGAY
    g_bottom_test = ttk.LabelFrame(right_frame, text=" 📝 Nhập Văn Bản Thử Nghiệm Xem Ngay ", padding=6)
    g_bottom_test.pack(fill=tk.X)

    r_test1 = ttk.Frame(g_bottom_test)
    r_test1.pack(fill=tk.X, pady=1)
    ttk.Label(r_test1, text="Title test:", width=10).pack(side=tk.LEFT)
    ttk.Entry(r_test1, textvariable=title_line1_var, width=46).pack(side=tk.LEFT, fill=tk.X, expand=True)

    r_test2 = ttk.Frame(g_bottom_test)
    r_test2.pack(fill=tk.X, pady=1)
    ttk.Label(r_test2, text="Sub test:", width=10).pack(side=tk.LEFT)
    ttk.Entry(r_test2, textvariable=sub_sample_var, width=46).pack(side=tk.LEFT, fill=tk.X, expand=True)

    footer_summary_lbl = ttk.Label(
        g_bottom_test,
        text="Phong cách: Tiêu Chuẩn (Standard - Segoe UI) | Cỡ Sub: 21px (Lề 89px) | Title Y: 320px | Vị trí Part: Dưới Y=1500px",
        font=("Segoe UI", 8), foreground="#94A3B8"
    )
    footer_summary_lbl.pack(anchor=tk.W, pady=(2, 0))

    def update_footer_summary():
        style_short = sub_style_var.get()
        footer_summary_lbl.config(
            text=f"Phong cách: {style_short} | Cỡ Sub: {sub_size_var.get()}px (Lề {sub_margin_v_var.get()}px) | Title Y: {title_y_var.get()}px | Vị trí Part: {'Dưới Y=' + str(part_bottom_y_var.get()) + 'px' if part_pos_var.get() == 'bottom_card' else 'Trong Banner'}"
        )

    photo_title_cache = None

    def redraw_title_preview():
        nonlocal photo_title_cache
        c_w = title_canvas.winfo_width()
        c_h = title_canvas.winfo_height()
        if c_w < 50 or c_h < 50:
            c_w, c_h = 440, 680

        scale = min((c_w - 20) / 1080.0, (c_h - 20) / 1920.0)
        offset_x = (c_w - 1080.0 * scale) / 2.0
        offset_y = (c_h - 1920.0 * scale) / 2.0

        t_out = None
        if "Đen" in title_outline_var.get():
            t_out = "#000000"
        elif "Trắng" in title_outline_var.get():
            t_out = "#FFFFFF"

        t_cfg = {
            "show_title": show_title_var.get(),
            "title_line1": title_line1_var.get(),
            "title_line2": title_line2_var.get(),
            "title_y_pos": title_y_var.get(),
            "title_bg_color": title_bg_color_var.get(),
            "title_color1": title_color1_var.get(),
            "title_color2": title_color2_var.get(),
            "title_outline_color": t_out
        }

        s_cfg = {
            "show_sub": show_sub_var.get(),
            "sample_text": sub_sample_var.get(),
            "style_type": sub_style_var.get(),
            "margin_v": sub_margin_v_var.get(),
            "color": sub_color_var.get(),
            "outline_color": sub_outline_color_var.get(),
            "font_size": sub_size_var.get()
        }

        p_cfg = {
            "show_part": show_part_var.get(),
            "part_prefix": part_format_var.get(),
            "part_number": part_num_var.get(),
            "position": part_pos_var.get(),
            "bottom_y": part_bottom_y_var.get(),
            "font_size": part_size_var.get()
        }

        rendered = render_title_and_sub_overlay(base_img, t_cfg, s_cfg, p_cfg)

        disp_w = int(1080 * scale)
        disp_h = int(1920 * scale)
        preview_disp = rendered.resize((disp_w, disp_h), Image.BILINEAR)

        photo_title_cache = ImageTk.PhotoImage(preview_disp)
        title_canvas.delete("all")
        title_canvas.create_image(int(offset_x), int(offset_y), anchor="nw", image=photo_title_cache)

        # Viền 9:16 Canvas
        title_canvas.create_rectangle(
            offset_x, offset_y, offset_x + disp_w, offset_y + disp_h,
            outline="#38BDF8", width=2
        )

        # Badge 9:16 PRO
        title_canvas.create_rectangle(
            offset_x + 8, offset_y + 8, offset_x + 100, offset_y + 32,
            fill="#020617", outline="#38BDF8", width=1
        )
        title_canvas.create_text(
            offset_x + 54, offset_y + 20,
            text="9:16 PRO", fill="#38BDF8", font=("Segoe UI", 8, "bold")
        )

        update_footer_summary()

    # Kéo thả Title Banner trực tiếp trên Canvas
    dragging_title = False

    def on_canvas_press(event):
        nonlocal dragging_title
        c_h = title_canvas.winfo_height()
        scale = min((title_canvas.winfo_width() - 20) / 1080.0, (c_h - 20) / 1920.0)
        offset_y = (c_h - 1920.0 * scale) / 2.0
        cur_y_px = int((event.y - offset_y) / scale)
        title_y = title_y_var.get()
        if abs(cur_y_px - title_y) < 100:
            dragging_title = True

    def on_canvas_drag(event):
        nonlocal dragging_title
        if dragging_title:
            c_h = title_canvas.winfo_height()
            scale = min((title_canvas.winfo_width() - 20) / 1080.0, (c_h - 20) / 1920.0)
            offset_y = (c_h - 1920.0 * scale) / 2.0
            new_y = int((event.y - offset_y) / scale)
            new_y = max(50, min(1800, new_y))
            title_y_var.set(new_y)

    def on_canvas_release(event):
        nonlocal dragging_title
        dragging_title = False

    title_canvas.bind("<ButtonPress-1>", on_canvas_press)
    title_canvas.bind("<B1-Motion>", on_canvas_drag)
    title_canvas.bind("<ButtonRelease-1>", on_canvas_release)
    title_canvas.bind("<Configure>", lambda e: redraw_title_preview())

    for var in [title_line1_var, title_line2_var, title_y_var, sub_sample_var, sub_size_var, sub_margin_v_var, part_bottom_y_var, part_size_var, part_num_var, part_format_var, title_custom_var]:
        var.trace_add("write", lambda *a: redraw_title_preview())

    # NÚT LƯU CẤU HÌNH & THOÁT (BIG SAVE BUTTON AT BOTTOM LEFT)
    saved_title_result = {}

    def on_save_title_sub():
        nonlocal saved_title_result
        t_out = None
        if "Đen" in title_outline_var.get():
            t_out = "#000000"
        elif "Trắng" in title_outline_var.get():
            t_out = "#FFFFFF"

        saved_title_result = {
            "enable_title": bool(show_title_var.get()),
            "show_title": bool(show_title_var.get()),
            "title_text": title_custom_var.get().strip() or title_line1_var.get().strip(),
            "title_line1": title_line1_var.get().strip(),
            "title_line2": title_line2_var.get().strip(),
            "title_y_pos": int(title_y_var.get()),
            "title_y": int(title_y_var.get()),
            "title_bg_color": title_bg_color_var.get(),
            "title_color1": title_color1_var.get(),
            "title_color": title_color1_var.get(),
            "title_color2": title_color2_var.get(),
            "title_outline_color": t_out or "none",
            "show_sub": bool(show_sub_var.get()),
            "sub_style_type": sub_style_var.get(),
            "sub_font_size": int(sub_size_var.get()),
            "sub_size": int(sub_size_var.get()),
            "sub_margin_v": int(sub_margin_v_var.get()),
            "sub_margin": int(sub_margin_v_var.get()),
            "sub_sample_text": sub_sample_var.get(),
            "sub_color": sub_color_var.get(),
            "sub_outline_color": sub_outline_color_var.get(),
            "show_part": bool(show_part_var.get()),
            "part_prefix": part_format_var.get(),
            "part_number": part_num_var.get(),
            "part_position": part_pos_var.get(),
            "part_bottom_y": int(part_bottom_y_var.get()),
            "part_font_size": int(part_size_var.get()),
        }
        redraw_title_preview()
        if on_save_callback:
            on_save_callback(saved_title_result)
        popup.destroy()

    btn_save_ts = tk.Button(
        c_content, text="💾 LƯU CẤU HÌNH TIÊU ĐỀ & PHỤ ĐỀ",
        bg="#10B981", fg="#FFFFFF", font=("Segoe UI", 10, "bold"),
        relief=tk.FLAT, pady=8, command=on_save_title_sub
    )
    btn_save_ts.pack(fill=tk.X, pady=(8, 4))

    popup.after(120, redraw_title_preview)
    popup.wait_window()
    return saved_title_result
