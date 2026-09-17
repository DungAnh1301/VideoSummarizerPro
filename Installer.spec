# -*- mode: python ; coding: utf-8 -*-
# Bootstrap gọn: không kéo pandas/numpy/opencv.
a = Analysis(
    ["installer_entry.py"],
    pathex=[],
    binaries=[],
    datas=[("config.example.json", ".")],
    hiddenimports=["ensure_runtime"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pandas", "numpy", "pygame", "cv2", "PIL", "Pillow",
        "torch", "faster_whisper", "matplotlib", "IPython",
        "notebook", "scipy", "sklearn", "pyarrow", "av",
        "onnxruntime", "ctranslate2", "yt_dlp", "edge_tts",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="setup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
