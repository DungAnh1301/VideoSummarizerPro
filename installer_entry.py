# -*- coding: utf-8 -*-
"""Entry setup.exe (slim): cài Python portable + pip + FFmpeg + CapCut TTS."""
from __future__ import annotations

import sys
import traceback

from ensure_runtime import _configure_stdio, ensure_all, print_status
from hardware_manager import detect_hardware


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pause = "--no-pause" not in argv and sys.stdin is not None and sys.stdin.isatty()
    _configure_stdio()
    print("=" * 60)
    print("  VIDEO SUMMARIZER PRO — CÀI ĐẶT")
    print("=" * 60)
    print("Tự cài Python (trong thư mục app), thư viện, FFmpeg, CapCut TTS.")
    print()
    try:
        ensure_all(with_whisper=True)
        print("\n🔎 Kiểm tra phần cứng và encoder một lần...")
        detect_hardware(force=True)
        print()
        print_status()
        print()
        print("👉 Mở app: shortcut Desktop  hoặc  Chay_App.bat")
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


if __name__ == "__main__":
    sys.exit(main())
