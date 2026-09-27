import os
import subprocess

def run_test():
    print("🧪 [TEST] Chạy test chuẩn hệ quy chiếu 1920x1080 cho Blur Mask...")
    
    source_video = None
    base_temp = "temp"
    if os.path.exists(base_temp):
        sub_dirs = [os.path.join(base_temp, d) for d in os.listdir(base_temp) if os.path.isdir(os.path.join(base_temp, d))]
        if sub_dirs:
            latest_dir = max(sub_dirs, key=os.path.getmtime)
            candidate = os.path.join(latest_dir, "source_video.mp4")
            if os.path.exists(candidate):
                source_video = candidate

    if not source_video:
        print("❌ Không tìm thấy file source_video.mp4 trong temp!")
        return

    out_no_zoom = os.path.join("temp", "test_blur_no_zoom.mp4")
    out_zoom = os.path.join("temp", "test_blur_with_zoom.mp4")
    
    # Tọa độ Blur của sếp
    mx, my, mw, mh = 20, 861, 932, 210
    zoom_pct = 1.78
    z_w = int(1080 * zoom_pct)
    
    # LUỒNG AN TOÀN: Chuẩn hóa video gốc về đúng 1920x1080 trước [v_src], rồi mới split & crop
    filter_no_zoom = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:12[bg];"
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_src];"
        f"[v_src]split=2[orig1][orig2];"
        f"[orig2]crop={mw}:{mh}:{mx}:{my},boxblur=25:25[blur_chunk];"
        f"[orig1][blur_chunk]overlay={mx}:{my}[video_blurred];"
        f"[video_blurred]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
    )

    filter_zoom = (
        f"[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:12[bg];"
        f"[0:v]scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2[v_src];"
        f"[v_src]split=2[orig1][orig2];"
        f"[orig2]crop={mw}:{mh}:{mx}:{my},boxblur=25:25[blur_chunk];"
        f"[orig1][blur_chunk]overlay={mx}:{my}[video_blurred];"
        f"[video_blurred]scale={z_w}:-2:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vout]"
    )

    def render(filter_str, out_path, name):
        print(f"\n🎬 Đang chạy FFmpeg render: {name}...")
        cmd = [
            "ffmpeg", "-y", "-i", source_video, "-t", "5.0",
            "-filter_complex", filter_str,
            "-map", "[vout]", "-c:v", "h264_nvenc", "-preset", "p1", "-an", out_path
        ]
        try:
            subprocess.run(cmd, check=True, creationflags=0x08000000)
            print(f"✅ Xong {name}! File tại: {out_path}")
        except Exception as e:
            print(f"❌ Lỗi {name}: {e}")

    render(filter_no_zoom, out_no_zoom, "BẢN KHÔNG ZOOM")
    render(filter_zoom, out_zoom, "BẢN ZOOM 178%")
    print("\n🎉 [HOÀN TẤT] Test xong! Sếp kiểm tra 2 file trong thư mục temp nhé.")

if __name__ == "__main__":
    run_test()