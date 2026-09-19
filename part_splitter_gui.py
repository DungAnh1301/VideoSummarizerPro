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

try:
    from compilation_processor import CompilationProcessor
except ImportError:
    CompilationProcessor = None


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
        """Khởi tạo toàn bộ biến trạng thái giao diện theo đúng PLAN_CHIA_PART.md."""
        c = self.config

        # --- Chế độ làm (Workflow Mode) ---
        self.workflow_mode_var = tk.StringVar(value=c.get("workflow_mode", "single"))
        self.comp_source_type_var = tk.StringVar(value=c.get("compilation_source_type", "playlist"))
        self.comp_teaser_var = tk.BooleanVar(value=bool(c.get("compilation_enable_teaser", False)))
        self.comp_target_dur_var = tk.DoubleVar(value=float(c.get("compilation_target_duration", 180.0)))
        self.comp_rank_by_var = tk.StringVar(value=c.get("compilation_rank_by", "views"))

        # --- Tab 1: Nguồn ---
        self.source_mode_var = tk.StringVar(value=c.get("source_mode", "youtube"))
        self.output_dir_var = tk.StringVar(value=c.get("output_dir", "output/parts"))
        self.hook_mode_var = tk.StringVar(value=c.get("hook_mode", "individual"))
        self.hook_duration_var = tk.DoubleVar(value=float(c.get("hook_target_sec", 6.0)))
        self.hook_range_label_var = tk.StringVar()

        # --- Tab 2: AI Gemini & Chiến Lược Chia Part ---
        self.google_status_var = tk.StringVar(value="● Đang kiểm tra phiên Google...")
        self.gemini_model_var = tk.StringVar(value=c.get("gemini_model", "gemini-2.5-flash"))
        self.gemini_api_key_var = tk.StringVar(value=c.get("gemini_api_key", ""))
        self.auto_title_var = tk.BooleanVar(value=bool(c.get("auto_regenerate_title", True)))

        self.prune_enabled_var = tk.BooleanVar(value=bool(c.get("prune_enabled", True)))
        self.prune_mode_var = tk.StringVar(value=c.get("prune_mode", "percent"))
        self.prune_percent_var = tk.DoubleVar(value=float(c.get("prune_percent", 20.0)))
        self.prune_minutes_var = tk.DoubleVar(value=float(c.get("prune_minutes", 15.0)))
        self.prune_range_label_var = tk.StringVar()

        self.part_count_var = tk.IntVar(value=int(c.get("part_count", 4)))

        # --- Tab 3: Hậu kỳ Video Gốc ---
        self.source_speed_var = tk.DoubleVar(value=float(c.get("source_speed", 1.05)))
        self.blur_var = tk.BooleanVar(value=bool(c.get("blur_bg", True)))
        self.zoom_var = tk.BooleanVar(value=bool(c.get("zoom_in", False)))
        self.capcut_limiter_var = tk.BooleanVar(value=bool(c.get("apply_capcut_limiter", True)))
        self.subtle_zoom_var = tk.BooleanVar(value=bool(c.get("apply_subtle_zoom", True)))
        self.random_mirror_var = tk.BooleanVar(value=bool(c.get("random_mirror", False)))
        self.part_prefix_var = tk.StringVar(value=c.get("part_label_prefix", "Part"))
        self.gemini_grid_inspector_var = tk.BooleanVar(value=bool(c.get("gemini_grid_inspector", True)))
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

        # Hàng 0: Chọn Chế độ làm (Workflow Mode)
        workflow_row = ttk.Frame(f1)
        workflow_row.grid(row=0, column=0, columnspan=6, sticky=tk.EW, pady=(0, 4))
        ttk.Label(workflow_row, text="Chế độ làm:", font=("Segoe UI Semibold", 9)).pack(side=tk.LEFT, padx=(2, 6))
        self.rb_workflow_single = ttk.Radiobutton(
            workflow_row, text="1 Video đơn lẻ (Cắt Hook & Chia Part)", variable=self.workflow_mode_var, value="single",
            command=self.on_workflow_mode_change
        )
        self.rb_workflow_single.pack(side=tk.LEFT, padx=(0, 12))
        self.rb_workflow_compilation = ttk.Radiobutton(
            workflow_row, text="Tuyển tập TOP Countdown (No. N ➔ No. 1 / Top Highlight)", variable=self.workflow_mode_var, value="compilation",
            command=self.on_workflow_mode_change
        )
        self.rb_workflow_compilation.pack(side=tk.LEFT)

        # ==================== KHUNG 1: 1 VIDEO ĐƠN LẺ ====================
        self.single_mode_frame = ttk.Frame(f1)
        self.single_mode_frame.grid(row=1, column=0, columnspan=6, sticky=tk.EW, pady=(0, 2))
        self.single_mode_frame.columnconfigure(1, weight=1)

        # Hàng 1.0: YouTube / Đa Link
        ttk.Label(self.single_mode_frame, text="YouTube:").grid(row=0, column=0, sticky=tk.W, padx=2, pady=2)
        self.url_entry = ttk.Entry(self.single_mode_frame, width=42)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        self.url_entry.insert(0, self.config.get("youtube_url", "https://www.youtube.com/watch?v=..."))

        yt_btn_row = ttk.Frame(self.single_mode_frame)
        yt_btn_row.grid(row=0, column=2, columnspan=4, sticky=tk.W, padx=2, pady=2)
        self.btn_paste = ttk.Button(yt_btn_row, text="📋 Dán", command=self._paste_clipboard, style="Tool.TButton")
        self.btn_paste.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_multi_link = ttk.Button(yt_btn_row, text="📑 Đa Link", command=self._open_multi_link_popup, style="Tool.TButton")
        self.btn_multi_link.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_txt_file = ttk.Button(yt_btn_row, text="📄 File TXT", command=self._browse_youtube_txt_file, style="Tool.TButton")
        self.btn_txt_file.pack(side=tk.LEFT)

        # Hàng 1.1: Nguồn YouTube XOR Local Video
        mode_row = ttk.Frame(self.single_mode_frame)
        mode_row.grid(row=1, column=0, columnspan=6, sticky=tk.W, pady=(2, 2))
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

        # Hàng 1.2: Local file / folder
        ttk.Label(self.single_mode_frame, text="Local:").grid(row=2, column=0, sticky=tk.W, padx=2, pady=2)
        self.local_path_entry = ttk.Entry(self.single_mode_frame, width=42)
        self.local_path_entry.grid(row=2, column=1, sticky=tk.EW, padx=5, pady=2)
        self.local_path_entry.insert(0, self.config.get("local_source_path", ""))

        local_btn_row = ttk.Frame(self.single_mode_frame)
        local_btn_row.grid(row=2, column=2, columnspan=4, sticky=tk.W, padx=2, pady=2)
        self.btn_browse_file = ttk.Button(local_btn_row, text="📄 File", command=self._browse_source_file, style="Tool.TButton")
        self.btn_browse_file.pack(side=tk.LEFT, padx=(0, 3))
        self.btn_browse_folder = ttk.Button(local_btn_row, text="📁 Folder", command=self._browse_source_folder, style="Tool.TButton")
        self.btn_browse_folder.pack(side=tk.LEFT)

        # Hàng 1.3: Kiểu Hook & Nút Hook Studio
        hook_row = ttk.Frame(self.single_mode_frame)
        hook_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(4, 2))
        ttk.Label(hook_row, text="Kiểu hook:").pack(side=tk.LEFT, padx=(0, 6))

        for label, val in (
            ("Tự chọn mốc", "native"),
            ("Gemini chọn cao trào (Toàn cảnh)", "shared"),
            ("Gemini chọn hook riêng (Từng Part)", "individual"),
            ("Hook AI", "ai"),
        ):
            ttk.Radiobutton(
                hook_row, text=label, variable=self.hook_mode_var, value=val,
                command=self.on_hook_mode_change
            ).pack(side=tk.LEFT, padx=(0, 8))

        self.btn_hook_studio = ttk.Button(
            hook_row, text="🛠 Hook Studio", command=self.open_custom_hook_studio_popup, style="Tool.TButton"
        )
        self.btn_hook_studio.pack(side=tk.RIGHT, padx=(4, 0))

        # Hàng 1.4: Thời lượng Hook & Mốc Hook
        time_row = ttk.Frame(self.single_mode_frame)
        time_row.grid(row=4, column=0, columnspan=6, sticky=tk.W, padx=2, pady=2)
        ttk.Label(time_row, text="Mốc hook:").pack(side=tk.LEFT, padx=(0, 4))
        self.hook_time_entry = ttk.Entry(time_row, width=8)
        self.hook_time_entry.pack(side=tk.LEFT, padx=(0, 8))
        self.hook_time_entry.insert(0, self.config.get("hook_time", "11:55"))

        ttk.Label(time_row, text="Thời lượng chuẩn:").pack(side=tk.LEFT, padx=(0, 4))
        self.hook_spin = ttk.Spinbox(time_row, from_=2.0, to=30.0, increment=0.5, textvariable=self.hook_duration_var, width=5)
        self.hook_spin.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(time_row, text="giây").pack(side=tk.LEFT, padx=(0, 10))

        self.lbl_hook_calc = ttk.Label(time_row, textvariable=self.hook_range_label_var, foreground="#2563EB", font=("Segoe UI Semibold", 9))
        self.lbl_hook_calc.pack(side=tk.LEFT)

        # ==================== KHUNG 2: TUYỂN TẬP PLAYLIST YOUTUBE ====================
        self.compilation_mode_frame = ttk.LabelFrame(f1, text=" 🏆 Tuyển tập Playlist YouTube (Top Highlight No. N ➔ No. 1) ", padding=8)
        self.compilation_mode_frame.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(0, 2))
        self.compilation_mode_frame.columnconfigure(1, weight=1)

        # Hàng 2.0: Link Playlist + Dán + Xem trước
        comp_url_row = ttk.Frame(self.compilation_mode_frame)
        comp_url_row.grid(row=0, column=0, columnspan=6, sticky=tk.EW, pady=(2, 3))
        ttk.Label(comp_url_row, text="Playlist URL:").pack(side=tk.LEFT, padx=(2, 6))
        self.comp_source_path_entry = ttk.Entry(comp_url_row, width=46)
        self.comp_source_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self.comp_source_path_entry.insert(0, self.config.get("compilation_source_path", self.config.get("playlist_url", "")))
        self.btn_comp_paste = ttk.Button(comp_url_row, text="📋 Dán", command=self._paste_comp_clipboard, style="Tool.TButton")
        self.btn_comp_paste.pack(side=tk.LEFT, padx=(0, 6))
        self.btn_comp_preview = ttk.Button(
            comp_url_row, text="👁 Xem Trước Top Clips",
            command=self.open_compilation_preview_popup, style="Primary.TButton"
        )
        self.btn_comp_preview.pack(side=tk.LEFT)

        # Hàng 2.1: Số clip Top & Thời lượng video tổng
        comp_settings_row = ttk.Frame(self.compilation_mode_frame)
        comp_settings_row.grid(row=1, column=0, columnspan=6, sticky=tk.EW, pady=(3, 3))
        ttk.Label(comp_settings_row, text="Số clip Top:").pack(side=tk.LEFT, padx=(2, 4))
        self.comp_clip_count_spin = ttk.Spinbox(comp_settings_row, from_=2, to=20, width=4)
        self.comp_clip_count_spin.pack(side=tk.LEFT, padx=(0, 14))
        self.comp_clip_count_spin.set(self.config.get("compilation_clip_count", 5))

        ttk.Label(comp_settings_row, text="Thời lượng video:").pack(side=tk.LEFT, padx=(0, 4))
        self.comp_target_dur_spin = ttk.Spinbox(
            comp_settings_row, from_=30.0, to=1800.0, increment=15.0,
            textvariable=self.comp_target_dur_var, width=6
        )
        self.comp_target_dur_spin.pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(comp_settings_row, text="giây").pack(side=tk.LEFT, padx=(0, 14))

        ttk.Label(comp_settings_row, text="Xếp No.1 theo:").pack(side=tk.LEFT, padx=(0, 4))
        self.comp_rank_by_cb = ttk.Combobox(
            comp_settings_row,
            textvariable=self.comp_rank_by_var,
            values=["Lượt xem (Views)", "Lượt thích (Likes)", "Ngẫu nhiên (Random)"],
            state="readonly",
            width=18
        )
        self.comp_rank_by_cb.pack(side=tk.LEFT, padx=(0, 10))
        r_init = self.config.get("compilation_rank_by", "views")
        if r_init == "likes":
            self.comp_rank_by_cb.set("Lượt thích (Likes)")
        elif r_init == "random":
            self.comp_rank_by_cb.set("Ngẫu nhiên (Random)")
        else:
            self.comp_rank_by_cb.set("Lượt xem (Views)")

        ttk.Label(
            comp_settings_row, text="(±20% linh hoạt theo nhịp cao trào)",
            foreground="#2563EB", font=("Segoe UI Semibold", 8)
        ).pack(side=tk.LEFT)

        # Hàng 2.2: Quy chuẩn Title 2 tầng & Xếp hạng
        comp_info_row = ttk.Frame(self.compilation_mode_frame)
        comp_info_row.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(2, 2))
        ttk.Label(
            comp_info_row,
            text="🏷️ Title: Trên cùng = Tên Playlist | Giữa màn hình = No. X : Tên video",
            foreground="#1749A3", font=("Segoe UI Semibold", 8)
        ).pack(side=tk.LEFT, padx=(2, 12))
        ttk.Label(
            comp_info_row,
            text="📊 Thứ tự: No. N ➔ No. 1 (Theo view/like/random) | 🔊 Âm thanh gốc",
            foreground="#4B5563", font=("Segoe UI", 8)
        ).pack(side=tk.LEFT)

        # Hàng 3: Thư mục xuất thành phẩm
        out_row = ttk.Frame(f1)
        out_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(6, 2))
        ttk.Label(out_row, text="Thư mục xuất:").pack(side=tk.LEFT, padx=(0, 6))
        self.output_dir_entry = ttk.Entry(out_row, textvariable=self.output_dir_var, width=48)
        self.output_dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        ttk.Button(out_row, text="📂 Chọn...", command=self._browse_output_dir, style="Tool.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(out_row, text="📁 Mở Folder", command=self.open_output_folder, style="Tool.TButton").pack(side=tk.LEFT)

        self.on_source_mode_change()
        self.on_hook_mode_change()
        self.on_workflow_mode_change()
        self.on_comp_source_type_change()

    # =========================================================================
    # TAB 2: AI GEMINI & CHIẾN LƯỢC CHIA PART (PLAN_CHIA_PART.md)
    # =========================================================================
    def _build_tab2(self):
        f2 = self.tab2
        f2.columnconfigure(3, weight=1)

        # Hàng 0: Google Gemini Local Frame
        google_box = ttk.LabelFrame(f2, text="  GOOGLE GEMINI LOCAL  ", padding=(7, 4))
        google_box.grid(row=0, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 6))
        google_box.columnconfigure(1, weight=1)

        if self.app and hasattr(self.app, "google_status_var"):
            status_var = self.app.google_status_var
        else:
            status_var = self.google_status_var

        self.google_status_label = ttk.Label(google_box, textvariable=status_var, foreground="#B56A09")
        self.google_status_label.grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=(2, 8))

        self.btn_google_login = ttk.Button(
            google_box, text="🔐 Đăng nhập / đổi tài khoản", command=self.open_google_login, style="Primary.TButton"
        )
        self.btn_google_login.grid(row=0, column=2, padx=3)

        self.btn_google_check = ttk.Button(
            google_box, text="↻ Kiểm tra", command=lambda: self.check_google_login(manual=True), style="Tool.TButton"
        )
        self.btn_google_check.grid(row=0, column=3, padx=3)

        # Model & API Key & Checkbox Viral Title
        sub_row = ttk.Frame(google_box)
        sub_row.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(3, 2))
        ttk.Label(sub_row, text="Mô hình AI:").pack(side=tk.LEFT, padx=(2, 4))
        self.model_cb = ttk.Combobox(
            sub_row, textvariable=self.gemini_model_var,
            values=["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash", "gemini-3.8-flash-high"],
            width=20, state="readonly"
        )
        self.model_cb.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Label(sub_row, text="API Key (dự phòng):").pack(side=tk.LEFT, padx=(0, 4))
        self.api_key_entry = ttk.Entry(sub_row, textvariable=self.gemini_api_key_var, width=18, show="*")
        self.api_key_entry.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            sub_row, text="☑ Tự đặt lại Tiêu đề Viral theo đúng ngôn ngữ gốc", variable=self.auto_title_var
        ).pack(side=tk.LEFT)

        # Hàng 1: Cấu hình Tinh Lược (Pruning +-20%) - Dành cho 1 video đơn lẻ
        self.prune_box = ttk.LabelFrame(f2, text="  ✂️ CẤU HÌNH TINH LƯỢC VIDEO THỪA (PRUNING)  ", padding=6)
        self.prune_box.grid(row=1, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 6))
        self.prune_box.columnconfigure(2, weight=1)

        ttk.Checkbutton(
            self.prune_box, text="☑ Bật tính năng lược đoạn thừa / mở đầu lan man / quảng cáo",
            variable=self.prune_enabled_var
        ).grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=2)

        mode_row = ttk.Frame(self.prune_box)
        mode_row.grid(row=1, column=0, columnspan=3, sticky=tk.W, pady=2)
        ttk.Radiobutton(mode_row, text="Lược theo tỷ lệ %:", variable=self.prune_mode_var, value="percent").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Spinbox(mode_row, from_=5.0, to=80.0, increment=5.0, textvariable=self.prune_percent_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(mode_row, text="%    |    Lược theo số phút:").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Radiobutton(mode_row, text="", variable=self.prune_mode_var, value="minutes").pack(side=tk.LEFT)
        ttk.Spinbox(mode_row, from_=1.0, to=60.0, increment=1.0, textvariable=self.prune_minutes_var, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(mode_row, text="phút").pack(side=tk.LEFT)

        ttk.Label(self.prune_box, textvariable=self.prune_range_label_var, foreground="#16825D", font=("Segoe UI Semibold", 9)).grid(
            row=2, column=0, columnspan=3, sticky=tk.W, pady=(2, 0)
        )

        # Hàng 2: Cấu hình Chia Part & Cliffhanger (+-50%) - Dành cho 1 video đơn lẻ
        self.split_box = ttk.LabelFrame(f2, text="  📍 QUY TẮC CHIA PART & BẪY TÒ MÒ (CLIFFHANGER)  ", padding=6)
        self.split_box.grid(row=2, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 4))
        s_row = ttk.Frame(self.split_box)
        s_row.pack(fill=tk.X, pady=4)
        ttk.Label(s_row, text="Số Part cần chia:").pack(side=tk.LEFT, padx=(0, 6))
        self.part_count_spin = ttk.Spinbox(s_row, from_=2, to=20, increment=1, textvariable=self.part_count_var, width=5)
        self.part_count_spin.pack(side=tk.LEFT, padx=4)
        ttk.Label(s_row, text="Parts (Mỗi Part đảm bảo > 1.0 phút, dao động ±50% tại điểm Cliffhanger)").pack(side=tk.LEFT, padx=8)

        # Khung thông tin riêng cho chế độ Tuyển tập Playlist YouTube
        self.compilation_tab2_box = ttk.LabelFrame(f2, text="  🏆 AI TUYỂN TẬP PLAYLIST (TOP HIGHLIGHT)  ", padding=8)
        self.compilation_tab2_box.grid(row=1, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 6))

        for icon, txt in (
            ("⏱️", "Thời lượng & Hạn ngạch: Tự động điều phối theo ô 'Thời lượng video' tại Tab 1 (Nguồn)."),
            ("✂️", "Tự động tinh lược: AI tự dò tìm đoạn cao trào nhất và cắt bỏ intro/outro/dead-air của từng video."),
            ("🌿", "Dung sai ±20%: Linh hoạt theo nhịp cao trào và ngắt câu tự nhiên (không ép cứng số giây)."),
            ("🚫", "Không chia Part & Không lược theo %: Ghép nối liền mạch No. N ➔ No. 1, không chia part cliffhanger."),
        ):
            r_item = ttk.Frame(self.compilation_tab2_box)
            r_item.pack(fill=tk.X, pady=2)
            ttk.Label(r_item, text=icon, width=3).pack(side=tk.LEFT)
            ttk.Label(r_item, text=txt, foreground="#1E40AF", font=("Segoe UI Semibold", 9)).pack(side=tk.LEFT)

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

        # Hàng 2: CapCut Limiter, Subtle Zoom, Lật gương hình ảnh
        effects_row = ttk.Frame(f3)
        effects_row.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Checkbutton(
            effects_row, text="☑ CapCut Limiter +20dB (Compressor chuẩn)", variable=self.capcut_limiter_var
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            effects_row, text="☑ Subtle Zoom (1.03x chống quét bản quyền)", variable=self.subtle_zoom_var
        ).pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            effects_row, text="☐ Lật gương video (Random Mirror)", variable=self.random_mirror_var
        ).pack(side=tk.LEFT)

        # Hàng 3.1: Tiền tố nhãn Part quốc tế (Dành cho 1 video đơn lẻ)
        self.prefix_row = ttk.Frame(f3)
        self.prefix_row.grid(row=3, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Label(self.prefix_row, text="Tiền tố nhãn Part:").pack(side=tk.LEFT, padx=(0, 4))
        self.prefix_entry = ttk.Entry(self.prefix_row, textvariable=self.part_prefix_var, width=12)
        self.prefix_entry.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(
            self.prefix_row,
            text="(Ví dụ: 'Part', 'Teil', 'Partie', 'Часть', 'Tập'...) Font tự động khớp Unicode quốc tế.",
            foreground="#64748B"
        ).pack(side=tk.LEFT)

        # Hàng 3.2: Khung thông tin Title riêng cho Tuyển tập Playlist YouTube
        self.compilation_prefix_info_row = ttk.Frame(f3)
        self.compilation_prefix_info_row.grid(row=3, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Label(
            self.compilation_prefix_info_row,
            text="🏷️ Title chế độ Tuyển tập: Tự động in Tên Playlist trên cùng & 'No. X : Tên video' ở giữa (Không dùng tiền tố Part).",
            foreground="#1E40AF", font=("Segoe UI Semibold", 8)
        ).pack(side=tk.LEFT)

        # Hàng 4: Quét lưới AI xóa logo & sub cũ + Slider độ mờ 10-100%
        clean_row = ttk.Frame(f3)
        clean_row.grid(row=4, column=0, columnspan=6, sticky=tk.W, pady=(3, 2))
        ttk.Checkbutton(
            clean_row, text="🔍 Quét lưới AI xóa logo & sub cũ", variable=self.gemini_grid_inspector_var
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Label(clean_row, text="Độ mờ che:").pack(side=tk.LEFT, padx=(6, 4))
        self.qc_blur_strength_lbl = ttk.Label(
            clean_row,
            text=f"{self.qc_blur_strength_var.get()}%",
            font=("Segoe UI", 9, "bold"),
            foreground="#2563EB",
            width=5
        )
        def _on_ps_qc_blur(val):
            try:
                v = int(float(val))
                self.qc_blur_strength_var.set(v)
                self.qc_blur_strength_lbl.config(text=f"{v}%")
            except Exception:
                pass

        self.qc_blur_scale = ttk.Scale(
            clean_row,
            from_=10,
            to=100,
            value=self.qc_blur_strength_var.get(),
            orient=tk.HORIZONTAL,
            length=100,
            command=_on_ps_qc_blur
        )
        self.qc_blur_scale.pack(side=tk.LEFT, padx=(0, 2))
        self.qc_blur_strength_lbl.pack(side=tk.LEFT, padx=(0, 10))

        ttk.Checkbutton(
            clean_row, text="Xóa thư mục tạm sau khi xuất video", variable=self.cleanup_temp_var
        ).pack(side=tk.LEFT)

        # Hàng 5: Bộ 3 nút công cụ Studio (Crop, Color, Title & Sub)
        studio_row = ttk.Frame(f3)
        studio_row.grid(row=5, column=0, columnspan=6, sticky=tk.EW, pady=(6, 2))
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

        cols = ("num", "source", "parts", "prune", "hook", "status", "output")
        self.queue_tree = ttk.Treeview(queue_box, columns=cols, show="headings", height=5)
        headings = {
            "num": "#", "source": "Video Nguồn", "parts": "Số Part",
            "prune": "Tinh lược", "hook": "Hook", "status": "Trạng thái", "output": "Thành phẩm"
        }
        widths = {"num": 34, "source": 250, "parts": 60, "prune": 90, "hook": 95, "status": 130, "output": 150}
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

            self.gemini_model_var.set(c.get("gemini_model", "gemini-2.5-flash"))
            self.gemini_api_key_var.set(c.get("gemini_api_key", ""))
            self.auto_title_var.set(bool(c.get("auto_regenerate_title", True)))

            self.prune_enabled_var.set(bool(c.get("prune_enabled", True)))
            self.prune_mode_var.set(c.get("prune_mode", "percent"))
            self.prune_percent_var.set(float(c.get("prune_percent", 20.0)))
            self.prune_minutes_var.set(float(c.get("prune_minutes", 15.0)))

            self.part_count_var.set(int(c.get("part_count", 4)))

            spd = float(c.get("speed") or c.get("source_speed") or 1.05)
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

            self.workflow_mode_var.set(c.get("workflow_mode", "single"))
            self.comp_source_type_var.set(c.get("compilation_source_type", "playlist"))
            if hasattr(self, "comp_source_path_entry"):
                self.comp_source_path_entry.delete(0, tk.END)
                self.comp_source_path_entry.insert(0, c.get("compilation_source_path", c.get("playlist_url", "")))
            if hasattr(self, "comp_clip_count_spin"):
                self.comp_clip_count_spin.set(c.get("compilation_clip_count", 5))
            if hasattr(self, "comp_target_dur_var"):
                self.comp_target_dur_var.set(float(c.get("compilation_target_duration", 180.0) or 180.0))
            if hasattr(self, "comp_title_style_cb"):
                title_style_val = c.get("compilation_title_style", "clean_original")
                if title_style_val == "ai_generated":
                    self.comp_title_style_cb.set("AI tự sáng chế giật gân (Clickbait)")
                elif title_style_val == "only_rank":
                    self.comp_title_style_cb.set("Chỉ hiện số No. (Không hiện tiêu đề)")
                else:
                    self.comp_title_style_cb.set("Tiêu đề gốc làm sạch (Clean Title)")
            self.comp_teaser_var.set(bool(c.get("compilation_enable_teaser", True)))
            if hasattr(self, "comp_teaser_duration_spin"):
                self.comp_teaser_duration_spin.set(float(c.get("compilation_teaser_duration", 3.0) or 3.0))
            if hasattr(self, "comp_rank_by_cb"):
                rank_by_val = c.get("compilation_rank_by", "views")
                if rank_by_val == "likes":
                    self.comp_rank_by_cb.set("Lượt thích (Likes)")
                elif rank_by_val == "random":
                    self.comp_rank_by_cb.set("Ngẫu nhiên (Random)")
                else:
                    self.comp_rank_by_cb.set("Lượt xem (Views)")

            self.on_source_mode_change()
            self.on_hook_mode_change()
            self.on_workflow_mode_change()
            self.on_comp_source_type_change()
            self._update_calc_labels()
        except Exception as e:
            print(f"⚠️ [PART SPLITTER] Lỗi nạp config lên UI: {e}")

    def _save_ui_to_config(self):
        """Lấy toàn bộ dữ liệu từ widgets cập nhật vào self.config và lưu JSON."""
        c = self.config
        c["workflow_mode"] = self.workflow_mode_var.get() if hasattr(self, "workflow_mode_var") else "single"
        c["compilation_source_type"] = self.comp_source_type_var.get() if hasattr(self, "comp_source_type_var") else "playlist"
        if hasattr(self, "comp_source_path_entry"):
            c["compilation_source_path"] = self.comp_source_path_entry.get().strip()
            c["playlist_url"] = self.comp_source_path_entry.get().strip()
        if hasattr(self, "comp_clip_count_spin"):
            try:
                c["compilation_clip_count"] = int(self.comp_clip_count_spin.get() or 5)
            except Exception:
                c["compilation_clip_count"] = 5
        if hasattr(self, "comp_target_dur_var"):
            try:
                c["compilation_target_duration"] = float(self.comp_target_dur_var.get() or 180.0)
            except Exception:
                c["compilation_target_duration"] = 180.0
        if hasattr(self, "comp_title_style_cb"):
            cb_txt = self.comp_title_style_cb.get().lower()
            if "không hiện" in cb_txt or "chỉ hiện" in cb_txt or "only" in cb_txt:
                c["compilation_title_style"] = "only_rank"
            elif "giật gân" in cb_txt or "ai" in cb_txt:
                c["compilation_title_style"] = "ai_generated"
            else:
                c["compilation_title_style"] = "clean_original"
        c["compilation_enable_teaser"] = bool(self.comp_teaser_var.get()) if hasattr(self, "comp_teaser_var") else True
        if hasattr(self, "comp_teaser_duration_spin"):
            try:
                c["compilation_teaser_duration"] = float(self.comp_teaser_duration_spin.get() or 3.0)
            except Exception:
                c["compilation_teaser_duration"] = 3.0
        if hasattr(self, "comp_rank_by_cb"):
            rb_txt = self.comp_rank_by_cb.get().lower()
            if "thích" in rb_txt or "like" in rb_txt:
                c["compilation_rank_by"] = "likes"
            elif "nhiên" in rb_txt or "random" in rb_txt:
                c["compilation_rank_by"] = "random"
            else:
                c["compilation_rank_by"] = "views"

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

        c["prune_enabled"] = bool(self.prune_enabled_var.get())
        c["prune_mode"] = self.prune_mode_var.get()
        c["prune_percent"] = float(self.prune_percent_var.get())
        c["prune_minutes"] = float(self.prune_minutes_var.get())

        c["part_count"] = int(self.part_count_var.get())

        spd_val = float(self.source_speed_var.get() or 1.05)
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

    def on_workflow_mode_change(self):
        mode = self.workflow_mode_var.get() if hasattr(self, "workflow_mode_var") else "single"
        if mode == "compilation":
            # Tab 1: Nguồn
            if hasattr(self, "single_mode_frame"):
                self.single_mode_frame.grid_remove()
            if hasattr(self, "compilation_mode_frame"):
                self.compilation_mode_frame.grid()

            # Tab 2: AI & Chiến lược (Ẩn prune %/phút & quy tắc chia part, hiện khung AI Tuyển tập)
            if hasattr(self, "prune_box"):
                self.prune_box.grid_remove()
            if hasattr(self, "split_box"):
                self.split_box.grid_remove()
            if hasattr(self, "compilation_tab2_box"):
                self.compilation_tab2_box.grid()

            # Tab 3: Hậu kỳ (Ẩn tiền tố nhãn part, hiện nhãn Title Tuyển tập)
            if hasattr(self, "prefix_row"):
                self.prefix_row.grid_remove()
            if hasattr(self, "compilation_prefix_info_row"):
                self.compilation_prefix_info_row.grid()
        else:
            # Tab 1: Nguồn
            if hasattr(self, "compilation_mode_frame"):
                self.compilation_mode_frame.grid_remove()
            if hasattr(self, "single_mode_frame"):
                self.single_mode_frame.grid()

            # Tab 2: AI & Chiến lược (Hiện lại prune %/phút & quy tắc chia part)
            if hasattr(self, "compilation_tab2_box"):
                self.compilation_tab2_box.grid_remove()
            if hasattr(self, "prune_box"):
                self.prune_box.grid()
            if hasattr(self, "split_box"):
                self.split_box.grid()

            # Tab 3: Hậu kỳ (Hiện lại tiền tố nhãn part)
            if hasattr(self, "compilation_prefix_info_row"):
                self.compilation_prefix_info_row.grid_remove()
            if hasattr(self, "prefix_row"):
                self.prefix_row.grid()

    def on_comp_source_type_change(self):
        src_type = self.comp_source_type_var.get() if hasattr(self, "comp_source_type_var") else "playlist"
        if hasattr(self, "lbl_comp_path"):
            self.lbl_comp_path.config(text="Thư mục:" if src_type == "local_folder" else "Playlist URL:")
        if hasattr(self, "btn_browse_comp_folder"):
            self.btn_browse_comp_folder.config(state=tk.NORMAL if src_type == "local_folder" else tk.DISABLED)

    def browse_comp_source_folder(self):
        path = filedialog.askdirectory(title="Chọn thư mục chứa các video ngắn làm Top Highlight")
        if path:
            self.comp_source_path_entry.delete(0, tk.END)
            self.comp_source_path_entry.insert(0, path)

    def open_compilation_preview_popup(self):
        source_path = self.comp_source_path_entry.get().strip() if hasattr(self, "comp_source_path_entry") else ""
        if not source_path:
            messagebox.showwarning("Thiếu nguồn", "Vui lòng dán Link YouTube Playlist trước!")
            return

        try:
            count = int(self.comp_clip_count_spin.get() or 5)
        except ValueError:
            count = 5

        try:
            target_dur = float(self.comp_target_dur_var.get() or 180.0)
        except Exception:
            target_dur = 180.0

        popup = tk.Toplevel(self)
        popup.title("Xem Trước Danh Sách Tuyển Tập Top (Countdown Preview)")
        popup.geometry("960x540")
        popup.transient(self.winfo_toplevel())
        popup.grab_set()

        initial_rank_by = "views"
        if hasattr(self, "comp_rank_by_cb"):
            rb_val = self.comp_rank_by_cb.get().lower()
            if "thích" in rb_val or "like" in rb_val:
                initial_rank_by = "likes"
            elif "nhiên" in rb_val or "random" in rb_val:
                initial_rank_by = "random"
            else:
                initial_rank_by = "views"
        else:
            initial_rank_by = self.config.get("compilation_rank_by", "views")

        lbl_status = ttk.Label(popup, text="⏳ Đang quét danh sách Playlist YouTube...", font=("Segoe UI", 10, "italic"))
        lbl_status.pack(pady=10)

        tree_frame = ttk.Frame(popup, padding=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("rank", "badge", "views", "likes", "title", "orig_dur", "pruned_dur")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10)
        tree.heading("rank", text="#")
        tree.heading("badge", text="Thứ Hạng")
        tree.heading("views", text="Lượt Xem")
        tree.heading("likes", text="Lượt Thích")
        tree.heading("title", text="Tiêu Đề Clip")
        tree.heading("orig_dur", text="Thời Lượng Gốc")
        tree.heading("pruned_dur", text="Cắt Tinh Lược")

        tree.column("rank", width=35, anchor=tk.CENTER)
        tree.column("badge", width=75, anchor=tk.CENTER)
        tree.column("views", width=95, anchor=tk.E)
        tree.column("likes", width=95, anchor=tk.E)
        tree.column("title", width=380, anchor=tk.W)
        tree.column("orig_dur", width=95, anchor=tk.CENTER)
        tree.column("pruned_dur", width=95, anchor=tk.CENTER)

        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        tree.configure(yscrollcommand=sb.set)

        def _format_views(v):
            if v is None or v == "":
                return "—"
            try:
                v = int(v)
                if v >= 1_000_000:
                    return f"{v / 1_000_000:.1f}M views"
                if v >= 1_000:
                    return f"{v / 1_000:.0f}K views"
                return f"{v:,} views"
            except Exception:
                return str(v)

        def _format_likes(l):
            if l is None or l == "":
                return "—"
            try:
                l = int(l)
                if l >= 1_000_000:
                    return f"{l / 1_000_000:.1f}M likes"
                if l >= 1_000:
                    return f"{l / 1_000:.1f}K likes"
                return f"{l:,} likes"
            except Exception:
                return str(l)

        def _format_dur(d):
            try:
                d = float(d or 0)
                m = int(d // 60)
                s = int(d % 60)
                return f"{m:02d}:{s:02d}"
            except Exception:
                return "--:--"

        raw_entries_holder = []
        current_rank_by_holder = [initial_rank_by]

        def _scan_and_populate(mode=None):
            if mode:
                current_rank_by_holder[0] = mode
            active_mode = current_rank_by_holder[0]
            popup.after(0, lambda: tree.delete(*tree.get_children()))
            popup.after(0, lambda: lbl_status.config(text=f"⏳ Đang xếp hạng clip theo {active_mode.upper()}..."))
            try:
                if CompilationProcessor is None:
                    popup.after(0, lambda: lbl_status.config(text="❌ Không tìm thấy CompilationProcessor!"))
                    return

                if not raw_entries_holder:
                    raw = CompilationProcessor.scan_youtube_playlist(source_path)
                    raw_entries_holder.extend(raw)

                if not raw_entries_holder:
                    popup.after(0, lambda: lbl_status.config(text="❌ Không tìm thấy video nào trong Playlist đã chọn!"))
                    return

                ranked = CompilationProcessor.pick_and_rank_clips(raw_entries_holder, count=count, rank_by=active_mode)

                raw_durs = [float(it.get("duration") or 0.0) for it in ranked]
                if any(d > 0 for d in raw_durs):
                    safe_durs = [d if d > 0 else (target_dur / max(1, len(ranked))) for d in raw_durs]
                    alloc_durs = CompilationProcessor.calculate_adaptive_clip_durations(safe_durs, target_dur)
                else:
                    alloc_durs = [target_dur / max(1, len(ranked))] * len(ranked)

                def _update_tree():
                    playlist_title = ranked[0].get("playlist_title", "TOP HIGHLIGHT") if ranked else "TOP HIGHLIGHT"
                    for idx, (it, alloc_d) in enumerate(zip(ranked, alloc_durs), 1):
                        orig_d = float(it.get("duration") or 0.0)
                        if orig_d > 0 and orig_d <= alloc_d + 0.5:
                            pruned_txt = f"{orig_d:.0f}s (Toàn bộ)"
                        else:
                            pruned_txt = f"~{alloc_d:.0f}s (AI)"
                        tree.insert("", tk.END, values=(
                            idx,
                            f"No. {it['rank']}",
                            _format_views(it.get("view_count")),
                            _format_likes(it.get("like_count")),
                            it.get("title", ""),
                            _format_dur(orig_d),
                            pruned_txt,
                        ))
                    if active_mode == "likes":
                        rule_text = "Xếp No.1 là clip nhiều LIKE nhất"
                    elif active_mode == "random":
                        rule_text = "Xếp RANDOM ngẫu nhiên thứ tự No. N ➔ No. 1"
                    else:
                        rule_text = "Xếp No.1 là clip nhiều VIEW nhất"
                    lbl_status.config(
                        text=f"📂 Playlist: {playlist_title} | Tổng: {target_dur:.0f}s ({len(ranked)} clips, AI tự bù trừ) | {rule_text}"
                    )

                popup.after(0, _update_tree)
            except Exception as e:
                popup.after(0, lambda: lbl_status.config(text=f"❌ Lỗi: {e}"))

        btn_box = ttk.Frame(popup, padding=10)
        btn_box.pack(fill=tk.X)
        ttk.Button(btn_box, text="🎲 Bốc Ngẫu Nhiên Lại (Reshuffle)", command=lambda: threading.Thread(target=lambda: _scan_and_populate(), daemon=True).start(), style="Tool.TButton").pack(side=tk.LEFT, padx=5)

        ttk.Label(btn_box, text="Xếp No.1:").pack(side=tk.LEFT, padx=(15, 4))
        preview_rank_cb = ttk.Combobox(
            btn_box,
            values=["Lượt xem (Views)", "Lượt thích (Likes)", "Ngẫu nhiên (Random)"],
            state="readonly", width=18
        )
        if initial_rank_by == "likes":
            preview_rank_cb.set("Lượt thích (Likes)")
        elif initial_rank_by == "random":
            preview_rank_cb.set("Ngẫu nhiên (Random)")
        else:
            preview_rank_cb.set("Lượt xem (Views)")
        preview_rank_cb.pack(side=tk.LEFT, padx=(0, 5))

        def _on_preview_rank_change(_e=None):
            val = preview_rank_cb.get().lower()
            if "thích" in val or "like" in val:
                new_r = "likes"
            elif "nhiên" in val or "random" in val:
                new_r = "random"
            else:
                new_r = "views"
            if hasattr(self, "comp_rank_by_cb"):
                self.comp_rank_by_cb.set(preview_rank_cb.get())
            threading.Thread(target=lambda: _scan_and_populate(new_r), daemon=True).start()

        preview_rank_cb.bind("<<ComboboxSelected>>", _on_preview_rank_change)
        ttk.Button(btn_box, text="Đóng", command=popup.destroy, style="Tool.TButton").pack(side=tk.RIGHT, padx=5)

        threading.Thread(target=lambda: _scan_and_populate(), daemon=True).start()

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
            print("💾 [PART SPLITTER] Đã lưu cấu hình Studio độc lập vào config_part_splitter.json!")

    def _sync_from_parent_after_studio(self):
        pass

    def open_crop_tool_popup(self):
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
        """Thêm job hiện tại vào hàng đợi Treeview (Hỗ trợ URL đơn, Playlist, Thư mục và Tuyển tập Top Highlight)."""
        self._save_ui_to_config()
        workflow_mode = self.workflow_mode_var.get() if hasattr(self, "workflow_mode_var") else "single"

        # 1. Chế độ Tuyển tập TOP Countdown / Playlist Highlight
        if workflow_mode == "compilation":
            comp_path = self.comp_source_path_entry.get().strip() if hasattr(self, "comp_source_path_entry") else ""
            if not comp_path:
                messagebox.showwarning("Thiếu nguồn", "Vui lòng nhập Link Playlist YouTube.")
                return

            try:
                clip_count = int(self.comp_clip_count_spin.get() or 5)
            except ValueError:
                clip_count = 5

            try:
                target_dur = float(self.comp_target_dur_var.get() or 180.0)
            except Exception:
                target_dur = 180.0

            job_id = hashlib.md5(f"comp_{comp_path}_{time.time()}".encode()).hexdigest()[:8]
            display_name = f"TOP {clip_count} PLAYLIST: {comp_path}"
            quota = target_dur / max(1, clip_count)
            job = {
                "id": job_id,
                "name": display_name,
                "job_type": "playlist_highlight",
                "workflow_mode": "compilation",
                "source": comp_path,
                "source_mode": "youtube",
                "compilation_source_type": "playlist",
                "compilation_source_path": comp_path,
                "compilation_clip_count": clip_count,
                "compilation_target_duration": target_dur,
                "compilation_rank_by": self.config.get("compilation_rank_by", "views"),
                "parts": clip_count,
                "prune": f"{target_dur:.0f}s (~{quota:.0f}s/c)",
                "hook": f"No. {clip_count} ➔ No. 1 ({'View' if self.config.get('compilation_rank_by', 'views') == 'views' else ('Like' if self.config.get('compilation_rank_by', 'views') == 'likes' else 'Random')})",
                "status": "Chờ xử lý",
                "output": "",
                "config_snapshot": deepcopy(self.config),
            }
            self.queue_items.append(job)
            self._refresh_queue_table()
            self.log(f"✅ Đã thêm tuyển tập Top {clip_count} Playlist Highlight vào hàng đợi: {display_name} (Tổng: {target_dur:.0f}s)")
            return

        # 2. Chế độ 1 Video đơn lẻ
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

        # Nếu người dùng nhập Playlist YouTube ở chế độ đơn lẻ
        if mode == "youtube" and ("playlist" in src or "list=" in src):
            choice = messagebox.askyesnocancel(
                "Phát hiện Playlist YouTube",
                "Bạn vừa nhập link Playlist YouTube!\n\n"
                "- Bấm YES: Chuyển sang chế độ Tuyển tập Top Highlight (chọn ra số clip Top để xếp hạng No.N -> No.1).\n"
                "- Bấm NO: Quét và thêm TẤT CẢ các video trong Playlist vào hàng đợi để chia part riêng lẻ.\n"
                "- Bấm CANCEL: Hủy bỏ."
            )
            if choice is True:
                self.workflow_mode_var.set("compilation")
                self.comp_source_type_var.set("playlist")
                self.comp_source_path_entry.delete(0, tk.END)
                self.comp_source_path_entry.insert(0, src)
                self.on_workflow_mode_change()
                self.on_comp_source_type_change()
                self.log(f"🔄 Đã chuyển sang chế độ Tuyển tập Top Highlight cho Playlist: {src}")
                return
            elif choice is False:
                if CompilationProcessor is not None:
                    self.log(f"🔍 Đang quét nhanh playlist YouTube: {src}...")
                    try:
                        items = CompilationProcessor.scan_youtube_playlist(src)
                        if not items:
                            messagebox.showwarning("Không có video", "Không tìm thấy video nào trong playlist này.")
                            return
                        for it in items:
                            self._create_job_item(it["url"], "youtube")
                        self._refresh_queue_table()
                        self.log(f"✅ Đã thêm {len(items)} video từ playlist vào hàng đợi.")
                        messagebox.showinfo("Thành công", f"Đã quét và thêm thành công {len(items)} video vào hàng đợi!")
                        return
                    except Exception as e:
                        self.log(f"⚠️ Lỗi quét playlist: {e}")
            else:
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
        job = {
            "id": job_id,
            "name": os.path.basename(source_path) or source_path,
            "job_type": "single",
            "workflow_mode": "single",
            "source": source_path,
            "source_mode": source_mode,
            "parts": int(self.part_count_var.get()),
            "prune": f"{self.prune_percent_var.get():.0f}%" if self.prune_mode_var.get() == "percent" else f"{self.prune_minutes_var.get():.0f}m",
            "hook": self.hook_mode_var.get(),
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
        wf = job.get("workflow_mode", "single")
        if wf == "compilation" and hasattr(self, "comp_source_path_entry"):
            self.workflow_mode_var.set("compilation")
            self.comp_source_path_entry.delete(0, tk.END)
            self.comp_source_path_entry.insert(0, src)
            self.on_workflow_mode_change()
        else:
            mode = job.get("source_mode", "youtube")
            if hasattr(self, "workflow_mode_var"):
                self.workflow_mode_var.set("single")
                self.on_workflow_mode_change()
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
            if job.get("job_type") == "compilation" or job.get("workflow_mode") == "compilation":
                src_name = f"🏆 Top {job.get('compilation_clip_count', 5)}: {os.path.basename(job.get('source', '')) if job.get('source_mode') == 'local' else job.get('source', '')}"
                parts_str = f"{job.get('compilation_clip_count', 5)} Top"
                prune_str = "Countdown"
                hook_str = "No. N ➔ 1"
            else:
                src_name = os.path.basename(job.get("source", "")) if job.get("source_mode") == "local" else job.get("source", "")
                parts_str = str(job.get("parts", 4))
                cfg_snap = job.get("config_snapshot", {})
                prune_str = job.get("prune", "") if cfg_snap.get("prune_enabled", True) else "Tắt"
                hook_str = str(job.get("hook", "individual"))

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
                    prune_str,
                    hook_str,
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

        # Nếu là job Tuyển tập Top Countdown / Playlist Highlight
        if job.get("job_type") in ("playlist_highlight", "compilation") or job.get("workflow_mode") == "compilation":
            clip_count = int(job.get("compilation_clip_count", 5) or 5)
            target_dur = float(job.get("compilation_target_duration", 180.0) or 180.0)
            self.log(f"\n🏆 [BẮT ĐẦU TOP PLAYLIST HIGHLIGHT {job_id}] Nguồn: {source} (Top {clip_count} clips, Mục tiêu: {target_dur:.0f}s)")
            job["status"] = "Đang dựng Top Playlist..."
            self.after(0, self._refresh_queue_table)

            if CompilationProcessor is None:
                raise RuntimeError("Không tìm thấy CompilationProcessor.")

            def _progress_cb(jid, st, txt):
                job["status"] = txt
                self.after(0, self._refresh_queue_table)
                self.log(f"[{st}] {txt}")

            comp_job = dict(job)
            comp_job.setdefault("url", source)
            comp_job["post_options"] = job.get("config_snapshot", {})
            comp_job["target_duration"] = target_dur
            comp_job["clip_count"] = clip_count

            final_out = CompilationProcessor.execute_playlist_highlight_job(comp_job, progress_callback=_progress_cb)
            job["status"] = "Hoàn thành"
            job["output"] = os.path.basename(final_out) if final_out else "Hoàn thành"
            self.after(0, self._refresh_queue_table)
            self.log(f"🎉 [THÀNH CÔNG] Đã xuất bản video Tuyển tập Playlist Highlight: {final_out}")
            return

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

        prune_target = (
            total_dur * (cfg.get("prune_percent", 20.0) / 100.0)
            if cfg.get("prune_mode") == "percent"
            else cfg.get("prune_minutes", 15.0) * 60.0
        )

        ai_plan = PartPrunerAI.analyze_and_plan(
            video_path=video_path,
            srt_path=srt_path,
            total_duration=total_dur,
            part_count=cfg.get("part_count", 4),
            target_prune_sec=prune_target if cfg.get("prune_enabled") else 0.0,
            hook_target_sec=cfg.get("hook_target_sec", 6.0),
            gemini_client=None,
            model_name=cfg.get("gemini_model", "gemini-2.5-flash"),
            api_key=cfg.get("gemini_api_key", ""),
            auto_title=cfg.get("auto_regenerate_title", True),
            part_prefix=cfg.get("part_label_prefix", "Part"),
            config=cfg,
            job_dir=work_dir,
            log_fn=self.log
        )

        self.log(f"🎯 Tiêu đề Viral (ngôn ngữ gốc): {ai_plan.get('master_title', 'Video')}")
        self.log(f"✂️ AI đề xuất lược {len(ai_plan.get('prune_plan', []))} đoạn thừa")
        self.log(f"📍 Điểm chia Part Cliffhanger: {ai_plan.get('part_splits', [])}")

        # 4. Cắt bỏ đoạn thừa & Ghép video gốc sạch
        cleaned_video = video_path
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

        # 5. Ánh xạ mốc chia Part sang timeline của video sạch & bảo đảm mỗi Part >= 60s
        mapped_splits = [
            PartSplitterEngine.map_orig_to_clean_time(pt, keep_ranges)
            for pt in ai_plan.get("part_splits", [])
        ]
        min_p_dur = max(60.0, float(cfg.get("min_part_duration_sec", 60.0)))
        sanitized_splits = PartSplitterEngine.sanitize_part_splits(
            splits=mapped_splits,
            total_duration=clean_dur,
            part_count=cfg.get("part_count", 4),
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

        # 7. Chia Part
        job["status"] = "Đang chia Part..."
        self.after(0, self._refresh_queue_table)
        self.log(f"📂 Đang chia video thành {cfg.get('part_count', 4)} part...")
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

        raw_title = ""
        if source_mode == "youtube" and "meta" in locals() and isinstance(meta, dict):
            raw_title = meta.get("title", "")
        if not raw_title and video_path:
            raw_title = os.path.splitext(os.path.basename(video_path))[0]
        if not raw_title:
            raw_title = job.get("name", "")

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

        # 7. Ráp Hook, Hậu kỳ CapCut Limiter, Tốc độ, Subtle Zoom & Banner
        job["status"] = "Hậu kỳ CapCut & Banner..."
        self.after(0, self._refresh_queue_table)
        final_part_files = []
        part_prefix = str(cfg.get("part_label_prefix", "Part")).strip() or "Part"

        for i, p_item in enumerate(parts_info):
            raw_part = p_item["raw_path"] if isinstance(p_item, dict) else str(p_item)
            part_title = (p_item.get("title") if isinstance(p_item, dict) else None) or (
                ai_plan.get("part_titles", [])[i] if i < len(ai_plan.get("part_titles", [])) else f"{part_prefix} {i+1}"
            )
            # Tên file video xuất sạch sẽ trong thư mục riêng: Part 1.mp4, Part 2.mp4...
            final_out = os.path.join(video_out_dir, f"{part_prefix} {i+1}.mp4")

            # Xác định hook cho Part
            hook_range = None
            if cfg.get("hook_mode") == "individual":
                ind_hooks = ai_plan.get("hooks", {}).get("individual_hooks", [])
                if i < len(ind_hooks):
                    hook_range = ind_hooks[i]
            elif cfg.get("hook_mode") == "shared":
                hook_range = ai_plan.get("hooks", {}).get("global_hook")

            self.log(f"🎬 Hậu kỳ {part_prefix} {i+1}/{len(parts_info)}: Limiter +20dB, Speed {cfg.get('source_speed', 1.05)}x, Banner...")
            PartSplitterEngine.apply_part_hook_and_postprocessing(
                raw_part_path=raw_part,
                hook_time_range=hook_range,
                speed=cfg.get("source_speed", 1.05),
                apply_limiter=cfg.get("apply_capcut_limiter", True),
                apply_subtle_zoom=cfg.get("apply_subtle_zoom", True),
                part_label_prefix=part_prefix,
                part_index=i + 1,
                part_title=part_title,
                output_final=final_out,
                config=cfg,
                log_fn=self.log,
                work_dir=work_dir
            )
            final_part_files.append(final_out)

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
