# -*- coding: utf-8 -*-
"""Tiến trình độc lập thay file update, rollback khi lỗi và mở lại ứng dụng."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

PROTECTED_ROOTS = {
    "config.json", "config_part_splitter.json", "hardware_profile.json", "output", "temp", "runtime",
    "bin", "vendor", "data", "update_backup", "update_download", "queue.json",
}


def wait_for_process(pid: int, timeout: int = 45) -> None:
    if pid <= 0 or os.name != "nt":
        time.sleep(1)
        return
    for _ in range(timeout * 2):
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True, text=True, creationflags=0x08000000,
        )
        if str(pid) not in (result.stdout or ""):
            return
        time.sleep(0.5)
    raise RuntimeError("Tool chính chưa đóng nên không thể cập nhật an toàn.")


def safe_members(archive: zipfile.ZipFile) -> list:
    members = []
    for info in archive.infolist():
        name = info.filename.replace("\\", "/").lstrip("/")
        parts = [part for part in name.split("/") if part not in ("", ".")]
        if not parts or ".." in parts or ":" in parts[0]:
            raise RuntimeError(f"Đường dẫn không an toàn trong update: {info.filename}")
        if parts[0].lower() in PROTECTED_ROOTS:
            continue
        members.append((info, Path(*parts)))
    return members


def apply_update(package: Path, target_version: str, pid: int, runtime_update: bool = False) -> None:
    root = Path(__file__).resolve().parent
    wait_for_process(pid)
    backup = root / "update_backup" / f"before_{target_version}_{datetime.now():%Y%m%d_%H%M%S}"
    backup.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="vsp_update_", dir=str(root)))
    changed = []
    try:
        with zipfile.ZipFile(package, "r") as archive:
            members = safe_members(archive)
            for info, relative in members:
                destination = (stage / relative).resolve()
                if stage.resolve() not in destination.parents and destination != stage.resolve():
                    raise RuntimeError("Update cố ghi file ra ngoài thư mục tạm.")
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(destination, "wb") as output:
                    shutil.copyfileobj(source, output)

        for source in stage.rglob("*"):
            if not source.is_file():
                continue
            relative = source.relative_to(stage)
            destination = root / relative
            existed = destination.exists()
            if existed:
                old_copy = backup / relative
                old_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(destination, old_copy)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            changed.append((relative, existed))

        (root / "update_success.json").write_text(json.dumps({
            "version": target_version, "updated_at": datetime.now().isoformat(timespec="seconds"),
            "backup": str(backup), "files": [str(x[0]) for x in changed],
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Cập nhật thành công lên v{target_version}.")
    except Exception:
        for relative, existed in reversed(changed):
            old_copy = backup / relative
            destination = root / relative
            if old_copy.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(old_copy, destination)
            elif not existed and destination.is_file():
                destination.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)

    if runtime_update:
        setup_exe = root / "setup.exe"
        if setup_exe.is_file():
            print("Đang cập nhật thư viện runtime...")
            subprocess.run([str(setup_exe), "--no-pause"], cwd=str(root), check=True)

    launcher = root / "Chay_App.bat"
    if launcher.is_file():
        subprocess.Popen(["cmd.exe", "/c", "start", "", str(launcher)], cwd=str(root))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--pid", type=int, default=0)
    parser.add_argument("--runtime-update", action="store_true")
    args = parser.parse_args()
    try:
        apply_update(Path(args.package).resolve(), args.target_version, args.pid, args.runtime_update)
        return 0
    except Exception as exc:
        print(f"Cập nhật thất bại: {exc}")
        input("Bấm Enter để đóng...")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
