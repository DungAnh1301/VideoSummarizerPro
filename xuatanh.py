import subprocess

youtube_url = "https://www.youtube.com/watch?v=XXXXX"
timestamp = 42.5

# Bước 1: tải video
subprocess.run([
    "yt-dlp",
    "-f", "bestvideo[height<=1080]+bestaudio/best",
    "-o", "video.mp4",
    youtube_url
], check=True)

# Bước 2: lấy frame
subprocess.run([
    "ffmpeg",
    "-ss", str(timestamp),
    "-i", "video.mp4",
    "-frames:v", "1",
    "-q:v", "2",
    "frame.jpg"
], check=True)