"""
GIAO DIỆN TAB ĐỘC LẬP: CHẾ ĐỘ 3 - BIÊN TẬP & RE-MIX VIDEO TIKTOK ĐỐI THỦ (TIKTOK REMIXER GUI)
Tích hợp đầy đủ:
1. Quản lý Đặt tên & Lưu Config (Config Bar chuyên nghiệp).
2. Danh sách hàng đợi nhiều video TikTok (Queue Treeview) + Render tuần tự hàng đợi + Log tiến trình.
3. 3 Tab chức năng tinh gọn + Bộ 3 Studio chuyên nghiệp.
"""
import os
import sys
import json
import time
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, scrolledtext, filedialog, messagebox
from typing import Optional, Dict, Any, List

from tiktok_remixer_config import (
    load_tiktok_remixer_config,
    save_tiktok_remixer_config,
    DEFAULT_TIKTOK_REMIXER_CONFIG
)
from tiktok_remixer_engine import TikTokRemixerEngine
from market_profiles import get_market_profile, MARKET_PROFILES


class TikTokRemixerTab(ttk.Frame):
    def __init__(self, parent, main_app=None):
        super().__init__(parent)
        self.main_app = main_app
        self.config = load_tiktok_remixer_config()
        self.queue_items: List[Dict[str, Any]] = []
        self.is_processing_queue = False
        self.stop_requested = False
        
        self._build_ui()
        self._sync_ui_from_config()

    def _build_ui(self):
        # Bố cục 3 hàng:
        # Row 0: Header
        # Row 1: Notebook 3 Tab Cấu hình
        # Row 2: Config Bar (Chọn & Đặt tên Config)
        # Row 3: Danh Sách Video Hàng Đợi & Log Tiến Trình
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # 1. TIÊU ĐỀ HEADER
        header_frame = ttk.Frame(self, padding=(8, 4, 8, 2))
        header_frame.grid(row=0, column=0, sticky=tk.EW)

        title_lbl = ttk.Label(
            header_frame,
            text="🎬 CHẾ ĐỘ 3: BIÊN TẬP & RE-MIX VIDEO TIKTOK ĐỐI THỦ (TIKTOK REMIXER PRO)",
            font=("Segoe UI", 10, "bold"),
            foreground="#1E40AF"
        )
        title_lbl.pack(side=tk.LEFT)

        sub_lbl = ttk.Label(
            header_frame,
            text="Tẩy dấu vết đối thủ • Tách Hook & Cắt chuyển cảnh thông minh • Giọng CapCut mới • Độc quyền 100%",
            font=("Segoe UI", 8, "italic"),
            foreground="#6B7280"
        )
        sub_lbl.pack(side=tk.LEFT, padx=(10, 0))

        # 2. KHU VỰC 3 TAB CHỨC NĂNG
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, sticky=tk.EW, padx=6, pady=(1, 3))

        # --- TAB 1: NGUỒN & DỌN RÁC (CLEAN) ---
        self.tab1 = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab1, text="  1. Nguồn & Dọn Rác  ")
        self._build_tab1_source_clean()

        # --- TAB 2: AI & GIỌNG ĐỌC ---
        self.tab2 = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab2, text="  2. AI & Giọng Đọc  ")
        self._build_tab2_ai_voice()

        # --- TAB 3: RE-MIX & HẬU KỲ ---
        self.tab3 = ttk.Frame(self.notebook, padding=6)
        self.notebook.add(self.tab3, text="  3. Re-mix & Hậu Kỳ  ")
        self._build_tab3_remix_post()

        # 3. THANH CONFIG BAR (ĐẶT TÊN & LƯU CONFIG)
        self._build_config_bar()

        # 4. DANH SÁCH HÀNG ĐỢI & NHẬT KÝ TIẾN TRÌNH
        self._build_queue_and_logs()

    def _build_tab1_source_clean(self):
        # Nguồn Video / Link TikTok
        src_group = ttk.LabelFrame(self.tab1, text=" 📥 Nguồn Video TikTok / Reels / Shorts ", padding=8)
        src_group.pack(fill=tk.X, pady=(0, 4))

        ttk.Label(src_group, text="Link TikTok / File:").grid(row=0, column=0, sticky=tk.W, pady=2)
        self.url_entry = ttk.Entry(src_group, width=54)
        self.url_entry.grid(row=0, column=1, sticky=tk.EW, padx=5, pady=2)
        src_group.columnconfigure(1, weight=1)

        btn_browse = ttk.Button(src_group, text="📁 Chọn File", command=self._browse_source_file, width=10)
        btn_browse.grid(row=0, column=2, padx=2, pady=2)

        btn_paste = ttk.Button(src_group, text="📋 Dán Link", command=self._paste_clipboard, width=9)
        btn_paste.grid(row=0, column=3, padx=2, pady=2)

        # Thị trường đầu ra
        mkt_row = ttk.Frame(src_group)
        mkt_row.grid(row=1, column=0, columnspan=4, sticky=tk.EW, pady=(4, 1))

        ttk.Label(mkt_row, text="Thị trường đầu ra:").pack(side=tk.LEFT)
        self.market_cb = ttk.Combobox(
            mkt_row, values=[p["label"] for p in MARKET_PROFILES.values()],
            width=22, state="readonly"
        )
        self.market_cb.pack(side=tk.LEFT, padx=(5, 8))
        self.market_cb.bind("<<ComboboxSelected>>", self._on_market_change)

        self.auto_voice_locale_var = tk.BooleanVar(value=True)
        chk_auto_v = ttk.Checkbutton(mkt_row, text="Tự đổi giọng đọc đúng vùng", variable=self.auto_voice_locale_var)
        chk_auto_v.pack(side=tk.LEFT)

        # Khung Tẩy Dấu Vết Đối Thủ
        clean_group = ttk.LabelFrame(self.tab1, text=" 🛡️ Tẩy Dấu Vết Đối Thủ (Gemini CLI Auto-Clean) ", padding=8)
        clean_group.pack(fill=tk.X, pady=(0, 2))

        self.auto_clean_core_var = tk.BooleanVar(value=True)
        chk_core = ttk.Checkbutton(
            clean_group,
            text="✂️ Tự động cắt 2 đầu (Bỏ Title cũ & Nền mờ ➔ Lấy Lõi Video Sạch như Ảnh 2)",
            variable=self.auto_clean_core_var
        )
        chk_core.grid(row=0, column=0, columnspan=3, sticky=tk.W, pady=2)

        self.auto_blur_sub_var = tk.BooleanVar(value=True)
        chk_sub = ttk.Checkbutton(
            clean_group,
            text="🌫️ Tự động bôi mờ dải Sub cũ & Badge Part 2 ở đáy video",
            variable=self.auto_blur_sub_var
        )
        chk_sub.grid(row=1, column=0, sticky=tk.W, pady=2)

        blur_strength_row = ttk.Frame(clean_group)
        blur_strength_row.grid(row=1, column=1, columnspan=2, sticky=tk.W, padx=(10, 0))
        ttk.Label(blur_strength_row, text="Độ mờ:").pack(side=tk.LEFT)
        self.blur_strength_spin = ttk.Spinbox(blur_strength_row, from_=10, to=100, increment=5, width=4)
        self.blur_strength_spin.pack(side=tk.LEFT, padx=3)
        self.blur_strength_spin.set(75)
        ttk.Label(blur_strength_row, text="%").pack(side=tk.LEFT)

        self.ai_inspect_intro_outro_var = tk.BooleanVar(value=True)
        chk_intro_outro = ttk.Checkbutton(
            clean_group,
            text="🔍 AI tự kiểm tra & cắt logo TikTok giật ở 2 đầu clip (0.0s nếu không có)",
            variable=self.ai_inspect_intro_outro_var
        )
        chk_intro_outro.grid(row=2, column=0, columnspan=3, sticky=tk.W, pady=2)

    def _build_tab2_ai_voice(self):
        ai_group = ttk.LabelFrame(self.tab2, text=" 🎙️ Cấu hình Thuyết Minh Giọng Đọc AI & Kịch Bản ", padding=8)
        ai_group.pack(fill=tk.BOTH, expand=True, pady=(0, 2))

        self.use_ai_voice_var = tk.BooleanVar(value=True)
        chk_voice = ttk.Checkbutton(ai_group, text="✅ Bật giọng đọc AI thuyết minh kịch bản mới", variable=self.use_ai_voice_var)
        chk_voice.grid(row=0, column=0, columnspan=4, sticky=tk.W, pady=(0, 6))

        ttk.Label(ai_group, text="Engine TTS:").grid(row=1, column=0, sticky=tk.W, pady=3)
        self.tts_engine_cb = ttk.Combobox(ai_group, values=["CapCut TTS", "Edge-TTS (Miễn phí)"], width=16, state="readonly")
        self.tts_engine_cb.grid(row=1, column=1, sticky=tk.W, padx=4, pady=3)
        self.tts_engine_cb.set("CapCut TTS")

        ttk.Label(ai_group, text="Giọng đọc:").grid(row=1, column=2, sticky=tk.W, padx=(10, 2), pady=3)
        self.voice_name_cb = ttk.Combobox(ai_group, width=24, state="readonly")
        self.voice_name_cb.grid(row=1, column=3, sticky=tk.W, padx=4, pady=3)

        btn_test_voice = ttk.Button(ai_group, text="🔊 Nghe Thử", command=self._preview_voice, width=10)
        btn_test_voice.grid(row=1, column=4, padx=4, pady=3)

        ttk.Label(ai_group, text="Tốc độ:").grid(row=2, column=0, sticky=tk.W, pady=3)
        self.tts_speed_cb = ttk.Combobox(ai_group, values=["-10%", "-5%", "+0%", "+5%", "+10%"], width=8, state="readonly")
        self.tts_speed_cb.grid(row=2, column=1, sticky=tk.W, padx=4, pady=3)
        self.tts_speed_cb.set("+0%")

        ttk.Label(ai_group, text="Bộ não AI:").grid(row=3, column=0, sticky=tk.W, pady=(6, 2))
        lbl_ai_info = ttk.Label(
            ai_group,
            text="Google Antigravity (Gemini 2.5 Flash Multimodal CLI) • Xem video trực tiếp 1 lần duy nhất",
            foreground="#2563EB",
            font=("Segoe UI", 9, "bold")
        )
        lbl_ai_info.grid(row=3, column=1, columnspan=4, sticky=tk.W, padx=4, pady=(6, 2))

    def _build_tab3_remix_post(self):
        scene_group = ttk.LabelFrame(self.tab3, text=" 🎬 Phân Tách Cảnh Thông Minh & Re-mix ", padding=6)
        scene_group.pack(fill=tk.X, pady=(0, 4))

        self.keep_hook_var = tk.BooleanVar(value=True)
        chk_hook = ttk.Checkbutton(
            scene_group,
            text="🎣 Tách & Giữ lại đoạn Hook gốc mở đầu (Hình + Tiếng kịch tính gốc)",
            variable=self.keep_hook_var
        )
        chk_hook.grid(row=0, column=0, sticky=tk.W, pady=1)

        self.smart_transition_var = tk.BooleanVar(value=True)
        chk_trans = ttk.Checkbutton(
            scene_group,
            text="✂️ Cắt bỏ chuyển cảnh rác (Flash/Zoom 0.15s - 0.22s • Hard Cut giữ nguyên 0.0s)",
            variable=self.smart_transition_var
        )
        chk_trans.grid(row=1, column=0, sticky=tk.W, pady=1)

        self.elastic_speed_var = tk.BooleanVar(value=True)
        chk_elastic = ttk.Checkbutton(
            scene_group,
            text="⏱️ Điều tốc đàn hồi B-Roll (0.95x trên cảnh lẻ) khớp khít âm thanh & hình ảnh",
            variable=self.elastic_speed_var
        )
        chk_elastic.grid(row=2, column=0, sticky=tk.W, pady=1)

        self.shuffle_broll_var = tk.BooleanVar(value=True)
        chk_shuf = ttk.Checkbutton(scene_group, text="🔀 Đảo trật tự cảnh b-roll hợp lý", variable=self.shuffle_broll_var)
        chk_shuf.grid(row=3, column=0, sticky=tk.W, pady=1)

        self.mirror_broll_var = tk.BooleanVar(value=True)
        chk_mirr = ttk.Checkbutton(scene_group, text="🪞 Lật gương phản chiếu (hflip)", variable=self.mirror_broll_var)
        chk_mirr.grid(row=3, column=1, sticky=tk.W, padx=(10, 0), pady=1)

        self.overlay_sub_on_blur_var = tk.BooleanVar(value=True)
        chk_sub_over = ttk.Checkbutton(
            scene_group,
            text="🎯 Đè Sub mới chính xác lên đúng tọa độ Sub cũ đã Blur (Xóa sạch dấu vết)",
            variable=self.overlay_sub_on_blur_var
        )
        chk_sub_over.grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=1)

        # Bộ 3 Studio Buttons
        studio_group = ttk.LabelFrame(self.tab3, text=" 🎛️ Bộ 3 Studio Chuyên Nghiệp ", padding=6)
        studio_group.pack(fill=tk.X, pady=(0, 2))
        for c in range(3):
            studio_group.columnconfigure(c, weight=1)

        btn_color = ttk.Button(studio_group, text="🎨 Bảng Màu (15 Màu + 17 Look)", command=self._open_color_studio)
        btn_color.grid(row=0, column=0, sticky=tk.EW, padx=2, pady=1)

        btn_title_sub = ttk.Button(studio_group, text="🔤 Title & Sub Studio", command=self._open_title_sub_studio)
        btn_title_sub.grid(row=0, column=1, sticky=tk.EW, padx=2, pady=1)

        btn_crop = ttk.Button(studio_group, text="✂ Crop Studio (Xem Trước)", command=self._open_crop_studio)
        btn_crop.grid(row=0, column=2, sticky=tk.EW, padx=2, pady=1)

    def _build_config_bar(self):
        """Thanh quản lý Đặt tên Config & Lưu Config chuyên nghiệp."""
        config_bar = ttk.LabelFrame(self, text="  ⚙️ QUẢN LÝ CẤU HÌNH (CONFIG TIKTOK REMIXER)  ", padding=(6, 3))
        config_bar.grid(row=2, column=0, sticky=tk.EW, padx=6, pady=(1, 3))
        config_bar.columnconfigure(1, weight=1)
        config_bar.columnconfigure(3, weight=1)

        ttk.Label(config_bar, text="Chọn Config:").grid(row=0, column=0, sticky=tk.W, padx=(2, 4))
        self.config_cb = ttk.Combobox(
            config_bar,
            values=list(self.config.get("saved_configs", {"default": {}}).keys()),
            state="readonly", width=18
        )
        self.config_cb.grid(row=0, column=1, sticky=tk.EW, padx=(0, 8))
        self.config_cb.set(self.config.get("selected_config", "default"))
        self.config_cb.bind("<<ComboboxSelected>>", self._on_config_selected)

        ttk.Label(config_bar, text="Tên Config:").grid(row=0, column=2, sticky=tk.W, padx=(0, 4))
        self.config_name_entry = ttk.Entry(config_bar, width=20)
        self.config_name_entry.grid(row=0, column=3, sticky=tk.EW, padx=(0, 6))
        self.config_name_entry.insert(0, self.config.get("selected_config", "default"))

        ttk.Button(config_bar, text="↧ Load", command=self._load_named_config, width=7).grid(row=0, column=4, padx=2)
        ttk.Button(config_bar, text="💾 Lưu", command=self._save_named_config, width=7).grid(row=0, column=5, padx=2)
        ttk.Button(config_bar, text="🗑 Xóa", command=self._delete_named_config, width=7).grid(row=0, column=6, padx=2)

    def _build_queue_and_logs(self):
        """Khung Danh Sách Video Hàng Đợi & Log Tiến Trình."""
        bottom_frame = ttk.Frame(self, padding=4)
        bottom_frame.grid(row=3, column=0, sticky=tk.NSEW, padx=6, pady=(1, 2))
        bottom_frame.columnconfigure(0, weight=1)
        bottom_frame.rowconfigure(1, weight=1)
        bottom_frame.rowconfigure(3, weight=1)

        # Action Bar Buttons
        action_bar = ttk.Frame(bottom_frame)
        action_bar.grid(row=0, column=0, sticky=tk.EW, pady=(0, 3))
        action_bar.columnconfigure(7, weight=1)

        ttk.Button(action_bar, text="＋ Thêm vào Hàng Đợi", command=self._add_to_queue, style="Tool.TButton").grid(row=0, column=0, padx=2)
        ttk.Button(action_bar, text="🗑 Xóa", command=self._delete_queue_item, style="Danger.TButton" if "Danger.TButton" in ttk.Style().theme_names() else "Tool.TButton").grid(row=0, column=1, padx=2)
        ttk.Button(action_bar, text="↻ Chạy lại", command=self._retry_queue_item, style="Tool.TButton").grid(row=0, column=2, padx=2)
        ttk.Button(action_bar, text="▲", width=3, command=lambda: self._move_queue_item(-1)).grid(row=0, column=3, padx=1)
        ttk.Button(action_bar, text="▼", width=3, command=lambda: self._move_queue_item(1)).grid(row=0, column=4, padx=1)
        ttk.Button(action_bar, text="📁 Mở Thư Mục Xuất", command=self._open_output_folder, style="Tool.TButton").grid(row=0, column=5, padx=4)

        self.btn_start = ttk.Button(
            action_bar, text="🚀 BẮT ĐẦU RE-MIX HÀNG ĐỢI",
            command=self._start_queue_processing, style="Primary.TButton"
        )
        self.btn_start.grid(row=0, column=6, padx=6)

        self.btn_stop = ttk.Button(
            action_bar, text="■ Dừng",
            command=self._stop_queue_processing, style="Tool.TButton"
        )
        self.btn_stop.grid(row=0, column=7, sticky=tk.E, padx=2)

        # Bảng Treeview Danh Sách Video
        queue_box = ttk.LabelFrame(bottom_frame, text="  DANH SÁCH VIDEO TIKTOK HÀNG ĐỢI  ", padding=3)
        queue_box.grid(row=1, column=0, sticky=tk.NSEW, pady=(0, 3))
        queue_box.columnconfigure(0, weight=1)
        queue_box.rowconfigure(0, weight=1)

        cols = ("num", "source", "market", "config", "status", "output")
        self.queue_tree = ttk.Treeview(queue_box, columns=cols, show="headings", height=4)
        headings = {
            "num": "#", "source": "Video Nguồn / Link TikTok", "market": "Thị trường",
            "config": "Config", "status": "Trạng thái", "output": "Thành phẩm"
        }
        widths = {"num": 34, "source": 260, "market": 85, "config": 90, "status": 125, "output": 150}
        for col in cols:
            self.queue_tree.heading(col, text=headings[col])
            self.queue_tree.column(col, width=widths[col], anchor=tk.W, stretch=(col in {"source", "status", "output"}))

        self.queue_tree.tag_configure("completed", foreground="#16825D")
        self.queue_tree.tag_configure("failed", foreground="#C93C37")
        self.queue_tree.tag_configure("running", background="#EAF1FF", foreground="#1749A3")
        self.queue_tree.tag_configure("pending", foreground="#172033")
        self.queue_tree.bind("<Double-1>", self._on_queue_double_click)

        q_scroll = ttk.Scrollbar(queue_box, orient=tk.VERTICAL, command=self.queue_tree.yview)
        self.queue_tree.configure(yscrollcommand=q_scroll.set)
        self.queue_tree.grid(row=0, column=0, sticky=tk.NSEW)
        q_scroll.grid(row=0, column=1, sticky=tk.NS)

        # Thanh Status & Progress Bar
        status_bar = ttk.Frame(bottom_frame)
        status_bar.grid(row=2, column=0, sticky=tk.EW, pady=(1, 2))
        status_bar.columnconfigure(1, weight=1)

        ttk.Label(status_bar, text="Tiến trình:", font=("Segoe UI Semibold", 9)).grid(row=0, column=0, sticky=tk.W, padx=(0, 4))
        self.prog_bar = ttk.Progressbar(status_bar, mode="indeterminate")
        self.prog_bar.grid(row=0, column=1, sticky=tk.EW, padx=(0, 6))

        self.status_msg_var = tk.StringVar(value="Sẵn sàng...")
        ttk.Label(status_bar, textvariable=self.status_msg_var, foreground="#2563EB", font=("Segoe UI", 9)).grid(row=0, column=2, sticky=tk.E)

        # Khung Log
        log_box = ttk.LabelFrame(bottom_frame, text="  NHẬT KÝ TIẾN TRÌNH (LOGS)  ", padding=3)
        log_box.grid(row=3, column=0, sticky=tk.NSEW)
        log_box.columnconfigure(0, weight=1)
        log_box.rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(
            log_box, height=4, font=("Consolas", 8),
            bg="#1E222D", fg="#E1E7F0", insertbackground="#FFFFFF"
        )
        self.log_text.grid(row=0, column=0, sticky=tk.NSEW)

    def log(self, msg: str):
        timestamp = time.strftime("[%H:%M:%S] ")
        self.log_text.insert(tk.END, timestamp + msg + "\n")
        self.log_text.see(tk.END)

    # --- SỰ KIỆN QUẢN LÝ CONFIG ---
    def _on_config_selected(self, event=None):
        sel = self.config_cb.get()
        if sel:
            self.config_name_entry.delete(0, tk.END)
            self.config_name_entry.insert(0, sel)
            self._load_named_config()

    def _load_named_config(self):
        name = self.config_name_entry.get().strip() or self.config_cb.get()
        saved = self.config.get("saved_configs", {})
        if name in saved:
            self.config.update(saved[name])
            self.config["selected_config"] = name
            self._sync_ui_from_config()
            self.log(f"✅ Đã nạp cấu hình '{name}'")
        else:
            self.log(f"⚠️ Chưa tìm thấy cấu hình '{name}'")

    def _save_named_config(self):
        name = self.config_name_entry.get().strip()
        if not name:
            messagebox.showwarning("Thiếu tên", "Vui lòng nhập Tên Config để lưu!", parent=self)
            return
        cfg = self._collect_config_from_ui()
        if "saved_configs" not in cfg or not isinstance(cfg["saved_configs"], dict):
            cfg["saved_configs"] = {}
        cfg["saved_configs"][name] = dict(cfg)
        cfg["selected_config"] = name
        save_tiktok_remixer_config(cfg)
        
        # Cập nhật lại dropdown
        names = list(cfg["saved_configs"].keys())
        self.config_cb["values"] = names
        self.config_cb.set(name)
        self.log(f"💾 Đã lưu cấu hình '{name}' thành công!")
        messagebox.showinfo("Thành công", f"Đã lưu cấu hình '{name}' cho TikTok Remixer!", parent=self)

    def _delete_named_config(self):
        name = self.config_name_entry.get().strip() or self.config_cb.get()
        if not name or name == "default":
            messagebox.showwarning("Không thể xóa", "Không thể xóa cấu hình mặc định 'default'!", parent=self)
            return
        if messagebox.askyesno("Xác nhận", f"Bạn có chắc muốn xóa config '{name}'?", parent=self):
            saved = self.config.get("saved_configs", {})
            if name in saved:
                del saved[name]
                self.config["saved_configs"] = saved
                self.config["selected_config"] = "default"
                save_tiktok_remixer_config(self.config)
                self.config_cb["values"] = list(saved.keys())
                self.config_cb.set("default")
                self.config_name_entry.delete(0, tk.END)
                self.config_name_entry.insert(0, "default")
                self._load_named_config()
                self.log(f"🗑 Đã xóa cấu hình '{name}'")

    # --- SỰ KIỆN NGUỒN & AI ---
    def _browse_source_file(self):
        f = filedialog.askopenfilename(
            title="Chọn Video TikTok Nguồn",
            filetypes=[("Video Files", "*.mp4 *.mov *.mkv *.webm"), ("All Files", "*.*")]
        )
        if f:
            self.url_entry.delete(0, tk.END)
            self.url_entry.insert(0, f)

    def _paste_clipboard(self):
        try:
            txt = self.clipboard_get()
            if txt:
                self.url_entry.delete(0, tk.END)
                self.url_entry.insert(0, txt.strip())
        except Exception:
            pass

    def _on_market_change(self, event=None):
        sel_label = self.market_cb.get()
        for code, prof in MARKET_PROFILES.items():
            if prof["label"] == sel_label:
                self.config["target_market"] = code
                if self.auto_voice_locale_var.get():
                    self._populate_voices_for_market(code)
                break

    def _populate_voices_for_market(self, market_code: str):
        prof = get_market_profile(market_code)
        loc = prof.get("locale", "de-DE")
        if loc.startswith("de"):
            self.voice_name_cb["values"] = ["de-DE-ConradNeural", "de-DE-KatjaNeural", "de-DE-KillianNeural", "de-DE-AmalaNeural"]
            self.voice_name_cb.set("de-DE-ConradNeural")
        elif loc.startswith("en"):
            self.voice_name_cb["values"] = ["en-US-AriaNeural", "en-US-GuyNeural", "en-US-ChristopherNeural", "en-US-JennyNeural"]
            self.voice_name_cb.set("en-US-AriaNeural")
        elif loc.startswith("vi"):
            self.voice_name_cb["values"] = ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
            self.voice_name_cb.set("vi-VN-HoaiMyNeural")
        else:
            self.voice_name_cb["values"] = [f"{loc}-StandardVoice"]
            self.voice_name_cb.set(self.voice_name_cb["values"][0])

    def _preview_voice(self):
        v_name = self.voice_name_cb.get()
        if not v_name:
            return
        threading.Thread(target=self._run_voice_test, args=(v_name,), daemon=True).start()

    def _run_voice_test(self, voice_name):
        from ai_processor import AIProcessor
        sample_text = "Hallo! Dies ist eine Teststimme für TikTok Remixer." if "de-DE" in voice_name else "Hello! This is a test voice for TikTok Remixer."
        tmp_mp3 = os.path.join("temp", "test_voice.mp3")
        os.makedirs("temp", exist_ok=True)
        AIProcessor.generate_edge_tts(sample_text, voice_name, tmp_mp3)
        if os.path.isfile(tmp_mp3):
            try:
                subprocess.Popen(["ffplay", "-nodisp", "-autoexit", tmp_mp3], creationflags=0x08000000 if os.name == "nt" else 0)
            except Exception:
                try:
                    os.startfile(tmp_mp3)
                except Exception:
                    pass

    def _open_color_studio(self):
        if self.main_app and hasattr(self.main_app, "open_capcut_color_popup"):
            self.main_app.open_capcut_color_popup(caller_config=self.config)

    def _open_title_sub_studio(self):
        if self.main_app and hasattr(self.main_app, "open_title_sub_studio_popup"):
            self.main_app.open_title_sub_studio_popup(caller_config=self.config)

    def _open_crop_studio(self):
        if self.main_app and hasattr(self.main_app, "open_crop_tool_popup"):
            self.main_app.open_crop_tool_popup(caller_config=self.config)

    def _open_output_folder(self):
        out_dir = os.path.join(os.getcwd(), "output_tiktok_remix")
        os.makedirs(out_dir, exist_ok=True)
        try:
            os.startfile(out_dir)
        except Exception:
            pass

    # --- SỰ KIỆN HÀNG ĐỢI (QUEUE) ---
    def _add_to_queue(self):
        src = self.url_entry.get().strip()
        if not src:
            messagebox.showwarning("Thiếu nguồn", "Vui lòng nhập Link TikTok hoặc chọn File video nguồn!", parent=self)
            return

        cfg_snapshot = self._collect_config_from_ui()
        job_id = f"job_{len(self.queue_items)+1:03d}_{int(time.time())}"
        job_data = {
            "id": job_id,
            "source": src,
            "market": cfg_snapshot.get("target_market", "DE"),
            "config_name": self.config_name_entry.get().strip() or "default",
            "options": dict(cfg_snapshot),
            "status": "pending",
            "output": ""
        }
        self.queue_items.append(job_data)
        self._refresh_queue_tree()
        self.log(f"➕ Đã thêm vào hàng đợi: {src}")

    def _delete_queue_item(self):
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = self.queue_tree.index(sel[0])
        if 0 <= idx < len(self.queue_items):
            del self.queue_items[idx]
            self._refresh_queue_tree()

    def _retry_queue_item(self):
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = self.queue_tree.index(sel[0])
        if 0 <= idx < len(self.queue_items):
            self.queue_items[idx]["status"] = "pending"
            self._refresh_queue_tree()

    def _move_queue_item(self, direction: int):
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = self.queue_tree.index(sel[0])
        new_idx = idx + direction
        if 0 <= new_idx < len(self.queue_items):
            item = self.queue_items.pop(idx)
            self.queue_items.insert(new_idx, item)
            self._refresh_queue_tree()
            # Select lại item vừa dời
            children = self.queue_tree.get_children()
            if 0 <= new_idx < len(children):
                self.queue_tree.selection_set(children[new_idx])

    def _on_queue_double_click(self, event=None):
        sel = self.queue_tree.selection()
        if not sel:
            return
        idx = self.queue_tree.index(sel[0])
        if 0 <= idx < len(self.queue_items):
            out_file = self.queue_items[idx].get("output", "")
            if out_file and os.path.isfile(out_file):
                try:
                    os.startfile(out_file)
                except Exception:
                    pass

    def _refresh_queue_tree(self):
        for row in self.queue_tree.get_children():
            self.queue_tree.delete(row)
        for i, item in enumerate(self.queue_items):
            st = item.get("status", "pending")
            st_text = "Chờ xử lý"
            tag = "pending"
            if st == "running":
                st_text = "⏳ Đang chạy..."
                tag = "running"
            elif st == "completed":
                st_text = "✅ Hoàn thành"
                tag = "completed"
            elif st == "failed":
                st_text = "❌ Thất bại"
                tag = "failed"

            self.queue_tree.insert(
                "", tk.END,
                values=(
                    i + 1,
                    item.get("source", ""),
                    item.get("market", "DE"),
                    item.get("config_name", "default"),
                    st_text,
                    os.path.basename(item.get("output", "")) if item.get("output") else ""
                ),
                tags=(tag,)
            )

    def _start_queue_processing(self):
        if self.is_processing_queue:
            return
        pending_jobs = [j for j in self.queue_items if j["status"] in ("pending", "failed")]
        if not pending_jobs:
            # Nếu chưa có job nào trong bảng, tự add job hiện tại từ UI
            self._add_to_queue()
            pending_jobs = [j for j in self.queue_items if j["status"] == "pending"]

        self.is_processing_queue = True
        self.stop_requested = False
        self.btn_start.config(state="disabled")
        self.prog_bar.start(10)
        self.status_msg_var.set("Đang chạy hàng đợi...")

        threading.Thread(target=self._process_queue_worker, daemon=True).start()

    def _stop_queue_processing(self):
        self.stop_requested = True
        self.log("🛑 Đã yêu cầu dừng hàng đợi sau job hiện tại.")

    def _process_queue_worker(self):
        for item in self.queue_items:
            if self.stop_requested:
                break
            if item["status"] not in ("pending", "failed"):
                continue

            item["status"] = "running"
            self.after(0, self._refresh_queue_tree)
            src = item.get("source", "")
            opts = item.get("options", {})
            self.log(f"🚀 [BẮT ĐẦU JOB] Nguồn: {src}")

            work_dir = os.path.join("temp", f"remix_{item['id']}")
            out_dir = os.path.join(os.getcwd(), "output_tiktok_remix")
            os.makedirs(work_dir, exist_ok=True)
            os.makedirs(out_dir, exist_ok=True)

            def update_cb(step, msg):
                self.after(0, lambda: self.status_msg_var.set(f"[{step.upper()}] {msg}"))
                self.after(0, lambda: self.log(f"   ➔ {msg}"))

            try:
                # 1. Tải Video
                if src.startswith("http://") or src.startswith("https://"):
                    local_vid = TikTokRemixerEngine.download_tiktok_no_watermark(src, work_dir, progress_cb=update_cb)
                else:
                    local_vid = src

                if not local_vid or not os.path.isfile(local_vid):
                    raise Exception("Không thể lấy file video nguồn.")

                # 2. Phân Tích Gemini CLI
                target_mkt = opts.get("target_market", "DE")
                gemini_plan = TikTokRemixerEngine.inspect_and_remix_with_gemini(
                    local_vid, target_market=target_mkt, progress_cb=update_cb
                )

                # 3. Tạo Giọng Đọc AI & Subtitles
                narration_info = gemini_plan.get("rewritten_narration", {})
                script_txt = narration_info.get("script_text", "")
                eng = opts.get("engine_tts", "CapCut TTS")
                voc = opts.get("voice_name", "de-DE-ConradNeural")
                spd = opts.get("tts_speed", "+0%")

                update_cb("tts", "Tạo giọng đọc AI CapCut mới...")
                audio_f, srt_f, audio_dur = TikTokRemixerEngine.generate_narration_audio_and_sub(
                    script_txt, engine_name=eng, voice_name=voc, speed_str=spd, output_dir=work_dir
                )

                # 4. Render Thành Phẩm
                final_out_name = f"TikTok_Remix_{os.path.splitext(os.path.basename(local_vid))[0]}.mp4"
                final_out_path = os.path.join(out_dir, final_out_name)

                ok = TikTokRemixerEngine.render_tiktok_remix(
                    source_video=local_vid,
                    gemini_plan=gemini_plan,
                    narration_audio=audio_f,
                    narration_srt=srt_f,
                    audio_duration=audio_dur,
                    options=opts,
                    output_path=final_out_path,
                    progress_cb=update_cb
                )

                if ok and os.path.isfile(final_out_path):
                    item["status"] = "completed"
                    item["output"] = final_out_path
                    self.log(f"🎉 [JOB XONG] Thành phẩm: {final_out_path}")
                else:
                    item["status"] = "failed"
                    self.log(f"❌ [JOB THẤT BẠI] Lỗi render.")

            except Exception as e:
                item["status"] = "failed"
                self.log(f"❌ [JOB LỖI] {e}")

            self.after(0, self._refresh_queue_tree)

        self.after(0, self._on_queue_finished)

    def _on_queue_finished(self):
        self.is_processing_queue = False
        self.prog_bar.stop()
        self.btn_start.config(state="normal")
        self.status_msg_var.set("Hoàn tất hàng đợi!")
        self.log("🏁 Đã xử lý xong toàn bộ danh sách hàng đợi!")

    def _sync_ui_from_config(self):
        self.url_entry.delete(0, tk.END)
        self.url_entry.insert(0, self.config.get("source_url_or_path", ""))
        
        m_code = self.config.get("target_market", "DE")
        self.market_cb.set(get_market_profile(m_code)["label"])
        self._populate_voices_for_market(m_code)

        self.auto_clean_core_var.set(self.config.get("auto_clean_core_crop", True))
        self.auto_blur_sub_var.set(self.config.get("auto_blur_sub_part", True))
        self.blur_strength_spin.set(self.config.get("qc_blur_strength", 75))
        self.ai_inspect_intro_outro_var.set(self.config.get("ai_inspect_intro_outro", True))

        self.keep_hook_var.set(self.config.get("keep_original_hook", True))
        self.smart_transition_var.set(self.config.get("smart_transition_cutout", True))
        self.elastic_speed_var.set(self.config.get("elastic_broll_speed", True))

        self.shuffle_broll_var.set(self.config.get("shuffle_broll", True))
        self.mirror_broll_var.set(self.config.get("mirror_broll", True))
        self.overlay_sub_on_blur_var.set(self.config.get("overlay_sub_on_blur_zone", True))

    def _collect_config_from_ui(self) -> dict:
        self.config["source_url_or_path"] = self.url_entry.get().strip()
        self.config["auto_clean_core_crop"] = bool(self.auto_clean_core_var.get())
        self.config["auto_blur_sub_part"] = bool(self.auto_blur_sub_var.get())
        try:
            self.config["qc_blur_strength"] = int(self.blur_strength_spin.get() or 75)
        except Exception:
            self.config["qc_blur_strength"] = 75
        self.config["ai_inspect_intro_outro"] = bool(self.ai_inspect_intro_outro_var.get())

        self.config["use_ai_voice"] = bool(self.use_ai_voice_var.get())
        self.config["engine_tts"] = self.tts_engine_cb.get()
        self.config["voice_name"] = self.voice_name_cb.get()
        self.config["tts_speed"] = self.tts_speed_cb.get()

        self.config["keep_original_hook"] = bool(self.keep_hook_var.get())
        self.config["smart_transition_cutout"] = bool(self.smart_transition_var.get())
        self.config["elastic_broll_speed"] = bool(self.elastic_speed_var.get())
        self.config["shuffle_broll"] = bool(self.shuffle_broll_var.get())
        self.config["mirror_broll"] = bool(self.mirror_broll_var.get())
        self.config["overlay_sub_on_blur_zone"] = bool(self.overlay_sub_on_blur_var.get())
        return self.config
