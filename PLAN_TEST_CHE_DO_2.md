# KẾ HOẠCH KIỂM THỬ THỰC CHIẾN CHẾ ĐỘ 2 (CHẾ ĐỘ CHIA PART)
> **Mục tiêu:** Kiểm chứng toàn diện pipeline xử lý từ AI Đạo Diễn 3-Pass đến Engine Render đóng gói video đối thủ 9:16 trên video thực tế, đối chiếu 1:1 với video đối thủ mẫu.

---

## I. MÔI TRƯỜNG VÀ DỮ LIỆU ĐẦU VÀO (TEST BENCH)

| Thành phần | Thông tin chi tiết |
| :--- | :--- |
| **Video nguồn test** | `C:\Users\DungAnh\Downloads\hitomi_downloader_GUI\phantich\[JACK TV] Loyalty Test *BACKFIRES* Girlfriend Gets Exposed Instead ?!?? (TYDkO6JzeJA).mp4` (Thời lượng: ~37m 44s) |
| **Video đối thủ mẫu để so sánh** | 6 file Part mẫu (`7691125849747180830.mp4` -> `7693814018334559502.mp4`) tại cùng thư mục |
| **Phiên bản Tool** | `v1.3.103` (Đã phát hành trên GitHub) |
| **Cấu hình AI** | Google Antigravity CLI / Gemini 3.8 Flash |
| **Thư mục xuất test** | `E:\Video_summarizer_Pro\output\test_che_do_2` |

---

## II. DANH MỤC CÁC HẠNG MỤC KIỂM THỬ (TEST MATRIX)

### 1. Test Giao diện (GUI & Config Persistence)
- [ ] Mở ứng dụng `Video Summarizer Pro`, chọn **Chế độ 2 (Chia Part)**.
- [ ] **Tab 1 (Nguồn):** Kiểm tra thao tác chọn file video `[JACK TV]...mp4`.
- [ ] **Tab 2 (AI & Chiến lược):**
  - Chế độ biên tập chọn: `🔘 Dựng Tinh Hoa Đối Thủ (3-Pass AI Director)`.
  - Số Part cần chia: `6` (hoặc test nhanh với `2` part để tiết kiệm thời gian render).
  - Thời lượng mỗi Part: `90.0 giây`.
- [ ] **Tab 3 (Hậu kỳ):**
  - Tốc độ: `1.0x`.
  - CapCut Limiter: `☑ Bật`.
  - Scale ngang/dọc: `100% / 100%`.
  - Kiểm tra nút `🔤 Title & Sub Studio`: Bật mở xem popup live preview có hiển thị đúng layout hay không.
- [ ] Bấm **"＋ Thêm vào Hàng Đợi"**: Kiểm tra item xuất hiện trên bảng Treeview đầy đủ thông số.

---

### 2. Test Bộ Não AI 3-Pass (`part_pruner_ai.py`)
- [ ] **Pass 1 (Tổng Đạo Diễn Thích Ứng Mở):**
  - AI đọc kịch bản/video, tự động nhận diện ngôn ngữ & thể loại nội dung.
  - Phân bổ câu chuyện thành $N$ hồi gay cấn (Chapter 1 -> $N$).
  - Giữ lại Tiêu đề gốc YouTube sạch sẽ.
- [ ] **Pass 2 (Trưởng Phòng Dựng - Micro-Cuts & Hook):**
  - Kiểm tra xem AI có nhấc đúng 2-3s cảnh sốc nhất đưa lên giây `00:00` làm Hook teaser không.
  - Kiểm tra các shot cắt: Mỗi shot dao động 2.0s - 8.0s, kết thúc đúng ranh giới câu nói (không bị cụt từ).
  - Kiểm tra điểm kết thúc Part: Có đúng điểm lửng lơ gây tò mò (Cliffhanger) không.
- [ ] **Pass 3 (Kiểm Duyệt QC):**
  - Rà soát tính hợp lệ của JSON cutlist, các mốc thời gian không bị đảo ngược hoặc đè nhau.

---

### 3. Test Engine Render & Ghép Nối (`part_splitter_engine.py`)
- [ ] **Micro-Cuts Concat & Audio Crossfade:**
  - FFmpeg cắt các đoạn shot và ghép lại bằng filter complex.
  - Điểm chuyển cảnh âm thanh: Nghe kỹ xem có bị tiếng "bụp/pop/click" không (nhờ 10ms micro-crossfade).
  - Punch-in Zoom: Các shot thoại cận cảnh có được phóng to 115% nhẹ nhàng không.
- [ ] **Đóng Gói Visual 9:16 Chuẩn Đối Thủ:**
  - Video thành phẩm xuất đúng độ phân giải dọc `1080x1920`.
  - Nền mờ Gaussian Blur ở trên và dưới mịn đẹp.
  - Top Card: Thẻ trắng bo góc in tiêu đề gốc đặt ở phần trên, chữ đen sắc nét.
  - Bottom Badge: Nút trắng viên thuốc in `PART X` đặt ở phần dưới.
  - Âm lượng thoại: Chuẩn hóa -14 LUFS, nghe trên điện thoại to rõ, đanh chắc, không rè.

---

### 4. Đối Chiếu 1:1 Với Video Đối Thủ (Benchmark)
- [ ] **Độ dài Part:** Video tool render ra (~75s - 95s) so với video đối thủ (~94s).
- [ ] **Nhịp dựng:** Nhịp chuyển cảnh có sôi động, dồn dập (trung bình 3-5s đổi góc máy) giống video mẫu không.
- [ ] **Tốc độ render:** Đo thời gian xử lý toàn bộ quy trình trên card đồ họa (NVIDIA NVENC / CPU).

---

## III. CÁCH THỨC THỰC THI TEST (2 PHƯƠNG ÁN)

### Phương án A: Test Tự Động Bằng Script CLI (Khuyên Dùng Để Debug Chi Tiết)
Chạy script kiểm thử độc lập gọi trực tiếp Engine và AI để render thử **Part 1** (~90s), in toàn bộ log từng Pass ra màn hình console để kiểm tra chi tiết.

### Phương án B: Test Trực Tiếp Trên Giao Diện GUI
Khởi chạy `main.py`, thao tác bấm nút như người dùng cuối để kiểm tra độ mượt của giao diện và luồng xử lý thực tế.
