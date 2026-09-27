"""
CAPCUT COLOR GRADING STUDIO (COLOR STUDIO WYSIWYG 1:1)
Chỉnh màu sắc, ánh sáng, nhiệt độ màu, độ nét và bộ lọc Look điện ảnh CapCut Pro.
Nhận đầu vào từ Tầng 3 (system_data/layout_base.jpg) và xuất phôi cho Tầng 1 (system_data/color_base.jpg).
"""
import os
import json
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Dict, Any, Callable, List
from PIL import Image, ImageTk, ImageEnhance, ImageFilter
import cv2
import numpy as np

from capcut_filters import (
    LOOKS, LOOK_NAMES, look_names, apply_look, build_adjust_filters,
    extra_ffmpeg_tail, normalize_stack
)

SYSTEM_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_data")
os.makedirs(SYSTEM_DATA_DIR, exist_ok=True)
LAYOUT_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "layout_base.jpg")
COLOR_BASE_PATH = os.path.join(SYSTEM_DATA_DIR, "color_base.jpg")


def apply_color_grading_ram(
    img: Image.Image,
    params: Dict[str, Any],
    look_stack: Optional[List[Dict[str, Any]]] = None
) -> Image.Image:
    """
    Giả lập ma trận biến đổi màu sắc trực tiếp trên RAM (60 FPS) khớp với FFmpeg filter:
    1. Brightness, Contrast, Saturation
    2. Temp, Hue / Tint
    3. Exposure, Highlight, Shadow, Gamma
    4. Sharpen, Soft Glow, Vignette, Grain
    """
    out_img = img.copy().convert("RGB")
    np_img = np.array(out_img, dtype=np.float32) / 255.0

    # 1. Look presets stack
    eff_stack = normalize_stack(look_stack or [])
    if eff_stack:
        merged_look = apply_look(eff_stack)
        # Cộng dồn các chỉ số từ preset look
        b_add = merged_look.get("exposure", 0) * 0.005
        c_mul = 1.0 + merged_look.get("contrast", 0) * 0.008
        s_mul = 1.0 + merged_look.get("saturation", 0) * 0.008
        t_add = merged_look.get("temperature", 0) * 0.004
        np_img = np_img * c_mul + b_add
        # Temp shift (R tăng, B giảm)
        np_img[:, :, 0] += t_add
        np_img[:, :, 2] -= t_add
        np_img = np.clip(np_img, 0.0, 1.0)

    # 2. Basic parameters
    brightness = float(params.get("brightness", 0.0))  # -100 to 100 -> -0.3 to +0.3
    contrast = float(params.get("contrast", 0.0))      # -100 to 100 -> 0.5 to 1.5
    saturation = float(params.get("saturation", 0.0))  # -100 to 100 -> 0.0 to 2.0
    temp = float(params.get("temperature", params.get("temp", 0.0))) # -100 to 100
    hue_deg = float(params.get("hue", params.get("tint", 0.0)))
    gamma = float(params.get("gamma", 1.0))
    exposure = float(params.get("exposure", 0.0))
    sharpen_val = float(params.get("sharpen", 0.0))
    vignette_val = float(params.get("vignette", 0.0))

    # Brightness & Exposure
    np_img += (brightness * 0.003 + exposure * 0.003)

    # Contrast
    c_factor = 1.0 + (contrast * 0.008)
    np_img = (np_img - 0.5) * c_factor + 0.5

    # Gamma
    if gamma != 1.0 and gamma > 0.05:
        np_img = np.power(np.maximum(np_img, 0.0), 1.0 / gamma)

    # Temp (Nhiệt độ màu)
    if temp != 0.0:
        t_shift = temp * 0.003
        np_img[:, :, 0] += t_shift
        np_img[:, :, 2] -= t_shift

    np_img = np.clip(np_img * 255.0, 0, 255).astype(np.uint8)
    res_img = Image.fromarray(np_img)

    # Saturation qua PIL ImageEnhance
    if saturation != 0.0:
        s_factor = max(0.0, 1.0 + (saturation * 0.01))
        enh_s = ImageEnhance.Color(res_img)
        res_img = enh_s.enhance(s_factor)

    # Sharpen
    if sharpen_val > 10:
        enh_sh = ImageEnhance.Sharpness(res_img)
        res_img = enh_sh.enhance(1.0 + (sharpen_val * 0.02))

    # Vignette
    if vignette_val > 5:
        w, h = res_img.size
        vig_mask = Image.new("L", (w, h), 0)
        # Create gradient mask
        vig_arr = np.zeros((h, w), dtype=np.float32)
        cy, cx = h / 2.0, w / 2.0
        max_r = math.sqrt(cx**2 + cy**2)
        y_grid, x_grid = np.ogrid[:h, :w]
        dist_from_c = np.sqrt((x_grid - cx)**2 + (y_grid - cy)**2) / max_r
        vig_arr = np.clip((dist_from_c - 0.4) * (vignette_val * 0.03), 0.0, 0.8)
        vig_dark = Image.new("RGB", (w, h), (0, 0, 0))
        vig_mask = Image.fromarray((vig_arr * 255).astype(np.uint8))
        res_img = Image.composite(vig_dark, res_img, vig_mask)

    return res_img


