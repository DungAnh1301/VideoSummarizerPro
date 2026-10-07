# MASTER PLAN: NÂNG CẤP TOÀN DIỆN CHẾ ĐỘ 2 (CHIA PART)
## DỰ ÁN: `E:\Video_summarizer_Pro`
### MỤC TIÊU: CHUYỂN ĐỔI SANG ĐẠO DIỄN AI THÍCH ỨNG MỞ (CHUẨN ĐỐI THỦ TRIỆU VIEW & LÁCH 100% BẢN QUYỀN TIKTOK)

---

## 1. CAM KẾT PHẠM VI & NGUYÊN TẮC CÔ LẬP (ZERO SIDE-EFFECT)

> [!IMPORTANT]
> **ĐÓNG BĂNG 100% TOÀN BỘ HỆ THỐNG CÒN LẠI:**  
> - **Chế độ 1 (Tóm Tắt & Voice AI):** Giữ nguyên vẹn 100% các file `config.json`, `editor_processor.py`, `ai_processor.py`, `compilation_processor.py`.  
> - **Chế độ 3 (TikTok Remixer):** Giữ nguyên vẹn 100% các file `tiktok_remixer_config.json`, `tiktok_remixer_engine.py`, `tiktok_remixer_gui.py`.  
> - **File Main (`main.py`):** Giữ nguyên khung điều hướng, không thay đổi logic tổng.  
> 
> **CHỈ THỰC HIỆN NÂNG CẤP DUY NHẤT TRÊN 4 FILE CỦA CHẾ ĐỘ 2:**  
> 1. `part_splitter_config.py` (Cấu hình riêng biệt trong `config_part_splitter.json`)  
> 2. `part_pruner_ai.py` (Bộ não 3-Pass AI Đạo Diễn Thích Ứng Mở)  
> 3. `part_splitter_engine.py` (Engine thi hành cắt nối micro-cuts & đóng gói thị giác 1:1 đối thủ)  
> 4. `part_splitter_gui.py` (Giao diện điều khiển tinh gọn, loại bỏ các nút thừa cũ)

---

## 2. BẢNG TỔNG KẾT: THAY ĐỔI TRÊN GIAO DIỆN GUI (`part_splitter_gui.py`)

| Tab Giao Diện | Những gì XÓA / LƯỢC BỎ | Những gì THAY ĐỔI & THÊM MỚI |
| :--- | :--- | :--- |
| **Tab 1: Nguồn** | - Ẩn/Vô hiệu hóa việc nhập tay mốc hook (`hook_time`) và kiểu hook thủ công. | - Giữ nguyên ô chọn link YouTube, File Local và Folder xuất.<br>- AI Trưởng phòng dựng sẽ tự động quét Part để nhặt Hook đỉnh cao nhất (2-3s) đưa lên giây 0. |
| **Tab 2: AI & Chia Part** *(Trọng tâm)* | - **XÓA BỎ HOÀN TOÀN** cụm radio "Lược theo %" và "Lược theo số phút" cũ (nguyên nhân dính gậy).<br>- Xóa nhãn tính toán số giây cắt thô cũ. | - **THAY MỚI bằng Khung: `🎬 CHẾ ĐỘ BIÊN TẬP`**:<br>  + 🔘 **Dựng Tinh Hoa Đối Thủ (3-Pass AI Director)** ⭐ *(Mặc định)*<br>  + ⚪ Cắt Khúc Cơ Bản (Classic Linear - Kiểu cũ dự phòng)<br>- Khi chọn Dựng Tinh Hoa:<br>  + Ô nhập: *Số Part cần chia* (Mặc định: 6 Parts).<br>  + Ô nhập: *Thời lượng mỗi Part* (Mặc định: 90s, biên độ 75s - 100s).<br>- Tự động ưu tiên mô hình `gemini-3.8-flash-high` qua Antigravity CLI (`agy.exe`). |
| **Tab 3: Hậu kỳ Video** | - Bỏ ép tăng tốc 1.05x mặc định. | - **Đổi Tốc độ mặc định về `1.0x` (Chuẩn gốc)** để giọng nói tự nhiên, không the thé.<br>- **THÊM MỚI Checkbox Đóng Gói Đối Thủ (Competitor Layout)**:<br>  + `[x] Canvas 9:16 + Nền Gaussian Blur trên/dưới`<br>  + `[x] In Top Card: Thẻ trắng bo góc lấy Title gốc YouTube`<br>  + `[x] In Bottom Badge: Nút trắng viên thuốc 'PART X'`<br>  + `[x] Tăng âm lượng thoại chuẩn -14 LUFS (đanh rõ)` |
| **Hộp Log Tiến Độ** | - Bỏ thông báo tiến độ chung chung một dòng. | - **Hiển thị Realtime 3 Đợt làm việc của AI**:<br>  + `[Pass 1/3] Tổng Đạo Diễn: Đọc vị thể loại & chia N chặng cốt truyện...`<br>  + `[Pass 2/3] Trưởng Phòng Dựng: Lọc Hook, Thoại, góc máy Part X...`<br>  + `[Pass 3/3] Kiểm Duyệt QC: Rà soát nghĩa câu thoại, chống nuốt chữ...`<br>  + `[Render] Engine: Cắt nối micro-cuts & dán Top Card/Bottom Badge...` |

