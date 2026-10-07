"""Giao diện 3 Tab chuyên biệt cho Chế độ Chia Part & Tinh Lược Video Gốc (PartSplitterFrame).

Đúng chuẩn 100% tài liệu kỹ thuật PLAN_CHIA_PART.md:
- Tab 1: Nguồn (YouTube, Local File/Folder, Cookie, Kiểu Hook + Hook Studio, Mốc hook & Dải +-50%, Thư mục xuất)
- Tab 2: AI Gemini & Chiến Lược Chia Part (Google Gemini Local, Đăng nhập/Kiểm tra, Model, Auto Viral Title theo ngôn ngữ gốc, Pruning +-20%, Part Splits Cliffhanger +-50%)
- Tab 3: Hậu kỳ Video Gốc (Tốc độ atempo, Âm lượng, Blur nền, Zoom-in, Scale ngang/dọc, CapCut Limiter +20dB, Subtle Zoom 1.03x, Random Mirror, Tiền tố Part quốc tế, Quét lưới AI xóa logo, Studio Popups: Crop, Color, Title & Sub)
- Thanh CONFIG CHIA PART độc lập (Chọn, Tên, Load, Lưu, Xóa) lưu vào config_part_splitter.json.
- Bảng hàng đợi Batch, thanh tiến trình và cửa sổ nhật ký tiến trình (Logs).
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import json
import time
import glob
import hashlib
import threading
import concurrent.futures
from copy import deepcopy
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog

from part_splitter_config import (
    load_part_config, save_part_config, DEFAULT_PART_CONFIG,
    save_named_part_config, delete_named_part_config, get_saved_part_config_names
)
from font_manager import FontManager
from part_pruner_ai import PartPrunerAI
from part_splitter_engine import PartSplitterEngine, probe_duration_sec

try:
    from downloader_processor import DownloaderProcessor
except ImportError:
    DownloaderProcessor = None

try:
    from local_source_processor import LocalSourceProcessor
except ImportError:
    LocalSourceProcessor = None



PART_QUEUE_FILE = os.path.join("data", "part_queue.json")


class PartSplitterFrame(ttk.Frame):
    """Giao diện độc lập cho Chế độ Chia Part & Tinh Lược Video Gốc."""

    def __init__(self, parent, app=None, log_fn: Optional[Callable[[str], None]] = None, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.app = app
        self.log_fn = log_fn
        self.config: Dict[str, Any] = load_part_config()

        # Hàng đợi công việc (đồng bộ lưu file persistent data/part_queue.json)
        self._queue_lock = threading.RLock()
        self.queue_items: List[Dict[str, Any]] = []
        self.queue_running: bool = False
        self.stop_requested: bool = False
        self.worker_thread: Optional[threading.Thread] = None

        self._init_variables()
        self._build_ui()
        self._load_config_to_ui()
        self._refresh_named_config_list()
        self._load_queue_from_disk()
        self._refresh_queue_table()
        sel_name = self.config.get("selected_config", "")
        if sel_name and sel_name in self.config.get("saved_configs", {}):
            try:
                self.on_named_config_selected(_event=object())
            except Exception:
                pass

    def _init_variables(self):
        """Khởi tạo toàn bộ biến trạng thái giao diện theo đúng PLAN_CHIA_PART.md & PLAN_CHE_DO_2.md."""
        c = self.config

        # --- Tab 1: Nguồn ---
        self.source_mode_var = tk.StringVar(value=c.get("source_mode", "youtube"))
        self.output_dir_var = tk.StringVar(value=c.get("output_dir", "output/parts"))
        self.hook_mode_var = tk.StringVar(value=c.get("hook_mode", "individual"))
        self.hook_duration_var = tk.DoubleVar(value=float(c.get("hook_target_sec", 6.0)))
        self.hook_range_label_var = tk.StringVar()

        # --- Tab 2: AI Gemini & Chiến Lược Chia Part ---
        self.google_status_var = tk.StringVar(value="● Đang kiểm tra phiên Google...")
        self.editing_mode_var = tk.StringVar(value=c.get("editing_mode", "viral_condensed"))
        self.target_part_duration_var = tk.DoubleVar(value=float(c.get("target_part_duration", 90.0)))
        self.gemini_model_var = tk.StringVar(value=c.get("gemini_model", "gemini-3.8-flash-high"))
        self.gemini_api_key_var = tk.StringVar(value=c.get("gemini_api_key", ""))
        self.auto_title_var = tk.BooleanVar(value=bool(c.get("auto_regenerate_title", True)))

        self.prune_enabled_var = tk.BooleanVar(value=bool(c.get("prune_enabled", True)))
        self.prune_mode_var = tk.StringVar(value=c.get("prune_mode", "percent"))
        self.prune_percent_var = tk.DoubleVar(value=float(c.get("prune_percent", 20.0)))
        self.prune_minutes_var = tk.DoubleVar(value=float(c.get("prune_minutes", 15.0)))
        self.prune_range_label_var = tk.StringVar()

        self.part_count_var = tk.IntVar(value=int(c.get("part_count", 6)))

        # --- Tab 3: Hậu kỳ Video Gốc (Đóng gói chuẩn đối thủ) ---
        self.enable_competitor_layout_var = tk.BooleanVar(value=bool(c.get("enable_competitor_layout", True)))
        self.competitor_top_card_var = tk.BooleanVar(value=bool(c.get("competitor_top_card", True)))
        self.competitor_bottom_badge_var = tk.BooleanVar(value=bool(c.get("competitor_bottom_badge", True)))
        self.source_speed_var = tk.DoubleVar(value=float(c.get("source_speed", 1.0)))
        self.blur_var = tk.BooleanVar(value=bool(c.get("blur_bg", True)))
        self.zoom_var = tk.BooleanVar(value=bool(c.get("zoom_in", False)))
        self.capcut_limiter_var = tk.BooleanVar(value=bool(c.get("apply_capcut_limiter", True)))
        self.subtle_zoom_var = tk.BooleanVar(value=bool(c.get("apply_subtle_zoom", True)))
        self.random_mirror_var = tk.BooleanVar(value=bool(c.get("random_mirror", False)))
        self.part_prefix_var = tk.StringVar(value=c.get("part_label_prefix", "Part"))
        self.gemini_grid_inspector_var = tk.BooleanVar(value=False)
        self.qc_blur_strength_var = tk.IntVar(value=int(c.get("qc_blur_strength", 75)))
        self.cleanup_temp_var = tk.BooleanVar(value=bool(c.get("cleanup_temp_after_export", False)))

        # Trạng thái tổng
        self.status_msg_var = tk.StringVar(value="Sẵn sàng thực thi chia part.")

        # Lắng nghe cập nhật nhãn tính toán dải dao động theo thời gian thực
        self.hook_duration_var.trace_add("write", lambda *_: self._update_calc_labels())
        self.prune_percent_var.trace_add("write", lambda *_: self._update_calc_labels())
        self.prune_minutes_var.trace_add("write", lambda *_: self._update_calc_labels())
        self.prune_mode_var.trace_add("write", lambda *_: self._update_calc_labels())
        self._update_calc_labels()

    def _update_calc_labels(self):
        """Cập nhật các nhãn dao động +-50% và +-20% trực quan."""
        # Hook +-50%
        try:
            h = float(self.hook_duration_var.get())
            h_min = max(1.0, h * 0.5)
            h_max = h * 1.5
            self.hook_range_label_var.set(f"🎯 Dải Hook (±50%): {h_min:.1f}s ~ {h_max:.1f}s (Cắt cụt lủn trước cao trào)")
        except Exception:
            self.hook_range_label_var.set("")

        # Prune +-20%
        try:
            m = self.prune_mode_var.get()
            if m == "percent":
                p = float(self.prune_percent_var.get())
                self.prune_range_label_var.set(f"✂️ Dải Lược (±20%): {p*0.8:.1f}% ~ {p*1.2:.1f}% thời lượng video")
            else:
                mins = float(self.prune_minutes_var.get())
                self.prune_range_label_var.set(f"✂️ Dải Lược (±20%): {mins*0.8:.1f}m ~ {mins*1.2:.1f}m ({mins*48:.0f}s ~ {mins*72:.0f}s)")
        except Exception:
            self.prune_range_label_var.set("")

    def _build_ui(self):
        """Thiết kế bố cục chuẩn: 3 Tabs -> Config Bar -> Danh sách Video & Hàng đợi."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # ---------------- 1. NOTEBOOK 3 TAB ----------------
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=0, column=0, sticky=tk.EW, padx=4, pady=(2, 4))

        self.tab1 = ttk.Frame(self.notebook, padding=7)
        self.tab2 = ttk.Frame(self.notebook, padding=7)
        self.tab3 = ttk.Frame(self.notebook, padding=7)

        self.notebook.add(self.tab1, text="  1  Nguồn  ")
        self.notebook.add(self.tab2, text="  2  AI & Chiến Lược Chia Part  ")
        self.notebook.add(self.tab3, text="  3  Hậu kỳ Video Gốc  ")

        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

        # ---------------- 2. CONFIG CHIA PART BAR ----------------
        self._build_config_bar()

        # ---------------- 3. HÀNG ĐỢI RENDER & LOG ----------------
        self._build_queue_and_logs()

    # =========================================================================
    # TAB 1: NGUỒN (PLAN_CHIA_PART.md: URL/Local, Hook strategy, Output dir)
    # =========================================================================
    def _build_tab1(self):
        f1 = self.tab1
        f1.columnconfigure(1, weight=1)

        # Hàng 0: Nguồn YouTube
        ttk.Label(f1, text="YouTube:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=3)
        self.url_entry = ttk.Entry(f1, width=42)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=3)
        self.url_entry.insert(0, self.config.get("youtube_url", "https://www.youtube.com/watch?v=..."))

        yt_btn_row = ttk.Frame(f1)
        yt_btn_row.grid(row=0, column=2, columnspan=4, sticky=tk.W, padx=2, pady=3)
        self.btn_paste = ttk.Button(yt_btn_row, text="📋 Dán", command=self._paste_clipboard, style="Tool.TButton")
        self.btn_paste.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_multi_link = ttk.Button(yt_btn_row, text="📑 Đa Link", command=self._open_multi_link_popup, style="Tool.TButton")
        self.btn_multi_link.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_txt_file = ttk.Button(yt_btn_row, text="📄 File TXT", command=self._browse_youtube_txt_file, style="Tool.TButton")
        self.btn_txt_file.pack(side=tk.LEFT)

        # Hàng 1: Nguồn YouTube XOR Local Video
        mode_row = ttk.Frame(f1)
        mode_row.grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=(2, 3))
        ttk.Label(mode_row, text="Nguồn:").pack(side=tk.LEFT, padx=(2, 8))
        self.rb_source_youtube = ttk.Radiobutton(
            mode_row, text="YouTube (1 Video, Playlist hoặc Đa link)", variable=self.source_mode_var, value="youtube",
            command=self.on_source_mode_change
        )
        self.rb_source_youtube.pack(side=tk.LEFT, padx=(0, 10))
        self.rb_source_local = ttk.Radiobutton(
            mode_row, text="Video có sẵn (File hoặc Thư mục)", variable=self.source_mode_var, value="local",
            command=self.on_source_mode_change
        )
        self.rb_source_local.pack(side=tk.LEFT)

        # Hàng 2: Local file / folder
        ttk.Label(f1, text="Local:").grid(row=2, column=0, sticky=tk.W, padx=2, pady=3)
        self.local_path_entry = ttk.Entry(f1, width=42)
        self.local_path_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=3)
        self.local_path_entry.insert(0, self.config.get("local_source_path", ""))

        local_btn_row = ttk.Frame(f1)
        local_btn_row.grid(row=2, column=2, columnspan=4, sticky=tk.W, padx=2, pady=3)
        self.btn_browse_file = ttk.Button(local_btn_row, text="📄 File", command=self._browse_source_file, style="Tool.TButton")
        self.btn_browse_file.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_browse_folder = ttk.Button(local_btn_row, text="📁 Folder", command=self._browse_source_folder, style="Tool.TButton")
        self.btn_browse_folder.pack(side=tk.LEFT)

        # Hàng 3: Thư mục xuất thành phẩm
        out_row = ttk.Frame(f1)
        out_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(4, 3))
        ttk.Label(out_row, text="Thư mục xuất:").pack(side=tk.LEFT, padx=(0, 6))
        self.output_dir_entry = ttk.Entry(out_row, textvariable=self.output_dir_var, width=48)
        self.output_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(out_row, text="📂 Chọn...", command=self._browse_output_dir, style="Tool.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(out_row, text="📁 Mở Folder", command=self.open_output_folder, style="Tool.TButton").pack(side=tk.LEFT)

        # Hàng 4: Thông báo Hook AI Đối Thủ (Chế độ 3-Pass)
        self.viral_hook_notice = ttk.Label(
            f1,
            text="✨ [ĐẠO DIỄN AI 3-PASS] Trưởng phòng dựng tự động quét tìm Hook đỉnh cao nhất (2-3s) đưa lên giây 00:00 của mỗi Part. Không cần cấu hình Hook thủ công.",
            foreground="#2563EB", font=("Segoe UI Semibold", 8)
        )
        self.viral_hook_notice.grid(row=4, column=0, columnspan=6, sticky=tk.W, padx=2, pady=(6, 2))

        # Hàng 5: Cụm cấu hình Hook thủ công (Chỉ hiển thị khi chọn Classic Linear ở Tab 2)
        self.classic_hook_frame = ttk.Frame(f1)
        self.classic_hook_frame.grid(row=5, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=2)

        self.hook_row = ttk.Frame(self.classic_hook_frame)
        self.hook_row.pack(fill=tk.X, pady=2)
        ttk.Label(self.hook_row, text="Kiểu hook:").pack(side=tk.LEFT, padx=(0, 6))
        for label, val in (
            ("Tự chọn mốc", "native"),
            ("Gemini chọn cao trào (Toàn cảnh)", "shared"),
            ("Gemini chọn hook riêng (Từng Part)", "individual"),
            ("Hook AI", "ai"),
        ):
            ttk.Radiobutton(
                self.hook_row, text=label, variable=self.hook_mode_var, value=val,
                command=self.on_hook_mode_change
            ).pack(side=tk.LEFT, padx=(0, 8))

        self.btn_hook_studio = ttk.Button(
            self.hook_row, text="🛠 Hook Studio", command=self.open_custom_hook_studio_popup, style="Tool.TButton"
        )
        self.btn_hook_studio.pack(side=tk.RIGHT, padx=(4, 0))

        self.time_row = ttk.Frame(self.classic_hook_frame)
        self.time_row.pack(fill=tk.X, pady=2)
        ttk.Label(self.time_row, text="Mốc hook:").pack(side=tk.LEFT, padx=(0, 4))
        self.hook_time_entry = ttk.Entry(self.time_row, width=8)
        self.hook_time_entry.pack(side=tk.LEFT, padx=(0, 8))
        self.hook_time_entry.insert(0, self.config.get("hook_time", "11:55"))

        ttk.Label(self.time_row, text="Thời lượng chuẩn:").pack(side=tk.LEFT, padx=(0, 4))
        self.hook_spin = ttk.Spinbox(self.time_row, from_=2.0, to=30.0, increment=0.5, textvariable=self.hook_duration_var, width=5)
        self.hook_spin.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(self.time_row, text="giây").pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_hook_calc = ttk.Label(self.time_row, textvariable=self.hook_range_label_var, foreground="#2563EB", font=("Segoe UI Semibold", 9))
        self.lbl_hook_calc.pack(side=tk.LEFT)

        self.on_source_mode_change()
        self.on_hook_mode_change()
        self.on_editing_mode_change()

    # =========================================================================
    # TAB 2: AI GEMINI & CHIẾN LƯỢC CHIA PART (PLAN_CHIA_PART.md)
    # =========================================================================
    def _build_tab2(self):
        f2 = self.tab2
        f2.columnconfigure(3, weight=1)

        # Hàng 0: AI Gemini Configuration Frame (Model & Viral Title)
        ai_box = ttk.LabelFrame(f2, text="  🤖 CẤU HÌNH AI GEMINI  ", padding=(7, 4))
        ai_box.grid(row=0, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 6))

        sub_row = ttk.Frame(ai_box)
        sub_row.pack(fill=tk.X, expand=True, pady=2)
        ttk.Label(sub_row, text="Mô hình AI:").pack(side=tk.LEFT, padx=(2, 4))
        self.model_cb = ttk.Combobox(
            sub_row, textvariable=self.gemini_model_var,
            values=["gemini-3.8-flash-high", "gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"],
            width=20, state="readonly"
        )
        self.model_cb.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(sub_row, text="API Key (dự phòng):").pack(side=tk.LEFT, padx=(0, 4))
        self.api_key_entry = ttk.Entry(sub_row, textvariable=self.gemini_api_key_var, width=18, show="*")
        self.api_key_entry.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            sub_row, text="☑ Tự đặt lại Tiêu đề Viral theo đúng ngôn ngữ gốc", variable=self.auto_title_var
        ).pack(side=tk.LEFT)

        # Hàng 1: CHẾ ĐỘ BIÊN TẬP (MỚI)
        self.editor_mode_box = ttk.LabelFrame(f2, text="  🎬 CHẾ ĐỘ BIÊN TẬP  ", padding=7)
        self.editor_mode_box.grid(row=1, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 6))

        em_radio_row = ttk.Frame(self.editor_mode_box)
        em_radio_row.pack(fill=tk.X, pady=(2, 6))
        self.rb_viral_condensed = ttk.Radiobutton(
            em_radio_row, text="🔘 Dựng Tinh Hoa Đối Thủ (3-Pass AI Director) ⭐ (Khuyên dùng)",
            variable=self.editing_mode_var, value="viral_condensed",
            command=self.on_editing_mode_change
        )
        self.rb_viral_condensed.pack(side=tk.LEFT, padx=(2, 16))

        self.rb_classic_linear = ttk.Radiobutton(
            em_radio_row, text="⚪ Cắt Khúc Cơ Bản (Classic Linear - Kiểu cũ)",
            variable=self.editing_mode_var, value="classic_linear",
            command=self.on_editing_mode_change
        )
        self.rb_classic_linear.pack(side=tk.LEFT)

        # Khung tham số cho Dựng Tinh Hoa Đối Thủ (3-Pass AI Director)
        self.viral_settings_frame = ttk.Frame(self.editor_mode_box)
        self.viral_settings_frame.pack(fill=tk.X, pady=2)
        ttk.Label(self.viral_settings_frame, text="Số Part cần chia:").pack(side=tk.LEFT, padx=(2, 4))
        self.part_count_spin = ttk.Spinbox(
            self.viral_settings_frame, from_=2, to=20, increment=1,
            textvariable=self.part_count_var, width=5
        )
        self.part_count_spin.pack(side=tk.LEFT, padx=(0, 14))

        ttk.Label(self.viral_settings_frame, text="Thời lượng mỗi Part:").pack(side=tk.LEFT, padx=(0, 4))
        self.target_dur_spin = ttk.Spinbox(
            self.viral_settings_frame, from_=60.0, to=300.0, increment=5.0,
            textvariable=self.target_part_duration_var, width=6
        )
        self.target_dur_spin.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(self.viral_settings_frame, text="giây").pack(side=tk.LEFT, padx=(0, 8))

        ttk.Label(
            self.viral_settings_frame,
            text="(Chuẩn đối thủ 75s – 95s: Đạo Diễn AI lọc 80% râu ria, chọn Hook 2-3s đưa lên đầu & micro-cuts thoại mượt)",
            foreground="#2563EB", font=("Segoe UI Semibold", 8)
        ).pack(side=tk.LEFT)

        # Khung tham số dự phòng cho Cắt Khúc Cơ Bản (Classic Linear)
        self.classic_settings_frame = ttk.Frame(self.editor_mode_box)
        ttk.Label(
            self.classic_settings_frame,
            text="✂️ Classic: Cắt đoạn thẳng theo mốc thời gian cliffhanger (không micro-cuts)",
            foreground="#64748B", font=("Segoe UI", 8)
        ).pack(side=tk.LEFT, padx=2)


    # =========================================================================
    # TAB 3: HẬU KỲ VIDEO GỐC (PLAN_CHIA_PART.md & STUDIO TOOLS)
    # =========================================================================
    def _build_tab3(self):
        f3 = self.tab3
        f3.columnconfigure(1, weight=1)

        # Hàng 0: Tốc độ, Âm lượng, Blur nền
        ttk.Label(f3, text="Tốc độ:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.speed_spin = ttk.Spinbox(f3, from_=0.5, to=3.0, increment=0.05, textvariable=self.source_speed_var, width=6)
        self.speed_spin.grid(row=0, column=1, sticky=tk.W, padx=3, pady=3)

        ttk.Label(f3, text="Âm lượng (dB):").grid(row=0, column=2, sticky=tk.W, pady=3, padx=(8, 2))
        self.audio_boost_spin = ttk.Spinbox(f3, from_=0.0, to=20.0, increment=1.0, width=6)
        self.audio_boost_spin.grid(row=0, column=3, sticky=tk.W, padx=3, pady=3)
        self.audio_boost_spin.set(self.config.get("audio_boost", 6.0))

        self.blur_chk = ttk.Checkbutton(f3, text="Làm mờ nền (Blur)", variable=self.blur_var)
        self.blur_chk.grid(row=0, column=4, padx=8, sticky=tk.W)

        # Hàng 1: Zoom-in & Scale ngang/dọc
        self.zoom_chk = ttk.Checkbutton(f3, text="Zoom-in (%):", variable=self.zoom_var)
        self.zoom_chk.grid(row=1, column=0, sticky=tk.W, pady=3)

        self.zoom_spin = ttk.Spinbox(f3, from_=100, to=300, increment=5, width=6)
        self.zoom_spin.grid(row=1, column=1, sticky=tk.W, padx=3, pady=3)
        self.zoom_spin.set(self.config.get("zoom_percent", 115))

        ttk.Label(f3, text="Scale ngang:").grid(row=1, column=2, sticky=tk.W, pady=2, padx=(8, 2))
        self.scale_w_spin = ttk.Spinbox(f3, from_=50, to=300, increment=5, width=6)
        self.scale_w_spin.grid(row=1, column=3, sticky=tk.W, padx=3, pady=3)
        self.scale_w_spin.set(self.config.get("scale_w", 100))

        ttk.Label(f3, text="Scale dọc:").grid(row=1, column=4, sticky=tk.W, pady=2, padx=(8, 2))
        self.scale_h_spin = ttk.Spinbox(f3, from_=50, to=300, increment=5, width=6)
        self.scale_h_spin.grid(row=1, column=5, sticky=tk.W, padx=3, pady=3)
        self.scale_h_spin.set(self.config.get("scale_h", 100))

        # Hàng 2: KHUNG ĐÓNG GÓI CHUẨN ĐỐI THỦ TRIỆU VIEW (COMPETITOR LAYOUT)
        self.competitor_box = ttk.LabelFrame(f3, text="  🌟 ĐÓNG GÓI CHUẨN ĐỐI THỦ TRIỆU VIEW (COMPETITOR LAYOUT)  ", padding=6)
        self.competitor_box.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(3, 3))

        c_row1 = ttk.Frame(self.competitor_box)
        c_row1.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(
            c_row1, text="☑ Canvas 9:16 + Nền Gaussian Blur trên/dưới",
            variable=self.enable_competitor_layout_var
        ).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Checkbutton(
            c_row1, text="☑ In Top Card: Thẻ trắng bo góc lấy Title gốc YouTube",
            variable=self.competitor_top_card_var
        ).pack(side=tk.LEFT, padx=(0, 12))

        c_row2 = ttk.Frame(self.competitor_box)
        c_row2.pack(fill=tk.X, pady=2)
        ttk.Checkbutton(
            c_row2, text="☑ In Bottom Badge: Nút trắng viên thuốc 'PART X'",
            variable=self.competitor_bottom_badge_var
        ).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(
            c_row2, text="🔊 Chuẩn âm lượng thoại: -14 LUFS (đanh rõ, sống động trên điện thoại)",
            foreground="#16825D", font=("Segoe UI Semibold", 8)
        ).pack(side=tk.LEFT)

        # Hàng 3: CapCut Limiter, Subtle Zoom, Lật gương hình ảnh
        effects_row = ttk.Frame(f3)
        effects_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, pady=(3, 2))
        ttk.Checkbutton(
            effects_row, text="☑ CapCut Limiter +20dB (Compressor chuẩn)", variable=self.capcut_limiter_var
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            effects_row, text="☑ Subtle Zoom (1.03x chống quét bản quyền)", variable=self.subtle_zoom_var
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            effects_row, text="☐ Lật gương video (Random Mirror)", variable=self.random_mirror_var
        ).pack(side=tk.LEFT)

        # Hàng 4: Tiền tố nhãn Part quốc tế
        self.prefix_row = ttk.Frame(f3)
        self.prefix_row.grid(row=4, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Label(self.prefix_row, text="Tiền tố nhãn Part:").pack(side=tk.LEFT, padx=(0, 4))
        self.prefix_entry = ttk.Entry(self.prefix_row, textvariable=self.part_prefix_var, width=12)
        self.prefix_entry.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(
            self.prefix_row,
            text="(Ví dụ: 'Part', 'Teil', 'Partie', 'Часть', 'Tập'...) Font tự động khớp Unicode quốc tế.",
            foreground="#64748B"
        ).pack(side=tk.LEFT)

        # Hàng 5: Xóa thư mục tạm sau khi xuất video
        clean_row = ttk.Frame(f3)
        clean_row.grid(row=5, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Checkbutton(
            clean_row, text="Xóa thư mục tạm sau khi xuất video", variable=self.cleanup_temp_var
        ).pack(side=tk.LEFT)

        # Hàng 6: Bộ 3 nút công cụ Studio (Crop, Color, Title & Sub)
        studio_row = ttk.Frame(f3)
        studio_row.grid(row=6, column=0, columnspan=6, sticky=tk.EW, pady=(6, 2))
        for col in range(3):
            studio_row.columnconfigure(col, weight=1)

        self.btn_open_crop = ttk.Button(studio_row, text="✂ Crop", command=self.open_crop_tool_popup, style="Tool.TButton")
        self.btn_open_crop.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3))

        self.btn_open_color = ttk.Button(studio_row, text="🎨 Color", command=self.open_capcut_color_popup, style="Tool.TButton")
        self.btn_open_color.grid(row=0, column=1, sticky=tk.EW, padx=3)

        self.btn_open_title_sub = ttk.Button(studio_row, text="🔤 Title & Sub", command=self.open_title_sub_studio_popup, style="Tool.TButton")
        self.btn_open_title_sub.grid(row=0, column=2, sticky=tk.EW, padx=(3, 0))

    # =========================================================================
    # CONFIG CHIA PART BAR (RIÊNG BIỆT CHO CHẾ ĐỘ CHIA PART)
    # =========================================================================
    def _build_config_bar(self):
        config_bar = ttk.LabelFrame(self, text="  CONFIG CHIA PART  ", padding=(7, 4))
        config_bar.grid(row=1, column=0, sticky=tk.EW, padx=4, pady=(0, 4))
        config_bar.columnconfigure(1, weight=1)
        config_bar.columnconfigure(3, weight=1)

        ttk.Label(config_bar, text="Chọn:").grid(row=0, column=0, sticky=tk.W, padx=(2, 5))
        self.named_config_cb = ttk.Combobox(
            config_bar, values=get_saved_part_config_names(),
            state="readonly"
        )
        self.named_config_cb.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        self.named_config_cb.bind("<<ComboboxSelected>>", self.on_named_config_choice)

        ttk.Label(config_bar, text="Tên:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.named_config_name_entry = ttk.Entry(config_bar)
        self.named_config_name_entry.grid(row=0, column=3, sticky=tk.EW, padx=(0, 8))

        ttk.Button(
            config_bar, text="↧ Load", command=self.on_named_config_selected,
            style="Tool.TButton", width=8
        ).grid(row=0, column=4, padx=(0, 4))

        ttk.Button(
            config_bar, text="💾 Lưu", command=self.save_named_config,
            style="Tool.TButton", width=8
        ).grid(row=0, column=5, padx=(0, 2))

        ttk.Button(
            config_bar, text="🗑 Xóa", command=self.delete_named_config,
            style="Danger.TButton", width=8
        ).grid(row=0, column=6, padx=(2, 2))

    # =========================================================================
    # HÀNG ĐỢI RENDER & LOG TIẾN TRÌNH
    # =========================================================================
    def _build_queue_and_logs(self):
        bottom_frame = ttk.Frame(self, padding=4)
        bottom_frame.grid(row=2, column=0, sticky=tk.NSEW, padx=4, pady=(0, 2))
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.rowconfigure(1, weight=1)
        bottom_frame.rowconfigure(3, weight=1)

        # Thanh nút bấm hành động
        action_bar = ttk.Frame(bottom_frame)
        action_bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        action_bar.columnconfigure(7, weight=1)

        ttk.Button(action_bar, text="＋ Thêm vào Hàng Đợi", command=self.add_current_to_queue, style="Tool.TButton").grid(row=0, column=0, padx=2)
        ttk.Button(action_bar, text="🗑 Xóa", command=self.delete_selected_queue, style="Danger.TButton").grid(row=0, column=1, padx=2)
        ttk.Button(action_bar, text="↻ Chạy lại", command=self.retry_selected_queue, style="Tool.TButton").grid(row=0, column=2, padx=2)
        ttk.Button(action_bar, text="▲", width=3, command=lambda: self.move_queue_item(-1)).grid(row=0, column=3, padx=2)
        ttk.Button(action_bar, text="▼", width=3, command=lambda: self.move_queue_item(1)).grid(row=0, column=4, padx=2)
        ttk.Button(action_bar, text="📁 Mở Thư Mục Xuất", command=self.open_output_folder, style="Tool.TButton").grid(row=0, column=5, padx=4)

        self.btn_start = ttk.Button(
            action_bar, text="🚀 BẮT ĐẦU CHIA PART & XUẤT VIDEO",
            command=self.start_queue_processing, style="Primary.TButton"
        )
        self.btn_start.grid(row=0, column=6, padx=8)

        self.btn_stop = ttk.Button(
            action_bar, text="■ Dừng",
            command=self.stop_queue_processing, style="Danger.TButton"
        )
        self.btn_stop.grid(row=0, column=7, sticky=tk.E, padx=4)

        # Bảng Treeview
        queue_box = ttk.LabelFrame(bottom_frame, text="  DANH SÁCH VIDEO CHIA PART  ", padding=4)
        queue_box.grid(row=1, column=0, sticky=tk.NSEW, pady=(0, 4))
        queue_box.columnconfigure(0, weight=1)
        queue_box.rowconfigure(0, weight=1)

        cols = ("num", "source", "parts", "mode", "dur", "status", "output")
        self.queue_tree = ttk.Treeview(queue_box, columns=cols, show="headings", height=5)
        headings = {
            "num": "#", "source": "Video Nguồn", "parts": "Số Part",
            "mode": "Chế độ Dựng", "dur": "Thời lượng", "status": "Trạng thái", "output": "Thành phẩm"
        }
        widths = {"num": 34, "source": 260, "parts": 70, "mode": 120, "dur": 85, "status": 120, "output": 160}
        for col in cols:
            self.queue_tree.heading(col, text=headings[col])
            self.queue_tree.column(col, width=widths[col], anchor=tk.W, stretch=(col in {"source", "status", "output"}))

        self.queue_tree.tag_configure("completed", foreground="#16825D")
        self.queue_tree.tag_configure("failed", foreground="#C93C37")
        self.queue_tree.tag_configure("running", background="#EAF1FF", foreground="#1749A3")
        self.queue_tree.tag_configure("pending", foreground="#172033")
        self.queue_tree.bind("<Double-1>", self.on_queue_double_click)
        self.queue_tree.bind("<ButtonRelease-1>", self.on_queue_click)

        q_scroll = ttk.Scrollbar(queue_box, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=q_scroll.set)
        self.queue_tree.grid(row=0, column=0, sticky=tk.NSEW)
        q_scroll.grid(row=0, column=1, sticky=tk.NS)

        # Progress bar
        status_bar = ttk.Frame(bottom_frame)
        status_bar.grid(row=2, column=0, sticky=tk.EW, pady=(2, 2))
        status_bar.columnconfigure(1, weight=1)

        ttk.Label(status_bar, text="Tiến trình:", font=("Segoe UI Semibold", 9)).grid(row=0, column=0, sticky=tk.W, padx=(0, 6))
        self.prog_bar = ttk.Progressbar(status_bar, mode="indeterminate")
        self.prog_bar.grid(row=0, column=1, sticky=tk.EW, padx=(0, 8))
        ttk.Label(status_bar, textvariable=self.status_msg_var, foreground="#2563EB", font=("Segoe UI", 9)).grid(row=0, column=2, sticky=tk.E)

        # Khung Log
        log_box = ttk.LabelFrame(bottom_frame, text="  NHẬT KÝ TIẾN TRÌNH (LOGS)  ", padding=4)
        log_box.grid(row=3, column=0, sticky=tk.NSEW)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_box, height=5, font=("Consolas", 9),
            bg="#1E222D", fg="#E1E7F0", insertbackground="#FFFFFF"
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)

    # =========================================================================
    # LOAD & SAVE CẤU HÌNH (ĐỒNG BỘ WIDGETS)
    # =========================================================================
    def _load_config_to_ui(self):
        """Nạp toàn bộ cấu hình từ self.config lên giao diện."""
        c = self.config
        try:
            if hasattr(self, "url_entry"):
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, c.get("youtube_url", ""))

            if hasattr(self, "local_path_entry"):
                self.local_path_entry.delete(0, tk.END)
                self.local_path_entry.insert(0, c.get("local_source_path", ""))

            self.source_mode_var.set(c.get("source_mode", "youtube"))
            self.hook_mode_var.set(c.get("hook_mode", "individual"))

            if hasattr(self, "hook_time_entry"):
                self.hook_time_entry.delete(0, tk.END)
                self.hook_time_entry.insert(0, c.get("hook_time", "11:55"))

            self.hook_duration_var.set(float(c.get("hook_target_sec", 6.0)))
            self.output_dir_var.set(c.get("output_dir", "output/parts"))

            self.gemini_model_var.set(c.get("gemini_model", "gemini-3.8-flash-high"))
            self.gemini_api_key_var.set(c.get("gemini_api_key", ""))
            self.auto_title_var.set(bool(c.get("auto_regenerate_title", True)))

            self.editing_mode_var.set(c.get("editing_mode", "viral_condensed"))
            self.target_part_duration_var.set(float(c.get("target_part_duration", 90.0)))
            self.enable_competitor_layout_var.set(bool(c.get("enable_competitor_layout", True)))
            self.competitor_top_card_var.set(bool(c.get("competitor_top_card", True)))
            self.competitor_bottom_badge_var.set(bool(c.get("competitor_bottom_badge", True)))

            self.prune_enabled_var.set(bool(c.get("prune_enabled", True)))
            self.prune_mode_var.set(c.get("prune_mode", "percent"))
            self.prune_percent_var.set(float(c.get("prune_percent", 20.0)))
            self.prune_minutes_var.set(float(c.get("prune_minutes", 15.0)))

            self.part_count_var.set(int(c.get("part_count", 6)))

            spd = float(c.get("speed") or c.get("source_speed") or 1.0)
            self.source_speed_var.set(spd)
            if hasattr(self, "audio_boost_spin"):
                self.audio_boost_spin.set(float(c.get("audio_boost", 20.0)))
            self.blur_var.set(bool(c.get("blur_bg", True)))
            z_pct = float(c.get("zoom_percent", 115.0))
            if hasattr(self, "zoom_spin"):
                self.zoom_spin.set(z_pct)
            self.zoom_var.set(bool(c.get("zoom_in", False)) or (abs(z_pct - 100.0) >= 0.1))
            if hasattr(self, "scale_w_spin"):
                self.scale_w_spin.set(float(c.get("scale_w", 100.0)))
            if hasattr(self, "scale_h_spin"):
                self.scale_h_spin.set(float(c.get("scale_h", 100.0)))

            self.capcut_limiter_var.set(bool(c.get("apply_capcut_limiter", True)))
            self.subtle_zoom_var.set(bool(c.get("apply_subtle_zoom", True)))
            self.random_mirror_var.set(bool(c.get("random_mirror", False)))
            self.part_prefix_var.set(c.get("part_label_prefix", "Part"))
            self.gemini_grid_inspector_var.set(bool(c.get("gemini_grid_inspector", True)))
            if hasattr(self, "qc_blur_strength_var"):
                b_val = int(c.get("qc_blur_strength", 75))
                self.qc_blur_strength_var.set(b_val)
                if hasattr(self, "qc_blur_strength_lbl"):
                    self.qc_blur_strength_lbl.config(text=f"{b_val}%")
                if hasattr(self, "qc_blur_scale"):
                    self.qc_blur_scale.set(b_val)
            self.cleanup_temp_var.set(bool(c.get("cleanup_temp_after_export", False)))

            self.on_source_mode_change()
            self.on_hook_mode_change()
            self.on_editing_mode_change()
            self._update_calc_labels()
        except Exception as e:
            print(f"⚠️ [PART SPLITTER] Lỗi nạp config lên UI: {e}")

    def _save_ui_to_config(self):
        """Lấy toàn bộ dữ liệu từ widgets cập nhật vào self.config và lưu JSON."""
        c = self.config

        if hasattr(self, "url_entry"):
            c["youtube_url"] = self.url_entry.get().strip()
        if hasattr(self, "local_path_entry"):
            c["local_source_path"] = self.local_path_entry.get().strip()

        c["source_mode"] = self.source_mode_var.get()
        c["hook_mode"] = self.hook_mode_var.get()
        c["hook_strategy"] = self.hook_mode_var.get()
        if hasattr(self, "hook_time_entry"):
            c["hook_time"] = self.hook_time_entry.get().strip()
        c["hook_target_sec"] = float(self.hook_duration_var.get())
        c["output_dir"] = self.output_dir_var.get().strip() or "output/parts"

        c["gemini_model"] = self.gemini_model_var.get()
        c["gemini_api_key"] = self.gemini_api_key_var.get().strip()
        c["auto_regenerate_title"] = bool(self.auto_title_var.get())

        c["editing_mode"] = self.editing_mode_var.get() if hasattr(self, "editing_mode_var") else "viral_condensed"
        c["target_part_duration"] = float(self.target_part_duration_var.get()) if hasattr(self, "target_part_duration_var") else 90.0
        c["enable_competitor_layout"] = bool(self.enable_competitor_layout_var.get()) if hasattr(self, "enable_competitor_layout_var") else True
        c["competitor_top_card"] = bool(self.competitor_top_card_var.get()) if hasattr(self, "competitor_top_card_var") else True
        c["competitor_bottom_badge"] = bool(self.competitor_bottom_badge_var.get()) if hasattr(self, "competitor_bottom_badge_var") else True

        c["prune_enabled"] = bool(self.prune_enabled_var.get())
        c["prune_mode"] = self.prune_mode_var.get()
        c["prune_percent"] = float(self.prune_percent_var.get())
        c["prune_minutes"] = float(self.prune_minutes_var.get())

        c["part_count"] = int(self.part_count_var.get())

        spd_val = float(self.source_speed_var.get() or 1.0)
        c["source_speed"] = spd_val
        c["speed"] = spd_val
        if hasattr(self, "audio_boost_spin"):
            try:
                c["audio_boost"] = float(self.audio_boost_spin.get() or 20.0)
            except Exception:
                pass
        c["blur_bg"] = bool(self.blur_var.get())
        zoom_val = 100.0
        if hasattr(self, "zoom_spin"):
            try:
                zoom_val = float(self.zoom_spin.get() or 100.0)
            except Exception:
                zoom_val = 100.0
        c["zoom_percent"] = zoom_val
        c["zoom_in"] = bool(self.zoom_var.get()) or (abs(zoom_val - 100.0) >= 0.1)
        if hasattr(self, "scale_w_spin"):
            try:
                c["scale_w"] = float(self.scale_w_spin.get() or 100.0)
            except Exception:
                pass
        if hasattr(self, "scale_h_spin"):
            try:
                c["scale_h"] = float(self.scale_h_spin.get() or 100.0)
            except Exception:
                pass

        c["apply_capcut_limiter"] = bool(self.capcut_limiter_var.get())
        c["apply_subtle_zoom"] = bool(self.subtle_zoom_var.get())
        c["random_mirror"] = bool(self.random_mirror_var.get())
        c["part_label_prefix"] = self.part_prefix_var.get().strip() or "Part"
        c["gemini_grid_inspector"] = bool(self.gemini_grid_inspector_var.get())
        if hasattr(self, "qc_blur_strength_var"):
            c["qc_blur_strength"] = int(self.qc_blur_strength_var.get())
        c["cleanup_temp_after_export"] = bool(self.cleanup_temp_var.get())

        save_part_config(c)

    # =========================================================================
    # QUẢN LÝ CONFIG CÓ TÊN (NAMED CONFIGS)
    # =========================================================================
    def _named_config_payload(self) -> Dict[str, Any]:
        """Tạo bản sao cấu hình hiện tại để lưu theo tên (loại bỏ URL/Local tạm)."""
        self._save_ui_to_config()
        excluded = {"youtube_url", "local_source_path", "saved_configs", "selected_config"}
        return {k: deepcopy(v) for k, v in self.config.items() if k not in excluded}

    def _refresh_named_config_list(self, select_name: str = ""):
        names = list(self.config.get("saved_configs", {}).keys())
        name = select_name or self.config.get("selected_config", "")
        if hasattr(self, "named_config_cb"):
            self.named_config_cb["values"] = names
            self.named_config_cb.set(name if name in names else "")
        if hasattr(self, "named_config_name_entry"):
            self.named_config_name_entry.delete(0, tk.END)
            if name in names:
                self.named_config_name_entry.insert(0, name)

    def save_named_config(self):
        name = self.named_config_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Thiếu tên", "Hãy đặt tên config, ví dụ: 'Chia 4 Part Chuẩn' hoặc 'Teil Phim Đức'.")
            return
        saved = self.config.setdefault("saved_configs", {})
        if name in saved and not messagebox.askyesno("Cập nhật Config", f'Config "{name}" đã tồn tại. Ghi đè?'):
            return
        saved[name] = self._named_config_payload()
        self.config["selected_config"] = name
        save_part_config(self.config)
        self._refresh_named_config_list(name)
        messagebox.showinfo("Đã lưu", f'Đã lưu thành công config Chia Part "{name}".')

    def on_named_config_selected(self, _event=None, name: str = ""):
        if not name:
            name = self.named_config_cb.get().strip() if hasattr(self, "named_config_cb") else ""
        if not name:
            name = self.config.get("selected_config", "")
        payload = self.config.get("saved_configs", {}).get(name)
        if not isinstance(payload, dict):
            return

        payload_to_load = deepcopy(payload)
        # Giữ lại nguồn và thư mục đang nhập
        preserved = {
            "youtube_url": self.url_entry.get().strip() if hasattr(self, "url_entry") else "",
            "local_source_path": self.local_path_entry.get().strip() if hasattr(self, "local_path_entry") else "",
            "source_mode": self.source_mode_var.get(),
            "output_dir": self.output_dir_var.get().strip() if hasattr(self, "output_dir_var") else "output/parts",
            "saved_configs": self.config.get("saved_configs", {}),
        }
        self.config.update(payload_to_load)
        self.config.update(preserved)
        self.config["selected_config"] = name
        self._load_config_to_ui()

        self.named_config_name_entry.delete(0, tk.END)
        self.named_config_name_entry.insert(0, name)
        save_part_config(self.config)

        if _event is None:
            messagebox.showinfo("Đã Load", f'Đã áp dụng config Chia Part "{name}" lên toàn bộ giao diện.')
        else:
            print(f'✅ [CONFIG PART] Đã tự động áp dụng config "{name}".')

    def on_named_config_choice(self, _event=None):
        name = self.named_config_cb.get().strip()
        self.named_config_name_entry.delete(0, tk.END)
        self.named_config_name_entry.insert(0, name)
        self.on_named_config_selected(_event)

    def delete_named_config(self):
        name = self.named_config_cb.get().strip()
        if not name:
            return
        if not messagebox.askyesno("Xóa Config", f'Xóa config Chia Part "{name}"?'):
            return
        self.config.setdefault("saved_configs", {}).pop(name, None)
        if self.config.get("selected_config") == name:
            self.config["selected_config"] = ""
        save_part_config(self.config)
        self._refresh_named_config_list()

    # =========================================================================
    # SỰ KIỆN GIAO DIỆN (HANDLERS)
    # =========================================================================
    def on_source_mode_change(self):
        mode = self.source_mode_var.get()
        if mode == "youtube":
            self.url_entry.config(state=tk.NORMAL)
            if hasattr(self, "btn_paste"): self.btn_paste.config(state=tk.NORMAL)
            if hasattr(self, "btn_multi_link"): self.btn_multi_link.config(state=tk.NORMAL)
            if hasattr(self, "btn_txt_file"): self.btn_txt_file.config(state=tk.NORMAL)
            self.local_path_entry.config(state=tk.DISABLED)
            self.btn_browse_file.config(state=tk.DISABLED)
            self.btn_browse_folder.config(state=tk.DISABLED)
        else:
            self.url_entry.config(state=tk.DISABLED)
            if hasattr(self, "btn_paste"): self.btn_paste.config(state=tk.DISABLED)
            if hasattr(self, "btn_multi_link"): self.btn_multi_link.config(state=tk.DISABLED)
            if hasattr(self, "btn_txt_file"): self.btn_txt_file.config(state=tk.DISABLED)
            self.local_path_entry.config(state=tk.NORMAL)
            self.btn_browse_file.config(state=tk.NORMAL)
            self.btn_browse_folder.config(state=tk.NORMAL)

    def on_hook_mode_change(self):
        m = self.hook_mode_var.get()
        if hasattr(self, "hook_time_entry"):
            self.hook_time_entry.config(state=tk.NORMAL if m == "native" else tk.DISABLED)

    def on_editing_mode_change(self):
        """Chuyển đổi giữa Dựng Tinh Hoa Đối Thủ (3-Pass AI) và Cắt Khúc Cơ Bản (Classic Linear)."""
        m = self.editing_mode_var.get() if hasattr(self, "editing_mode_var") else "viral_condensed"
        is_viral = (m == "viral_condensed")
        if hasattr(self, "viral_settings_frame") and hasattr(self, "classic_settings_frame"):
            if is_viral:
                self.viral_settings_frame.pack(fill=tk.X, pady=2)
                self.classic_settings_frame.pack_forget()
            else:
                self.viral_settings_frame.pack_forget()
                self.classic_settings_frame.pack(fill=tk.X, pady=2)

        if hasattr(self, "viral_hook_notice") and hasattr(self, "classic_hook_frame"):
            if is_viral:
                self.viral_hook_notice.grid()
                self.classic_hook_frame.grid_remove()
            else:
                self.viral_hook_notice.grid_remove()
                self.classic_hook_frame.grid()

    def on_workflow_mode_change(self):
        self.on_editing_mode_change()

    # =========================================================================
    # LIÊN KẾT STUDIO POPUPS VỚI APP CHA (DELEGATION)
    # =========================================================================
    STUDIO_SYNC_KEYS = (
        "use_crop", "base_w", "base_h", "crop_x", "crop_y", "crop_w", "crop_h", "crop_ratio", "crop_top", "crop_bottom", "crop_left", "crop_right",
        "use_blur_mask", "blur_shape", "blur_mask_x", "blur_mask_y", "blur_mask_w", "blur_mask_h",
        "color_look", "color_look_intensity", "color_look_stack",
        "temperature", "tint", "saturation", "exposure", "contrast",
        "highlights", "shadows", "whites", "blacks", "brilliance",
        "sharpen", "clarity", "grain", "blur", "vignette",
        "lut_preset", "brightness", "pos_x", "pos_y", "zoom_percent", "zoom_in", "scale_w", "scale_h", "blur_bg",
        "speed", "audio_boost",
        "title_y_pos", "title_color1", "title_color2", "title_outline_color", "title_bg_color",
        "sub_style_type", "sub_size", "sub_outline", "sub_shadow", "sub_margin_v", "sub_color", "sub_outline_color"
    )

    def _sync_to_parent_for_studio(self):
        """Giữ cách ly tuyệt đối: không ghi đè dữ liệu Chia Part sang app cha."""
        pass

    def _on_studio_saved(self, saved_dict=None):
        """Lưu cấu hình Studio độc lập vào self.config và ghi xuống config_part_splitter.json."""
        if isinstance(saved_dict, dict):
            for k, v in saved_dict.items():
                self.config[k] = deepcopy(v)
            c_sel = self.config.get("selected_config", "")
            if c_sel and isinstance(self.config.get("saved_configs"), dict) and c_sel in self.config["saved_configs"]:
                for k, v in saved_dict.items():
                    self.config["saved_configs"][c_sel][k] = deepcopy(v)
            save_part_config(self.config)

            # Đồng bộ trực tiếp ngược lại lên các Widget trên GUI
            if "zoom_percent" in saved_dict and hasattr(self, "zoom_spin"):
                self.zoom_spin.set(saved_dict["zoom_percent"])
            elif "zoom_in" in saved_dict and isinstance(saved_dict["zoom_in"], (int, float)) and hasattr(self, "zoom_spin"):
                self.zoom_spin.set(saved_dict["zoom_in"])

            if hasattr(self, "zoom_var"):
                if "zoom_in" in saved_dict and isinstance(saved_dict["zoom_in"], bool):
                    self.zoom_var.set(saved_dict["zoom_in"])
                elif "zoom_percent" in saved_dict:
                    self.zoom_var.set(abs(float(saved_dict["zoom_percent"]) - 100.0) >= 0.1)

            if "scale_w" in saved_dict and hasattr(self, "scale_w_spin"):
                self.scale_w_spin.set(saved_dict["scale_w"])
            elif "scale_x" in saved_dict and hasattr(self, "scale_w_spin"):
                self.scale_w_spin.set(saved_dict["scale_x"])

            if "scale_h" in saved_dict and hasattr(self, "scale_h_spin"):
                self.scale_h_spin.set(saved_dict["scale_h"])
            elif "scale_y" in saved_dict and hasattr(self, "scale_h_spin"):
                self.scale_h_spin.set(saved_dict["scale_y"])

            if "blur_bg" in saved_dict and hasattr(self, "blur_var"):
                self.blur_var.set(bool(saved_dict["blur_bg"]))

            print("💾 [PART SPLITTER] Đã lưu cấu hình Studio độc lập vào config_part_splitter.json và đồng bộ giao diện!")

    def _sync_from_parent_after_studio(self):
        pass

    def open_crop_tool_popup(self):
        self._save_ui_to_config()
        self.config["app_mode"] = "compilation"
        self.config["is_part_splitter"] = True
        if self.app and hasattr(self.app, "open_crop_tool_popup"):
            popup = self.app.open_crop_tool_popup(
                on_save_callback=self._on_studio_saved,
                caller_config=self.config
            )
            if popup and hasattr(popup, "wait_window"):
                popup.wait_window()
        else:
            messagebox.showinfo("Crop Tool", "Mở công cụ Crop Studio từ cửa sổ ứng dụng chính.")

    def open_capcut_color_popup(self):
        self._save_ui_to_config()
        self.config["app_mode"] = "compilation"
        self.config["is_part_splitter"] = True
        if self.app and hasattr(self.app, "open_capcut_color_popup"):
            popup = self.app.open_capcut_color_popup(
                on_save_callback=self._on_studio_saved,
                caller_config=self.config
            )
            if popup and hasattr(popup, "wait_window"):
                popup.wait_window()
        else:
            messagebox.showinfo("CapCut Color", "Mở công cụ Chỉnh Màu CapCut từ cửa sổ chính.")

    def open_title_sub_studio_popup(self):
        self._save_ui_to_config()
        self.config["app_mode"] = "compilation"
        self.config["is_part_splitter"] = True
        if self.app and hasattr(self.app, "open_title_sub_studio_popup"):
            popup = self.app.open_title_sub_studio_popup(
                on_save_callback=self._on_studio_saved,
                caller_config=self.config
            )
            if popup and hasattr(popup, "wait_window"):
                popup.wait_window()
        else:
            messagebox.showinfo("Title & Sub", "Mở Studio Tiêu Đề & Phụ Đề từ cửa sổ chính.")

    def open_custom_hook_studio_popup(self):
        if self.app and hasattr(self.app, "open_custom_hook_studio_popup"):
            self.app.open_custom_hook_studio_popup()
        else:
            messagebox.showinfo("Hook Studio", "Mở Custom Hook Studio từ cửa sổ chính.")

    def open_youtube_cookie_dialog(self):
        if self.app and hasattr(self.app, "open_youtube_cookie_dialog"):
            self.app.open_youtube_cookie_dialog()
        else:
            messagebox.showinfo("Cookie", "Mở hội thoại Cookie từ cửa sổ chính.")

    def open_google_login(self):
        if self.app and hasattr(self.app, "open_google_login"):
            self.app.open_google_login()
        else:
            messagebox.showinfo("Google Login", "Vui lòng mở đăng nhập Google qua cửa sổ chính.")

    def check_google_login(self, manual=True):
        if self.app and hasattr(self.app, "check_google_login"):
            self.app.check_google_login(manual=manual)
        else:
            self.google_status_var.set("● Antigravity Google Active")

    # =========================================================================
    # XỬ LÝ FILE & NGUỒN
    # =========================================================================
    def _paste_clipboard(self):
        try:
            txt = self.clipboard_get().strip()
            if txt:
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, txt)
        except Exception:
            pass

    def _paste_comp_clipboard(self):
        try:
            txt = self.clipboard_get().strip()
            if txt:
                self.comp_source_path_entry.delete(0, tk.END)
                self.comp_source_path_entry.insert(0, txt)
        except Exception:
            pass

    def _browse_source_file(self):
        f = filedialog.askopenfilename(
            title="Chọn file video nguồn",
            filetypes=[("Video Files", "*.mp4;*.mkv;*.mov;*.avi;*.flv;*.ts;*.webm"), ("All Files", "*.*")]
        )
        if f:
            self.local_path_entry.delete(0, tk.END)
            self.local_path_entry.insert(0, f)
            self.source_mode_var.set("local")
            self.on_source_mode_change()

    def _browse_source_folder(self):
        d = filedialog.askdirectory(title="Chọn thư mục chứa video nguồn")
        if d:
            self.local_path_entry.delete(0, tk.END)
            self.local_path_entry.insert(0, d)
            self.source_mode_var.set("local")
            self.on_source_mode_change()

    def _browse_output_dir(self):
        d = filedialog.askdirectory(title="Chọn thư mục xuất thành phẩm các Part")
        if d:
            self.output_dir_var.set(d)

    def open_output_folder(self):
        sel = self.queue_tree.selection() if hasattr(self, "queue_tree") else None
        target_dir = ""
        if sel:
            selected_id = sel[0]
            job = next((j for j in self.queue_items if j.get("id") == selected_id), None)
            if job and job.get("output_dir") and os.path.isdir(job.get("output_dir")):
                target_dir = os.path.abspath(job["output_dir"])
        if not target_dir:
            target_dir = os.path.abspath(self.output_dir_var.get().strip() or "output/parts")
        os.makedirs(target_dir, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(target_dir)
        elif sys.platform == "darwin":
            os.system(f'open "{target_dir}"')
        else:
            os.system(f'xdg-open "{target_dir}"')

    def _open_multi_link_popup(self):
        """Cửa sổ dán danh sách nhiều link YouTube cùng lúc để thêm hàng loạt vào hàng đợi."""
        pop = tk.Toplevel(self)
        pop.title("Nhập Danh Sách Link YouTube (Mỗi Link 1 Dòng)")
        pop.geometry("600x400")
        pop.transient(self.winfo_toplevel())
        pop.grab_set()

        ttk.Label(
            pop,
            text="Dán danh sách các link YouTube vào đây (hỗ trợ cả link video và link playlist):",
            font=("Segoe UI Semibold", 9)
        ).pack(anchor=tk.W, padx=10, pady=(10, 4))

        txt = scrolledtext.ScrolledText(pop, wrap=tk.WORD, height=13, font=("Segoe UI", 9))
        txt.pack(fill=tk.BOTH, expand=True, padx=10, pady=4)
        txt.focus_set()

        btn_row = ttk.Frame(pop)
        btn_row.pack(fill=tk.X, padx=10, pady=(6, 10))

        def _paste_to_box():
            try:
                cl = self.clipboard_get().strip()
                if cl:
                    txt.insert(tk.END, cl + "\n")
            except Exception:
                pass

        def _add_all():
            content = txt.get("1.0", tk.END).strip()
            if not content:
                pop.destroy()
                return
            lines = [line.strip() for line in content.splitlines() if line.strip() and not line.strip().startswith("#")]
            added = 0
            for line in lines:
                if "playlist" in line or "list=" in line:
                    try:
                        if CompilationProcessor is not None:
                            self.log(f"🔍 Đang quét nhanh playlist: {line}...")
                            items = CompilationProcessor.scan_youtube_playlist(line)
                            for it in items:
                                self._create_job_item(it["url"], "youtube")
                                added += 1
                            continue
                    except Exception as e:
                        self.log(f"⚠️ Không quét được playlist {line}: {e}")
                if line.startswith("http") or "youtu" in line:
                    self._create_job_item(line, "youtube")
                    added += 1

            self._refresh_queue_table()
            self.log(f"✅ Đã thêm {added} video YouTube vào hàng đợi.")
            messagebox.showinfo("Thành công", f"Đã thêm thành công {added} video YouTube vào hàng đợi!")
            pop.destroy()

        ttk.Button(btn_row, text="📋 Dán từ Clipboard", command=_paste_to_box, style="Tool.TButton").pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(btn_row, text="＋ Thêm Tất Cả Vào Hàng Đợi", command=_add_all, style="Primary.TButton").pack(side=tk.RIGHT)

    def _browse_youtube_txt_file(self):
        """Chọn file .txt chứa danh sách link YouTube để nạp hàng loạt."""
        f = filedialog.askopenfilename(
            title="Chọn file text chứa danh sách link YouTube",
            filetypes=[("Text Files", "*.txt"), ("All Files", "*.*")]
        )
        if not f:
            return
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                lines = [l.strip() for l in fp.readlines() if l.strip() and not l.strip().startswith("#")]
            added = 0
            for line in lines:
                if "playlist" in line or "list=" in line:
                    try:
                        if CompilationProcessor is not None:
                            self.log(f"🔍 Đang quét nhanh playlist: {line}...")
                            items = CompilationProcessor.scan_youtube_playlist(line)
                            for it in items:
                                self._create_job_item(it["url"], "youtube")
                                added += 1
                            continue
                    except Exception:
                        pass
                if line.startswith("http") or "youtu" in line:
                    self._create_job_item(line, "youtube")
                    added += 1
            self._refresh_queue_table()
            self.log(f"✅ Đã nạp {added} video từ file {os.path.basename(f)} vào hàng đợi.")
            messagebox.showinfo("Thành công", f"Đã nạp thành công {added} video từ file txt vào hàng đợi!")
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không thể đọc file: {e}")

    # =========================================================================
    # QUẢN LÝ HÀNG ĐỢI (QUEUE) & TIẾN TRÌNH XỬ LÝ
    # =========================================================================
    def add_current_to_queue(self):
        """Thêm video hiện tại vào hàng đợi Treeview để chia Part."""
        self._save_ui_to_config()
        mode = self.source_mode_var.get()
        src = self.local_path_entry.get().strip() if mode == "local" else self.url_entry.get().strip()

        if not src:
            messagebox.showwarning("Thiếu nguồn", "Vui lòng nhập link YouTube hoặc chọn file/thư mục video.")
            return

        # Nếu là thư mục Local, nạp từng file video bên trong
        if mode == "local" and os.path.isdir(src):
            files = [
                f for f in glob.glob(os.path.join(src, "*.*"))
                if os.path.splitext(f)[1].lower() in [".mp4", ".mkv", ".mov", ".avi", ".flv", ".ts", ".webm"]
            ]
            if not files:
                messagebox.showwarning("Không có video", f"Không tìm thấy file video nào trong: {src}")
                return
            for f in files:
                self._create_job_item(f, "local")
            self._refresh_queue_table()
            self.log(f"✅ Đã thêm {len(files)} video từ thư mục vào hàng đợi.")
            return

        # Nếu là Playlist YouTube: Quét và thêm tất cả video vào hàng đợi
        if mode == "youtube" and ("playlist" in src or "list=" in src):
            if messagebox.askyesno(
                "Phát hiện Playlist YouTube",
                "Bạn vừa nhập link Playlist YouTube!\n\nBạn có muốn quét và thêm TẤT CẢ các video trong Playlist vào hàng đợi để chia Part hàng loạt không?"
            ):
                try:
                    urls = []
                    if DownloaderProcessor is not None and hasattr(DownloaderProcessor, "extract_playlist_urls"):
                        urls = DownloaderProcessor.extract_playlist_urls(src)
                    if not urls:
                        urls = [src]
                    for u in urls:
                        self._create_job_item(u, "youtube")
                    self._refresh_queue_table()
                    self.log(f"✅ Đã thêm {len(urls)} video từ playlist vào hàng đợi.")
                    messagebox.showinfo("Thành công", f"Đã thêm thành công {len(urls)} video vào hàng đợi!")
                    return
                except Exception as e:
                    self.log(f"⚠️ Lỗi quét playlist: {e}")
            return

        # Nếu là nhiều link YouTube dán cùng lúc
        if mode == "youtube":
            urls = [u.strip() for u in src.split() if u.strip().startswith("http") or "youtu" in u.strip()]
            if len(urls) > 1:
                for u in urls:
                    self._create_job_item(u, "youtube")
                self._refresh_queue_table()
                self.log(f"✅ Đã thêm {len(urls)} video YouTube vào hàng đợi.")
                messagebox.showinfo("Thành công", f"Đã thêm thành công {len(urls)} video YouTube vào hàng đợi!")
                return

        # Thêm 1 video đơn lẻ
        self._create_job_item(src, mode)
        self._refresh_queue_table()
        self.log(f"✅ Đã thêm video vào hàng đợi: {os.path.basename(src)}")

    def _create_job_item(self, source_path: str, source_mode: str):
        self._save_ui_to_config()
        job_id = hashlib.md5(f"{source_path}_{time.time()}".encode()).hexdigest()[:8]
        editing_mode = str(self.editing_mode_var.get() if hasattr(self, "editing_mode_var") else "viral_condensed")
        target_dur = float(self.target_part_duration_var.get() if hasattr(self, "target_part_duration_var") else 90.0)
        part_cnt = int(self.part_count_var.get() if hasattr(self, "part_count_var") else 6)
        job = {
            "id": job_id,
            "name": os.path.basename(source_path) or source_path,
            "job_type": "single",
            "source": source_path,
            "source_mode": source_mode,
            "parts": part_cnt,
            "editing_mode": editing_mode,
            "target_part_duration": target_dur,
            "status": "Chờ xử lý",
            "output": "",
            "config_snapshot": deepcopy(self.config),
        }
        self.queue_items.append(job)

    def _load_queue_from_disk(self):
        """Khôi phục danh sách video chia part đã lưu từ file data/part_queue.json."""
        with self._queue_lock:
            try:
                if os.path.isfile(PART_QUEUE_FILE):
                    with open(PART_QUEUE_FILE, "r", encoding="utf-8") as f:
                        raw = json.load(f)
                    self.queue_items = raw if isinstance(raw, list) else []
                else:
                    self.queue_items = []
            except Exception as e:
                print(f"⚠️ [PART SPLITTER] Lỗi đọc {PART_QUEUE_FILE}: {e}")
                self.queue_items = []

            # Khôi phục trạng thái cho các job bị ngắt quãng khi app bị đóng đột ngột
            for job in self.queue_items:
                st = str(job.get("status", ""))
                if st.startswith("Đang ") or st in (
                    "Đang xử lý...", "Đang tải video...", "AI Gemini phân tích...",
                    "Soát mốc OpenCV & Audio...", "Đang tinh lược video...",
                    "Đang chia Part...", "Hậu kỳ CapCut & Banner...", "Đang dựng Top Playlist..."
                ):
                    job["status"] = "Chờ xử lý"
            self._save_queue_to_disk()

    def _save_queue_to_disk(self):
        """Ghi danh sách hàng đợi chia part xuống đĩa an toàn (atomic write qua .tmp)."""
        with self._queue_lock:
            try:
                folder = os.path.dirname(os.path.abspath(PART_QUEUE_FILE))
                os.makedirs(folder, exist_ok=True)
                tmp_file = PART_QUEUE_FILE + ".tmp"
                with open(tmp_file, "w", encoding="utf-8") as f:
                    json.dump(self.queue_items, f, ensure_ascii=False, indent=2)
                os.replace(tmp_file, PART_QUEUE_FILE)
            except Exception as e:
                print(f"⚠️ [PART SPLITTER] Lỗi lưu {PART_QUEUE_FILE}: {e}")

    def on_closing(self):
        """Được gọi khi đóng ứng dụng để lưu toàn bộ cấu hình UI & hàng đợi chia part."""
        try:
            self._save_ui_to_config()
            self._save_queue_to_disk()
        except Exception as e:
            print(f"⚠️ [PART SPLITTER] Lỗi lưu khi thoát: {e}")

    def on_queue_double_click(self, _event=None):
        """Nhấp đúp chuột vào job để load lại link/đường dẫn lên ô nhập nguồn."""
        sel = self.queue_tree.selection()
        if not sel:
            return
        selected_id = sel[0]
        job = next((j for j in self.queue_items if j.get("id") == selected_id), None)
        if not job:
            return
        src = job.get("source", "")
        mode = job.get("source_mode", "youtube")
        if mode == "youtube" and hasattr(self, "url_entry"):
            self.source_mode_var.set("youtube")
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, src)
            self.on_source_mode_change()
        elif mode == "local" and hasattr(self, "local_path_entry"):
            self.source_mode_var.set("local")
            self.local_path_entry.delete(0, tk.END)
            self.local_path_entry.insert(0, src)
            self.on_source_mode_change()

    def on_queue_click(self, event=None):
        """Bấm chuột vào cột Video Nguồn để sao chép link/đường dẫn."""
        if not event or self.queue_tree.identify_region(event.x, event.y) != "cell":
            return
        col = self.queue_tree.identify_column(event.x)
        if col == "#2":
            row_id = self.queue_tree.identify_row(event.y)
            job = next((j for j in self.queue_items if j.get("id") == row_id), None)
            if job and job.get("source"):
                try:
                    self.clipboard_clear()
                    self.clipboard_append(job["source"])
                    self.status_msg_var.set(f"Đã sao chép link nguồn: {job['source'][:50]}")
                except Exception:
                    pass

    def retry_selected_queue(self):
        """Đặt lại trạng thái job được chọn về 'Chờ xử lý' để chạy lại."""
        sel = self.queue_tree.selection()
        if not sel:
            messagebox.showinfo("Chọn Job", "Vui lòng chọn video cần chạy lại trong hàng đợi.")
            return
        selected_id = sel[0]
        with self._queue_lock:
            for j in self.queue_items:
                if j.get("id") == selected_id:
                    j["status"] = "Chờ xử lý"
                    j["output"] = ""
                    self.log(f"🔄 Đã đặt lại trạng thái video '{j.get('name', selected_id)}' về 'Chờ xử lý'.")
                    break
        self._refresh_queue_table()

    def clear_all_queue(self):
        """Xóa toàn bộ hàng đợi chia part sau khi xác nhận."""
        if not self.queue_items:
            return
        if self.queue_running:
            messagebox.showwarning("Đang chạy", "Hàng đợi đang xử lý, vui lòng dừng trước khi xóa tất cả.")
            return
        if messagebox.askyesno("Xóa tất cả", "Bạn có chắc muốn xóa sạch toàn bộ danh sách chia part?"):
            with self._queue_lock:
                self.queue_items.clear()
            self._refresh_queue_table()
            self.log("🗑️ Đã xóa sạch toàn bộ danh sách chia part.")

    def _refresh_queue_table(self):
        sel = self.queue_tree.selection()
        selected_id = sel[0] if sel else ""

        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)

        with self._queue_lock:
            items_snapshot = list(self.queue_items)

        for idx, job in enumerate(items_snapshot, 1):
            src_name = os.path.basename(job.get("source", "")) if job.get("source_mode") == "local" else job.get("source", "")
            part_cnt = job.get("parts", 6)
            parts_str = f"{part_cnt} Parts"
            ed_mode = job.get("editing_mode", "viral_condensed")
            if ed_mode == "viral_condensed":
                mode_str = "⭐ 3-Pass AI"
                dur_val = job.get("target_part_duration", 90.0)
                dur_str = f"~{dur_val:.0f}s"
            else:
                mode_str = "✂️ Classic"
                dur_str = "Cliffhanger"

            st = str(job.get("status", "Chờ xử lý"))
            tag = "pending"
            if st in ("Hoàn thành", "Hoàn tất"):
                tag = "completed"
            elif st in ("Lỗi", "Thất bại"):
                tag = "failed"
            elif st.startswith("Đang "):
                tag = "running"

            self.queue_tree.insert(
                "", tk.END, iid=job["id"],
                tags=(tag,),
                values=(
                    idx,
                    src_name[:40],
                    parts_str,
                    mode_str,
                    dur_str,
                    st,
                    job.get("output", ""),
                )
            )

        if selected_id and self.queue_tree.exists(selected_id):
            self.queue_tree.selection_set(selected_id)

        self._save_queue_to_disk()

    def delete_selected_queue(self):
        sel = self.queue_tree.selection()
        if not sel:
            return
        selected_id = sel[0]
        job = next((j for j in self.queue_items if j.get("id") == selected_id), None)
        job_name = job.get("name", selected_id) if job else selected_id
        if messagebox.askyesno("Xóa Job", f"Bạn có chắc muốn xóa video '{job_name}' khỏi hàng đợi?"):
            with self._queue_lock:
                self.queue_items = [j for j in self.queue_items if j.get("id") != selected_id]
            self._refresh_queue_table()
            self.log(f"🗑️ Đã xóa video khỏi hàng đợi: {job_name}")

    def move_queue_item(self, delta: int):
        sel = self.queue_tree.selection()
        if not sel:
            return
        selected_id = sel[0]
        with self._queue_lock:
            idx = next((i for i, j in enumerate(self.queue_items) if j.get("id") == selected_id), -1)
            if idx == -1:
                return
            new_idx = idx + delta
            if 0 <= new_idx < len(self.queue_items):
                self.queue_items[idx], self.queue_items[new_idx] = self.queue_items[new_idx], self.queue_items[idx]
        self._refresh_queue_table()
        if self.queue_tree.exists(selected_id):
            self.queue_tree.selection_set(selected_id)

    # =========================================================================
    # TIẾN TRÌNH THỰC THI (ENGINE WORKER THREAD)
    # =========================================================================
    def start_queue_processing(self):
        """Bắt đầu chạy tiến trình xử lý hàng đợi Chia Part."""
        if self.queue_running:
            messagebox.showinfo("Đang chạy", "Hàng đợi đang được xử lý.")
            return

        with self._queue_lock:
            pending_jobs = [j for j in self.queue_items if j.get("status") in ("Chờ xử lý", "Lỗi")]
        if not pending_jobs:
            messagebox.showinfo("Trống", "Không có video nào cần chia part. Hãy thêm video vào hàng đợi!")
            return

        self.queue_running = True
        self.stop_requested = False
        self.btn_start.config(state=tk.DISABLED)
        self.prog_bar.start(10)
        self.status_msg_var.set("Đang xử lý hàng đợi...")

        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop_queue_processing(self):
        if self.queue_running:
            self.stop_requested = True
            self.status_msg_var.set("Đang dừng sau khi hoàn tất video hiện tại...")
            self.log("⚠️ Yêu cầu dừng hàng đợi đã được gửi.")

    def _worker_loop(self):
        with self._queue_lock:
            jobs_to_run = list(self.queue_items)
        for job in jobs_to_run:
            if self.stop_requested:
                break
            if job.get("status") not in ("Chờ xử lý", "Lỗi"):
                continue

            try:
                self._process_single_job(job)
            except Exception as e:
                job["status"] = "Lỗi"
                self.log(f"❌ [LỖI] Job {job['id']} thất bại: {e}")
                self.after(0, self._refresh_queue_table)

        self.queue_running = False
        self.after(0, self._finish_queue_ui)

    def _finish_queue_ui(self):
        self.prog_bar.stop()
        self.btn_start.config(state=tk.NORMAL)
        self.status_msg_var.set("Đã hoàn tất toàn bộ hàng đợi chia part!")
        self.log("🏁 Hoàn tất toàn bộ công việc chia part.")

    def _process_single_job(self, job: Dict[str, Any]):
        cfg = job["config_snapshot"]
        job_id = job["id"]
        source = job["source"]
        source_mode = job["source_mode"]


        job["status"] = "Đang chuẩn bị..."
        self.after(0, self._refresh_queue_table)
        self.log(f"\n🚀 [BẮT ĐẦU JOB {job_id}] Nguồn: {source}")

        work_dir = os.path.abspath(f"temp/part_splitter_{job_id}")
        os.makedirs(work_dir, exist_ok=True)
        out_dir = os.path.abspath(cfg.get("output_dir", "output/parts"))
        os.makedirs(out_dir, exist_ok=True)

        video_path = source
        srt_path = ""

        # 1. Tải hoặc chuẩn bị file
        if source_mode == "youtube":
            job["status"] = "Đang tải video..."
            self.after(0, self._refresh_queue_table)
            self.log(f"📥 Đang tải source YouTube qua DownloaderProcessor...")
            if DownloaderProcessor is not None:
                dl = DownloaderProcessor(output_dir=work_dir, log_fn=self.log)
                meta = dl.download(source)
                video_path = meta.get("video_path")
                srt_path = meta.get("subtitle_path", "")
            else:
                raise RuntimeError("Không tìm thấy DownloaderProcessor.")
        else:
            if not srt_path and video_path:
                cand = os.path.splitext(video_path)[0] + ".srt"
                if os.path.isfile(cand):
                    srt_path = cand

        if not video_path or not os.path.isfile(video_path):
            raise FileNotFoundError(f"Không tìm thấy video nguồn: {video_path}")

        # 2. Đo thời lượng và chuẩn bị subtitle cues
        total_dur = probe_duration_sec(video_path)
        self.log(f"⏱️ Thời lượng video gốc: {total_dur:.1f}s ({total_dur/60:.1f} phút)")

        cues: List[Dict[str, Any]] = []
        if srt_path and os.path.isfile(srt_path):
            try:
                from broll_semantic import parse_srt
                cues = parse_srt(srt_path)
            except Exception:
                pass

        # 3. Phân tích kế hoạch với PartPrunerAI
        job["status"] = "AI Gemini phân tích..."
        self.after(0, self._refresh_queue_table)
        self.log("🤖 Gemini AI đang phân tích toàn diện (Tinh lược, bẫy tò mò, săn hook)...")

        # Cơ chế bảo vệ mốc 1 phút: Tính toán thời lượng thô tối thiểu mỗi Part cần có để sau speedup vẫn >= 61s
        speed_factor = max(1.0, float(cfg.get("speed", cfg.get("source_speed", 1.05))))
        min_final_dur = max(61.0, float(cfg.get("min_part_duration_sec", 60.0)))
        needed_raw_per_part = min_final_dur * speed_factor
        p_count = max(2, int(cfg.get("part_count", 4)))
        min_clean_dur_needed = p_count * needed_raw_per_part
        max_allowable_prune = max(0.0, total_dur - min_clean_dur_needed)

        nominal_prune = (
            total_dur * (cfg.get("prune_percent", 20.0) / 100.0)
            if cfg.get("prune_mode") == "percent"
            else cfg.get("prune_minutes", 15.0) * 60.0
        )

        prune_target = nominal_prune
        if cfg.get("prune_enabled") and nominal_prune > max_allowable_prune:
            prune_target = max_allowable_prune
            actual_pct = (prune_target / total_dur) * 100.0 if total_dur > 0 else 0.0
            req_str = f"{cfg.get('prune_percent', 20.0):.0f}%" if cfg.get("prune_mode") == "percent" else f"{cfg.get('prune_minutes', 15.0):.1f}m"
            self.log(
                f"🛡️ [BẢO VỆ MỐC 1 PHÚT] Cắt {req_str} sẽ làm video còn lại quá ngắn, khiến {p_count} Part sau khi tăng tốc {speed_factor:.2f}x bị dưới 60s!\n"
                f"   ↳ Hệ thống tự động bù/hạ mức cắt xuống {actual_pct:.1f}% ({prune_target:.1f}s) "
                f"để giữ lại ít nhất {min_clean_dur_needed:.1f}s, cam kết 100% các Part xuất xưởng đều > 1 phút!"
            )

        # Xác định raw_title của video
        raw_title = ""
        if source_mode == "youtube" and "meta" in locals() and isinstance(meta, dict):
            raw_title = meta.get("title", "")
        if not raw_title and video_path:
            raw_title = os.path.splitext(os.path.basename(video_path))[0]
        if not raw_title:
            raw_title = job.get("name", "")

        ai_cfg = dict(cfg)
        ai_cfg["min_part_duration_sec"] = needed_raw_per_part
        ai_cfg["source_speed"] = speed_factor
        ai_cfg["speed"] = speed_factor

        editing_mode = str(cfg.get("editing_mode", "viral_condensed")).strip()
        self.log(f"🎬 Chế độ biên tập: {'Dựng Tinh Hoa Đối Thủ (3-Pass AI)' if editing_mode == 'viral_condensed' else 'Cắt Khúc Cơ Bản (Classic Linear)'}")

        ai_plan = PartPrunerAI.analyze_and_plan(
            video_path=video_path,
            srt_path=srt_path,
            total_duration=total_dur,
            part_count=p_count,
            target_prune_sec=prune_target if cfg.get("prune_enabled") else 0.0,
            hook_target_sec=cfg.get("hook_target_sec", 6.0),
            gemini_client=None,
            model_name=cfg.get("gemini_model", "gemini-3.8-flash-high"),
            api_key=cfg.get("gemini_api_key", ""),
            auto_title=cfg.get("auto_regenerate_title", True),
            part_prefix=cfg.get("part_label_prefix", "Part"),
            config=ai_cfg,
            job_dir=work_dir,
            original_title=raw_title,
            log_fn=self.log
        )

        self.log(f"🎯 Tiêu đề Viral (ngôn ngữ gốc): {ai_plan.get('master_title', raw_title or 'Video')}")

        cleaned_video = video_path
        if editing_mode == "viral_condensed" and ai_plan.get("parts"):
            # CHẾ ĐỘ DỰNG TINH HOA ĐỐI THỦ: Dùng trực tiếp kịch bản micro-cuts của 3-Pass AI
            parts_info = ai_plan["parts"]
            self.log(f"🎬 [3-PASS DIRECTOR] Nhận kịch bản {len(parts_info)} Parts micro-cuts chuẩn đối thủ!")
        else:
            # CHẾ ĐỘ CẮT KHÚC CƠ BẢN (CLASSIC LINEAR): Tinh lược theo dải mốc thời gian
            self.log(f"✂️ AI đề xuất lược {len(ai_plan.get('prune_plan', []))} đoạn thừa")
            self.log(f"📍 Điểm chia Part Cliffhanger: {ai_plan.get('part_splits', [])}")

            # 4. Cắt bỏ đoạn thừa & Ghép video gốc sạch
            keep_ranges = [(0.0, total_dur)]
            if cfg.get("prune_enabled") and ai_plan.get("prune_plan"):
                job["status"] = "Đang tinh lược video..."
                self.after(0, self._refresh_queue_table)
                self.log("✂️ Đang áp dụng FFmpeg concat stream cắt bỏ đoạn thừa...")
                clean_out = os.path.join(work_dir, "cleaned_source.mp4")
                cleaned_video, keep_ranges = PartSplitterEngine.prune_and_build_cleaned_source(
                    source_video=video_path,
                    drop_ranges=ai_plan.get("prune_plan", []),
                    output_clean=clean_out,
                    log_fn=self.log
                )

            clean_dur = probe_duration_sec(cleaned_video)
            self.log(f"⏱️ Thời lượng video sạch sau tinh lược: {clean_dur:.1f}s ({clean_dur/60:.1f} phút)")

            # 5. Ánh xạ mốc chia Part sang timeline của video sạch
            mapped_splits = [
                PartSplitterEngine.map_orig_to_clean_time(pt, keep_ranges)
                for pt in ai_plan.get("part_splits", [])
            ]
            min_p_dur = needed_raw_per_part
            sanitized_splits = PartSplitterEngine.sanitize_part_splits(
                splits=mapped_splits,
                total_duration=clean_dur,
                part_count=p_count,
                min_part_dur=min_p_dur
            )
            self.log(f"📍 Điểm chia Part trên video sạch: {[round(s, 1) for s in sanitized_splits]}")

            # 6. Tinh chỉnh các mốc cắt với Scene Transitions & Silence Gap
            job["status"] = "Soát mốc OpenCV & Audio..."
            self.after(0, self._refresh_queue_table)
            refined_splits = PartSplitterEngine.refine_cuts_with_opencv_and_audio(
                ai_cuts=sanitized_splits,
                scene_map=None,
                srt_cues=cues,
                tolerance=0.5,
                radius=0.5,
                log_fn=self.log
            )

            # 7. Chia Part thô
            job["status"] = "Đang chia Part..."
            self.after(0, self._refresh_queue_table)
            self.log(f"📂 Đang chia video thành {len(refined_splits) + 1} part...")
            parts_info = PartSplitterEngine.split_into_parts(
                clean_video=cleaned_video,
                cut_points=refined_splits,
                output_dir=work_dir,
                part_label_prefix=cfg.get("part_label_prefix", "Part"),
                part_titles=ai_plan.get("part_titles", []),
                log_fn=self.log
            )

        # Xác định tên thư mục riêng cho video này trong output_dir (mỗi video 1 thư mục riêng biệt)
        def _sanitize_folder_name(name: str, max_len: int = 120) -> str:
            s = re.sub(r'[\\/:*?"<>|\r\n\t]', '_', str(name or "")).strip()
            s = re.sub(r'\s+', ' ', s)
            return s[:max_len].strip(". _-")

        ai_master = str(ai_plan.get("master_title") or "").strip()
        if ai_master and ai_master.lower() not in ("video", "unknown title", "video_task"):
            folder_title = ai_master
        elif raw_title and raw_title.lower() not in ("video", "source_video", "video_task", "unknown title"):
            folder_title = raw_title
        else:
            folder_title = job.get("name") or f"video_{job_id}"

        video_folder_name = _sanitize_folder_name(folder_title) or f"Video_Job_{job_id}"
        video_out_dir = os.path.join(out_dir, video_folder_name)
        os.makedirs(video_out_dir, exist_ok=True)
        self.log(f"📁 [THƯ MỤC XUẤT] Thư mục riêng cho video: {video_out_dir}")

        # 6.5. Single-Pass AI QC: Quét AI 1 lần duy nhất trên toàn bộ timeline của cleaned_video
        enable_gemini_qc = False
        master_blurs = []

        # Phân bổ các vùng mờ về từng part theo mốc start/end của part
        parts_blurs_map = {}
        if master_blurs is not None:
            for i, p_item in enumerate(parts_info):
                p_start = float(p_item.get("start", 0.0)) if isinstance(p_item, dict) else 0.0
                p_end = float(p_item.get("end", 0.0)) if isinstance(p_item, dict) else 0.0
                p_dur = max(0.0, p_end - p_start)
                if p_dur <= 0.0:
                    parts_blurs_map[i] = []
                    continue
                p_blurs = []
                for b in master_blurs:
                    b_s = float(b.get("start_sec", 0.0))
                    b_e = float(b.get("end_sec", 0.0))
                    if b_e > p_start and b_s < p_end:
                        b_c = dict(b)
                        b_c["start_sec"] = max(0.0, b_s - p_start)
                        b_c["end_sec"] = min(p_dur, b_e - p_start)
                        if "intervals" in b and isinstance(b["intervals"], list):
                            new_ivs = []
                            for iv in b["intervals"]:
                                iv_s, iv_e = float(iv[0]), float(iv[1])
                                if iv_e > p_start and iv_s < p_end:
                                    new_ivs.append([max(0.0, iv_s - p_start), min(p_dur, iv_e - p_start)])
                            if new_ivs:
                                b_c["intervals"] = new_ivs
                        p_blurs.append(b_c)
                parts_blurs_map[i] = p_blurs

        # 6.6. Tự động kiểm tra cấu hình máy (HIGH / MEDIUM / LOW) và lựa chọn chế độ render tối ưu
        from hardware_manager import get_part_render_strategy
        hw_strategy = get_part_render_strategy()
        max_render_workers = hw_strategy["max_parallel_workers"] if len(parts_info) > 1 else 1

        self.log(
            f"🖥️ [KIỂM TRA CẤU HÌNH MÁY] Mức: {hw_strategy['tier_label']} | "
            f"{hw_strategy['gpu_name']} ({hw_strategy['vram_gb']:.1f}GB VRAM) | "
            f"{hw_strategy['cpu_cores']} CPU cores | {hw_strategy['ram_gb']:.1f}GB RAM"
        )
        self.log(f"⚡ [CHẾ ĐỘ RENDER TỰ ĐỘNG] {hw_strategy['render_mode']} | Encoder: {hw_strategy['encoder']} | Luồng filter: {hw_strategy['filter_threads']}")

        # 7. Ráp Hook, Hậu kỳ CapCut Limiter, Tốc độ, Subtle Zoom & Banner
        # Render song song (Dual GPU nếu card >= 6GB VRAM, hoặc 1 Part tuần tự nếu card yếu/iGPU/CPU)
        if editing_mode == "viral_condensed":
            job["status"] = "[Render] Engine: Cắt nối micro-cuts & dán Top Card/Bottom Badge..."
        else:
            job["status"] = "Hậu kỳ CapCut & Banner..."
        self.after(0, self._refresh_queue_table)
        part_prefix = str(cfg.get("part_label_prefix", "Part")).strip() or "Part"

        def _render_one_part(i_idx: int, p_item: Any) -> tuple[int, str]:
            raw_part = p_item.get("raw_path", "") if isinstance(p_item, dict) else str(p_item)
            part_title = (p_item.get("title") if isinstance(p_item, dict) else None) or (
                ai_plan.get("part_titles", [])[i_idx] if i_idx < len(ai_plan.get("part_titles", [])) else f"{part_prefix} {i_idx+1}"
            )
            final_out = os.path.join(video_out_dir, f"{part_prefix} {i_idx+1}.mp4")

            hook_range = p_item.get("hook") if isinstance(p_item, dict) and p_item.get("hook") else None
            if not hook_range:
                if cfg.get("hook_mode") == "individual":
                    ind_hooks = ai_plan.get("hooks", {}).get("individual_hooks", [])
                    if i_idx < len(ind_hooks):
                        hook_range = ind_hooks[i_idx]
                elif cfg.get("hook_mode") == "shared":
                    hook_range = ai_plan.get("hooks", {}).get("global_hook")

            p_blurs = parts_blurs_map.get(i_idx, None) if master_blurs is not None else None
            part_cuts = p_item.get("cuts") if isinstance(p_item, dict) else None

            if editing_mode == "viral_condensed":
                self.log(f"🎬 [Render] Engine: Cắt nối micro-cuts & dán Top Card/Bottom Badge cho {part_prefix} {i_idx+1}/{len(parts_info)}...")
            else:
                self.log(f"🎬 Bắt đầu hậu kỳ {part_prefix} {i_idx+1}/{len(parts_info)}: Limiter +20dB, Speed {cfg.get('source_speed', 1.0)}x, Banner...")

            PartSplitterEngine.apply_part_hook_and_postprocessing(
                raw_part_path=raw_part,
                part_info=p_item if isinstance(p_item, dict) else {"raw_path": raw_part, "index": i_idx + 1, "title": part_title},
                hook_time_range=hook_range,
                source_video=video_path,
                cuts=part_cuts,
                speed=cfg.get("source_speed", 1.0),
                apply_limiter=cfg.get("apply_capcut_limiter", True),
                apply_subtle_zoom=cfg.get("apply_subtle_zoom", True),
                part_label_prefix=part_prefix,
                part_index=i_idx + 1,
                part_title=part_title,
                master_title=ai_plan.get("master_title", raw_title),
                output_final=final_out,
                config=cfg,
                log_fn=self.log,
                work_dir=work_dir,
                watermark_blurs=p_blurs,
                filter_threads=hw_strategy.get("filter_threads")
            )
            return i_idx, final_out

        self.log(f"⚡ [TIẾN HÀNH RENDER] Hậu kỳ {len(parts_info)} Parts ({max_render_workers} tiến trình - Cấp độ: {hw_strategy['tier_label']})...")

        rendered_results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_render_workers) as executor:
            future_to_idx = {
                executor.submit(_render_one_part, i, p_item): i
                for i, p_item in enumerate(parts_info)
            }
            for f in concurrent.futures.as_completed(future_to_idx):
                idx_res, out_path = f.result()
                rendered_results.append((idx_res, out_path))

        # Đảm bảo danh sách thành phẩm sắp xếp chuẩn thứ tự Part 1, Part 2, Part 3, Part 4
        rendered_results.sort(key=lambda x: x[0])
        final_part_files = [x[1] for x in rendered_results]

        # 8. Hoàn thành job
        job["status"] = "Hoàn thành"
        job["output_dir"] = video_out_dir
        job["output"] = f"{len(final_part_files)} Parts -> {video_folder_name}"
        self.after(0, self._refresh_queue_table)
        self.log(f"🎉 [THÀNH CÔNG] Job {job_id} đã xuất {len(final_part_files)} Parts vào: {video_out_dir}")

        if cfg.get("cleanup_temp_after_export"):
            try:
                import shutil
                shutil.rmtree(work_dir, ignore_errors=True)
                self.log(f"🧹 Đã xóa thư mục tạm: {work_dir}")
            except Exception:
                pass

    def log(self, message: str):
        """Ghi log vào khung ScrolledText và gửi tới log_fn."""
        def _append():
            t = time.strftime("[%H:%M:%S] ")
            self.log_text.insert(tk.END, t + message + "\n")
            self.log_text.see(tk.END)
            if callable(self.log_fn):
                try:
                    self.log_fn(message)
                except Exception:
                    pass
        self.after(0, _append)
