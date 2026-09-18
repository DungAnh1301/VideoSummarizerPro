import json
import os

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
CONFIG_SCHEMA_VERSION = 8


def normalize_hook_mode(value, use_custom_hook=False):
    """Chuẩn hóa ba kiểu hook, đồng thời đọc được config Hook AI đời cũ."""
    mode = str(value or "").strip().lower()
    aliases = {
        "manual": "native", "source": "native", "native": "native",
        "gemini": "gemini", "gemini_auto": "gemini",
        "ai": "ai", "custom": "ai", "custom_ai": "ai",
    }
    if mode in aliases:
        return aliases[mode]
    return "ai" if bool(use_custom_hook) else "native"


def normalize_broll_mode(value):
    """Giữ preset cũ chạy được, đồng thời chuẩn hóa về ba mode sản phẩm mới."""
    mode = str(value or "").strip().lower()
    aliases = {
        "opencv": "heatmap_ranked",
        "opencv_action": "heatmap_ranked",
        "timesub": "timesub_content",
        "semantic": "timesub_content",
        "timesub_semantic": "timesub_content",
        "heatmap_ranked": "heatmap_ranked",
        "heatmap_chronological": "heatmap_chronological",
        "timesub_content": "timesub_content",
    }
    return aliases.get(mode, "heatmap_ranked")


def normalize_mode_for_voice(value, use_ai_voice=True):
    """Khóa ma trận sản phẩm cho cả GUI, preset cũ và job đã lưu."""
    mode = normalize_broll_mode(value)
    if bool(use_ai_voice) and mode == "heatmap_chronological":
        return "timesub_content"
    if not bool(use_ai_voice) and mode == "timesub_content":
        return "heatmap_chronological"
    return mode


