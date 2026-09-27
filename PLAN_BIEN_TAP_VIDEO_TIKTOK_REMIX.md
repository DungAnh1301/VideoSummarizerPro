# 📱 MASTER PLAN: BIÊN TẬP & RE-MIX VIDEO TIKTOK ĐỐI THỦ (CHẾ ĐỘ 3 - TIKTOK SHORT REMIXER)

> **Tài liệu thiết kế kiến trúc kỹ thuật & giải thuật toàn diện 100%**  
> Dành cho việc phát triển và chuyển giao công nghệ cho hệ thống dựng video ngắn tự động độc quyền.

---

## 📑 MỤC LỤC
1. [Tầm Nhìn & Phân Kỳ Phát Triển (2-Phase Roadmap)](#1-tầm-nhìn--phân-kỳ-phát-triển)
2. [Chiến Lược Xử Lý Khung Hình & Tẩy Dấu Vết Đối Thủ (Ảnh 1 ➔ Ảnh 2 ➔ Thành Phẩm)](#2-chiến-lược-xử-lý-khung-hình--tẩy-dấu-vết-đối-thủ)
3. [Bộ Não Gemini CLI Đa Phương Thức (1 Lần Gọi Duy Nhất)](#3-bộ-não-gemini-cli-đa-phương-thức)
4. [Thuật Toán Cắt Chuyển Cảnh Thông Minh (Smart Transition Detection)](#4-thuật-toán-cắt-chuyển-cảnh-thông-minh)
5. [Giải Thuật Điều Tốc B-Roll Đàn Hồi (Khớp Khít Âm Thanh & Hình Ảnh)](#5-giải-thuật-điều-tốc-b-roll-đàn-hồi)
6. [Kỹ Thuật Đè Subtitle Mới Lên Đúng Vùng Sub Cũ Đã Blur](#6-kỹ-thuật-đè-subtitle-mới-lên-đúng-vùng-sub-cũ-đã-blur)
7. [Phân Hệ Tải Video TikTok Không Watermark (yt-dlp Engine)](#7-phân-hệ-tải-video-tiktok-không-watermark)
8. [Thiết Kế Giao Diện GUI Tinh Gọn (Giữ Lại, Lược Bỏ & Thêm Mới)](#8-thiết-kế-giao-diện-gui-tinh-gọn)
9. [Tổng Hợp Quy Trình Xử Lý Trong 1 Lệnh FFmpeg Master](#9-tổng-hợp-quy-trình-xử-lý-trong-1-lệnh-ffmpeg-master)

---

## 1. TẦM NHÌN & PHÂN KỲ PHÁT TRIỂN

```mermaid
flowchart LR
    subgraph Phase_1 ["🎯 PHASE 1: LÀM TRƯỚC & HOÀN THIỆN NGAY (CORE)"]
        P1_IN["Video TikTok Đối Thủ (Drama / Phim / Kể chuyện)"] --> P1_H["Hook Gốc (Hình + Tiếng kịch tính)"]
        P1_H --> P1_BODY["Kịch Bản Viết Lại + Giọng Đọc AI CapCut/Edge-TTS Mới"]
        P1_BODY --> P1_OUT["Thành Phẩm 9:16 Chuẩn Màu 8K + Title Banner/Sub Mới"]
    end

    subgraph Phase_2 ["🚀 PHASE 2: MỞ RỘNG SAU KHI PHASE 1 CHẠY MƯỢT"]
        P2_IN["Video TikTok Hài / ASMR / Biến Hình / Trend"] --> P2_AUDIO["Giữ Âm Thanh Tự Nhiên Hoặc Lồng Nhạc Trend TikTok"]
        P2_AUDIO --> P2_REMIX["Remix Kho Cảnh Sạch + Đổi Hiệu Ứng/Màu/Tốc Độ"]
    end

    Phase_1 -.->|Nghiệm thu thành công 100%| Phase_2
```

---

## 2. CHIẾN LƯỢC XỬ LÝ KHUNG HÌNH & TẨY DẤU VẾT ĐỐI THỦ

```
[1. VIDEO GỐC ĐỐI THỦ (Ảnh 1 & 3)]         [2. LÕI VIDEO SẠCH (Ảnh 2)]          [3. THÀNH PHẨM 9:16 MỚI 100%]
┌──────────────────────────────┐              ┌──────────────────────────┐          ┌──────────────────────────┐
│ ░░ NỀN BLUR / TEXTURE TRÊN ░░│              │                          │          │ ┌──────────────────────┐ │
│ ┌──────────────────────────┐ │ ─── CẮT BỎ   │                          │          │ │ TITLE 2 DÒNG MỚI     │ │
│ │ TITLE IN TRÊN ĐẦU        │ │ ─── PHẦN NÀY │                          │          │ └──────────────────────┘ │
│ └──────────────────────────┘ │              │   LÕI HÌNH ẢNH NHÂN VẬT  │          │                          │
├──────────────────────────────┤              │     (KHÔNG CÒN TITLE)    │ ───────► │   LÕI VIDEO ĐÃ CẮT SẠCH  │
│                              │              │                          │          │   (Scale + Lật gương)    │
│    HÌNH ẢNH NHÂN VẬT CHÍNH   │ ─── GIỮ LẠI  │                          │          │                          │
│                              │              ├──────────────────────────┤          ├──────────────────────────┤
│                              │              │ ░░ [BLUR SUB: "OK"] ░░░░ │          │ █ SUBTIKTOK SLIM MỚI   █ │
├──────────────────────────────┤              │ ░░ [BLUR: "Part 2"] ░░░░ │          │ █ ĐÈ KÍN LÊN VẾT MỜ    █ │
│    [SUB CŨ: "THE COUNT OF"]  │ ─── BÔI MỜ   └──────────────────────────┘          │ └──────────────────────┘ │
│        [BADGE: "Part 2"]     │ ─── BÔI MỜ                                         └──────────────────────────┘
│ ░░ NỀN BLUR / TIKTOK UI DƯỚI ░│ ─── CẮT BỎ
└──────────────────────────────┘
```

1. **Cắt bỏ dải 2 đầu (Bỏ Title cũ):**
   - Áp dụng FFmpeg Crop cắt phăng toàn bộ phần nền mờ/texture ở trên chứa Title in chết của đối thủ.
   - Thu được **Lõi Video Sạch 100%** sắc nét như **Ảnh 2**.
2. **Bôi mờ dải Sub cũ & Badge Part cũ:**
   - Dùng sub-stream `boxblur=12:3` khoanh đúng dải đáy xuất hiện chữ Sub cũ (`OK`, `THE COUNT OF`) và badge `Part 2`.
3. **Đè Sub mới lên đúng vết mờ:**
   - Đặt Subtitle mới của mình đè chính xác lên dải vừa làm mờ, che phủ $100\%$ dấu vết cũ.

---

## 3. BỘ NÃO GEMINI CLI ĐA PHƯƠNG THỨC (1 LẦN GỌI DUY NHẤT)

Gửi trực tiếp video gốc (1–2 phút) vào Gemini CLI để AI "vừa làm đạo diễn hình ảnh vừa làm biên kịch" trong **1 lệnh gọi duy nhất (~15 giây)**:

```json
{
  "layout_geometry": {
    "has_top_title_or_blur": true,
    "core_crop_normalized": {
      "ymin": 0.16,
      "ymax": 0.84,
      "note": "Cắt bỏ 16% đỉnh trên chứa Title và 16% đáy dưới chứa UI, lấy lõi giữa như Ảnh 2"
    },
    "sub_blur_normalized": {
      "ymin": 0.74,
      "ymax": 0.86,
      "xmin": 0.10,
      "xmax": 0.90,
      "note": "Vùng chữ sub OK/The Count Of và badge Part 2 cần bôi mờ"
    }
  },
  "intro_outro_inspection": {
    "has_intro_tiktok_logo": false,
    "trim_start_sec": 0.0,
    "has_outro_tiktok_logo": true,
    "trim_end_sec": 0.6,
    "note": "Đoạn đầu là câu nói của Hook nên giữ nguyên 0.0s; đoạn cuối có logo TikTok giật nên cắt 0.6s"
  },
  "hook_analysis": {
    "has_hook": true,
    "start_sec": 0.0,
    "end_sec": 3.6,
    "keep_original_audio": true,
    "reason": "Biểu cảm bất ngờ của nhân vật nữ rất kịch tính"
  },
  "scenes": [
    {
      "id": "S1",
      "raw_range": [3.6, 8.2],
      "transition_type": "none",
      "cutout_sec": 0.0,
      "clean_range": [3.6, 8.2]
    },
    {
      "id": "S2",
      "raw_range": [8.2, 13.5],
      "transition_type": "white_flash",
      "cutout_sec": 0.18,
      "clean_range": [8.38, 13.5]
    },
    {
      "id": "S3",
      "raw_range": [13.5, 19.0],
      "transition_type": "zoom_glitch",
      "cutout_sec": 0.22,
      "clean_range": [13.5, 18.78]
    }
  ],
  "rewritten_narration": {
    "language": "de-DE",
    "title_line1": "DIE REAKTION WAR",
    "title_line2": "VÖLLIG UNERWARTET",
    "script_text": "Niemand hatte mit diesem Moment gerechnet, doch als der Druck plötzlich nachließ..."
  }
}
```

---

## 4. THUẬT TOÁN CẮT CHUYỂN CẢNH THÔNG MINH (SMART TRANSITION DETECTION)

Không cắt cứng cố định $0.5s$ để tránh làm cụt các cảnh ngắn ($1.5s - 2.0s$). Gemini CLI phân loại chính xác từng điểm nối:

| Loại Chuyển Cảnh | Nhận Diện Của AI | Cắt Bỏ (`cutout_sec`) | Xử Lý Thực Tế |
|:---|:---|:---:|:---|
| **Hard Cut (Cắt thẳng)** | Chuyển góc máy tức thì, không có hiệu ứng. | **`0.00s`** | **Giữ nguyên 100% độ dài cảnh**, không cắt bất kỳ mili-giây nào. |
| **White Flash (Chớp trắng)** | Màn hình sáng lóa trong 2-4 frame. | **`0.15s`** | Cắt bỏ đúng $0.15s$ vệt sáng tại điểm nối. |
| **Zoom Blur / Glitch** | Rung giật méo hình chuyển cảnh. | **`0.20s - 0.25s`** | Cắt bỏ vừa khít đoạn méo hình, giữ trọn chuyển động đẹp. |
| **Fade to Black (Mờ đen)** | Màn hình tối dần rồi sáng lại. | **`0.25s`** | Cắt bỏ phần tối đen chuyển tiếp. |

---

## 5. GIẢI THUẬT ĐIỀU TỐC B-ROLL ĐÀN HỒI (ELASTIC B-ROLL SPEED MATCHING)

* **Bỏ tăng tốc toàn cục:** Giữ nguyên tốc độ chuẩn $1.0x$ của video.

```mermaid
flowchart TD
    A["So Sánh: Tổng Cảnh Sạch (T_video) vs Giọng Đọc AI (T_audio)"] --> B{"T_video vs T_audio"}
    
    B -->|T_video < T_audio: THIẾU HÌNH| C["1. Giảm tốc nhẹ 0.95x trên các B-Roll lẻ: 1, 3, 5, 7, 9..."]
    C --> C1{"Đã đủ thời lượng?"}
    C1 -->|Đã vừa khít| C2["Hoàn thành khớp khít 100%"]
    C1 -->|Vẫn còn thiếu nhẹ| C3["Fade out âm thanh an toàn 0.8s ở cuối"]

    B -->|T_video > T_audio: THỪA HÌNH| D["2. Lọc bỏ 1-2 B-Roll phụ hoặc co ngắn cảnh cuối"]
    D --> D1["Khớp chuẩn mili-giây với câu nói cuối"]
```

1. **Khi THIẾU HÌNH ($T_{\text{video}} < T_{\text{audio}}$):**
   - Giảm tốc độ nhẹ $0.95x$ (`setpts=1.0526*PTS`) trên các cảnh B-Roll lẻ ($1, 3, 5, 7, 9...$), cảnh chẵn giữ nguyên $1.0x$.
   - Mức $0.95x$ là ngưỡng an toàn tuyệt đối, mắt người không nhận ra sự thay đổi.
   - Nếu vẫn còn thiếu nhẹ $\to$ Áp dụng `afade=t=out:st=T_video-0.8:d=0.8` kết thúc êm ái, **tuyệt đối không bao giờ để đen màn hình**.
2. **Khi THỪA HÌNH ($T_{\text{video}} > T_{\text{audio}}$):**
   - Tự động bỏ 1-2 cảnh phụ hoặc co ngắn đuôi cảnh cuối khớp khít câu kết thúc của giọng AI.

---

## 6. KỸ THUẬT ĐÈ SUBTITLE MỚI LÊN ĐÚNG VÙNG SUB CŨ ĐÃ BLUR

1. Gemini CLI trả về tọa độ dải Sub cũ: $Y_{\text{sub}} = [0.74, 0.86]$.
2. FFmpeg bôi mờ (`boxblur`) dải chữ cũ này.
3. Hệ thống tính toán lề đáy cho Sub mới:
   $$\text{MarginV} = H_{\text{output}} \times (1.0 - Y_{\text{sub\_center}})$$
4. Đè Subtitle mới (kiểu TikTok Slim, viền đen dày $3\text{px}$, đổ bóng 3D) vào đúng tọa độ này.
5. **Kết quả:** Phụ đề mới sắc nét đè kín lên trên mảng mờ $\to$ Người xem thấy video đẹp tự nhiên $100\%$, xóa sạch mọi dấu vết cũ.

---

## 7. PHÂN HỆ TẢI VIDEO TIKTOK KHÔNG WATERMARK (YT-DLP ENGINE)

- Tích hợp lệnh `yt-dlp` trích xuất luồng API gốc của TikTok (`download_addr`), **không dính logo watermark mờ/nhấp nháy ở 4 góc**.
- Hỗ trợ link đầy đủ, link rút gọn (`vt.tiktok.com`), Reels, Shorts.
- **Kiểm tra 2 đầu thông minh bằng AI:** Gemini CLI tự xem 0.5s đầu và 1s cuối; nếu có logo TikTok giật thì mới cắt, nếu là nội dung thật thì **giữ nguyên $100\%$ ($0.0s$)**.

---

## 8. THIẾT KẾ GIAO DIỆN GUI TINH GỌN

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  TAB 1: NGUỒN & DỌN RÁC (CLEAN) │  TAB 2: AI & GIỌNG ĐỌC  │  TAB 3: RE-MIX & HẬU KỲ│
├──────────────────────────────────────────────────────────────────────────────────┤
│ [TAB 1: NGUỒN & DỌN RÁC]                                                         │
│  • Link TikTok / File MP4: [ https://vt.tiktok.com/ZS...            ] [📥 Tải/📁] │
│  • Thị trường đầu ra:      [ 🇩🇪 Đức — Deutsch ▼ ]  [✔] Tự đổi giọng đúng vùng    │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  🛡️ TẨY DẤU VẾT ĐỐI THỦ (GEMINI CLI AUTO-CLEAN):                                 │
│  [✔] Tự động cắt 2 đầu (Bỏ Title cũ & Nền mờ ➔ Lấy Lõi Video Sạch Ảnh 2)        │
│  [✔] Tự động bôi mờ dải Sub cũ & Badge Part cũ    Độ mờ: [====●====] 75%         │
│  [✔] AI tự kiểm tra & cắt logo TikTok 2 đầu (0.0s nếu không có)                 │
├──────────────────────────────────────────────────────────────────────────────────┤
│ [TAB 2: AI & GIỌNG ĐỌC]                                                          │
│  [✔] Bật Giọng đọc AI (Thuyết minh kịch bản mới)                                 │
│  • Engine TTS: [ CapCut TTS ▼ ]    Vùng: [ de-DE ▼ ]   Giọng: [ Conrad (Nam) ▼ ] │
│  • Tốc độ đọc: [ +0% ▼ ]           [🔊 Nghe thử giọng]                            │
│  • Gemini Model: [ Google Antigravity (Gemini 2.5 Flash CLI) ▼ ]                 │
├──────────────────────────────────────────────────────────────────────────────────┤
│ [TAB 3: RE-MIX & HẬU KỲ]                                                         │
│  🎬 BỘ NÃO PHÂN TÁCH CẢNH THÔNG MINH:                                            │
│  [✔] Tách & Giữ lại đoạn Hook gốc mở đầu (Hình + Tiếng kịch tính)                │
│  [✔] Cắt bỏ chuyển cảnh rác (Flash/Zoom 0.15s - 0.25s / Hard Cut giữ 0.0s)       │
│  [✔] Điều tốc đàn hồi B-Roll (0.95x cảnh lẻ) khớp khít âm thanh                  │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  🔀 CÔNG CỤ PHÁ MÃ BẢN QUYỀN (PIXEL ANTI-HASH):                                  │
│  [✔] Đảo trật tự cảnh b-roll hợp lý      [✔] Lật gương phản chiếu (hflip)        │
│  [✔] Đè Sub mới chính xác lên vùng Sub cũ đã Blur                                │
│  ──────────────────────────────────────────────────────────────────────────────  │
│  🎛️ BỘ 3 STUDIO CHUYÊN NGHIỆP:                                                  │
│  [ 🎨 Color (15 Màu CapCut + 17 Look) ]  [ 🔤 Title & Sub Studio ]  [ ✂ Crop Studio ]│
├──────────────────────────────────────────────────────────────────────────────────┤
│  CONFIG: [ test1 ▼ ] [💾 Lưu Config]                 [ 🚀 BẮT ĐẦU XỬ LÝ REMIX ]  │
└──────────────────────────────────────────────────────────────────────────────────┘
```

---

## 9. TỔNG HỢP QUY TRÌNH XỬ LÝ TRONG 1 LỆNH FFMPEG MASTER

```mermaid
sequenceDiagram
    autonumber
    participant UI as Giao Diện GUI
    participant YTL as yt-dlp Engine
    participant CLI as Gemini 2.5 Flash CLI
    participant TTS as CapCut / Edge-TTS
    participant FFMPEG as FFmpeg Master Renderer

    UI->>YTL: Tải video TikTok sạch không watermark
    UI->>CLI: Gửi toàn bộ video MP4 phân tích (1 lần duy nhất)
    CLI-->>UI: Trả về JSON (Crop lõi, Blur sub, Hook, Clean scenes, Script)
    UI->>TTS: Tạo giọng đọc AI narration.mp3 + subtitles.srt
    UI->>FFMPEG: Thực thi chuỗi lệnh Master:
    Note over FFMPEG: 1. Crop lõi sạch (bỏ Title cũ)<br/>2. Blur dải Sub cũ<br/>3. Cắt cảnh sạch (0.0s vs 0.2s)<br/>4. Điều tốc đàn hồi 0.95x cảnh lẻ<br/>5. Shuffle + Lật gương<br/>6. Ghép Hook gốc + Giọng AI mới<br/>7. Đổ màu CapCut 8K<br/>8. Đè Title Banner + Đè Sub mới lên vết mờ
    FFMPEG-->>UI: Xuất Video thành phẩm 9:16 độc quyền 100% (~30s)
```