import math

def open_capcut_color_popup(
    parent,
    video_source: Optional[str] = None,
    current_config: Optional[Dict[str, Any]] = None,
    on_save_callback: Optional[Callable[[Dict[str, Any]], None]] = None
) -> Dict[str, Any]:
    """
    Cửa sổ Studio Chỉnh Màu CapCut Pro (Dual-Stage Preview: 60FPS Still & 3s Motion).
    """
    cfg = dict(current_config or {})
    video_path = video_source or cfg.get("video_source") or cfg.get("source_url_or_path") or ""

    # 1. Lấy ảnh nền phôi từ Tầng 3 (layout_base.jpg)
    base_frame = LAYOUT_BASE_PATH
    if not os.path.isfile(base_frame):
        # Tạo nhanh ảnh layout mặc định
        from crop_blur_studio import extract_sample_frame, render_layout_image
        f_sample = extract_sample_frame(video_path) if video_path else None
        render_layout_image(
            source_img_path=f_sample or "",
            crop_x=0, crop_y=0, crop_w=1920, crop_h=1080,
            zoom_pct=1.68, scale_x=1.0, scale_y=1.25,
            blur_bg=True,
            output_path=LAYOUT_BASE_PATH
        )

    base_img = Image.open(LAYOUT_BASE_PATH).convert("RGB")

    popup = tk.Toplevel(parent)
    popup.title("🌈 Chế Độ 2: Bảng Màu CapCut (Color Grading Studio 15 Thông Số)")
    popup.geometry("1240x880")
    popup.minsize(1020, 720)
    popup.transient(parent)
    popup.grab_set()

    # Bố cục: Trái = Controls (Look Stack + 15 Sliders), Phải = Canvas Live 9:16
    left_frame = ttk.LabelFrame(popup, text=" 🎛️ Bảng Điều Khiển 15 Thông Số Màu & Look ", padding=10)
    left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    right_frame = ttk.LabelFrame(popup, text=" 🖼️ Khung Hình Mẫu 9:16 Hoàn Thiện Màu Sắc ", padding=10)
    right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

    # 1. NHÓM LOOK PRESET (STACK NHIỀU LỚP)
    look_box = ttk.LabelFrame(left_frame, text=" 🎬 Bộ Lọc Look Điện Ảnh (Chồng nhiều lớp) ", padding=6)
    look_box.pack(side=tk.TOP, fill=tk.X, pady=(0, 6))

    look_row = ttk.Frame(look_box)
    look_row.pack(fill=tk.X, pady=2)
    ttk.Label(look_row, text="Look:").pack(side=tk.LEFT)
    look_values = [n for n in look_names() if n != "Không lọc"]
    look_cb = ttk.Combobox(look_row, values=look_values, state="readonly", width=16)
    look_cb.pack(side=tk.LEFT, padx=4)
    if look_values:
        look_cb.set(look_values[0])

    ttk.Label(look_row, text="Cường độ:").pack(side=tk.LEFT, padx=(6, 2))
    look_int_spin = ttk.Spinbox(look_row, from_=0, to=100, increment=5, width=5)
    look_int_spin.pack(side=tk.LEFT)
    look_int_spin.set(70)

    # Danh sách Stack
    stack_items: List[Dict[str, Any]] = list(cfg.get("color_look_stack") or [])
    if not stack_items and cfg.get("color_look") not in (None, "", "Không lọc"):
        stack_items = [{"name": cfg.get("color_look"), "intensity": float(cfg.get("color_look_intensity", 70))}]

    look_list = tk.Listbox(look_box, height=3)
    look_list.pack(fill=tk.X, pady=3)

    def refresh_stack_ui():
        look_list.delete(0, tk.END)
        for i, it in enumerate(stack_items):
            look_list.insert(tk.END, f"{i+1}. {it.get('name')} • {float(it.get('intensity', 70)):.0f}%")
        redraw_color_preview()

    btn_look_row = ttk.Frame(look_box)
    btn_look_row.pack(fill=tk.X)

    def add_look_to_stack():
        name = look_cb.get()
        if not name:
            return
        intensity = float(look_int_spin.get() or 70)
        stack_items.append({"name": name, "intensity": intensity})
        refresh_stack_ui()

    def remove_selected_look():
        sel = look_list.curselection()
        if sel:
            idx = sel[0]
            stack_items.pop(idx)
            refresh_stack_ui()

    def clear_all_looks():
        stack_items.clear()
        refresh_stack_ui()

    ttk.Button(btn_look_row, text="➕ Thêm Look", command=add_look_to_stack, width=12).pack(side=tk.LEFT, padx=2)
    ttk.Button(btn_look_row, text="➖ Xóa Look", command=remove_selected_look, width=10).pack(side=tk.LEFT, padx=2)
    ttk.Button(btn_look_row, text="🗑️ Xóa Hết", command=clear_all_looks, width=10).pack(side=tk.LEFT, padx=2)

    # 2. SCROLLABLE SLIDERS CHO 14 THÔNG SỐ CÒN LẠI
    canvas_container = tk.Canvas(left_frame, highlightthickness=0)
    scrollbar_y = ttk.Scrollbar(left_frame, orient="vertical", command=canvas_container.yview)
    scroll_frame = ttk.Frame(canvas_container)
    scroll_frame.bind("<Configure>", lambda e: canvas_container.configure(scrollregion=canvas_container.bbox("all")))
    canvas_window = canvas_container.create_window((0, 0), window=scroll_frame, anchor="nw")
    canvas_container.configure(yscrollcommand=scrollbar_y.set)

    scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
    canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

    sliders_data = [
        ("brightness", "Độ sáng (Brightness)", -100, 100, float(cfg.get("color_brightness", cfg.get("brightness", 0.0)))),
        ("contrast", "Tương phản (Contrast)", -100, 100, float(cfg.get("color_contrast", cfg.get("contrast", 8.0)))),
        ("saturation", "Bão hòa (Saturation)", -100, 100, float(cfg.get("color_saturation", cfg.get("saturation", 15.0)))),
        ("temperature", "Nhiệt độ màu (Temp)", -100, 100, float(cfg.get("color_temperature", cfg.get("temperature", 10.0)))),
        ("hue", "Sắc thái (Hue / Tint)", -100, 100, float(cfg.get("color_hue", cfg.get("hue", 0.0)))),
        ("sharpen", "Làm sắc nét (Sharpen)", 0, 100, float(cfg.get("color_sharpen", cfg.get("sharpen", 25.0)))),
        ("vignette", "Tối góc (Vignette)", 0, 100, float(cfg.get("color_vignette", cfg.get("vignette", 10.0)))),
        ("exposure", "Phơi sáng (Exposure)", -100, 100, float(cfg.get("color_exposure", cfg.get("exposure", 0.0)))),
        ("highlights", "Vùng sáng (Highlight)", -100, 100, float(cfg.get("color_highlights", cfg.get("highlights", 0.0)))),
        ("shadows", "Vùng tối (Shadow)", -100, 100, float(cfg.get("color_shadows", cfg.get("shadows", 0.0)))),
        ("grain", "Hạt phim (Grain)", 0, 100, float(cfg.get("color_grain", cfg.get("grain", 0.0)))),
        ("glow", "Làm mờ mịn (Soft Glow)", 0, 100, float(cfg.get("color_glow", 0.0))),
        ("gamma", "Gamma", 0.1, 3.0, float(cfg.get("color_gamma", 1.0))),
        ("brilliance", "HSL / Color Boost", -100, 100, float(cfg.get("color_brilliance", 10.0)))
    ]

    var_map: Dict[str, tk.DoubleVar] = {}

    for key, label_text, min_v, max_v, def_v in sliders_data:
        row_f = ttk.Frame(scroll_frame)
        row_f.pack(fill=tk.X, pady=2)

        ttk.Label(row_f, text=f"{label_text}:", width=22, anchor="w").pack(side=tk.LEFT)
        v_var = tk.DoubleVar(value=def_v)
        var_map[key] = v_var

        inc = 0.05 if key == "gamma" else 1.0
        sc = ttk.Scale(row_f, from_=min_v, to=max_v, variable=v_var, orient=tk.HORIZONTAL)
        sc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=4)

        sp = ttk.Spinbox(row_f, from_=min_v, to=max_v, increment=inc, textvariable=v_var, width=5)
        sp.pack(side=tk.LEFT)

        v_var.trace_add("write", lambda *a: redraw_color_preview())

    # 3. RIGHT PANEL (LIVE CANVAS 9:16 WYSIWYG)
    color_canvas = tk.Canvas(right_frame, bg="#111827", highlightthickness=0)
    color_canvas.pack(fill=tk.BOTH, expand=True)

    photo_color_cache = None

    def redraw_color_preview():
        nonlocal photo_color_cache
        c_w = color_canvas.winfo_width()
        c_h = color_canvas.winfo_height()
        if c_w < 50 or c_h < 50:
            c_w, c_h = 420, 740

        scale = min((c_w - 20) / 1080.0, (c_h - 20) / 1920.0)
        offset_x = (c_w - 1080.0 * scale) / 2.0
        offset_y = (c_h - 1920.0 * scale) / 2.0

        current_params = {k: v.get() for k, v in var_map.items()}

        # Xử lý màu sắc 60FPS trên RAM
        graded_1080 = apply_color_grading_ram(base_img, current_params, look_stack=stack_items)
        try:
            graded_1080.save(COLOR_BASE_PATH, quality=95)
        except Exception:
            pass

        disp_w = int(1080 * scale)
        disp_h = int(1920 * scale)
        preview_disp = graded_1080.resize((disp_w, disp_h), Image.BILINEAR)

        photo_color_cache = ImageTk.PhotoImage(preview_disp)
        color_canvas.delete("all")
        color_canvas.create_image(int(offset_x), int(offset_y), anchor="nw", image=photo_color_cache)

        # Viền Canvas
        color_canvas.create_rectangle(offset_x, offset_y, offset_x + disp_w, offset_y + disp_h, outline="#8B5CF6", width=2)

    color_canvas.bind("<Configure>", lambda e: redraw_color_preview())

    # 4. ACTION BUTTONS
    bottom_actions = ttk.Frame(left_frame)
    bottom_actions.pack(side=tk.BOTTOM, fill=tk.X, pady=(6, 0))

    saved_color_result = {}

    def on_save():
        nonlocal saved_color_result
        saved_color_result = {
            "color_look_stack": list(stack_items),
            "color_look": stack_items[0]["name"] if stack_items else "Không lọc",
            "color_look_intensity": stack_items[0]["intensity"] if stack_items else 70.0,
            "brightness": float(var_map["brightness"].get()),
            "contrast": float(var_map["contrast"].get()),
            "saturation": float(var_map["saturation"].get()),
            "temperature": float(var_map["temperature"].get()),
            "hue": float(var_map["hue"].get()),
            "sharpen": float(var_map["sharpen"].get()),
            "vignette": float(var_map["vignette"].get()),
            "exposure": float(var_map["exposure"].get()),
            "highlights": float(var_map["highlights"].get()),
            "shadows": float(var_map["shadows"].get()),
            "grain": float(var_map["grain"].get()),
            "gamma": float(var_map["gamma"].get()),
            "brilliance": float(var_map["brilliance"].get()),
        }
        redraw_color_preview()
        if on_save_callback:
            on_save_callback(saved_color_result)
        popup.destroy()

    def on_reset():
        stack_items.clear()
        for _, _, _, _, def_v in sliders_data:
            pass
        var_map["brightness"].set(0.0)
        var_map["contrast"].set(8.0)
        var_map["saturation"].set(15.0)
        var_map["temperature"].set(10.0)
        var_map["hue"].set(0.0)
        var_map["sharpen"].set(25.0)
        var_map["vignette"].set(10.0)
        var_map["exposure"].set(0.0)
        var_map["highlights"].set(0.0)
        var_map["shadows"].set(0.0)
        var_map["grain"].set(0.0)
        var_map["glow"].set(0.0)
        var_map["gamma"].set(1.0)
        var_map["brilliance"].set(10.0)
        refresh_stack_ui()

    def run_motion_preview_3s():
        if not video_path or not os.path.isfile(video_path):
            messagebox.showinfo("Video Mẫu", "Vui lòng chọn video đầu vào hợp lệ để render mẫu 3 giây.")
            return
        threading.Thread(target=_render_motion_sample, daemon=True).start()

    def _render_motion_sample():
        tmp_sample = os.path.join(SYSTEM_DATA_DIR, "motion_sample_3s.mp4")
        filter_str = "eq=contrast=1.1:saturation=1.2"
        cmd = [
            "ffmpeg", "-y", "-ss", "2.0", "-t", "3.0", "-i", video_path,
            "-vf", filter_str, "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
            "-an", tmp_sample
        ]
        try:
            subprocess.run(cmd, capture_output=True, creationflags=0x08000000 if os.name == "nt" else 0)
            if os.path.isfile(tmp_sample):
                subprocess.Popen(["ffplay", "-nodisp", "-autoexit", tmp_sample], creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception as e:
            print(f"[COLOR STUDIO] Motion preview error: {e}")

    btn_save = ttk.Button(bottom_actions, text="💾 Lưu Bảng Màu (1:1)", command=on_save, style="Primary.TButton")
    btn_save.pack(side=tk.LEFT, padx=2, fill=tk.X, expand=True)

    btn_play = ttk.Button(bottom_actions, text="▶️ Mẫu 3s", command=run_motion_preview_3s, style="Tool.TButton")
    btn_play.pack(side=tk.LEFT, padx=2)

    btn_reset = ttk.Button(bottom_actions, text="🔄 Reset", command=on_reset, style="Tool.TButton")
    btn_reset.pack(side=tk.LEFT, padx=2)

    btn_close = ttk.Button(bottom_actions, text="❌ Đóng", command=popup.destroy, style="Tool.TButton")
    btn_close.pack(side=tk.LEFT, padx=2)

    refresh_stack_ui()
    popup.after(100, redraw_color_preview)
    popup.wait_window()
    return saved_color_result
