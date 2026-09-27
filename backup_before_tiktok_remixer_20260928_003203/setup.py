# -*- coding: utf-8 -*-
"""Cài đặt 1 lần: Python portable + pip + FFmpeg + CapCut TTS. Ra setup.exe bằng build_setup.ps1."""
from __future__ import annotations

import argparse
import sys
import traceback

from ensure_runtime import (
    REQUIRED_PACKAGES,
    _configure_stdio,
    ensure_all,
    print_status,
)
from hardware_manager import detect_hardware

__all__ = [
    "REQUIRED_PACKAGES",
    "install_dependencies",
    "run_setup",
    "ffmpeg_exists",
    "tts_dir_exists",
]


def _reexport_checks():
    from ensure_runtime import ffmpeg_exists as _ff
    from ensure_runtime import tts_dir_exists as _tts
    return _ff, _tts


ffmpeg_exists, tts_dir_exists = _reexport_checks()


def install_dependencies():
    from ensure_runtime import ensure_portable_python, ensure_python_deps, log
    log("🚀 Đang cài Python portable và thư viện...")
    py = ensure_portable_python()
    ensure_python_deps(python_cmd=[str(py)])
    log("✅ Xong.")


def run_setup(with_whisper: bool = True, pause: bool = False) -> int:
    _configure_stdio()
    print("=" * 60)
    print("  VIDEO SUMMARIZER PRO — CÀI ĐẶT 1 LẦN")
    print("=" * 60)
    print("Tự cài: Python (trong thư mục app), thư viện, FFmpeg, CapCut TTS.")
    print("Không cần cài Python trên máy. Đã có thì bỏ qua.")
    print()
    try:
        ensure_all(with_whisper=with_whisper)
        print("\n🔎 Kiểm tra phần cứng và encoder một lần...")
        detect_hardware(force=True)
        print()
        print_status()
        print()
        print("👉 Xong. Double-click  Chay_App.bat  để mở app.")
        if pause:
            try:
                input("\nBấm Enter để thoát...")
            except EOFError:
                pass
        return 0
    except Exception as exc:
        print(f"\n❌ Setup thất bại: {exc}")
        traceback.print_exc()
        if pause:
            try:
                input("\nBấm Enter để thoát...")
            except EOFError:
                pass
        return 1


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Cài đặt Video Summarizer Pro")
    parser.add_argument("--ensure", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-whisper", action="store_true")
    parser.add_argument("--no-pause", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    _configure_stdio()
    args = _parse_args(argv)
    pause = not args.no_pause and sys.stdin is not None and sys.stdin.isatty()
    if args.check:
        ok = print_status()
        return 0 if ok else 1
    return run_setup(with_whisper=not args.no_whisper, pause=pause)


if __name__ == "__main__":
    sys.exit(main())
