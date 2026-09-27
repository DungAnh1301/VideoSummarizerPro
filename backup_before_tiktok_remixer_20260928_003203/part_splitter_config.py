"""Quản lý cấu hình độc lập cho Chế độ Chia Part & Tinh Lược Video Gốc.

Lưu trữ riêng biệt tại config_part_splitter.json, hỗ trợ hệ thống Config có tên (Named Configs),
hoàn toàn không xung đột với file config.json của Chế độ Tóm Tắt.
"""
from __future__ import annotations

import json
import os
import copy
from pathlib import Path
from typing import Dict, Any, List

CONFIG_FILE_NAME = "config_part_splitter.json"

DEFAULT_PART_CONFIG: Dict[str, Any] = {
    # 0. Chế độ làm (Workflow Mode)
    "workflow_mode": "single",            # 'single' (1 video đơn lẻ) hoặc 'compilation' (tuyển tập playlist top countdown)
    "playlist_url": "",                   # URL YouTube Playlist
    "compilation_source_path": "",
    "compilation_clip_count": 5,          # Số clip bốc ra từ playlist làm Top Highlight
    "compilation_target_duration": 180.0, # Tổng thời lượng mong muốn (giây), AI sẽ tinh lược đoạn thừa từng clip
    "compilation_duration_tolerance": 0.20, # Linh hoạt +-20% thời lượng tổng theo nhịp cao trào
    "playlist_title_y_pos": 160,          # Vị trí trên cùng màn hình cho Tên Playlist
    "clip_badge_y_pos": 960,              # Vị trí giữa màn hình cho No. X : Tên video
    "compilation_title_style": "clean_original",
    "compilation_enable_teaser": False,
    "compilation_teaser_duration": 3.0,
    "compilation_rank_by": "views",       # 'views', 'likes', hoặc 'random'

    # 1. Nguồn & Hook
    "source_mode": "youtube",             # 'youtube' hoặc 'local'
    "youtube_url": "https://www.youtube.com/watch?v=...",
    "local_source_path": "",
    "hook_mode": "individual",            # 'native', 'shared' (toàn cảnh), 'individual' (từng part), 'ai'
    "hook_time": "11:55",
    "hook_duration": 6.0,
    "hook_strategy": "individual",        # 'shared' hoặc 'individual'
    "hook_target_sec": 6.0,
    "hook_tolerance": 0.50,               # Dao động +-50% (3s ~ 9s)

    # 2. Cấu hình chia Part & Cliffhanger
    "part_count": 4,
    "min_part_duration_sec": 60.0,
    "part_tolerance": 0.50,               # Dao động +-50%
    "auto_regenerate_title": True,        # AI tự động đặt lại tiêu đề viral theo ngôn ngữ gốc
    "gemini_auth_mode": "antigravity",    # 'antigravity' hoặc 'api_key'
    "gemini_model": "gemini-2.5-flash",
    "gemini_api_key": "",

    # 3. Cấu hình tinh lược (Prune)
    "prune_enabled": True,
    "prune_mode": "percent",              # 'percent' hoặc 'minutes'
    "prune_percent": 20.0,
    "prune_minutes": 15.0,
    "prune_tolerance": 0.20,              # Hạn ngạch co giãn +-20%

    # 5. Hậu kỳ video (Đồng bộ 100% với Tóm Tắt Video)
    "part_label_prefix": "Part",          # Tiền tố: 'Part', 'Teil', 'Partie', 'Часть', 'Tập'...
    "source_speed": 1.05,                 # Tốc độ video (1.05x - 1.20x)
    "speed": 1.05,
    "audio_boost": 6.0,
    "apply_capcut_limiter": True,         # Áp dụng CapCut Limiter +20dB
    "apply_subtle_zoom": True,            # Subtle Zoom (1.03x) lách bản quyền
    "random_mirror": False,               # Lật gương hình ảnh
    "blur_bg": True,
    "zoom_in": True,
    "zoom_percent": 115.0,
    "scale_w": 100.0,
    "scale_h": 100.0,
    "pos_x": 0,
    "pos_y": 0,
    "use_crop": False,
    "base_w": 1920,
    "base_h": 1080,
    "crop_w": 1920,
    "crop_h": 1080,
    "crop_x": 0,
    "crop_y": 0,
    "crop_ratio": "Tự do",
    "crop_top": 0,
    "crop_bottom": 0,
    "crop_left": 0,
    "crop_right": 0,
    "use_blur_mask": False,
    "blur_shape": "Hình chữ nhật",
    "blur_mask_x": 20,
    "blur_mask_y": 861,
    "blur_mask_w": 932,
    "blur_mask_h": 210,
    "color_look": "Không lọc",
    "color_look_intensity": 70.0,
    "color_look_stack": [],
    "temperature": 0.0,
    "tint": 0.0,
    "saturation": 0.0,
    "exposure": 0.0,
    "contrast": 0.0,
    "highlights": 0.0,
    "shadows": 0.0,
    "whites": 0.0,
    "blacks": 0.0,
    "brilliance": 0.0,
    "sharpen": 0.0,
    "clarity": 0.0,
    "grain": 0.0,
    "blur": 0.0,
    "vignette": 0.0,
    "title_y_pos": 260,
    "title_color1": "black",
    "title_color2": "red",
    "title_outline_color": "none",
    "title_bg_color": "white",
    "sub_style_type": "tiktok_slim",
    "sub_size": 16,
    "sub_outline": 2,
    "sub_shadow": 1,
    "sub_margin_v": 100,
    "sub_color": "&HFFFFFF&",
    "sub_outline_color": "&H000000&",
    "gemini_grid_inspector": True,        # Quét lưới AI xóa logo & sub cũ
    "qc_blur_strength": 75,               # Độ mờ che logo / sub AI (10% - 100%)
    "cleanup_temp_after_export": False,

    # 6. Đường dẫn xuất
    "output_dir": "output/parts",

    # 7. Quản lý cấu hình có tên (Named Configs)
    "saved_configs": {},
    "selected_config": "",
}


