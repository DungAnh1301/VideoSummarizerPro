"""
CẤU HÌNH ĐỘC LẬP CHO CHẾ ĐỘ 3: BIÊN TẬP & RE-MIX VIDEO TIKTOK ĐỐI THỦ (TIKTOK SHORT REMIXER)
Tách biệt hoàn toàn với config.json và config_part_splitter.json.
"""
import os
import json
import copy

CONFIG_TIKTOK_FILE = os.path.join(os.path.dirname(__file__), "config_tiktok_remixer.json")

DEFAULT_TIKTOK_REMIXER_CONFIG = {
    # 1. Nguồn & Thị trường
    "is_tiktok_remixer": True,
    "source_url_or_path": "",
    "target_market": "DE",
    "auto_voice_locale": True,
    
    # 2. Tẩy dấu vết đối thủ (Gemini CLI Auto-Clean)
    "auto_clean_core_crop": True,         # Cắt bỏ 2 đầu (Title cũ + Nền mờ) lấy lõi sạch
    "auto_blur_sub_part": True,           # Bôi mờ dải Sub cũ và Badge Part 2 ở đáy
    "qc_blur_strength": 75,               # Độ mờ 75%
    "ai_inspect_intro_outro": True,       # AI tự kiểm tra và cắt logo TikTok 2 đầu (0.0s nếu sạch)
    
    # 3. Phân cảnh & Cắt chuyển cảnh thông minh
    "keep_original_hook": True,           # Tách & Giữ lại Hook gốc đầu video (Hình + Tiếng kịch tính)
    "smart_transition_cutout": True,      # Cắt chuyển cảnh rác (0.15s - 0.22s), Hard cut giữ nguyên 0.0s
    "elastic_broll_speed": True,          # Điều tốc đàn hồi 0.95x trên cảnh lẻ khi thiếu hình
    
    # 4. Re-mix chống bản quyền
    "shuffle_broll": True,                # Đảo trật tự cảnh b-roll hợp lý
    "mirror_broll": True,                 # Lật gương phản chiếu (hflip)
    "overlay_sub_on_blur_zone": True,     # Đè Sub mới chính xác lên đúng tọa độ Sub cũ đã blur
    "blur_bg_916": True,                  # Tạo nền mờ 9:16 điện ảnh phía sau lõi video sạch
    
    # 5. AI Kịch bản & Giọng đọc
    "use_ai_voice": True,
    "ai_model": "Google Antigravity (Local)",
    "engine_tts": "CapCut TTS",
    "voice_name": "de-DE-ConradNeural",
    "tts_speed": "+0%",
    "story_style": "Kịch tính / Giật gân",
    
    # 6. Màu sắc CapCut 15 thông số & Preset Look
    "color_look": "8K",
    "color_look_intensity": 70,
    "color_look_stack": [{"name": "8K", "intensity": 70}],
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

    # 6.1 Khung hình, Tốc độ, Zoom & Scale nhẹ né quét bản quyền TikTok (mặc định 105%, không méo hình)
    "speed": 1.05,
    "audio_boost": 6.0,
    "zoom_in": True,
    "zoom_percent": 105.0,
    "scale_x": 100.0,
    "scale_y": 100.0,
    "scale_w": 100.0,
    "scale_h": 100.0,
    "blur_bg": True,
    
    # 7. Title & Subtitle Studio
    "enable_title": True,
    "title_y_pos": 260,
    "title_color1": "#000000",
    "title_color2": "#FF0000",
    "title_outline_color": "none",
    "title_bg_color": "#FFFFFF",
    
    "enable_sub": True,
    "sub_style_type": "tiktok_slim",
    "sub_size": 38,
    "sub_outline": 4,
    "sub_shadow": 1,
    "sub_margin_v": 100,
    "sub_color": "&H00FFFF&",
    "sub_outline_color": "&H000000&",
    
    # 8. Quản lý lưu config mẫu
    "selected_config": "default",
    "saved_configs": {
        "default": {}
    }
}

def load_tiktok_remixer_config() -> dict:
    """Tải cấu hình riêng biệt cho Chế Độ 3 với cơ chế tự phục hồi nếu file bị ngắt quãng."""
    config = copy.deepcopy(DEFAULT_TIKTOK_REMIXER_CONFIG)
    if os.path.exists(CONFIG_TIKTOK_FILE):
        try:
            with open(CONFIG_TIKTOK_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                config.update(saved)
                # Đảm bảo saved_configs luôn có cấu trúc đúng
                if "saved_configs" not in config or not isinstance(config["saved_configs"], dict):
                    config["saved_configs"] = {"default": {}}
        except (json.JSONDecodeError, ValueError) as err:
            # Tự động phục hồi nếu file bị ngắt quãng do crash trước đây
            try:
                corrupt_backup = CONFIG_TIKTOK_FILE + ".bak"
                if os.path.exists(corrupt_backup):
                    os.remove(corrupt_backup)
                os.rename(CONFIG_TIKTOK_FILE, corrupt_backup)
            except Exception:
                pass
            save_tiktok_remixer_config(config)
            print("[TikTok Config] Da tu dong phuc hoi cau hinh mac dinh cho Che Do 3.")
        except Exception as e:
            print(f"[TikTok Config] Loi doc config: {e}")
    else:
        # Nếu chưa có file, tự động khởi tạo file chuẩn
        save_tiktok_remixer_config(config)
    return config

def save_tiktok_remixer_config(config: dict) -> bool:
    """Lưu cấu hình riêng biệt cho Chế Độ 3 an toàn bằng Atomic Write (chống ngắt file)."""
    try:
        # Làm sạch config để triệt tiêu mọi tham chiếu lặp (circular reference)
        clean_config = {}
        for k, v in config.items():
            if k == "saved_configs" and isinstance(v, dict):
                clean_saved = {}
                for s_name, s_val in v.items():
                    if isinstance(s_val, dict):
                        clean_saved[s_name] = {sk: copy.deepcopy(sv) for sk, sv in s_val.items() if sk != "saved_configs"}
                    else:
                        clean_saved[s_name] = copy.deepcopy(s_val)
                clean_config[k] = clean_saved
            elif k != "saved_configs":
                clean_config[k] = copy.deepcopy(v)

        # 1. Chuyển đổi thành chuỗi JSON trước trong RAM để kiểm tra toàn vẹn
        json_str = json.dumps(clean_config, ensure_ascii=False, indent=2)

        # 2. Ghi ra file tạm rồi đổi tên nguyên tử (Atomic Write) tránh bị file dở dang
        temp_file = CONFIG_TIKTOK_FILE + ".tmp"
        with open(temp_file, "w", encoding="utf-8") as f:
            f.write(json_str)
        os.replace(temp_file, CONFIG_TIKTOK_FILE)
        return True
    except Exception as e:
        print(f"[TikTok Config] Loi luu config: {e}")
        return False
