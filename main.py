import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox, filedialog
from PIL import Image, ImageTk, ImageFilter
import threading
import sys
import os
import subprocess
import json
import shutil
import hashlib
import pygame  # Import luôn để chặn dòng chào rác của pygame nếu cần
from copy import deepcopy
from batch_queue import JobQueue
from hardware_manager import detect_hardware, get_hardware_profile, set_console_visible
from update_manager import check_for_update, current_version, download_update, launch_updater

# Windows có thể mở app với stdout cp1252; log Unicode khi đó làm
# thread tải voice chết dù GUI vẫn còn. Chuẩn hoá ngay khi khởi động.
for _stream_name in ("stdout", "stderr"):
    _stream = getattr(sys, _stream_name, None)
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class WaitingForAIHook(Exception):
    """Job đã chuẩn bị source/AI/voice xong và đang chờ người dùng gắn Hook AI."""

try:
    from ensure_runtime import apply_runtime_env
    apply_runtime_env()
except Exception:
    pass

from config_manager import (load_config, normalize_config, normalize_hook_mode,
                            normalize_mode_for_voice, save_config)
from antigravity_processor import AntigravityProcessor
from part_processor import PartProcessor
from market_profiles import MARKET_PROFILES, get_market_profile, market_code_from_label

try:
    from downloader_processor import DownloaderProcessor
except ImportError:
    messagebox.showerror("Lỗi", "Không tìm thấy file downloader_processor.py!")
    sys.exit(1)

try:
    from local_source_processor import LocalSourceProcessor
except ImportError:
    LocalSourceProcessor = None

try:
    from ai_processor import AIProcessor
except ImportError:
    pass

try:
    from editor_processor import EditorProcessor
except ImportError:
    pass

try:
    from editor_ai import EditorAI
except ImportError:
    try:
        from edittor_ai import EditorAI
    except ImportError:
        EditorAI = None

try:
    from part_splitter_gui import PartSplitterFrame
except ImportError:
    PartSplitterFrame = None

class PrintLogger:
    def __init__(self, text_widget):
        self.text_widget = text_widget

    def write(self, message):
        self.text_widget.insert(tk.END, message)
        self.text_widget.see(tk.END)

    def flush(self):
        pass

