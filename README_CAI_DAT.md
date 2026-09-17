# Video Summarizer Pro

## Ba chế độ Hook

- **Tự chọn mốc:** nhập mốc bắt đầu và số giây; cắt từ source HD, giữ tiếng gốc.
- **Gemini chọn cao trào:** chỉ nhập số giây; Gemini xem proxy 360p, chọn mốc mạnh nhất rồi ứng dụng cắt từ source HD và giữ tiếng gốc.
- **Hook AI:** dùng video Hook tùy chỉnh trong Hook Studio như các phiên bản trước.

Đặt thời lượng Hook bằng `0` để tắt Hook hoàn toàn.

Gói cập nhật chứa bộ cài Antigravity CLI ở cả thư mục `runtime` và thư mục
gốc để tương thích với updater của những phiên bản cũ. Máy mới chỉ cần bấm
**Đăng nhập / đổi tài khoản**; ứng dụng sẽ tự cài CLI nếu chưa có.

1. Copy **VideoSummarizerPro_Setup.exe** (trong thư mục project hoặc `dist\`) sang máy mới — **đừng** chạy `CaiDat_VideoSummarizerPro.exe` / `setup.exe` 141MB.
2. Double-click → chọn thư mục → đợi cài xong.
3. Mở app bằng shortcut Desktop **Video Summarizer Pro**.

nén lại filt update file 
powershell -File build_setup.ps1

## Hàng đợi render

- Chỉnh một video như bình thường, chọn Config có tên rồi bấm **Add vào hàng đợi**.
- Có thể tiếp tục Add video mới trong lúc video trước đang chạy.
- Hook AI phải có sẵn video AI; nếu thiếu, Job chuyển sang **Chờ video AI** và không chặn Job sau.
- Log chi tiết hiển thị trong cửa sổ CMD; trạng thái từng video hiển thị trong bảng hàng đợi.
- Config có tên và Job có thể chứa API key cục bộ. Không chia sẻ `config.json` hoặc thư mục `data`.
# Cấu hình tối thiểu

- Windows 10/11 64-bit.
- CPU 4 nhân, RAM 8 GB.
- Không bắt buộc card đồ họa rời; máy không có GPU hỗ trợ sẽ tự render bằng CPU và chậm hơn.
- Nên còn trống ít nhất 20 GB cho source, video low-res và file tạm.
- Cần Internet để tải video và sử dụng các dịch vụ AI/TTS.

Setup kiểm tra encoder đúng một lần và lưu `hardware_profile.json`. Tool tự ưu tiên NVIDIA NVENC, Intel Quick Sync, AMD AMF rồi đến CPU; người dùng không phải chọn. Nếu encoder đang dùng lỗi, tool tự chuyển phương án khác. Nút **Kiểm tra máy** trên giao diện dùng khi thay GPU hoặc cập nhật driver.

# Phát hành cập nhật qua GitHub

Repository phát hành: `DungAnh1301/VideoSummarizerPro`. Mỗi GitHub Release phải đính kèm đúng cặp file do `build_update.ps1` tạo: `latest.json` và `VideoSummarizerPro_Update_X.Y.Z.zip`. Không đổi tên hai file sau khi build.

Ví dụ tạo bản mới:

```powershell
powershell -ExecutionPolicy Bypass -File .\build_update.ps1 -Version 1.1.1 -Notes "Mô tả thay đổi"
```

Tạo GitHub Release với tag trùng phiên bản, ví dụ `v1.1.1`, rồi tải hai file trong thư mục `release` lên phần Assets. Không đưa `config.json`, API key, `output`, `temp`, `data` hoặc `hardware_profile.json` lên GitHub.
