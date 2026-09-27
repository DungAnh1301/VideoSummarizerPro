# -*- coding: utf-8 -*-
"""Kiểm tra và tải update từ GitHub Releases."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

GITHUB_REPO = "DungAnh1301/VideoSummarizerPro"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
USER_AGENT = "VideoSummarizerPro-Updater/1.0"


def _ssl_context() -> ssl.SSLContext:
    """Gộp chứng chỉ Windows (kể cả antivirus/proxy) với CA bundle đi kèm."""
    context = ssl.create_default_context()
    try:
        import certifi
        context.load_verify_locations(cafile=certifi.where())
    except (ImportError, OSError):
        bundled_ca = app_root() / "runtime" / "python" / "Lib" / "site-packages" / "certifi" / "cacert.pem"
        if bundled_ca.is_file():
            try:
                context.load_verify_locations(cafile=str(bundled_ca))
            except OSError:
                pass
    return context


def app_root() -> Path:
    return Path(__file__).resolve().parent


def current_version() -> str:
    try:
        return str(json.loads((app_root() / "version.json").read_text(encoding="utf-8-sig"))["version"])
    except Exception:
        return "0.0.0"


def _version_tuple(value: str) -> tuple:
    clean = str(value or "0").strip().lower().lstrip("v").split("-", 1)[0]
    parts = []
    for item in clean.split("."):
        try:
            parts.append(int(item))
        except ValueError:
            parts.append(0)
    return tuple((parts + [0, 0, 0])[:3])


def is_newer(remote: str, local: str) -> bool:
    return _version_tuple(remote) > _version_tuple(local)


def _read_url(url: str, timeout: int = 20) -> bytes:
    request = Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
        return response.read()


def check_for_update() -> dict:
    release = json.loads(_read_url(LATEST_RELEASE_API).decode("utf-8"))
    assets = {asset.get("name", ""): asset for asset in release.get("assets", [])}
    manifest_asset = assets.get("latest.json")
    if not manifest_asset:
        raise RuntimeError("GitHub Release chưa có file latest.json.")
    manifest = json.loads(_read_url(manifest_asset["browser_download_url"]).decode("utf-8-sig"))
    package_name = manifest.get("package")
    package_asset = assets.get(package_name)
    if not package_asset:
        raise RuntimeError(f"GitHub Release thiếu gói cập nhật: {package_name}")
    manifest["download_url"] = package_asset["browser_download_url"]
    manifest["release_url"] = release.get("html_url", "")
    manifest["release_notes"] = manifest.get("notes") or release.get("body", "")
    manifest["current_version"] = current_version()
    manifest["available"] = is_newer(manifest.get("version", "0"), manifest["current_version"])
    return manifest


def download_update(manifest: dict, progress=None) -> Path:
    target_dir = app_root() / "update_download"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / str(manifest["package"])
    request = Request(manifest["download_url"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    with urlopen(request, timeout=60, context=_ssl_context()) as response, open(target, "wb") as stream:
        total = int(response.headers.get("Content-Length") or manifest.get("size") or 0)
        received = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            stream.write(chunk)
            digest.update(chunk)
            received += len(chunk)
            if progress:
                progress(received, total)
    expected = str(manifest.get("sha256") or "").lower().strip()
    actual = digest.hexdigest().lower()
    if not expected or actual != expected:
        target.unlink(missing_ok=True)
        raise RuntimeError("Gói cập nhật sai SHA-256 hoặc tải chưa hoàn chỉnh.")
    return target


def launch_updater(package_path: Path, target_version: str, runtime_update: bool = False) -> None:
    python_exe = app_root() / "runtime" / "python" / "python.exe"
    if not python_exe.is_file():
        python_exe = Path(sys.executable)
    cmd = [
        str(python_exe), str(app_root() / "updater.py"),
        "--package", str(package_path), "--target-version", str(target_version),
        "--pid", str(os.getpid()),
    ]
    if runtime_update:
        cmd.append("--runtime-update")
    kwargs = {"cwd": str(app_root())}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
    subprocess.Popen(cmd, **kwargs)