class ProfessionalVideoApp:
    @staticmethod
    def _format_elapsed(seconds):
        seconds = max(0, int(round(float(seconds or 0))))
        minutes, remain = divmod(seconds, 60)
        if minutes:
            return f"{minutes} phút {remain:02d} giây"
        return f"{remain} giây"

    @classmethod
    def _print_timing_report(cls, source_seconds, ai_seconds, render_seconds, total_seconds):
        print("\n⏱️ [THỜI GIAN TỪNG CÔNG ĐOẠN]")
        print(f"📥 Tải / chuẩn bị source : {cls._format_elapsed(source_seconds)}")
        print(f"🤖 AI + giọng + time-sub : {cls._format_elapsed(ai_seconds)}")
        print(f"🎬 Render video           : {cls._format_elapsed(render_seconds)}")
        print(f"🏁 Tổng thời gian thực    : {cls._format_elapsed(total_seconds)}")

    def __init__(self, root):
        self.root = root
        self.root.title("Video Automator Pro — Production Studio")
        self.root.configure(bg="#EEF1F5", padx=8, pady=6)
        
        self.config = load_config()
        self.tts_database = {}
        self.job_queue = JobQueue()
        self.queue_running = False
        self.stop_after_current = False
        self.editing_job_id = None
        
        self.setup_styles()
        self.setup_ui()
        
        # Load cấu hình đã lưu vào toàn bộ widget trên giao diện ngay khi khởi động
        self.load_configs_to_ui()
        self._refresh_named_config_list()
        sel_name = self.config.get("selected_config", "")
        if sel_name and sel_name in self.config.get("saved_configs", {}):
            try:
                self.on_named_config_selected(_event=object())
            except Exception:
                pass
        self.refresh_queue_table()
        self.update_cookie_badge()
        self._fit_window_to_screen()
        if bool(self.config.get("auto_check_updates", True)):
            self.root.after(1800, lambda: self.check_updates(manual=False))
        
    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        self.colors = {
            "canvas": "#EEF1F5", "surface": "#FFFFFF", "navy": "#14213D",
            "blue": "#2563EB", "blue_hover": "#1D4ED8", "text": "#172033",
            "muted": "#64748B", "line": "#DCE2EA", "danger": "#C93C37",
            "success": "#16825D", "warning": "#B56A09",
        }
        c = self.colors
        style.configure("TFrame", background=c["surface"])
        style.configure("App.TFrame", background=c["canvas"])
        style.configure("Header.TFrame", background=c["navy"])
        style.configure("HeaderTitle.TLabel", background=c["navy"], foreground="#FFFFFF",
                        font=("Segoe UI Semibold", 13))
        style.configure("HeaderMeta.TLabel", background=c["navy"], foreground="#B8C5DD",
                        font=("Segoe UI", 9))
        style.configure("TLabel", background=c["surface"], foreground=c["text"],
                        font=("Segoe UI", 9))
        style.configure("Muted.TLabel", foreground=c["muted"], font=("Segoe UI", 8))
        style.configure("Summary.TLabel", foreground=c["navy"], font=("Segoe UI Semibold", 9))
        style.configure("TLabelframe", background=c["surface"], bordercolor=c["line"],
                        lightcolor=c["line"], darkcolor=c["line"], relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=c["surface"], foreground=c["navy"],
                        font=("Segoe UI Semibold", 10), padding=(4, 0, 4, 0))
        style.configure("TButton", font=("Segoe UI Semibold", 9), padding=(8, 5),
                        background="#F5F7FA", foreground=c["text"], bordercolor=c["line"])
        style.map("TButton", background=[("active", "#E9EEF5"), ("pressed", "#DDE5EF")])
        style.configure("Primary.TButton", background=c["blue"], foreground="#FFFFFF",
                        bordercolor=c["blue"], padding=(10, 6))
        style.map("Primary.TButton", background=[("active", c["blue_hover"]),
                                                  ("pressed", "#173FA5")])
        style.configure("Success.TButton", background=c["success"], foreground="#FFFFFF",
                        bordercolor=c["success"])
        style.map("Success.TButton", background=[("active", "#106C4D")])
        style.configure("Danger.TButton", background="#FFF3F2", foreground=c["danger"],
                        bordercolor="#F1C8C5")
        style.map("Danger.TButton", background=[("active", "#FDE5E3")])
        style.configure("Tool.TButton", background="#F8FAFC", foreground=c["navy"],
                        bordercolor="#CBD5E1", padding=(8, 5))
        style.configure("TEntry", fieldbackground="#F8FAFC", foreground=c["text"],
                        bordercolor="#CBD5E1", lightcolor="#CBD5E1", padding=4)
        style.configure("TCombobox", fieldbackground="#F8FAFC", foreground=c["text"],
                        bordercolor="#CBD5E1", lightcolor="#CBD5E1", padding=3)
        style.configure("TSpinbox", fieldbackground="#F8FAFC", foreground=c["text"],
                        bordercolor="#CBD5E1", padding=3)
        style.configure("TCheckbutton", background=c["surface"], foreground=c["text"], padding=3)
        style.configure("TRadiobutton", background=c["surface"], foreground=c["text"], padding=3)
        style.configure("Treeview", background="#FFFFFF", fieldbackground="#FFFFFF",
                        foreground=c["text"], rowheight=25, borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Treeview.Heading", background="#E8EDF4", foreground=c["navy"],
                        relief="flat", padding=(5, 5), font=("Segoe UI Semibold", 9))
        style.map("Treeview", background=[("selected", "#DCE8FF")],
                  foreground=[("selected", c["navy"])])
        style.configure("TNotebook", background=c["canvas"], borderwidth=0, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background="#E2E7EE", foreground=c["muted"],
                        padding=(14, 6), font=("Segoe UI Semibold", 9))
        style.map("TNotebook.Tab", background=[("selected", c["surface"]), ("active", "#EDF2F7")],
                  foreground=[("selected", c["navy"])])

    def _fit_window_to_screen(self):
        """Mở rộng chiều cao và chiều rộng để thấy full danh sách video đang render / hoàn thành."""
        self.root.update_idletasks()
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        taskbar = 70
        max_w = max(900, screen_w - 16)
        max_h = max(600, screen_h - taskbar)
        w = min(max_w, 840)
        h = min(max_h, max(820, int(screen_h * 0.88)))
        self.root.resizable(True, True)
        self.root.minsize(740, 580)
        self.root.maxsize(screen_w, max_h)
        self.root.geometry(f"{w}x{h}+8+8")

    def setup_ui(self):
        # Khởi tạo biến thời lượng chung cho toàn bộ ứng dụng (Đồng bộ Tab 1 và Tab 2)
        init_wf = self.config.get("workflow_mode", "single")
        init_dur = (
            self.config.get("compilation_target_duration")
            if init_wf == "compilation"
            else self.config.get("video_min_duration_sec")
        ) or self.config.get("video_min_duration_sec") or self.config.get("compilation_target_duration") or (180 if init_wf == "compilation" else 90)
        self.video_duration_var = tk.StringVar(value=str(int(init_dur)))
        self.comp_hint_var = tk.StringVar()
        self.word_budget_var = tk.StringVar()
        self.video_duration_var.trace_add("write", lambda *args: self.update_word_budget_label())

        # Bốn khối cấu hình giữ chiều cao tự nhiên; danh sách luôn có vùng riêng
        # và nhận toàn bộ không gian còn lại khi resize cửa sổ.
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1, minsize=220)

        header = ttk.Frame(self.root, style="Header.TFrame", padding=(10, 4))
        header.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))
        ttk.Label(header, text="VIDEO AUTOMATOR PRO", style="HeaderTitle.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text="AI VIDEO STUDIO", style="HeaderMeta.TLabel").pack(side=tk.RIGHT, pady=(2, 0))
        ttk.Button(
            header, text=f"Update · v{current_version()}",
            command=lambda: self.check_updates(manual=True), style="Tool.TButton",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        ttk.Button(
            header, text="Kiểm tra máy", command=self.recheck_hardware,
            style="Tool.TButton",
        ).pack(side=tk.RIGHT, padx=(0, 8))
        self.btn_cookie = ttk.Button(
            header, text="🍪 Cookie",
            command=self.open_youtube_cookie_dialog, style="Tool.TButton",
        )
        self.btn_cookie.pack(side=tk.RIGHT, padx=(0, 8))
        self.show_cmd_var = tk.BooleanVar(value=bool(self.config.get("show_cmd_log", True)))
        ttk.Checkbutton(
            header, text="Hiện CMD log", variable=self.show_cmd_var,
            command=self.on_cmd_log_toggle,
        ).pack(side=tk.RIGHT, padx=(0, 12))

        # --- THANH GẠT CHUYỂN ĐỔI CHẾ ĐỘ (MODE SWITCHER) ---
        mode_bar = ttk.Frame(self.root, style="Header.TFrame", padding=(8, 3))
        mode_bar.grid(row=1, column=0, sticky=tk.EW, pady=(0, 4))
        ttk.Label(mode_bar, text="CHẾ ĐỘ:", style="HeaderMeta.TLabel", font=("Segoe UI Semibold", 9)).pack(side=tk.LEFT, padx=(4, 6))

        self.app_mode_var = tk.StringVar(value="summarizer")
        self.btn_mode_summarizer = ttk.Button(
            mode_bar, text="🎙️ Tóm Tắt & Voice AI",
            command=lambda: self.switch_app_mode("summarizer"),
            style="Primary.TButton"
        )
        self.btn_mode_summarizer.pack(side=tk.LEFT, padx=3)

        self.btn_mode_splitter = ttk.Button(
            mode_bar, text="✂️ Chia Part & Tinh Lược Video Gốc",
            command=lambda: self.switch_app_mode("splitter"),
            style="Tool.TButton"
        )
        self.btn_mode_splitter.pack(side=tk.LEFT, padx=3)

        # Container cho Chế độ 1: Tóm Tắt & Voice AI (Đóng băng nguyên vẹn 100% logic cũ)
        self.summarizer_container = ttk.Frame(self.root, style="App.TFrame")
        self.summarizer_container.grid(row=2, column=0, sticky=tk.NSEW)
        self.summarizer_container.columnconfigure(0, weight=1)
        self.summarizer_container.rowconfigure(2, weight=1, minsize=220)

        config_tabs = ttk.Notebook(self.summarizer_container)
        config_tabs.grid(row=0, column=0, sticky=tk.EW, pady=(0, 4))

        # --- PHẦN 1: TẢI VIDEO & CẮT HOOK ---
        f1 = ttk.Frame(config_tabs, padding=7)
        config_tabs.add(f1, text="  1  Nguồn  ")
        f1.columnconfigure(1, weight=1)
        
                # --- CHỌN CHẾ ĐỘ LÀM: 1 VIDEO ĐƠN LẺ vs TUYỂN TẬP TOP COUNTDOWN ---
        workflow_row = ttk.Frame(f1)
        workflow_row.grid(row=0, column=0, columnspan=6, sticky=tk.EW, pady=(0, 4))
        ttk.Label(workflow_row, text="Chế độ làm:", font=("Segoe UI Semibold", 9)).pack(side=tk.LEFT, padx=(2, 6))
        self.workflow_mode_var = tk.StringVar(value=self.config.get("workflow_mode", "single"))
        self.rb_workflow_single = ttk.Radiobutton(
            workflow_row, text="1 Video đơn lẻ (Truyền thống)", variable=self.workflow_mode_var, value="single",
            command=self.on_workflow_mode_change
        )
        self.rb_workflow_single.pack(side=tk.LEFT, padx=(0, 12))
        self.rb_workflow_compilation = ttk.Radiobutton(
            workflow_row, text="Tuyển tập TOP Countdown (No. N ➔ No. 1)", variable=self.workflow_mode_var, value="compilation",
            command=self.on_workflow_mode_change
        )
        self.rb_workflow_compilation.pack(side=tk.LEFT)
        self.btn_cookie_badge = ttk.Button(
            workflow_row, text="🍪 Cookie: Kiểm tra...", command=self.open_youtube_cookie_dialog,
            style="Tool.TButton"
        )
        self.btn_cookie_badge.pack(side=tk.RIGHT, padx=(4, 2))

        # KHUNG 1: 1 VIDEO ĐƠN LẺ (SINGLE MODE)
        self.single_mode_frame = ttk.Frame(f1)
        self.single_mode_frame.grid(row=1, column=0, columnspan=6, sticky=tk.EW, pady=(0, 2))
        self.single_mode_frame.columnconfigure(1, weight=1)

        ttk.Label(self.single_mode_frame, text="YouTube:").grid(row=0, column=0, sticky=tk.W, padx=2)
        self.url_entry = ttk.Entry(self.single_mode_frame, width=72)
        self.url_entry.grid(row=0, column=1, columnspan=5, sticky=tk.EW, padx=5)
        self.url_entry.insert(0, self.config.get("youtube_url", "https://www.youtube.com/watch?v=..."))
        
        ttk.Label(self.single_mode_frame, text="Mốc hook:").grid(row=1, column=0, sticky=tk.W, padx=2)
        self.hook_time_entry = ttk.Entry(self.single_mode_frame, width=10)
        self.hook_time_entry.grid(row=1, column=1, sticky=tk.W, padx=5)
        self.hook_time_entry.insert(0, self.config.get("hook_time", "11:55"))
        
        ttk.Label(self.single_mode_frame, text="Giây:").grid(row=1, column=2, sticky=tk.W, padx=(8, 2))
        self.hook_duration_entry = ttk.Entry(self.single_mode_frame, width=6)
        self.hook_duration_entry.grid(row=1, column=3, sticky=tk.W, padx=5)
        self.hook_duration_entry.insert(0, self.config.get("hook_duration", "9.0"))

        # Nguồn: YouTube XOR Video có sẵn
        self.source_mode_var = tk.StringVar(value=self.config.get("source_mode", "youtube"))
        mode_row = ttk.Frame(self.single_mode_frame)
        mode_row.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(2, 1))
        ttk.Label(mode_row, text="Nguồn:").pack(side=tk.LEFT, padx=(2, 8))
        self.rb_source_youtube = ttk.Radiobutton(
            mode_row, text="YouTube", variable=self.source_mode_var, value="youtube",
            command=self.on_source_mode_change
        )
        self.rb_source_youtube.pack(side=tk.LEFT, padx=(0, 10))
        self.rb_source_local = ttk.Radiobutton(
            mode_row, text="Video có sẵn", variable=self.source_mode_var, value="local",
            command=self.on_source_mode_change
        )
        self.rb_source_local.pack(side=tk.LEFT)

        ttk.Label(self.single_mode_frame, text="Local:").grid(row=3, column=0, sticky=tk.W, padx=2, pady=1)
        self.local_path_entry = ttk.Entry(self.single_mode_frame, width=24)
        self.local_path_entry.grid(row=3, column=1, sticky=tk.EW, padx=5, pady=2)
        self.local_path_entry.insert(0, self.config.get("local_source_path", ""))
        local_btn_row = ttk.Frame(self.single_mode_frame)
        local_btn_row.grid(row=3, column=2, columnspan=4, sticky=tk.W, padx=5, pady=2)
        self.btn_browse_local_file = ttk.Button(local_btn_row, text="📄 File", command=self.browse_local_video_file, style="Tool.TButton")
        self.btn_browse_local_file.pack(side=tk.LEFT, padx=(0, 4))
        self.btn_browse_local_folder = ttk.Button(local_btn_row, text="📁 Folder", command=self.browse_local_video_folder, style="Tool.TButton")
        self.btn_browse_local_folder.pack(side=tk.LEFT)

        initial_hook_mode = normalize_hook_mode(
            self.config.get("hook_mode"), self.config.get("use_custom_hook", False)
        )
        self.hook_mode_var = tk.StringVar(value=initial_hook_mode)
        self.use_custom_hook_var = tk.BooleanVar(value=initial_hook_mode == "ai")
        hook_mode_row = ttk.Frame(self.single_mode_frame)
        hook_mode_row.grid(row=4, column=0, columnspan=4, sticky=tk.W, padx=2, pady=1)
        ttk.Label(hook_mode_row, text="Kiểu hook:").pack(side=tk.LEFT, padx=(0, 6))
        for label, value in (
            ("Tự chọn mốc", "native"),
            ("Gemini chọn cao trào", "gemini"),
            ("Hook AI", "ai"),
        ):
            ttk.Radiobutton(
                hook_mode_row, text=label, variable=self.hook_mode_var, value=value,
                command=self.on_hook_mode_change,
            ).pack(side=tk.LEFT, padx=(0, 10))

        self.btn_open_custom_hook_studio = ttk.Button(self.single_mode_frame, text="🛠 Hook Studio", command=self.open_custom_hook_studio_popup, style="Tool.TButton")
        self.btn_open_custom_hook_studio.grid(row=4, column=4, columnspan=2, sticky=tk.EW, padx=5, pady=1)

        part_row = ttk.Frame(self.single_mode_frame)
        part_row.grid(row=5, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(5, 1))
        ttk.Label(part_row, text="Chia video thành:").pack(side=tk.LEFT)
        self.part_count_var = tk.IntVar(value=int(self.config.get("part_count", 1) or 1))
        self.part_count_spin = ttk.Spinbox(
            part_row, from_=0, to=20, width=5, textvariable=self.part_count_var,
            command=self.on_part_count_change,
        )
        self.part_count_spin.pack(side=tk.LEFT, padx=(6, 5))
        self.part_count_spin.bind("<KeyRelease>", self.on_part_count_change)
        self.part_count_spin.bind("<FocusOut>", self.on_part_count_change)
        ttk.Label(part_row, text="Part (0/1 = không chia)").pack(side=tk.LEFT)
        self.part_cuts_frame = ttk.Frame(self.single_mode_frame)
        self.part_cuts_frame.grid(row=6, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(1, 3))
        self.part_split_entries = []
        self._rebuild_part_cut_inputs(self.config.get("part_cut_times", []))

        # KHUNG 2: TUYỂN TẬP TOP COUNTDOWN (COMPILATION MODE)
        self.compilation_mode_frame = ttk.LabelFrame(f1, text=" 🏆 Cấu hình Tuyển tập Top Countdown (No. N ➔ No. 1) ", padding=8)
        self.compilation_mode_frame.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(0, 2))
        self.compilation_mode_frame.columnconfigure(1, weight=1)

        comp_src_row = ttk.Frame(self.compilation_mode_frame)
        comp_src_row.grid(row=0, column=0, columnspan=6, sticky=tk.W, pady=(0, 3))
        ttk.Label(comp_src_row, text="Nguồn dữ liệu:").pack(side=tk.LEFT, padx=(2, 8))
        self.comp_source_type_var = tk.StringVar(value=self.config.get("compilation_source_type", "playlist"))
        self.rb_comp_playlist = ttk.Radiobutton(
            comp_src_row, text="YouTube Playlist", variable=self.comp_source_type_var, value="playlist",
            command=self.on_comp_source_type_change
        )
        self.rb_comp_playlist.pack(side=tk.LEFT, padx=(0, 10))
        self.rb_comp_local = ttk.Radiobutton(
            comp_src_row, text="Thư mục Local", variable=self.comp_source_type_var, value="local_folder",
            command=self.on_comp_source_type_change
        )
        self.rb_comp_local.pack(side=tk.LEFT)

        self.lbl_comp_path = ttk.Label(self.compilation_mode_frame, text="Playlist URL:")
        self.lbl_comp_path.grid(row=1, column=0, sticky=tk.W, padx=2, pady=2)
        self.comp_source_path_entry = ttk.Entry(self.compilation_mode_frame, width=54)
        self.comp_source_path_entry.grid(row=1, column=1, columnspan=3, sticky=tk.EW, padx=5, pady=2)
        self.comp_source_path_entry.insert(0, self.config.get("compilation_source_path", ""))
        self.btn_browse_comp_folder = ttk.Button(
            self.compilation_mode_frame, text="📁 Thư mục", command=self.browse_comp_source_folder, style="Tool.TButton"
        )
        self.btn_browse_comp_folder.grid(row=1, column=4, padx=5, pady=2)

        comp_settings_row = ttk.Frame(self.compilation_mode_frame)
        comp_settings_row.grid(row=2, column=0, columnspan=6, sticky=tk.EW, pady=(4, 3))
        ttk.Label(comp_settings_row, text="Số clip Top:").pack(side=tk.LEFT, padx=(2, 4))
        self.comp_clip_count_spin = ttk.Spinbox(comp_settings_row, from_=2, to=20, width=4)
        self.comp_clip_count_spin.pack(side=tk.LEFT, padx=(0, 12))
        self.comp_clip_count_spin.set(self.config.get("compilation_clip_count", 5))
        self.comp_clip_count_spin.configure(command=self.update_word_budget_label)
        self.comp_clip_count_spin.bind("<KeyRelease>", lambda e: self.update_word_budget_label())
        self.comp_clip_count_spin.bind("<FocusOut>", lambda e: self.update_word_budget_label())

        ttk.Label(comp_settings_row, text="Xếp No.1 theo:").pack(side=tk.LEFT, padx=(0, 4))
        self.comp_rank_by_cb = ttk.Combobox(
            comp_settings_row,
            values=[
                "Lượt xem (Views)",
                "Lượt thích (Likes)",
                "Ngẫu nhiên (Random)"
            ],
            width=18, state="readonly"
        )
        self.comp_rank_by_cb.pack(side=tk.LEFT, padx=(0, 10))
        rank_val = self.config.get("compilation_rank_by", "views")
        if rank_val == "likes":
            self.comp_rank_by_cb.set("Lượt thích (Likes)")
        elif rank_val == "random":
            self.comp_rank_by_cb.set("Ngẫu nhiên (Random)")
        else:
            self.comp_rank_by_cb.set("Lượt xem (Views)")

        comp_action_row = ttk.Frame(self.compilation_mode_frame)
        comp_action_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, pady=(3, 2))
        ttk.Label(comp_action_row, text="Tiêu đề clip:").pack(side=tk.LEFT, padx=(2, 4))
        self.comp_title_style_cb = ttk.Combobox(
            comp_action_row, values=[
                "Tiêu đề gốc làm sạch (Clean Title)",
                "AI tự sáng chế giật gân (Clickbait)",
                "Chỉ hiện số No. (Không hiện tiêu đề)"
            ],
            width=28, state="readonly"
        )
        self.comp_title_style_cb.pack(side=tk.LEFT, padx=(0, 12))
        title_style_val = self.config.get("compilation_title_style", "clean_original")
        if title_style_val == "ai_generated":
            self.comp_title_style_cb.set("AI tự sáng chế giật gân (Clickbait)")
        elif title_style_val == "only_rank":
            self.comp_title_style_cb.set("Chỉ hiện số No. (Không hiện tiêu đề)")
        else:
            self.comp_title_style_cb.set("Tiêu đề gốc làm sạch (Clean Title)")

        self.comp_teaser_var = tk.BooleanVar(value=bool(self.config.get("compilation_enable_teaser", True)))
        ttk.Checkbutton(comp_action_row, text="Teaser Highlight:", variable=self.comp_teaser_var).pack(side=tk.LEFT, padx=(0, 3))
        self.comp_teaser_duration_spin = ttk.Spinbox(
            comp_action_row, from_=0.5, to=30.0, increment=0.5, width=4
        )
        self.comp_teaser_duration_spin.pack(side=tk.LEFT, padx=(0, 2))
        self.comp_teaser_duration_spin.set(float(self.config.get("compilation_teaser_duration", 3.0) or 3.0))
        ttk.Label(comp_action_row, text="giây").pack(side=tk.LEFT, padx=(0, 10))

        ttk.Button(
            comp_action_row, text="👁 Xem Trước Top Clips",
            command=self.open_compilation_preview_popup, style="Primary.TButton"
        ).pack(side=tk.RIGHT, padx=(0, 2))

        self.on_source_mode_change()
        self.on_hook_mode_change()
        self.on_workflow_mode_change()
        self.on_comp_source_type_change()

        market_row = ttk.Frame(f1)
        market_row.grid(row=3, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(4, 1))
        ttk.Label(market_row, text="Thị trường đầu ra:").pack(side=tk.LEFT)
        self.market_cb = ttk.Combobox(
            market_row, values=[profile["label"] for profile in MARKET_PROFILES.values()],
            width=25, state="readonly"
        )
        self.market_cb.pack(side=tk.LEFT, padx=(6, 8))
        market_code = str(self.config.get("target_market", "US") or "US").upper()
        self.market_cb.set(get_market_profile(market_code)["label"])
        self.market_cb.bind("<<ComboboxSelected>>", self.on_market_change)
        self.output_language_var = tk.StringVar(
            value=f"{get_market_profile(market_code)['language']} • {get_market_profile(market_code)['locale']}"
        )
        ttk.Label(market_row, textvariable=self.output_language_var, foreground="#2563EB").pack(
            side=tk.LEFT, padx=(0, 8)
        )
        self.auto_voice_locale_var = tk.BooleanVar(
            value=bool(self.config.get("auto_voice_locale", True))
        )
        ttk.Checkbutton(
            market_row, text="Tự đổi giọng đúng vùng", variable=self.auto_voice_locale_var,
            command=self.on_market_change
        ).pack(side=tk.LEFT)

        # --- PHẦN 2: AI TÓM TẮT & GIỌNG ĐỌC (HỖ TRỢ EDGE-TTS & CAPCUT TTS) ---
        f2 = ttk.Frame(config_tabs, padding=7)
        config_tabs.add(f2, text="  2  AI & Voice  ")
        f2.columnconfigure(3, weight=1)

        self.use_ai_voice_var = tk.BooleanVar(value=bool(self.config.get("use_ai_voice", True)))
        self.chk_ai_voice = ttk.Checkbutton(
            f2, text="Giọng đọc AI", variable=self.use_ai_voice_var,
            command=self.on_ai_voice_toggle,
        )
        self.chk_ai_voice.grid(row=0, column=0, columnspan=6, sticky=tk.W, padx=2, pady=(0, 5))
        
        # Provider cũ vẫn tồn tại trong backend/config để mở lại khi
        # cần, nhưng GUI sản phẩm mới chỉ hiển thị Gemini qua
        # Antigravity đăng nhập Google, không bắt người dùng nhập API key.
        self.ai_model_cb = ttk.Combobox(
            f2,
            values=["Google Antigravity (Local)", "DeepSeek (V3/R1)", "Google Gemini Pro", "OpenAI GPT-4o"],
            width=25, state="readonly",
        )
        self.ai_model_cb.set("Google Antigravity (Local)")
        self.api_key_entry = ttk.Entry(f2, width=22, show="*")
        self.api_key_entry.insert(0, self.config.get("api_key", ""))

        google_box = ttk.LabelFrame(f2, text="  GOOGLE GEMINI LOCAL  ", padding=(7, 4))
        google_box.grid(row=1, column=0, columnspan=6, sticky=tk.EW, padx=2, pady=(0, 4))
        google_box.columnconfigure(1, weight=1)
        self.google_status_var = tk.StringVar(value="● Đang kiểm tra phiên Google...")
        self.google_status_label = ttk.Label(
            google_box, textvariable=self.google_status_var, foreground=self.colors["warning"]
        )
        self.google_status_label.grid(row=0, column=0, columnspan=2, sticky=tk.W, padx=(2, 8))
        self.btn_google_login = ttk.Button(
            google_box, text="🔐 Đăng nhập / đổi tài khoản",
            command=self.open_google_login, style="Primary.TButton",
        )
        self.btn_google_login.grid(row=0, column=2, padx=3)
        self.btn_google_check = ttk.Button(
            google_box, text="↻ Kiểm tra", command=lambda: self.check_google_login(manual=True),
            style="Tool.TButton",
        )
        self.btn_google_check.grid(row=0, column=3, padx=3)
        ttk.Label(
            google_box,
            text="Gemini xem proxy 360p local • video thành phẩm vẫn render từ source HD",
            style="Muted.TLabel",
        ).grid(row=1, column=0, columnspan=4, sticky=tk.W, padx=2, pady=(3, 0))
        
        # Ô 1: Engine TTS (Đã thêm CapCut TTS vào danh sách lựa chọn)
        ttk.Label(f2, text="TTS:").grid(row=2, column=0, sticky=tk.W, pady=1)
        self.engine_cb = ttk.Combobox(f2, values=["Edge-TTS (Miễn phí)", "CapCut TTS", "Google Translate TTS"], width=16, state="readonly")
        self.engine_cb.grid(row=2, column=1, sticky=tk.W, padx=5, pady=3)
        self.engine_cb.set(self.config.get("engine_tts", "Edge-TTS (Miễn phí)"))
        self.engine_cb.bind("<<ComboboxSelected>>", self.on_engine_change)

        # Ô 2 & 3 hàng dưới cho Quốc gia và Giọng cụ thể + Nút Preview 🔊
        ttk.Label(f2, text="Vùng:").grid(row=2, column=2, sticky=tk.W, pady=2, padx=(8, 2))
        self.country_cb = ttk.Combobox(f2, width=14, state="readonly")
        self.country_cb.grid(row=2, column=3, sticky=tk.W, padx=5, pady=5)
        self.country_cb.bind("<<ComboboxSelected>>", self.on_country_change)

        ttk.Label(f2, text="Giọng:").grid(row=2, column=4, sticky=tk.W, pady=2, padx=(8, 2))
        self.voice_cb = ttk.Combobox(f2, width=22, state="readonly")
        self.voice_cb.grid(row=2, column=5, sticky=tk.W, padx=5, pady=5)
        self.voice_cb.bind("<<ComboboxSelected>>", self.on_voice_selected)

        # Nút nghe thử (Preview 🔊)
        self.btn_preview = ttk.Button(f2, text="🔊 Test", command=self.preview_selected_voice, width=9, style="Tool.TButton")
        self.btn_preview.grid(row=3, column=5, sticky=tk.EW, padx=5, pady=5)

        # Hàng 3: gợi ý màu sắc kể chuyện & Nút thực thi
        ttk.Label(f2, text="Màu sắc kể:").grid(row=3, column=0, sticky=tk.W, pady=2)
        prompt_names = list(self.config.get("prompts", {}).keys())
        self.prompt_cb = ttk.Combobox(f2, values=prompt_names, width=14, state="readonly")
        self.prompt_cb.grid(row=3, column=1, sticky=tk.W, padx=5, pady=5)
        selected_p_name = self.config.get("selected_prompt", prompt_names[0] if prompt_names else "")
        if selected_p_name in prompt_names:
            self.prompt_cb.set(selected_p_name)
        self.prompt_cb.bind("<<ComboboxSelected>>", self.on_prompt_selected)
        
        ttk.Label(f2, text="Tên:").grid(row=3, column=2, sticky=tk.W, pady=5, padx=(5, 0))
        self.prompt_name_entry = ttk.Entry(f2, width=10)
        self.prompt_name_entry.grid(row=3, column=3, sticky=tk.W, padx=5, pady=5)
        self.prompt_name_entry.insert(0, "Mặc định")

        self.btn_save_prompt = ttk.Button(f2, text="💾 Lưu", command=self.save_current_prompt_direct, width=9, style="Tool.TButton")
        self.btn_save_prompt.grid(row=3, column=4, sticky=tk.W, padx=5, pady=5)
        
        self.btn_step2 = ttk.Button(f2, text="▶ AI + Voice", command=self.run_step_2, style="Primary.TButton")
        self.btn_step2.grid(row=4, column=5, sticky=tk.EW, padx=5, pady=5)

        self.prompt_text = scrolledtext.ScrolledText(
            f2, width=80, height=2, font=("Segoe UI", 9), wrap=tk.WORD,
            bg="#F8FAFC", fg="#172033", insertbackground="#2563EB",
            relief=tk.FLAT, borderwidth=1, highlightthickness=1,
            highlightbackground="#CBD5E1", highlightcolor="#2563EB", padx=8, pady=7
        )
        self.prompt_text.grid(row=6, column=0, columnspan=6, sticky=tk.W+tk.E, padx=5, pady=2)
        self.update_prompt_content_view()

        ttk.Label(f2, text="Thời lượng video:").grid(row=4, column=0, sticky=tk.W, pady=3)
        self.video_min_duration_spin = ttk.Spinbox(
            f2, from_=30, to=600, increment=5, width=8, textvariable=self.video_duration_var
        )
        self.video_min_duration_spin.grid(row=4, column=1, sticky=tk.W, padx=5, pady=3)
        duration_help = ttk.Label(f2, textvariable=self.word_budget_var, foreground="#2563EB")
        duration_help.grid(row=4, column=2, columnspan=3, sticky=tk.W, pady=3)

        duration_help.bind(
            "<Button-1>",
            lambda _e: messagebox.showinfo(
                "Thời lượng video",
                "Thời lượng video và giọng đọc AI cho toàn bộ dự án:\n"
                "• Chế độ 1 Video đơn lẻ: Thời lượng cho toàn bộ video & kịch bản AI voice.\n"
                "• Chế độ Tuyển tập Top: Thời lượng tổng chia đều cho số clip Top, hình ảnh mỗi clip được cắt khớp đúng thời lượng voice của clip đó.\n\n"
                "Video được giữ từ mốc nhập đến tối đa dài hơn 20%.\n"
                "Có AI + Voice: tool đo voice thật sau Speed và tự kéo dài/rút gọn một lần nếu lệch.\n"
                "Tắt AI + Voice: tool dùng mốc này để cắt âm thanh gốc."
            ),
        )
        self.update_word_budget_label()

        original_mix_row = ttk.Frame(f2)
        original_mix_row.grid(row=5, column=0, columnspan=6, sticky=tk.W, padx=2, pady=(1, 2))
        self.mix_original_audio_var = tk.BooleanVar(
            value=bool(self.config.get("mix_original_audio_with_voice", False))
        )
        self.chk_mix_original_audio = ttk.Checkbutton(
            original_mix_row, text="Kèm âm thanh gốc", variable=self.mix_original_audio_var,
            command=self.on_original_audio_mix_toggle,
        )
        self.chk_mix_original_audio.pack(side=tk.LEFT)
        ttk.Label(original_mix_row, text="Mức:").pack(side=tk.LEFT, padx=(12, 4))
        self.original_audio_mix_spin = ttk.Spinbox(
            original_mix_row, from_=0, to=100, increment=5, width=6,
        )
        self.original_audio_mix_spin.pack(side=tk.LEFT)
        self.original_audio_mix_spin.set(self.config.get("original_audio_mix_percent", 50.0))
        ttk.Label(original_mix_row, text="% (mặc định 50%)").pack(side=tk.LEFT, padx=(4, 0))

        # Chỉ các điều khiển TTS phụ thuộc Voice AI. Gemini, yêu cầu chọn
        # cảnh và nút chạy vẫn phải hoạt động cho Review/Highlight tiếng gốc.
        self.ai_only_grid_widgets = []
        for widget in f2.grid_slaves():
            info = widget.grid_info()
            row = int(info.get("row", 0))
            column = int(info.get("column", 0))
            if row == 2 or (row == 3 and column == 5):
                self.ai_only_grid_widgets.append(widget)

        # Khởi tạo dữ liệu danh sách giọng đọc ngay khi mở app
        self.init_tts_data()
        self.on_original_audio_mix_toggle(save=False)
        self.root.after(400, lambda: self.check_google_login(manual=False))

        # --- PHẦN 3: EDIT & XUẤT VIDEO FINAL (CAPCUT STYLE NÂNG CAO) ---
        f3 = ttk.Frame(config_tabs, padding=7)
        config_tabs.add(f3, text="  3  Hậu kỳ  ")
        
        # Hàng 0: Tốc độ, Âm lượng, Blur nền
        ttk.Label(f3, text="Tốc độ:").grid(row=0, column=0, sticky=tk.W, pady=3)
        self.speed_spin = ttk.Spinbox(f3, from_=0.5, to=3.0, increment=0.1, width=6)
        self.speed_spin.grid(row=0, column=1, sticky=tk.W, padx=3, pady=3)
        self.speed_spin.set(self.config.get("speed", 1.0))
        
        ttk.Label(f3, text="Âm lượng (dB):").grid(row=0, column=2, sticky=tk.W, pady=3, padx=(5, 2))
        self.audio_boost_spin = ttk.Spinbox(f3, from_=0.0, to=20.0, increment=1.0, width=6)
        self.audio_boost_spin.grid(row=0, column=3, sticky=tk.W, padx=3, pady=3)
        self.audio_boost_spin.set(self.config.get("audio_boost", 6.0))

        self.blur_var = tk.BooleanVar(value=self.config.get("blur_bg", True))
        self.blur_chk = ttk.Checkbutton(f3, text="Làm mờ nền", variable=self.blur_var)
        self.blur_chk.grid(row=0, column=4, padx=5, sticky=tk.W)

        # Hàng 1: Zoom-in & Thu phóng ngang/dọc
        self.zoom_var = tk.BooleanVar(value=self.config.get("zoom_in", False))
        self.zoom_chk = ttk.Checkbutton(f3, text="Zoom-in (%):", variable=self.zoom_var)
        self.zoom_chk.grid(row=1, column=0, sticky=tk.W, pady=3)
        
        self.zoom_spin = ttk.Spinbox(f3, from_=100, to=300, increment=5, width=6)
        self.zoom_spin.grid(row=1, column=1, sticky=tk.W, padx=3, pady=3)
        self.zoom_spin.set(self.config.get("zoom_percent", 115))

        ttk.Label(f3, text="Scale ngang:").grid(row=1, column=2, sticky=tk.W, pady=2, padx=(5, 2))
        self.scale_w_spin = ttk.Spinbox(f3, from_=50, to=300, increment=5, width=6)
        self.scale_w_spin.grid(row=1, column=3, sticky=tk.W, padx=3, pady=3)
        self.scale_w_spin.set(self.config.get("scale_w", 100))

        ttk.Label(f3, text="Scale dọc:").grid(row=1, column=4, sticky=tk.W, pady=2, padx=(5, 2))
        self.scale_h_spin = ttk.Spinbox(f3, from_=50, to=300, increment=5, width=6)
        self.scale_h_spin.grid(row=1, column=5, sticky=tk.W, padx=3, pady=3)
        self.scale_h_spin.set(self.config.get("scale_h", 100))

        # Hàng 2: ba motif dựng. OpenCV + heatmap vẫn là lõi của hai mode highlight.
        configured_broll_mode = str(self.config.get("broll_mode", "heatmap_ranked") or "").lower()
        configured_broll_mode = {
            "opencv": "heatmap_ranked",
            "timesub_semantic": "timesub_content",
        }.get(configured_broll_mode, configured_broll_mode)
        self.broll_mode_var = tk.StringVar(value=configured_broll_mode)
        broll_row = ttk.Frame(f3)
        broll_row.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(4, 2))
        self.broll_mode_caption_var = tk.StringVar(value="Chọn cách dựng:")
        ttk.Label(broll_row, textvariable=self.broll_mode_caption_var).pack(side=tk.LEFT, padx=(0, 8))
        self.rb_broll_timesub = ttk.Radiobutton(
            broll_row,
            text="1. Theo nội dung — Timesub",
            variable=self.broll_mode_var,
            value="timesub_content",
        )
        self.rb_broll_timesub.pack(side=tk.LEFT, padx=(0, 12))
        self.rb_broll_ranked = ttk.Radiobutton(
            broll_row,
            text="2. Review cao trào",
            variable=self.broll_mode_var,
            value="heatmap_ranked",
        )
        self.rb_broll_ranked.pack(side=tk.LEFT, padx=(0, 12))
        self.rb_broll_chronological = ttk.Radiobutton(
            broll_row,
            text="3. Highlight theo diễn biến",
            variable=self.broll_mode_var,
            value="heatmap_chronological",
        )
        self.rb_broll_chronological.pack(side=tk.LEFT)
        self.broll_mode_help_var = tk.StringVar()
        ttk.Label(f3, textvariable=self.broll_mode_help_var, style="Muted.TLabel").grid(
            row=7, column=0, columnspan=6, sticky=tk.W, pady=(3, 0)
        )
        # Tách các tuỳ chọn sang hàng riêng để Tab 3 không bị tràn ngang.
        broll_options_row = ttk.Frame(f3)
        broll_options_row.grid(row=3, column=0, columnspan=6, sticky=tk.W, pady=(0, 1))
        ttk.Label(broll_options_row, text="Cảnh tối đa:").pack(side=tk.LEFT, padx=(0, 3))
        self.broll_max_spin = ttk.Spinbox(
            broll_options_row, from_=1.5, to=30, increment=0.5, width=4
        )
        self.broll_max_spin.pack(side=tk.LEFT)
        self.broll_max_spin.set(self.config.get("broll_max_sec", 10.0))
        ttk.Label(broll_options_row, text="giây").pack(side=tk.LEFT, padx=(3, 0))
        self.random_broll_mirror_var = tk.BooleanVar(
            value=bool(self.config.get("random_broll_mirror", False))
        )
        self.random_broll_mirror_chk = ttk.Checkbutton(
            broll_options_row,
            text="Random phản chiếu B-roll (~1/3 clip)",
            variable=self.random_broll_mirror_var,
        )
        self.random_broll_mirror_chk.pack(side=tk.LEFT, padx=(16, 0))

        # Hàng 4: Quét lưới AI xóa logo & sub cũ
        anti_copyright_row = ttk.Frame(f3)
        anti_copyright_row.grid(row=4, column=0, columnspan=6, sticky=tk.W, pady=(1, 1))
        self.scramble_original_audio_var = tk.BooleanVar(value=False)

        self.gemini_grid_inspector_var = tk.BooleanVar(
            value=bool(self.config.get("gemini_grid_inspector", True))
        )
        self.gemini_grid_inspector_chk = ttk.Checkbutton(
            anti_copyright_row,
            text="🔍 Quét lưới AI xóa logo & sub cũ",
            variable=self.gemini_grid_inspector_var,
        )
        self.gemini_grid_inspector_chk.pack(side=tk.LEFT)

        # Hàng 5: Xóa thư mục tạm sau xuất (mặc định tắt — an toàn)
        self.cleanup_temp_var = tk.BooleanVar(value=bool(self.config.get("cleanup_temp_after_export", False)))
        cleanup_row = ttk.Frame(f3)
        cleanup_row.grid(row=5, column=0, columnspan=6, sticky=tk.W, pady=(1, 1))
        self.cleanup_temp_chk = ttk.Checkbutton(
            cleanup_row,
            text="Xóa thư mục tạm sau khi xuất video",
            variable=self.cleanup_temp_var,
        )
        self.cleanup_temp_chk.pack(side=tk.LEFT)
        ttk.Label(
            cleanup_row,
            text="(File xuất ra mang tên title banner, lưu trong thư mục output)",
        ).pack(side=tk.LEFT, padx=(8, 0))

        # Gom công cụ studio vào một hàng để nhường chiều cao cho danh sách render.
        studio_row = ttk.Frame(f3)
        studio_row.grid(row=6, column=0, columnspan=6, sticky=tk.EW, pady=(1, 0))
        for col in range(3):
            studio_row.columnconfigure(col, weight=1)
        self.btn_open_crop = ttk.Button(studio_row, text="✂ Crop", command=self.open_crop_tool_popup, style="Tool.TButton")
        self.btn_open_crop.grid(row=0, column=0, sticky=tk.EW, padx=(0, 3))
        self.btn_open_color_popup = ttk.Button(studio_row, text="🎨 Color", command=self.open_capcut_color_popup, style="Tool.TButton")
        self.btn_open_color_popup.grid(row=0, column=1, sticky=tk.EW, padx=3)
        self.btn_open_title_sub = ttk.Button(studio_row, text="🔤 Title & Sub", command=self.open_title_sub_studio_popup, style="Tool.TButton")
        self.btn_open_title_sub.grid(row=0, column=2, sticky=tk.EW, padx=(3, 0))

        self.on_ai_voice_toggle(save=False)

        # Config là thiết lập chung cho toàn bộ quy trình, đặt ngoài ba tab.
        config_bar = ttk.LabelFrame(self.summarizer_container, text="  CONFIG  ", padding=(7, 4))
        config_bar.grid(row=1, column=0, sticky=tk.EW, pady=(0, 4))
        config_bar.columnconfigure(1, weight=1)
        config_bar.columnconfigure(3, weight=1)
        ttk.Label(config_bar, text="Chọn:").grid(row=0, column=0, sticky=tk.W, padx=(2, 5))
        self.named_config_cb = ttk.Combobox(
            config_bar, values=list(self.config.get("saved_configs", {}).keys()),
            state="readonly",
        )
        self.named_config_cb.grid(row=0, column=1, sticky=tk.EW, padx=(0, 10))
        self.named_config_cb.bind("<<ComboboxSelected>>", self.on_named_config_choice)
        ttk.Label(config_bar, text="Tên:").grid(row=0, column=2, sticky=tk.W, padx=(0, 5))
        self.named_config_name_entry = ttk.Entry(config_bar)
        self.named_config_name_entry.grid(row=0, column=3, sticky=tk.EW, padx=(0, 8))
        ttk.Button(
            config_bar, text="↧ Load", command=self.on_named_config_selected,
            style="Tool.TButton", width=8,
        ).grid(row=0, column=4, padx=(0, 4))
        ttk.Button(
            config_bar, text="💾 Lưu", command=self.save_named_config,
            style="Tool.TButton", width=8,
        ).grid(row=0, column=5, padx=(0, 2))
        ttk.Button(
            config_bar, text="🗑 Xóa", command=self.delete_named_config,
            style="Danger.TButton", width=8,
        ).grid(row=0, column=6, padx=(2, 2))


        # --- PHẦN 4: HÀNG ĐỢI RENDER (log chi tiết tiếp tục hiện ở cửa sổ CMD) ---
        f_queue = ttk.LabelFrame(self.summarizer_container, text="  4 · DANH SÁCH VIDEO  ", padding=6)
        f_queue.grid(row=2, column=0, sticky=tk.NSEW, pady=(0, 0))
        queue_toolbar = ttk.Frame(f_queue)
        queue_toolbar.pack(fill=tk.X, pady=(0, 4))
        self.auto_start_var = tk.BooleanVar(value=False)
        ttk.Button(queue_toolbar, text="＋ Add", command=self.add_current_job, style="Tool.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="▶ Play", command=self.start_queue, style="Success.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="■ Stop", command=self.pause_after_current).pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="🗑 Xóa", command=self.delete_selected_job, style="Danger.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="▲", width=3, command=lambda: self.move_selected_job(-1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="▼", width=3, command=lambda: self.move_selected_job(1)).pack(side=tk.LEFT, padx=2)
        ttk.Button(queue_toolbar, text="↻ Retry", command=self.retry_selected_job).pack(side=tk.LEFT, padx=2)
        self.queue_summary_var = tk.StringVar(value="Chưa có video")
        ttk.Label(queue_toolbar, textvariable=self.queue_summary_var, style="Summary.TLabel").pack(side=tk.RIGHT, padx=8)

        cols = ("num", "video", "link", "source", "config", "hook", "ai", "status", "output")
        self.queue_tree = ttk.Treeview(f_queue, columns=cols, show="headings", height=12)
        headings = {"num": "#", "video": "Video", "link": "Link (bấm để copy)", "source": "Nguồn", "config": "Config",
                    "hook": "Hook", "ai": "Video AI", "status": "Trạng thái", "output": "Output"}
        widths = {"num": 34, "video": 125, "link": 155, "source": 58, "config": 80, "hook": 48,
                  "ai": 55, "status": 115, "output": 105}
        for col in cols:
            self.queue_tree.heading(col, text=headings[col])
            self.queue_tree.column(col, width=widths[col], anchor=tk.W, stretch=col in {"video", "link", "status", "output"})
        scrollbar = ttk.Scrollbar(f_queue, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=scrollbar.set)
        self.queue_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.queue_tree.bind("<ButtonRelease-1>", self.copy_queue_link)
        self.queue_tree.bind("<Double-1>", self.load_selected_job_for_edit)
        self.queue_tree.tag_configure("completed", foreground="#16825D")
        self.queue_tree.tag_configure("failed", foreground="#C93C37")
        self.queue_tree.tag_configure("running", background="#EAF1FF", foreground="#1749A3")
        self.queue_tree.tag_configure("waiting_ai_video", foreground="#B56A09")

        # Khởi tạo Container cho Chế độ 2: Chia Part & Tinh Lược Video Gốc (Độc lập 100%)
        if PartSplitterFrame is not None:
            self.part_splitter_frame = PartSplitterFrame(self.root, app=self, log_fn=print)
        else:
            self.part_splitter_frame = ttk.Frame(self.root)

    def switch_app_mode(self, mode: str):
        """Chuyển đổi hiển thị giữa Chế độ Tóm Tắt và Chế độ Chia Part."""
        self.app_mode_var.set(mode)
        if mode == "summarizer":
            if hasattr(self, "part_splitter_frame"):
                self.part_splitter_frame.grid_remove()
            if hasattr(self, "summarizer_container"):
                self.summarizer_container.grid(row=2, column=0, sticky=tk.NSEW)
            if hasattr(self, "btn_mode_summarizer"):
                self.btn_mode_summarizer.configure(style="Primary.TButton")
            if hasattr(self, "btn_mode_splitter"):
                self.btn_mode_splitter.configure(style="Tool.TButton")
        else:
            if hasattr(self, "summarizer_container"):
                self.summarizer_container.grid_remove()
            if hasattr(self, "part_splitter_frame"):
                self.part_splitter_frame.grid(row=2, column=0, sticky=tk.NSEW)
            if hasattr(self, "btn_mode_summarizer"):
                self.btn_mode_summarizer.configure(style="Tool.TButton")
            if hasattr(self, "btn_mode_splitter"):
                self.btn_mode_splitter.configure(style="Primary.TButton")

    def open_custom_hook_studio_popup(self):
        """Cửa sổ Custom Hook Studio & YouTube Frame Studio - Gộp chuẩn 1 ô custom_hook_path duy nhất."""
        popup = tk.Toplevel(self.root)
        popup.title("Custom Hook Studio & YouTube Frame Studio")
        popup.geometry("1240x880")
        popup.transient(self.root)
        popup.grab_set()

        left_frame = ttk.LabelFrame(popup, text=" ⚙ Cột 1: Cấu Hình Hook & Load Ảnh ", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        right_frame = ttk.LabelFrame(popup, text=" 🖼️ Cột 2: Giả Lập Khung Hình & Xuất Ảnh Mẫu AI ", padding=12)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Giữ cùng BooleanVar với checkbox chính — không tạo var mới (tránh lệch Path A/B)
        if not hasattr(self, "use_custom_hook_var") or self.use_custom_hook_var is None:
            self.use_custom_hook_var = tk.BooleanVar(
                value=self.config.get("use_custom_hook", False)
            )

        # ============================================================
        # 🎬 CUSTOM HOOK VIDEO - GỘP VIDEO HOOK + VIDEO AI (1 Ô DUY NHẤT)
        # ============================================================
        hook_box = ttk.LabelFrame(
            left_frame,
            text=" 🎬 Video Custom Hook ",
            padding=8
        )
        hook_box.pack(fill=tk.X, pady=(0, 8))

        # Chọn đích rõ ràng theo ID, không dựa vào "job mới nhất" hay tự đoán.
        waiting_hook_jobs = [
            job for job in self.job_queue.snapshot()
            if job.get("hook_mode") == "ai"
            and job.get("status") == "waiting_ai_video"
        ]
        hook_job_labels = {}
        for job in waiting_hook_jobs:
            label = f'{job.get("name", "video")[:32]}  ·  {job.get("id", "")}'
            hook_job_labels[label] = job.get("id", "")
        ttk.Label(hook_box, text="Video chờ Hook:").pack(anchor=tk.W, pady=(0, 3))
        hook_job_cb = ttk.Combobox(
            hook_box, values=list(hook_job_labels.keys()), state="readonly", width=34
        )
        hook_job_cb.pack(fill=tk.X, pady=(0, 7))
        selected_queue_id = self._selected_job_id() if hasattr(self, "queue_tree") else ""
        selected_label = next(
            (label for label, job_id in hook_job_labels.items() if job_id == selected_queue_id), ""
        )
        if selected_label:
            hook_job_cb.set(selected_label)
        elif len(hook_job_labels) == 1:
            hook_job_cb.set(next(iter(hook_job_labels)))

        ttk.Label(
            hook_box,
            text="Video Hook / Video AI đã sinh:"
        ).pack(anchor=tk.W, pady=(0, 3))

        hook_path_grid = ttk.Frame(hook_box)
        hook_path_grid.pack(fill=tk.X, pady=2)

        hook_entry = ttk.Entry(hook_path_grid, width=28)
        hook_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))

        # Tương thích ngược: lấy custom_hook_path, nếu không có thì lấy ai_video_path cũ
        saved_hook_path = self.config.get("custom_hook_path", "")
        if not saved_hook_path:
            saved_hook_path = self.config.get("ai_video_path", "")
        hook_entry.insert(0, saved_hook_path)

        def browse_custom_hook():
            f_path = filedialog.askopenfilename(
                title="Chọn Video Custom Hook",
                filetypes=[
                    ("Video Files", "*.mp4 *.mov *.avi *.mkv"),
                    ("All Files", "*.*")
                ]
            )
            if f_path:
                hook_entry.delete(0, tk.END)
                hook_entry.insert(0, f_path)
                self.use_custom_hook_var.set(True)
                if hasattr(self, "hook_mode_var"):
                    self.hook_mode_var.set("ai")

        ttk.Button(
            hook_path_grid,
            text="📁 File...",
            command=browse_custom_hook,
            width=7
        ).pack(side=tk.RIGHT)

        def browse_custom_hook_folder():
            d_path = filedialog.askdirectory(title="Chọn thư mục chứa Video Hook AI")
            if d_path:
                hook_entry.delete(0, tk.END)
                hook_entry.insert(0, d_path)
                self.use_custom_hook_var.set(True)
                if hasattr(self, "hook_mode_var"):
                    self.hook_mode_var.set("ai")

        ttk.Button(
            hook_box,
            text="📂 Chọn thư mục...",
            command=browse_custom_hook_folder,
        ).pack(fill=tk.X, pady=(4, 0))

        ttk.Label(
            hook_box,
            text="💡 Trỏ file video Hook AI hoặc thư mục chứa clip. Path B không zoom / speed / blur trên hook.",
            wraplength=300,
            justify=tk.LEFT
        ).pack(anchor=tk.W, pady=(5, 3))

        def save_custom_hook_action():
            if EditorAI is None:
                messagebox.showerror("Lỗi", "Không tìm thấy editor_ai.py — không thể chạy Path B!", parent=popup)
                return

            raw_hook = hook_entry.get().strip()
            if not raw_hook:
                messagebox.showwarning("Cảnh báo", "Sếp chưa chọn Video Custom Hook!", parent=popup)
                return

            hook_path = EditorAI.resolve_hook_source(raw_hook)
            if not hook_path or not os.path.exists(hook_path):
                messagebox.showwarning("Cảnh báo", "File/thư mục Video Hook AI không hợp lệ!", parent=popup)
                return

            self.config["custom_hook_path"] = hook_path
            self.config["ai_video_path"] = hook_path
            self.config["use_custom_hook"] = True
            self.config["hook_mode"] = "ai"
            self.use_custom_hook_var.set(True)
            if hasattr(self, "hook_mode_var"):
                self.hook_mode_var.set("ai")
                self.on_hook_mode_change()
            save_config(self.config)

            # Gắn Hook vào đúng job đang chọn. Nếu không chọn nhưng chỉ có một job
            # đang chờ Hook AI, tự nhận job đó; tuyệt đối không render trực tiếp.
            target_job = None
            chosen_job_id = hook_job_labels.get(hook_job_cb.get().strip(), "")
            if chosen_job_id:
                target_job = self.job_queue.get(chosen_job_id)
            selected_id = self._selected_job_id() if hasattr(self, "queue_tree") else ""
            if target_job is None and selected_id:
                selected_job = self.job_queue.get(selected_id)
                if selected_job and selected_job.get("hook_mode") == "ai" and selected_job.get("status") != "running":
                    target_job = selected_job
            if target_job is None:
                waiting = [
                    job for job in self.job_queue.snapshot()
                    if job.get("hook_mode") == "ai"
                    and job.get("status") == "waiting_ai_video"
                ]
                if len(waiting) == 1:
                    target_job = waiting[0]

            if target_job is not None:
                options = deepcopy(target_job.get("post_options", {}))
                options.update({
                    "use_custom_hook": True,
                    "custom_hook_path": hook_path,
                    "ai_video_path": hook_path,
                })
                self.job_queue.update(
                    target_job["id"],
                    ai_video_path=hook_path,
                    post_options=options,
                    status="pending",
                    status_text="Chờ",
                    error="",
                )
                self.refresh_queue_table()
                notice = f'Đã lưu Hook cho job "{target_job.get("name", "video")}".\nBấm Play để render.'
            else:
                notice = "Đã lưu Hook vào cấu hình hiện tại.\nChọn đúng job hoặc Add job, sau đó bấm Play."

            messagebox.showinfo("Đã lưu Hook", notice, parent=popup)
            popup.destroy()

        ttk.Button(
            hook_box,
            text="💾 Lưu Hook",
            command=save_custom_hook_action
        ).pack(fill=tk.X, pady=(6, 2))


        # --- TRÍCH XUẤT ẢNH TỪ VIDEO GỐC CÓ SẴN ---
        source_ext_box = ttk.LabelFrame(left_frame, text=" 📸 Trích Xuất Ảnh Tĩnh Từ Video Gốc Có Sẵn ", padding=8)
        source_ext_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(source_ext_box, text="File Video Gốc (source_video.mp4):").pack(anchor=tk.W, pady=(0, 2))
        src_grid = ttk.Frame(source_ext_box)
        src_grid.pack(fill=tk.X, pady=(0, 4))

        source_vid_entry = ttk.Entry(src_grid, width=28)
        source_vid_entry.pack(side=tk.LEFT, padx=(0, 5))
        
        default_src = self.config.get("source_video_path", "")
        if not default_src or not os.path.exists(default_src):
            try:
                base_temp = "temp"
                if os.path.exists(base_temp):
                    sub_dirs = [os.path.join(base_temp, d) for d in os.listdir(base_temp) if os.path.isdir(os.path.join(base_temp, d))]
                    if sub_dirs:
                        latest_dir = max(sub_dirs, key=os.path.getmtime)
                        s_vid = os.path.join(latest_dir, "source_video.mp4")
                        if os.path.exists(s_vid): default_src = s_vid
            except: pass
        source_vid_entry.insert(0, default_src)

        def browse_source_vid():
            f_p = filedialog.askopenfilename(title="Chọn video gốc", filetypes=[("Video Files", "*.mp4 *.mov *.avi"), ("All Files", "*.*")])
            if f_p:
                source_vid_entry.delete(0, tk.END)
                source_vid_entry.insert(0, f_p)

        ttk.Button(src_grid, text="📁 Chọn...", command=browse_source_vid, width=7).pack(side=tk.RIGHT)

        time_row_frame = ttk.Frame(source_ext_box)
        time_row_frame.pack(fill=tk.X, pady=(2, 4))

        ttk.Label(time_row_frame, text="Mốc Thời Gian (Phút:Giây):").pack(side=tk.LEFT, padx=(0, 5))
        source_time_entry = ttk.Entry(time_row_frame, width=10)
        source_time_entry.pack(side=tk.LEFT)
        source_time_entry.insert(0, "0:00")

        # --- CÔNG CỤ CẮT XÉN VIỀN ---
        crop_box = ttk.LabelFrame(left_frame, text=" ✂ Cắt Xén Viền (Xóa Logo / Watermark) ", padding=8)
        crop_box.pack(fill=tk.X, pady=(2, 5))

        ttk.Label(crop_box, text="Cắt bỏ trên (Pixel):").grid(row=0, column=0, sticky=tk.W, pady=2)
        crop_top_spin = ttk.Spinbox(crop_box, from_=0, to=500, increment=5, width=8)
        crop_top_spin.grid(row=0, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(crop_box, text="Cắt bỏ dưới (Pixel):").grid(row=1, column=0, sticky=tk.W, pady=2)
        crop_bot_spin = ttk.Spinbox(crop_box, from_=0, to=500, increment=5, width=8)
        crop_bot_spin.grid(row=1, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(crop_box, text="Cắt bỏ trái (Pixel):").grid(row=2, column=0, sticky=tk.W, pady=2)
        crop_left_spin = ttk.Spinbox(crop_box, from_=0, to=500, increment=5, width=8)
        crop_left_spin.grid(row=2, column=1, sticky=tk.W, padx=5, pady=2)

        ttk.Label(crop_box, text="Cắt bỏ phải (Pixel):").grid(row=3, column=0, sticky=tk.W, pady=2)
        crop_right_spin = ttk.Spinbox(crop_box, from_=0, to=500, increment=5, width=8)
        crop_right_spin.grid(row=3, column=1, sticky=tk.W, padx=5, pady=2)

        try:
            base_w = self.config.get("base_w", 1920)
            base_h = self.config.get("base_h", 1080)
            if self.config.get("use_crop", False):
                cx = self.config.get("crop_x", 0)
                cy = self.config.get("crop_y", 0)
                cw = self.config.get("crop_w", base_w)
                ch = self.config.get("crop_h", base_h)
                
                crop_top_spin.set(cy)
                crop_bot_spin.set(max(0, base_h - (cy + ch)))
                crop_left_spin.set(cx)
                crop_right_spin.set(max(0, base_w - (cx + cw)))
            else:
                crop_top_spin.set(self.config.get("hook_crop_top", 0))
                crop_bot_spin.set(self.config.get("hook_crop_bot", 0))
                crop_left_spin.set(self.config.get("hook_crop_left", 0))
                crop_right_spin.set(self.config.get("hook_crop_right", 0))
        except Exception as e:
            print(f"Lỗi nạp config crop: {e}")

        def extract_frame_from_source(mode="preview"):
            v_path = source_vid_entry.get().strip()
            t_str = source_time_entry.get().strip()
            if not v_path or not os.path.exists(v_path):
                messagebox.showwarning("Cảnh báo", "Sếp chưa chọn file Video Gốc hợp lệ!", parent=popup)
                return

            try:
                if ":" in t_str:
                    parts = t_str.split(":")
                    sec = (float(parts[0]) * 60) + float(parts[1])
                else:
                    sec = float(t_str)
            except:
                sec = 0.0

            print(f"🎬 Đang trích xuất frame từ video gốc tại mốc {sec}s bằng FFmpeg...")
            try:
                os.makedirs("temp", exist_ok=True)
                temp_frame_path = os.path.join("temp", "extracted_source_frame.jpg")

                subprocess.run([
                    "ffmpeg", "-y", "-ss", str(sec), "-i", v_path,
                    "-frames:v", "1", "-q:v", "2", temp_frame_path
                ], check=True, creationflags=0x08000000)

                if not os.path.exists(temp_frame_path) or os.path.getsize(temp_frame_path) == 0:
                    messagebox.showerror("Lỗi", "Không thể trích xuất frame tại mốc thời gian này!", parent=popup)
                    return

                if mode == "preview":
                    self.sim_image_path = temp_frame_path
                    try: update_simulator()
                    except: pass
                    messagebox.showinfo("Thành công", "Đã trích xuất và load ảnh vào khung giả lập thành công!", parent=popup)
                elif mode == "download":
                    save_path = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPEG files", "*.jpg")], title="Lưu ảnh tĩnh")
                    if save_path:
                        import shutil
                        shutil.copyfile(temp_frame_path, save_path)
                        messagebox.showinfo("Thành công", f"Đã lưu ảnh thành công tại:\n{save_path}", parent=popup)
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể trích xuất frame:\n{e}", parent=popup)

        btn_src_grid = ttk.Frame(source_ext_box)
        btn_src_grid.pack(fill=tk.X, pady=4)

        ttk.Button(btn_src_grid, text="1️⃣ Load Khung Giả Lập", command=lambda: extract_frame_from_source("preview")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_src_grid, text="2️⃣ Tải Ảnh Về Máy", command=lambda: extract_frame_from_source("download")).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=2)

        # --- BỐC FRAME TỪ YOUTUBE (DỰ PHÒNG) ---
        yt_box = ttk.LabelFrame(left_frame, text=" 📥 Bốc Frame Trực Tiếp Từ YouTube (Dự phòng) ", padding=8)
        yt_box.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(yt_box, text="Link YouTube:").grid(row=0, column=0, sticky=tk.W, pady=2)
        yt_link_entry = ttk.Entry(yt_box, width=28)
        yt_link_entry.grid(row=0, column=1, columnspan=2, sticky=tk.W, padx=3, pady=2)
        yt_link_entry.insert(0, self.url_entry.get().strip() if hasattr(self, 'url_entry') else "")

        ttk.Label(yt_box, text="Mốc (Phút:Giây):").grid(row=1, column=0, sticky=tk.W, pady=2)
        yt_time_entry = ttk.Entry(yt_box, width=10)
        yt_time_entry.grid(row=1, column=1, sticky=tk.W, padx=3, pady=2)
        yt_time_entry.insert(0, self.hook_time_entry.get().strip() if hasattr(self, 'hook_time_entry') else "0:00")

        def process_youtube_frame(mode="preview"):
            link = yt_link_entry.get().strip()
            t_str = yt_time_entry.get().strip()
            if not link:
                messagebox.showwarning("Cảnh báo", "Sếp chưa nhập Link YouTube!", parent=popup)
                return

            try:
                if ":" in t_str:
                    parts = t_str.split(":")
                    sec = (float(parts[0]) * 60) + float(parts[1])
                else:
                    sec = float(t_str)
            except:
                sec = 0.0

            def background_task():
                try:
                    import yt_dlp
                    import cv2
                    os.makedirs("temp", exist_ok=True)
                    temp_video_path = os.path.join("temp", "temp_hook_source.mp4")
                    temp_frame_path = os.path.join("temp", "extracted_frame.jpg")

                    from downloader_processor import (
                        HIGH_RES_FORMAT,
                        HIGH_RES_FORMAT_FALLBACK,
                        HIGH_RES_FORMAT_SORT,
                        HIGH_RES_EXTRACTOR_ARGS,
                    )
                    ydl_opts = {
                        'outtmpl': temp_video_path,
                        'format': HIGH_RES_FORMAT,
                        'format_sort': list(HIGH_RES_FORMAT_SORT),
                        'merge_output_format': 'mp4',
                        'noplaylist': True,
                        'quiet': True,
                        'no_warnings': True,
                        'nocheckcertificate': True,
                        'nopart': True,
                        'retries': 10,
                        'fragment_retries': 10,
                        'concurrent_fragment_downloads': 16,
                        'headers': {
                            'Accept-Language': 'en-US,en;q=0.9',
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                        },
                        'extractor_args': HIGH_RES_EXTRACTOR_ARGS,
                    }
                    ydl_opts = DownloaderProcessor.apply_cookies_to_opts(ydl_opts)
                    try:
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([link])
                    except Exception as ydl_err:
                        if "requested format is not available" not in str(ydl_err).lower():
                            raise
                        ydl_opts["format"] = HIGH_RES_FORMAT_FALLBACK
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([link])

                    if not os.path.exists(temp_video_path):
                        possible_files = [os.path.join("temp", f) for f in os.listdir("temp") if f.startswith("temp_hook_source")]
                        if possible_files:
                            temp_video_path = possible_files[0]

                    subprocess.run([
                        "ffmpeg", "-y", "-ss", str(sec), "-i", temp_video_path,
                        "-frames:v", "1", "-q:v", "2", temp_frame_path
                    ], check=True, creationflags=0x08000000)

                    if mode == "preview":
                        def apply_preview():
                            self.sim_image_path = temp_frame_path
                            update_simulator()
                            messagebox.showinfo("Thành công", "Đã tải xong và load frame vào khung giả lập thành công!", parent=popup)
                        self.root.after(0, apply_preview)
                    elif mode == "download":
                        def save_file_dialog():
                            save_path = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPEG files", "*.jpg")], title="Lưu ảnh")
                            if save_path:
                                import shutil
                                shutil.copyfile(temp_frame_path, save_path)
                                messagebox.showinfo("Thành công", f"Đã lưu tại:\n{save_path}", parent=popup)
                        self.root.after(0, save_file_dialog)
                except Exception as e:
                    self.root.after(0, lambda err=e: messagebox.showerror("Lỗi", f"Không thể bốc frame:\n{err}", parent=popup))

            import threading
            threading.Thread(target=background_task, daemon=True).start()

        btn_yt_grid = ttk.Frame(yt_box)
        btn_yt_grid.grid(row=2, column=0, columnspan=3, sticky=tk.EW, pady=5)
        ttk.Button(btn_yt_grid, text="1️⃣ Load Khung Giả Lập", command=lambda: process_youtube_frame("preview")).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_yt_grid, text="2️⃣ Tải Ảnh Về Máy", command=lambda: process_youtube_frame("download")).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=2)

        ttk.Label(left_frame, text="Âm lượng Hook (dB):").pack(anchor=tk.W, pady=(2, 2))
        hook_vol_spin = ttk.Spinbox(left_frame, from_=0.0, to=20.0, increment=1.0, width=15)
        hook_vol_spin.pack(anchor=tk.W, pady=(0, 8))
        hook_vol_spin.set(self.config.get("hook_audio_boost", 6.0))

        no_speed_var = tk.BooleanVar(value=self.config.get("hook_no_speed", True))
        ttk.Checkbutton(left_frame, text="Loại bỏ Speed ở đoạn Hook\n(Giữ nguyên tốc độ gốc & Audio)", variable=no_speed_var).pack(anchor=tk.W, pady=(0, 8))

        def on_toggle_custom_hook():
            if not self.use_custom_hook_var.get():
                temp_dir = "temp"
                if os.path.exists(temp_dir):
                    for filename in os.listdir(temp_dir):
                        if "temp_hook_source" in filename or "extracted_frame" in filename:
                            try: os.remove(os.path.join(temp_dir, filename))
                            except: pass

        ttk.Checkbutton(left_frame, text="Bật tính năng Custom Hook này", variable=self.use_custom_hook_var, command=on_toggle_custom_hook).pack(anchor=tk.W, pady=(5, 10))

        # --- CỘT 2: GIẢ LẬP KHUNG HÌNH LINH HOẠT ---
        sim_top_frame = ttk.Frame(right_frame)
        sim_top_frame.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(sim_top_frame, text="Chọn Tỉ lệ Khung hình:").pack(side=tk.LEFT, padx=(0, 5))
        ratio_values = ["9:16 (TikTok / Shorts - 1080x1920)", "1:1 (Vuông - 1080x1080)", "16:9 (Ngang HD - 1920x1080)", "4:5 (Instagram - 1080x1350)"]
        res_combo = ttk.Combobox(sim_top_frame, values=ratio_values, width=32, state="readonly")
        res_combo.pack(side=tk.LEFT, padx=5)
        res_combo.set(ratio_values[0])

        preview_container = ttk.Frame(right_frame)
        preview_container.pack(fill=tk.BOTH, expand=True, pady=5)

        sim_canvas = tk.Canvas(preview_container, bg="#111111", highlightthickness=0)
        sim_canvas.pack(fill=tk.BOTH, expand=True)

        self.sim_image_path = None
        self.sim_image_tk_cache = None

        def parse_target_resolution():
            sel = res_combo.get()
            if "1:1" in sel: return 1080, 1080
            elif "16:9" in sel: return 1920, 1080
            elif "4:5" in sel: return 1080, 1350
            return 1080, 1920

        def generate_simulated_pil_image():
            import numpy as np
            import cv2
            target_w, target_h = parse_target_resolution()

            if not self.sim_image_path or not os.path.exists(self.sim_image_path):
                dummy = np.zeros((1080, 1920, 3), dtype=np.uint8)
                dummy[:] = (45, 90, 150)
                cv2.putText(dummy, "TRICH XUAT ANH TU VIDEO GOC", (100, 960), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 2)
                self.sim_image_path = os.path.join("temp", "sim_default.jpg")
                os.makedirs("temp", exist_ok=True)
                cv2.imwrite(self.sim_image_path, dummy)

            pil_orig = Image.open(self.sim_image_path).convert("RGB")
            orig_w, orig_h = pil_orig.size

            img_ratio = orig_w / float(orig_h if orig_h > 0 else 1)
            target_ratio = target_w / float(target_h)

            if img_ratio > target_ratio:
                bg_h = target_h
                bg_w = int(target_h * img_ratio)
            else:
                bg_w = target_w
                bg_h = int(target_w / img_ratio)

            bg_resized = pil_orig.resize((bg_w, bg_h), Image.Resampling.LANCZOS)
            bg_canvas = bg_resized.crop(((bg_w - target_w) / 2, (bg_h - target_h) / 2, (bg_w + target_w) / 2, (bg_h + target_h) / 2))
            bg_canvas = bg_canvas.filter(ImageFilter.GaussianBlur(30))

            pil_fg = pil_orig.copy()
            try:
                ct = int(float(crop_top_spin.get()))
                cb = int(float(crop_bot_spin.get()))
                cl = int(float(crop_left_spin.get()))
                cr = int(float(crop_right_spin.get()))
                ow, oh = pil_fg.size
                pil_fg = pil_fg.crop((cl, ct, max(cl + 10, ow - cr), max(ct + 10, oh - cb)))
            except Exception as e:
                print(f"Lỗi cắt xén ảnh: {e}")

            fg_orig_w, fg_orig_h = pil_fg.size
            zoom_in_val = self.zoom_var.get()
            zoom_pct_val = float(self.zoom_spin.get() or 100.0) / 100.0 if zoom_in_val else 1.0
            try:
                scale_w_val = float(self.scale_w_spin.get() or 100.0) / 100.0
            except Exception:
                scale_w_val = 1.0
            try:
                scale_h_val = float(self.scale_h_spin.get() or 100.0) / 100.0
            except Exception:
                scale_h_val = 1.0

            base_h = int(target_w * fg_orig_h / float(fg_orig_w if fg_orig_w > 0 else 1))
            fg_w = max(32, int(round(target_w * zoom_pct_val * scale_w_val)))
            fg_h = max(32, int(round(base_h * zoom_pct_val * scale_h_val)))
            fg_resized = pil_fg.resize((fg_w, fg_h), Image.Resampling.LANCZOS)

            bg_canvas.paste(fg_resized, (int((target_w - fg_w) / 2), int((target_h - fg_h) / 2)))
            return bg_canvas

        def update_simulator(event=None):
            try:
                final_canvas = generate_simulated_pil_image()
                cw = sim_canvas.winfo_width()
                ch = sim_canvas.winfo_height()
                if cw < 50 or ch < 50: cw, ch = 550, 600

                target_w, target_h = parse_target_resolution()
                target_ratio = target_w / float(target_h)
                container_ratio = cw / float(ch)

                if container_ratio > target_ratio:
                    disp_h = int(ch - 30)
                    disp_w = int(disp_h * target_ratio)
                else:
                    disp_w = int(cw - 30)
                    disp_h = int(disp_w / float(target_ratio))

                display_img = final_canvas.resize((max(50, disp_w), max(50, disp_h)), Image.Resampling.LANCZOS)
                self.sim_image_tk_cache = ImageTk.PhotoImage(display_img)
                sim_canvas.delete("all")
                sim_canvas.create_image(int(cw / 2), int(ch / 2), image=self.sim_image_tk_cache, anchor=tk.CENTER)
            except Exception as e:
                print(f"Lỗi cập nhật giả lập: {e}")

        res_combo.bind("<<ComboboxSelected>>", update_simulator)
        sim_canvas.bind("<Configure>", update_simulator)
        for sp in [crop_top_spin, crop_bot_spin, crop_left_spin, crop_right_spin]:
            sp.bind("<KeyRelease>", update_simulator)
            sp.config(command=update_simulator)

        def upload_sample_image():
            f_path = filedialog.askopenfilename(title="Chọn ảnh mẫu đại diện", filetypes=[("Image Files", "*.jpg *.png *.jpeg *.webp")])
            if f_path:
                self.sim_image_path = f_path
                update_simulator()

        def export_simulated_image():
            try:
                target_w, target_h = parse_target_resolution()
                save_path = filedialog.asksaveasfilename(defaultextension=".jpg", filetypes=[("JPEG files", "*.jpg")], title="Xuất ảnh mẫu")
                if save_path:
                    generate_simulated_pil_image().save(save_path, quality=95)
                    messagebox.showinfo("Thành công", f"Đã xuất tại:\n{save_path}", parent=popup)
            except Exception as e:
                messagebox.showerror("Lỗi", f"{e}", parent=popup)

        btn_row = ttk.Frame(right_frame)
        btn_row.pack(fill=tk.X, pady=5)
        ttk.Button(btn_row, text="📂 Tải ảnh từ máy...", command=upload_sample_image).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=2)
        ttk.Button(btn_row, text="💾 Xuất Ảnh Mẫu AI", command=export_simulated_image).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=2)

        def clean_temp_hook_files():
            temp_dir = "temp"
            count = 0
            if os.path.exists(temp_dir):
                for filename in os.listdir(temp_dir):
                    if "temp_hook_source" in filename or "extracted_frame" in filename or filename.endswith(".part"):
                        try:
                            os.remove(os.path.join(temp_dir, filename))
                            count += 1
                        except: pass
            messagebox.showinfo("Thành công", f"Đã dọn dẹp {count} file rác!", parent=popup)

        def save_custom_hook_settings():
            self.config["use_custom_hook"] = True
            self.config["hook_mode"] = "ai"
            self.use_custom_hook_var.set(True)
            if hasattr(self, "hook_mode_var"):
                self.hook_mode_var.set("ai")
            self.config["custom_hook_path"] = hook_entry.get().strip()
            self.config["ai_video_path"] = hook_entry.get().strip() # Đồng bộ tương thích ngược
            self.config["source_video_path"] = source_vid_entry.get().strip()
            self.config["hook_audio_boost"] = float(hook_vol_spin.get())
            self.config["hook_no_speed"] = no_speed_var.get()
            self.config["hook_crop_top"] = int(float(crop_top_spin.get()))
            self.config["hook_crop_bot"] = int(float(crop_bot_spin.get()))
            self.config["hook_crop_left"] = int(float(crop_left_spin.get()))
            self.config["hook_crop_right"] = int(float(crop_right_spin.get()))
            
            if not self.use_custom_hook_var.get():
                clean_temp_hook_files()

            save_config(self.config)
            messagebox.showinfo("Thành công", "Đã lưu cấu hình Custom Hook thành công!", parent=popup)
            popup.destroy()

        bottom_btn_frame = ttk.Frame(left_frame)
        bottom_btn_frame.pack(side=tk.BOTTOM, fill=tk.X, pady=(15, 0))
        ttk.Button(bottom_btn_frame, text="🧹 Dọn Rác Tạm", command=clean_temp_hook_files).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 3))
        ttk.Button(bottom_btn_frame, text="💾 Lưu Cấu Hình", command=save_custom_hook_settings).pack(side=tk.RIGHT, expand=True, fill=tk.X, padx=(3, 0))

        popup.after(150, update_simulator)
        popup.focus_force()

             
    def init_tts_data(self):
        """Tải riêng biệt danh sách giọng đọc của Edge-TTS và CapCut TTS và đồng bộ với config đã lưu."""
        def load_thread():
            print("⏳ Đang tự động quét danh sách giọng đọc Edge-TTS và CapCut TTS...")
            self.edge_database = AIProcessor.get_all_edge_voices()
            
            try:
                self.capcut_database = AIProcessor.get_all_capcut_voices()
            except Exception as e:
                print(f"⚠️ Không tải được CapCut voices: {e}")
                self.capcut_database = {"en-US": ["Jessie (DiT_en_female_jessie)"], "vi-VN": ["NamMinh (DiT_vi_male_namminh)"]}

            def update_ui():
                # Kiểm tra engine đang được chọn từ config để nạp đúng database tương ứng
                saved_engine = self.config.get("engine_tts", "Edge-TTS (Miễn phí)")
                if "CapCut TTS" in saved_engine:
                    self.tts_database = self.capcut_database
                else:
                    self.tts_database = self.edge_database

                countries = sorted(list(self.tts_database.keys()))
                self.country_cb['values'] = countries
                
                saved_country = self.config.get("country", "")
                if saved_country in countries:
                    self.country_cb.set(saved_country)
                elif countries:
                    self.country_cb.set(countries[0])
                
                # Cập nhật danh sách giọng đọc theo quốc gia
                selected_country = self.country_cb.get()
                voices = self.tts_database.get(selected_country, [])
                self.voice_cb['values'] = voices
                
                # Khôi phục đúng giọng đọc đã lưu trong config (ví dụ Jessie)
                saved_voice = self.config.get("voice", "")
                if saved_voice in voices:
                    self.voice_cb.set(saved_voice)
                elif voices:
                    self.voice_cb.set(voices[0])

                self._sync_voice_to_market(force=False)

                print("✅ Tải danh sách giọng đọc và đồng bộ config hoàn tất!")
            
            self.root.after(0, update_ui)

        threading.Thread(target=load_thread, daemon=True).start()

    def on_engine_change(self, event):
        engine = self.engine_cb.get()
        if "Edge-TTS" in engine:
            self.tts_database = getattr(self, 'edge_database', {})
            self.country_cb.config(state="readonly")
            self.voice_cb.config(state="readonly")
            if self.tts_database:
                countries = sorted(list(self.tts_database.keys()))
                self.country_cb['values'] = countries
                target_locale = get_market_profile(self.config.get("target_market", "US"))["locale"]
                self.country_cb.set(target_locale if target_locale in countries else countries[0])
                self.on_country_change(None)
        elif "CapCut TTS" in engine:
            # Lấy đúng kho dữ liệu riêng của CapCut TTS
            self.tts_database = getattr(self, 'capcut_database', {})
            if not self.tts_database:
                self.capcut_database = AIProcessor.get_all_capcut_voices()
                self.tts_database = self.capcut_database
                
            self.country_cb.config(state="readonly")
            self.voice_cb.config(state="readonly")
            
            countries = sorted(list(self.tts_database.keys()))
            self.country_cb['values'] = countries
            if countries:
                target_locale = get_market_profile(self.config.get("target_market", "US"))["locale"]
                default_c = target_locale if target_locale in countries else countries[0]
                self.country_cb.set(default_c)
                self.on_country_change(None)
        else:
            self.country_cb.config(state=tk.DISABLED)
            self.voice_cb.config(state=tk.DISABLED)
            self.voice_cb['values'] = ["Google Translate TTS (Miễn phí)"]
            self.voice_cb.set("Google Translate TTS (Miễn phí)")

    def on_country_change(self, event):
        selected_country = self.country_cb.get()
        voices = self.tts_database.get(selected_country, [])
        self.voice_cb['values'] = voices
        if voices:
            preferred = voices[0]
            for v in voices:
                if "Andrew" in v or "NamMinh" in v or "jessie" in v.lower():
                    preferred = v
                    break
            self.voice_cb.set(preferred)

    def on_voice_selected(self, _event=None):
        locale = self.country_cb.get().strip()
        voice = self.voice_cb.get().strip()
        if locale and voice:
            preferred = self.config.setdefault("preferred_voice_by_locale", {})
            preferred[locale] = voice

    def on_market_change(self, _event=None):
        if not hasattr(self, "market_cb"):
            return
        code = market_code_from_label(self.market_cb.get())
        profile = get_market_profile(code)
        self.config["target_market"] = code
        self.config["output_language"] = profile["language"]
        self.config["output_locale"] = profile["locale"]
        if hasattr(self, "output_language_var"):
            self.output_language_var.set(f"{profile['language']} • {profile['locale']}")
        self._sync_voice_to_market(force=True)

    def _sync_voice_to_market(self, force=False):
        """Giữ giọng đúng locale; CapCut thiếu locale thì tự chuyển sang Edge."""
        if not hasattr(self, "country_cb") or not hasattr(self, "voice_cb"):
            return
        auto = bool(self.auto_voice_locale_var.get()) if hasattr(self, "auto_voice_locale_var") else True
        if not auto and force:
            return
        profile = get_market_profile(self.config.get("target_market", "US"))
        locale = profile["locale"]
        engine = self.engine_cb.get()
        if "Google Translate" in engine:
            self.country_cb["values"] = [locale]
            self.country_cb.set(locale)
            self.country_cb.config(state=tk.DISABLED)
            self.voice_cb["values"] = ["Google Translate TTS (Miễn phí)"]
            self.voice_cb.set("Google Translate TTS (Miễn phí)")
            self.voice_cb.config(state=tk.DISABLED)
            return
        old_voice = self.voice_cb.get().lower()
        wanted_gender = "female" if ("female" in old_voice or "_female_" in old_voice) else (
            "male" if ("male" in old_voice or "_male_" in old_voice) else ""
        )
        database = getattr(self, "capcut_database", {}) if "CapCut" in engine else getattr(self, "edge_database", {})
        if locale not in database:
            edge_database = getattr(self, "edge_database", {})
            if locale in edge_database:
                self.engine_cb.set("Edge-TTS (Miễn phí)")
                self.tts_database = edge_database
                database = edge_database
                print(f"ℹ️ [TTS MARKET] CapCut không có {locale}; tự chuyển sang Edge-TTS đúng vùng.")
            elif not database:
                return
            else:
                print(f"⚠️ [TTS MARKET] Chưa tải được giọng đúng vùng {locale}.")
                return
        voices = database.get(locale, [])
        if not voices:
            return
        self.country_cb.config(state="readonly")
        self.voice_cb.config(state="readonly")
        self.country_cb["values"] = sorted(database.keys())
        self.country_cb.set(locale)
        self.voice_cb["values"] = voices
        current = self.voice_cb.get()
        preferred = self.config.get("preferred_voice_by_locale", {}).get(locale)
        if current not in voices:
            selected = preferred if preferred in voices else ""
            if not selected and wanted_gender:
                selected = next((voice for voice in voices if wanted_gender in voice.lower()), "")
            self.voice_cb.set(selected or voices[0])
        self.on_voice_selected()

    def preview_selected_voice(self):
        engine = self.engine_cb.get()
        voice = self.voice_cb.get()
        locale = self.country_cb.get()

        def play_thread():
            try:
                print(f"🔊 Đang tạo và phát bản nghe thử cho giọng [{voice}] trực tiếp trên tool...")
                AIProcessor.preview_voice_audio(voice, locale)
                print("▶ Đang phát âm thanh thành công!")
            except Exception as e:
                print(f"❌ Lỗi khi nghe thử: {e}")

        threading.Thread(target=play_thread, daemon=True).start()

    def update_prompt_content_view(self):
        current_name = self.prompt_cb.get()
        prompts_dict = self.config.get("prompts", {})
        if current_name in prompts_dict:
            self.prompt_text.delete("1.0", tk.END)
            self.prompt_text.insert("1.0", prompts_dict[current_name])
            self.prompt_name_entry.delete(0, tk.END)
            self.prompt_name_entry.insert(0, current_name)

    def on_prompt_selected(self, event):
        self.update_prompt_content_view()

    def save_current_prompt_direct(self):
        p_name = self.prompt_name_entry.get().strip()
        p_content = self.prompt_text.get("1.0", tk.END).strip()
        if not p_name or not p_content:
            messagebox.showwarning("Cảnh báo", "Tên và nội dung prompt không được để trống!")
            return
        if "prompts" not in self.config:
            self.config["prompts"] = {}
        self.config["prompts"][p_name] = p_content
        self.config["selected_prompt"] = p_name
        save_config(self.config)
        self.prompt_cb['values'] = list(self.config["prompts"].keys())
        self.prompt_cb.set(p_name)
        messagebox.showinfo("Thành công", f"Đã lưu prompt '{p_name}' thành công!")

    def open_capcut_color_popup(self, on_save_callback=None, caller_config=None):
        """Bảng màu + bộ lọc. Ảnh tĩnh đổi màu ngay; Render 6s để xem chuyển động."""
        popup = tk.Toplevel(self.root)
        popup.title("Bảng màu + Bộ lọc — khung 9:16 như Hook Studio")
        popup.geometry("1240x880")
        popup.transient(self.root)

        src_cfg = caller_config if isinstance(caller_config, dict) else self.config

        left_frame = ttk.LabelFrame(popup, text=" 🎛️ Màu CapCut + Bộ lọc Look ", padding=10)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)
        right_frame = ttk.LabelFrame(popup, text=" 🖼️ Khung 9:16 — ảnh tĩnh + video mẫu 6s ", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        from PIL import Image, ImageTk
        try:
            from capcut_filters import look_names
            look_values = [n for n in look_names() if n != "Không lọc"]
        except Exception:
            look_values = ["8K", "HDR", "Cinematic", "Vintage Film"]

        filter_box = ttk.LabelFrame(left_frame, text=" Bộ lọc (chồng nhiều lớp) ", padding=6)
        filter_box.pack(side=tk.TOP, fill=tk.X, pady=(0, 8))
        pick_row = ttk.Frame(filter_box)
        pick_row.pack(fill=tk.X)
        ttk.Label(pick_row, text="Chọn:").pack(side=tk.LEFT)
        look_pick = ttk.Combobox(pick_row, values=look_values, state="readonly", width=16)
        look_pick.pack(side=tk.LEFT, padx=4)
        if look_values:
            look_pick.set(look_values[0])
        ttk.Label(pick_row, text="Cường độ:").pack(side=tk.LEFT, padx=(8, 2))
        look_int = ttk.Spinbox(pick_row, from_=0, to=100, increment=5, width=5)
        look_int.pack(side=tk.LEFT)
        look_int.set(70)
        look_list = tk.Listbox(filter_box, height=4)
        look_list.pack(fill=tk.X, pady=4)
        stack = list(src_cfg.get("color_look_stack") or [])
        if not stack and src_cfg.get("color_look") not in (None, "", "Không lọc"):
            stack = [{"name": src_cfg.get("color_look"),
                      "intensity": float(src_cfg.get("color_look_intensity", 70))}]

        def refresh_stack_list():
            look_list.delete(0, tk.END)
            for i, item in enumerate(stack):
                look_list.insert(tk.END, f"{i+1}. {item.get('name')}  {float(item.get('intensity', 70)):.0f}%")

        canvas_container = tk.Canvas(left_frame, highlightthickness=0)
        scrollbar_y = ttk.Scrollbar(left_frame, orient="vertical", command=canvas_container.yview)
        scroll_frame = ttk.Frame(canvas_container)
        scroll_frame.bind("<Configure>", lambda e: canvas_container.configure(scrollregion=canvas_container.bbox("all")))
        canvas_container.create_window((0, 0), window=scroll_frame, anchor="nw")
        canvas_container.configure(yscrollcommand=scrollbar_y.set)
        canvas_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)

        vars_map = {}
        controls = [
            ("Nhiệt độ (Temperature):", "temperature", 0, -100, 100),
            ("Tông màu (Tint):", "tint", 0, -100, 100),
            ("Độ bão hòa (Saturation):", "saturation", 0, -100, 100),
            ("Phơi sáng (Exposure):", "exposure", 0, -100, 100),
            ("Tương phản (Contrast):", "contrast", 0, -100, 100),
            ("Vùng sáng (Highlights):", "highlights", 0, -100, 100),
            ("Bóng (Shadows):", "shadows", 0, -100, 100),
            ("Vùng trắng (Whites):", "whites", 0, -100, 100),
            ("Vùng đen (Blacks):", "blacks", 0, -100, 100),
            ("Độ chói (Brilliance):", "brilliance", 0, -100, 100),
            ("Làm sắc nét (Sharpen):", "sharpen", 0, 0, 100),
            ("Độ rõ nét (Clarity):", "clarity", 0, 0, 100),
            ("Hạt nhỏ (Grain):", "grain", 0, 0, 100),
            ("Làm mờ (Blur):", "blur", 0, 0, 100),
            ("Viền mờ dần (Vignette):", "vignette", 0, 0, 100),
        ]

        preview_dir = os.path.join("temp", "filter_preview")
        os.makedirs(preview_dir, exist_ok=True)
        layout_jpg = os.path.join(preview_dir, "layout_base.jpg")
        color_jpg = os.path.join(preview_dir, "color_still.jpg")
        preview_mp4 = os.path.join(preview_dir, "preview_6s.mp4")
        preview_jpg = os.path.join(preview_dir, "preview_frame.jpg")
        self._color_preview_tk = None
        self._color_preview_src = None
        refresh_job = {"id": None}

        preview_stage = ttk.Frame(right_frame)
        preview_stage.pack(fill=tk.BOTH, expand=True, pady=5)
        color_sim_canvas = tk.Canvas(preview_stage, bg="#111111", highlightthickness=0)
        color_sim_canvas.pack(fill=tk.BOTH, expand=True)

        def paint_color_canvas(event=None):
            cw = color_sim_canvas.winfo_width()
            ch = color_sim_canvas.winfo_height()
            if cw < 50 or ch < 50:
                cw, ch = 420, 620
            color_sim_canvas.delete("all")
            target_ratio = 1080 / 1920.0
            if cw / float(ch) > target_ratio:
                disp_h = max(80, int(ch - 24))
                disp_w = max(45, int(disp_h * target_ratio))
            else:
                disp_w = max(45, int(cw - 24))
                disp_h = max(80, int(disp_w / target_ratio))
            src = self._color_preview_src
            if src and os.path.isfile(src):
                im = Image.open(src).convert("RGB")
                im = im.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
            else:
                im = Image.new("RGB", (disp_w, disp_h), (32, 32, 36))
            self._color_preview_tk = ImageTk.PhotoImage(im)
            color_sim_canvas.create_image(int(cw / 2), int(ch / 2), image=self._color_preview_tk, anchor=tk.CENTER)
            color_sim_canvas.create_rectangle(
                int(cw / 2 - disp_w / 2), int(ch / 2 - disp_h / 2),
                int(cw / 2 + disp_w / 2), int(ch / 2 + disp_h / 2),
                outline="#666666",
            )
        color_sim_canvas.bind("<Configure>", paint_color_canvas)

        ttk.Label(right_frame, text="Video mẫu:").pack(anchor=tk.W)
        vid_row = ttk.Frame(right_frame)
        vid_row.pack(fill=tk.X, pady=4)
        vid_ent = ttk.Entry(vid_row)
        vid_ent.pack(side=tk.LEFT, fill=tk.X, expand=True)

        saved_prev = src_cfg.get("filter_preview_video", "")
        if not saved_prev or not os.path.isfile(saved_prev):
            saved_prev = src_cfg.get("crop_preview_video", "")
        if not saved_prev or not os.path.isfile(saved_prev):
            # Fallback 1: Tab Chia Part
            if hasattr(self, "part_splitter_tab") and self.part_splitter_tab:
                ps = self.part_splitter_tab
                if hasattr(ps, "video_entry") and ps.video_entry.get().strip():
                    v = ps.video_entry.get().strip()
                    if os.path.isfile(v):
                        saved_prev = v
                elif hasattr(ps, "files_data") and ps.files_data:
                    for fd in ps.files_data:
                        v = fd.get("path", "")
                        if v and os.path.isfile(v):
                            saved_prev = v
                            break
        if not saved_prev or not os.path.isfile(saved_prev):
            # Fallback 2: Tab Tóm Tắt
            if hasattr(self, "video_entry") and hasattr(self.video_entry, "get"):
                v = self.video_entry.get().strip()
                if v and os.path.isfile(v):
                    saved_prev = v

        if saved_prev and os.path.isfile(saved_prev):
            vid_ent.insert(0, saved_prev)

        start_row = ttk.Frame(right_frame)
        start_row.pack(fill=tk.X)
        ttk.Label(start_row, text="Bắt đầu (giây):").pack(side=tk.LEFT)
        start_spin = ttk.Spinbox(start_row, from_=0, to=36000, increment=1, width=8)
        start_spin.pack(side=tk.LEFT, padx=6)
        start_spin.set(src_cfg.get("filter_preview_start", 0))
        status_prev = ttk.Label(right_frame, text="Add video → hiện khung blur/zoom. Kéo màu là đổi ngay.", wraplength=380)
        status_prev.pack(anchor=tk.W, pady=4)

        def collect_layout_options():
            if isinstance(caller_config, dict):
                opts = dict(caller_config)
            else:
                opts = dict(self._collect_post_options())
            opts["color_look_stack"] = []
            opts["color_look"] = "Không lọc"
            for k in ("temperature", "tint", "saturation", "exposure", "contrast",
                      "highlights", "shadows", "whites", "blacks", "brilliance",
                      "sharpen", "clarity", "grain", "blur", "vignette"):
                opts[k] = 0
            opts["use_custom_hook"] = False
            return opts

        def collect_color_options():
            if isinstance(caller_config, dict):
                opts = dict(caller_config)
            else:
                opts = dict(self._collect_post_options())
            for k, spin_w in vars_map.items():
                try:
                    opts[k] = float(spin_w.get())
                except Exception:
                    pass
            opts["color_look_stack"] = list(stack)
            if stack:
                opts["color_look"] = stack[0].get("name", "Không lọc")
                opts["color_look_intensity"] = float(stack[0].get("intensity", 70))
            else:
                opts["color_look"] = "Không lọc"
                opts["color_look_intensity"] = 0
            opts["use_custom_hook"] = False
            return opts

        def ensure_layout_frame():
            v_path = vid_ent.get().strip()
            if not v_path or not os.path.isfile(v_path):
                status_prev.config(text="Chưa chọn video hợp lệ.")
                return False
            try:
                sec = float(start_spin.get() or 0)
            except Exception:
                sec = 0.0
            from editor_processor import EditorProcessor
            opts = collect_layout_options()
            vf = EditorProcessor._build_vf_filter(opts, input_label="[0:v]")
            cmd = [
                "ffmpeg", "-y", "-ss", str(sec), "-i", v_path,
                "-filter_complex", f"{vf};[vout]scale=540:960[outv]",
                "-map", "[outv]", "-vframes", "1", "-q:v", "2", layout_jpg
            ]
            flags = 0x08000000 if os.name == "nt" else 0
            r = subprocess.run(cmd, capture_output=True, creationflags=flags)
            if r.returncode != 0 or not os.path.isfile(layout_jpg):
                if sec > 0:
                    cmd_fallback = [
                        "ffmpeg", "-y", "-ss", "0", "-i", v_path,
                        "-filter_complex", f"{vf};[vout]scale=540:960[outv]",
                        "-map", "[outv]", "-vframes", "1", "-q:v", "2", layout_jpg
                    ]
                    r = subprocess.run(cmd_fallback, capture_output=True, creationflags=flags)

            if r.returncode != 0 or not os.path.isfile(layout_jpg):
                status_prev.config(text="Không trích xuất được khung layout.")
                return False
            return True

        def apply_color_still():
            if not os.path.isfile(layout_jpg):
                if not ensure_layout_frame():
                    return
            if not os.path.isfile(layout_jpg):
                return
            try:
                from editor_processor import EditorProcessor
                opts = collect_color_options()
                color_filter_str = EditorProcessor._build_pure_color_filter(opts)
                if color_filter_str and color_filter_str != "null":
                    vf_arg = f"[0:v]{color_filter_str}[outv]"
                else:
                    vf_arg = "[0:v]null[outv]"
                cmd = [
                    "ffmpeg", "-y", "-i", layout_jpg,
                    "-filter_complex", vf_arg,
                    "-map", "[outv]", "-vframes", "1", "-q:v", "2", color_jpg
                ]
                flags = 0x08000000 if os.name == "nt" else 0
                r = subprocess.run(cmd, capture_output=True, creationflags=flags)
                if r.returncode == 0 and os.path.isfile(color_jpg):
                    self._color_preview_src = color_jpg
                    paint_color_canvas()
                    status_prev.config(text="Đã cập nhật màu ảnh tĩnh.")
                else:
                    if os.path.isfile(layout_jpg):
                        self._color_preview_src = layout_jpg
                        paint_color_canvas()
                    status_prev.config(text="Áp dụng màu chưa xong, hiển thị ảnh gốc.")
            except Exception as e:
                print(f"[Color Studio] Exception in apply_color_still: {e}")
                if os.path.isfile(layout_jpg):
                    self._color_preview_src = layout_jpg
                    paint_color_canvas()

        def schedule_color_refresh():
            if refresh_job["id"] is not None:
                try:
                    popup.after_cancel(refresh_job["id"])
                except Exception:
                    pass
            refresh_job["id"] = popup.after(180, apply_color_still)

        def add_look():
            name = (look_pick.get() or "").strip()
            if not name:
                return
            try:
                inten = float(look_int.get())
            except Exception:
                inten = 70
            stack.append({"name": name, "intensity": max(0.0, min(100.0, inten))})
            refresh_stack_list()
            schedule_color_refresh()

        def remove_look():
            sel = look_list.curselection()
            if not sel:
                return
            stack.pop(sel[0])
            refresh_stack_list()
            schedule_color_refresh()

        def update_look_intensity():
            sel = look_list.curselection()
            if not sel:
                return
            try:
                inten = float(look_int.get())
            except Exception:
                inten = 70
            stack[sel[0]]["intensity"] = max(0.0, min(100.0, inten))
            refresh_stack_list()
            look_list.selection_set(sel[0])
            schedule_color_refresh()

        btn_fr = ttk.Frame(filter_box)
        btn_fr.pack(fill=tk.X)
        ttk.Button(btn_fr, text="➕ Add", command=add_look).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_fr, text="✎ Cường độ", command=update_look_intensity).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_fr, text="➖ Xóa", command=remove_look).pack(side=tk.LEFT, padx=2)
        refresh_stack_list()

        for idx, (label_text, key, default_val, min_v, max_v) in enumerate(controls):
            ttk.Label(scroll_frame, text=label_text, font=("Segoe UI", 9)).grid(row=idx, column=0, sticky=tk.W, pady=4, padx=5)
            val = src_cfg.get(key, default_val)
            scale_frame = ttk.Frame(scroll_frame)
            scale_frame.grid(row=idx, column=1, sticky=tk.EW, padx=5, pady=4)
            scale_w = tk.Scale(scale_frame, from_=min_v, to=max_v, orient=tk.HORIZONTAL, length=150, showvalue=False)
            scale_w.set(val)
            scale_w.pack(side=tk.LEFT, fill=tk.X, expand=True)
            spin = ttk.Spinbox(scale_frame, from_=min_v, to=max_v, increment=1, width=5)
            spin.pack(side=tk.RIGHT, padx=(5, 0))
            spin.set(val)

            def bind_sync(sc, sp):
                sc.config(command=lambda v: (sp.set(int(float(v))), schedule_color_refresh()))
                sp.config(command=lambda: (sc.set(float(sp.get() or 0)), schedule_color_refresh()))
                sp.bind("<Return>", lambda e: (sc.set(float(sp.get() or 0)), schedule_color_refresh()))

            bind_sync(scale_w, spin)
            vars_map[key] = spin

        def pick_preview_video():
            f = filedialog.askopenfilename(
                title="Chọn video mẫu",
                filetypes=[("Video", "*.mp4 *.mov *.mkv *.webm"), ("All", "*.*")]
            )
            if f:
                vid_ent.delete(0, tk.END)
                vid_ent.insert(0, f)
                ensure_layout_frame()
                apply_color_still()

        ttk.Button(vid_row, text="Chọn...", width=7, command=pick_preview_video).pack(side=tk.RIGHT, padx=(4, 0))

        def on_start_change():
            if ensure_layout_frame():
                apply_color_still()

        start_spin.config(command=on_start_change)
        start_spin.bind("<Return>", lambda e: on_start_change())

        def render_preview_6s():
            v_path = vid_ent.get().strip()
            if not v_path or not os.path.isfile(v_path):
                messagebox.showwarning("Thiếu video", "Chọn video mẫu trước khi render 6s.", parent=popup)
                return
            try:
                sec = float(start_spin.get() or 0)
            except Exception:
                sec = 0.0
            from editor_processor import EditorProcessor
            opts = collect_color_options()
            vf = EditorProcessor._build_vf_filter(opts, input_label="[0:v]")
            status_prev.config(text="Đang render 6s...")
            popup.update_idletasks()
            cmd = [
                "ffmpeg", "-y", "-ss", str(sec), "-i", v_path,
                "-t", "6",
                "-filter_complex", f"{vf};[vout]scale=540:960[outv]",
                "-map", "[outv]", "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                "-an", preview_mp4
            ]
            flags = 0x08000000 if os.name == "nt" else 0
            r = subprocess.run(cmd, capture_output=True, creationflags=flags)
            if r.returncode == 0 and os.path.isfile(preview_mp4):
                status_prev.config(text="Đã xuất xong clip 6s (preview_6s.mp4).")
            else:
                status_prev.config(text="Lỗi render 6s.")

        def play_preview_6s():
            if not os.path.isfile(preview_mp4):
                render_preview_6s()
            if not os.path.isfile(preview_mp4):
                return
            flags = 0x08000000 if os.name == "nt" else 0
            try:
                subprocess.Popen(["ffplay", "-autoexit", "-x", "360", "-y", "640", preview_mp4], creationflags=flags)
            except Exception:
                try:
                    os.startfile(preview_mp4)
                except Exception:
                    status_prev.config(text="Không mở player.")

        ttk.Button(right_frame, text="▶ Render mẫu 6s", command=render_preview_6s).pack(fill=tk.X, pady=3)
        ttk.Button(right_frame, text="⏯ Phát clip 6s", command=play_preview_6s).pack(fill=tk.X, pady=3)

        def save_and_close():
            color_data = {}
            for k, spin_w in vars_map.items():
                try:
                    color_data[k] = float(spin_w.get())
                except Exception:
                    pass
            color_data["color_look_stack"] = list(stack)
            if stack:
                color_data["color_look"] = stack[0].get("name", "Không lọc")
                color_data["color_look_intensity"] = float(stack[0].get("intensity", 70))
            else:
                color_data["color_look"] = "Không lọc"
                color_data["color_look_intensity"] = 0
            color_data["filter_preview_video"] = vid_ent.get().strip()
            try:
                color_data["filter_preview_start"] = float(start_spin.get() or 0)
            except Exception:
                color_data["filter_preview_start"] = 0

            if isinstance(caller_config, dict):
                # Gọi từ Tab Chia Part: Chỉ cập nhật caller_config, KHÔNG đụng đến self.config của Tab Tóm Tắt!
                caller_config.update(color_data)
                c_sel = caller_config.get("selected_config", "")
                if c_sel and isinstance(caller_config.get("saved_configs"), dict) and c_sel in caller_config["saved_configs"]:
                    caller_config["saved_configs"][c_sel].update(color_data)
            else:
                # Gọi từ Tab Tóm Tắt Video: Chỉ cập nhật self.config và lưu vào config.json
                self.config.update(color_data)
                sel_name = self.config.get("selected_config", "")
                if sel_name and isinstance(self.config.get("saved_configs"), dict) and sel_name in self.config["saved_configs"]:
                    self.config["saved_configs"][sel_name].update(color_data)
                save_config(self.config)

            if callable(on_save_callback):
                try:
                    on_save_callback(color_data)
                except Exception as cb_err:
                    print(f"Lỗi callback color studio: {cb_err}")

            messagebox.showinfo("Thành công", "Đã lưu màu + stack bộ lọc.", parent=popup)
            popup.destroy()

        ttk.Button(left_frame, text="💾 Lưu Cấu Hình Màu", command=save_and_close).pack(side=tk.BOTTOM, fill=tk.X, pady=(10, 5), padx=5)

        if vid_ent.get().strip() and os.path.isfile(vid_ent.get().strip()):
            popup.after(200, lambda: (ensure_layout_frame() and apply_color_still()))

        return popup

    def open_crop_tool_popup(self, on_save_callback=None, caller_config=None):
        """
        CAPCUT STYLE CROP EDITOR (Fixed Resolution Base & Scaled Preview)
        ----------------------------------------------------------------
        - W, H, X, Y dựa hoàn toàn trên hệ tọa độ chuẩn gốc của video (Ví dụ 1920x1080).
        - Ảnh mẫu load lên tự động co bóp (fit) vừa khít khung Canvas để preview trực quan.
        - Kéo thả / thay đổi spinbox tự động quy đổi chuẩn xác 100% về hệ tọa độ gốc.
        """
        import os
        import tkinter as tk
        from tkinter import ttk, filedialog, messagebox
        from PIL import Image, ImageTk
        import cv2

        popup = tk.Toplevel(self.root)
        popup.title("CapCut Pro Video Crop Studio (Fixed Resolution Edition)")
        popup.geometry("1120x880")
        popup.minsize(980, 700)
        popup.transient(self.root)
        popup.grab_set()

        src_cfg = caller_config if isinstance(caller_config, dict) else self.config

        left_frame = ttk.LabelFrame(popup, text=" ⚙ Thông số Cắt xén & Làm mờ (Hệ tọa độ gốc) ", padding=12)
        left_frame.pack(side=tk.LEFT, fill=tk.Y, padx=10, pady=10)

        right_frame = ttk.LabelFrame(popup, text=" ✂ Preview & Kéo co bóp trực quan ", padding=10)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=10, pady=10)

        use_crop_var = tk.BooleanVar(value=src_cfg.get("use_crop", False))
        ratio_var = tk.StringVar(value=src_cfg.get("crop_ratio", "Tự do"))

        # Biến trạng thái cho tính năng Blur Mask
        use_blur_var = tk.BooleanVar(value=src_cfg.get("use_blur_mask", False))
        blur_shape_var = tk.StringVar(value=src_cfg.get("blur_shape", "Hình chữ nhật"))

        ttk.Checkbutton(left_frame, text="Bật tính năng Cắt xén (Crop)", variable=use_crop_var).pack(anchor=tk.W, pady=(0, 8))

        ttk.Label(left_frame, text="Tỷ lệ khung Crop:").pack(anchor=tk.W)
        ratio_combo = ttk.Combobox(left_frame, textvariable=ratio_var, values=["Tự do", "9:16", "16:9", "1:1", "4:5", "3:4"], state="readonly", width=18)
        ratio_combo.pack(anchor=tk.W, pady=(2, 8))

        # Khung nhập kích thước gốc chuẩn video
        ttk.Label(left_frame, text="Chiều rộng gốc Video (W):").pack(anchor=tk.W)
        w_spin = ttk.Spinbox(left_frame, from_=100, to=10000, increment=10, width=18)
        w_spin.pack(anchor=tk.W, pady=(2, 6))
        w_spin.set(src_cfg.get("base_w", src_cfg.get("crop_w", 1920)))

        ttk.Label(left_frame, text="Chiều cao gốc Video (H):").pack(anchor=tk.W)
        h_spin = ttk.Spinbox(left_frame, from_=100, to=10000, increment=10, width=18)
        h_spin.pack(anchor=tk.W, pady=(2, 6))
        h_spin.set(src_cfg.get("base_h", src_cfg.get("crop_h", 1080)))

        ttk.Label(left_frame, text="Tọa độ X (Trái):").pack(anchor=tk.W)
        x_spin = ttk.Spinbox(left_frame, from_=0, to=10000, increment=1, width=18)
        x_spin.pack(anchor=tk.W, pady=(2, 6))
        x_spin.set(src_cfg.get("crop_x", 0))

        ttk.Label(left_frame, text="Tọa độ Y (Trên):").pack(anchor=tk.W)
        y_spin = ttk.Spinbox(left_frame, from_=0, to=10000, increment=1, width=18)
        y_spin.pack(anchor=tk.W, pady=(2, 8))
        y_spin.set(src_cfg.get("crop_y", 0))

        # Giao diện cấu hình Làm mờ vùng chọn (Blur Mask) kèm tùy chọn hình dạng
        blur_group = ttk.LabelFrame(left_frame, text=" 🛡️ Làm mờ Vùng Chọn (Blur Mask) ", padding=8)
        blur_group.pack(fill=tk.X, pady=(2, 8))

        ttk.Checkbutton(blur_group, text="Bật làm mờ 1 phần video", variable=use_blur_var).pack(anchor=tk.W, pady=(0, 3))
        
        ttk.Label(blur_group, text="Hình dạng làm mờ:").pack(anchor=tk.W)
        blur_shape_combo = ttk.Combobox(blur_group, textvariable=blur_shape_var, values=["Hình chữ nhật", "Hình tròn"], state="readonly", width=16)
        blur_shape_combo.pack(anchor=tk.W, pady=(2, 5))

        blur_grid = ttk.Frame(blur_group)
        blur_grid.pack(fill=tk.X)

        ttk.Label(blur_grid, text="X:").grid(row=0, column=0, sticky=tk.W, pady=2)
        blur_x_spin = ttk.Spinbox(blur_grid, from_=0, to=10000, increment=5, width=7)
        blur_x_spin.grid(row=0, column=1, sticky=tk.W, padx=2, pady=2)
        blur_x_spin.set(src_cfg.get("blur_mask_x", 100))

        ttk.Label(blur_grid, text="Y:").grid(row=0, column=2, sticky=tk.W, pady=2, padx=(4, 0))
        blur_y_spin = ttk.Spinbox(blur_grid, from_=0, to=10000, increment=5, width=7)
        blur_y_spin.grid(row=0, column=3, sticky=tk.W, padx=2, pady=2)
        blur_y_spin.set(src_cfg.get("blur_mask_y", 100))

        ttk.Label(blur_grid, text="W:").grid(row=1, column=0, sticky=tk.W, pady=2)
        blur_w_spin = ttk.Spinbox(blur_grid, from_=20, to=10000, increment=5, width=7)
        blur_w_spin.grid(row=1, column=1, sticky=tk.W, padx=2, pady=2)
        blur_w_spin.set(src_cfg.get("blur_mask_w", 300))

        ttk.Label(blur_grid, text="H:").grid(row=1, column=2, sticky=tk.W, pady=2, padx=(4, 0))
        blur_h_spin = ttk.Spinbox(blur_grid, from_=20, to=10000, increment=5, width=7)
        blur_h_spin.grid(row=1, column=3, sticky=tk.W, padx=2, pady=2)
        blur_h_spin.set(src_cfg.get("blur_mask_h", 150))

        info_frame = ttk.LabelFrame(left_frame, text=" Thông tin quy chiếu ")
        info_frame.pack(fill=tk.X, pady=(5, 10))

        source_info_var = tk.StringVar(value="Base Resolution: --")
        crop_info_var = tk.StringVar(value="Crop: --")
        ttk.Label(info_frame, textvariable=source_info_var, justify=tk.LEFT).pack(anchor=tk.W, padx=7, pady=(6, 2))
        ttk.Label(info_frame, textvariable=crop_info_var, justify=tk.LEFT).pack(anchor=tk.W, padx=7, pady=(2, 6))

        canvas_container = ttk.Frame(right_frame)
        canvas_container.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        canvas = tk.Canvas(canvas_container, bg="#181818", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)

        crop_image_path = None
        source_pil_image = None
        sample_img_tk = None
        disp_w, disp_h = 800, 450
        image_offset_x, image_offset_y = 0, 0

        crop_rect = {"x": 0, "y": 0, "w": 1920, "h": 1080}
        blur_rect = {"x": 100, "y": 100, "w": 300, "h": 150}
        
        image_id = None
        overlay_ids = []
        box_id = None
        blur_box_id = None
        handle_ids = []
        blur_handle_ids = []
        active_handle = None
        drag_start_mouse = (0, 0)
        drag_start_rect = {"x": 0, "y": 0, "w": 0, "h": 0}
        MIN_CROP_SIZE = 20

        def get_base_resolution():
            try:
                bw = int(float(w_spin.get()))
                bh = int(float(h_spin.get()))
                return max(100, bw), max(100, bh)
            except:
                return 1920, 1080

        def get_ratio_value():
            value = ratio_var.get()
            ratios = {"9:16": 9.0/16.0, "16:9": 16.0/9.0, "1:1": 1.0, "4:5": 4.0/5.0, "3:4": 3.0/4.0}
            return ratios.get(value)

        def clamp_crop():
            bw, bh = get_base_resolution()
            x = float(crop_rect["x"])
            y = float(crop_rect["y"])
            w = float(crop_rect["w"])
            h = float(crop_rect["h"])

            w = max(MIN_CROP_SIZE, min(w, bw))
            h = max(MIN_CROP_SIZE, min(h, bh))
            x = max(0, min(x, bw - w))
            y = max(0, min(y, bh - h))

            crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(round(x)), int(round(y)), int(round(w)), int(round(h))

            # Clamp blur mask
            bw_m = max(20, min(float(blur_rect["w"]), bw))
            bh_m = max(20, min(float(blur_rect["h"]), bh))
            bx_m = max(0, min(float(blur_rect["x"]), bw - bw_m))
            by_m = max(0, min(float(blur_rect["y"]), bh - bh_m))
            blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = int(bx_m), int(by_m), int(bw_m), int(bh_m)

        def load_crop_from_config():
            bw, bh = get_base_resolution()
            try:
                cfg_w = int(src_cfg.get("crop_w", bw))
                cfg_h = int(src_cfg.get("crop_h", bh))
                cfg_x = int(src_cfg.get("crop_x", 0))
                cfg_y = int(src_cfg.get("crop_y", 0))

                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = cfg_x, cfg_y, cfg_w, cfg_h

                blur_rect["x"] = int(src_cfg.get("blur_mask_x", 100))
                blur_rect["y"] = int(src_cfg.get("blur_mask_y", 100))
                blur_rect["w"] = int(src_cfg.get("blur_mask_w", 300))
                blur_rect["h"] = int(src_cfg.get("blur_mask_h", 150))

                clamp_crop()
            except:
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = 0, 0, bw, bh

        def video_to_canvas(vx, vy):
            bw, bh = get_base_resolution()
            scale_x = disp_w / float(bw) if disp_w > 0 else 1.0
            scale_y = disp_h / float(bh) if disp_h > 0 else 1.0
            return image_offset_x + vx * scale_x, image_offset_y + vy * scale_y

        def canvas_to_video(cx, cy):
            bw, bh = get_base_resolution()
            scale_x = bw / float(disp_w) if disp_w > 0 else 1.0
            scale_y = bh / float(disp_h) if disp_h > 0 else 1.0
            vx = (cx - image_offset_x) * scale_x
            vy = (cy - image_offset_y) * scale_y
            return vx, vy

        def update_info():
            bw, bh = get_base_resolution()
            source_info_var.set(f"Base Canvas: {bw} × {bh}")
            crop_info_var.set(f"Crop: X={crop_rect['x']}, Y={crop_rect['y']} | W={crop_rect['w']} × H={crop_rect['h']}")

        def update_spinboxes():
            try:
                x_spin.delete(0, tk.END); x_spin.insert(0, str(crop_rect["x"]))
                y_spin.delete(0, tk.END); y_spin.insert(0, str(crop_rect["y"]))
                
                blur_x_spin.delete(0, tk.END); blur_x_spin.insert(0, str(blur_rect["x"]))
                blur_y_spin.delete(0, tk.END); blur_y_spin.insert(0, str(blur_rect["y"]))
                blur_w_spin.delete(0, tk.END); blur_w_spin.insert(0, str(blur_rect["w"]))
                blur_h_spin.delete(0, tk.END); blur_h_spin.insert(0, str(blur_rect["h"]))

                update_info()
            except: pass

        def draw_overlay(cx1, cy1, cx2, cy2):
            nonlocal overlay_ids
            for item in overlay_ids:
                try: canvas.delete(item)
                except: pass
            overlay_ids = []
            cw, ch = canvas.winfo_width(), canvas.winfo_height()

            if cy1 > 0: overlay_ids.append(canvas.create_rectangle(0, 0, cw, cy1, fill="#000000", stipple="gray50", outline=""))
            if cy2 < ch: overlay_ids.append(canvas.create_rectangle(0, cy2, cw, ch, fill="#000000", stipple="gray50", outline=""))
            if cx1 > 0: overlay_ids.append(canvas.create_rectangle(0, cy1, cx1, cy2, fill="#000000", stipple="gray50", outline=""))
            if cx2 < cw: overlay_ids.append(canvas.create_rectangle(cx2, cy1, cw, cy2, fill="#000000", stipple="gray50", outline=""))

        def draw_crop():
            nonlocal box_id, blur_box_id, handle_ids, blur_handle_ids
            if not canvas: return
            if box_id is not None:
                try: canvas.delete(box_id)
                except: pass
            if blur_box_id is not None:
                try: canvas.delete(blur_box_id)
                except: pass
            for hid in handle_ids:
                try: canvas.delete(hid)
                except: pass
            for hid in blur_handle_ids:
                try: canvas.delete(hid)
                except: pass
            handle_ids = []
            blur_handle_ids = []

            # 1. Vẽ khung Crop
            cx1, cy1 = video_to_canvas(crop_rect["x"], crop_rect["y"])
            cx2, cy2 = video_to_canvas(crop_rect["x"] + crop_rect["w"], crop_rect["y"] + crop_rect["h"])

            draw_overlay(cx1, cy1, cx2, cy2)
            box_id = canvas.create_rectangle(cx1, cy1, cx2, cy2, outline="white", width=2)

            cs, ew, eh = 7, 18, 6
            for hx, hy in [(cx1, cy1), (cx2, cy1), (cx1, cy2), (cx2, cy2)]:
                handle_ids.append(canvas.create_rectangle(hx - cs, hy - cs, hx + cs, hy + cs, fill="white", outline="white"))

            handle_ids.append(canvas.create_rectangle((cx1+cx2)/2 - ew/2, cy1 - eh/2, (cx1+cx2)/2 + ew/2, cy1 + eh/2, fill="white", outline="white"))
            handle_ids.append(canvas.create_rectangle((cx1+cx2)/2 - ew/2, cy2 - eh/2, (cx1+cx2)/2 + ew/2, cy2 + eh/2, fill="white", outline="white"))
            handle_ids.append(canvas.create_rectangle(cx1 - eh/2, (cy1+cy2)/2 - ew/2, cx1 + eh/2, (cy1+cy2)/2 + ew/2, fill="white", outline="white"))
            handle_ids.append(canvas.create_rectangle(cx2 - eh/2, (cy1+cy2)/2 - ew/2, cx2 + eh/2, (cy1+cy2)/2 + ew/2, fill="white", outline="white"))

            # 2. Vẽ khung Blur Mask nếu bật (Hỗ trợ cả hình chữ nhật lẫn hình tròn trên preview)
            if use_blur_var.get():
                bx1, by1 = video_to_canvas(blur_rect["x"], blur_rect["y"])
                bx2, by2 = video_to_canvas(blur_rect["x"] + blur_rect["w"], blur_rect["y"] + blur_rect["h"])
                
                if blur_shape_var.get() == "Hình tròn":
                    blur_box_id = canvas.create_oval(bx1, by1, bx2, by2, outline="#FF6600", width=3, fill="#FF6600", stipple="gray25")
                else:
                    blur_box_id = canvas.create_rectangle(bx1, by1, bx2, by2, outline="#FF6600", width=3, fill="#FF6600", stipple="gray25")

                # Thêm 4 góc handle cho khung blur để kéo co bóp W, H
                for bhx, bhy in [(bx1, by1), (bx2, by1), (bx1, by2), (bx2, by2)]:
                    blur_handle_ids.append(canvas.create_rectangle(bhx - cs, bhy - cs, bhx + cs, bhy + cs, fill="#FF6600", outline="white"))

            canvas.tag_raise(box_id)
            for hid in handle_ids: canvas.tag_raise(hid)
            if use_blur_var.get() and blur_box_id:
                canvas.tag_raise(blur_box_id)
                for bhid in blur_handle_ids: canvas.tag_raise(bhid)

            update_spinboxes()

        def calculate_display_size():
            nonlocal disp_w, disp_h, image_offset_x, image_offset_y
            cw, ch = canvas.winfo_width(), canvas.winfo_height()
            if cw < 100: cw = 800
            if ch < 100: ch = 600

            bw, bh = get_base_resolution()
            video_ratio = bw / float(bh)
            aw, ah = max(100, cw - 20), max(100, ch - 20)

            if aw / float(ah) > video_ratio:
                disp_h = int(ah)
                disp_w = int(disp_h * video_ratio)
            else:
                disp_w = int(aw)
                disp_h = int(disp_w / video_ratio)

            image_offset_x = int((cw - disp_w) / 2)
            image_offset_y = int((ch - disp_h) / 2)

        def redraw_canvas():
            nonlocal sample_img_tk, image_id
            if source_pil_image is None: return
            calculate_display_size()
            
            resized = source_pil_image.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
            sample_img_tk = ImageTk.PhotoImage(resized)

            if image_id is not None:
                try: canvas.delete(image_id)
                except: pass
            image_id = canvas.create_image(image_offset_x, image_offset_y, image=sample_img_tk, anchor=tk.NW)
            draw_crop()

        def load_sample_frame(img_path=None):
            nonlocal crop_image_path, source_pil_image
            try:
                if not img_path:
                    base_temp = "temp"
                    if os.path.exists(base_temp):
                        sub_dirs = [os.path.join(base_temp, d) for d in os.listdir(base_temp) if os.path.isdir(os.path.join(base_temp, d))]
                        if sub_dirs:
                            latest_dir = max(sub_dirs, key=os.path.getmtime)
                            sample_vid = os.path.join(latest_dir, "source_video_low.mp4")
                            if not os.path.exists(sample_vid): sample_vid = os.path.join(latest_dir, "source_video.mp4")
                            if os.path.exists(sample_vid):
                                cap = cv2.VideoCapture(sample_vid)
                                cap.set(cv2.CAP_PROP_POS_MSEC, 2000)
                                ret, frame = cap.read()
                                cap.release()
                                if ret:
                                    img_path = os.path.join(latest_dir, "crop_preview_sample.jpg")
                                    cv2.imwrite(img_path, frame)

                if img_path and os.path.exists(img_path):
                    crop_image_path = img_path
                    source_pil_image = Image.open(img_path).convert("RGB")

                bw, bh = get_base_resolution()
                crop_rect["x"] = 0
                crop_rect["y"] = 0
                crop_rect["w"] = bw
                crop_rect["h"] = bh

                clamp_crop()
                update_spinboxes()
                redraw_canvas()
            except Exception as e:
                print(f"Lỗi load ảnh preview crop: {e}")

        # Kiểm tra vị trí click chuột để nhận diện kéo thả hoặc co giãn Blur Mask
        def hit_test(event_x, event_y):
            if use_blur_var.get():
                bx1, by1 = video_to_canvas(blur_rect["x"], blur_rect["y"])
                bx2, by2 = video_to_canvas(blur_rect["x"] + blur_rect["w"], blur_rect["y"] + blur_rect["h"])
                hit = 14

                # Kiểm tra 4 góc handle của Blur Mask để resize W, H
                if abs(event_x - bx1) <= hit and abs(event_y - by1) <= hit: return "blur_tl"
                if abs(event_x - bx2) <= hit and abs(event_y - by1) <= hit: return "blur_tr"
                if abs(event_x - bx1) <= hit and abs(event_y - by2) <= hit: return "blur_bl"
                if abs(event_x - bx2) <= hit and abs(event_y - by2) <= hit: return "blur_br"
                
                # Click vào giữa khung Blur Mask để di chuyển
                if bx1 < event_x < bx2 and by1 < event_y < by2: return "move_blur"

            cx1, cy1 = video_to_canvas(crop_rect["x"], crop_rect["y"])
            cx2, cy2 = video_to_canvas(crop_rect["x"] + crop_rect["w"], crop_rect["y"] + crop_rect["h"])
            hit = 14

            if abs(event_x - cx1) <= hit and abs(event_y - cy1) <= hit: return "tl"
            if abs(event_x - cx2) <= hit and abs(event_y - cy1) <= hit: return "tr"
            if abs(event_x - cx1) <= hit and abs(event_y - cy2) <= hit: return "bl"
            if abs(event_x - cx2) <= hit and abs(event_y - cy2) <= hit: return "br"
            if abs(event_y - cy1) <= hit and cx1 <= event_x <= cx2: return "top"
            if abs(event_y - cy2) <= hit and cx1 <= event_x <= cx2: return "bottom"
            if abs(event_x - cx1) <= hit and cy1 <= event_y <= cy2: return "left"
            if abs(event_x - cx2) <= hit and cy1 <= event_y <= cy2: return "right"
            if cx1 < event_x < cx2 and cy1 < event_y < cy2: return "move"
            return None

        def free_resize(handle, mouse_x, mouse_y):
            bw, bh = get_base_resolution()
            vx, vy = canvas_to_video(mouse_x, mouse_y)
            sx, sy, sw, sh = drag_start_rect["x"], drag_start_rect["y"], drag_start_rect["w"], drag_start_rect["h"]
            x2, y2 = sx + sw, sy + sh

            if handle == "tl":
                nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(nx), int(ny), int(x2 - nx), int(y2 - ny)
            elif handle == "tr":
                nx2 = max(min(bw, vx), sx + MIN_CROP_SIZE)
                ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(sx), int(ny), int(nx2 - sx), int(y2 - ny)
            elif handle == "bl":
                nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                ny2 = max(min(bh, vy), sy + MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(nx), int(sy), int(x2 - nx), int(ny2 - sy)
            elif handle == "br":
                nx2 = max(min(bw, vx), sx + MIN_CROP_SIZE)
                ny2 = max(min(bh, vy), sy + MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(sx), int(sy), int(nx2 - sx), int(ny2 - sy)
            elif handle == "top":
                ny = min(max(0, vy), y2 - MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(sx), int(ny), int(sw), int(y2 - ny)
            elif handle == "bottom":
                ny2 = max(min(bh, vy), sy + MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(sx), int(sy), int(sw), int(ny2 - sy)
            elif handle == "left":
                nx = min(max(0, vx), x2 - MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(nx), int(sy), int(x2 - nx), int(sh)
            elif handle == "right":
                nx2 = max(min(bw, vx), sx + MIN_CROP_SIZE)
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = int(sx), int(sy), int(nx2 - sx), int(sh)
            clamp_crop()

        # Hàm resize riêng cho Blur Mask khi kéo 4 góc handle
        def resize_blur(handle, mouse_x, mouse_y):
            bw, bh = get_base_resolution()
            vx, vy = canvas_to_video(mouse_x, mouse_y)
            sx, sy, sw, sh = drag_start_rect["x"], drag_start_rect["y"], drag_start_rect["w"], drag_start_rect["h"]
            x2, y2 = sx + sw, sy + sh

            if handle == "blur_tl":
                nx = min(max(0, vx), x2 - 20)
                ny = min(max(0, vy), y2 - 20)
                blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = int(nx), int(ny), int(x2 - nx), int(y2 - ny)
            elif handle == "blur_tr":
                nx2 = max(min(bw, vx), sx + 20)
                ny = min(max(0, vy), y2 - 20)
                blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = int(sx), int(ny), int(nx2 - sx), int(y2 - ny)
            elif handle == "blur_bl":
                nx = min(max(0, vx), x2 - 20)
                ny2 = max(min(bh, vy), sy + 20)
                blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = int(nx), int(sy), int(x2 - nx), int(ny2 - sy)
            elif handle == "blur_br":
                nx2 = max(min(bw, vx), sx + 20)
                ny2 = max(min(bh, vy), sy + 20)
                blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = int(sx), int(sy), int(nx2 - sx), int(ny2 - sy)
            clamp_crop()

        def move_crop(mouse_x, mouse_y):
            bw, bh = get_base_resolution()
            start_vx, start_vy = canvas_to_video(drag_start_mouse[0], drag_start_mouse[1])
            curr_vx, curr_vy = canvas_to_video(mouse_x, mouse_y)
            dx, dy = curr_vx - start_vx, curr_vy - start_vy

            nx = max(0, min(bw - drag_start_rect["w"], drag_start_rect["x"] + dx))
            ny = max(0, min(bh - drag_start_rect["h"], drag_start_rect["y"] + dy))
            crop_rect["x"], crop_rect["y"] = int(round(nx)), int(round(ny))
            crop_rect["w"], crop_rect["h"] = int(drag_start_rect["w"]), int(drag_start_rect["h"])

        def move_blur(mouse_x, mouse_y):
            bw, bh = get_base_resolution()
            start_vx, start_vy = canvas_to_video(drag_start_mouse[0], drag_start_mouse[1])
            curr_vx, curr_vy = canvas_to_video(mouse_x, mouse_y)
            dx, dy = curr_vx - start_vx, curr_vy - start_vy

            nx = max(0, min(bw - drag_start_rect["w"], drag_start_rect["x"] + dx))
            ny = max(0, min(bh - drag_start_rect["h"], drag_start_rect["y"] + dy))
            blur_rect["x"], blur_rect["y"] = int(round(nx)), int(round(ny))
            blur_rect["w"], blur_rect["h"] = int(drag_start_rect["w"]), int(drag_start_rect["h"])

        def on_press(event):
            nonlocal active_handle, drag_start_mouse, drag_start_rect
            active_handle = hit_test(event.x, event.y)
            if not active_handle: return
            drag_start_mouse = (event.x, event.y)
            if active_handle == "move_blur":
                drag_start_rect = {"x": blur_rect["x"], "y": blur_rect["y"], "w": blur_rect["w"], "h": blur_rect["h"]}
            elif active_handle in ["blur_tl", "blur_tr", "blur_bl", "blur_br"]:
                drag_start_rect = {"x": blur_rect["x"], "y": blur_rect["y"], "w": blur_rect["w"], "h": blur_rect["h"]}
            else:
                drag_start_rect = {"x": crop_rect["x"], "y": crop_rect["y"], "w": crop_rect["w"], "h": crop_rect["h"]}

        def on_drag(event):
            if not active_handle: return
            try:
                if active_handle == "move_blur": move_blur(event.x, event.y)
                elif active_handle in ["blur_tl", "blur_tr", "blur_bl", "blur_br"]: resize_blur(active_handle, event.x, event.y)
                elif active_handle == "move": move_crop(event.x, event.y)
                else: free_resize(active_handle, event.x, event.y)
                update_spinboxes(); draw_crop()
            except Exception as e: print(f"Crop/Blur drag error: {e}")

        def on_release(event):
            nonlocal active_handle
            active_handle = None
            clamp_crop(); update_spinboxes(); draw_crop()

        def apply_spinbox_values(event=None):
            bw, bh = get_base_resolution()
            try:
                x = int(float(x_spin.get()))
                y = int(float(y_spin.get()))
                w = int(float(w_spin.get()))
                h = int(float(h_spin.get()))

                w = max(MIN_CROP_SIZE, min(w, bw))
                h = max(MIN_CROP_SIZE, min(h, bh))
                x = max(0, min(x, bw - w))
                y = max(0, min(y, bh - h))
                crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = x, y, w, h

                bx = int(float(blur_x_spin.get()))
                by = int(float(blur_y_spin.get()))
                bw_m = int(float(blur_w_spin.get()))
                bh_m = int(float(blur_h_spin.get()))
                blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = bx, by, bw_m, bh_m

                clamp_crop()
                update_spinboxes(); draw_crop()
            except: pass

        for sp in [x_spin, y_spin, w_spin, h_spin, blur_x_spin, blur_y_spin, blur_w_spin, blur_h_spin]:
            sp.bind("<Return>", apply_spinbox_values)

        def reset_crop():
            bw, bh = get_base_resolution()
            crop_rect["x"], crop_rect["y"], crop_rect["w"], crop_rect["h"] = 0, 0, bw, bh
            blur_rect["x"], blur_rect["y"], blur_rect["w"], blur_rect["h"] = 100, 100, 300, 150
            clamp_crop(); update_spinboxes(); draw_crop()

        btn_reload_frame = ttk.Button(left_frame, text="🔄 Load lại khung hình", command=lambda: load_sample_frame())
        btn_reload_frame.pack(anchor=tk.W, fill=tk.X, pady=4)

        def upload_custom_image():
            file_path = filedialog.askopenfilename(title="Chọn ảnh mẫu video", filetypes=[("Image Files", "*.jpg *.jpeg *.png *.webp"), ("All Files", "*.*")])
            if file_path: load_sample_frame(file_path)

        btn_upload = ttk.Button(left_frame, text="📂 Tải ảnh mẫu từ máy...", command=upload_custom_image)
        btn_upload.pack(anchor=tk.W, fill=tk.X, pady=4)

        btn_reset = ttk.Button(left_frame, text="↩ Reset Crop", command=reset_crop)
        btn_reset.pack(anchor=tk.W, fill=tk.X, pady=4)

        canvas.bind("<ButtonPress-1>", on_press)
        canvas.bind("<B1-Motion>", on_drag)
        canvas.bind("<ButtonRelease-1>", on_release)

        def update_cursor(event):
            if not canvas: return
            handle = hit_test(event.x, event.y)
            cursor = "size_nw_se" if handle in ["tl", "br", "blur_tl", "blur_br"] else ("size_ne_sw" if handle in ["tr", "bl", "blur_tr", "blur_bl"] else ("size_we" if handle in ["left", "right"] else ("size_ns" if handle in ["top", "bottom"] else ("fleur" if handle in ["move", "move_blur"] else ""))))
            try: canvas.configure(cursor=cursor)
            except: pass

        canvas.bind("<Motion>", update_cursor)
        canvas.bind("<Configure>", lambda e: popup.after_idle(redraw_canvas))

        def save_crop_config():
            try:
                clamp_crop()
                base_w, base_h = get_base_resolution()
                
                crop_data = {
                    "use_crop": use_crop_var.get(),
                    "crop_w": int(crop_rect["w"]),
                    "crop_h": int(crop_rect["h"]),
                    "crop_x": int(crop_rect["x"]),
                    "crop_y": int(crop_rect["y"]),
                    "crop_ratio": ratio_var.get(),
                    "use_blur_mask": use_blur_var.get(),
                    "blur_shape": blur_shape_var.get(),
                    "blur_mask_x": int(blur_rect["x"]),
                    "blur_mask_y": int(blur_rect["y"]),
                    "blur_mask_w": int(blur_rect["w"]),
                    "blur_mask_h": int(blur_rect["h"]),
                    "base_w": base_w,
                    "base_h": base_h,
                }

                if isinstance(caller_config, dict):
                    # Gọi từ Tab Chia Part: Chỉ cập nhật caller_config, KHÔNG đụng đến self.config của Tab Tóm Tắt!
                    caller_config.update(crop_data)
                    c_sel = caller_config.get("selected_config", "")
                    if c_sel and isinstance(caller_config.get("saved_configs"), dict) and c_sel in caller_config["saved_configs"]:
                        caller_config["saved_configs"][c_sel].update(crop_data)
                else:
                    # Gọi từ Tab Tóm Tắt Video: Chỉ cập nhật self.config và lưu vào config.json
                    self.config.update(crop_data)
                    sel_name = self.config.get("selected_config", "")
                    if sel_name and isinstance(self.config.get("saved_configs"), dict) and sel_name in self.config["saved_configs"]:
                        self.config["saved_configs"][sel_name].update(crop_data)
                    save_config(self.config)

                if callable(on_save_callback):
                    try:
                        on_save_callback(crop_data)
                    except Exception as cb_err:
                        print(f"Lỗi callback crop studio: {cb_err}")

                messagebox.showinfo("Thành công", f"Đã lưu cấu hình Cắt xén & Làm mờ vùng chọn!", parent=popup)
                popup.destroy()
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể lưu Cắt xén & Làm mờ:\n{e}", parent=popup)

        btn_save_crop = ttk.Button(left_frame, text="💾 Lưu Cấu Hình Cắt Xén & Làm Mờ", command=save_crop_config)
        btn_save_crop.pack(side=tk.BOTTOM, fill=tk.X, pady=(15, 0))

        load_sample_frame()
        load_crop_from_config()
        update_spinboxes()
        draw_crop()
        
        popup.focus_force()
        return popup

    def open_title_sub_studio_popup(self, on_save_callback=None, caller_config=None):
        """Cửa sổ Studio tùy chỉnh tọa độ và màu sắc chi tiết, lưu chuẩn mã màu hiện tại."""
        from tkinter import colorchooser

        popup = tk.Toplevel(self.root)
        popup.title("Title & Subtitle Studio (Detailed Colors & Save)")
        popup.geometry("700x680")
        popup.minsize(620, 520)
        popup.transient(self.root)
        popup.grab_set()

        src_cfg = caller_config if isinstance(caller_config, dict) else self.config

        # 1. THANH NÚT BẤM CỐ ĐỊNH Ở ĐÁY CỬA SỔ (LUÔN HIỂN THỊ 100%, KHÔNG THỂ BỊ CHE KHUẤT)
        bottom_bar = ttk.Frame(popup, padding=(15, 8, 15, 12))
        bottom_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # 2. KHU VỰC NỘI DUNG CÓ THỂ CUỘN (SCROLLABLE CANVAS)
        content_container = ttk.Frame(popup)
        content_container.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        canvas = tk.Canvas(content_container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(content_container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        def _on_canvas_configure(event):
            canvas.itemconfig(canvas_window, width=event.width)
        canvas.bind("<Configure>", _on_canvas_configure)

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        popup.bind("<MouseWheel>", _on_mousewheel)

        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        main_frame = ttk.LabelFrame(scrollable_frame, text=" ⚙ Bảng Điều Khiển Thông Số & Màu Sắc Chi Tiết ", padding=12)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=10)

        # --- NHÓM TITLE ---
        title_group = ttk.LabelFrame(main_frame, text=" 📌 Cấu hình Tiêu đề (Title Banner) ", padding=10)
        title_group.pack(fill=tk.X, pady=(0, 10))

        enable_title_var = tk.BooleanVar(value=bool(src_cfg.get("enable_title", True)))
        cb_enable_title = ttk.Checkbutton(title_group, text="✅ Hiển thị Tiêu đề (Title Banner)", variable=enable_title_var)
        cb_enable_title.grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))

        ttk.Label(title_group, text="Tọa độ Y (Lên/Xuống):").grid(row=1, column=0, sticky=tk.W, pady=3)
        title_y_spin = ttk.Spinbox(title_group, from_=0, to=1000, increment=10, width=12)
        title_y_spin.grid(row=1, column=1, sticky=tk.W, padx=5, pady=3)
        title_y_spin.set(src_cfg.get("title_y_pos", 260))

        # Hàm cập nhật màu nền ô preview trực quan dựa trên mã màu hiện tại
        def update_preview_box(box_widget, color_val):
            try:
                color_val = color_val.strip()
                if color_val.startswith("#"):
                    box_widget.config(bg=color_val)
                elif color_val.startswith("&H"):
                    clean = color_val.replace("&H", "").replace("&", "")
                    if len(clean) == 6:
                        b, g, r = clean[0:2], clean[2:4], clean[4:6]
                        box_widget.config(bg=f"#{r}{g}{b}")
                    else:
                        box_widget.config(bg="#FFFFFF")
                else:
                    # Hỗ trợ các từ khóa như black, red, white, none...
                    box_widget.config(bg="lightgray" if color_val == "none" else color_val)
            except:
                box_widget.config(bg="#FFFFFF")

        def pick_color(entry_widget, preview_box):
            color_code = colorchooser.askcolor(title="Chọn màu chi tiết")[1] # Trả về #RRGGBB
            if color_code:
                entry_widget.delete(0, tk.END)
                entry_widget.insert(0, color_code)
                update_preview_box(preview_box, color_code)

        def create_color_row(parent, row_idx, label_text, config_key, default_val, is_ass=False):
            ttk.Label(parent, text=label_text).grid(row=row_idx, column=0, sticky=tk.W, pady=3)
            
            entry = ttk.Entry(parent, width=14)
            entry.grid(row=row_idx, column=1, sticky=tk.W, padx=5, pady=3)
            current_val = src_cfg.get(config_key, default_val)
            entry.insert(0, current_val)

            preview_box = tk.Label(parent, width=3, height=1, relief=tk.SOLID, borderwidth=1)
            preview_box.grid(row=row_idx, column=2, padx=5, pady=3)
            update_preview_box(preview_box, current_val)

            entry.bind("<KeyRelease>", lambda e, eb=preview_box, en=entry: update_preview_box(eb, en.get()))

            def on_pick(en=entry, eb=preview_box, ass_mode=is_ass):
                if ass_mode:
                    color_code = colorchooser.askcolor(title="Chọn màu phụ đề")[1]
                    if color_code:
                        hex_clean = color_code.lstrip('#')
                        r, g, b = hex_clean[0:2], hex_clean[2:4], hex_clean[4:6]
                        ass_format = f"&H{b}{g}{r}&".upper()
                        en.delete(0, tk.END)
                        en.insert(0, ass_format)
                        update_preview_box(eb, ass_format)
                else:
                    color_code = colorchooser.askcolor(title="Chọn màu chi tiết")[1]
                    if color_code:
                        en.delete(0, tk.END)
                        en.insert(0, color_code)
                        update_preview_box(eb, color_code)

            btn = ttk.Button(parent, text="🎨 Chọn", command=on_pick, width=7)
            btn.grid(row=row_idx, column=3, padx=5, pady=3)
            return entry

        t_c1_entry = create_color_row(title_group, 2, "Màu dòng 1:", "title_color1", "black")
        t_c2_entry = create_color_row(title_group, 3, "Màu dòng 2:", "title_color2", "red")
        t_outline_entry = create_color_row(title_group, 4, "Màu viền Title:", "title_outline_color", "none")
        t_bg_entry = create_color_row(title_group, 5, "Màu nền Banner:", "title_bg_color", "white")

        # --- NHÓM SUBTITLE ---
        sub_group = ttk.LabelFrame(main_frame, text=" 💬 Cấu hình Phụ đề (Subtitle Style) ", padding=10)
        sub_group.pack(fill=tk.X, pady=(0, 10))

        enable_sub_var = tk.BooleanVar(value=bool(src_cfg.get("enable_sub", True)))
        cb_enable_sub = ttk.Checkbutton(sub_group, text="✅ Hiển thị Phụ đề (Subtitle)", variable=enable_sub_var)
        cb_enable_sub.grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))

        # 3 Kiểu Style hiển thị trực quan
        current_sub_style = src_cfg.get("sub_style_type", "tiktok_slim")
        if current_sub_style not in ["classic", "tiktok_slim", "standard"]:
            current_sub_style = "tiktok_slim"
        sub_style_var = tk.StringVar(value=current_sub_style)

        style_frame = ttk.Frame(sub_group)
        style_frame.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(0, 8))
        ttk.Label(style_frame, text="Kiểu phụ đề:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 8))

        def apply_sub_style_preset(selected_key=None):
            if selected_key is None:
                selected_key = sub_style_var.get()
            else:
                sub_style_var.set(selected_key)
            if selected_key == "classic":
                sub_size_spin.set(26)
                sub_outline_spin.set(3)
                sub_shadow_spin.set(0)
                sub_margin_spin.set(80)
            elif selected_key == "tiktok_slim":
                sub_size_spin.set(16)
                sub_outline_spin.set(2)
                sub_shadow_spin.set(1)
                sub_margin_spin.set(100)
            else:  # standard
                sub_size_spin.set(21)
                sub_outline_spin.set(2)
                sub_shadow_spin.set(0)
                sub_margin_spin.set(90)

        rb_classic = ttk.Radiobutton(style_frame, text="🏛️ Cổ điển (In hoa to)", variable=sub_style_var, value="classic", command=lambda: apply_sub_style_preset("classic"))
        rb_classic.pack(side=tk.LEFT, padx=4)

        rb_slim = ttk.Radiobutton(style_frame, text="✨ Thanh mảnh (Giống No.1)", variable=sub_style_var, value="tiktok_slim", command=lambda: apply_sub_style_preset("tiktok_slim"))
        rb_slim.pack(side=tk.LEFT, padx=4)

        rb_std = ttk.Radiobutton(style_frame, text="📺 Bình thường (Tiêu chuẩn)", variable=sub_style_var, value="standard", command=lambda: apply_sub_style_preset("standard"))
        rb_std.pack(side=tk.LEFT, padx=4)

        ttk.Label(sub_group, text="Cỡ chữ Sub:").grid(row=2, column=0, sticky=tk.W, pady=3)
        sub_size_spin = ttk.Spinbox(sub_group, from_=10, to=48, increment=1, width=12)
        sub_size_spin.grid(row=2, column=1, sticky=tk.W, padx=5, pady=3)
        sub_size_spin.set(src_cfg.get("sub_size", 16 if current_sub_style == "tiktok_slim" else 22))

        ttk.Label(sub_group, text="Độ dày viền:").grid(row=3, column=0, sticky=tk.W, pady=3)
        sub_outline_spin = ttk.Spinbox(sub_group, from_=1, to=8, increment=1, width=12)
        sub_outline_spin.grid(row=3, column=1, sticky=tk.W, padx=5, pady=3)
        sub_outline_spin.set(src_cfg.get("sub_outline", 2 if current_sub_style == "tiktok_slim" else 3))

        ttk.Label(sub_group, text="Bóng mờ 3D (Shadow):").grid(row=4, column=0, sticky=tk.W, pady=3)
        sub_shadow_spin = ttk.Spinbox(sub_group, from_=0, to=4, increment=1, width=12)
        sub_shadow_spin.grid(row=4, column=1, sticky=tk.W, padx=5, pady=3)
        sub_shadow_spin.set(src_cfg.get("sub_shadow", 1 if current_sub_style == "tiktok_slim" else 0))

        ttk.Label(sub_group, text="MarginV (Lề đáy):").grid(row=5, column=0, sticky=tk.W, pady=3)
        sub_margin_spin = ttk.Spinbox(sub_group, from_=20, to=600, increment=10, width=12)
        sub_margin_spin.grid(row=5, column=1, sticky=tk.W, padx=5, pady=3)
        sub_margin_spin.set(src_cfg.get("sub_margin_v", 100))

        sub_c_entry = create_color_row(sub_group, 6, "Màu chữ Sub:", "sub_color", "&HFFFFFF&", is_ass=True)
        sub_outline_c_entry = create_color_row(sub_group, 7, "Màu viền Sub:", "sub_outline_color", "&H000000&", is_ass=True)
        lbl_locale_note = ttk.Label(sub_group, text="🌍 Tự động đổi font theo Quốc gia (Nga, Đức, Pháp, Việt, Ả Rập, CJK...) — Luôn sắc nét 100%, chống lỗi font [].", foreground="#2563EB", font=("Segoe UI", 8, "italic"))
        lbl_locale_note.grid(row=8, column=0, columnspan=4, sticky=tk.W, pady=(6, 2))

        def save_studio_config():
            # Lưu chuẩn xác toàn bộ giá trị màu hiện tại đang hiển thị trên giao diện vào config
            sub_data = {
                "enable_title": bool(enable_title_var.get()),
                "enable_sub": bool(enable_sub_var.get()),
                "title_y_pos": int(title_y_spin.get()),
                "title_color1": t_c1_entry.get().strip(),
                "title_color2": t_c2_entry.get().strip(),
                "title_outline_color": t_outline_entry.get().strip(),
                "title_bg_color": t_bg_entry.get().strip(),
                "sub_style_type": sub_style_var.get(),
                "sub_size": int(sub_size_spin.get()),
                "sub_outline": int(sub_outline_spin.get()),
                "sub_shadow": int(sub_shadow_spin.get()),
                "sub_margin_v": int(sub_margin_spin.get()),
                "sub_color": sub_c_entry.get().strip(),
                "sub_outline_color": sub_outline_c_entry.get().strip(),
            }

            if isinstance(caller_config, dict):
                # Gọi từ Tab Chia Part: Chỉ cập nhật caller_config, KHÔNG đụng đến self.config của Tab Tóm Tắt!
                caller_config.update(sub_data)
                c_sel = caller_config.get("selected_config", "")
                if c_sel and isinstance(caller_config.get("saved_configs"), dict) and c_sel in caller_config["saved_configs"]:
                    caller_config["saved_configs"][c_sel].update(sub_data)
            else:
                # Gọi từ Tab Tóm Tắt Video: Chỉ cập nhật self.config và lưu vào config.json
                self.config.update(sub_data)
                sel_name = self.config.get("selected_config", "")
                if sel_name and isinstance(self.config.get("saved_configs"), dict) and sel_name in self.config["saved_configs"]:
                    self.config["saved_configs"][sel_name].update(sub_data)
                save_config(self.config)

            if callable(on_save_callback):
                try:
                    on_save_callback(sub_data)
                except Exception as cb_err:
                    print(f"Lỗi callback title sub studio: {cb_err}")

            messagebox.showinfo("Thành công", "Đã lưu chuẩn xác cấu hình Title & Subtitle!", parent=popup)
            popup.destroy()

        btn_save = ttk.Button(bottom_bar, text="💾 Lưu Cấu Hình Studio", command=save_studio_config)
        btn_save.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        btn_close = ttk.Button(bottom_bar, text="✖ Đóng", command=popup.destroy, width=10)
        btn_close.pack(side=tk.RIGHT)

        popup.focus_force()
        return popup

    def save_configs_to_json(self):
        """Lưu toàn bộ trạng thái của mọi widget trên GUI vào từ điển config và ghi xuống file JSON."""
        self.config["youtube_url"] = self.url_entry.get().strip()
        self.config["hook_time"] = self.hook_time_entry.get().strip()
        self.config["hook_duration"] = self.hook_duration_entry.get().strip()
        if hasattr(self, "part_count_var"):
            try:
                part_count, part_times, _ = self._part_settings_value()
            except ValueError:
                part_count = max(0, int(self.part_count_var.get() or 1))
                part_times = [entry.get().strip() for entry in self.part_split_entries]
            self.config["part_count"] = part_count
            self.config["part_cut_times"] = part_times
        if hasattr(self, "show_cmd_var"):
            self.config["show_cmd_log"] = bool(self.show_cmd_var.get())
        if hasattr(self, "source_mode_var"):
            self.config["source_mode"] = self.source_mode_var.get()
        if hasattr(self, "local_path_entry"):
            self.config["local_source_path"] = self.local_path_entry.get().strip()
        if hasattr(self, "workflow_mode_var"):
            self.config["workflow_mode"] = self.workflow_mode_var.get()
        if hasattr(self, "comp_source_type_var"):
            self.config["compilation_source_type"] = self.comp_source_type_var.get()
        if hasattr(self, "comp_source_path_entry"):
            self.config["compilation_source_path"] = self.comp_source_path_entry.get().strip()
        if hasattr(self, "comp_clip_count_spin"):
            try:
                self.config["compilation_clip_count"] = int(self.comp_clip_count_spin.get() or 5)
            except ValueError:
                self.config["compilation_clip_count"] = 5
        # Gộp chung thời lượng video / voice thành 1 giá trị duy nhất
        dur_val = self._minimum_duration_value()
        self.config["compilation_target_duration"] = dur_val
        self.config["video_min_duration_sec"] = dur_val
        if hasattr(self, "comp_title_style_cb"):
            cb_val = self.comp_title_style_cb.get().lower()
            if "không hiện" in cb_val or "chỉ hiện" in cb_val or "only" in cb_val:
                self.config["compilation_title_style"] = "only_rank"
            elif "giật gân" in cb_val or "ai" in cb_val:
                self.config["compilation_title_style"] = "ai_generated"
            else:
                self.config["compilation_title_style"] = "clean_original"
        if hasattr(self, "comp_teaser_var"):
            self.config["compilation_enable_teaser"] = bool(self.comp_teaser_var.get())
        if hasattr(self, "comp_teaser_duration_spin"):
            try:
                self.config["compilation_teaser_duration"] = max(
                    0.5, min(30.0, float(self.comp_teaser_duration_spin.get() or 3.0))
                )
            except ValueError:
                self.config["compilation_teaser_duration"] = 3.0
        if hasattr(self, "comp_rank_by_cb"):
            rb_val = self.comp_rank_by_cb.get().lower()
            if "thích" in rb_val or "like" in rb_val:
                self.config["compilation_rank_by"] = "likes"
            elif "nhiên" in rb_val or "random" in rb_val:
                self.config["compilation_rank_by"] = "random"
            else:
                self.config["compilation_rank_by"] = "views"
        
        # --- BỔ SUNG: LƯU TRẠNG THÁI CUSTOM HOOK ---
        if hasattr(self, 'use_custom_hook_var'):
            hook_mode = self._hook_mode_value()
            self.config["hook_mode"] = hook_mode
            self.config["use_custom_hook"] = hook_mode == "ai"
        if hasattr(self, "use_ai_voice_var"):
            self.config["use_ai_voice"] = bool(self.use_ai_voice_var.get())
        if hasattr(self, "mix_original_audio_var"):
            self.config["mix_original_audio_with_voice"] = bool(self.mix_original_audio_var.get())
        if hasattr(self, "original_audio_mix_spin"):
            try:
                self.config["original_audio_mix_percent"] = max(
                    0.0, min(100.0, float(self.original_audio_mix_spin.get() or 50.0))
                )
            except (TypeError, ValueError):
                self.config["original_audio_mix_percent"] = 50.0
        
        self.config["ai_model"] = self.ai_model_cb.get()
        self.config["api_key"] = self.api_key_entry.get().strip()
        self.config["engine_tts"] = self.engine_cb.get()
        self.config["country"] = self.country_cb.get()
        self.config["voice"] = self.voice_cb.get()
        if hasattr(self, "market_cb"):
            market_code = market_code_from_label(self.market_cb.get())
            profile = get_market_profile(market_code)
            self.config["target_market"] = market_code
            self.config["output_language"] = profile["language"]
            self.config["output_locale"] = profile["locale"]
        if hasattr(self, "auto_voice_locale_var"):
            self.config["auto_voice_locale"] = bool(self.auto_voice_locale_var.get())
        self.config["selected_prompt"] = self.prompt_cb.get()
        if hasattr(self, "video_min_duration_spin"):
            self.config["video_min_duration_sec"] = self._minimum_duration_value()
        
        self.config["speed"] = float(self.speed_spin.get())
        self.config["audio_boost"] = float(self.audio_boost_spin.get())
        self.config["blur_bg"] = self.blur_var.get()
        self.config["zoom_in"] = self.zoom_var.get()
        self.config["zoom_percent"] = float(self.zoom_spin.get())
        self.config["scale_w"] = float(self.scale_w_spin.get())
        self.config["scale_h"] = float(self.scale_h_spin.get())
        self.config["broll_mode"] = self._broll_mode_value()
        self.config["broll_max_sec"] = self._broll_max_sec_value()
        if hasattr(self, "random_broll_mirror_var"):
            self.config["random_broll_mirror"] = bool(self.random_broll_mirror_var.get())
        if hasattr(self, "cleanup_temp_var"):
            self.config["cleanup_temp_after_export"] = bool(self.cleanup_temp_var.get())
        if hasattr(self, "scramble_original_audio_var"):
            self.config["scramble_original_audio"] = bool(self.scramble_original_audio_var.get())
        if hasattr(self, "gemini_grid_inspector_var"):
            self.config["gemini_grid_inspector"] = bool(self.gemini_grid_inspector_var.get())
        self.config["use_crop"] = self.config.get("use_crop", False)
        # CHỈ LƯU VÙNG CROP THỰC TẾ, KHÔNG ĐỂ ĐÈ LÊN KHUNG GỐC
        self.config["crop_w"] = self.config.get("crop_w", 1920)
        self.config["crop_h"] = self.config.get("crop_h", 1080)
        self.config["crop_x"] = self.config.get("crop_x", 0)
        self.config["crop_y"] = self.config.get("crop_y", 0)
        
        # Cấu hình Subtitle kích thước và viền (Các thuộc tính màu sắc & tọa độ đã được Studio xử lý riêng)
        current_sub_style = self.config.get("sub_style_type", "tiktok_slim")
        fallback_sub_sz = 16 if current_sub_style == "tiktok_slim" else (26 if current_sub_style == "classic" else 21)
        fallback_sub_ol = 2 if current_sub_style == "tiktok_slim" else 3
        self.config["sub_size"] = int(self.config.get("sub_size", fallback_sub_sz))
        self.config["sub_outline"] = int(self.config.get("sub_outline", fallback_sub_ol))
        
        curr_name = self.prompt_cb.get()
        curr_content = self.prompt_text.get("1.0", tk.END).strip()
        if "prompts" not in self.config:
            self.config["prompts"] = {}
        if curr_name:
            self.config["prompts"][curr_name] = curr_content

        # Đồng bộ toàn bộ thông số vào preset đang chọn (nếu có)
        sel_name = self.config.get("selected_config", "")
        if sel_name and isinstance(self.config.get("saved_configs"), dict) and sel_name in self.config["saved_configs"]:
            excluded = {"saved_configs", "selected_config", "youtube_url", "local_source_path", "hook_time", "compilation_source_path"}
            for k, v in self.config.items():
                if k not in excluded:
                    self.config["saved_configs"][sel_name][k] = deepcopy(v)
            
        save_config(self.config)

    def load_configs_to_ui(self):
        """Đọc từ `self.config` và điền lại toàn bộ giá trị lên các widget trên giao diện."""
        try:
            if "youtube_url" in self.config:
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, self.config["youtube_url"])
            if "hook_time" in self.config:
                self.hook_time_entry.delete(0, tk.END)
                self.hook_time_entry.insert(0, self.config["hook_time"])
            if "hook_duration" in self.config:
                self.hook_duration_entry.delete(0, tk.END)
                self.hook_duration_entry.insert(0, self.config["hook_duration"])
            if hasattr(self, "part_count_var"):
                self.part_count_var.set(int(self.config.get("part_count", 1) or 1))
                self._rebuild_part_cut_inputs(self.config.get("part_cut_times", []))
            if hasattr(self, "source_mode_var"):
                self.source_mode_var.set(self.config.get("source_mode", "youtube"))
            if hasattr(self, "local_path_entry") and "local_source_path" in self.config:
                self.local_path_entry.config(state=tk.NORMAL)
                self.local_path_entry.delete(0, tk.END)
                self.local_path_entry.insert(0, self.config.get("local_source_path", ""))
            if hasattr(self, "on_source_mode_change"):
                self.on_source_mode_change()
            if hasattr(self, "workflow_mode_var"):
                self.workflow_mode_var.set(self.config.get("workflow_mode", "single"))
                self.on_workflow_mode_change()
            if hasattr(self, "comp_source_type_var"):
                self.comp_source_type_var.set(self.config.get("compilation_source_type", "playlist"))
                self.on_comp_source_type_change()
            if hasattr(self, "comp_source_path_entry"):
                self.comp_source_path_entry.delete(0, tk.END)
                self.comp_source_path_entry.insert(0, self.config.get("compilation_source_path", ""))
            if hasattr(self, "comp_clip_count_spin"):
                self.comp_clip_count_spin.set(self.config.get("compilation_clip_count", 5))
            wf_mode = self.config.get("workflow_mode", "single")
            dur = (
                self.config.get("compilation_target_duration")
                if wf_mode == "compilation"
                else self.config.get("video_min_duration_sec")
            ) or self.config.get("video_min_duration_sec") or self.config.get("compilation_target_duration") or (180 if wf_mode == "compilation" else 90)
            self.video_duration_var.set(str(int(dur)))
            self.update_word_budget_label()
            if hasattr(self, "comp_title_style_cb"):
                ts = self.config.get("compilation_title_style", "clean_original")
                if ts == "ai_generated":
                    self.comp_title_style_cb.set("AI tự sáng chế giật gân (Clickbait)")
                elif ts == "only_rank":
                    self.comp_title_style_cb.set("Chỉ hiện số No. (Không hiện tiêu đề)")
                else:
                    self.comp_title_style_cb.set("Tiêu đề gốc làm sạch (Clean Title)")
            if hasattr(self, "comp_teaser_var"):
                self.comp_teaser_var.set(bool(self.config.get("compilation_enable_teaser", True)))
            if hasattr(self, "comp_teaser_duration_spin"):
                self.comp_teaser_duration_spin.set(float(self.config.get("compilation_teaser_duration", 3.0) or 3.0))
            if hasattr(self, "comp_rank_by_cb"):
                rb = self.config.get("compilation_rank_by", "views")
                if rb == "likes":
                    self.comp_rank_by_cb.set("Lượt thích (Likes)")
                elif rb == "random":
                    self.comp_rank_by_cb.set("Ngẫu nhiên (Random)")
                else:
                    self.comp_rank_by_cb.set("Lượt xem (Views)")
                
            if "ai_model" in self.config:
                self.ai_model_cb.set(self.config["ai_model"])
            if "api_key" in self.config:
                self.api_key_entry.delete(0, tk.END)
                self.api_key_entry.insert(0, self.config["api_key"])
            if "engine_tts" in self.config:
                self.engine_cb.set(self.config["engine_tts"])
                self.on_engine_change(None)
            if "country" in self.config:
                self.country_cb.set(self.config["country"])
                self.on_country_change(None)
            if "voice" in self.config:
                self.voice_cb.set(self.config["voice"])
            if hasattr(self, "market_cb"):
                market_code = str(self.config.get("target_market", "US") or "US").upper()
                self.market_cb.set(get_market_profile(market_code)["label"])
            if hasattr(self, "auto_voice_locale_var"):
                self.auto_voice_locale_var.set(bool(self.config.get("auto_voice_locale", True)))
            if hasattr(self, "market_cb"):
                self.on_market_change()
# Đồng bộ tự động qua self.video_duration_var
                
            prompts = self.config.get("prompts", {})
            if prompts:
                self.prompt_cb['values'] = list(prompts.keys())
                sel_p = self.config.get("selected_prompt", list(prompts.keys())[0])
                if sel_p in prompts:
                    self.prompt_cb.set(sel_p)
                    self.update_prompt_content_view()

            # Hậu kỳ & Màu sắc
            self.speed_spin.set(self.config.get("speed", 1.0))
            self.audio_boost_spin.set(self.config.get("audio_boost", 6.0))
            self.blur_var.set(self.config.get("blur_bg", True))
            
            # --- BỔ SUNG: KHÔI PHỤC TRẠNG THÁI CUSTOM HOOK LÊN GIAO DIỆN ---
            if hasattr(self, 'use_custom_hook_var'):
                hook_mode = normalize_hook_mode(
                    self.config.get("hook_mode"), self.config.get("use_custom_hook", False)
                )
                self.hook_mode_var.set(hook_mode)
                self.use_custom_hook_var.set(hook_mode == "ai")
                self.on_hook_mode_change()
            if hasattr(self, "use_ai_voice_var"):
                self.use_ai_voice_var.set(bool(self.config.get("use_ai_voice", True)))
            if hasattr(self, "mix_original_audio_var"):
                self.mix_original_audio_var.set(bool(
                    self.config.get("mix_original_audio_with_voice", False)
                ))
            if hasattr(self, "original_audio_mix_spin"):
                self.original_audio_mix_spin.set(
                    self.config.get("original_audio_mix_percent", 50.0)
                )
                self.on_original_audio_mix_toggle(save=False)
            self.zoom_var.set(self.config.get("zoom_in", False))
            self.zoom_spin.set(self.config.get("zoom_percent", 115))
            self.scale_w_spin.set(self.config.get("scale_w", 100))
            self.scale_h_spin.set(self.config.get("scale_h", 100))
            if hasattr(self, "broll_mode_var"):
                mode = str(self.config.get("broll_mode", "heatmap_ranked") or "").lower()
                mode = {"opencv": "heatmap_ranked", "timesub_semantic": "timesub_content"}.get(mode, mode)
                if mode not in ("timesub_content", "heatmap_ranked", "heatmap_chronological"):
                    mode = "heatmap_ranked"
                self.broll_mode_var.set(mode)
            if hasattr(self, "broll_max_spin"):
                try:
                    saved_max = float(self.config.get("broll_max_sec", 10.0))
                except (TypeError, ValueError):
                    saved_max = 10.0
                self.broll_max_spin.set(max(1.5, min(30.0, saved_max)))
            if hasattr(self, "random_broll_mirror_var"):
                self.random_broll_mirror_var.set(bool(self.config.get("random_broll_mirror", False)))
            if hasattr(self, "scramble_original_audio_var"):
                self.scramble_original_audio_var.set(bool(self.config.get("scramble_original_audio", True)))
            if hasattr(self, "gemini_grid_inspector_var"):
                self.gemini_grid_inspector_var.set(bool(self.config.get("gemini_grid_inspector", True)))
            if hasattr(self, "cleanup_temp_var"):
                self.cleanup_temp_var.set(bool(self.config.get("cleanup_temp_after_export", False)))
            if hasattr(self, "on_ai_voice_toggle"):
                self.on_ai_voice_toggle(save=False)
            if hasattr(self, "show_cmd_var"):
                self.show_cmd_var.set(bool(self.config.get("show_cmd_log", True)))
                set_console_visible(self.show_cmd_var.get())

            
            # Không còn dùng sub_style_cb cũ, các thông số size/outline/marginV/color đã được Studio quản lý và tự động load qua config
            pass
        except Exception as e:
            print(f"⚠️ Lỗi khi load config lên giao diện: {e}")

    def manual_save_all_config(self):
        self.save_configs_to_json()
        messagebox.showinfo("Thành công", "Đã lưu toàn bộ cấu hình vào file config.json!")

    def manual_load_all_config(self):
        file_path = filedialog.askopenfilename(
            title="Chọn file cấu hình JSON",
            filetypes=[("JSON Files", "*.json"), ("All Files", "*.*")],
            initialdir="."
        )
        if file_path:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    self.config = normalize_config(json.load(f))
                self.load_configs_to_ui()
                messagebox.showinfo("Thành công", f"Đã nạp thành công cấu hình từ: {file_path}")
            except Exception as e:
                messagebox.showerror("Lỗi", f"Không thể đọc file JSON: {e}")

    # ---------------- CONFIG CÓ TÊN ----------------
    def _named_config_widgets(self):
        combos = [getattr(self, "named_config_cb", None)]
        entries = [getattr(self, "named_config_name_entry", None)]
        return [w for w in combos if w is not None], [w for w in entries if w is not None]

    def save_named_config_from(self, entry):
        name = entry.get().strip() if entry is not None else ""
        if hasattr(self, "named_config_name_entry"):
            self.named_config_name_entry.delete(0, tk.END)
            self.named_config_name_entry.insert(0, name)
        self.save_named_config()

    def delete_named_config_from(self, combo):
        name = combo.get().strip() if combo is not None else ""
        if hasattr(self, "named_config_cb"):
            self.named_config_cb.set(name)
        self.delete_named_config()

    def _named_config_payload(self):
        self.save_configs_to_json()
        excluded = {
            "youtube_url", "local_source_path", "source_mode", "hook_time",
            "custom_hook_path", "ai_video_path", "compilation_source_path",
            "saved_configs", "selected_config", "queue_auto_start",
        }
        payload = {k: deepcopy(v) for k, v in self.config.items() if k not in excluded}
        payload["prompt_content"] = self.prompt_text.get("1.0", tk.END).strip()
        return payload

    def _refresh_named_config_list(self, select_name=""):
        names = list(self.config.get("saved_configs", {}).keys())
        name = select_name or self.config.get("selected_config", "")
        combos, entries = self._named_config_widgets()
        for combo in combos:
            combo["values"] = names
            combo.set(name if name in names else "")
        for entry in entries:
            entry.delete(0, tk.END)
            if name in names:
                entry.insert(0, name)

    def save_named_config(self):
        name = self.named_config_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Thiếu tên", "Hãy đặt tên config, ví dụ: Review phim Việt hoặc Vlog.")
            return
        saved = self.config.setdefault("saved_configs", {})
        if name in saved and not messagebox.askyesno("Cập nhật Config", f'Config "{name}" đã tồn tại. Ghi đè?'):
            return
        saved[name] = self._named_config_payload()
        self.config["selected_config"] = name
        save_config(self.config)
        self._refresh_named_config_list(name)
        messagebox.showinfo("Đã lưu", f'Đã lưu config "{name}" (bao gồm API key).')

    def _output_market_context(self):
        code = str(self.config.get("target_market", "US") or "US").upper()
        profile = get_market_profile(code)
        return {
            "target_market": code,
            "output_language": str(self.config.get("output_language") or profile["language"]),
            "output_locale": str(self.config.get("output_locale") or profile["locale"]),
        }

    def on_named_config_selected(self, _event=None):
        source_widget = getattr(_event, "widget", None) if _event is not None else None
        name = source_widget.get().strip() if source_widget is not None else self.named_config_cb.get().strip()
        payload = self.config.get("saved_configs", {}).get(name)
        if not isinstance(payload, dict):
            return
        # Config tạo trước khi có công tắc này đều là pipeline AI + Voice cũ.
        # Khi Load phải tự hiểu là bật, không giữ trạng thái tắt hiện tại.
        payload_to_load = deepcopy(payload)
        payload_to_load.setdefault("use_ai_voice", True)
        p_wf = payload_to_load.get("workflow_mode", "single")
        p_dur = float(
            (payload_to_load.get("compilation_target_duration") if p_wf == "compilation" else payload_to_load.get("video_min_duration_sec"))
            or payload_to_load.get("video_min_duration_sec")
            or payload_to_load.get("compilation_target_duration")
            or (180.0 if p_wf == "compilation" else 90.0)
        )
        payload_to_load["video_min_duration_sec"] = p_dur
        payload_to_load["compilation_target_duration"] = p_dur
        payload_to_load["original_audio_target_sec"] = p_dur
        if "use_ai_voice" not in payload:
            self.config.setdefault("saved_configs", {}).setdefault(name, {})["use_ai_voice"] = True
        preserved = {
            "youtube_url": self.url_entry.get().strip(),
            "source_mode": self.source_mode_var.get(),
            "local_source_path": self.local_path_entry.get().strip(),
            "hook_time": self.hook_time_entry.get().strip(),
            "compilation_source_path": self.comp_source_path_entry.get().strip() if hasattr(self, "comp_source_path_entry") else self.config.get("compilation_source_path", ""),
            "saved_configs": self.config.get("saved_configs", {}),
        }
        self.config.update(payload_to_load)
        self.config.update(preserved)
        self.config["selected_config"] = name
        self.load_configs_to_ui()
        prompt_content = payload_to_load.get("prompt_content")
        if prompt_content is not None:
            self.prompt_text.delete("1.0", tk.END)
            self.prompt_text.insert("1.0", prompt_content)
        combos, entries = self._named_config_widgets()
        for combo in combos:
            combo.set(name)
        for entry in entries:
            entry.delete(0, tk.END)
            entry.insert(0, name)
        save_config(self.config)
        # Chọn trong combobox sẽ auto-load, không bật popup làm gián đoạn.
        # Bấm nút Load thủ công vẫn hiện xác nhận như cũ.
        if _event is None:
            messagebox.showinfo("Đã Load", f'Đã áp dụng config "{name}" lên toàn bộ giao diện.')
        else:
            print(f'✅ [CONFIG] Đã tự động áp dụng config "{name}" lên giao diện.')

    def on_named_config_choice(self, _event=None):
        """Chọn preset là áp dụng ngay để tên hiển thị luôn khớp dữ liệu render."""
        name = self.named_config_cb.get().strip()
        self.named_config_name_entry.delete(0, tk.END)
        self.named_config_name_entry.insert(0, name)
        self.on_named_config_selected(_event)

    def delete_named_config(self):
        name = self.named_config_cb.get().strip()
        if not name:
            return
        if not messagebox.askyesno("Xóa Config", f'Xóa config "{name}"?'):
            return
        self.config.setdefault("saved_configs", {}).pop(name, None)
        if self.config.get("selected_config") == name:
            self.config["selected_config"] = ""
        save_config(self.config)
        self._refresh_named_config_list()

    # ---------------- HÀNG ĐỢI VIDEO ----------------
    @staticmethod
    def _valid_ai_video(path):
        return bool(path and os.path.isfile(path) and os.path.getsize(path) > 1024)

    def _build_job_from_ui(self):
        self.save_configs_to_json()
        workflow_mode = self.workflow_mode_var.get() if hasattr(self, "workflow_mode_var") else "single"
        if workflow_mode == "compilation":
            comp_source_type = self.comp_source_type_var.get() if hasattr(self, "comp_source_type_var") else "playlist"
            comp_path = self.comp_source_path_entry.get().strip() if hasattr(self, "comp_source_path_entry") else ""
            if not comp_path:
                raise ValueError("Vui lòng nhập Link Playlist YouTube hoặc chọn Thư mục Local.")
            if comp_source_type == "local_folder" and not os.path.isdir(comp_path):
                raise ValueError(f"Thư mục Local không tồn tại: {comp_path}")
            
            clip_count = int(self.comp_clip_count_spin.get() or 5)
            cb_txt = self.comp_title_style_cb.get().lower() if hasattr(self, "comp_title_style_cb") else ""
            if "không hiện" in cb_txt or "chỉ hiện" in cb_txt or "only" in cb_txt:
                title_style = "only_rank"
            elif "giật gân" in cb_txt or "ai" in cb_txt:
                title_style = "ai_generated"
            else:
                title_style = "clean_original"
            enable_teaser = bool(self.comp_teaser_var.get())
            teaser_duration = float(self.comp_teaser_duration_spin.get() or 3.0) if hasattr(self, "comp_teaser_duration_spin") else 3.0

            rank_by = "views"
            if hasattr(self, "comp_rank_by_cb"):
                rb_val = self.comp_rank_by_cb.get().lower()
                if "thích" in rb_val or "like" in rb_val:
                    rank_by = "likes"
                elif "nhiên" in rb_val or "random" in rb_val:
                    rank_by = "random"
                else:
                    rank_by = "views"
            else:
                rank_by = self.config.get("compilation_rank_by", "views")

            display_name = f"TOP {clip_count} COUNTDOWN: {os.path.basename(comp_path.rstrip('/\\')) or comp_path}"
            post_options = deepcopy(self._collect_post_options())
            post_options["compilation_rank_by"] = rank_by
            return {
                "name": display_name[:120],
                "job_type": "compilation",
                "workflow_mode": "compilation",
                "source_mode": "local" if comp_source_type == "local_folder" else "youtube",
                "url": comp_path if comp_source_type == "playlist" else "",
                "local_path": comp_path if comp_source_type == "local_folder" else "",
                "compilation_source_type": comp_source_type,
                "compilation_source_path": comp_path,
                "compilation_clip_count": clip_count,
                "compilation_target_duration": target_duration,
                "video_min_duration_sec": target_duration,
                "compilation_title_style": title_style,
                "compilation_enable_teaser": enable_teaser,
                "compilation_teaser_duration": teaser_duration,
                "compilation_rank_by": rank_by,
                "config_name": self.named_config_cb.get().strip() or "Config hiện tại",
                "config_snapshot": deepcopy(self.config),
                "model": self.ai_model_cb.get(),
                "api_key": self.api_key_entry.get().strip(),
                "prompt": self.prompt_text.get("1.0", tk.END).strip(),
                "voice": self.voice_cb.get(),
                "post_options": post_options,
                "status": "pending",
                "status_text": "Chờ xử lý Top Countdown",
            }
        source_mode = self.source_mode_var.get()
        source = self.local_path_entry.get().strip() if source_mode == "local" else self.url_entry.get().strip()
        if not source or (source_mode == "local" and not os.path.exists(source)):
            raise ValueError("Nguồn video chưa hợp lệ.")
        hook_time = self.parse_time_to_seconds(self.hook_time_entry.get().strip())
        hook_duration = float(self.hook_duration_entry.get().strip())
        part_count, part_cut_times, part_cut_seconds = self._part_settings_value()
        use_ai_voice = bool(self.use_ai_voice_var.get()) if hasattr(self, "use_ai_voice_var") else True
        hook_mode = self._hook_mode_value()
        if hook_mode == "ai" and not use_ai_voice:
            hook_mode = "native"
        ai_path = str(self.config.get("custom_hook_path") or self.config.get("ai_video_path") or "").strip()
        # Hook AI thiếu file vẫn phải chạy phần tải + AI + voice trước. Chỉ sau
        # khi chuẩn bị xong pipeline mới chuyển job sang trạng thái chờ Hook.
        status = "pending"
        status_text = "Chờ xử lý"
        display = os.path.basename(source.rstrip("/\\")) or source
        post_options = deepcopy(self._collect_post_options())
        if source_mode == "youtube":
            post_options["youtube_url"] = source
        return {
            "name": display[:120], "source_mode": source_mode,
            "url": source if source_mode == "youtube" else "",
            "local_path": source if source_mode == "local" else "",
            "hook_time": hook_time, "hook_duration": hook_duration,
            "use_ai_voice": use_ai_voice,
            "hook_mode": hook_mode, "ai_video_path": ai_path,
            "part_count": part_count, "part_cut_times": part_cut_times,
            "part_cut_seconds": part_cut_seconds,
            "config_name": self.named_config_cb.get().strip() or "Config hiện tại",
            "config_snapshot": deepcopy(self.config),
            "model": self.ai_model_cb.get(), "api_key": self.api_key_entry.get().strip(),
            "prompt": self.prompt_text.get("1.0", tk.END).strip(), "voice": self.voice_cb.get(),
            "post_options": post_options,
            "status": status, "status_text": status_text,
        }

    def add_current_job(self, start=False):
        try:
            payload = self._build_job_from_ui()
            if self.editing_job_id:
                self.job_queue.update(self.editing_job_id, **payload)
                self.editing_job_id = None
            else:
                self.job_queue.add(payload)
            self.config["queue_auto_start"] = False
            save_config(self.config)
            self.refresh_queue_table()
            if start:
                self.start_queue()
        except Exception as e:
            messagebox.showerror("Không thể Add", str(e))

    def refresh_queue_table(self):
        if not hasattr(self, "queue_tree"):
            return
        selected = self.queue_tree.selection()
        selected_id = selected[0] if selected else ""
        for item in self.queue_tree.get_children():
            self.queue_tree.delete(item)
        jobs = self.job_queue.snapshot()
        counts = {"completed": 0, "failed": 0, "waiting_ai_video": 0}
        for idx, job in enumerate(jobs, 1):
            status = job.get("status", "pending")
            if status in counts:
                counts[status] += 1
            ai_text = "Có" if job.get("hook_mode") == "ai" and self._valid_ai_video(job.get("ai_video_path")) else ("Thiếu" if job.get("hook_mode") == "ai" else "—")
            output = os.path.basename(job.get("output_path", ""))
            raw_link = str(job.get("url") or "").strip()
            if "watch?" in raw_link:
                link_text = raw_link[raw_link.find("watch?"):]
            elif "youtu.be/" in raw_link:
                link_text = raw_link[raw_link.find("youtu.be/"):]
            else:
                link_text = raw_link or "—"
            if job.get("job_type") == "compilation" or job.get("workflow_mode") == "compilation":
                source_display_val = "Top Local" if job.get("compilation_source_type") == "local_folder" else "Top Playlist"
                hook_display_val = "Countdown"
            else:
                source_display_val = "Local" if job.get("source_mode") == "local" else "YouTube"
                hook_display_val = {"ai": "AI", "gemini": "Gemini", "native": "Gốc"}.get(job.get("hook_mode"), "Gốc")

            self.queue_tree.insert("", tk.END, iid=job["id"], tags=(status,), values=(
                idx, job.get("name", ""), link_text,
                source_display_val,
                job.get("config_name", ""), hook_display_val,
                ai_text, job.get("status_text", status), output,
            ))
        if selected_id and self.queue_tree.exists(selected_id):
            self.queue_tree.selection_set(selected_id)
        self.queue_summary_var.set(
            f"Tổng {len(jobs)} | Xong {counts['completed']} | Chờ Hook {counts['waiting_ai_video']} | Lỗi {counts['failed']}"
        )

    def _selected_job_id(self):
        selected = self.queue_tree.selection()
        return selected[0] if selected else ""

    def copy_queue_link(self, event):
        """Bấm vào cột Link để copy URL YouTube đầy đủ."""
        if self.queue_tree.identify_region(event.x, event.y) != "cell":
            return
        if self.queue_tree.identify_column(event.x) != "#3":
            return
        row_id = self.queue_tree.identify_row(event.y)
        job = self.job_queue.get(row_id) if row_id else None
        url = str((job or {}).get("url") or "").strip()
        if not url:
            self.queue_summary_var.set("Video local không có link YouTube để copy")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(url)
        self.root.update_idletasks()
        self.queue_summary_var.set(f"Đã copy link: {url}")

    def delete_selected_job(self):
        job_id = self._selected_job_id()
        job = self.job_queue.get(job_id)
        if not job or job.get("status") == "running":
            return
        if messagebox.askyesno("Xóa Job", f'Xóa "{job.get("name", "video")}" khỏi hàng đợi?'):
            self.job_queue.remove(job_id)
            self.refresh_queue_table()

    def move_selected_job(self, delta):
        if self.job_queue.move(self._selected_job_id(), delta):
            self.refresh_queue_table()

    def _clean_job_temp(self, job):
        """Xóa sạch thư mục tạm của job khi retry hoặc khi gặp lỗi để chạy lại từ đầu hoàn toàn mới."""
        if not job or not isinstance(job, dict):
            return
        import shutil
        import time
        import gc
        gc.collect()

        target_dirs = set()
        temp_root = os.path.abspath(getattr(DownloaderProcessor, "TEMP_DIR", "temp"))

        # 1. Thư mục đã ghi nhận trực tiếp trong job
        if job.get("specific_dir") and isinstance(job.get("specific_dir"), str):
            target_dirs.add(os.path.abspath(job["specific_dir"]))

        # 2. Suy luận từ name, title hoặc file nguồn local (chỉ thêm nếu thư mục ĐÃ TỒN TẠI)
        candidates = [job.get("name", ""), job.get("title", "")]
        if job.get("local_path"):
            src = os.path.splitext(os.path.basename(job["local_path"]))[0]
            candidates.append(src)
        for c in candidates:
            if c:
                try:
                    import unicodedata, re
                    nfkd = unicodedata.normalize('NFKD', str(c))
                    no_accents = "".join([ch for ch in nfkd if not unicodedata.combining(ch)])
                    clean_str = re.sub(r'[^a-zA-Z0-9\s]', '', no_accents).strip().lower()
                    clean_str = re.sub(r'\s+', '_', clean_str)
                    s_name = clean_str[:20].rstrip('_') or "video_task"
                    cand_path = os.path.abspath(os.path.join(temp_root, s_name))
                    if os.path.exists(cand_path):
                        target_dirs.add(cand_path)
                except Exception:
                    pass

        # 3. Quét các thư mục con trong temp/ đối chiếu với original_title.txt
        if os.path.exists(temp_root):
            try:
                job_name_clean = (job.get("name") or "").strip().lower()
                for entry in os.scandir(temp_root):
                    if entry.is_dir():
                        title_file = os.path.join(entry.path, "original_title.txt")
                        if os.path.exists(title_file):
                            try:
                                with open(title_file, "r", encoding="utf-8") as ft:
                                    t_content = ft.read().strip().lower()
                                    if job_name_clean and (job_name_clean in t_content or t_content in job_name_clean):
                                        target_dirs.add(os.path.abspath(entry.path))
                            except Exception:
                                pass
            except Exception:
                pass

        # Thực hiện xóa an toàn: đảm bảo nằm trong temp_root và không phải chính temp_root
        for tdir in target_dirs:
            if not os.path.exists(tdir):
                continue
            try:
                if os.path.commonpath([tdir, temp_root]) == temp_root and tdir != temp_root:
                    deleted = False
                    for _ in range(3):
                        try:
                            shutil.rmtree(tdir, ignore_errors=False)
                            deleted = True
                            break
                        except Exception:
                            time.sleep(0.3)
                    if not deleted:
                        shutil.rmtree(tdir, ignore_errors=True)
                    print(f"🗑️ [AUTO-CLEAN TEMP] Đã xóa sạch thư mục tạm '{tdir}' để chạy lại từ đầu!")
            except Exception as ex:
                print(f"⚠️ [AUTO-CLEAN TEMP] Không thể xóa thư mục tạm '{tdir}': {ex}")

    def retry_selected_job(self):
        job_id = self._selected_job_id()
        job = self.job_queue.get(job_id)
        if not job or job.get("status") == "running":
            return

        current_retries = int(job.get("retry_count", 0) or 0)
        next_retries = current_retries + 1

        if current_retries >= 1:
            # Retry từ lần 2 trở đi: Xóa sạch file tạm để chạy lại từ đầu hoàn toàn mới
            print(f"🗑️ [RETRY LẦN {next_retries}] Retry lần 2 trở đi: tự động xóa file tạm của video '{job.get('name')}' để chạy lại từ đầu...")
            self._clean_job_temp(job)
            status_text = f"Chờ (Retry lần {next_retries}: đã xóa file tạm)"
        else:
            # Retry lần 1: Giữ nguyên file tạm để tận dụng dữ liệu đã tải/xử lý, không lãng phí
            print(f"🔄 [RETRY LẦN 1] Retry lần 1: Giữ nguyên file tạm cho '{job.get('name')}' để tiếp tục xử lý nhanh...")
            status_text = "Chờ (Retry lần 1: giữ file tạm)"

        if job.get("hook_mode") == "ai" and not self._valid_ai_video(job.get("ai_video_path")):
            self.job_queue.update(job_id, status="waiting_ai_video", status_text="Chờ video AI", error="", retry_count=next_retries)
        else:
            self.job_queue.update(job_id, status="pending", status_text=status_text, error="", retry_count=next_retries)
        self.refresh_queue_table()
        self.start_queue()

    def load_selected_job_for_edit(self, _event=None):
        if _event is not None and self.queue_tree.identify_column(_event.x) == "#3":
            return
        job = self.job_queue.get(self._selected_job_id())
        if not job or job.get("status") == "running":
            return
        self.editing_job_id = job["id"]
        self.config.update(deepcopy(job.get("config_snapshot", {})))
        self.config["source_mode"] = job.get("source_mode", "youtube")
        self.config["youtube_url"] = job.get("url", "")
        self.config["local_source_path"] = job.get("local_path", "")
        self.config["hook_time"] = str(job.get("hook_time", 0))
        self.config["hook_duration"] = str(job.get("hook_duration", 8))
        self.config["part_count"] = int(job.get("part_count", 1) or 1)
        self.config["part_cut_times"] = list(job.get("part_cut_times", []))
        self.config["use_custom_hook"] = job.get("hook_mode") == "ai"
        self.config["hook_mode"] = normalize_hook_mode(job.get("hook_mode"), self.config["use_custom_hook"])
        self.config["use_ai_voice"] = bool(
            job.get("use_ai_voice", job.get("post_options", {}).get("use_ai_voice", True))
        )
        self.config["custom_hook_path"] = job.get("ai_video_path", "")
        self.load_configs_to_ui()
        self.prompt_text.delete("1.0", tk.END)
        self.prompt_text.insert("1.0", job.get("prompt", ""))
        messagebox.showinfo("Sửa Job", "Đã nạp Job lên giao diện. Chỉnh xong bấm Add vào hàng đợi để cập nhật.")

    def pause_after_current(self):
        self.stop_after_current = True

    def start_queue(self):
        if self.queue_running:
            return
        self.stop_after_current = False
        self.queue_running = True
        threading.Thread(target=self._queue_worker, daemon=True).start()

    def _queue_status(self, job_id, status, text):
        self.job_queue.update(job_id, status=status, status_text=text)
        self.root.after(0, self.refresh_queue_table)

    def _queue_worker(self):
        try:
            while not self.stop_after_current:
                job = self.job_queue.next_pending()
                if not job:
                    break
                self._queue_status(job["id"], "running", "Đang chạy")
                try:
                    output = self._run_job(job)
                    self.job_queue.update(
                        job["id"], status="completed", status_text="Hoàn thành",
                        output_path=output or "", error="", retry_count=0
                    )
                except WaitingForAIHook:
                    self.job_queue.update(
                        job["id"], status="waiting_ai_video",
                        status_text="Chờ video AI", error="",
                    )
                except Exception as e:
                    print(f"\n⚠️ [LỖI PIPELINE] Job '{job.get('name')}' gặp lỗi: {e}")
                    current_retries = int(job.get("retry_count", 0) or 0)

                    # Nếu đang ở Retry lần 1 mà vẫn lỗi -> Tự động xóa file tạm và chạy Retry lần 2 từ đầu sạch sẽ
                    if current_retries == 1:
                        print(f"🗑️ [AUTO RETRY 2] Retry lần 1 vẫn lỗi! Đang tự động xóa sạch file tạm và chạy lại lần 2 từ đầu...")
                        self._clean_job_temp(job)
                        job["retry_count"] = 2
                        self.job_queue.update(job["id"], retry_count=2, status_text="Đang xóa file tạm & chạy lại lần 2...")
                        try:
                            output = self._run_job(job)
                            self.job_queue.update(
                                job["id"], status="completed", status_text="Hoàn thành",
                                output_path=output or "", error="", retry_count=0
                            )
                            self.root.after(0, self.refresh_queue_table)
                            continue
                        except WaitingForAIHook:
                            self.job_queue.update(
                                job["id"], status="waiting_ai_video",
                                status_text="Chờ video AI", error="",
                            )
                            self.root.after(0, self.refresh_queue_table)
                            continue
                        except Exception as retry_err:
                            print(f"❌ [LỖI RETRY 2] Vẫn gặp lỗi sau khi xóa file tạm ở lần 2: {retry_err}")
                            self._clean_job_temp(job)
                            e = retry_err
                    elif current_retries >= 2:
                        # Nếu lần 2 trở đi gặp lỗi thì dọn dẹp file tạm hỏng
                        self._clean_job_temp(job)

                    # Chạy lần đầu (current_retries == 0) bị lỗi: KHÔNG xóa file tạm để người dùng ấn Retry lần 1 tận dụng lại
                    self.job_queue.update(job["id"], status="failed", status_text=f"Lỗi: {str(e)[:100]}", error=str(e))
                self.root.after(0, self.refresh_queue_table)
        finally:
            self.queue_running = False
            self.root.after(0, self.refresh_queue_table)

    def _run_job(self, job):
        if job.get("job_type") == "compilation" or job.get("workflow_mode") == "compilation":
            from compilation_processor import CompilationProcessor
            return CompilationProcessor.execute_compilation_job(
                job, progress_callback=lambda jid, st, txt: self._queue_status(jid, st, txt)
            )
        if int(job.get("part_count", 1) or 1) > 1 and not job.get("_part_child"):
            return self._run_partitioned_job(job)
        return self.process_pipeline_thread(
            job.get("url", ""), float(job.get("hook_time", 0)), float(job.get("hook_duration", 8)),
            job.get("model", ""), job.get("api_key", ""), job.get("prompt", ""), job.get("voice", ""),
            job.get("source_mode", "youtube"), job.get("local_path", ""),
            job=job, managed_by_queue=True,
        )

    def _run_partitioned_job(self, job):
        """Chuẩn bị source một lần, cắt HD+360p rồi render từng Part độc lập."""
        count = int(job.get("part_count", 1) or 1)
        cut_points = [float(value) for value in job.get("part_cut_seconds", [])]
        if len(cut_points) != count - 1:
            cut_points = [self.parse_time_to_seconds(value) for value in job.get("part_cut_times", [])]
        use_ai_voice = bool(job.get("use_ai_voice", True))
        source_mode = job.get("source_mode", "youtube")
        print(f"\n🧩 [CHIA PART] Chuẩn bị nguồn một lần để xuất {count} video độc lập...")

        if source_mode == "local":
            prepared = LocalSourceProcessor.prepare(
                job.get("local_path", ""), with_transcript=use_ai_voice
            )
            master_title = prepared.get("title") or "video"
            source_hd = prepared["video_path"]
            source_low = prepared["low_res_path"]
            duration = float(prepared.get("duration") or 0.0)
            master_dir = prepared["specific_dir"]
        else:
            url = job.get("url", "")
            metadata = DownloaderProcessor.get_video_info(url)
            master_title = DownloaderProcessor.clean_source_title(
                metadata.get("title", "video"), metadata.get("uploader", "")
            )
            master_dir = DownloaderProcessor.get_safe_video_folder(master_title)
            if job and isinstance(job, dict) and "id" in job:
                job["specific_dir"] = master_dir
                try:
                    self.job_queue.update(job["id"], specific_dir=master_dir)
                except Exception:
                    pass
            source_hd = os.path.join(master_dir, "source_video.mp4")
            source_low = os.path.join(master_dir, "source_video_low.mp4")
            ydl_opts = {
                "outtmpl": os.path.join(master_dir, "source_video.%(ext)s"),
                "merge_output_format": "mp4", "noplaylist": True, "quiet": False,
                "no_warnings": True, "nocheckcertificate": True, "nopart": True,
                "retries": 10, "fragment_retries": 10,
                "concurrent_fragment_downloads": int(get_hardware_profile().get("download_threads", 8)),
            }
            info = DownloaderProcessor.download_high_res_video(url, source_hd, ydl_opts=ydl_opts, log_fn=print)
            duration = float(info.get("duration") or metadata.get("duration")
                             or DownloaderProcessor.probe_duration_sec(source_hd) or 0.0)
            DownloaderProcessor.download_low_res_video(
                url, source_low, expected_duration=duration, source_video=source_hd, log_fn=print
            )

            if use_ai_voice:
                # Caption belongs to the master source. Fetch once, then shift
                # it into every Part instead of running Whisper per child.
                AIProcessor.transcribe_audio(
                    audio_path=None, video_url=url, output_dir=master_dir,
                    youtube_caption_only=True,
                )

        master_srt = os.path.join(master_dir, "transcript_sub.srt")

        master_gemini_preview = ""
        hook_mode = str(job.get("hook_mode") or "native")
        transcript_available = (
            os.path.isfile(master_srt) and os.path.getsize(master_srt) > 20
        )
        needs_gemini = (
            "Antigravity" in str(job.get("model") or "")
            or hook_mode == "gemini"
            or (use_ai_voice and not transcript_available)
        )
        if needs_gemini:
            master_gemini_preview = DownloaderProcessor.create_gemini_preview(
                source_low,
                os.path.join(master_dir, "gemini_preview.mp4"),
                fps=2.0,
            )

        # Scan scene boundaries once on the complete 360p source. Each child
        # receives a shifted cache derived from this map instead of rescanning
        # every frame of every Part.
        master_scene_map = None
        try:
            post_snapshot = (job or {}).get("post_options", {})
            broll_max_sec = float(post_snapshot.get("broll_max_sec", (job or {}).get("broll_max_sec", self._broll_max_sec_value())))
            from scene_mapper import build_scene_map
            master_scene_map = build_scene_map(source_low, target_limit=broll_max_sec)
        except Exception as exc:
            print(f"⚠️ [SCENE MAP MASTER] Không tạo trước được, từng Part sẽ tự xử lý: {exc}")

        parts_root = os.path.join(master_dir, "parts")
        parts = PartProcessor.split_sources(
            source_hd, source_low, duration, cut_points, parts_root,
            source_title=master_title,
            source_gemini=master_gemini_preview,
        )
        outputs = []
        for part in parts:
            index = part["index"]
            print(
                f"\n{'=' * 60}\n🧩 [PART {index}/{count}] "
                f"{part['start']:.1f}s → {part['end']:.1f}s\n{'=' * 60}"
            )
            # LocalSourceProcessor sẽ dùng thư mục theo filename. Chép proxy
            # đã cắt sẵn vào đúng chỗ để nó không transcode lại từ HD.
            part_title = os.path.splitext(os.path.basename(part["video_path"]))[0]
            identity = (
                f"{os.path.abspath(master_dir)}|{index}|"
                f"{part['start']:.3f}|{part['end']:.3f}"
            )
            digest = hashlib.sha1(identity.encode("utf-8", errors="ignore")).hexdigest()[:10]
            work_key = f"part_{index:02d}_{digest}"
            child_dir = DownloaderProcessor.get_safe_video_folder(work_key)
            os.makedirs(child_dir, exist_ok=True)
            shutil.copy2(part["low_res_path"], os.path.join(child_dir, "source_video_low.mp4"))
            if part.get("gemini_preview_path"):
                shutil.copy2(
                    part["gemini_preview_path"],
                    os.path.join(child_dir, "gemini_preview.mp4"),
                )
            if use_ai_voice:
                PartProcessor.split_srt(
                    master_srt,
                    os.path.join(child_dir, "transcript_sub.srt"),
                    float(part["start"]), float(part["end"]),
                )
            if master_scene_map:
                try:
                    from scene_mapper import seed_part_scene_map
                    seed_part_scene_map(
                        master_scene_map,
                        os.path.join(child_dir, "source_video_low.mp4"),
                        float(part["start"]), float(part["end"]),
                    )
                except Exception as exc:
                    print(f"⚠️ [SCENE MAP PART {index}] Không seed được cache: {exc}")
            print(f"🗂️ [PART {index}] Cache riêng: {child_dir}")
            child = deepcopy(job)
            child.update({
                "_part_child": True, "part_count": 1, "part_index": index,
                "_part_work_key": work_key,
                "_skip_source_whisper": True,
                "source_mode": "local", "local_path": part["video_path"],
                "url": "", "name": f"{master_title} - Part {index}",
            })
            child["post_options"] = deepcopy(job.get("post_options", {}))
            child["post_options"]["cleanup_temp_after_export"] = False
            child["post_options"]["output_suffix"] = f"Part {index}"
            # Một video Hook AI không thể đại diện cho nhiều Part. Hai kiểu
            # hook source/native và Gemini vẫn chạy độc lập trên từng Part.
            if child.get("hook_mode") == "ai":
                child["hook_mode"] = "native"
            output = self._run_local_source_pipeline(
                child["local_path"], float(child.get("hook_time", 0)),
                float(child.get("hook_duration", 0)), child.get("model", ""),
                child.get("api_key", ""), child.get("prompt", ""), child.get("voice", ""),
                job=child, managed_by_queue=True,
            )
            if output:
                outputs.append(output)
        if len(outputs) != count:
            raise RuntimeError(f"Chỉ xuất thành công {len(outputs)}/{count} Part.")
        manifest_path = os.path.join(master_dir, "part_outputs.json")
        with open(manifest_path, "w", encoding="utf-8") as stream:
            json.dump({"title": master_title, "outputs": outputs}, stream, ensure_ascii=False, indent=2)
        print(f"\n✅ [CHIA PART] Đã xuất đủ {count}/{count} video riêng.")
        return outputs[0] if len(outputs) == 1 else os.path.dirname(outputs[0])

    def on_source_mode_change(self):
        """Chỉ 1 mode active: YouTube bật URL / tắt file; Local tắt URL / bật browse. Hook time luôn dùng được."""
        mode = self.source_mode_var.get() if hasattr(self, "source_mode_var") else "youtube"
        is_local = mode == "local"
        if hasattr(self, "url_entry"):
            self.url_entry.config(state=tk.DISABLED if is_local else tk.NORMAL)
        if hasattr(self, "local_path_entry"):
            self.local_path_entry.config(state=tk.NORMAL if is_local else tk.DISABLED)
        for btn_name in ("btn_browse_local_file", "btn_browse_local_folder"):
            btn = getattr(self, btn_name, None)
            if btn is not None:
                btn.config(state=tk.NORMAL if is_local else tk.DISABLED)

    def on_workflow_mode_change(self):
        mode = self.workflow_mode_var.get() if hasattr(self, "workflow_mode_var") else "single"
        if mode == "compilation":
            if hasattr(self, "single_mode_frame"):
                self.single_mode_frame.grid_remove()
            if hasattr(self, "compilation_mode_frame"):
                self.compilation_mode_frame.grid()
            try:
                cur_dur = float(self.video_duration_var.get() or 90.0)
                if cur_dur <= 90.0:
                    self.video_duration_var.set(str(int(self.config.get("compilation_target_duration", 180))))
            except Exception:
                pass
        else:
            if hasattr(self, "compilation_mode_frame"):
                self.compilation_mode_frame.grid_remove()
            if hasattr(self, "single_mode_frame"):
                self.single_mode_frame.grid()
            try:
                cur_dur = float(self.video_duration_var.get() or 180.0)
                if cur_dur > 120.0:
                    self.video_duration_var.set(str(int(self.config.get("video_min_duration_sec", 90))))
            except Exception:
                pass
        self.update_word_budget_label()

    def on_comp_source_type_change(self):
        src_type = self.comp_source_type_var.get() if hasattr(self, "comp_source_type_var") else "playlist"
        if hasattr(self, "lbl_comp_path"):
            self.lbl_comp_path.config(text="Thư mục:" if src_type == "local_folder" else "Playlist URL:")
        if hasattr(self, "btn_browse_comp_folder"):
            self.btn_browse_comp_folder.config(state=tk.NORMAL if src_type == "local_folder" else tk.DISABLED)

    def browse_comp_source_folder(self):
        path = filedialog.askdirectory(title="Chọn thư mục chứa các video ngắn làm Top")
        if path:
            self.comp_source_path_entry.delete(0, tk.END)
            self.comp_source_path_entry.insert(0, path)

    def open_compilation_preview_popup(self):
        from compilation_processor import CompilationProcessor
        source_type = self.comp_source_type_var.get() if hasattr(self, "comp_source_type_var") else "playlist"
        source_path = self.comp_source_path_entry.get().strip() if hasattr(self, "comp_source_path_entry") else ""
        if not source_path:
            messagebox.showwarning("Thiếu nguồn", "Vui lòng dán Link YouTube Playlist hoặc chọn Thư mục Local trước!")
            return

        try:
            count = int(self.comp_clip_count_spin.get() or 5)
        except ValueError:
            count = 5

        popup = tk.Toplevel(self.root)
        popup.title("Xem Trước Danh Sách Tuyển Tập Top (Countdown Preview)")
        popup.geometry("880x520")
        popup.transient(self.root)
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

        lbl_status = ttk.Label(popup, text="⏳ Đang quét danh sách video (siêu tốc)...", font=("Segoe UI", 10, "italic"))
        lbl_status.pack(pady=10)

        tree_frame = ttk.Frame(popup, padding=10)
        tree_frame.pack(fill=tk.BOTH, expand=True)

        cols = ("rank", "badge", "views", "likes", "title", "duration")
        tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=10)
        tree.heading("rank", text="#")
        tree.heading("badge", text="Thứ Hạng")
        tree.heading("views", text="Lượt Xem (View)")
        tree.heading("likes", text="Lượt Thích (Like)")
        tree.heading("title", text="Tiêu Đề Clip")
        tree.heading("duration", text="Thời Lượng")

        tree.column("rank", width=35, anchor=tk.CENTER)
        tree.column("badge", width=75, anchor=tk.CENTER)
        tree.column("views", width=105, anchor=tk.E)
        tree.column("likes", width=105, anchor=tk.E)
        tree.column("title", width=390, anchor=tk.W)
        tree.column("duration", width=85, anchor=tk.CENTER)

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
                if not raw_entries_holder:
                    if source_type == "local_folder" or os.path.isdir(source_path):
                        raw = CompilationProcessor.scan_local_folder(source_path)
                    else:
                        raw = CompilationProcessor.scan_youtube_playlist(source_path)
                    raw_entries_holder.extend(raw)

                if not raw_entries_holder:
                    popup.after(0, lambda: lbl_status.config(text="❌ Không tìm thấy video nào trong nguồn đã chọn!"))
                    return

                ranked = CompilationProcessor.pick_and_rank_clips(raw_entries_holder, count=count, rank_by=active_mode)
                
                def _update_tree():
                    for idx, it in enumerate(ranked, 1):
                        tree.insert("", tk.END, values=(
                            idx,
                            f"No. {it['rank']}",
                            _format_views(it.get("view_count")),
                            _format_likes(it.get("like_count")),
                            it.get("title", ""),
                            _format_dur(it.get("duration")),
                        ))
                    if active_mode == "likes":
                        rule_text = "Đã xếp: No.1 là clip nhiều LIKE nhất (Countdown climax)"
                    elif active_mode == "random":
                        rule_text = "Đã xếp: RANDOM ngẫu nhiên thứ tự No. N ➔ No. 1"
                    else:
                        rule_text = "Đã xếp: No.1 là clip nhiều VIEW nhất (Countdown climax)"
                    lbl_status.config(text=f"✅ Đã chọn {len(ranked)}/{len(raw_entries_holder)} clips. {rule_text}")

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

    def update_word_budget_label(self):
        """Hiển thị ngay word budget và đồng bộ thời lượng cho cả Video đơn lẻ và Tuyển tập Top."""
        workflow_mode = getattr(self, "workflow_mode_var", None)
        mode = workflow_mode.get() if workflow_mode else "single"
        if mode == "compilation":
            try:
                total_dur = float(self.video_duration_var.get() or 180.0)
                clip_count = int(self.comp_clip_count_spin.get() or 5) if hasattr(self, "comp_clip_count_spin") else 5
                per_clip = total_dur / max(1, clip_count)
                words = round(per_clip * 2.3)
                text = f"giây (Tuyển tập {clip_count} clip: Mỗi clip ~{per_clip:.0f}s voice · ~{words} từ) ⓘ"
                if hasattr(self, "comp_hint_var"):
                    self.comp_hint_var.set(f"(Mỗi clip ~{per_clip:.0f}s voice · ~{words} từ)")
            except Exception:
                text = "giây (Tuyển tập Top Countdown)"
        else:
            try:
                duration = max(30.0, min(600.0, float(self.video_duration_var.get())))
                try:
                    playback_speed = float(self.speed_spin.get())
                except (AttributeError, TypeError, ValueError):
                    playback_speed = 1.0
                minimum, maximum, preferred = AIProcessor.narration_word_window(
                    duration, playback_speed
                )
                text = f"giây  •  {minimum}–{maximum} từ  •  tốt nhất ~{preferred} từ  ⓘ"
            except Exception:
                text = "giây  •  nhập 30–600 để tính số từ"
        if hasattr(self, "word_budget_var"):
            self.word_budget_var.set(text)

    def open_google_login(self):
        """Mở CLI chính thức; quyền chỉ tồn tại trong tiến trình đăng nhập."""
        self.google_status_var.set("● Đang chuẩn bị Antigravity CLI...")
        self.google_status_label.config(foreground=self.colors["warning"])
        if hasattr(self, "btn_google_login"):
            self.btn_google_login.config(state="disabled")

        def prepare_and_login():
            try:
                from antigravity_processor import AntigravityProcessor
                executable = AntigravityProcessor.ensure_installed()
                self.root.after(0, lambda: self.google_status_var.set(
                    "● Đang chờ đăng nhập Google trong cửa sổ CLI..."
                ))
                process = subprocess.Popen(
                    [executable, "--prompt-interactive",
                     "Sign in to Google if requested, then reply READY."],
                    cwd=os.path.abspath("."), creationflags=0x00000010,
                )
                process.wait()
                self.root.after(0, lambda: self.check_google_login(manual=True))
            except Exception as exc:
                error_text = str(exc)
                def show_error(error_text=error_text):
                    self.google_status_var.set("● Không thể chuẩn bị Gemini Local")
                    self.google_status_label.config(foreground=self.colors["danger"])
                    messagebox.showerror("Google Gemini Local", f"Không mở được đăng nhập:\n{error_text}")
                self.root.after(0, show_error)
            finally:
                if hasattr(self, "btn_google_login"):
                    self.root.after(0, lambda: self.btn_google_login.config(state="normal"))

        threading.Thread(target=prepare_and_login, daemon=True).start()

    def check_google_login(self, manual=False):
        """Kiểm tra phiên bằng lệnh read-only `agy models`."""
        if not hasattr(self, "google_status_var"):
            return
        self.google_status_var.set("● Đang kiểm tra phiên Google...")
        self.google_status_label.config(foreground=self.colors["warning"])
        if hasattr(self, "btn_google_check"):
            self.btn_google_check.config(state="disabled")

        def worker():
            ok, detail = False, ""
            try:
                from antigravity_processor import AntigravityProcessor
                executable = AntigravityProcessor.ensure_installed()
                result = subprocess.run(
                    [executable, "models"], cwd=os.path.abspath("."),
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=180, creationflags=0x08000000,
                )
                output = ((result.stdout or "") + "\n" + (result.stderr or "")).strip()
                ok = result.returncode == 0 and bool(output)
                detail = output[-500:]
            except Exception as exc:
                detail = str(exc)

            def finish():
                if hasattr(self, "btn_google_check"):
                    self.btn_google_check.config(state="normal")
                if ok:
                    self.google_status_var.set("● Đã đăng nhập Google • Gemini Local sẵn sàng")
                    self.google_status_label.config(foreground=self.colors["success"])
                else:
                    self.google_status_var.set("● Chưa đăng nhập hoặc phiên đã hết hạn")
                    self.google_status_label.config(foreground=self.colors["danger"])
                    if manual:
                        messagebox.showwarning(
                            "Google Gemini Local",
                            "Không xác nhận được phiên Google. Hãy bấm 'Đăng nhập / đổi tài khoản'.\n\n"
                            + (detail or "Antigravity không trả trạng thái."),
                        )

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()

    def on_ai_voice_toggle(self, save=True):
        """Áp dụng ma trận mode: AI=Timesub/Review, tiếng gốc=Review/Highlight."""
        enabled = bool(self.use_ai_voice_var.get()) if hasattr(self, "use_ai_voice_var") else True
        for widget in getattr(self, "ai_only_grid_widgets", []):
            try:
                if enabled:
                    widget.grid()
                else:
                    widget.grid_remove()
            except Exception:
                pass

        normal_widgets = ("engine_cb", "country_cb", "voice_cb", "btn_preview")
        for name in normal_widgets:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                if enabled:
                    state = "normal"
                    if isinstance(widget, ttk.Combobox):
                        state = "readonly"
                    widget.config(state=state)
                else:
                    widget.config(state="disabled")
            except Exception:
                pass

        # Gemini là bộ phân tích/chọn cảnh ở cả hai chế độ. Tắt Voice chỉ
        # bỏ khâu viết lời đọc + TTS, không được khóa tài khoản hay yêu cầu AI.
        always_enabled_widgets = (
            "btn_google_login", "btn_google_check", "prompt_cb",
            "prompt_name_entry", "btn_save_prompt", "btn_step2", "prompt_text",
        )
        for name in always_enabled_widgets:
            widget = getattr(self, name, None)
            if widget is None:
                continue
            try:
                widget.config(state="readonly" if isinstance(widget, ttk.Combobox) else "normal")
            except Exception:
                pass
        if hasattr(self, "btn_step2"):
            self.btn_step2.config(
                text="▶ AI + Voice" if enabled else "▶ Gemini chọn Highlight"
            )

        if not enabled:
            if hasattr(self, "broll_mode_help_var"):
                self.broll_mode_help_var.set(
                    "Không Voice AI: Review cao trào = Top 1→N; Highlight = cùng Top cảnh nhưng theo thời gian nguồn, giữ tiếng gốc."
                )
            if hasattr(self, "broll_mode_var"):
                if self.broll_mode_var.get() == "timesub_content":
                    self.broll_mode_var.set("heatmap_chronological")
            if hasattr(self, "rb_broll_timesub") and self.rb_broll_timesub.winfo_manager():
                self.rb_broll_timesub.pack_forget()
            if (hasattr(self, "rb_broll_chronological")
                    and not self.rb_broll_chronological.winfo_manager()):
                self.rb_broll_chronological.pack(side=tk.LEFT)
            if hasattr(self, "use_custom_hook_var"):
                self.use_custom_hook_var.set(False)
            if hasattr(self, "chk_custom_hook"):
                self.chk_custom_hook.config(state="disabled")
            if hasattr(self, "btn_open_custom_hook_studio"):
                self.btn_open_custom_hook_studio.config(state="disabled")
        else:
            if hasattr(self, "broll_mode_help_var"):
                self.broll_mode_help_var.set(
                    "Có Voice AI: Timesub kể đúng diễn biến; Review cao trào đưa sự kiện mạnh lên đầu và ghép hình theo cụm voice."
                )
            if hasattr(self, "broll_mode_var"):
                if self.broll_mode_var.get() == "heatmap_chronological":
                    self.broll_mode_var.set("timesub_content")
            if hasattr(self, "rb_broll_timesub") and not self.rb_broll_timesub.winfo_manager():
                self.rb_broll_timesub.pack(
                    side=tk.LEFT, padx=(0, 12), before=self.rb_broll_ranked
                )
            if (hasattr(self, "rb_broll_chronological")
                    and self.rb_broll_chronological.winfo_manager()):
                self.rb_broll_chronological.pack_forget()
            if hasattr(self, "chk_custom_hook"):
                self.chk_custom_hook.config(state="normal")
            if hasattr(self, "btn_open_custom_hook_studio"):
                self.btn_open_custom_hook_studio.config(state="normal")

        self.config["use_ai_voice"] = enabled
        self.config["broll_mode"] = self._broll_mode_value()
        self.on_original_audio_mix_toggle(save=False)
        if save:
            save_config(self.config)

    def on_original_audio_mix_toggle(self, save=True):
        """Chỉ mở mức tiếng nền khi vừa bật Voice AI vừa chọn kèm tiếng gốc."""
        voice_enabled = bool(self.use_ai_voice_var.get()) if hasattr(self, "use_ai_voice_var") else True
        mix_enabled = bool(self.mix_original_audio_var.get()) if hasattr(self, "mix_original_audio_var") else False
        if hasattr(self, "chk_mix_original_audio"):
            self.chk_mix_original_audio.config(state="normal" if voice_enabled else "disabled")
        if hasattr(self, "original_audio_mix_spin"):
            self.original_audio_mix_spin.config(
                state="normal" if voice_enabled and mix_enabled else "disabled"
            )
        self.config["mix_original_audio_with_voice"] = bool(voice_enabled and mix_enabled)
        if save:
            save_config(self.config)

    def on_hook_mode_change(self):
        """Đồng bộ ba lựa chọn Hook và chỉ mở các ô thật sự cần dùng."""
        mode = normalize_hook_mode(
            self.hook_mode_var.get() if hasattr(self, "hook_mode_var") else self.config.get("hook_mode"),
            self.config.get("use_custom_hook", False),
        )
        if hasattr(self, "hook_mode_var"):
            self.hook_mode_var.set(mode)
        if hasattr(self, "use_custom_hook_var"):
            self.use_custom_hook_var.set(mode == "ai")
        if hasattr(self, "hook_time_entry"):
            self.hook_time_entry.configure(state=tk.NORMAL if mode == "native" else tk.DISABLED)
        if hasattr(self, "hook_duration_entry"):
            self.hook_duration_entry.configure(state=tk.NORMAL if mode != "ai" else tk.DISABLED)
        if hasattr(self, "btn_open_custom_hook_studio"):
            self.btn_open_custom_hook_studio.configure(state=tk.NORMAL if mode == "ai" else tk.DISABLED)

    def _hook_mode_value(self):
        return normalize_hook_mode(
            self.hook_mode_var.get() if hasattr(self, "hook_mode_var") else self.config.get("hook_mode"),
            self.use_custom_hook_var.get() if hasattr(self, "use_custom_hook_var") else False,
        )

    def on_part_count_change(self, _event=None):
        previous = [entry.get().strip() for entry in getattr(self, "part_split_entries", [])]
        self._rebuild_part_cut_inputs(previous)

    def _rebuild_part_cut_inputs(self, values=None):
        if not hasattr(self, "part_cuts_frame"):
            return
        for child in self.part_cuts_frame.winfo_children():
            child.destroy()
        self.part_split_entries = []
        try:
            count = max(0, min(20, int(self.part_count_var.get())))
        except (TypeError, ValueError, tk.TclError):
            count = 1
        values = list(values or [])
        for index in range(max(0, count - 1)):
            row = ttk.Frame(self.part_cuts_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"Part {index + 1}: 00:00:00 →" if index == 0
                      else f"Part {index + 1}: mốc {index} →", width=23).pack(side=tk.LEFT)
            entry = ttk.Entry(row, width=12)
            entry.pack(side=tk.LEFT, padx=(4, 7))
            if index < len(values):
                entry.insert(0, str(values[index]))
            ttk.Label(row, text=f"mốc {index + 1}").pack(side=tk.LEFT)
            self.part_split_entries.append(entry)
        if count > 1:
            row = ttk.Frame(self.part_cuts_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"Part {count}: mốc {count - 1} → hết video").pack(side=tk.LEFT)

    def _part_settings_value(self):
        try:
            count = max(0, min(20, int(self.part_count_var.get())))
        except (TypeError, ValueError, tk.TclError):
            count = 1
        raw = [entry.get().strip() for entry in getattr(self, "part_split_entries", [])]
        if count <= 1:
            return count, [], []
        if len(raw) != count - 1 or any(not value for value in raw):
            raise ValueError(f"Chia {count} Part cần nhập đủ {count - 1} mốc thời gian.")
        seconds = [self.parse_time_to_seconds(value) for value in raw]
        if any(value <= 0 for value in seconds) or seconds != sorted(seconds) or len(seconds) != len(set(seconds)):
            raise ValueError("Các mốc Part phải lớn hơn 00:00:00, tăng dần và không trùng nhau.")
        return count, raw, seconds

    def on_cmd_log_toggle(self):
        visible = bool(self.show_cmd_var.get())
        self.config["show_cmd_log"] = visible
        save_config(self.config)
        set_console_visible(visible)

    def recheck_hardware(self):
        def worker():
            try:
                profile = detect_hardware(force=True, log=print)
                gpu_lines = []
                for gpu in profile.get("gpus", []):
                    vram = f" | VRAM {gpu.get('vram_gb')} GB" if gpu.get("vram_gb") else ""
                    driver = f" | Driver {gpu.get('driver')}" if gpu.get("driver") else ""
                    gpu_lines.append(f"• {gpu.get('name', 'GPU')}{vram}{driver}")
                gpu_text = "\n".join(gpu_lines) if gpu_lines else "Không đọc được tên GPU"
                message = (
                    f"Mức máy: {profile.get('strength')}\n"
                    f"CPU: {profile.get('cpu_cores')} luồng\n"
                    f"RAM: {profile.get('ram_gb')} GB\n"
                    f"GPU:\n{gpu_text}\n"
                    f"Encoder: {profile.get('encoder')}"
                )
                self.root.after(0, lambda: messagebox.showinfo("Cấu hình máy", message))
            except Exception as exc:
                error_text = str(exc)
                self.root.after(0, lambda text=error_text: messagebox.showerror("Lỗi kiểm tra máy", text))
        threading.Thread(target=worker, daemon=True).start()

    def update_cookie_badge(self):
        """Cập nhật nhãn trạng thái Cookie trên thanh tiêu đề và Tab 1."""
        path = DownloaderProcessor.get_cookie_file_path()
        use_cookies = bool(self.config.get("use_youtube_cookies", True))
        if path and os.path.isfile(path) and use_cookies:
            txt_tab = "🍪 Cookie: Đã nạp ✓"
            txt_head = "🍪 Cookie ✓"
        elif path and not use_cookies:
            txt_tab = "🍪 Cookie: Tạm tắt"
            txt_head = "🍪 Cookie (Tắt)"
        else:
            txt_tab = "🍪 Cookie: Chưa nạp"
            txt_head = "🍪 Cookie"

        if hasattr(self, "btn_cookie_badge"):
            self.btn_cookie_badge.configure(text=txt_tab)
        if hasattr(self, "btn_cookie"):
            self.btn_cookie.configure(text=txt_head)

    def open_youtube_cookie_dialog(self):
        """Mở hộp thoại quản lý Cookie YouTube để vượt giới hạn độ tuổi và đăng nhập."""
        popup = tk.Toplevel(self.root)
        popup.title("Cấu hình Cookie YouTube — Vượt Giới Hạn Độ Tuổi & Đăng Nhập")
        popup.geometry("680x520")
        popup.transient(self.root)
        popup.grab_set()

        content = ttk.Frame(popup, padding=16)
        content.pack(fill=tk.BOTH, expand=True)

        # Header info
        header_frame = ttk.Frame(content)
        header_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(
            header_frame,
            text="🍪 QUẢN LÝ COOKIE YOUTUBE (BYPASS AGE RESTRICTION)",
            font=("Segoe UI Semibold", 11),
            foreground=self.colors.get("navy", "#14213D"),
        ).pack(anchor=tk.W)
        ttk.Label(
            header_frame,
            text="Tự động vượt rào cản độ tuổi (18+), đăng nhập và bảo vệ bản quyền khi tải video đơn hoặc playlist.",
            font=("Segoe UI", 9),
            foreground=self.colors.get("muted", "#64748B"),
        ).pack(anchor=tk.W, pady=(2, 0))

        # Khung trạng thái hiện tại
        status_box = ttk.LabelFrame(content, text=" Trạng Thái Cookie Hiện Tại ", padding=10)
        status_box.pack(fill=tk.X, pady=(0, 10))

        lbl_status_badge = ttk.Label(status_box, text="Đang kiểm tra...", font=("Segoe UI Semibold", 9))
        lbl_status_badge.pack(anchor=tk.W, pady=(0, 4))

        lbl_detected_path = ttk.Label(status_box, text="", font=("Segoe UI", 8), foreground="#475569", wraplength=620)
        lbl_detected_path.pack(anchor=tk.W)

        # Khung cấu hình file
        config_box = ttk.LabelFrame(content, text=" Thiết Lập File cookies.txt ", padding=10)
        config_box.pack(fill=tk.X, pady=(0, 10))

        use_cookie_var = tk.BooleanVar(value=bool(self.config.get("use_youtube_cookies", True)))
        chk_use = ttk.Checkbutton(
            config_box,
            text="Bật sử dụng Cookie cho yt-dlp khi tải video và quét Playlist",
            variable=use_cookie_var,
        )
        chk_use.pack(anchor=tk.W, pady=(0, 8))

        file_row = ttk.Frame(config_box)
        file_row.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(file_row, text="Đường dẫn:").pack(side=tk.LEFT, padx=(0, 6))
        cookie_path_var = tk.StringVar(value=self.config.get("youtube_cookie_file", ""))
        cookie_entry = ttk.Entry(file_row, textvariable=cookie_path_var, width=45)
        cookie_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        def refresh_status_view():
            cur_path = cookie_path_var.get().strip()
            if not cur_path:
                detected = DownloaderProcessor.get_cookie_file_path()
            else:
                detected = cur_path if os.path.isfile(cur_path) else ""

            is_active = bool(use_cookie_var.get())
            if detected and os.path.isfile(detected):
                if is_active:
                    lbl_status_badge.configure(
                        text="🟢 Đã nạp file Cookie thành công (Sẵn sàng tải video 18+)",
                        foreground="#16825D"
                    )
                else:
                    lbl_status_badge.configure(
                        text="🟡 Đã tìm thấy Cookie nhưng đang TẠM TẮT",
                        foreground="#B56A09"
                    )
                lbl_detected_path.configure(text=f"Tệp: {detected}")
            else:
                lbl_status_badge.configure(
                    text="⚪ Chưa phát hiện file Cookie (Video 18+ sẽ bị YouTube chặn)",
                    foreground="#64748B"
                )
                lbl_detected_path.configure(text="Gợi ý: Chọn file cookies.txt hoặc đặt file 'cookies.txt' vào thư mục app.")

        chk_use.configure(command=refresh_status_view)

        def browse_cookie_file():
            selected = filedialog.askopenfilename(
                title="Chọn file cookies.txt (Netscape format)",
                filetypes=[("Text files (*.txt)", "*.txt"), ("All files (*.*)", "*.*")],
                parent=popup
            )
            if selected:
                cookie_path_var.set(selected)
                refresh_status_view()

        def clear_cookie_file():
            cookie_path_var.set("")
            refresh_status_view()

        ttk.Button(file_row, text="📁 Chọn File", command=browse_cookie_file, style="Tool.TButton").pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(file_row, text="🗑️ Xóa", command=clear_cookie_file, style="Tool.TButton").pack(side=tk.LEFT)

        # Nút kiểm tra cookie
        test_frame = ttk.Frame(config_box)
        test_frame.pack(fill=tk.X, pady=(4, 0))

        lbl_test_result = ttk.Label(test_frame, text="", font=("Segoe UI", 9), wraplength=480)

        def run_test_cookie():
            lbl_test_result.configure(text="⏳ Đang kiểm tra kết nối với YouTube (thử video giới hạn độ tuổi)...", foreground="#D97706")
            popup.update_idletasks()
            path_to_test = cookie_path_var.get().strip()

            def _thread_test():
                ok, msg = DownloaderProcessor.test_cookie_file(path_to_test if path_to_test else None)
                def _update_ui():
                    if ok:
                        lbl_test_result.configure(text=f"✅ {msg}", foreground="#16825D")
                    else:
                        lbl_test_result.configure(text=f"❌ {msg}", foreground="#C93C37")
                    refresh_status_view()
                self.root.after(0, _update_ui)

            threading.Thread(target=_thread_test, daemon=True).start()

        ttk.Button(test_frame, text="🔍 Kiểm Tra Cookie Ngay", command=run_test_cookie, style="Tool.TButton").pack(side=tk.LEFT, padx=(0, 8))
        lbl_test_result.pack(side=tk.LEFT, fill=tk.X, expand=True)

        refresh_status_view()

        # Khung hướng dẫn
        guide_box = ttk.LabelFrame(content, text=" 📖 Hướng Dẫn Xuất Cookie YouTube Chuẩn (3 Bước) ", padding=10)
        guide_box.pack(fill=tk.BOTH, expand=True, pady=(0, 10))

        guide_text = (
            "1. Cài tiện ích 'Get cookies.txt LOCALLY' trên Chrome / Edge / Brave / Cốc Cốc.\n"
            "2. Đăng nhập tài khoản YouTube của bạn trên trình duyệt (tài khoản đã trên 18 tuổi).\n"
            "3. Bấm vào icon tiện ích ➔ Nhấn 'Export' để tải file cookies.txt về máy.\n"
            "4. Bấm [📁 Chọn File] ở trên và chọn file vừa tải (hoặc copy file đặt vào thư mục app)."
        )
        lbl_guide = ttk.Label(guide_box, text=guide_text, font=("Segoe UI", 9), justify=tk.LEFT)
        lbl_guide.pack(anchor=tk.W)

        # Nút Lưu & Đóng
        btn_row = ttk.Frame(content)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM)

        def save_and_close():
            self.config["youtube_cookie_file"] = cookie_path_var.get().strip()
            self.config["use_youtube_cookies"] = bool(use_cookie_var.get())
            save_config(self.config)
            self.update_cookie_badge()
            popup.destroy()

        ttk.Button(btn_row, text="💾 Lưu & Áp Dụng", command=save_and_close, style="Primary.TButton").pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Đóng", command=popup.destroy).pack(side=tk.RIGHT)

    def check_updates(self, manual=True):
        """Kiểm tra GitHub ở nền; lỗi mạng khi tự kiểm tra sẽ không làm phiền."""
        def worker():
            try:
                manifest = check_for_update()
                if manifest.get("available"):
                    self.root.after(0, lambda data=manifest: self._offer_update(data))
                elif manual:
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Cập nhật", f"Bạn đang dùng phiên bản mới nhất: v{current_version()}"
                    ))
            except Exception as exc:
                if manual:
                    error_text = str(exc)
                    self.root.after(0, lambda text=error_text: messagebox.showerror(
                        "Không kiểm tra được cập nhật", text
                    ))
        threading.Thread(target=worker, daemon=True).start()

    def _offer_update(self, manifest):
        if self.queue_running:
            messagebox.showwarning(
                "Đang xử lý video",
                "Hãy đợi hàng đợi dừng rồi mới cập nhật để không làm hỏng video đang render.",
            )
            return
        notes = str(manifest.get("release_notes") or "Không có ghi chú.").strip()
        if len(notes) > 900:
            notes = notes[:900] + "…"
        version = manifest.get("version", "")
        size_mb = float(manifest.get("size") or 0) / (1024 * 1024)
        question = (
            f"Có phiên bản mới v{version} (đang dùng v{current_version()}).\n"
            f"Dung lượng: {size_mb:.1f} MB\n\n{notes}\n\n"
            "Tải và cập nhật ngay? Tool sẽ tự đóng rồi mở lại."
        )
        if not messagebox.askyesno("Có bản cập nhật", question):
            return

        def download_worker():
            try:
                last_percent = [-1]
                def progress(received, total):
                    percent = int(received * 100 / total) if total else 0
                    if percent // 10 != last_percent[0] // 10:
                        last_percent[0] = percent
                        print(f"⬇️ [UPDATE] Đã tải {percent}%")
                package = download_update(manifest, progress=progress)
                print("✅ [UPDATE] Tải xong và SHA-256 hợp lệ. Đang mở updater...")
                launch_updater(package, str(version), bool(manifest.get("runtime_update", False)))
                self.root.after(0, self.root.destroy)
            except Exception as exc:
                error_text = str(exc)
                self.root.after(0, lambda text=error_text: messagebox.showerror(
                    "Cập nhật thất bại", text
                ))
        threading.Thread(target=download_worker, daemon=True).start()

    def browse_local_video_file(self):
        path = filedialog.askopenfilename(
            title="Chọn file video có sẵn",
            filetypes=[
                ("Video", "*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.flv *.ts"),
                ("MP4", "*.mp4"),
                ("MKV", "*.mkv"),
                ("MOV", "*.mov"),
                ("Tất cả", "*.*"),
            ],
        )
        if path:
            self.local_path_entry.config(state=tk.NORMAL)
            self.local_path_entry.delete(0, tk.END)
            self.local_path_entry.insert(0, path)

    def browse_local_video_folder(self):
        path = filedialog.askdirectory(title="Chọn thư mục chứa video có sẵn")
        if path:
            self.local_path_entry.config(state=tk.NORMAL)
            self.local_path_entry.delete(0, tk.END)
            self.local_path_entry.insert(0, path)

    def parse_time_to_seconds(self, time_str: str) -> float:
        time_str = time_str.strip()
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 3:
                return (float(parts[0]) * 3600) + (float(parts[1]) * 60) + float(parts[2])
            if len(parts) == 2:
                return (float(parts[0]) * 60) + float(parts[1])
        return float(time_str)

    def _broll_mode_value(self):
        allowed = ("timesub_content", "heatmap_ranked", "heatmap_chronological")
        if hasattr(self, "broll_mode_var"):
            mode = (self.broll_mode_var.get() or "heatmap_ranked").strip().lower()
            mode = {"opencv": "heatmap_ranked", "timesub_semantic": "timesub_content"}.get(mode, mode)
            if hasattr(self, "use_ai_voice_var"):
                use_ai_voice = bool(self.use_ai_voice_var.get())
                if use_ai_voice and mode == "heatmap_chronological":
                    return "timesub_content"
                if not use_ai_voice and mode == "timesub_content":
                    return "heatmap_chronological"
            if mode in allowed:
                return mode
        mode = str(self.config.get("broll_mode", "heatmap_ranked") or "").strip().lower()
        mode = {"opencv": "heatmap_ranked", "timesub_semantic": "timesub_content"}.get(mode, mode)
        return mode if mode in allowed else "heatmap_ranked"

    def _broll_max_sec_value(self):
        raw = (
            self.broll_max_spin.get()
            if hasattr(self, "broll_max_spin")
            else self.config.get("broll_max_sec", 10.0)
        )
        try:
            return max(1.5, min(30.0, float(raw)))
        except (TypeError, ValueError):
            return 10.0

    def _minimum_duration_value(self):
        raw = (
            self.video_duration_var.get()
            if hasattr(self, "video_duration_var")
            else (
                self.video_min_duration_spin.get()
                if hasattr(self, "video_min_duration_spin")
                else self.config.get("video_min_duration_sec", 90.0)
            )
        )
        try:
            return max(30.0, min(900.0, float(raw)))
        except (TypeError, ValueError):
            return 90.0

    def _collect_post_options(self):
        use_ai_voice = bool(self.use_ai_voice_var.get()) if hasattr(self, "use_ai_voice_var") else True
        source_is_youtube = not hasattr(self, "source_mode_var") or self.source_mode_var.get() == "youtube"
        return {
            "use_ai_voice": use_ai_voice,
            "original_audio_mode": not use_ai_voice,
            "youtube_url": self.url_entry.get().strip() if source_is_youtube else "",
            "video_min_duration_sec": self._minimum_duration_value(),
            "original_audio_target_sec": self._minimum_duration_value(),
            "speed": float(self.speed_spin.get()),
            "broll_mode": self._broll_mode_value(),
            "broll_max_sec": self._broll_max_sec_value(),
            "random_broll_mirror": bool(
                self.random_broll_mirror_var.get()
                if hasattr(self, "random_broll_mirror_var")
                else self.config.get("random_broll_mirror", False)
            ),
            "audio_boost": float(self.audio_boost_spin.get()),
            "mix_original_audio_with_voice": bool(
                use_ai_voice and self.mix_original_audio_var.get()
                if hasattr(self, "mix_original_audio_var") else False
            ),
            "original_audio_mix_percent": max(
                0.0, min(100.0, float(self.original_audio_mix_spin.get() or 50.0))
            ) if hasattr(self, "original_audio_mix_spin") else 50.0,
            "blur_bg": self.blur_var.get(),
            "zoom_in": self.zoom_var.get(),
            "zoom_percent": float(self.zoom_spin.get()),
            "scale_w": float(self.scale_w_spin.get()),
            "scale_h": float(self.scale_h_spin.get()),
            "use_crop": self.config.get("use_crop", False),
            "crop_w": int(self.config.get("crop_w", self.config.get("base_w", 1920))),
            "crop_h": int(self.config.get("crop_h", self.config.get("base_h", 1080))),
            "crop_x": int(self.config.get("crop_x", 0)),
            "crop_y": int(self.config.get("crop_y", 0)),
            "use_blur_mask": self.config.get("use_blur_mask", False),
            "blur_shape": self.config.get("blur_shape", "Hình chữ nhật"),
            "blur_mask_x": int(self.config.get("blur_mask_x", 100)),
            "blur_mask_y": int(self.config.get("blur_mask_y", 100)),
            "blur_mask_w": int(self.config.get("blur_mask_w", 300)),
            "blur_mask_h": int(self.config.get("blur_mask_h", 150)),
            "use_custom_hook": False,
            "custom_hook_path": "",
            "hook_audio_boost": float(self.config.get("hook_audio_boost", 6.0)),
            "hook_no_speed": self.config.get("hook_no_speed", True),
            "temperature": float(self.config.get("temperature", 0)),
            "tint": float(self.config.get("tint", 0)),
            "saturation": float(self.config.get("saturation", 0)),
            "exposure": float(self.config.get("exposure", 0)),
            "contrast": float(self.config.get("contrast", 0)),
            "highlights": float(self.config.get("highlights", 0)),
            "shadows": float(self.config.get("shadows", 0)),
            "whites": float(self.config.get("whites", 0)),
            "blacks": float(self.config.get("blacks", 0)),
            "brilliance": float(self.config.get("brilliance", 0)),
            "sharpen": float(self.config.get("sharpen", 0)),
            "clarity": float(self.config.get("clarity", 0)),
            "grain": float(self.config.get("grain", 0)),
            "blur": float(self.config.get("blur", 0)),
            "vignette": float(self.config.get("vignette", 0)),
            "sub_style_type": self.config.get("sub_style_type", "tiktok_slim"),
            "sub_size": int(self.config.get("sub_size", 16 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 22)),
            "sub_outline": int(self.config.get("sub_outline", 2 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 3)),
            "sub_shadow": int(self.config.get("sub_shadow", 1 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 0)),
            "title_y_pos": int(self.config.get("title_y_pos", 260)),
            "title_color1": self.config.get("title_color1", "black"),
            "title_color2": self.config.get("title_color2", "red"),
            "title_outline_color": self.config.get("title_outline_color", "none"),
            "title_bg_color": self.config.get("title_bg_color", "white"),
            "sub_margin_v": int(self.config.get("sub_margin_v", 100)),
            "sub_color": self.config.get("sub_color", "&HFFFFFF&"),
            "sub_outline_color": self.config.get("sub_outline_color", "&H000000&"),
            "cleanup_temp_after_export": bool(
                self.cleanup_temp_var.get()
                if hasattr(self, "cleanup_temp_var")
                else self.config.get("cleanup_temp_after_export", False)
            ),
            "scramble_original_audio": bool(
                self.scramble_original_audio_var.get()
                if hasattr(self, "scramble_original_audio_var")
                else self.config.get("scramble_original_audio", True)
            ),
            "gemini_grid_inspector": bool(
                self.gemini_grid_inspector_var.get()
                if hasattr(self, "gemini_grid_inspector_var")
                else self.config.get("gemini_grid_inspector", True)
            ),
            "api_key": self.api_key_entry.get().strip() if hasattr(self, "api_key_entry") else str(self.config.get("api_key", "")),
            "ai_model": self.ai_model_cb.get().strip() if hasattr(self, "ai_model_cb") else str(self.config.get("ai_model", "")),
            "color_look": self.config.get("color_look", "Không lọc"),
            "color_look_intensity": float(self.config.get("color_look_intensity", 70)),
            "color_look_stack": deepcopy(self.config.get("color_look_stack", [])),
        }

    def _create_gemini_native_hook(self, specific_dir, source_video, proxy_video,
                                   video_duration, hook_duration, allow_single_gemini_call=False):
        """Dùng mốc từ request viết script; không bao giờ gọi Gemini lần hai."""
        duration = float(hook_duration or 0.0)
        if duration <= 0.0:
            return ""
        selection_path = os.path.join(specific_dir, "gemini_hook_selection.json")
        try:
            with open(selection_path, "r", encoding="utf-8") as stream:
                choice = json.load(stream)
            start = float(choice["start_sec"])
            print(
                f"✅ [HOOK GEMINI] Dùng mốc từ cùng request: {start:.2f}s–{start + duration:.2f}s | "
                f"độ tin cậy {choice.get('confidence', 0.0):.0%} | {choice.get('reason', '')}"
            )
        except Exception:
            if allow_single_gemini_call:
                print("🎯 [HOOK GEMINI] Không có bước viết voice; Gemini sẽ xem proxy đúng một lần để chọn hook.")
                try:
                    choice = AntigravityProcessor.select_best_hook(
                        specific_dir, proxy_video, float(video_duration or 0.0), duration
                    )
                    start = float(choice["start_sec"])
                except Exception as gemini_error:
                    print(f"⚠️ [HOOK GEMINI] Lượt chọn hook lỗi: {gemini_error}")
                    fallback = DownloaderProcessor.find_best_hook_timestamp(
                        proxy_video, float(video_duration or 0.0), duration
                    )
                    start = float(fallback[0] if isinstance(fallback, (tuple, list)) else fallback)
            else:
                print("🔁 [HOOK GEMINI] Response viết voice thiếu mốc; dùng bộ dò local, không gọi Gemini lần hai.")
                fallback = DownloaderProcessor.find_best_hook_timestamp(
                    proxy_video, float(video_duration or 0.0), duration
                )
                start = float(fallback[0] if isinstance(fallback, (tuple, list)) else fallback)
        hook_file = DownloaderProcessor.get_hook_segment(
            video_path=source_video,
            video_duration=float(video_duration or 0.0),
            duration=duration,
            manual_start_time=float(start),
        )
        print(f"✅ [HOOK GEMINI] Đã cắt từ source HD và giữ âm thanh gốc: {hook_file}")
        return hook_file

    def _run_local_source_pipeline(self, local_path, hook_time, hook_duration, model, api_key, prompt, voice,
                                   job=None, managed_by_queue=False):
        """Mode Video có sẵn: copy + render 360p + xuất sub, rồi AI/TTS/editor. Không yt-dlp."""
        import time
        start_time_total = time.time()
        print(f"\n{'='*60}\n📁 [LOCAL] Chạy từ file/thư mục có sẵn — BỎ QUA tải YouTube (yt-dlp).\n{'='*60}")
        try:
            use_ai_voice = bool(job.get("use_ai_voice", job.get("post_options", {}).get("use_ai_voice", True))) if job else bool(self.use_ai_voice_var.get())
            post_snapshot = (job or {}).get("post_options", {})
            minimum_duration = float(
                post_snapshot.get("video_min_duration_sec", post_snapshot.get("original_audio_target_sec", 90.0))
                if job else self._minimum_duration_value()
            )
            playback_speed = float(post_snapshot.get("speed", 1.0) if job else self.speed_spin.get())
            broll_mode = normalize_mode_for_voice(
                post_snapshot.get("broll_mode", self._broll_mode_value())
                if job else self._broll_mode_value(),
                use_ai_voice,
            )
            broll_max_sec = float(post_snapshot.get("broll_max_sec", self._broll_max_sec_value()) if job else self._broll_max_sec_value())
            hook_mode = normalize_hook_mode(
                job.get("hook_mode") if job else self._hook_mode_value(),
                self.use_custom_hook_var.get() if not job else False,
            )
            use_custom_hook = hook_mode == "ai" and use_ai_voice
            if hook_mode == "ai" and not use_ai_voice:
                hook_mode = "native"
            self.config["use_custom_hook"] = use_custom_hook
            self.config["hook_mode"] = hook_mode

            source_started = time.time()
            prepared = LocalSourceProcessor.prepare(
                local_path,
                with_transcript=use_ai_voice,
                work_key=str((job or {}).get("_part_work_key", "")),
                skip_whisper=bool((job or {}).get("_skip_source_whisper", False)),
            )
            source_seconds = time.time() - source_started
            specific_dir = prepared["specific_dir"]
            if job and isinstance(job, dict) and "id" in job:
                job["specific_dir"] = specific_dir
                try:
                    self.job_queue.update(job["id"], specific_dir=specific_dir)
                except Exception:
                    pass
            trans_text = prepared["transcript"]
            gemini_proxy = os.path.join(specific_dir, "source_video_low.mp4")
            if use_ai_voice:
                evidence_mode = AIProcessor.detect_evidence_mode(trans_text)
                if evidence_mode == "visual_only":
                    print("🎥 [VISUAL-ONLY] Không có transcript đủ tin cậy — Gemini sẽ xem proxy và tự kể theo hình.")
                    broll_mode = ("heatmap_ranked" if broll_mode == "heatmap_ranked"
                                  else "heatmap_chronological")
                else:
                    print(f"✅ [LOCAL] Sub sẵn sàng ({len(trans_text)} ký tự). Thư mục: {specific_dir}")
            else:
                evidence_mode = "none"
                print(f"✅ [LOCAL] Chế độ tiếng gốc: bỏ qua transcript, AI và Voice. Thư mục: {specific_dir}")

            try:
                from scene_mapper import build_scene_map
                build_scene_map(os.path.join(specific_dir, "source_video_low.mp4"), target_limit=broll_max_sec)
            except Exception as map_error:
                print(f"⚠️ [SCENE MAP] Chưa tạo sớm được bản đồ: {map_error}")

            ai_seconds = 0.0
            if use_ai_voice:
                print("\n🤖 [NHÁNH 2] Gọi AI tóm tắt và sinh voice từ transcript local...")
                ai_started = time.time()
                highlight_candidates = []
                if broll_mode in ("heatmap_ranked", "heatmap_chronological"):
                    from scene_mapper import prepare_highlight_candidates
                    highlight_candidates = prepare_highlight_candidates(
                        os.path.join(specific_dir, "source_video_low.mp4"),
                        os.path.join(specific_dir, "transcript_sub.srt"), specific_dir,
                        broll_mode, minimum_duration * playback_speed,
                        max_duration=broll_max_sec,
                    )
                script, banner_title, voice_path = AIProcessor.resume_script_and_voice(
                    model, api_key, prompt, trans_text, specific_dir, voice,
                    minimum_duration_sec=minimum_duration,
                    playback_speed=playback_speed,
                    source_context={
                        **self._output_market_context(),
                        "title": prepared.get("title", ""),
                        "description": "",
                        "language": "",
                        "specific_dir": specific_dir,
                        "video_path": gemini_proxy,
                        "evidence_mode": evidence_mode,
                        "video_duration": prepared.get("duration", 0),
                        "gemini_hook_duration": hook_duration if hook_mode == "gemini" else 0.0,
                    },
                    narration_mode=broll_mode,
                    highlight_candidates=highlight_candidates,
                )
                ai_seconds = time.time() - ai_started
                if banner_title:
                    print(f"🏷️ [BANNER TITLE]: {banner_title}")
                print(f"\n📝 [KỊCH BẢN GIỌNG ĐỌC]:\n{script}\n")
                print(f"💾 [NHÁNH 2] Voice sẵn sàng tại: -> {voice_path}")

            if use_custom_hook:
                ai_path = (job or {}).get("ai_video_path", "")
                if managed_by_queue:
                    if not self._valid_ai_video(ai_path):
                        self._print_timing_report(
                            source_seconds, ai_seconds, 0, time.time() - start_time_total
                        )
                        print("⏸️ [HOOK AI] Source, script và voice đã sẵn sàng — chuyển job sang Chờ video AI.")
                        raise WaitingForAIHook()
                    opts = deepcopy((job or {}).get("post_options", {}))
                    opts.update({
                        "use_custom_hook": True, "custom_hook_path": ai_path,
                        "ai_video_path": ai_path,
                    })
                    render_started = time.time()
                    final_video_path = EditorAI.build_final_video(specific_dir, opts)
                    render_seconds = time.time() - render_started
                    self._print_timing_report(
                        source_seconds, ai_seconds, render_seconds, time.time() - start_time_total
                    )
                    print(f"\n🎉 [SUCCESS] HOÀN TẤT PIPELINE LOCAL HOOK AI!\n-> {final_video_path}")
                    return final_video_path
                print("⏭️ [HOOK AI] Đã chuẩn bị xong. Mở Custom Hook Studio để render thủ công.")
                return ""

            native_hook_enabled = float(hook_duration or 0.0) > 0.0
            if native_hook_enabled:
                if hook_mode == "gemini":
                    self._create_gemini_native_hook(
                        specific_dir, prepared["video_path"],
                        gemini_proxy,
                        prepared.get("duration", 0), hook_duration,
                        allow_single_gemini_call=not use_ai_voice,
                    )
                else:
                    print("🎬 [HOOK GỐC] Cắt source HD theo mốc thời gian trên GUI...")
                    DownloaderProcessor.get_hook_segment(
                        video_path=prepared["video_path"],
                        video_duration=prepared.get("duration", 0),
                        duration=hook_duration,
                        manual_start_time=hook_time
                    )
            else:
                print("⏭️ [HOOK TẮT] Hook = 0s — bắt đầu video trực tiếp từ cảnh B-roll Top 1.")

            print(f"\n🎬 Đang khởi chạy Module Biên tập Hậu kỳ & Xuất Video Final (Bước 3)...")
            post_options = deepcopy((job or {}).get("post_options", {})) if job else self._collect_post_options()
            post_options["native_hook_enabled"] = native_hook_enabled
            if not use_ai_voice:
                source_title = str(prepared.get("title") or "").strip()
                if source_title:
                    with open(os.path.join(specific_dir, "original_title.txt"), "w", encoding="utf-8") as f_title:
                        f_title.write(source_title)
                post_options.update({
                    "use_ai_voice": False, "original_audio_mode": True,
                    "video_min_duration_sec": minimum_duration,
                    "original_audio_target_sec": minimum_duration, "broll_mode": broll_mode,
                    "use_custom_hook": False, "custom_hook_path": "",
                    "source_title": source_title,
                })
            render_started = time.time()
            final_video_path = EditorProcessor.build_final_video(specific_dir, post_options)
            render_seconds = time.time() - render_started

            elapsed_seconds = int(time.time() - start_time_total)
            if elapsed_seconds < 60:
                time_str = f"{elapsed_seconds}s"
            else:
                m = elapsed_seconds // 60
                s = elapsed_seconds % 60
                time_str = f"{m} phút {s}s"

            self._print_timing_report(
                source_seconds, ai_seconds, render_seconds, time.time() - start_time_total
            )
            print(f"\n🎉 [SUCCESS] HOÀN TẤT PIPELINE LOCAL! (Tổng thời gian: {time_str})\nVideo thành phẩm tại:\n-> {final_video_path}")
            subprocess.Popen(f'explorer /select,"{os.path.abspath(final_video_path)}"')
            return final_video_path
        except Exception as e:
            print(f"\n❌ [LỖI PIPELINE LOCAL]: {str(e)}")
            if managed_by_queue:
                raise
        finally:
            if not managed_by_queue and hasattr(self, "btn_step1"):
                self.root.after(0, lambda: self.btn_step1.config(state=tk.NORMAL))

    def process_pipeline_thread(self, url, hook_time, hook_duration, model, api_key, prompt, voice,
                                source_mode="youtube", local_path="", job=None, managed_by_queue=False):
        if (source_mode or "youtube") == "local":
            return self._run_local_source_pipeline(
                local_path, hook_time, hook_duration, model, api_key, prompt, voice,
                job=job, managed_by_queue=managed_by_queue,
            )

        import time
        import concurrent.futures
        start_time_total = time.time()
        
        print(f"\n{'='*60}\n⏱️ [TIMER] Đã bắt đầu bấm giờ toàn bộ quy trình tự động (Đa luồng song song chuẩn xác)...\n{'='*60}")
        
        try:
            use_ai_voice = bool(job.get("use_ai_voice", job.get("post_options", {}).get("use_ai_voice", True))) if job else bool(self.use_ai_voice_var.get())
            post_snapshot = (job or {}).get("post_options", {})
            minimum_duration = float(
                post_snapshot.get("video_min_duration_sec", post_snapshot.get("original_audio_target_sec", 90.0))
                if job else self._minimum_duration_value()
            )
            playback_speed = float(post_snapshot.get("speed", 1.0) if job else self.speed_spin.get())
            broll_mode = normalize_mode_for_voice(
                post_snapshot.get("broll_mode", self._broll_mode_value())
                if job else self._broll_mode_value(),
                use_ai_voice,
            )
            broll_max_sec = float(post_snapshot.get("broll_max_sec", self._broll_max_sec_value()) if job else self._broll_max_sec_value())
            hook_mode = normalize_hook_mode(
                job.get("hook_mode") if job else self._hook_mode_value(),
                self.use_custom_hook_var.get() if not job else False,
            )
            use_custom_hook = hook_mode == "ai" and use_ai_voice
            if hook_mode == "ai" and not use_ai_voice:
                hook_mode = "native"
            self.config["use_custom_hook"] = use_custom_hook
            self.config["hook_mode"] = hook_mode
            if use_custom_hook:
                print("🎬 [HOOK AI] Path B bật: tải high+low + sub + TTS, bỏ cắt hook YouTube, dừng trước editor cũ.")

            # 1. Trích xuất metadata trước để lấy chính xác tên thư mục riêng của video ngay từ đầu
            print("📁 Đang phân tích metadata video để tạo thư mục lưu trữ...")
            video_title = "video_task"
            info_meta = {}
            try:
                info_meta = DownloaderProcessor.get_video_info(url)
                video_title = DownloaderProcessor.clean_source_title(
                    info_meta.get("title", "Unknown Title"), info_meta.get("uploader", "")
                )
            except:
                pass
            
            specific_dir = DownloaderProcessor.get_safe_video_folder(video_title)
            print(f"📁 Thư mục độc lập của video: {specific_dir}")
            if job and isinstance(job, dict) and "id" in job:
                job["specific_dir"] = specific_dir
                try:
                    self.job_queue.update(job["id"], specific_dir=specific_dir)
                except Exception:
                    pass

            # Lưu luôn tiêu đề gốc vào thư mục riêng
            try:
                title_file_path = os.path.join(specific_dir, "original_title.txt")
                with open(title_file_path, "w", encoding="utf-8") as f_title:
                    f_title.write(video_title)
            except:
                pass

            hook_file = None
            summary_result = ""
            voice_output_path = ""
            import threading
            visual_pool_ready = threading.Event()
            audio_ready = threading.Event()
            visual_pool_error = []

            # Máy yếu chạy tuần tự để tránh đầy RAM; máy vừa/mạnh chạy hai nhánh song song.
            hardware = get_hardware_profile()
            pipeline_workers = 2 if hardware.get("parallel_pipeline", True) else 1
            download_threads = int(hardware.get("download_threads", 8))
            print(
                f"🖥️ [MÁY] {hardware.get('strength', 'auto')} | "
                f"encoder {hardware.get('encoder', 'libx264')} | {pipeline_workers} nhánh | "
                f"{download_threads} luồng tải"
            )
            with concurrent.futures.ThreadPoolExecutor(max_workers=pipeline_workers) as executor:

                # --- NHÁNH 1: TẢI VIDEO GỐC & VIDEO LOW-RES CHO OPENCV ---
                def download_branch():
                    branch_started = time.time()
                    print("\n📥 [NHÁNH 1] Bắt đầu tải video gốc (đa luồng) và bản low-res (360p)...")
                    os.makedirs(specific_dir, exist_ok=True)
                    video_filename = os.path.join(specific_dir, "source_video.mp4")
                    
                    # 1. Tải video gốc (có sẵn hình + tiếng) — skip nếu file trong cùng slug đã hợp lệ
                    output_template = os.path.join(specific_dir, "source_video.%(ext)s")
                    ydl_opts = {
                        'outtmpl': output_template,
                        'merge_output_format': 'mp4',
                        'noplaylist': True, 
                        'quiet': False,
                        'no_warnings': True, 
                        'nocheckcertificate': True, 
                        'nopart': True,
                        'retries': 10, 
                        'fragment_retries': 10,
                        'concurrent_fragment_downloads': download_threads,
                        'headers': {
                            'Accept-Language': 'en-US,en;q=0.9',
                            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
                        },
                    }
                    
                    info_dict = DownloaderProcessor.download_high_res_video(
                        url, video_filename, ydl_opts=ydl_opts, log_fn=print
                    )
                    if not info_dict.get("_skipped") and not info_dict.get("_high_ok"):
                        print("⚠️ [NHÁNH 1] Video gốc không tải được file dùng được — xem log format/độ phân giải phía trên.")

                    # 2. Tải bản low-res 360p (DASH đủ duration; stub format 18 → transcode)
                    low_res_path = os.path.join(specific_dir, "source_video_low.mp4")
                    expected_dur = float(
                        info_dict.get("duration")
                        or DownloaderProcessor.probe_duration_sec(video_filename)
                        or 0
                    )
                    DownloaderProcessor.download_low_res_video(
                        url,
                        low_res_path,
                        expected_duration=expected_dur,
                        source_video=video_filename,
                        log_fn=print,
                    )

                    # Tạo scene map ngay khi low-res vừa sẵn sàng. Nhánh AI/TTS
                    # vẫn đang chạy song song; editor sau đó chỉ đọc cache.
                    try:
                        from scene_mapper import build_scene_map
                        build_scene_map(low_res_path, target_limit=broll_max_sec)
                    except Exception as map_error:
                        print(f"⚠️ [SCENE MAP] Chưa tạo sớm được bản đồ: {map_error}")
                        visual_pool_error.append(map_error)
                    finally:
                        visual_pool_ready.set()

                    # 3. Trích xuất audio WAV cho Whisper/AI (chỉ khi source_video.mp4 ffprobe OK)
                    audio_path = ""
                    if use_ai_voice:
                        audio_path = os.path.join(specific_dir, "extracted_audio.wav")
                        try:
                            DownloaderProcessor.extract_pcm_wav(video_filename, audio_path, log_fn=print)
                        except Exception as audio_error:
                            print(f"🎥 [VISUAL-ONLY] Video không có audio dùng được: {audio_error}")
                        finally:
                            audio_ready.set()
                    else:
                        print("⏭️ [TIẾNG GỐC] Bỏ trích WAV vì không chạy AI/Voice.")
                        audio_ready.set()

                    v_info = {
                        "video_path": video_filename,
                        "audio_path": audio_path,
                        "title": DownloaderProcessor.clean_source_title(
                            info_dict.get("title") or video_title, info_dict.get("uploader", "")
                        ),
                        "duration": info_dict.get("duration", 0),
                        "specific_dir": specific_dir
                    }

                    if use_custom_hook:
                        print("⏭️ [HOOK AI] Bỏ qua cắt hook YouTube — Path B dùng clip AI sau khi sếp trỏ file.")
                        return v_info, None, time.time() - branch_started

                    if hook_mode == "gemini" and float(hook_duration or 0.0) > 0.0:
                        print("⏳ [HOOK GEMINI] Proxy đã sẵn sàng — chờ các nhánh hoàn tất rồi Gemini sẽ chọn mốc.")
                        return v_info, None, time.time() - branch_started

                    if float(hook_duration or 0.0) > 0.0:
                        h_file = DownloaderProcessor.get_hook_segment(
                            video_path=v_info["video_path"],
                            video_duration=v_info["duration"],
                            duration=hook_duration,
                            manual_start_time=hook_time
                        )
                        print(f"✅ [NHÁNH 1] Cắt Hook thành công tại: {h_file}")
                    else:
                        h_file = ""
                        print("⏭️ [HOOK TẮT] Hook = 0s — bỏ qua cắt Hook, video sẽ bắt đầu từ cảnh B-roll Top 1.")
                    return v_info, h_file, time.time() - branch_started

                # --- NHÁNH 2: BỐC SUB, GỬI DEEPSEEK VÀ SINH VOICE (GHI THẲNG VÀO THƯ MỤC RIÊNG) ---
                def ai_tts_branch():
                    branch_started = time.time()
                    print("\n🤖 [NHÁNH 2] Tiến hành lấy sub YouTube, gọi DeepSeek và sinh voice song song...")
                    
                    # Bốc sub trực tiếp qua URL YouTube API và lưu thẳng vào specific_dir
                    trans_text = AIProcessor.transcribe_audio(
                        audio_path=None, video_url=url, output_dir=specific_dir,
                        youtube_caption_only=True,
                    )
                    if not trans_text.strip():
                        print("⏳ [NHÁNH 2] YouTube không có caption — chờ audio trích xuất để chạy Whisper AI bốc sub...")
                        if audio_ready.wait(timeout=600):
                            extracted_wav = os.path.join(specific_dir, "extracted_audio.wav")
                            if os.path.isfile(extracted_wav) and os.path.getsize(extracted_wav) > 1024:
                                try:
                                    trans_text = AIProcessor.transcribe_audio(
                                        audio_path=extracted_wav, video_url=None, output_dir=specific_dir,
                                        youtube_caption_only=False,
                                    )
                                except Exception as whisper_err:
                                    print(f"⚠️ [WHISPER FALLBACK] Không chạy được Whisper: {whisper_err}")
                    evidence_mode = AIProcessor.detect_evidence_mode(trans_text)
                    ai_broll_mode = broll_mode
                    effective_model = model
                    if evidence_mode == "visual_only":
                        print("🎥 [VISUAL-ONLY] Không có transcript đủ tin cậy — Gemini sẽ xem storyboard ảnh và tự kể theo hình.")
                        if "Antigravity" not in effective_model:
                            effective_model = "Google Antigravity (Local)"
                            print("🔄 [VISUAL-ONLY] Tự chuyển sang Google Antigravity Local vì model API không xem được video.")
                        ai_broll_mode = ("heatmap_ranked" if broll_mode == "heatmap_ranked"
                                         else "heatmap_chronological")
                    else:
                        print(f"✅ [NHÁNH 2] Bốc sub thành công ({len(trans_text)} ký tự).")

                    highlight_candidates = []
                    if ai_broll_mode in ("heatmap_ranked", "heatmap_chronological"):
                        print("⏳ [NHÁNH 2] Chờ Top cảnh từ OpenCV/heatmap trước khi viết lời...")
                        if not visual_pool_ready.wait(timeout=1800):
                            raise RuntimeError("Hết thời gian chờ bản đồ cảnh cho prompt highlight.")
                        if visual_pool_error:
                            print(f"⚠️ [HIGHLIGHT PRE-AI] Scene map từng báo lỗi: {visual_pool_error[-1]}")
                        from scene_mapper import prepare_highlight_candidates
                        highlight_candidates = prepare_highlight_candidates(
                            os.path.join(specific_dir, "source_video_low.mp4"),
                            os.path.join(specific_dir, "transcript_sub.srt"), specific_dir,
                            ai_broll_mode, minimum_duration * playback_speed,
                            max_duration=broll_max_sec, youtube_url=url,
                        )

                    # Antigravity xem visual storyboard tối đa 600 frame từ video proxy 360p. Kể cả Timesub
                    # cũng chờ proxy 360p và storyboard sẵn sàng trước khi gọi AI.
                    proxy_path = os.path.join(specific_dir, "source_video_low.mp4")
                    if "Antigravity" in effective_model:
                        print("⏳ [ANTIGRAVITY LOCAL] Chờ proxy 360p và visual storyboard cho AI...")
                        if not visual_pool_ready.wait(timeout=1800):
                            raise RuntimeError("Hết thời gian chờ video proxy 360p cho Antigravity.")
                        if not os.path.isfile(proxy_path) or os.path.getsize(proxy_path) < 1024:
                            raise RuntimeError("Proxy 360p chưa sẵn sàng cho Antigravity local.")
                        try:
                            from scene_mapper import ensure_visual_storyboards
                            analysis_dir = os.path.join(specific_dir, "ai_storyboard")
                            ensure_visual_storyboards(proxy_path, analysis_dir, candidates=highlight_candidates)
                        except Exception as sb_err:
                            print(f"⚠️ [STORYBOARD PRE-AI] Lỗi chuẩn bị storyboard: {sb_err}")

                    # Gửi DeepSeek tóm tắt — tách title (banner) / script (TTS)
                    script, banner_title, v_file = AIProcessor.resume_script_and_voice(
                        effective_model, api_key, prompt, trans_text, specific_dir, voice,
                        minimum_duration_sec=minimum_duration,
                        playback_speed=playback_speed,
                        source_context={
                            **self._output_market_context(),
                            "title": video_title,
                            "description": info_meta.get("description", ""),
                            "language": info_meta.get("language", ""),
                            "specific_dir": specific_dir,
                            "video_path": proxy_path if "Antigravity" in effective_model else os.path.join(specific_dir, "source_video_low.mp4"),
                            "evidence_mode": evidence_mode,
                            "video_duration": (
                                info_meta.get("duration")
                                or DownloaderProcessor.probe_duration_sec(
                                    os.path.join(specific_dir, "source_video_low.mp4")
                                )
                                or 0.0
                            ),
                            "gemini_hook_duration": hook_duration if hook_mode == "gemini" else 0.0,
                        },
                        narration_mode=ai_broll_mode,
                        highlight_candidates=highlight_candidates,
                    )
                    if banner_title:
                        print(f"🏷️ [BANNER TITLE]: {banner_title}")
                    print(f"\n📝 [KỊCH BẢN GIỌNG ĐỌC]:\n{script}\n")

                    # Sinh voice AI ghi thẳng vào thư mục riêng — CHỈ nhận script
                    print(f"💾 [NHÁNH 2] Voice sẵn sàng tại: -> {v_file}")
                    
                    return script, v_file, time.time() - branch_started, ai_broll_mode

                # Kích hoạt chạy đồng thời cả 2 nhánh
                future_download = executor.submit(download_branch)
                future_download.add_done_callback(lambda _future: visual_pool_ready.set())
                future_ai = executor.submit(ai_tts_branch) if use_ai_voice else None

                # Chờ cả 2 nhánh hoàn tất
                video_info, hook_file, source_seconds = future_download.result()
                if future_ai is not None:
                    summary_result, voice_output_path, ai_seconds, broll_mode = future_ai.result()
                else:
                    summary_result, voice_output_path, ai_seconds = "", "", 0.0
                    print("⏭️ [AI + VOICE] Đã tắt — giữ âm thanh gốc của từng cảnh.")

            if hook_mode == "gemini" and float(hook_duration or 0.0) > 0.0:
                actual_duration = float(
                    video_info.get("duration")
                    or DownloaderProcessor.probe_duration_sec(video_info["video_path"])
                    or 0.0
                )
                hook_proxy = os.path.join(specific_dir, "source_video_low.mp4")
                hook_file = self._create_gemini_native_hook(
                    specific_dir, video_info["video_path"],
                    hook_proxy,
                    actual_duration, hook_duration,
                    allow_single_gemini_call=not use_ai_voice,
                )

            if use_custom_hook:
                ai_path = (job or {}).get("ai_video_path", "")
                if managed_by_queue:
                    if not self._valid_ai_video(ai_path):
                        self._print_timing_report(
                            source_seconds, ai_seconds, 0, time.time() - start_time_total
                        )
                        print("⏸️ [HOOK AI] Source, script và voice đã sẵn sàng — chuyển job sang Chờ video AI.")
                        raise WaitingForAIHook()
                    opts = deepcopy((job or {}).get("post_options", {}))
                    opts.update({
                        "use_custom_hook": True, "custom_hook_path": ai_path,
                        "ai_video_path": ai_path, "youtube_url": url,
                    })
                    render_started = time.time()
                    final_video_path = EditorAI.build_final_video(specific_dir, opts)
                    render_seconds = time.time() - render_started
                    self._print_timing_report(
                        source_seconds, ai_seconds, render_seconds, time.time() - start_time_total
                    )
                    print(f"\n🎉 [SUCCESS] HOÀN TẤT HOOK AI!\n-> {final_video_path}")
                    return final_video_path
                print(f"\n⏸️ [HOOK AI MODE] Đã chuẩn bị xong. Mở Custom Hook Studio để render thủ công.")
                return ""

            print(f"\n🎬 [5/4] Đang khởi chạy Module Biên tập Hậu kỳ & Xuất Video Final (Bước 3)...")
            post_options = deepcopy((job or {}).get("post_options", {})) if job else {
                "speed": float(self.speed_spin.get()),
                "audio_boost": float(self.audio_boost_spin.get()),
                "mix_original_audio_with_voice": bool(
                    self.mix_original_audio_var.get()
                    if hasattr(self, "mix_original_audio_var") else False
                ),
                "original_audio_mix_percent": max(
                    0.0, min(100.0, float(self.original_audio_mix_spin.get() or 50.0))
                ) if hasattr(self, "original_audio_mix_spin") else 50.0,
                "blur_bg": self.blur_var.get(),
                "zoom_in": self.zoom_var.get(),
                "zoom_percent": float(self.zoom_spin.get()),
                "scale_w": float(self.scale_w_spin.get()),
                "scale_h": float(self.scale_h_spin.get()),
                "use_crop": self.config.get("use_crop", False),
                "crop_w": int(self.config.get("crop_w", self.config.get("base_w", 1920))),
                "crop_h": int(self.config.get("crop_h", self.config.get("base_h", 1080))),
                "crop_x": int(self.config.get("crop_x", 0)),
                "crop_y": int(self.config.get("crop_y", 0)),
                "use_blur_mask": self.config.get("use_blur_mask", False),
                "blur_shape": self.config.get("blur_shape", "Hình chữ nhật"),
                "blur_mask_x": int(self.config.get("blur_mask_x", 100)),
                "blur_mask_y": int(self.config.get("blur_mask_y", 100)),
                "blur_mask_w": int(self.config.get("blur_mask_w", 300)),
                "blur_mask_h": int(self.config.get("blur_mask_h", 150)),
                "use_custom_hook": False,
                "custom_hook_path": "",
                "hook_audio_boost": float(self.config.get("hook_audio_boost", 6.0)),
                "hook_no_speed": self.config.get("hook_no_speed", True),
                "temperature": float(self.config.get("temperature", 0)),
                "tint": float(self.config.get("tint", 0)),
                "saturation": float(self.config.get("saturation", 0)),
                "exposure": float(self.config.get("exposure", 0)),
                "contrast": float(self.config.get("contrast", 0)),
                "highlights": float(self.config.get("highlights", 0)),
                "shadows": float(self.config.get("shadows", 0)),
                "whites": float(self.config.get("whites", 0)),
                "blacks": float(self.config.get("blacks", 0)),
                "brilliance": float(self.config.get("brilliance", 0)),
                "sharpen": float(self.config.get("sharpen", 0)),
                "clarity": float(self.config.get("clarity", 0)),
                "grain": float(self.config.get("grain", 0)),
                "blur": float(self.config.get("blur", 0)),
                "vignette": float(self.config.get("vignette", 0)),
                "sub_style_type": self.config.get("sub_style_type", "tiktok_slim"),
                "sub_size": int(self.config.get("sub_size", 16 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 22)),
                "sub_outline": int(self.config.get("sub_outline", 2 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 3)),
                "sub_shadow": int(self.config.get("sub_shadow", 1 if self.config.get("sub_style_type", "tiktok_slim") == "tiktok_slim" else 0)),
                "enable_title": bool(self.config.get("enable_title", True)),
                "enable_sub": bool(self.config.get("enable_sub", True)),
                "title_y_pos": int(self.config.get("title_y_pos", 260)),
                "title_color1": self.config.get("title_color1", "black"),
                "title_color2": self.config.get("title_color2", "red"),
                "title_outline_color": self.config.get("title_outline_color", "none"),
                "title_bg_color": self.config.get("title_bg_color", "white"),
                "sub_margin_v": int(self.config.get("sub_margin_v", 100)),
                "sub_color": self.config.get("sub_color", "&HFFFFFF&"),
                "sub_outline_color": self.config.get("sub_outline_color", "&H000000&"),
                "broll_mode": self._broll_mode_value(),
                "broll_max_sec": self._broll_max_sec_value(),
                "random_broll_mirror": bool(
                    self.random_broll_mirror_var.get()
                    if hasattr(self, "random_broll_mirror_var")
                    else self.config.get("random_broll_mirror", False)
                ),
                "cleanup_temp_after_export": bool(
                    self.cleanup_temp_var.get()
                    if hasattr(self, "cleanup_temp_var")
                    else self.config.get("cleanup_temp_after_export", False)
                ),
                "color_look": self.config.get("color_look", "Không lọc"),
                "color_look_intensity": float(self.config.get("color_look_intensity", 70)),
                "color_look_stack": deepcopy(self.config.get("color_look_stack", [])),
            }
            post_options["youtube_url"] = url
            post_options["scramble_original_audio"] = bool(
                self.scramble_original_audio_var.get()
                if hasattr(self, "scramble_original_audio_var")
                else self.config.get("scramble_original_audio", True)
            )
            post_options["gemini_grid_inspector"] = bool(
                self.gemini_grid_inspector_var.get()
                if hasattr(self, "gemini_grid_inspector_var")
                else self.config.get("gemini_grid_inspector", True)
            )
            post_options["api_key"] = self.api_key_entry.get().strip() if hasattr(self, "api_key_entry") else str(self.config.get("api_key", ""))
            post_options["ai_model"] = self.ai_model_cb.get().strip() if hasattr(self, "ai_model_cb") else str(self.config.get("ai_model", ""))
            post_options["native_hook_enabled"] = (
                hook_mode in ("native", "gemini") and float(hook_duration or 0.0) > 0.0
            )
            if not use_ai_voice:
                source_title = str(video_info.get("title") or video_title or "").strip()
                if source_title:
                    with open(os.path.join(specific_dir, "original_title.txt"), "w", encoding="utf-8") as f_title:
                        f_title.write(source_title)
                post_options.update({
                    "use_ai_voice": False, "original_audio_mode": True,
                    "video_min_duration_sec": minimum_duration,
                    "original_audio_target_sec": minimum_duration, "broll_mode": broll_mode,
                    "use_custom_hook": False, "custom_hook_path": "",
                    "source_title": source_title,
                })
            render_started = time.time()
            final_video_path = EditorProcessor.build_final_video(specific_dir, post_options)
            render_seconds = time.time() - render_started
            self._print_timing_report(
                source_seconds, ai_seconds, render_seconds, time.time() - start_time_total
            )

            elapsed_seconds = int(time.time() - start_time_total)
            if elapsed_seconds < 60:
                time_str = f"{elapsed_seconds}s"
            else:
                m = elapsed_seconds // 60
                s = elapsed_seconds % 60
                time_str = f"{m} phút {s}s"

            print(f"\n🎉 [SUCCESS] HOÀN TẤT TOÀN BỘ QUY TRÌNH ĐA LUỒNG TỐC ĐỘ CAO! (Tổng thời gian: {time_str})\nVideo thành phẩm tại:\n-> {final_video_path}")
            subprocess.Popen(f'explorer /select,"{os.path.abspath(final_video_path)}"')
            return final_video_path

        except Exception as e:
            print(f"\n❌ [LỖI PIPELINE]: {str(e)}")
            if managed_by_queue:
                raise
        finally:
            if not managed_by_queue and hasattr(self, "btn_step1"):
                self.root.after(0, lambda: self.btn_step1.config(state=tk.NORMAL))

    def run_step_2(self):
        self.save_configs_to_json()
        model = self.ai_model_cb.get()
        api_key = self.api_key_entry.get().strip()
        prompt = self.prompt_text.get("1.0", tk.END).strip()
        voice = self.voice_cb.get()
        minimum_duration = self._minimum_duration_value()
        playback_speed = float(self.speed_spin.get())
        
        if not api_key and "Antigravity" not in model:
            messagebox.showwarning("Cảnh báo", "Sếp chưa điền API Key cho Model AI kìa!")
            return
            
        self.btn_step2.config(state=tk.DISABLED)
        threading.Thread(
            target=self.process_step_2_thread,
            args=(model, api_key, prompt, voice, minimum_duration, playback_speed),
            daemon=True,
        ).start()

    def process_step_2_thread(self, model, api_key, prompt, voice,
                              minimum_duration=90.0, playback_speed=1.0):
        print(f"\n{'='*50}\n🤖 [BƯỚC 2] BẮT ĐẦU GỌI AI TÓM TẮT & SINH GIỌNG ĐỌC...\n{'='*50}")
        try:
            url = self.url_entry.get().strip()
            base_temp = "temp"
            sub_dirs = [os.path.join(base_temp, d) for d in os.listdir(base_temp) if os.path.isdir(os.path.join(base_temp, d))]
            if sub_dirs:
                specific_dir = max(sub_dirs, key=os.path.getmtime)
            else:
                specific_dir = base_temp

            audio_source = os.path.join(specific_dir, "extracted_audio.wav")
            real_transcript = AIProcessor.transcribe_audio(
                audio_source, video_url=url, output_dir=specific_dir,
                youtube_caption_only=bool(url),
            )
            evidence_mode = AIProcessor.detect_evidence_mode(real_transcript)
            effective_model = model
            if evidence_mode == "visual_only" and "Antigravity" not in effective_model:
                effective_model = "Google Antigravity (Local)"
                print("🔄 [VISUAL-ONLY] Không có sub YouTube — tự chuyển sang Google Antigravity Local.")
            broll_mode = self._broll_mode_value()
            highlight_candidates = []
            if broll_mode in ("heatmap_ranked", "heatmap_chronological"):
                from scene_mapper import prepare_highlight_candidates
                highlight_candidates = prepare_highlight_candidates(
                    os.path.join(specific_dir, "source_video_low.mp4"),
                    os.path.join(specific_dir, "transcript_sub.srt"), specific_dir,
                    broll_mode, minimum_duration * playback_speed,
                    max_duration=self._broll_max_sec_value(), youtube_url=url,
                )
            
            # Bước AI tạo luôn voice + SRT timing chuẩn ngay tại đây. Hậu kỳ chỉ
            # đọc file SRT có sẵn, không chạy nhận diện audio khi máy đang render.
            script, banner_title, voice_file = AIProcessor.resume_script_and_voice(
                effective_model, api_key, prompt, real_transcript, specific_dir, voice,
                minimum_duration_sec=minimum_duration,
                playback_speed=playback_speed,
                source_context={
                    **self._output_market_context(),
                    "title": os.path.basename(specific_dir), "description": "", "language": "",
                    "specific_dir": specific_dir,
                    "video_path": os.path.join(specific_dir, "source_video_low.mp4"),
                    "evidence_mode": evidence_mode,
                },
                narration_mode=broll_mode,
                highlight_candidates=highlight_candidates,
            )
            if banner_title:
                print(f"🏷️ [BANNER TITLE]: {banner_title}")
            print(f"\n📝 [KỊCH BẢN GIỌNG ĐỌC]:\n{script}\n")
            print(
                "\n✅ [SUCCESS] Hoàn thành AI + voice + time-sub trước hậu kỳ!"
                f"\nVoice: -> {voice_file}"
                f"\nSub:   -> {os.path.join(specific_dir, 'voice_script_exact.srt')}"
            )
        except Exception as e:
            print(f"\n❌ [LỖI BƯỚC 2]: {str(e)}")
        finally:
            self.root.after(0, lambda: self.btn_step2.config(state=tk.NORMAL))

if __name__ == "__main__":
    root = tk.Tk()
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except:
        pass
    app = ProfessionalVideoApp(root)
    root.mainloop()
