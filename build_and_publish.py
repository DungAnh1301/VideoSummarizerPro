import os
import sys
import json
import shutil
import hashlib
import zipfile
import subprocess
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VERSION = "1.3.12"
REPOSITORY = "DungAnh1301/VideoSummarizerPro"
NOTES = "Bản phát hành v1.3.12: Chuẩn hóa 100% tư duy khử Logo / Sub / Banner theo đúng chuẩn Tóm Tắt Video. Loại bỏ việc phân mảnh quá nhiều filter blur gây lỗi FFmpeg; Tự động gom cụm không gian (Spatial Clustering) và hợp nhất thời gian để tạo kính mờ duy nhất cho logo/sub/banner; Giữ nguyên pipeline màu sắc, crop, zoom và Blur Mask chuẩn Tóm Tắt."
ROOT = os.path.dirname(os.path.abspath(__file__))

print(f"[BUILD] Bat dau dong goi ban v{VERSION}...")

# 1. Update version.json
version_file = os.path.join(ROOT, "version.json")
with open(version_file, "w", encoding="utf-8") as f:
    json.dump({"version": VERSION, "channel": "stable"}, f, indent=2)

# 2. Tao staging & release directory
stage_dir = os.path.join(ROOT, "build", f"update-payload-{VERSION}")
release_dir = os.path.join(ROOT, "release")
if os.path.exists(stage_dir):
    shutil.rmtree(stage_dir)
os.makedirs(stage_dir, exist_ok=True)
os.makedirs(release_dir, exist_ok=True)

# 3. Copy files
files = [
    "main.py", "compilation_processor.py", "ai_processor.py", "antigravity_processor.py", "batch_queue.py", "broll_semantic.py",
    "capcut_filters.py", "config_manager.py", "downloader_processor.py",
    "editor_ai.py", "editor_processor.py", "ensure_runtime.py",
    "hardware_manager.py", "local_source_processor.py", "market_profiles.py", "part_processor.py", "scene_mapper.py",
    "youtube_heatmap.py", "update_manager.py", "updater.py", "version.json", "requirements.txt",
    "config.example.json", "Chay_App.bat", "README_CAI_DAT.md",
    "font_manager.py", "part_splitter_config.py", "part_pruner_ai.py",
    "part_splitter_engine.py", "part_splitter_gui.py", "config_part_splitter.example.json", "PLAN_CHIA_PART.md"
]
for fname in files:
    src = os.path.join(ROOT, fname)
    if os.path.isfile(src):
        shutil.copy2(src, os.path.join(stage_dir, fname))
    else:
        print(f"[WARN] Khong thay file: {fname}")

for folder in ["utils", "assets", "luts"]:
    src = os.path.join(ROOT, folder)
    if os.path.isdir(src):
        dst = os.path.join(stage_dir, folder)
        shutil.copytree(src, dst, dirs_exist_ok=True)

agy_installer = os.path.join(ROOT, "runtime", "antigravity-install.cmd")
if os.path.isfile(agy_installer):
    os.makedirs(os.path.join(stage_dir, "runtime"), exist_ok=True)
    shutil.copy2(agy_installer, os.path.join(stage_dir, "runtime", "antigravity-install.cmd"))
    shutil.copy2(agy_installer, os.path.join(stage_dir, "antigravity-install.cmd"))

# 4. Tao zip package
package_name = f"VideoSummarizerPro_Update_{VERSION}.zip"
package_path = os.path.join(release_dir, package_name)
if os.path.exists(package_path):
    os.remove(package_path)

print(f"[ZIP] Dang nen file zip: {package_name}...")
with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
    for root, dirs, fnames in os.walk(stage_dir):
        for fname in fnames:
            full_path = os.path.join(root, fname)
            arcname = os.path.relpath(full_path, stage_dir)
            zipf.write(full_path, arcname)

# 5. Tinh SHA256 va size
hasher = hashlib.sha256()
with open(package_path, "rb") as f:
    while chunk := f.read(65536):
        hasher.update(chunk)
file_hash = hasher.hexdigest().lower()
file_size = os.path.getsize(package_path)

# 6. Ghi manifest latest.json
manifest = {
    "version": VERSION,
    "package": package_name,
    "sha256": file_hash,
    "size": file_size,
    "notes": NOTES,
    "runtime_update": False,
    "minimum_setup_version": "1.1.0",
    "repository": REPOSITORY,
    "published_at": datetime.now().isoformat()
}
latest_path = os.path.join(release_dir, "latest.json")
with open(latest_path, "w", encoding="utf-8") as f:
    json.dump(manifest, f, indent=2)

print(f"[OK] Package: {package_path} ({file_size / (1024*1024):.2f} MB)")
print(f"[OK] SHA256: {file_hash}")

# 7. Upload qua GitHub CLI
tag = f"v{VERSION}"
print(f"[UPLOAD] Dang upload len GitHub Release {tag} qua gh CLI...")

check_res = subprocess.run(["gh", "release", "view", tag, "--repo", REPOSITORY], capture_output=True, text=True, encoding="utf-8", errors="replace")
if check_res.returncode == 0:
    print(f"[UPDATE] Release {tag} da ton tai, cap nhat assets...")
    subprocess.run([
        "gh", "release", "edit", tag, "--repo", REPOSITORY,
        "--title", f"Video Summarizer Pro {tag}", "--notes", NOTES, "--latest"
    ], check=True, encoding="utf-8", errors="replace")
    subprocess.run([
        "gh", "release", "upload", tag, package_path, latest_path,
        "--repo", REPOSITORY, "--clobber"
    ], check=True, encoding="utf-8", errors="replace")
else:
    print(f"[CREATE] Tao moi Release {tag}...")
    subprocess.run([
        "gh", "release", "create", tag, package_path, latest_path,
        "--repo", REPOSITORY,
        "--title", f"Video Summarizer Pro {tag}",
        "--notes", NOTES,
        "--latest"
    ], check=True, encoding="utf-8", errors="replace")

print(f"\n[DONE] XUAT BAN THANH CONG: https://github.com/{REPOSITORY}/releases/tag/{tag}")
