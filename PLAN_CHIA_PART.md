# KẾ HOẠCH TRIỂN KHAI KỸ THUẬT: BẢN 'CHIA PART & TINH LƯỢC VIDEO GỐC' (SMART PART SPLITTER & PRUNER)

> Tài liệu hướng dẫn kỹ thuật chi tiết dành cho IDE Agent đọc và tự động thực thi từ A đến Z.
> Ngày lập: 2026-09-17 | Trạng thái: Đã duyệt (Approved)

---

## I. NGUYÊN TẮC BẢO VỆ MÃ NGUỒN CỐT LÕI (CODE FREEZE & DECOUPLING)

1. **Đóng băng 100% Code bản Tóm Tắt**:
   - Các file: ditor_processor.py, i_processor.py, compilation_processor.py, roll_semantic.py và toàn bộ logic B-roll cũ trong main.py được **giữ nguyên vẹn 100%**, không chỉnh sửa logic cốt lõi.
2. **Xây dựng bản Chia Part thành các module .py hoàn toàn độc lập**:
   - ont_manager.py: Module font quốc tế tự động dùng chung cho cả 2 chế độ (triệt tiêu lỗi ô vuông font chữ).
   - part_splitter_config.py: Quản lý cấu hình độc lập, lưu riêng tại config_part_splitter.json.
   - part_pruner_ai.py: Module Gemini AI đọc hiểu toàn bộ video, tự đặt lại tiêu đề viral theo ngôn ngữ, lập kế hoạch lược thừa (+-20%), tìm điểm chia part cliffhanger, săn hook toàn cảnh (+-50%).
   - part_splitter_engine.py: Engine local cắt nối video sạch (OpenCV SceneMapper + Audio silence snapping), chia part, áp dụng CapCut Limiter +20dB, dán nhãn Part đa ngôn ngữ.
   - part_splitter_gui.py: Giao diện 3 Tab chuyên biệt cho Chế độ Chia Part.
3. **Điểm gắn kết duy nhất trong main.py**:
   - Thêm 1 thanh gạt chuyển chế độ (Mode Switcher) ở đầu App:
     [ 🎙️ Tóm Tắt & Voice AI ]  vs  [ ✂️ Chia Part & Tinh Lược Video Gốc ]
   - Khi chọn Tóm Tắt: Nạp giao diện cũ (đóng băng).
   - Khi chọn Chia Part: Nạp PartSplitterFrame từ part_splitter_gui.py.

---

## II. CHI TIẾT 5 COMPONENT CẦN LẬP TRÌNH

### 1. COMPONENT 1: font_manager.py (DÙNG CHUNG CẢ 2 BẢN)
- **Mục tiêu**: Tự động nhận diện dải mã Unicode của Text (Sub/Title) để gán Font chuẩn, không bao giờ bị lỗi ô vuông tofu [].
- **Hệ thống chữ viết & Font ánh xạ**:
  - cyrillic (Nga, Ukraine): Subtitle Font = 'Segoe UI Black', Banner Font = 'C:/Windows/Fonts/seguibl.ttf' hoặc 'ariblk.ttf'.
  - rabic (Pakistan/Urdu, Ả Rập): Subtitle Font = 'Tahoma', Banner Font = 'C:/Windows/Fonts/tahoma.ttf'.
  - latin_extended (Đức, Pháp, Tây Ban Nha, Việt Nam): Subtitle Font = 'Impact' / 'Segoe UI Black', Banner Font = 'impact.ttf' / 'seguibl.ttf'.
- **Hàm cần viết**:
  - detect_script(text: str) -> str
  - FontManager.get_banner_font(text: str, size: int) -> ImageFont
  - FontManager.get_subtitle_font_name(sample_text: str) -> str

### 2. COMPONENT 2: part_splitter_config.py
- **Mục tiêu**: Quản lý cấu hình độc lập, lưu tại config_part_splitter.json.
- **Dữ liệu cấu hình chuẩn**:
  - part_count: int = 4
  - min_part_duration_sec: float = 60.0
  - part_tolerance: float = 0.50 (dao động +-50%)
  - prune_enabled: bool = True
  - prune_mode: str = 'percent' ('percent' hoặc 'minutes')
  - prune_percent: float = 20.0
  - prune_minutes: float = 15.0
  - prune_tolerance: float = 0.20 (dao động +-20%)
  - hook_strategy: str = 'individual' ('shared' hoặc 'individual')
  - hook_target_sec: float = 6.0
  - hook_tolerance: float = 0.50 (dao động +-50% -> 3s ~ 9s)
  - uto_regenerate_title: bool = True
  - part_label_prefix: str = 'Part' (người dùng có thể gõ 'Teil', 'Partie', 'Часть'...)
  - source_speed: float = 1.05
  - pply_capcut_limiter: bool = True
  - pply_subtle_zoom: bool = True
  - 
andom_mirror: bool = False
  - gemini_model: str = 'gemini-2.5-flash'
- **Hàm cần viết**:
  - load_part_config() -> dict
  - save_part_config(cfg: dict)