def normalize_config(data, defaults=None):
    """Nâng config cũ lên schema hiện tại mà không làm mất các thiết lập khác."""
    if not isinstance(data, dict):
        data = {}
    defaults = defaults or {}
    previous_version = int(data.get("config_schema_version", 0) or 0)

    for key, value in defaults.items():
        if key not in data:
            data[key] = value

    # Mốc mới: config tổng và mọi config có tên cũ đều mặc định 90 giây (hoặc 180s cho Tuyển tập).
    data.setdefault("workflow_mode", "single")
    wf = data.get("workflow_mode", "single")
    default_dur = 180.0 if wf == "compilation" else 90.0
    unified_dur = float(
        (data.get("compilation_target_duration") if wf == "compilation" else data.get("video_min_duration_sec"))
        or data.get("video_min_duration_sec")
        or data.get("compilation_target_duration")
        or default_dur
    )
    data["video_min_duration_sec"] = unified_dur
    data["compilation_target_duration"] = unified_dur

    data.setdefault("enable_title", True)
    data.setdefault("enable_sub", True)
    data.setdefault("scramble_original_audio", True)
    data.setdefault("gemini_grid_inspector", True)
    data.setdefault("auto_shot_detection", True)
    data.setdefault("compilation_source_type", "playlist")
    data.setdefault("compilation_source_path", "")
    data.setdefault("compilation_clip_count", 5)
    data.setdefault("compilation_title_style", "clean_original")
    data.setdefault("compilation_enable_teaser", True)
    data.setdefault("compilation_teaser_duration", 3.0)
    data.setdefault("compilation_rank_by", "views")
    data.setdefault("youtube_cookie_file", "")
    data.setdefault("use_youtube_cookies", True)
    saved_configs = data.get("saved_configs")
    if not isinstance(saved_configs, dict):
        saved_configs = {}
        data["saved_configs"] = saved_configs
    for payload in saved_configs.values():
        if isinstance(payload, dict):
            p_wf = payload.get("workflow_mode", "single")
            p_def = 180.0 if p_wf == "compilation" else 90.0
            p_dur = float(
                (payload.get("compilation_target_duration") if p_wf == "compilation" else payload.get("video_min_duration_sec"))
                or payload.get("video_min_duration_sec")
                or payload.get("compilation_target_duration")
                or p_def
            )
            payload["video_min_duration_sec"] = p_dur
            payload["compilation_target_duration"] = p_dur
            payload.setdefault("original_audio_target_sec", p_dur)
            payload.setdefault("workflow_mode", "single")
            payload.setdefault("compilation_source_type", "playlist")
            payload.setdefault("compilation_clip_count", 5)
            payload.setdefault("compilation_title_style", "clean_original")
            payload.setdefault("compilation_enable_teaser", True)
            payload.setdefault("compilation_teaser_duration", 3.0)
            payload.setdefault("compilation_rank_by", "views")
            payload.setdefault("enable_title", True)
            payload.setdefault("enable_sub", True)
            # Bảo đảm preset cũ cũng có đủ các trường Tab 3.
            payload.setdefault("broll_max_sec", 3.5)
            payload.setdefault("scramble_original_audio", True)
            payload.setdefault("gemini_grid_inspector", True)
            payload.setdefault("auto_shot_detection", True)
            payload.setdefault("scale_w", 100.0)
            payload.setdefault("scale_h", 100.0)
            payload.setdefault("title_y_pos", 260)
            payload.setdefault("title_color1", "black")
            payload.setdefault("title_color2", "red")
            payload.setdefault("title_outline_color", "none")
            payload.setdefault("title_bg_color", "white")
            payload.setdefault("sub_size", 22)
            payload.setdefault("sub_outline", 3)
            payload.setdefault("sub_margin_v", 100)
            payload.setdefault("sub_color", "&HFFFFFF&")
            payload.setdefault("sub_outline_color", "&H000000&")
            payload.setdefault("mix_original_audio_with_voice", False)
            payload.setdefault("original_audio_mix_percent", 50.0)
            payload["broll_mode"] = normalize_mode_for_voice(
                payload.get("broll_mode"), payload.get("use_ai_voice", True)
            )

    # Bảo đảm đồng bộ các trường Crop, Blur Mask, Color và Sub Title
    for d_obj in [data] + [p for p in saved_configs.values() if isinstance(p, dict)]:
        d_obj.setdefault("sub_style_type", "tiktok_slim")
        d_obj.setdefault("sub_shadow", 1)
        d_obj.setdefault("use_crop", False)
        d_obj.setdefault("crop_w", 1920)
        d_obj.setdefault("crop_h", 1080)
        d_obj.setdefault("crop_x", 0)
        d_obj.setdefault("crop_y", 0)
        d_obj.setdefault("crop_ratio", "Tự do")
        d_obj.setdefault("base_w", 1920)
        d_obj.setdefault("base_h", 1080)
        d_obj.setdefault("use_blur_mask", False)
        d_obj.setdefault("blur_shape", "Hình chữ nhật")
        d_obj.setdefault("blur_mask_x", 100)
        d_obj.setdefault("blur_mask_y", 100)
        d_obj.setdefault("blur_mask_w", 300)
        d_obj.setdefault("blur_mask_h", 150)
        d_obj.setdefault("color_look_stack", [])

    data["broll_mode"] = normalize_mode_for_voice(
        data.get("broll_mode"), data.get("use_ai_voice", True)
    )
    data["hook_mode"] = normalize_hook_mode(
        data.get("hook_mode"), data.get("use_custom_hook", False)
    )
    data["use_custom_hook"] = data["hook_mode"] == "ai"
    for payload in saved_configs.values():
        if isinstance(payload, dict):
            payload["hook_mode"] = normalize_hook_mode(
                payload.get("hook_mode"), payload.get("use_custom_hook", False)
            )
            payload["use_custom_hook"] = payload["hook_mode"] == "ai"

    # Schema v5: kho hình luôn giữ full Top 1→N. Tỷ lệ 200% chỉ là
    # mốc cảnh báo an toàn, tuyệt đối không phải giới hạn cắt kho.
    data.setdefault("visual_pool_ratio", 2.0)
    data.setdefault("narration_min_words_per_90s", 200)
    data.setdefault("narration_max_words_per_90s", 280)
    data.setdefault("narration_preferred_words_per_90s", 240)
    data.setdefault("narration_continuous", True)
    data.setdefault("visual_tail_seconds", 1.0)
    data.setdefault("mix_original_audio_with_voice", False)
    data.setdefault("original_audio_mix_percent", 50.0)
    data.setdefault("target_market", "US")
    data.setdefault("output_language", "English")
    data.setdefault("output_locale", "en-US")
    data.setdefault("auto_voice_locale", True)
    data.setdefault("preferred_voice_by_locale", {})
    for payload in saved_configs.values():
        if isinstance(payload, dict):
            payload.setdefault("target_market", "US")
            payload.setdefault("output_language", "English")
            payload.setdefault("output_locale", "en-US")
            payload.setdefault("auto_voice_locale", True)
            payload.setdefault("preferred_voice_by_locale", {})
    if previous_version < 5:
        data["ai_model"] = "Google Antigravity (Local)"
        for payload in saved_configs.values():
            if isinstance(payload, dict):
                payload["ai_model"] = "Google Antigravity (Local)"

    # Schema v2 bỏ toàn bộ prompt dài kiểu cũ. Ô mới bắt đầu trống và chỉ nhận
    # gợi ý màu sắc kể chuyện do người dùng nhập sau khi nâng cấp.
    if previous_version < 2:
        data["prompts"] = {"Tự nhận diện": ""}
        data["selected_prompt"] = "Tự nhận diện"
        for payload in saved_configs.values():
            if isinstance(payload, dict):
                payload["prompts"] = {"Tự nhận diện": ""}
                payload["selected_prompt"] = "Tự nhận diện"
                payload["prompt_content"] = ""

    if previous_version < 8:
        if data.get("broll_max_sec", 6.0) in (6.0, 10.0):
            data["broll_max_sec"] = 3.5
        data["scramble_original_audio"] = data.get("scramble_original_audio", True)
        data["gemini_grid_inspector"] = data.get("gemini_grid_inspector", True)
        data["auto_shot_detection"] = data.get("auto_shot_detection", True)
        for payload in saved_configs.values():
            if isinstance(payload, dict):
                if payload.get("broll_max_sec", 6.0) in (6.0, 10.0):
                    payload["broll_max_sec"] = 3.5
                payload["scramble_original_audio"] = payload.get("scramble_original_audio", True)
                payload["gemini_grid_inspector"] = payload.get("gemini_grid_inspector", True)
                payload["auto_shot_detection"] = payload.get("auto_shot_detection", True)

    data["config_schema_version"] = CONFIG_SCHEMA_VERSION
    return data

