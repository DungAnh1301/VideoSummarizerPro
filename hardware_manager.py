# -*- coding: utf-8 -*-
"""Nhận diện phần cứng một lần, lưu hồ sơ và tự chọn encoder FFmpeg."""
from __future__ import annotations

import ctypes
import json
import os
import platform
import subprocess
import tempfile
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
ENCODERS = ("h264_nvenc", "h264_qsv", "h264_amf", "libx264")


def app_root() -> Path:
    if getattr(__import__("sys"), "frozen", False):
        return Path(__import__("sys").executable).resolve().parent
    return Path(__file__).resolve().parent


def profile_path() -> Path:
    return app_root() / "hardware_profile.json"


def _memory_gb() -> float:
    if os.name != "nt":
        return 0.0
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    state = MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(state)
    return round(state.ullTotalPhys / (1024 ** 3), 1) if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)) else 0.0


def hardware_fingerprint() -> str:
    return "|".join((platform.machine(), platform.processor(), str(os.cpu_count() or 1), str(_memory_gb())))


def _gpu_info() -> list[dict]:
    """Đọc tên GPU/VRAM/driver khi Setup hoặc người dùng yêu cầu kiểm tra lại."""
    results = []
    try:
        nvidia = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15, creationflags=CREATE_NO_WINDOW,
        )
        if nvidia.returncode == 0:
            for line in (nvidia.stdout or "").splitlines():
                parts = [part.strip() for part in line.split(",")]
                if len(parts) >= 3:
                    results.append({
                        "name": parts[0], "vram_gb": round(float(parts[1]) / 1024.0, 1),
                        "driver": parts[2], "vendor": "NVIDIA",
                    })
    except Exception:
        pass

    if os.name == "nt":
        try:
            script = (
                "Get-CimInstance Win32_VideoController | "
                "Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json -Compress"
            )
            query = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=25, creationflags=CREATE_NO_WINDOW,
            )
            if query.returncode == 0 and (query.stdout or "").strip():
                payload = json.loads(query.stdout)
                for item in payload if isinstance(payload, list) else [payload]:
                    name = str(item.get("Name") or "Unknown GPU").strip()
                    if any(existing["name"].lower() == name.lower() for existing in results):
                        continue
                    raw_ram = int(item.get("AdapterRAM") or 0)
                    vendor = "NVIDIA" if "nvidia" in name.lower() else ("Intel" if "intel" in name.lower() else ("AMD" if any(x in name.lower() for x in ("amd", "radeon")) else "Other"))
                    results.append({
                        "name": name,
                        "vram_gb": round(raw_ram / (1024 ** 3), 1) if raw_ram else 0.0,
                        "driver": str(item.get("DriverVersion") or ""), "vendor": vendor,
                    })
        except Exception:
            pass
    return results


def _available_encoders() -> set[str]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=20, creationflags=CREATE_NO_WINDOW,
        )
        text = (result.stdout or "") + (result.stderr or "")
        return {name for name in ENCODERS if name in text}
    except Exception:
        return {"libx264"}


def _test_encoder(name: str) -> bool:
    target = Path(tempfile.gettempdir()) / f"vsp_encoder_{name}.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=0.25",
        "-an", "-c:v", name,
    ]
    if name == "libx264":
        cmd += ["-preset", "ultrafast", "-crf", "23"]
    cmd += ["-frames:v", "2", str(target)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=20, creationflags=CREATE_NO_WINDOW)
        return result.returncode == 0 and target.is_file() and target.stat().st_size > 0
    except Exception:
        return False
    finally:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass


def test_encoder_with_options(encoder: str, options: list[str]) -> bool:
    """Kiểm tra xem cặp encoder + tham số có chạy thành công trên FFmpeg của máy không."""
    target = Path(tempfile.gettempdir()) / f"vsp_test_{encoder}.mp4"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=c=black:s=320x180:r=10:d=0.25",
        "-an", "-c:v", encoder, *options,
        "-frames:v", "2", str(target)
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10, creationflags=CREATE_NO_WINDOW)
        return result.returncode == 0 and target.is_file() and target.stat().st_size > 0
    except Exception:
        return False
    finally:
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass


