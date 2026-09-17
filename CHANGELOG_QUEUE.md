# Bản cập nhật Config có tên + Hàng đợi render

## Đã thêm

- Config có tên hoạt động giống phần Prompt; có lưu API key cục bộ.
- Nút Add vào hàng đợi, Add & Chạy và tùy chọn tự chạy khi Add.
- Bảng hàng đợi thay cho log Tkinter; log chi tiết tiếp tục ra CMD.
- Có thể Add video mới trong lúc Job hiện tại đang chạy.
- Mỗi Job giữ snapshot nguồn, AI, API key, prompt, voice và hậu kỳ riêng.
- Hàng đợi lưu tại `data/queue.json` và tự khôi phục khi mở lại app.
- Hook AI thiếu file chuyển sang `Chờ video AI` và không chặn Job sau.
- Hook AI có file sẵn tự chạy bằng `EditorAI` trong chế độ hàng đợi.
- Xóa, đổi thứ tự, chạy lại và sửa Job bằng double-click.
- Khi chạy/chạy lại, tự kiểm tra `source_video.mp4` và `source_video_low.mp4`
  bằng ffprobe + giải mã thử ở đầu/giữa/gần cuối. File hỏng bị loại bỏ và
  tải/copy/render lại; file còn tốt tiếp tục được tái sử dụng.
- Bỏ luồng/nút `Chạy Liền Mạch`; render chính thức đi qua hàng đợi để tránh chạy song song.
- Cache AI/voice theo fingerprint model + prompt + transcript + script + giọng đọc.
  Retry giữ lại script/voice hợp lệ và chỉ chạy lại stage bị hỏng hoặc đã đổi cấu hình.

## Cài vào dự án hiện tại

Sao chép các file trong thư mục này vào thư mục cài/source của Video Summarizer Pro,
giữ nguyên các thư mục lớn `runtime`, `vendor`, `bin`, `assets` và `luts` đang có.

Không chia sẻ `config.json` hoặc `data/queue.json` vì chúng có thể chứa API key.
