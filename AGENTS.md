# QUY TẮC PHÁT TRIỂN & CẬP NHẬT DỰ ÁN VIDEO SUMMARIZER PRO

## Quy trình khi người dùng yêu cầu "Cập nhật" / "Update":
Mỗi khi người dùng yêu cầu "cập nhật", "update" hoặc triển khai phiên bản mới, Agent PHẢI tự động thực hiện đầy đủ quy trình trọn gói sau mà không cần hỏi lại:

1. **Tăng phiên bản (Bump Version)**:
   - Cập nhật số phiên bản (patch version) trong `version.json` (ví dụ: `1.3.97` -> `1.3.98`).
   - Cập nhật `VERSION` và `NOTES` mô tả các thay đổi tương ứng trong [build_and_publish.py](file:///e:/Video_summarizer_Pro/build_and_publish.py).

2. **Commit và Push mã nguồn**:
   - `git add .` (hoặc các file liên quan)
   - `git commit -m "..."`
   - `git push origin main`

3. **Đóng gói & Xuất bản GitHub Release**:
   - Chạy lệnh: `python build_and_publish.py`
   - Script này sẽ tự động:
     - Tạo thư mục staging và nén `release/VideoSummarizerPro_Update_<VERSION>.zip`
     - Tính hash SHA-256 và tạo `release/latest.json`
     - Dùng `gh release create v<VERSION>` (hoặc edit) đẩy zip + latest.json lên GitHub Releases và đánh dấu `--latest`.
   - Cam kết file `build_and_publish.py` và push nếu có thay đổi.

4. **Kiểm tra & Thông báo**:
   - Đảm bảo GitHub Release đã có tag mới nhất trước khi hoàn tất để tính năng tự động cập nhật trong ứng dụng (In-App Updater) đọc được ngay lập tức.