def detect_hardware(force: bool = False, log=print) -> dict:
    path = profile_path()
    fingerprint = hardware_fingerprint()
    if not force and path.is_file():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if saved.get("fingerprint") == fingerprint and saved.get("encoder"):
                # Nếu encoder đang bị lưu là libx264 nhưng máy có card rời NVIDIA, tự động ưu tiên khôi phục NVENC
                if saved.get("encoder") != "h264_nvenc" and _test_encoder("h264_nvenc"):
                    saved["encoder"] = "h264_nvenc"
                    saved["encoder_fallbacks"] = [x for x in ENCODERS if x != "h264_nvenc"]
                    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
                return saved
        except Exception:
            pass

    cores, ram = os.cpu_count() or 1, _memory_gb()
    gpus = _gpu_info()
    available = _available_encoders()
    working = []
    for encoder in ENCODERS:
        if encoder in available and _test_encoder(encoder):
            working.append(encoder)
    if "libx264" not in working:
        working.append("libx264")
    strength = "weak" if cores <= 4 or (ram and ram < 10) else ("strong" if cores >= 12 and ram >= 16 else "medium")
    profile = {
        "version": 1, "fingerprint": fingerprint, "cpu_cores": cores, "ram_gb": ram,
        "gpus": gpus,
        "strength": strength, "encoder": working[0], "encoder_fallbacks": working[1:],
        "download_threads": 4 if strength == "weak" else (8 if strength == "medium" else 16),
        "parallel_pipeline": strength != "weak",
        "scene_samples_per_second": 1 if strength == "weak" else 2,
    }
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    if log:
        log(f"Hardware: {strength} | {cores} CPU | {ram:.1f} GB RAM | encoder {working[0]}")
    return profile


def get_hardware_profile() -> dict:
    return detect_hardware(force=False, log=None)


