# -*- coding: utf-8 -*-
"""Bootstrap Windows: Python portable + FFmpeg + CapCut TTS + Whisper. Idempotent."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Callable, Optional
from urllib.request import Request, urlopen

CAPCUT_TTS_REPO = "https://github.com/K07VN/capcut-tts-api.git"
CAPCUT_TTS_ZIP = "https://github.com/K07VN/capcut-tts-api/archive/refs/heads/main.zip"

FFMPEG_URLS = [
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
]
WINGET_FFMPEG_ID = "Gyan.FFmpeg"
USER_AGENT = "VideoSummarizerPro-Setup/1.0"
WHISPER_HF_REPO = "Systran/faster-whisper-base"

# Python đủ stdlib + tkinter, cài im lặng vào thư mục app (không PATH máy).
PYTHON_VERSION = "3.12.10"
PYTHON_INSTALLER_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.exe"
)
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"

REQUIRED_PACKAGES = [
    "edge-tts",
    "pygame",
    "faster-whisper",
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "yt-dlp",
    "opencv-python",
    "scenedetect",
    "numpy",
    "gtts",
    "requests",
    "pillow",
    "youtube-transcript-api",
]

LogFn = Callable[[str], None]


def _configure_stdio() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def get_app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bin_dir() -> Path:
    return get_app_root() / "bin"


def tts_dir() -> Path:
    return get_app_root() / "vendor" / "capcut_tts"


def runtime_python_dir() -> Path:
    return get_app_root() / "runtime" / "python"


def portable_python_exe() -> Optional[Path]:
    p = runtime_python_dir() / "python.exe"
    return p if p.is_file() else None


def skip_download() -> bool:
    return os.environ.get("ENSURE_RUNTIME_SKIP_DOWNLOAD", "").strip().lower() in (
        "1", "true", "yes",
    )


def log(msg: str, logger: Optional[LogFn] = None) -> None:
    text = str(msg)
    if logger:
        logger(text)
    else:
        print(text, flush=True)


def ffmpeg_exe_path() -> Optional[Path]:
    bundled = bin_dir() / "ffmpeg.exe"
    if bundled.is_file():
        return bundled
    found = shutil.which("ffmpeg")
    return Path(found) if found else None


def ffmpeg_exists() -> bool:
    path = ffmpeg_exe_path()
    return bool(path and path.is_file())


def tts_dir_exists() -> bool:
    root = tts_dir()
    return (root / "capcut_tts_api" / "__init__.py").is_file() and (root / "Voice.json").is_file()


def hf_cache_dir() -> Path:
    return get_app_root() / "runtime" / "hf_cache"


def whisper_model_exists() -> bool:
    for base in (
        hf_cache_dir() / "hub" / "models--Systran--faster-whisper-base",
        Path.home() / ".cache" / "huggingface" / "hub" / "models--Systran--faster-whisper-base",
    ):
        if base.is_dir() and any(base.rglob("*.bin")):
            return True
    return False


def apply_runtime_env() -> None:
    bundled_bin = bin_dir()
    if bundled_bin.is_dir():
        current = os.environ.get("PATH", "")
        prefix = str(bundled_bin)
        parts = current.split(os.pathsep) if current else []
        if prefix not in parts:
            os.environ["PATH"] = prefix + (os.pathsep + current if current else "")
    py_dir = runtime_python_dir()
    if py_dir.is_dir():
        scripts = py_dir / "Scripts"
        extra_path = str(py_dir)
        if scripts.is_dir():
            extra_path = str(scripts) + os.pathsep + extra_path
        current = os.environ.get("PATH", "")
        if str(py_dir) not in current.split(os.pathsep):
            os.environ["PATH"] = extra_path + os.pathsep + current
    os.environ.setdefault("HF_HOME", str(hf_cache_dir()))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    try:
        import logging as _logging
        _logging.getLogger("huggingface_hub").setLevel(_logging.ERROR)
    except Exception:
        pass
    root = get_app_root()
    for extra in (str(tts_dir()), str(root)):
        if extra not in sys.path:
            sys.path.insert(0, extra)


def find_python() -> Optional[list]:
    """Ưu tiên Python portable trong app. Không phụ thuộc Python trên PATH máy."""
    bundled = portable_python_exe()
    if bundled:
        return [str(bundled)]
    return None


def _python_ok(exe: Path, logger: Optional[LogFn] = None) -> bool:
    if not exe.is_file():
        log(f"❌ Chưa có file: {exe}", logger)
        return False
    try:
        r = subprocess.run(
            [str(exe), "-c", "import tkinter, pip, sys; assert sys.version_info >= (3, 9)"],
            capture_output=True,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode == 0:
            return True
        err = (r.stderr or r.stdout or "").strip()
        log(f"❌ python.exe có nhưng không chạy được (mã {r.returncode}): {err[:500]}", logger)
        return False
    except Exception as exc:
        log(f"❌ Không gọi được python.exe ({exc})", logger)
        return False


def _download_file(url: str, dest: Path, logger: Optional[LogFn] = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = Request(url, headers={"User-Agent": USER_AGENT})
    log(f"⬇️  Đang tải: {url}", logger)
    with urlopen(req, timeout=300) as resp, open(dest, "wb") as out:
        total = resp.headers.get("Content-Length")
        total_n = int(total) if total and str(total).isdigit() else 0
        read = 0
        last_pct = -1
        while True:
            data = resp.read(256 * 1024)
            if not data:
                break
            out.write(data)
            read += len(data)
            if total_n > 0:
                pct = int(read * 100 / total_n)
                if pct != last_pct and pct % 5 == 0:
                    log(f"   … {min(pct, 100)}%", logger)
                    last_pct = pct
    log(f"✅ Đã tải xong: {dest.name}", logger)


def ensure_pip(python_exe: Path, logger: Optional[LogFn] = None) -> None:
    r = subprocess.run(
        [str(python_exe), "-m", "pip", "--version"],
        capture_output=True,
        timeout=60,
    )
    if r.returncode == 0:
        return
    log("📦 Đang bật pip cho Python portable...", logger)
    pip_py = get_app_root() / "temp" / "get-pip.py"
    _download_file(GET_PIP_URL, pip_py, logger)
    subprocess.check_call([str(python_exe), str(pip_py), "--no-warn-script-location"])
    pip_py.unlink(missing_ok=True)


def ensure_portable_python(logger: Optional[LogFn] = None) -> Path:
    dest = runtime_python_dir()
    exe = dest / "python.exe"
    if _python_ok(exe, logger):
        log(f"✅ Python portable đã có — bỏ qua tải. ({exe})", logger)
        ensure_pip(exe, logger)
        return exe
    if skip_download():
        raise RuntimeError("Thiếu Python portable nhưng ENSURE_RUNTIME_SKIP_DOWNLOAD=1.")

    log(f"⏳ Đang cài Python {PYTHON_VERSION} vào thư mục app (không đụng PATH máy)...", logger)
    dest.parent.mkdir(parents=True, exist_ok=True)
    keep_installer = False
    installer = None
    for cand in (
        get_app_root() / "tools" / f"python-{PYTHON_VERSION}-amd64.exe",
        get_app_root() / "tools" / "python-amd64.exe",
    ):
        if cand.is_file() and cand.stat().st_size > 5_000_000:
            installer = cand
            keep_installer = True
            log(f"📦 Dùng Python installer có sẵn trong gói: {cand.name}", logger)
            break
    if installer is None:
        installer = get_app_root() / "temp" / f"python-{PYTHON_VERSION}-amd64.exe"
        installer.parent.mkdir(parents=True, exist_ok=True)
        _download_file(PYTHON_INSTALLER_URL, installer, logger)

    cmd = [
        str(installer),
        "/quiet",
        "InstallAllUsers=0",
        "PrependPath=0",
        "Include_launcher=0",
        "Include_test=0",
        "Include_doc=0",
        "Include_pip=1",
        "Include_tcltk=1",
        f"TargetDir={dest}",
    ]
    log("🔧 Đang chạy trình cài Python silent vào runtime\\python ...", logger)
    result = subprocess.run(cmd, timeout=600)
    if not keep_installer:
        installer.unlink(missing_ok=True)
    if result.returncode != 0 or not _python_ok(exe, logger):
        raise RuntimeError(
            f"Cài Python portable thất bại (mã {result.returncode}). "
            f"Thiếu {exe} hoặc không import được tkinter/pip."
        )
    ensure_pip(exe, logger)
    log(f"✅ Đã cài Python portable: {exe}", logger)
    return exe


def write_launcher(logger: Optional[LogFn] = None) -> Path:
    root = get_app_root()
    bat = root / "Chay_App.bat"
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        "cd /d \"%~dp0\"\r\n"
        "set \"PATH=%~dp0bin;%~dp0runtime\\python;%~dp0runtime\\python\\Scripts;%PATH%\"\r\n"
        "if not exist \"%~dp0runtime\\python\\python.exe\" (\r\n"
        "  echo Chua cai dat. Hay chay setup.exe mot lan.\r\n"
        "  pause\r\n"
        "  exit /b 1\r\n"
        ")\r\n"
        "echo Dang mo Video Summarizer Pro...\r\n"
        "\"%~dp0runtime\\python\\python.exe\" \"%~dp0main.py\"\r\n"
        "if errorlevel 1 pause\r\n"
    )
    bat.write_text(content, encoding="utf-8")
    log(f"✅ Đã tạo Chay_App.bat (dùng Python trong runtime\\python, không dùng Python máy).", logger)
    _try_desktop_shortcut(root, bat, logger)
    return bat


def _try_desktop_shortcut(root: Path, bat: Path, logger: Optional[LogFn] = None) -> None:
    desktop = Path.home() / "Desktop"
    if not desktop.is_dir():
        desktop = Path.home() / "OneDrive" / "Desktop"
    if not desktop.is_dir():
        return
    lnk = desktop / "Video Summarizer Pro.lnk"
    ps = (
        "$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}'); "
        "$s.TargetPath = '{bat}'; "
        "$s.WorkingDirectory = '{root}'; "
        "$s.WindowStyle = 1; "
        "$s.Save()"
    ).format(
        lnk=str(lnk).replace("'", "''"),
        bat=str(bat).replace("'", "''"),
        root=str(root).replace("'", "''"),
    )
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            check=False,
            capture_output=True,
            timeout=30,
        )
        if lnk.is_file():
            log(f"✅ Đã tạo shortcut Desktop: {lnk.name}", logger)
    except Exception as exc:
        log(f"⚠️  Không tạo được shortcut Desktop ({exc}). Dùng Chay_App.bat.", logger)


def _copy_ffmpeg_binaries(extracted_root: Path, target_bin: Path) -> None:
    found = next(extracted_root.rglob("ffmpeg.exe"), None)
    if not found:
        raise FileNotFoundError("Gói FFmpeg không chứa ffmpeg.exe (không dùng bản giả).")
    target_bin.mkdir(parents=True, exist_ok=True)
    for name in ("ffmpeg.exe", "ffprobe.exe", "ffplay.exe"):
        src = found.parent / name
        if src.is_file():
            shutil.copy2(src, target_bin / name)
    if not (target_bin / "ffmpeg.exe").is_file():
        raise FileNotFoundError("Không copy được ffmpeg.exe.")


def _install_ffmpeg_from_zip(zip_path: Path, logger: Optional[LogFn] = None) -> None:
    extract_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_extract_"))
    try:
        log("📦 Đang giải nén FFmpeg...", logger)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)
        _copy_ffmpeg_binaries(extract_dir, bin_dir())
    finally:
        shutil.rmtree(extract_dir, ignore_errors=True)


def _try_winget_ffmpeg(logger: Optional[LogFn] = None) -> bool:
    winget = shutil.which("winget")
    if not winget:
        return False
    log("🔧 Thử cài FFmpeg bằng winget (Gyan.FFmpeg)...", logger)
    try:
        result = subprocess.run(
            [
                winget, "install", "-e", "--id", WINGET_FFMPEG_ID,
                "--accept-package-agreements", "--accept-source-agreements",
            ],
            check=False,
        )
        return result.returncode == 0 and bool(shutil.which("ffmpeg"))
    except Exception as exc:
        log(f"⚠️  winget thất bại: {exc}", logger)
        return False


def ensure_ffmpeg(logger: Optional[LogFn] = None) -> Path:
    apply_runtime_env()
    existing = ffmpeg_exe_path()
    if existing:
        log(f"✅ FFmpeg đã có — bỏ qua tải. ({existing})", logger)
        return existing
    if skip_download():
        raise RuntimeError("Thiếu FFmpeg nhưng ENSURE_RUNTIME_SKIP_DOWNLOAD=1 (không tải).")

    log("⏳ Đang tải FFmpeg (máy chưa có trên PATH / bin\\ffmpeg.exe)...", logger)
    errors = []
    tmp_zip = get_app_root() / "temp" / "ffmpeg_download.zip"
    tmp_zip.parent.mkdir(parents=True, exist_ok=True)

    for url in FFMPEG_URLS:
        try:
            _download_file(url, tmp_zip, logger)
            _install_ffmpeg_from_zip(tmp_zip, logger)
            tmp_zip.unlink(missing_ok=True)
            apply_runtime_env()
            path = ffmpeg_exe_path()
            if not path:
                raise RuntimeError("Đã giải nén nhưng không thấy ffmpeg.exe.")
            log(f"✅ Đã cài FFmpeg vào: {path}", logger)
            return path
        except Exception as exc:
            errors.append(f"{url} → {exc}")
            log(f"⚠️  Không tải được từ nguồn này: {exc}", logger)
            tmp_zip.unlink(missing_ok=True)

    if _try_winget_ffmpeg(logger):
        apply_runtime_env()
        path = ffmpeg_exe_path()
        if path:
            log(f"✅ FFmpeg đã cài qua winget: {path}", logger)
            return path

    raise RuntimeError(
        "Không cài được FFmpeg. Đã thử gyan.dev, BtbN GitHub và winget.\n"
        + "\n".join(errors)
    )


def _unwrap_github_zip(extract_dir: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    src = children[0] if len(children) == 1 else extract_dir
    if (src / "capcut_tts_api").is_dir():
        shutil.copytree(src, dest)
        return
    nested = next(extract_dir.rglob("capcut_tts_api/__init__.py"), None)
    if not nested:
        raise FileNotFoundError("Zip GitHub không chứa package capcut_tts_api.")
    shutil.copytree(nested.parent.parent, dest)


def _install_tts_into_venv(
    dest: Path, logger: Optional[LogFn], python_cmd: Optional[list]
) -> None:
    cmd = python_cmd or find_python()
    if not cmd:
        log("⚠️  Không pip được CapCut TTS — app vẫn import qua vendor\\capcut_tts.", logger)
        return
    try:
        log("📦 Đang cài CapCut TTS vào Python portable...", logger)
        subprocess.check_call(cmd + ["-m", "pip", "install", "-e", str(dest), "--quiet"])
        log("✅ Đã pip install capcut-tts-api.", logger)
    except Exception as exc:
        log(f"⚠️  pip install -e TTS lỗi ({exc}). App vẫn import qua vendor\\capcut_tts.", logger)


def ensure_capcut_tts(
    logger: Optional[LogFn] = None, python_cmd: Optional[list] = None
) -> Path:
    dest = tts_dir()
    if tts_dir_exists():
        log(f"✅ CapCut TTS đã có — bỏ qua tải. ({dest})", logger)
        _install_tts_into_venv(dest, logger, python_cmd)
        apply_runtime_env()
        return dest
    if skip_download():
        raise RuntimeError("Thiếu CapCut TTS nhưng ENSURE_RUNTIME_SKIP_DOWNLOAD=1 (không tải).")

    log("⏳ Đang tải CapCut TTS từ GitHub (K07VN/capcut-tts-api)...", logger)
    dest.parent.mkdir(parents=True, exist_ok=True)
    zip_path = get_app_root() / "temp" / "capcut_tts.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    last_error = None

    try:
        _download_file(CAPCUT_TTS_ZIP, zip_path, logger)
        extract_dir = Path(tempfile.mkdtemp(prefix="capcut_tts_"))
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            _unwrap_github_zip(extract_dir, dest)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
    except Exception as exc:
        last_error = exc
        log(f"⚠️  Tải zip GitHub thất bại: {exc}. Thử git clone...", logger)
        zip_path.unlink(missing_ok=True)
        git = shutil.which("git")
        if not git:
            raise RuntimeError(
                f"Không tải được CapCut TTS từ GitHub và máy không có git. Lỗi zip: {exc}"
            ) from exc
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        subprocess.check_call([git, "clone", "--depth", "1", CAPCUT_TTS_REPO, str(dest)])

    if not tts_dir_exists():
        raise RuntimeError(
            f"Đã tải CapCut TTS nhưng thiếu capcut_tts_api hoặc Voice.json tại {dest}. "
            f"Lỗi trước đó: {last_error}"
        )

    _install_tts_into_venv(dest, logger, python_cmd)
    apply_runtime_env()
    log(f"✅ Đã cài CapCut TTS vào: {dest}", logger)
    return dest


def ensure_python_deps(
    logger: Optional[LogFn] = None, python_cmd: Optional[list] = None
) -> None:
    cmd = python_cmd or find_python()
    if not cmd:
        raise RuntimeError("Chưa có Python portable — chạy ensure_portable_python trước.")
    req = get_app_root() / "requirements.txt"
    log("🚀 Đang cài thư viện vào Python portable...", logger)
    if req.is_file():
        pkgs = [
            line.strip()
            for line in req.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#") and line.strip().lower() != "pyinstaller"
        ]
    else:
        pkgs = list(REQUIRED_PACKAGES)
    for package in pkgs:
        log(f"📦 Đang kiểm tra và cài đặt: [{package}]...", logger)
        subprocess.check_call(cmd + ["-m", "pip", "install", package])
    log("✅ Đã cài xong thư viện Python.", logger)


def ensure_whisper_model(
    logger: Optional[LogFn] = None, python_cmd: Optional[list] = None
) -> None:
    if whisper_model_exists():
        log("✅ Whisper model 'base' đã có — bỏ qua tải.", logger)
        return
    if skip_download():
        log("⚠️  Thiếu Whisper nhưng SKIP_DOWNLOAD=1.", logger)
        return
    cmd = python_cmd or find_python()
    if not cmd:
        log("⚠️  Không có Python portable để tải Whisper.", logger)
        return
    log("⏳ Đang tải Whisper model 'base' (~150MB) vào runtime\\hf_cache ...", logger)
    env = os.environ.copy()
    env["HF_HOME"] = str(hf_cache_dir())
    try:
        subprocess.check_call(
            cmd + [
                "-c",
                "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')",
            ],
            env=env,
        )
        log("✅ Đã tải Whisper model 'base'.", logger)
    except Exception as exc:
        log(f"⚠️  Chưa tải sẵn Whisper ({exc}). App sẽ tự tải khi bốc sub lần đầu.", logger)


def ensure_sample_config(logger: Optional[LogFn] = None) -> None:
    root = get_app_root()
    example = root / "config.example.json"
    config = root / "config.json"
    if config.is_file():
        log("✅ Đã có config.json — không ghi đè (tránh mất API key).", logger)
        return
    if example.is_file():
        shutil.copy2(example, config)
        log("✅ Đã tạo config.json từ config.example.json (chưa có secret).", logger)


def ensure_all(
    logger: Optional[LogFn] = None,
    with_whisper: bool = True,
    python_cmd: Optional[list] = None,
) -> dict:
    _configure_stdio()
    apply_runtime_env()
    py = ensure_portable_python(logger)
    python_cmd = [str(py)]
    ensure_python_deps(logger, python_cmd)
    ffmpeg_path = ensure_ffmpeg(logger)
    tts_path = ensure_capcut_tts(logger, python_cmd)
    if with_whisper:
        ensure_whisper_model(logger, python_cmd)
    ensure_sample_config(logger)
    write_launcher(logger)
    apply_runtime_env()
    log("🎉 Setup xong. Mở app bằng Chay_App.bat (không cần cài Python).", logger)
    return {
        "app_root": str(get_app_root()),
        "python": str(py),
        "ffmpeg": str(ffmpeg_path),
        "ffmpeg_exists": ffmpeg_exists(),
        "tts_dir": str(tts_path),
        "tts_dir_exists": tts_dir_exists(),
        "whisper_ready": whisper_model_exists(),
    }


def runtime_status() -> dict:
    apply_runtime_env()
    py = portable_python_exe()
    return {
        "app_root": str(get_app_root()),
        "python_portable": str(py or ""),
        "python_ok": bool(py and _python_ok(py)),
        "ffmpeg_exists": ffmpeg_exists(),
        "ffmpeg_path": str(ffmpeg_exe_path() or ""),
        "tts_dir_exists": tts_dir_exists(),
        "tts_dir": str(tts_dir()),
        "whisper_ready": whisper_model_exists(),
        "capcut_tts_repo": CAPCUT_TTS_REPO,
        "ffmpeg_urls": list(FFMPEG_URLS),
        "python_installer_url": PYTHON_INSTALLER_URL,
    }


def print_status(logger: Optional[LogFn] = None) -> bool:
    info = runtime_status()
    log(f"📁 App root: {info['app_root']}", logger)
    log(
        f"{'✅' if info['python_ok'] else '❌'} Python portable: "
        f"{info['python_portable'] or 'chưa có'}",
        logger,
    )
    log(
        f"{'✅' if info['ffmpeg_exists'] else '❌'} FFmpeg: {info['ffmpeg_path'] or 'chưa có'}",
        logger,
    )
    log(
        f"{'✅' if info['tts_dir_exists'] else '❌'} CapCut TTS: {info['tts_dir']}",
        logger,
    )
    ready = "đã cache" if info["whisper_ready"] else "sẽ tải lúc setup"
    log(
        f"{'✅' if info['whisper_ready'] else '⚠️ '} Whisper base: {ready}",
        logger,
    )
    return bool(info["python_ok"] and info["ffmpeg_exists"] and info["tts_dir_exists"])