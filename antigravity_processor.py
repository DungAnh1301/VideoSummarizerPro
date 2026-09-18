"""Local video analysis through the signed-in Google Antigravity CLI.

The process is deliberately scoped to one job directory.  It receives the
360p proxy for visual understanding while the editor continues to render from
the HD source.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
import time
import re
import tempfile
import uuid
from pathlib import Path


logger = logging.getLogger("AntigravityProcessor")


class AntigravityProcessor:
    _install_lock = threading.Lock()
    DEFAULT_EXE = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "agy", "bin", "agy.exe"
    )
    TIERED_PROFILES = [
        {"model": "gemini-3.8-flash-high", "effort": "high", "label": "Gemini 3.8 Flash (High)"},
        {"model": "gemini-3.8-flash-low", "effort": "low", "label": "Gemini 3.8 Flash (Low fallback)"},
        {"model": "gemini-3.7-flash-high", "effort": "high", "label": "Gemini 3.7 Flash (High fallback)"},
        {"model": "gemini-3.7-flash-low", "effort": "low", "label": "Gemini 3.7 Flash (Low fallback)"},
        {"model": "gemini-3.6-flash-low", "effort": "low", "label": "Gemini 3.6 Flash (Emergency fallback)"},
    ]

    @classmethod
    def get_candidate_profiles(cls, frame_count: int = 0) -> list[dict]:
        override_model = str(os.environ.get("AGY_MODEL") or "").strip()
        override_effort = str(os.environ.get("AGY_EFFORT") or "").strip()

        # Quy tắc lựa chọn mô hình theo số lượng frame gửi tới Antigravity CLI:
        # - Nếu frame_count <= 200 (hoặc text thuần): Ưu tiên số 1 là Gemini 3.8 Flash (High).
        # - Nếu frame_count > 200: Tự động chuyển ưu tiên số 1 sang Gemini 3.8 Flash (Low fallback)
        #   để tránh lỗi quá tải token / reasoning dẫn tới 503 No capacity available for model gemini-3.8-flash-high.
        if frame_count > 200:
            profiles = [
                {"model": "gemini-3.8-flash-low", "effort": "low", "label": "Gemini 3.8 Flash (Low fallback)"},
                {"model": "gemini-3.7-flash-low", "effort": "low", "label": "Gemini 3.7 Flash (Low fallback)"},
                {"model": "gemini-3.6-flash-low", "effort": "low", "label": "Gemini 3.6 Flash (Emergency fallback)"},
                {"model": "gemini-3.8-flash-high", "effort": "high", "label": "Gemini 3.8 Flash (High fallback)"},
                {"model": "gemini-3.7-flash-high", "effort": "high", "label": "Gemini 3.7 Flash (High fallback)"},
            ]
        else:
            profiles = [dict(p) for p in cls.TIERED_PROFILES]

        if override_model:
            custom_effort = override_effort or ("high" if "high" in override_model.lower() else "low")
            custom_prof = {
                "model": override_model,
                "effort": custom_effort,
                "label": f"{override_model} ({custom_effort.capitalize()} - Custom)"
            }
            profiles = [p for p in profiles if p["model"] != override_model]
            profiles.insert(0, custom_prof)
        return profiles

    @classmethod
    def executable(cls) -> str:
        # Không để AGY_EXE cũ/sai chặn toàn bộ fallback. Bản đóng gói và máy
        # Windows mới có thể có LOCALAPPDATA/PATH khác với tiến trình GUI.
        app_root = Path(__file__).resolve().parent
        candidates = [
            str(os.environ.get("AGY_EXE") or "").strip(),
            cls.DEFAULT_EXE,
            str(Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"),
            str(app_root / "runtime" / "agy" / "bin" / "agy.exe"),
            str(app_root / "bin" / "agy.exe"),
            shutil.which("agy") or "",
            shutil.which("agy.exe") or "",
        ]
        seen = set()
        for candidate in candidates:
            candidate = os.path.abspath(os.path.expandvars(os.path.expanduser(candidate))) \
                if candidate else ""
            key = os.path.normcase(candidate)
            if not candidate or key in seen:
                continue
            seen.add(key)
            if os.path.isfile(candidate):
                return candidate
        return ""

    @classmethod
    def searched_locations(cls) -> list[str]:
        """Danh sách gợi ý dùng trong thông báo cài đặt, không chứa bí mật."""
        app_root = Path(__file__).resolve().parent
        return [
            cls.DEFAULT_EXE,
            str(Path.home() / "AppData" / "Local" / "agy" / "bin" / "agy.exe"),
            str(app_root / "runtime" / "agy" / "bin" / "agy.exe"),
            str(app_root / "bin" / "agy.exe"),
        ]

    @classmethod
    def installer_script(cls) -> str:
        """Return the bundled official installer used on a fresh Windows PC."""
        app_root = Path(__file__).resolve().parent
        candidates = [
            app_root / "runtime" / "antigravity-install.cmd",
            app_root / "antigravity-install.cmd",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return ""

    @classmethod
    def ensure_installed(cls, timeout: int = 900) -> str:
        """Find agy, or install it automatically with the bundled installer."""
        exe = cls.executable()
        if exe:
            return exe
        if os.name != "nt":
            raise RuntimeError("Antigravity CLI tự cài hiện chỉ hỗ trợ Windows.")

        with cls._install_lock:
            exe = cls.executable()
            if exe:
                return exe
            installer = cls.installer_script()
            if not installer:
                raise RuntimeError(
                    "Thiếu bộ cài Antigravity đi kèm ứng dụng "
                    "(runtime\\antigravity-install.cmd)."
                )
            logger.info("📦 [ANTIGRAVITY] Máy chưa có CLI — đang tự tải và cài đặt...")
            comspec = os.environ.get("COMSPEC") or "cmd.exe"
            completed = subprocess.run(
                [comspec, "/d", "/c", installer],
                cwd=str(Path(installer).parent), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=0x08000000,
            )
            exe = cls.executable()
            if completed.returncode != 0 or not exe:
                detail = (completed.stderr or completed.stdout or "unknown error").strip()
                raise RuntimeError(
                    "Không thể tự cài Antigravity CLI. Kiểm tra Internet rồi thử lại.\n"
                    + detail[-1200:]
                )
            logger.info("✅ [ANTIGRAVITY] Đã tự cài CLI: %s", exe)
            return exe

    @classmethod
    def analyze_prompt(cls, system_prompt: str, user_prompt: str, job_dir: str = "") -> str:
        """Thực thi prompt văn bản thuần qua Antigravity CLI (cho Countdown, kịch bản batch, text summarization)."""
        exe = cls.ensure_installed()
        if not job_dir:
            job_dir = os.path.join(tempfile.gettempdir(), f"agy_prompt_{uuid.uuid4().hex[:8]}")
        job_dir = os.path.abspath(job_dir)
        analysis_dir = os.path.join(job_dir, "ai_prompt")
        os.makedirs(analysis_dir, exist_ok=True)

        request_path = os.path.join(analysis_dir, "antigravity_request.txt")
        request_body = (
            "You are an AI assistant helping with video content analysis, scripting and storytelling.\n"
            "Do not edit files and do not run unrelated commands. Return only the JSON required by the instructions.\n\n"
            f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER INPUT:\n{user_prompt}\n"
        )
        Path(request_path).write_text(request_body, encoding="utf-8")
        short_request = (
            "Read antigravity_request.txt in the current directory. Follow all instructions in the request file and return ONLY the required JSON immediately."
        )
        candidate_profiles = cls.get_candidate_profiles()
        max_retries = max(3, len(candidate_profiles))
        last_error = None
        for attempt in range(1, max_retries + 1):
            prof = candidate_profiles[(attempt - 1) % len(candidate_profiles)]
            model_name = prof["model"]
            effort = prof["effort"]
            command = [
                exe, "--print", short_request,
                "--model", model_name,
                "--output-format", "json",
                "--effort", effort,
                "--add-dir", analysis_dir,
                "--dangerously-skip-permissions",
            ]
            if attempt == 1:
                logger.info("🧠 [ANTIGRAVITY PROMPT] Gửi yêu cầu tới Antigravity (Ưu tiên: %s)...", prof["label"])
            else:
                logger.info("🧠 [ANTIGRAVITY RETRY] Tự động chuyển fallback sang %s (lần %d/%d)...", prof["label"], attempt, max_retries)
            try:
                completed = subprocess.run(
                    command, cwd=analysis_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=600,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "unknown error").strip()
                    raise RuntimeError(f"Antigravity local lỗi: {detail[-1200:]}")
                raw = (completed.stdout or "").strip()
                if not raw:
                    raise RuntimeError("Antigravity local không trả kết quả.")
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        if envelope.get("status") and envelope.get("status") != "SUCCESS":
                            err_msg = envelope.get("error") or envelope.get("message") or envelope.get("status")
                            raise RuntimeError(f"Antigravity CLI báo lỗi: {err_msg}")
                        if "response" in envelope and not str(envelope.get("response") or "").strip():
                            raise RuntimeError(
                                "Antigravity CLI hoàn thành nhưng trả về response rỗng (quá tải token hoặc mô hình dừng sớm)."
                            )
                        for key in ("result", "response", "content", "text", "output"):
                            value = envelope.get(key)
                            if isinstance(value, str) and value.strip():
                                raw = value.strip()
                                break
                except json.JSONDecodeError:
                    pass
                logger.info("✅ [ANTIGRAVITY PROMPT] Phân tích xong (%d ký tự).", len(raw))
                return raw
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                is_fatal = any(k in err_str for k in [
                    "file not found", "no such file", "invalid argument",
                    "permission denied", "syntax error"
                ])
                if attempt < max_retries and not is_fatal:
                    next_prof = candidate_profiles[attempt % len(candidate_profiles)]
                    logger.warning(
                        "⚠️ [ANTIGRAVITY RETRY] Gặp lỗi (%s). Tự động hạ cấp sang '%s' (lần %d/%d sau 2s)...",
                        exc, next_prof["label"], attempt + 1, max_retries
                    )
                    time.sleep(2.0)
                    continue
                raise last_error
        raise last_error or RuntimeError("Antigravity local thất bại sau các lần thử lại.")

    @classmethod
    def analyze_local_video(cls, system_prompt: str, user_prompt: str,
                            job_dir: str, video_path: str = "") -> str:
        exe = cls.ensure_installed()
        job_dir = os.path.abspath(job_dir or tempfile.gettempdir())

        # Nếu không có video_path hoặc file không tồn tại: tự động chạy chế độ phân tích prompt thuần
        if not video_path or not os.path.isfile(video_path):
            logger.info("ℹ️ [ANTIGRAVITY] Không có video proxy local, chuyển sang phân tích prompt nội dung...")
            return cls.analyze_prompt(system_prompt, user_prompt, job_dir)

        video_path = os.path.abspath(video_path)
        if os.path.commonpath([job_dir, video_path]) != job_dir:
            raise RuntimeError("Video proxy phải nằm trong thư mục job đang xử lý.")

        # Tạo thư mục chuyên biệt cho AI để cô lập ảnh và request,
        # tránh nạp video gốc .mp4 và audio .wav làm phình 310.000 tokens.
        analysis_dir = os.path.join(job_dir, "ai_storyboard")
        os.makedirs(analysis_dir, exist_ok=True)

        storyboard_files = []
        try:
            from scene_mapper import ensure_visual_storyboards
            storyboard_files = ensure_visual_storyboards(video_path, analysis_dir)
        except Exception as exc:
            logger.warning("⚠️ [ANTIGRAVITY] Không thể tạo storyboard ảnh: %s", exc)

        # Tính chính xác số frame gửi CLI để quyết định mô hình (ngưỡng 200 frames)
        sampled_frame_count = 0
        manifest_path = os.path.join(analysis_dir, "storyboard_manifest.json")
        if os.path.isfile(manifest_path):
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    manifest_data = json.load(f)
                sampled_frame_count = int(manifest_data.get("total_sampled_frames") or 0)
            except Exception:
                pass

        if not sampled_frame_count and storyboard_files:
            sampled_frame_count = len(storyboard_files) * 64

        if not sampled_frame_count and video_path and os.path.isfile(video_path):
            try:
                import cv2
                vcap = cv2.VideoCapture(video_path)
                if vcap.isOpened():
                    sampled_frame_count = int(vcap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                    vcap.release()
            except Exception:
                pass

        if storyboard_files:
            storyboard_names = [os.path.basename(f) for f in storyboard_files]
            first_sheet = storyboard_names[0]
            last_sheet = storyboard_names[-1]
            storyboard_section = (
                f"VISUAL STORYBOARDS ({len(storyboard_files)} Full HD contact sheets covering the entire video from 00:00 to end):\n"
                f"Files: {', '.join(storyboard_names)}\n"
                "Each image contains an 8x8 grid of chronological 16:9 video frames with burned timestamps [mm:ss] and candidate tags.\n"
                "INSPECTIONS:\n"
                f"1. Review the storyboard contact sheets ({first_sheet} to {last_sheet}) and 'storyboard_timeline.txt' to understand the visual narrative.\n"
                "2. Understand the full visual progression: setup, interactions, escalations, climax, and visible resolution.\n"
                "3. Inspect frames for unusable_visual_ranges: scan for intervals where channel branding, watermarks, giant text banners, censor blur, or subscribe overlays obscure the main action.\n"
                "4. Select the most impactful visual moments for segments and hook.\n"
            )
            short_request = (
                f"Read antigravity_request.txt. Review storyboard contact sheets ({first_sheet} to {last_sheet}) "
                "and storyboard_timeline.txt to understand the visual timeline and detect unusable visual ranges. "
                "Follow all instructions in the request file and return ONLY the required JSON immediately."
            )
        else:
            storyboard_section = (
                "Use the video itself to verify visible events, people, actions and scene order.\n"
            )
            short_request = (
                "Open and read antigravity_request.txt in the current job directory, then inspect "
                f"the local video {os.path.basename(video_path)}. Follow every instruction in the "
                "request file and return only its required JSON."
            )

        # Windows has a relatively small process command-line limit. Long
        # transcripts and full visual pools can easily exceed it (WinError
        # 206), so the complete request must travel through a UTF-8 file.
        request_path = os.path.join(analysis_dir, "antigravity_request.txt")
        request_body = (
            "You are the visual story analyst for this one local video job.\n"
            f"LOCAL PROXY VIDEO: {os.path.basename(video_path)}\n"
            f"{storyboard_section}\n"
            "Use the visual storyboards and video to verify visible events, people, actions and scene order.\n"
            "Use the transcript as dialogue/context evidence. Do not edit files and do not run "
            "unrelated commands. Return only the JSON required by the instructions.\n\n"
            f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER INPUT:\n{user_prompt}\n"
        )
        Path(request_path).write_text(request_body, encoding="utf-8")
        candidate_profiles = cls.get_candidate_profiles(frame_count=sampled_frame_count)
        if sampled_frame_count > 200:
            logger.info(
                "⚡ [ANTIGRAVITY CAPACITY] Storyboard gửi %d frames (> 200 frames) -> Tự động chuyển ưu tiên sang '%s' để tránh lỗi 503 No capacity.",
                sampled_frame_count, candidate_profiles[0]["label"]
            )
        else:
            logger.info(
                "⚡ [ANTIGRAVITY CAPACITY] Storyboard gửi %d frames (<= 200 frames) -> Ưu tiên '%s'.",
                sampled_frame_count, candidate_profiles[0]["label"]
            )

        max_retries = max(5, len(candidate_profiles))
        last_error = None
        for attempt in range(1, max_retries + 1):
            prof = candidate_profiles[(attempt - 1) % len(candidate_profiles)]
            model_name = prof["model"]
            effort = prof["effort"]
            command = [
                exe, "--print", short_request,
                "--model", model_name,
                "--output-format", "json",
                "--effort", effort,
                "--add-dir", analysis_dir,
                "--dangerously-skip-permissions",
            ]
            if attempt == 1:
                logger.info(
                    "🎬 [ANTIGRAVITY LOCAL] Phân tích visual storyboard Full HD & proxy (Ưu tiên: %s | %d frames): %s",
                    prof["label"], sampled_frame_count, video_path
                )
            else:
                logger.info(
                    "🎬 [ANTIGRAVITY RETRY] Tự động chuyển fallback sang %s (lần %d/%d | %d frames): %s",
                    prof["label"], attempt, max_retries, sampled_frame_count, video_path
                )
            try:
                completed = subprocess.run(
                    command, cwd=analysis_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=1800,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "unknown error").strip()
                    raise RuntimeError(f"Antigravity local lỗi: {detail[-1200:]}")
                raw = (completed.stdout or "").strip()
                if not raw:
                    raise RuntimeError("Antigravity local không trả kết quả.")
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        if envelope.get("status") and envelope.get("status") != "SUCCESS":
                            err_msg = envelope.get("error") or envelope.get("message") or envelope.get("status")
                            raise RuntimeError(f"Antigravity CLI báo lỗi: {err_msg}")
                        if "response" in envelope and not str(envelope.get("response") or "").strip():
                            raise RuntimeError(
                                "Antigravity CLI hoàn thành nhưng trả về response rỗng (quá tải token hoặc mô hình dừng sớm)."
                            )
                        for key in ("result", "response", "content", "text", "output"):
                            value = envelope.get(key)
                            if isinstance(value, str) and value.strip():
                                raw = value.strip()
                                break
                except json.JSONDecodeError:
                    pass
                logger.info("✅ [ANTIGRAVITY LOCAL] Phân tích video xong (%d ký tự).", len(raw))
                return raw
            except Exception as exc:
                last_error = exc
                err_str = str(exc).lower()
                is_fatal = any(k in err_str for k in [
                    "file not found", "no such file", "invalid argument",
                    "permission denied", "syntax error"
                ])
                if attempt < max_retries and not is_fatal:
                    next_prof = candidate_profiles[attempt % len(candidate_profiles)]
                    logger.warning(
                        "⚠️ [ANTIGRAVITY RETRY] Gặp lỗi mạng/API/Server (%s). Tự động hạ cấp sang '%s' (lần %d/%d sau 3s)...",
                        exc, next_prof["label"], attempt + 1, max_retries
                    )
                    time.sleep(3.0)
                    continue
                raise last_error
        raise last_error or RuntimeError("Antigravity local thất bại sau các lần thử lại.")

    @staticmethod
    def parse_hook_selection(raw: str, video_duration: float, hook_duration: float) -> dict:
        """Đọc JSON Gemini và ép mốc nằm trọn trong video."""
        text = str(raw or "").strip()
        fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.I | re.S)
        if fence:
            text = fence.group(1)
        else:
            obj = re.search(r"\{.*\}", text, re.S)
            if obj:
                text = obj.group(0)
        data = json.loads(text)
        start = float(data.get("start_sec"))
        duration = max(0.1, float(hook_duration))
        maximum = max(0.0, float(video_duration or 0.0) - duration)
        start = min(max(0.0, start), maximum)
        return {
            "start_sec": round(start, 3),
            "end_sec": round(min(float(video_duration or start + duration), start + duration), 3),
            "reason": str(data.get("reason") or "Cảnh cao trào do Gemini lựa chọn").strip(),
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.0) or 0.0))),
        }

    @classmethod
    def select_best_hook(cls, job_dir: str, video_path: str,
                         video_duration: float, hook_duration: float) -> dict:
        system_prompt = (
            "Select exactly one strongest continuous native-audio hook from the supplied video. "
            "The hook will be cut from the HD source at your timestamp, so timestamps must match "
            "the proxy timeline exactly. Prefer decisive action, confrontation, impact, reveal, "
            "danger, surprise, or a powerful reaction. Avoid intros, logos, setup, dead air, driving, "
            "talking in cars, and aftermath when a stronger visible event exists. Reject any interval "
            "where channel branding, a warning card, giant text, a graphic overlay, censor blur, a "
            "subscribe panel, or a replay graphic hides the central subject/action or covers a large "
            "part of the frame (roughly 25% or more), even if the event is dramatic. Choose the nearest "
            "clear moment immediately before or after that event instead. Include a short "
            "lead-in before the key action when useful. The clip must make sense with its original "
            "audio and without AI narration. Inspect the whole video before deciding."
        )
        user_prompt = (
            f"Video duration: {float(video_duration or 0.0):.3f} seconds. "
            f"Required hook duration: {float(hook_duration):.3f} seconds. "
            "Return only one JSON object using exactly this schema: "
            '{"start_sec": 0.0, "end_sec": 0.0, "reason": "brief visual reason", '
            '"confidence": 0.0}. The end must equal start plus the required duration.'
        )
        raw = cls.analyze_local_video(system_prompt, user_prompt, job_dir, video_path)
        result = cls.parse_hook_selection(raw, video_duration, hook_duration)
        Path(os.path.join(job_dir, "gemini_hook_selection.json")).write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

    @classmethod
    def get_qc_candidate_profiles(cls) -> list[dict]:
        """Profile chuyên biệt cho tác vụ quét thị giác (QC / Logo / Safety): Ưu tiên low-effort, siêu tốc < 10s."""
        return [
            {"model": "gemini-3.8-flash-low", "effort": "low", "label": "Gemini 3.8 Flash (Low)"},
            {"model": "gemini-3.7-flash-low", "effort": "low", "label": "Gemini 3.7 Flash (Low)"},
            {"model": "gemini-3.6-flash-low", "effort": "low", "label": "Gemini 3.6 Flash (Emergency fallback)"},
        ]

    @classmethod
    def inspect_grid_image(cls, image_path: str, prompt: str, ref_image_path: str = "") -> str:
        """Kiểm tra ảnh lưới 2s contact sheet và ảnh tham chiếu 9:16 bằng Antigravity CLI cục bộ."""
        exe = cls.executable()
        if not exe:
            raise RuntimeError("Antigravity CLI chưa sẵn sàng.")
        image_path = os.path.abspath(image_path)
        if not os.path.isfile(image_path):
            raise RuntimeError(f"Không tìm thấy ảnh lưới: {image_path}")
        parent_dir = os.path.dirname(image_path)
        image_name = os.path.basename(image_path)

        # Cô lập thư mục QC chuyên biệt, CHỈ chứa ảnh lưới, ảnh tham chiếu và request text
        # TUYỆT ĐỐI KHÔNG add thư mục cha vì sẽ kéo theo source_video.mp4 và extracted_audio.wav làm nghẽn/timeout CLI
        qc_dir = os.path.join(parent_dir, "ai_qc_isolated")
        os.makedirs(qc_dir, exist_ok=True)
        # Dọn sạch các file cũ trong qc_dir để tránh gây nhiễu cho AI
        for old_f in os.listdir(qc_dir):
            try:
                os.remove(os.path.join(qc_dir, old_f))
            except Exception:
                pass

        isolated_image = os.path.join(qc_dir, image_name)
        if not os.path.isfile(isolated_image) or os.path.getsize(isolated_image) != os.path.getsize(image_path):
            shutil.copy2(image_path, isolated_image)

        if ref_image_path and os.path.isfile(ref_image_path):
            ref_name = os.path.basename(ref_image_path)
            isolated_ref = os.path.join(qc_dir, ref_name)
            if not os.path.isfile(isolated_ref) or os.path.getsize(isolated_ref) != os.path.getsize(ref_image_path):
                shutil.copy2(ref_image_path, isolated_ref)

        request_file = os.path.join(qc_dir, "grid_inspection_request.txt")
        Path(request_file).write_text(prompt, encoding="utf-8")

        short_request = (
            "Inspect the 9:16 reference frame and contact sheet (sampled 1s per frame) in this directory strictly following "
            "grid_inspection_request.txt. Do not edit files or run extra commands. Output ONLY valid JSON immediately."
        )
        candidate_profiles = cls.get_qc_candidate_profiles()
        max_retries = min(2, len(candidate_profiles))
        for attempt in range(1, max_retries + 1):
            prof = candidate_profiles[(attempt - 1) % len(candidate_profiles)]
            command = [
                exe, "--print", short_request,
                "--model", prof["model"],
                "--output-format", "json",
                "--effort", prof["effort"],
                "--disable-slash-commands",
                "--print-timeout", "30s",
                "--add-dir", qc_dir,
                "--dangerously-skip-permissions",
            ]
            try:
                completed = subprocess.run(
                    command, cwd=qc_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=40,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "unknown error").strip()
                    raise RuntimeError(f"Antigravity grid inspection error: {detail[-600:]}")
                raw = (completed.stdout or "").strip()
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        if envelope.get("status") and envelope.get("status") != "SUCCESS":
                            err_msg = envelope.get("error") or envelope.get("message") or envelope.get("status")
                            raise RuntimeError(f"Antigravity CLI báo lỗi: {err_msg}")
                        for key in ("result", "response", "content", "text", "output"):
                            value = envelope.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip()
                except json.JSONDecodeError:
                    pass
                return raw
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ [ANTIGRAVITY QC TIMEOUT] Quá thời gian chờ (40s) ở lần thử %d/%d.", attempt, max_retries)
                if attempt < max_retries:
                    continue
                return ""
            except Exception as exc:
                if attempt < max_retries:
                    next_prof = candidate_profiles[attempt % len(candidate_profiles)]
                    logger.warning("⚠️ [ANTIGRAVITY QC RETRY] Lỗi (%s), tự động thử với '%s' (lần %d/%d sau 2s)...", exc, next_prof["label"], attempt + 1, max_retries)
                    time.sleep(2.0)
                    continue
                logger.warning("⚠️ [ANTIGRAVITY QC] Bỏ qua quét AI do lỗi: %s", exc)
                return ""
        return ""

    @classmethod
    def inspect_safety_sheets(cls, sheet_paths: list, prompt: str) -> str:
        """Kiểm tra chuyên sâu các ảnh lưới an toàn (Safety Sheets) để phát hiện máu và chấn thương hở."""
        exe = cls.executable()
        if not exe:
            raise RuntimeError("Antigravity CLI chưa sẵn sàng.")
        valid_sheets = [os.path.abspath(p) for p in sheet_paths if p and os.path.isfile(p)]
        if not valid_sheets:
            return ""
        parent_dir = os.path.dirname(valid_sheets[0])
        qc_safety_dir = os.path.join(parent_dir, "ai_qc_safety_isolated")
        os.makedirs(qc_safety_dir, exist_ok=True)
        # Dọn sạch các file cũ
        for old_f in os.listdir(qc_safety_dir):
            try:
                os.remove(os.path.join(qc_safety_dir, old_f))
            except Exception:
                pass

        isolated_sheet_names = []
        for s_path in valid_sheets:
            s_name = os.path.basename(s_path)
            iso_path = os.path.join(qc_safety_dir, s_name)
            shutil.copy2(s_path, iso_path)
            isolated_sheet_names.append(s_name)

        request_file = os.path.join(qc_safety_dir, "grid_inspection_request.txt")
        Path(request_file).write_text(prompt, encoding="utf-8")

        short_request = (
            f"Read grid_inspection_request.txt. Inspect all timeline keyframe images ({', '.join(isolated_sheet_names)}) in this directory strictly following "
            "grid_inspection_request.txt. Detect ALL watermark_logo, crawling_ticker_banner, scoreboard_nameplate, hardcoded_subtitles, and blood_wound. "
            "Do not edit files, do not write scripts or run extra commands. Rely purely on direct visual inspection and output ONLY valid JSON immediately."
        )
        candidate_profiles = cls.get_qc_candidate_profiles()
        max_retries = min(2, len(candidate_profiles))
        for attempt in range(1, max_retries + 1):
            prof = candidate_profiles[(attempt - 1) % len(candidate_profiles)]
            command = [
                exe, "--print", short_request,
                "--model", prof["model"],
                "--output-format", "json",
                "--effort", prof["effort"],
                "--disable-slash-commands",
                "--print-timeout", "30s",
                "--add-dir", qc_safety_dir,
                "--dangerously-skip-permissions",
            ]
            try:
                completed = subprocess.run(
                    command, cwd=qc_safety_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=40,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "unknown error").strip()
                    raise RuntimeError(f"Antigravity safety inspection error: {detail[-600:]}")
                raw = (completed.stdout or "").strip()
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        if envelope.get("status") and envelope.get("status") != "SUCCESS":
                            err_msg = envelope.get("error") or envelope.get("message") or envelope.get("status")
                            raise RuntimeError(f"Antigravity CLI báo lỗi: {err_msg}")
                        for key in ("result", "response", "content", "text", "output"):
                            value = envelope.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip()
                except json.JSONDecodeError:
                    pass
                return raw
            except subprocess.TimeoutExpired:
                logger.warning("⚠️ [ANTIGRAVITY SAFETY TIMEOUT] Quá thời gian chờ (40s) ở lần thử %d/%d.", attempt, max_retries)
                if attempt < max_retries:
                    continue
                return ""
            except Exception as exc:
                if attempt < max_retries:
                    next_prof = candidate_profiles[attempt % len(candidate_profiles)]
                    logger.warning("⚠️ [ANTIGRAVITY SAFETY RETRY] Lỗi (%s), tự động thử lại với '%s' (lần %d/%d sau 2s)...", exc, next_prof["label"], attempt + 1, max_retries)
                    time.sleep(2.0)
                    continue
                logger.warning("⚠️ [ANTIGRAVITY SAFETY] Bỏ qua quét AI do lỗi: %s", exc)
                return ""
        return ""

    @classmethod
    def generate_text(cls, system_prompt: str, user_prompt: str, job_dir: str = None) -> str:
        """Sinh/mở rộng text siêu tốc qua Antigravity CLI trong thư mục cô lập, KHÔNG nạp video hay storyboard."""
        exe = cls.executable()
        if not exe:
            raise RuntimeError("Antigravity CLI chưa sẵn sàng.")

        import tempfile
        work_dir = os.path.join(tempfile.gettempdir(), "ai_text_isolated")
        os.makedirs(work_dir, exist_ok=True)

        req_file = os.path.join(work_dir, "text_request.txt")
        full_content = f"SYSTEM INSTRUCTIONS:\n{system_prompt}\n\nUSER REQUEST:\n{user_prompt}"
        Path(req_file).write_text(full_content, encoding="utf-8")

        short_request = (
            "Read text_request.txt in this folder. Output ONLY valid JSON containing title and script immediately. "
            "Do not edit files or run any tools."
        )
        candidate_profiles = cls.get_candidate_profiles()
        max_retries = max(2, len(candidate_profiles))
        for attempt in range(1, max_retries + 1):
            prof = candidate_profiles[(attempt - 1) % len(candidate_profiles)]
            command = [
                exe, "--print", short_request,
                "--model", prof["model"],
                "--output-format", "json",
                "--effort", prof["effort"],
                "--add-dir", work_dir,
                "--dangerously-skip-permissions",
            ]
            try:
                completed = subprocess.run(
                    command, cwd=work_dir, capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=300,
                    creationflags=0x08000000 if os.name == "nt" else 0,
                )
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "unknown error").strip()
                    raise RuntimeError(f"Antigravity text error: {detail[-600:]}")
                raw = (completed.stdout or "").strip()
                try:
                    envelope = json.loads(raw)
                    if isinstance(envelope, dict):
                        if envelope.get("status") and envelope.get("status") != "SUCCESS":
                            err_msg = envelope.get("error") or envelope.get("message") or envelope.get("status")
                            raise RuntimeError(f"Antigravity CLI báo lỗi: {err_msg}")
                        for key in ("result", "response", "content", "text", "output"):
                            value = envelope.get(key)
                            if isinstance(value, str) and value.strip():
                                return value.strip()
                            elif isinstance(value, dict):
                                return json.dumps(value, ensure_ascii=False)
                except json.JSONDecodeError:
                    pass
                return raw
            except Exception as exc:
                if attempt < max_retries:
                    next_prof = candidate_profiles[attempt % len(candidate_profiles)]
                    time.sleep(1.0)
                    continue
                raise