def get_part_render_strategy() -> dict:
    """Tự động kiểm tra cấu hình phần cứng (CPU, RAM, GPU, VRAM, Driver)
    để quyết định chiến lược render tối ưu nhất cho từng máy cá nhân:
    - Encoder: h264_nvenc, h264_qsv, h264_amf, hoặc libx264
    - Encoder options: tự kiểm tra và chọn preset tương thích tốt nhất
    - max_parallel_workers: 1 hoặc 2 (dựa trên VRAM GPU và số nhân CPU)
    - filter_threads: số luồng filter_complex cho mỗi tiến trình
    - hardware_summary: chuỗi mô tả trực quan để log ra giao diện người dùng
    """
    hw = get_hardware_profile()
    cores = int(hw.get("cpu_cores") or os.cpu_count() or 4)
    ram = float(hw.get("ram_gb") or 8.0)
    gpus = hw.get("gpus") or []
    enc = hw.get("encoder") or "libx264"

    # 1. Tìm thông tin GPU chính
    primary_gpu = None
    for g in gpus:
        if g.get("vendor") == "NVIDIA":
            primary_gpu = g
            break
    if not primary_gpu and gpus:
        primary_gpu = gpus[0]

    gpu_name = primary_gpu.get("name", "Không phát hiện GPU rời") if primary_gpu else "CPU only"
    vram_gb = float(primary_gpu.get("vram_gb", 0.0)) if primary_gpu else 0.0

    # 2. Xác định Encoder & Tham số kiểm tra
    chosen_encoder = enc
    chosen_opts = []

    if enc == "h264_nvenc":
        # Thử preset p1 + spatial-aq 1 trước
        cand_opts = ["-preset", "p1", "-cq", "19", "-spatial-aq", "1"]
        if test_encoder_with_options("h264_nvenc", cand_opts):
            chosen_opts = cand_opts
        else:
            # Fallback preset phổ thông cho card NVIDIA thế hệ cũ hơn
            chosen_opts = ["-preset", "fast", "-cq", "19"]
    elif enc == "h264_qsv":
        chosen_opts = ["-preset", "veryfast", "-global_quality", "19"]
    elif enc == "h264_amf":
        chosen_opts = ["-quality", "speed", "-qp_i", "19", "-qp_p", "21"]
    else:
        chosen_encoder = "libx264"
        chosen_opts = ["-preset", "ultrafast", "-crf", "19"]

    # 3. Quyết định số Part render song song (max_parallel_workers)
    # Nguyên tắc an toàn:
    # - NVIDIA GPU với VRAM >= 6GB VÀ CPU >= 6 nhân VÀ RAM >= 12GB: Cho phép 2 Part song song (RTX 3060, 4060, 3070...)
    # - Mọi trường hợp khác (GTX 1650 4GB, iGPU Intel QSV, AMD AMF, hoặc CPU libx264): Chạy 1 Part tuần tự
    #   để tránh lỗi Out of Memory VRAM, lỗi quá tải NVENC session limit, hoặc quá tải 100% CPU gây đơ máy.
    if chosen_encoder == "h264_nvenc" and vram_gb >= 6.0 and cores >= 6 and ram >= 12.0:
        max_workers = 2
        filter_threads = min(8, max(2, cores // 2))
        strategy_reason = f"GPU rời {gpu_name} ({vram_gb:.1f}GB VRAM >= 6GB) -> Kích hoạt Dual GPU render song song 2 Part"
    else:
        max_workers = 1
        # Nếu chạy 1 Part, phân bổ luồng hợp lý cho tiến trình đó
        filter_threads = min(8, max(2, cores - 1 if cores > 2 else cores))
        if chosen_encoder == "h264_nvenc":
            strategy_reason = f"GPU {gpu_name} ({vram_gb:.1f}GB VRAM < 6GB) -> 1 Part an toàn chống tràn VRAM"
        elif chosen_encoder == "h264_qsv":
            strategy_reason = f"Đồ họa tích hợp {gpu_name} (Intel QSV) -> 1 Part tối ưu hóa phần cứng iGPU"
        elif chosen_encoder == "h264_amf":
            strategy_reason = f"Đồ họa AMD {gpu_name} (AMF) -> 1 Part tăng tốc phần cứng"
        else:
            strategy_reason = f"CPU {cores} nhân, {ram:.1f}GB RAM (libx264) -> 1 Part đa luồng tối đa"

    summary = f"{gpu_name} ({vram_gb:.1f}GB VRAM) | {cores} CPU | {ram:.1f}GB RAM => {strategy_reason}"

    return {
        "encoder": chosen_encoder,
        "encoder_opts": chosen_opts,
        "max_parallel_workers": max_workers,
        "filter_threads": filter_threads,
        "ffmpeg_threads": str(cores),
        "hardware_summary": summary,
        "vram_gb": vram_gb,
        "cpu_cores": cores,
        "ram_gb": ram,
        "gpu_name": gpu_name,
    }


def _encoder_command(command: list, encoder: str) -> list:
    cmd = list(command)
    if "-c:v" not in cmd:
        return cmd
    remove_flags = {"-preset", "-rc", "-cq", "-b:v", "-maxrate", "-bufsize",
                    "-crf", "-quality", "-qp_i", "-qp_p"}
    cleaned, i = [], 0
    while i < len(cmd):
        if cmd[i] in remove_flags and i + 1 < len(cmd):
            i += 2
            continue
        cleaned.append(cmd[i])
        i += 1
    idx = cleaned.index("-c:v")
    cleaned[idx + 1] = encoder
    extras = {
        "h264_nvenc": ["-preset", "p4", "-rc", "vbr", "-cq", "17", "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M"],
        "h264_qsv": ["-preset", "veryfast", "-global_quality", "18"],
        "h264_amf": ["-quality", "speed", "-qp_i", "18", "-qp_p", "20"],
        "libx264": ["-preset", "veryfast", "-crf", "18"],
    }[encoder]
    cleaned[idx + 2:idx + 2] = extras
    return cleaned


def run_ffmpeg_auto(command: list, *, label: str = "render", logger=None, **run_kwargs):
    """Chạy encoder tốt nhất; lỗi thì tự hạ xuống encoder kế tiếp và lưu lựa chọn."""
    profile = get_hardware_profile()
    candidates = [profile.get("encoder", "libx264"), *profile.get("encoder_fallbacks", [])]
    candidates += [x for x in ENCODERS if x not in candidates]
    last_error = None
    last_detail = ""
    for encoder in candidates:
        cmd = _encoder_command(command, encoder)
        try:
            if logger:
                logger.info(f"🖥️ [{label}] Encoder tự động: {encoder}")
            effective_kwargs = dict(run_kwargs)
            # GUI không có console để hiển thị stderr. Luôn thu lại lỗi để
            # thông báo cuối cùng cho biết nguyên nhân thật thay vì chỉ có exit code.
            if not any(key in effective_kwargs for key in ("stderr", "capture_output")):
                effective_kwargs["stderr"] = subprocess.PIPE
            if "text" not in effective_kwargs and "encoding" not in effective_kwargs:
                effective_kwargs["text"] = True
                effective_kwargs["encoding"] = "utf-8"
                effective_kwargs["errors"] = "replace"
            result = subprocess.run(cmd, **effective_kwargs)
            if run_kwargs.get("check") is False and result.returncode != 0:
                raise subprocess.CalledProcessError(result.returncode, cmd, stderr=result.stderr)
            if encoder != profile.get("encoder"):
                profile["encoder"] = encoder
                profile["encoder_fallbacks"] = [x for x in candidates if x != encoder]
                profile_path().write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
            return result
        except (subprocess.CalledProcessError, OSError) as exc:
            last_error = exc
            stderr = getattr(exc, "stderr", None)
            if not stderr and "result" in locals():
                stderr = getattr(result, "stderr", None)
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            lines = [line.strip() for line in str(stderr or "").splitlines() if line.strip()]
            last_detail = lines[-1] if lines else str(exc)
            if logger:
                logger.warning(
                    f"⚠️ [{label}] {encoder} lỗi: {last_detail} — tự chuyển encoder khác."
                )
    raise RuntimeError(f"Không encoder nào render được. FFmpeg: {last_detail or last_error}")


def set_console_visible(visible: bool) -> bool:
    if os.name != "nt":
        return False
    hwnd = ctypes.windll.kernel32.GetConsoleWindow()
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, 5 if visible else 0)
    return True