### 3. COMPONENT 3: part_pruner_ai.py
- **Mục tiêu**: Kết nối Gemini (Antigravity OAuth / Google local hoặc Gemini API Key) để đọc hiểu toàn bộ video qua Transcript Whisper (	ranscript_sub.srt) và Video Preview (1fps).
- **Nhiệm vụ AI trả về JSON**:
  1. master_title: Tiêu đề viral tổng thể theo ngôn ngữ gốc của video.
  2. part_titles: Danh sách tiêu đề cho từng Part theo ngôn ngữ gốc.
  3. prune_plan: Danh sách đoạn thừa cần cắt [{'start': float, 'end': float, 'reason': str}].
     - Áp dụng hạn ngạch co giãn +-20% (ví dụ 20 phút -> cho phép dao động 16 ~ 24 phút).
  4. part_splits: Danh sách N-1 điểm cắt chia part rơi vào bẫy tò mò (Cliffhanger).
  5. hooks:
     - global_hook: 1 cảnh hay nhất video (dài +-50% so với thời lượng hook GUI, ví dụ 6s: 3s ~ 9s).
     - individual_hooks: Cảnh cao trào nhất của từng Part, cắt cụt lủn ngay trước khi kết quả xảy ra.

### 4. COMPONENT 4: part_splitter_engine.py
- **Mục tiêu**: Bộ máy xử lý video local:
  1. 
efine_cuts_with_opencv_and_audio(ai_cuts, scene_map, srt_cues):
     - Soát lại các mốc của AI trong bán kính +-0.5s:
     - Hít vào điểm chuyển cảnh (Scene Cut) của scene_mapper.
     - Hít vào khoảng lặng (Silence Gap) giữa 2 câu thoại trong SRT/WAV.
     - Triệt tiêu 100% lỗi cụt tiếng hoặc giật hình.
  2. prune_and_build_cleaned_source(source_video, drop_ranges, output_clean):
     - Dùng FFmpeg stream concat nối mượt các đoạn giữ lại thành video gốc sạch.
  3. split_into_parts(clean_video, cut_points, output_dir, part_label_prefix, part_titles):
     - Cắt từng Part đảm bảo > 1.0 phút và dải +-50%.
  4. pply_part_hook_and_postprocessing(...):
     - Ráp Hook cụt lủn vào đầu Part.
     - Tăng tốc 1.05x - 1.2x (tempo giữ nguyên cao độ giọng gốc).
     - Bộ lọc CapCut Limiter (+20dB, compressor, true peak -0.5dB).
     - Vẽ Banner Header Part: {part_label_prefix} {index}: {part_title} với font từ ont_manager.

### 5. COMPONENT 5: part_splitter_gui.py
- **Mục tiêu**: Giao diện 3 Tab độc lập kế thừa 	tk.Frame:
  - **Tab 1: Nguồn & Hàng Đợi**:
    - Nhập YouTube URL / Playlist / Local file / Folder.
    - Hook do Gemini chọn: Radio Hook Chung vs Hook Riêng; Ô nhập thời lượng Hook +-50%.
    - Bảng hàng đợi Batch riêng.
  - **Tab 2: AI Gemini & Chiến Lược Chia Part**:
    - Quản lý tài khoản Gemini (Local / API Key).
    - Checkbox AI tự động đặt lại tiêu đề viral.
    - Cấu hình Lược: Chọn theo % hoặc Số phút (+-20%).
    - Cấu hình Chia Part: Số Part (>1 phút, +-50%).
  - **Tab 3: Hậu Kỳ Video Gốc**:
    - Ô điền Tiền tố Part: (mặc định: Part).
    - Tốc độ video (1.05x - 1.2x).
    - Checkbox CapCut Limiter +20dB.
    - Checkbox Subtle Zoom & Lật hình.
    - Nút bấm: 🚀 BẮT ĐẦU CHIA PART & XUẤT VIDEO.

---

## III. TÍCH HỢP VÀO main.py

- Thêm Mode Switcher ở đỉnh cửa sổ:
  `python
  # Mode 1: self.summarizer_container_frame
  # Mode 2: self.part_splitter_frame = PartSplitterFrame(self.root, on_status=self.log_fn)
  `
- Khi bấm chuyển chế độ: pack_forget() frame này và pack() frame kia. Không can thiệp vào logic cũ.

---

## IV. LỘ TRÌNH THỰC THI (EXECUTION STEPS FOR IDE)

1. **Bước 1**: Tạo file ont_manager.py và cập nhật nhẹ ditor_processor.py để dùng font quốc tế.
2. **Bước 2**: Tạo file part_splitter_config.py và khởi tạo config_part_splitter.json.
3. **Bước 3**: Tạo file part_pruner_ai.py tích hợp Gemini client & prompt phân tích toàn diện.
4. **Bước 4**: Tạo file part_splitter_engine.py xử lý snapping OpenCV, cắt nối video và hậu kỳ CapCut.
5. **Bước 5**: Tạo file part_splitter_gui.py chứa 3 Tab chuyên biệt.
6. **Bước 6**: Nhúng PartSplitterFrame vào main.py qua Mode Switcher.
7. **Bước 7**: Chạy lệnh kiểm tra cú pháp và unit test:
   python -m py_compile font_manager.py part_splitter_config.py part_pruner_ai.py part_splitter_engine.py part_splitter_gui.py main.py