def load_config():
    default_config = {
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "workflow_mode": "single",
        "compilation_source_type": "playlist",
        "compilation_source_path": "",
        "compilation_clip_count": 5,
        "compilation_target_duration": 180.0,
        "compilation_title_style": "clean_original",
        "compilation_enable_teaser": True,
        "compilation_teaser_duration": 3.0,
        "compilation_rank_by": "views",
        "youtube_cookie_file": "",
        "use_youtube_cookies": True,
        "hook_duration": "9.0",
        "hook_mode": "native",
        "part_count": 1,
        "part_cut_times": [],
        "ai_model": "Google Antigravity (Local)",
        "api_key": "",
        "prompts": {
            "Tự nhận diện": ""
        },
        "selected_prompt": "Tự nhận diện",
        "engine_tts": "Edge-TTS (Miễn phí)",
        "voice": "Google Translate TTS (Miễn phí)",
        "target_market": "US",
        "output_language": "English",
        "output_locale": "en-US",
        "auto_voice_locale": True,
        "preferred_voice_by_locale": {},
        "use_ai_voice": True,
        "mix_original_audio_with_voice": False,
        "original_audio_mix_percent": 50.0,
        "video_min_duration_sec": 90.0,
        "original_audio_target_sec": 90.0,
        "broll_mode": "heatmap_ranked",
        "broll_max_sec": 3.5,
        "scramble_original_audio": True,
        "gemini_grid_inspector": True,
        "auto_shot_detection": True,
        "visual_pool_ratio": 2.0,
        "narration_min_words_per_90s": 200,
        "narration_max_words_per_90s": 280,
        "narration_preferred_words_per_90s": 240,
        "narration_continuous": True,
        "visual_tail_seconds": 1.0,
        "scale_w": 100.0,
        "scale_h": 100.0,
        "title_y_pos": 260,
        "title_color1": "black",
        "title_color2": "red",
        "title_outline_color": "none",
        "title_bg_color": "white",
        "sub_size": 22,
        "sub_outline": 3,
        "sub_margin_v": 100,
        "sub_color": "&HFFFFFF&",
        "sub_outline_color": "&H000000&",
        "cleanup_temp_after_export": False,
        "color_look": "Không lọc",
        "color_look_intensity": 70,
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
        "use_crop": False,
        "crop_w": 1920,
        "crop_h": 1080,
        "crop_x": 0,
        "crop_y": 0,
        "crop_ratio": "Tự do",
        "base_w": 1920,
        "base_h": 1080,
        "use_blur_mask": False,
        "blur_shape": "Hình chữ nhật",
        "blur_mask_x": 100,
        "blur_mask_y": 100,
        "blur_mask_w": 300,
        "blur_mask_h": 150,
        "sub_style_type": "tiktok_slim",
        "sub_shadow": 1,
        "saved_configs": {},
        "selected_config": "",
        "queue_auto_start": True,
        "show_cmd_log": True,
        "auto_check_updates": True,
    }
    
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return normalize_config(data, default_config)
        except Exception:
            return default_config
    return default_config

def save_config(config_data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Lỗi lưu config: {e}")