---

## 3. THIẾT KẾ BỘ NÃO AI 3 ĐỢT TRONG `part_pruner_ai.py`

Hệ thống kết nối trực tiếp với Google Antigravity CLI (`agy.exe`) qua `AntigravityProcessor` để chạy chu trình 3 lượt (3 Passes):

### 🧠 ĐỢT 1: TỔNG ĐẠO DIỄN THÍCH ỨNG MỞ (Adaptive Showrunner)
- **Đầu vào:** Video gốc (proxy 360p / frames) + Target Part Count ($N$).
- **Nguyên lý Mở (Open-Ended Analysis):** AI không bị ép vào danh mục khép kín. AI tự do phân tích video trên **3 Trục Bản Chất**:
  1. *Thị giác vs Lời thoại (Visual vs Verbal Ratio)*
  2. *Nhịp điệu cảm xúc (Emotional Energy Curve)*
  3. *Mô thức cốt truyện tự thân (Native Story Arc)*
- **Đầu ra:**
  - Trích xuất 100% Tiêu đề gốc YouTube chuẩn (`master_title`).
  - Bản **Chỉ Đạo Nghệ Thuật (Director's Brief)** đo ni đóng giày cho video (ví dụ: *"Video du lịch/khám phá: Ưu tiên giữ B-roll phong cảnh hùng vĩ, nén lời nói giới thiệu, nhịp cắt cinematic..."* hoặc *"Drama đối thoại: Cắt cặp câu hỏi-đáp, nhịp dồn dập, bắt chuyển góc máy..."*).
  - Phân chia video thành $N$ chặng bối cảnh/nhiệm vụ lớn cân đối.

### ✂️ ĐỢT 2: TRƯỞNG PHÒNG DỰNG (Pacing & Continuity Editor)
- **Đầu vào:** Từng chặng thời gian của mỗi Part + Bản Chỉ Đạo Nghệ Thuật từ Đợt 1.
- **Nhiệm vụ thi hành:**
  - **Hook 2-3s:** Quét tìm khoảnh khắc kịch tính/đẹp nhất ở giữa hoặc cuối Part đưa lên giây 00:00.
  - **Lọc tinh hoa 75% - 85% râu ria:** Chỉ giữ lại các shot then chốt mang thông tin hoặc hành động đắt giá.
  - **Quy tắc độ dài Shot:** Dao động từ **2.0s đến 8.0s tùy theo trọn vẹn câu nói**, cắt đúng theo ranh giới từ vựng và nhịp thở, không bao giờ cắt cụt chữ.
  - **Chống giật hình (Visual Continuity):**
    + Video đối thoại: Neo vết cắt vào thời điểm camera đổi góc máy hoặc đổi người nói.
    + Video 1 người kể chuyện (Vlog/Talking head): Tự động chỉ thị **Punch-in Zoom (115%)** để đổi cỡ cảnh từ Trung cảnh sang Cận cảnh.
  - **Cliffhanger:** Khóa điểm dừng tò mò/nghẹt thở ở cuối Part.
  - **Khống chế thời lượng:** Vừa khít **75s – 95s** mỗi Part.

### 🔍 ĐỢT 3: KIỂM DUYỆT CHẤT LƯỢNG & LỜI THOẠI (Quality Auditor / QC)
- **Nhiệm vụ:**
  - Đọc lại toàn bộ kịch bản ghép của từng Part.
  - Rà soát: *"Các câu thoại nối vào nhau có bị mất từ, què ngữ pháp không? Khán giả nghe lần đầu có hiểu mạch chuyện không?"*
  - Tự động bù trừ timecode $\pm 0.5s$ nếu phát hiện nguy cơ bị nuốt âm hoặc cắt dở hơi thở.
  - Khóa và xuất file JSON Cutlist hoàn thiện cho Engine render.

---

## 4. THIẾT KẾ ENGINE THI HÀNH TRONG `part_splitter_engine.py`

Engine thi hành Python + FFmpeg sẽ làm việc cơ học chính xác theo lệnh của AI:

1. **Hàm `build_condensed_part_video()` (Cắt nối Micro-Cuts):**
   - Đọc danh sách `cuts` của Part từ JSON.
   - Cắt các đoạn nhỏ 2-8s và ghép lại bằng `concat filter`.
   - Áp dụng **Micro-Crossfade Audio (10ms)** tại các điểm nối để triệt tiêu 100% tiếng "pop/click" âm thanh.
   - Áp dụng Punch-in Zoom (115%) tự động cho các shot được chỉ định.

2. **Hàm `apply_competitor_visual_packaging()` (Đóng gói chuẩn 1:1 Đối thủ):**
   - **Canvas dọc 9:16 (1080x1920).**
   - **Lớp nền:** Phóng to video chính lấp đầy 1080x1920, phủ hiệu ứng Gaussian Blur (`gblur=sigma=20`).
   - **Lớp giữa:** Video chính giữ nguyên tỉ lệ 16:9 sắc nét đặt chính giữa màn hình.
   - **Lớp trên (Top Card):** Render thẻ trắng bo góc chứa Tiêu đề gốc YouTube + Emoji ở tọa độ $Y \approx 140$.
   - **Lớp dưới (Bottom Badge):** Render nút trắng hình viên thuốc in chữ `PART X` ở tọa độ $Y \approx 1700$.
   - **Âm thanh:** Áp dụng Audio Normalization đưa âm lượng thoại về chuẩn **-14 LUFS** (đanh rõ, sống động trên điện thoại).

---

## 5. CẤU HÌNH ĐỘC LẬP TRONG `part_splitter_config.py`

Bổ sung các tham số mới vào `DEFAULT_PART_CONFIG` (lưu tại `config_part_splitter.json`):
```python
# Cấu hình Chế độ Biên tập Mới
"editing_mode": "viral_condensed",          # 'viral_condensed' (Chế độ đối thủ mới) hoặc 'classic_linear' (Kiểu cũ)
"target_part_duration": 90.0,               # Thời lượng mục tiêu mỗi Part (75s - 95s)
"min_part_duration": 70.0,                  # Thời lượng tối thiểu
"max_part_duration": 110.0,                 # Thời lượng tối đa
"keep_original_speed": True,                # Mặc định giữ tốc độ 1.0x chuẩn gốc
"enable_competitor_layout": True,           # Bật layout chuẩn đối thủ
"competitor_top_card": True,                # In title gốc trên Card trắng bo góc
"competitor_bottom_badge": True,            # In nút viên thuốc PART X
```

---

## 6. LỘ TRÌNH TRIỂN KHAI CODE CHI TIẾT (5 BƯỚC)

```
[BƯỚC 1: Config] ──> [BƯỚC 2: AI 3 Pass] ──> [BƯỚC 3: Engine Dựng] ──> [BƯỚC 4: GUI] ──> [BƯỚC 5: Test Thực Tế]
```

- **Bước 1:** Cập nhật `part_splitter_config.py` (Bổ sung các tham số cấu hình độc lập mới).
- **Bước 2:** Cập nhật `part_pruner_ai.py` (Viết bộ não 3-Pass AI gọi qua `AntigravityProcessor` và `agy.exe`).
- **Bước 3:** Cập nhật `part_splitter_engine.py` (Viết engine cắt nối micro-cuts, audio crossfade và hàm render Top Card + Bottom Badge chuẩn đối thủ).
- **Bước 4:** Cập nhật `part_splitter_gui.py` (Xóa cụm % cũ, thay bằng tùy chọn Dựng Tinh Hoa Đối Thủ, gắn log tiến độ 3 đợt).
- **Bước 5:** Thử nghiệm thực tế trực tiếp trên video gốc `[JACK TV]...mp4` trong thư mục `phantich` để so khớp trực tiếp kết quả với 6 part của đối thủ.