def _get_config_path(custom_path: str = "") -> str:
    if custom_path:
        return os.path.abspath(custom_path)
    base_dir = Path(__file__).resolve().parent
    return str(base_dir / CONFIG_FILE_NAME)


def _recover_from_backup_if_needed(base_dir: Path, config: Dict[str, Any]) -> bool:
    """
    Tự động khôi phục saved_configs và cấu hình quan trọng từ update_backup
    nếu phát hiện config_part_splitter.json bị ghi đè sau khi cập nhật phiên bản.
    """
    if config.get("saved_configs") and len(config["saved_configs"]) > 0:
        return False

    backup_dir = base_dir / "update_backup"
    if not backup_dir.is_dir():
        return False

    candidates = list(backup_dir.glob("**/config_part_splitter.json"))
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for c in candidates:
        try:
            with open(c, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("saved_configs") and len(data["saved_configs"]) > 0:
                config["saved_configs"] = data["saved_configs"]
                if data.get("selected_config"):
                    config["selected_config"] = data["selected_config"]
                for key in ("gemini_api_key", "output_dir", "part_label_prefix", "source_speed", "audio_boost"):
                    if data.get(key) and not config.get(key):
                        config[key] = data[key]
                print(f"♻️ [PART SPLITTER] Đã tự động khôi phục {len(data['saved_configs'])} cấu hình chia part từ bản sao lưu: {c}")
                return True
        except Exception:
            pass
    return False


def load_part_config(config_path: str = "") -> Dict[str, Any]:
    """Nạp cấu hình chia part từ file JSON, tự động bù mặc định nếu thiếu trường."""
    path = _get_config_path(config_path)
    config = copy.deepcopy(DEFAULT_PART_CONFIG)
    base_dir = Path(path).resolve().parent
    
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                if isinstance(saved, dict):
                    for k, v in saved.items():
                        config[k] = v
        except Exception:
            pass

    # Cấu hình Chia Part hoàn toàn độc lập với Tóm Tắt Video, không thừa kế ngầm để tránh ghi đè chéo

    # Nếu zoom_percent khác 100%, tự động bật zoom_in
    try:
        zv = float(config.get("zoom_percent", 100.0) or 100.0)
        if abs(zv - 100.0) >= 0.1:
            config["zoom_in"] = True
    except Exception:
        pass

    # Tự động cứu lại cấu hình từ update_backup nếu bị mất do update bản cũ
    if _recover_from_backup_if_needed(base_dir, config):
        save_part_config(config, config_path)

    # Đảm bảo kiểu dữ liệu an toàn
    try:
        config["part_count"] = max(2, int(config.get("part_count", 4)))
        config["min_part_duration_sec"] = max(10.0, float(config.get("min_part_duration_sec", 60.0)))
        config["part_tolerance"] = max(0.05, min(0.90, float(config.get("part_tolerance", 0.50))))
        config["prune_percent"] = max(1.0, min(90.0, float(config.get("prune_percent", 20.0))))
        config["prune_minutes"] = max(0.5, float(config.get("prune_minutes", 15.0)))
        config["prune_tolerance"] = max(0.05, min(0.80, float(config.get("prune_tolerance", 0.20))))
        config["hook_target_sec"] = max(2.0, min(30.0, float(config.get("hook_target_sec", 6.0))))
        config["hook_duration"] = float(config.get("hook_duration", 6.0))
        config["hook_tolerance"] = max(0.10, min(0.80, float(config.get("hook_tolerance", 0.50))))
        config["source_speed"] = max(1.0, min(2.0, float(config.get("source_speed", 1.05))))
        config["speed"] = float(config.get("speed", 1.05))
    except Exception:
        pass

    return config


def save_part_config(cfg: Dict[str, Any], config_path: str = "") -> None:
    """Lưu cấu hình an toàn xuống file JSON."""
    path = _get_config_path(config_path)
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        tmp_path = f"{path}.tmp_{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if os.path.isfile(path):
            os.replace(tmp_path, path)
        else:
            os.rename(tmp_path, path)
    except Exception:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def save_named_part_config(name: str, payload: Dict[str, Any], config_path: str = "") -> None:
    """Lưu cấu hình có tên vào config_part_splitter.json."""
    if not name:
        return
    cfg = load_part_config(config_path)
    saved = cfg.setdefault("saved_configs", {})
    saved[name] = copy.deepcopy(payload)
    cfg["selected_config"] = name
    save_part_config(cfg, config_path)


def delete_named_part_config(name: str, config_path: str = "") -> None:
    """Xóa cấu hình có tên."""
    if not name:
        return
    cfg = load_part_config(config_path)
    saved = cfg.get("saved_configs", {})
    if name in saved:
        del saved[name]
        if cfg.get("selected_config") == name:
            cfg["selected_config"] = next(iter(saved.keys()), "")
        save_part_config(cfg, config_path)


def get_saved_part_config_names(config_path: str = "") -> List[str]:
    """Lấy danh sách tên các cấu hình Chia Part đã lưu."""
    cfg = load_part_config(config_path)
    return list(cfg.get("saved_configs", {}).keys())
