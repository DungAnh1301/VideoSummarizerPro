"""
TITLE BANNER, SUBTITLE & PART STUDIO (WYSIWYG 1:1)
Tiêu đề Banner bo tròn 2 dòng (Pill Banner), Phụ đề Whisper AI & ASS chuẩn quốc tế, Thẻ Part.
Nhận đầu vào từ Tầng 2 (system_data/color_base.jpg) và hiển thị tương tác thời gian thực.
"""
import os
import json
import tkinter as tk
from tkinter import ttk, colorchooser, messagebox
from typing import Optional, Dict, Any, Callable
from PIL import Image, ImageTk, ImageDraw, ImageFont

from font_manager import get_pillow_font

SYSTEM_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_data")
os.makedirs(SYSTEM_DATA_DIR, exist_ok=True)
COLOR_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "color_base.jpg")
LAYOUT_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "layout_base.jpg")


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
        line1 = str(title_cfg.get("title_line1", "TIÊU ĐỀ VIDEO VIRAL DÒNG 1")).strip()
        line2 = str(title_cfg.get("title_line2", "NỘI DUNG CUỐN HÚT DÒNG 2")).strip()
        
        y_pos = int(title_cfg.get("title_y_pos", 280))
        bg_color = title_cfg.get("title_bg_color", "#FF2D55")
        color1 = title_cfg.get("title_color1", "#FFFFFF")
        color2 = title_cfg.get("title_color2", "#FFE600")
        outline_color = title_cfg.get("title_outline_color", "none")

        bw = 886
        bx = (1080 - bw) // 2
        bh = 120 if line2 else 72
        by = y_pos

        out_c = outline_color if outline_color and outline_color != "none" else None
        out_w = 2 if out_c else 0

        # Vẽ Pill Banner
        draw_rounded_rectangle(
            draw, [(bx, by), (bx + bw, by + bh)],
            radius=20, fill=bg_color, outline=out_c, width=out_w
        )

        # Vẽ Chữ Dòng 1 & Dòng 2
        font_main = get_pillow_font(size=36, bold=True)
        if line2:
            # Dòng 1
            draw.text((bx + bw // 2, by + 32), line1, fill=color1, anchor="mm", font=font_main)
            # Dòng 2
            font_sub = get_pillow_font(size=32, bold=True)
            draw.text((bx + bw // 2, by + 84), line2, fill=color2, anchor="mm", font=font_sub)
        else:
            # 1 dòng
            draw.text((bx + bw // 2, by + bh // 2), line1, fill=color1, anchor="mm", font=font_main)

    # 2. VẼ PART CARD (NẾU CÓ)
    if part_cfg.get("show_part", False):
        p_mode = part_cfg.get("part_position", "after_title")
        p_num = part_cfg.get("part_number", "1")
        if p_mode == "bottom_card":
            # Card độc lập ở đáy Y=1650
            card_w, card_h = 240, 60
            card_x = (1080 - card_w) // 2
            card_y = 1650
            draw_rounded_rectangle(
                draw, [(card_x, card_y), (card_x + card_w, card_y + card_h)],
                radius=14, fill="#111827", outline="#F59E0B", width=2
            )
            font_p = get_pillow_font(size=28, bold=True)
            draw.text((card_x + card_w // 2, card_y + card_h // 2), f"PART {p_num}", fill="#F59E0B", anchor="mm", font=font_p)

    # 3. VẼ PHỤ ĐỀ SUBTITLE
    if sub_cfg.get("show_sub", True):
        sub_text = str(sub_cfg.get("sample_text", "Phụ đề mẫu đồng bộ giọng đọc AI cực cuốn...")).strip()
        sub_style = sub_cfg.get("sub_style_type", "tiktok_slim")
        margin_v = int(sub_cfg.get("sub_margin_v", 80))
        sub_color = sub_cfg.get("sub_color", "#FFFFFF")
        sub_outline = sub_cfg.get("sub_outline_color", "#000000")

        if sub_style == "tiktok_slim":
            f_size = 32
            f_bold = False
            out_stroke = 2
        elif sub_style == "classic":
            f_size = 46
            f_bold = True
            out_stroke = 4
        else:  # standard
            f_size = 36
            f_bold = True
            out_stroke = 3

        font_sub_ass = get_pillow_font(size=f_size, bold=f_bold)
        sub_y = 1920 - margin_v - 40
        sub_x = 1080 // 2

        # Stroke viền chữ
        draw.text(
            (sub_x, sub_y), sub_text,
            fill=sub_color, stroke_fill=sub_outline, stroke_width=out_stroke,
            anchor="mm", font=font_sub_ass
        )

    out_img = Image.alpha_composite(out_img, overlay)
    return out_img.convert("RGB")


def open_title_sub_studio_popup(
    parent,
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Cửa sổ Studio Tiêu Đề Banner & Phụ Đề Subtitle (WYSIWYG 1:1).
    """
    cfg = dict(current_config or {})

    # Lấy background từ Tầng 2 (color_base.jpg) hoặc Tầng 3 (layout_base.jpg)
    bg_frame = COLOR_BASE_PATH if os.path.isfile(COLOR_BASE_PATH) else LAYOUT_BASE_PATH
    if not os.path.isfile(bg_frame):
        from crop_blur_studio import extract_sample_frame, render_layout_image
        f_sample = extract_sample_frame(video_source or "")
        render_layout_image(f_sample or "", 0, 0, 1920, 1080, 1.68, 1.0, 1.25, blur_bg=True, output_path=LAYOUT_BASE_PATH)
        bg_frame = LAYOUT_BASE_PATH

    base_img = Image.open(bg_frame).convert("RGB")

    popup = tk.Toplevel(parent)
    popup.title("📝 Chế Độ 1: Title Banner, Subtitle & Part Studio (WYSIWYG 1:1)")
    popup.geometry("1220x880")
    popup.minsize(1020, 720)
    popup.transient(parent)
    popup.grab_set()

    left_frame = ttk.LabelFrame(popup, text=" ⚙ Tùy Chỉnh Tiêu Đề Banner & Phụ Đề Subtitle ", padding=10)
    left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    right_frame = ttk.LabelFrame(popup, text=" 📱 Khung Hình Xem Trước 9:16 (WYSIWYG 1:1) ", padding=10)
    right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    # --- VARIABLES ---
    # Title
    show_title_var = tk.BooleanVar(value=bool(cfg.get("enable_title", cfg.get("show_title", True))))
    title_line1_var = tk.StringVar(value=cfg.get("title_line1", cfg.get("title_text", "TOP BÍ MẬT BẬT MÍ")))
    title_line2_var = tk.StringVar(value=cfg.get("title_line2", "BẠN CHƯA TỪNG BIẾT"))
    title_y_var = tk.IntVar(value=int(cfg.get("title_y_pos", cfg.get("title_y", 280))))
    title_bg_color_var = tk.StringVar(value=cfg.get("title_bg_color", "#FF2D55"))
    title_color1_var = tk.StringVar(value=cfg.get("title_color1", cfg.get("title_color", "#FFFFFF")))
    title_color2_var = tk.StringVar(value=cfg.get("title_color2", "#FFE600"))
    title_outline_var = tk.StringVar(value=cfg.get("title_outline_color", "none"))

    # Subtitle
    show_sub_var = tk.BooleanVar(value=bool(cfg.get("show_sub", True)))
    sub_sample_var = tk.StringVar(value=cfg.get("sub_sample_text", "Phụ đề mẫu đồng bộ giọng đọc AI cực cuốn..."))
    sub_style_var = tk.StringVar(value=cfg.get("sub_style_type", "tiktok_slim"))
    sub_margin_v_var = tk.IntVar(value=int(cfg.get("sub_margin_v", cfg.get("sub_margin", 80))))
    sub_color_var = tk.StringVar(value=cfg.get("sub_color", "#FFFFFF"))
    sub_outline_color_var = tk.StringVar(value=cfg.get("sub_outline_color", "#000000"))

    # Part
    show_part_var = tk.BooleanVar(value=bool(cfg.get("show_part", False)))
    part_pos_var = tk.StringVar(value=cfg.get("part_position", "after_title"))
    part_num_var = tk.StringVar(value=str(cfg.get("part_number", "1")))

    # --- LEFT CONTROLS (SCROLLABLE) ---
    c_canvas = tk.Canvas(left_frame, highlightthickness=0)
    c_scroll = ttk.Scrollbar(left_frame, orient="vertical", command=c_canvas.yview)
    c_frame = ttk.Frame(c_canvas)
    c_frame.bind("<Configure>", lambda e: c_canvas.configure(scrollregion=c_canvas.bbox("all")))
    c_canvas.create_window((0, 0), window=c_frame, anchor="nw")
    c_canvas.configure(yscrollcommand=c_scroll.set)

    c_scroll.pack(side=tk.RIGHT, fill=tk.Y)
    c_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    # 1. GROUP TITLE BANNER
    tg = ttk.LabelFrame(c_frame, text=" 📌 Tiêu Đề Banner Bo Góc (Pill Banner 2 Dòng) ", padding=8)
    tg.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(tg, text="Hiển thị Tiêu đề (Title Banner)", variable=show_title_var).pack(anchor=tk.W, pady=(0, 4))

    ttk.Label(tg, text="Dòng 1:").pack(anchor=tk.W)
    ttk.Entry(tg, textvariable=title_line1_var, width=36).pack(fill=tk.X, pady=(1, 4))

    ttk.Label(tg, text="Dòng 2 (để trống nếu làm 1 dòng):").pack(anchor=tk.W)
    ttk.Entry(tg, textvariable=title_line2_var, width=36).pack(fill=tk.X, pady=(1, 4))

    # Tọa độ Y
    y_row = ttk.Frame(tg)
    y_row.pack(fill=tk.X, pady=(2, 4))
    ttk.Label(y_row, text="Tọa độ Y:").pack(side=tk.LEFT)
    sp_ty = ttk.Spinbox(y_row, from_=0, to=1900, increment=10, textvariable=title_y_var, width=6)
    sp_ty.pack(side=tk.LEFT, padx=4)
    ttk.Scale(y_row, from_=0, to=1900, variable=title_y_var, orient=tk.HORIZONTAL).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

    # Màu sắc Title
    color_grid = ttk.Frame(tg)
    color_grid.pack(fill=tk.X, pady=4)

    def pick_color(target_var, title="Chọn màu"):
        c = colorchooser.askcolor(title=title, color=target_var.get())
        if c and c[1]:
            target_var.set(c[1].upper())
            redraw_title_preview()

    btn_bg_col = tk.Button(color_grid, text="Màu Nền", bg=title_bg_color_var.get(), fg="#FFFFFF", width=9,
                           command=lambda: pick_color(title_bg_color_var, "Chọn màu nền Banner"))
    btn_bg_col.grid(row=0, column=0, padx=2, pady=2)

    btn_c1 = tk.Button(color_grid, text="Màu Chữ 1", bg=title_color1_var.get(), fg="#000000", width=9,
                       command=lambda: pick_color(title_color1_var, "Màu chữ Dòng 1"))
    btn_c1.grid(row=0, column=1, padx=2, pady=2)

    btn_c2 = tk.Button(color_grid, text="Màu Chữ 2", bg=title_color2_var.get(), fg="#000000", width=9,
                       command=lambda: pick_color(title_color2_var, "Màu chữ Dòng 2"))
    btn_c2.grid(row=0, column=2, padx=2, pady=2)

    # 2. GROUP SUBTITLE
    sg = ttk.LabelFrame(c_frame, text=" 💬 Phụ Đề Subtitle (Chuẩn ASS & Whisper AI) ", padding=8)
    sg.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(sg, text="Hiển thị Phụ đề Subtitle", variable=show_sub_var).pack(anchor=tk.W, pady=(0, 4))

    ttk.Label(sg, text="Phong cách Sub:").pack(anchor=tk.W)
    sub_cb = ttk.Combobox(
        sg, textvariable=sub_style_var,
        values=["tiktok_slim", "classic", "standard"], state="readonly", width=18
    )
    sub_cb.pack(anchor=tk.W, pady=(1, 4))

    ttk.Label(sg, text="Câu mẫu xem trước:").pack(anchor=tk.W)
    ttk.Entry(sg, textvariable=sub_sample_var, width=36).pack(fill=tk.X, pady=(1, 4))

    s_row = ttk.Frame(sg)
    s_row.pack(fill=tk.X, pady=2)
    ttk.Label(s_row, text="Lề đáy (MarginV):").pack(side=tk.LEFT)
    ttk.Spinbox(s_row, from_=20, to=400, increment=5, textvariable=sub_margin_v_var, width=6).pack(side=tk.LEFT, padx=4)

    sub_c_row = ttk.Frame(sg)
    sub_c_row.pack(fill=tk.X, pady=4)
    btn_sub_c = tk.Button(sub_c_row, text="Màu Chữ Sub", bg=sub_color_var.get(), fg="#000000", width=12,
                          command=lambda: pick_color(sub_color_var, "Chọn màu chữ Phụ đề"))
    btn_sub_c.pack(side=tk.LEFT, padx=2)

    btn_sub_out = tk.Button(sub_c_row, text="Viền Chữ Sub", bg=sub_outline_color_var.get(), fg="#FFFFFF", width=12,
                            command=lambda: pick_color(sub_outline_color_var, "Chọn màu viền Phụ đề"))
    btn_sub_out.pack(side=tk.LEFT, padx=2)

    # 3. GROUP PART
    pg = ttk.LabelFrame(c_frame, text=" 🏷️ Thẻ Part (Phần Video) ", padding=8)
    pg.pack(fill=tk.X, pady=(0, 6))

    ttk.Checkbutton(pg, text="Bật hiển thị Part", variable=show_part_var).pack(anchor=tk.W, pady=(0, 2))
    p_pos_row = ttk.Frame(pg)
    p_pos_row.pack(fill=tk.X, pady=2)
    ttk.Label(p_pos_row, text="Vị trí:").pack(side=tk.LEFT)
    ttk.Radiobutton(p_pos_row, text="Trong Banner", variable=part_pos_var, value="after_title").pack(side=tk.LEFT, padx=4)
    ttk.Radiobutton(p_pos_row, text="Thẻ Đáy (Y=1650)", variable=part_pos_var, value="bottom_card").pack(side=tk.LEFT, padx=4)

    # --- RIGHT PANEL: LIVE CANVAS 9:16 ---
    title_canvas = tk.Canvas(right_frame, bg="#111827", highlightthickness=0)
    title_canvas.pack(fill=tk.BOTH, expand=True)

    photo_title_cache = None

    def redraw_title_preview():
        nonlocal photo_title_cache
        c_w = title_canvas.winfo_width()
        c_h = title_canvas.winfo_height()
        if c_w < 50 or c_h < 50:
            c_w, c_h = 420, 740

        scale = min((c_w - 20) / 1080.0, (c_h - 20) / 1920.0)
        offset_x = (c_w - 1080.0 * scale) / 2.0
        offset_y = (c_h - 1920.0 * scale) / 2.0

        t_cfg = {
            "show_title": show_title_var.get(),
            "title_line1": title_line1_var.get(),
            "title_line2": title_line2_var.get(),
            "title_y_pos": title_y_var.get(),
            "title_bg_color": title_bg_color_var.get(),
            "title_color1": title_color1_var.get(),
            "title_color2": title_color2_var.get(),
            "title_outline_color": title_outline_var.get(),
        }

        s_cfg = {
            "show_sub": show_sub_var.get(),
            "sample_text": sub_sample_var.get(),
            "sub_style_type": sub_style_var.get(),
            "sub_margin_v": sub_margin_v_var.get(),
            "sub_color": sub_color_var.get(),
            "sub_outline_color": sub_outline_color_var.get()
        }

        p_cfg = {
            "show_part": show_part_var.get(),
            "part_position": part_pos_var.get(),
            "part_number": part_num_var.get()
        }

        # Cập nhật màu nút bấm
        btn_bg_col.config(bg=title_bg_color_var.get())
        btn_c1.config(bg=title_color1_var.get())
        btn_c2.config(bg=title_color2_var.get())
        btn_sub_c.config(bg=sub_color_var.get())
        btn_sub_out.config(bg=sub_outline_color_var.get())

        overlay_1080 = render_title_and_sub_overlay(base_img, t_cfg, s_cfg, p_cfg)

        disp_w = int(1080 * scale)
        disp_h = int(1920 * scale)
        preview_disp = overlay_1080.resize((disp_w, disp_h), Image.BILINEAR)

        photo_title_cache = ImageTk.PhotoImage(preview_disp)
        title_canvas.delete("all")
        title_canvas.create_image(int(offset_x), int(offset_y), anchor="nw", image=photo_title_cache)

        # Viền Canvas
        title_canvas.create_rectangle(offset_x, offset_y, offset_x + disp_w, offset_y + disp_h, outline="#EF4444", width=2)

    for v in [show_title_var, title_line1_var, title_line2_var, title_y_var, title_bg_color_var, title_color1_var, title_color2_var,
              show_sub_var, sub_sample_var, sub_style_var, sub_margin_v_var, sub_color_var, sub_outline_color_var, show_part_var, part_pos_var]:
        v.trace_add("write", lambda *a: redraw_title_preview())

    title_canvas.bind("<Configure>", lambda e: redraw_title_preview())

    # Kéo thả Title trực tiếp trên Canvas
    dragging_title = False
    def on_canvas_press(event):
        nonlocal dragging_title
        dragging_title = True

    def on_canvas_drag(event):
        nonlocal dragging_title
        if dragging_title:
            c_h = title_canvas.winfo_height()
            scale = min((title_canvas.winfo_width() - 20) / 1080.0, (c_h - 20) / 1920.0)
            offset_y = (c_h - 1920.0 * scale) / 2.0
            new_y_1080 = int((event.y - offset_y) / scale)
            title_y_var.set(max(0, min(1800, new_y_1080)))

    def on_canvas_release(event):
        nonlocal dragging_title
        dragging_title = False

    title_canvas.bind("<ButtonPress-1>", on_canvas_press)
    title_canvas.bind("<B1-Motion>", on_canvas_drag)
    title_canvas.bind("<ButtonRelease-1>", on_canvas_release)

    # 4. ACTION BUTTONS
    bottom_bar = ttk.Frame(left_frame)
    bottom_bar.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

    saved_ts_result = {}

    def on_save():
        nonlocal saved_ts_result
        saved_ts_result = {
            "enable_title": bool(show_title_var.get()),
            "show_title": bool(show_title_var.get()),
            "title_line1": title_line1_var.get(),
            "title_line2": title_line2_var.get(),
            "title_text": title_line1_var.get(),
            "title_y_pos": int(title_y_var.get()),
            "title_y": int(title_y_var.get()),
            "title_bg_color": title_bg_color_var.get(),
            "title_color1": title_color1_var.get(),
            "title_color2": title_color2_var.get(),
            "title_outline_color": title_outline_var.get(),
            "show_sub": bool(show_sub_var.get()),
            "sub_style_type": sub_style_var.get(),
            "sub_margin_v": int(sub_margin_v_var.get()),
            "sub_color": sub_color_var.get(),
            "sub_outline_color": sub_outline_color_var.get(),
            "show_part": bool(show_part_var.get()),
            "part_position": part_pos_var.get(),
            "part_number": part_num_var.get()
        }
        if on_save_callback:
            on_save_callback(saved_ts_result)
        popup.destroy()

    def on_reset():
        show_title_var.set(True)
        title_y_var.set(280)
        title_bg_color_var.set("#FF2D55")
        title_color1_var.set("#FFFFFF")
        title_color2_var.set("#FFE600")
        sub_style_var.set("tiktok_slim")
        sub_margin_v_var.set(80)
        sub_color_var.set("#FFFFFF")
        sub_outline_color_var.set("#000000")
        redraw_title_preview()

    btn_save = ttk.Button(bottom_bar, text="💾 Lưu Cấu Hình Title & Sub (1:1)", command=on_save, style="Primary.TButton")
    btn_save.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

    btn_reset = ttk.Button(bottom_bar, text="🔄 Mặc Định", command=on_reset, style="Tool.TButton")
    btn_reset.pack(side=tk.LEFT, padx=2)

    btn_close = ttk.Button(bottom_bar, text="❌ Đóng", command=popup.destroy, style="Tool.TButton")
    btn_close.pack(side=tk.LEFT, padx=2)

    popup.after(100, redraw_title_preview)
    popup.wait_window()
    return saved_ts_result
