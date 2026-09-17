# -*- coding: utf-8 -*-
"""
Module CompilationProcessor:
Xử lý chế độ Tuyển tập Top-N / Countdown Compilation (No.N ➔ No.1):
- Quét nhanh danh sách từ YouTube Playlist hoặc Thư mục Local.
- Bốc ngẫu nhiên N clip theo chỉ định.
- Xếp hạng từ No. N đếm ngược về No. 1:
  * Nếu có số View (YouTube): Clip ít view nhất là No. N, nhiều view nhất là No. 1 (cao trào cuối video).
  * Nếu không có số View (Local / Ẩn view): Random thứ tự No. N ➔ No. 1.
- Tải chọn lọc đúng N clip (không tải thừa cả playlist).
- Bốc sub siêu tốc bằng Whisper CUDA GPU (11s).
- Gọi AI viết 1 kịch bản đếm ngược duy nhất cho cả N clip liền mạch.
- Sinh voice TTS song song.
- Trích xuất Teaser Highlight (2-3s) trước mỗi No.X.
- Dựng và xuất video Direct NVENC GPU tốc độ cao.
"""

from __future__ import annotations

import os
import sys
import json
import re
import time
import random
import shutil
import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from PIL import Image, ImageDraw, ImageFont

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

try:
    from utils.logger import logger
except ImportError:
    import logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger("CompilationProcessor")

import yt_dlp
from hardware_manager import run_ffmpeg_auto, get_hardware_profile
from downloader_processor import DownloaderProcessor
from ai_processor import AIProcessor
from editor_processor import EditorProcessor

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class CompilationProcessor:
    """Bộ xử lý toàn diện cho chế độ Tuyển tập Top-N / Countdown Video."""

    TEMP_DIR = "temp"
    OUTPUT_DIR = "output"

    @classmethod
    def scan_youtube_playlist(cls, playlist_url: str) -> list[dict]:
        """
        Quét phẳng (flat-scan) toàn bộ playlist YouTube trong 2-3s.
        KHÔNG tải video data, chỉ lấy metadata (id, title, view_count, duration, url).
        """
        logger.info(f"🔍 [PLAYLIST SCAN] Đang quét nhanh playlist: {playlist_url}")
        ydl_opts = {
            "extract_flat": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extractor_args": {
                "youtubetab": {"skip": ["authcheck"]}
            }
        }
        ydl_opts = DownloaderProcessor.apply_cookies_to_opts(ydl_opts)
        entries = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                info = ydl.extract_info(playlist_url, download=False)
            except Exception as e:
                err_msg = str(e)
                if "400" in err_msg or "Bad Request" in err_msg:
                    raise RuntimeError(
                        f"Link Playlist YouTube không tồn tại hoặc ID playlist không hợp lệ (HTTP 400 Bad Request): '{playlist_url}'. "
                        f"Vui lòng dán link Playlist YouTube thực tế trên YouTube!"
                    )
                elif "404" in err_msg or "Not Found" in err_msg:
                    raise RuntimeError(f"Không tìm thấy Playlist YouTube này trên YouTube (HTTP 404 Not Found): '{playlist_url}'.")
                elif "Private" in err_msg or "private" in err_msg:
                    raise RuntimeError(f"Playlist YouTube này đang ở chế độ Riêng tư (Private), không thể truy cập: '{playlist_url}'.")
                raise RuntimeError(f"Lỗi khi quét Playlist YouTube ({playlist_url}): {err_msg}")

            if not info:
                raise RuntimeError(f"Không thể đọc thông tin từ Playlist YouTube: {playlist_url}")

            playlist_title = str(info.get("title") or "TOP HIGHLIGHT").strip()
            raw_entries = info.get("entries") or []
            if not raw_entries:
                raise RuntimeError(f"Playlist YouTube '{playlist_title}' không chứa video nào hoặc toàn bộ video đã bị ẩn/xóa.")
            for item in raw_entries:
                if not item:
                    continue
                v_id = item.get("id") or item.get("url")
                if not v_id:
                    continue
                raw_title = item.get("title")
                # Lọc bỏ các video bị xóa, ẩn, private hoặc không khả dụng trên YouTube
                if not raw_title or str(raw_title).strip() in ("", "None", "Untitled Video", "[Deleted video]", "[Private video]"):
                    logger.info(f"⏭️ [PLAYLIST SCAN] Bỏ qua video bị ẩn/xóa trên YouTube: {v_id}")
                    continue
                if item.get("duration") is None and item.get("view_count") is None:
                    logger.info(f"⏭️ [PLAYLIST SCAN] Bỏ qua video thiếu thông tin metadata: {v_id}")
                    continue

                url = item.get("url") if item.get("url", "").startswith("http") else f"https://www.youtube.com/watch?v={v_id}"
                views = item.get("view_count")
                try:
                    views = int(views) if views is not None else None
                except (ValueError, TypeError):
                    views = None
                
                likes = item.get("like_count")
                try:
                    likes = int(likes) if likes is not None else None
                except (ValueError, TypeError):
                    likes = None

                duration = item.get("duration")
                try:
                    duration = float(duration) if duration is not None else 0.0
                except (ValueError, TypeError):
                    duration = 0.0

                entries.append({
                    "id": str(v_id),
                    "title": str(raw_title).strip(),
                    "url": url,
                    "view_count": views,
                    "like_count": likes,
                    "duration": duration,
                    "source_type": "youtube",
                    "playlist_title": playlist_title,
                })
        logger.info(f"✅ [PLAYLIST SCAN] '{playlist_title}': Đã tìm thấy {len(entries)} clip hợp lệ.")
        return entries

    @classmethod
    def scan_local_folder(cls, folder_path: str) -> list[dict]:
        """
        Quét thư mục local để tìm tất cả các file video hợp lệ (.mp4, .mkv, .mov, .webm, .avi).
        Tự động tìm kiếm file .info.json kèm theo hoặc tên file chứa view/like count nếu có.
        """
        folder = Path(folder_path).resolve()
        if not folder.is_dir():
            raise ValueError(f"Thư mục không tồn tại: {folder_path}")

        valid_exts = {".mp4", ".mkv", ".mov", ".webm", ".avi", ".ts"}
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in valid_exts]
        
        entries = []
        for file_path in files:
            # Kiểm tra xem có file metadata .json đi kèm không
            json_file = file_path.with_suffix(".info.json")
            if not json_file.exists():
                json_file = file_path.with_suffix(".json")

            view_count = None
            like_count = None
            title = file_path.stem
            duration = 0.0

            if json_file.exists():
                try:
                    with open(json_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    view_count = meta.get("view_count")
                    like_count = meta.get("like_count")
                    if meta.get("title"):
                        title = meta.get("title")
                    duration = float(meta.get("duration") or 0.0)
                except Exception:
                    pass

            # Nếu chưa có view, thử phân tích từ tên file (vd: video_name_[1.2M_views])
            if view_count is None:
                view_match = re.search(r"\[([\d\.]+)\s*([KkMmBb])?\s*views?\]", file_path.name, re.I)
                if view_match:
                    try:
                        num = float(view_match.group(1))
                        unit = (view_match.group(2) or "").upper()
                        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(unit, 1)
                        view_count = int(num * mult)
                    except Exception:
                        pass

            if like_count is None:
                like_match = re.search(r"\[([\d\.]+)\s*([KkMmBb])?\s*likes?\]", file_path.name, re.I)
                if like_match:
                    try:
                        num = float(like_match.group(1))
                        unit = (like_match.group(2) or "").upper()
                        mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(unit, 1)
                        like_count = int(num * mult)
                    except Exception:
                        pass

            if duration <= 0:
                duration = DownloaderProcessor.probe_duration_sec(str(file_path))

            entries.append({
                "id": file_path.stem,
                "title": title,
                "url": str(file_path),
                "local_path": str(file_path),
                "view_count": view_count,
                "like_count": like_count,
                "duration": duration,
                "source_type": "local",
            })

        logger.info(f"✅ [LOCAL SCAN] Đã quét thấy {len(entries)} video trong thư mục: {folder_path}")
        return entries

    @classmethod
    def _ensure_likes_for_clips(cls, clips: list[dict]):
        """Lấy nhanh like_count đa luồng cho các clip đã bốc nếu thiếu."""
        missing = [c for c in clips if c.get("like_count") is None and c.get("source_type") == "youtube" and c.get("url")]
        if not missing:
            return

        def _fetch_one(item):
            url = item.get("url")
            if not url:
                return
            try:
                ydl_opts = {
                    "quiet": True,
                    "no_warnings": True,
                    "skip_download": True,
                }
                ydl_opts = DownloaderProcessor.apply_cookies_to_opts(ydl_opts)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    if info:
                        if info.get("like_count") is not None:
                            item["like_count"] = int(info["like_count"])
                        if item.get("view_count") is None and info.get("view_count") is not None:
                            item["view_count"] = int(info["view_count"])
            except Exception:
                pass

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(5, len(missing))) as executor:
            list(executor.map(_fetch_one, missing))

    @classmethod
    def pick_and_rank_clips(cls, entries: list[dict], count: int = 5, seed: int = None, rank_by: str = "views") -> list[dict]:
        """
        BƯỚC 1: Bốc ngẫu nhiên N clip từ danh sách.
        BƯỚC 2: Xếp hạng đếm ngược No. N ➔ No. 1 theo 3 tiêu chí:
        1. 'views'  : Xếp theo Lượt xem tăng dần (Ít view nhất = No. N, Nhiều view nhất = No. 1 - Trùm cuối).
        2. 'likes'  : Xếp theo Lượt thích tăng dần (Ít like nhất = No. N, Nhiều like nhất = No. 1 - Trùm cuối).
        3. 'random' : Xếp ngẫu nhiên hoàn toàn thứ tự No. N ➔ No. 1.
        """
        if not entries:
            raise ValueError("Danh sách clip rỗng, không thể bốc clip!")

        rng = random.Random(seed)
        n = min(count, len(entries))
        
        # Bốc ngẫu nhiên N clip từ kho
        sampled = rng.sample(entries, n)

        rank_mode = str(rank_by or "views").lower().strip()
        effective_mode = rank_mode

        if rank_mode in ("likes", "like", "luot_thich"):
            # Lấy nhanh Like nếu thiếu
            cls._ensure_likes_for_clips(sampled)
            has_valid_likes = any(
                isinstance(item.get("like_count"), (int, float)) and item.get("like_count", 0) > 0
                for item in sampled
            )
            if has_valid_likes:
                logger.info("👍 [RANKING] Sắp xếp No.1 là clip nhiều LIKE nhất!")
                sampled.sort(key=lambda x: (x.get("like_count") or 0))
                effective_mode = "likes"
            else:
                logger.warning("⚠️ [RANKING] Không có số liệu Like Count -> Fallback sang xếp theo View!")
                rank_mode = "views"

        if rank_mode in ("views", "view", "luot_xem"):
            has_valid_views = any(
                isinstance(item.get("view_count"), (int, float)) and item.get("view_count", 0) > 0
                for item in sampled
            )
            if has_valid_views:
                logger.info("📊 [RANKING] Phát hiện số liệu View Count -> Sắp xếp No.1 là clip nhiều VIEW nhất!")
                sampled.sort(key=lambda x: (x.get("view_count") or 0))
                effective_mode = "views"
            else:
                logger.info("🎲 [RANKING] Không có số liệu View Count -> Tự động RANDOM thứ tự No. N ➔ No. 1!")
                rng.shuffle(sampled)
                effective_mode = "random"
        elif rank_mode in ("random", "ngau_nhien"):
            logger.info("🎲 [RANKING] Chế độ RANDOM -> Tự động xếp ngẫu nhiên hoàn toàn No. N ➔ No. 1!")
            rng.shuffle(sampled)
            effective_mode = "random"

        # Gán nhãn thứ hạng No. N đếm ngược về No. 1
        ranked_items = []
        for i, item in enumerate(sampled):
            rank = n - i
            item_copy = dict(item)
            item_copy["rank"] = rank
            item_copy["badge_label"] = f"TOP {rank}"
            item_copy["rank_by"] = effective_mode
            ranked_items.append(item_copy)

        return ranked_items

    @classmethod
    def clean_clip_title(cls, raw_title: str) -> str:
        """Làm sạch tiêu đề: bỏ phần thừa như | Gypsy Sisters, [HD], #Shorts..."""
        title = str(raw_title or "").strip()
        # Bỏ các tag vuông/tròn [Official], (Lyrics), vv
        title = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", title)
        # Bỏ phần sau dấu gạch đứng hoặc dấu gạch ngang thương hiệu
        parts = re.split(r"[\s\|\-–—]{2,}|(?<=\s)[\-–—\|](?=\s)", title)
        if parts:
            title = parts[0].strip()
        # Bỏ hashtags
        title = re.sub(r"#\w+", "", title).strip()
        title = re.sub(r"\s+", " ", title)
        return title or "Moment"

    @classmethod
    def download_selected_clips(
        cls,
        ranked_items: list[dict],
        work_dir: str,
        progress_cb=None,
        all_entries: list[dict] = None
    ) -> list[dict]:
        """
        Tải chọn lọc ĐÚNG N clip đã được bốc (Selective Downloader).
        Với clip Local: giữ nguyên đường dẫn có sẵn.
        Tự động bốc video thay thế từ all_entries nếu video ban đầu bị YouTube chặn/xóa.
        """
        os.makedirs(work_dir, exist_ok=True)
        results = []
        used_ids = {it.get("id") for it in ranked_items if it.get("id")}
        backup_pool = [e for e in (all_entries or []) if e.get("id") and e.get("id") not in used_ids]

        for item in ranked_items:
            rank = item["rank"]
            source_type = item.get("source_type", "youtube")
            clip_dir = os.path.join(work_dir, f"top_{rank}")
            os.makedirs(clip_dir, exist_ok=True)

            if source_type == "local" and os.path.exists(item.get("local_path", "")):
                item["downloaded_video"] = item["local_path"]
                results.append(item)
                logger.info(f"📁 [LOCAL READY] Top {rank}: {os.path.basename(item['local_path'])}")
                continue

            # Tải YouTube
            target_video = os.path.join(clip_dir, "source_video.mp4")
            if os.path.exists(target_video) and os.path.getsize(target_video) > 1024 * 1024:
                logger.info(f"⏭️ [SKIP DL] Top {rank} đã có sẵn tại {target_video}")
                item["downloaded_video"] = target_video
                results.append(item)
                continue

            url = item["url"]
            logger.info(f"📥 [SELECTIVE DL] Top {rank} (Rank {rank}): {item['title'][:40]}...")
            if progress_cb:
                progress_cb(f"Đang tải Top {rank} ({item['title'][:30]}...)")

            ydl_opts = {
                "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
                "outtmpl": target_video,
                "quiet": True,
                "no_warnings": True,
                "merge_output_format": "mp4",
            }
            ydl_opts = DownloaderProcessor.apply_cookies_to_opts(ydl_opts)
            dl_ok = False
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                if os.path.exists(target_video) and os.path.getsize(target_video) > 500 * 1024:
                    dl_ok = True
                    item["downloaded_video"] = target_video
            except Exception as e:
                logger.warning(f"⚠️ Lỗi tải Top {rank} bằng yt-dlp: {e} - thử chế độ fallback...")
                try:
                    DownloaderProcessor.download_high_res_video(url, target_video)
                    if os.path.exists(target_video) and os.path.getsize(target_video) > 500 * 1024:
                        dl_ok = True
                        item["downloaded_video"] = target_video
                except Exception as fb_err:
                    logger.warning(f"⚠️ Fallback tải Top {rank} thất bại: {fb_err}")

            # Nếu video bị chặn/xóa hoàn toàn trên YouTube -> Tự động bốc video dự phòng từ playlist
            if not dl_ok and backup_pool:
                while backup_pool and not dl_ok:
                    cand = backup_pool.pop(0)
                    used_ids.add(cand["id"])
                    logger.info(f"🔄 [AUTO-REPLACE] Top {rank} ('{item['title']}') không khả dụng. Bốc video dự phòng thay thế: '{cand['title']}'...")
                    if progress_cb:
                        progress_cb(f"Bốc video thay thế cho Top {rank}: '{cand['title'][:25]}'...")
                    c_url = cand["url"]
                    cand_opts = dict(ydl_opts)
                    try:
                        with yt_dlp.YoutubeDL(cand_opts) as ydl:
                            ydl.download([c_url])
                        if os.path.exists(target_video) and os.path.getsize(target_video) > 500 * 1024:
                            dl_ok = True
                            item["id"] = cand["id"]
                            item["title"] = cand["title"]
                            item["url"] = c_url
                            item["downloaded_video"] = target_video
                            logger.info(f"✅ [AUTO-REPLACE] Thay thế Top {rank} thành công bằng: '{cand['title']}'")
                            break
                    except Exception as cand_err:
                        logger.warning(f"⚠️ Video dự phòng '{cand['title']}' cũng lỗi: {cand_err}, thử video khác...")

            if dl_ok:
                results.append(item)
            else:
                logger.error(f"❌ Không thể tải clip Top {rank} ('{item.get('title')}'), bỏ qua clip này.")

        return results

    @classmethod
    def extract_subtitles_or_transcripts(cls, ranked_items: list[dict], work_dir: str) -> list[dict]:
        """
        Trích xuất phụ đề/lời thoại cho từng clip bằng YouTube API hoặc Whisper CUDA GPU siêu tốc (11s).
        """
        for item in ranked_items:
            rank = item["rank"]
            video_path = item.get("downloaded_video", "")
            clip_dir = os.path.join(work_dir, f"top_{rank}")
            os.makedirs(clip_dir, exist_ok=True)

            transcript = ""
            # 1. Thử lấy nhanh phụ đề YouTube API nếu có (siêu tốc 0.1s)
            if item.get("source_type") == "youtube" and item.get("url"):
                try:
                    transcript = AIProcessor.get_youtube_transcript(item["url"]) or ""
                except Exception:
                    transcript = ""

            # 2. Nếu video không có phụ đề sẵn, dùng tiêu đề gốc làm sạch cho AI tóm tắt (siêu tốc, không đơ máy)
            if not transcript:
                transcript = cls.clean_clip_title(item.get("title", ""))
                logger.info(f"ℹ️ [TOP CONTEXT] Top {rank}: Dùng ngữ cảnh tiêu đề '{transcript}' cho AI.")
            else:
                logger.info(f"📝 [SUB READY] Top {rank}: Đã nạp {len(transcript.split())} từ lời thoại YouTube.")

            item["transcript"] = transcript.strip()

        return ranked_items

    @classmethod
    def generate_countdown_batch_script(
        cls,
        ranked_items: list[dict],
        total_duration_sec: float = 180.0,
        market: str = "US",
        title_style: str = "clean_original",
        model_name: str = "Google Antigravity (Local)",
        api_key: str = "",
        custom_prompt_hint: str = "",
        work_dir: str = "",
    ) -> tuple[str, list[dict]]:
        """
        1 LẦN GỌI AI DUY NHẤT (Single Unified Batch Prompt):
        Gửi danh sách N clip cho AI để viết kịch bản đếm ngược hoàn chỉnh từ No. N về No. 1.
        """
        count = len(ranked_items)
        sec_per_clip = max(20.0, total_duration_sec / max(1, count))
        target_words_per_clip = max(40, round(sec_per_clip * 2.3))

        logger.info(
            f"🧠 [BATCH AI] Đang tạo kịch bản Countdown {count} clip (~{sec_per_clip:.1f}s/clip, ~{target_words_per_clip} từ/clip)..."
        )

        clips_info = []
        for it in ranked_items:
            clean_t = cls.clean_clip_title(it["title"])
            clips_info.append({
                "rank": it["rank"],
                "original_title": it["title"],
                "clean_title": clean_t,
                "view_count": it.get("view_count"),
                "transcript_snippet": (it.get("transcript") or "")[:500],
            })

        system_prompt = (
            "You are a master viral countdown video narrator and editor specializing in high-retention Top-N countdown videos. "
            "Your task is to write a cohesive, thrilling countdown script from the highest number down to No. 1. "
            "No. 1 MUST be the ultimate grand climax / showstopper with the highest intensity. "
            "Requirements:\n"
            f"- Output strictly valid JSON without any markdown formatting or extra text.\n"
            f"- Write in language suitable for market {market} (e.g. Vietnamese for VN, English for US).\n"
            f"- For each item, write a voiceover narration of approximately {target_words_per_clip} words.\n"
            "- Make sure there are smooth verbal transitions leading into each next rank.\n"
            "- Provide a punchy badge title for each clip (1-4 words) suitable for an on-screen overlay badge.\n"
            "- JSON Schema:\n"
            "{\n"
            '  "compilation_title": "Top X Most Explosive Moments",\n'
            '  "items": [\n'
            '    {\n'
            '      "rank": 5,\n'
            '      "badge_title": "SHORT PUNCHY TITLE",\n'
            '      "narration": "Narration text for this rank...",\n'
            '      "transition": "Transition sentence to next rank..."\n'
            '    }\n'
            "  ]\n"
            "}"
        )

        user_prompt = f"CLIPS DATA:\n{json.dumps(clips_info, ensure_ascii=False, indent=2)}\n\n"
        if custom_prompt_hint:
            user_prompt += f"Storytelling hint: {custom_prompt_hint}\n"
        user_prompt += f"Target duration: ~{total_duration_sec}s total for {count} clips."

        # Gọi AI qua AIProcessor.call_raw_llm (tránh wrap nhầm template single-video)
        raw_response = AIProcessor.call_raw_llm(
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            job_dir=work_dir or cls.TEMP_DIR,
        )

        # Parse JSON
        parsed_data = None
        clean_json = raw_response.strip()
        match = re.search(r"\{.*\}", clean_json, re.DOTALL)
        if match:
            try:
                parsed_data = json.loads(match.group(0))
            except Exception:
                pass

        compilation_title = "TOP HIGHLIGHTS COUNTDOWN"
        ai_items_map = {}
        if parsed_data and isinstance(parsed_data, dict):
            compilation_title = parsed_data.get("compilation_title") or compilation_title
            for it in parsed_data.get("items", []):
                if isinstance(it, dict) and "rank" in it:
                    ai_items_map[int(it["rank"])] = it

        # Hợp nhất kịch bản vào từng item
        for item in ranked_items:
            r = item["rank"]
            ai_data = ai_items_map.get(r, {})
            if title_style == "ai_generated" and ai_data.get("badge_title"):
                item["display_title"] = ai_data.get("badge_title")
            else:
                item["display_title"] = cls.clean_clip_title(item["title"])

            item["narration"] = (ai_data.get("narration") or item["title"]).strip()
            item["transition"] = ai_data.get("transition", "").strip()

        return compilation_title, ranked_items

    @classmethod
    def generate_voices_parallel(cls, ranked_items: list[dict], voice: str, locale: str, work_dir: str) -> list[dict]:
        """
        Sinh giọng đọc Voice AI song song cho tất cả các phần đếm ngược.
        """
        logger.info(f"🎙️ [PARALLEL TTS] Đang sinh voice song song cho {len(ranked_items)} clips...")

        def _make_voice(item):
            rank = item["rank"]
            text = item.get("narration", "")
            if item.get("transition"):
                text = f"{text} {item['transition']}".strip()

            v_path = os.path.join(work_dir, f"top_{rank}", f"voice_top_{rank}.mp3")
            AIProcessor.generate_voice(voice, text, output_path=v_path, locale=locale)
            dur = DownloaderProcessor.probe_duration_sec(v_path)
            item["voice_path"] = v_path
            item["voice_duration"] = dur
            logger.info(f"✅ [TTS DONE] Top {rank}: Voice {dur:.1f}s")
            return item

        with ThreadPoolExecutor(max_workers=min(5, len(ranked_items))) as pool:
            futures = [pool.submit(_make_voice, it) for it in ranked_items]
            results = [f.result() for f in futures]

        return results

    @classmethod
    def detect_intro_outro_bounds(cls, video_path: str, total_dur: float = None) -> tuple[float, float]:
        """
        AI & Computer Vision tự động dò tìm và cắt bỏ đoạn thừa (Smart Fluff Trimming):
        1. Intro Bumper / Title Sequence / Logo Card:
           - Quét scene cuts trong 12 giây đầu (select='gt(scene,0.22)').
           - Nếu có scene cut trong dải [0.9s, 8.5s], điểm cut đầu tiên chính là kết thúc của
             Intro Bumper (như logo Gypsy Sisters, intro TLC, bumper đài truyền hình, title card).
           - Tự động cắt bỏ trọn vẹn intro bumper, bắt đầu từ t_cut + 0.12s.
           - Nếu không có scene cut và video đủ dài (>= 20s), tự động skip 2.0s đầu an toàn.
        2. Outro / End Card / Subscribe Prompt:
           - Quét scene cuts trong 8 giây cuối.
           - Nếu có scene cut trong dải [total_dur - 6.5s, total_dur - 1.2s],
             cắt bỏ phần màn hình kết thúc / kêu gọi subscribe.
        """
        try:
            if not video_path or not os.path.exists(video_path):
                return 0.0, 0.0

            if total_dur is None or total_dur <= 0:
                total_dur = DownloaderProcessor.probe_duration_sec(video_path)

            if total_dur <= 5.0:
                return 0.0, total_dur

            # 1. Quét Intro Scene Cuts trong 12s đầu
            scan_t = min(12.0, max(4.0, total_dur * 0.4))
            cmd_intro = [
                "ffmpeg", "-hide_banner", "-i", video_path, "-t", f"{scan_t:.2f}",
                "-vf", "select=gt(scene\\,0.22),metadata=print", "-f", "null", "-"
            ]
            run_kwargs = {"capture_output": True, "text": True}
            if CREATE_NO_WINDOW:
                run_kwargs["creationflags"] = CREATE_NO_WINDOW
            res_intro = subprocess.run(cmd_intro, **run_kwargs)
            raw_intro = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", res_intro.stderr or "")]

            intro_end = 0.0
            for c in raw_intro:
                if 0.9 <= c <= 8.5:
                    intro_end = round(c + 0.12, 2)
                    logger.info(f"✂️ [SMART FLUFF CUT] Phát hiện Intro Bumper / Logo Card dài {c:.2f}s trong '{os.path.basename(video_path)}'. Tự động cắt bỏ, start={intro_end:.2f}s")
                    break

            if intro_end == 0.0 and total_dur >= 20.0:
                intro_end = 2.0
                logger.info(f"✂️ [SMART FLUFF CUT] Tự động skip 2.0s đầu chống dính logo/fade-in cho '{os.path.basename(video_path)}'")

            # 2. Quét Outro Scene Cuts trong 8s cuối
            outro_start = total_dur
            if total_dur >= 15.0:
                scan_start = max(0.0, total_dur - 8.0)
                cmd_outro = [
                    "ffmpeg", "-hide_banner", "-ss", f"{scan_start:.2f}", "-i", video_path,
                    "-vf", "select=gt(scene\\,0.22),metadata=print", "-f", "null", "-"
                ]
                res_outro = subprocess.run(cmd_outro, **run_kwargs)
                raw_outro = [round(scan_start + float(x), 2) for x in re.findall(r"pts_time:([0-9.]+)", res_outro.stderr or "")]
                for c in raw_outro:
                    if (total_dur - 6.5) <= c <= (total_dur - 1.2):
                        outro_start = round(c - 0.10, 2)
                        logger.info(f"✂️ [SMART FLUFF CUT] Phát hiện Outro / End Card từ {c:.2f}s trong '{os.path.basename(video_path)}'. Cắt gọn tại {outro_start:.2f}s")
                        break

            if outro_start <= intro_end + 3.0:
                outro_start = total_dur
                intro_end = min(intro_end, 2.0)

            return intro_end, outro_start
        except Exception as e:
            logger.warning(f"⚠️ [SMART FLUFF CUT] Lỗi dò đoạn thừa: {e}")
            return 0.0, total_dur if total_dur else 0.0

    @classmethod
    def find_clean_clip_window(
        cls,
        video_path: str,
        required_dur: float,
        preferred_start: float = 0.0,
        max_scan: float = 180.0,
        min_start: float = 0.0,
        max_end: float = None
    ) -> float:
        """
        Tự động dò tìm khoảng thời gian sạch (không chứa màn hình đen, fade đen, quảng cáo ngắt đoạn,
        và TUYỆT ĐỐI KHÔNG chứa intro bumper / title card thừa) để cắt ghép phân đoạn video tuyển tập.
        """
        try:
            if not video_path or not os.path.exists(video_path):
                return max(0.0, min_start)
            total_dur = DownloaderProcessor.probe_duration_sec(video_path)
            
            end_limit = min(total_dur, max_end) if max_end else total_dur
            usable_dur = max(0.0, end_limit - min_start)

            if usable_dur <= required_dur:
                # Nếu thời lượng khả dụng nhỏ hơn hoặc bằng yêu cầu, bắt đầu ngay tại min_start
                # để đảm bảo 100% cắt bỏ trọn vẹn intro bumper!
                chosen = max(0.0, min(min_start, max(0.0, total_dur - 5.0)))
                logger.info(f"✨ [CLEAN WINDOW] Clip vừa vặn hạn ngạch ({usable_dur:.1f}s <= {required_dur:.1f}s), chọn start_t={chosen:.2f}s (cắt bỏ intro bumper)")
                return chosen

            # Quét blackdetect
            scan_dur = min(total_dur, max_scan)
            cmd_bd = [
                "ffmpeg", "-y", "-i", video_path, "-t", f"{scan_dur:.2f}",
                "-vf", "blackdetect=d=0.7:pix_th=0.12",
                "-f", "null", "-"
            ]
            run_kwargs = {"capture_output": True, "text": True}
            if CREATE_NO_WINDOW:
                run_kwargs["creationflags"] = CREATE_NO_WINDOW
            p = subprocess.run(cmd_bd, **run_kwargs)
            black_intervals = []
            for line in (p.stderr or "").split("\n"):
                if "blackdetect" in line and "black_start:" in line:
                    m = re.search(r"black_start:([\d\.]+)\s+black_end:([\d\.]+)", line)
                    if m:
                        black_intervals.append((float(m.group(1)), float(m.group(2))))

            safe_pref = max(min_start, preferred_start)
            if end_limit and (safe_pref + required_dur) > end_limit:
                safe_pref = max(min_start, end_limit - required_dur)

            if not black_intervals:
                return safe_pref

            logger.info(f"🌑 [BLACK DETECT] Phát hiện {len(black_intervals)} đoạn màn hình đen trong '{os.path.basename(video_path)}': {black_intervals}")

            # Xây dựng các khoảng thời gian sạch (không có màn hình đen) và nằm trong [min_start, end_limit]
            clean_intervals = []
            curr = min_start
            for b_start, b_end in sorted(black_intervals):
                if b_end <= min_start:
                    continue
                actual_b_start = max(min_start, b_start)
                if actual_b_start > curr:
                    clean_intervals.append((curr, min(actual_b_start, end_limit)))
                curr = max(curr, b_end)
            if curr < end_limit:
                clean_intervals.append((curr, end_limit))

            # Tìm các khoảng sạch đủ độ dài required_dur
            valid_intervals = [ci for ci in clean_intervals if (ci[1] - ci[0]) >= required_dur]
            if valid_intervals:
                # Nếu safe_pref nằm trọn trong một khoảng sạch, ưu tiên dùng
                for c_start, c_end in valid_intervals:
                    if safe_pref >= c_start and (safe_pref + required_dur) <= c_end:
                        return safe_pref
                target = valid_intervals[0]
                chosen_start = target[0] + (0.3 if target[0] > min_start else 0.0)
                final_start = max(min_start, min(chosen_start, target[1] - required_dur))
                logger.info(f"✨ [CLEAN WINDOW] Tự động đổi start_t={final_start:.2f}s để né trọn màn hình đen & intro!")
                return final_start

            # Fallback: chọn khoảng sạch dài nhất
            if clean_intervals:
                largest = max(clean_intervals, key=lambda x: x[1] - x[0])
                fallback_start = max(min_start, min(largest[0] + 0.3, end_limit - required_dur))
                logger.info(f"✨ [CLEAN WINDOW] Fallback khoảng sạch dài nhất: start_t={fallback_start:.2f}s")
                return fallback_start
            return safe_pref
        except Exception as e:
            logger.warning(f"⚠️ [CLEAN WINDOW] Lỗi dò đoạn đen: {e}, fallback start={min_start:.2f}s")
            return max(0.0, min_start)

    @classmethod
    def find_natural_clip_duration(
        cls,
        video_path: str,
        target_dur: float,
        total_clip_dur: float,
        start_t: float = 0.0,
        tolerance_ratio: float = 0.20
    ) -> float:
        """
        Xác định thời lượng cắt tự nhiên trong khoảng [target_dur * (1 - tolerance), target_dur * (1 + tolerance)]:
        - Dung sai linh hoạt +-20% (không ép cứng số giây).
        - Nếu toàn bộ clip hoặc phần còn lại nằm trong ngưỡng +20% (dài hơn target tối đa 20%), giữ trọn vẹn
          để tránh cụt cảnh kết thúc hoặc đứt câu thoại.
        - Tìm khoảng lặng âm thanh (silencedetect) trong khoảng dung sai để ngắt câu tự nhiên.
        """
        if total_clip_dur <= 0:
            return target_dur

        rem_dur = max(0.0, total_clip_dur - start_t)
        if rem_dur <= target_dur:
            return rem_dur

        # Ngưỡng cho phép dung sai +-20%
        min_dur = max(5.0, target_dur * (1.0 - tolerance_ratio))
        max_dur = min(rem_dur, target_dur * (1.0 + tolerance_ratio))

        # Nếu phần còn lại của clip nằm vừa vặn trong biên độ +20%, giữ trọn vẹn
        if rem_dur <= max_dur:
            logger.info(f"🌿 [FLEXIBLE DURATION] Giữ trọn {rem_dur:.1f}s (trong biên độ +20% của mục tiêu {target_dur:.1f}s, không cắt cụt cảnh)")
            return rem_dur

        # Quét khoảng lặng âm thanh để ngắt tự nhiên trong dải [start_t + min_dur, start_t + max_dur]
        try:
            scan_start = start_t + min_dur
            scan_len = max_dur - min_dur
            if scan_len >= 0.5:
                cmd_silence = [
                    "ffmpeg", "-hide_banner", "-loglevel", "info", "-y",
                    "-ss", f"{scan_start:.2f}", "-t", f"{scan_len:.2f}",
                    "-i", video_path,
                    "-af", "silencedetect=noise=-28dB:d=0.20",
                    "-f", "null", "-"
                ]
                run_kwargs = {"capture_output": True, "text": True}
                if CREATE_NO_WINDOW:
                    run_kwargs["creationflags"] = CREATE_NO_WINDOW
                res = subprocess.run(cmd_silence, **run_kwargs)
                silence_ends = []
                for line in (res.stderr or "").split("\n"):
                    if "silence_end:" in line:
                        m = re.search(r"silence_end:([\d\.]+)", line)
                        if m:
                            silence_ends.append(float(m.group(1)))
                if silence_ends:
                    # Chọn điểm ngắt gần target_dur nhất
                    best_rel = min(silence_ends, key=lambda s: abs((scan_start + s) - (start_t + target_dur)))
                    snapped_dur = (scan_start + best_rel) - start_t
                    if min_dur <= snapped_dur <= max_dur:
                        logger.info(f"🎯 [NATURAL SNAP] Hít điểm ngắt câu tự nhiên: {snapped_dur:.1f}s (mục tiêu {target_dur:.1f}s, dung sai ±20%)")
                        return snapped_dur
        except Exception as e:
            logger.debug(f"Silencedetect snap error: {e}")

        return min(target_dur, rem_dur)

    @classmethod
    def extract_clip_teasers(cls, ranked_items: list[dict], teaser_duration: float = 3.0, work_dir: str = "", post_options: dict = None) -> list[dict]:
        """
        Trích xuất 2-3s Highlight Teaser cho từng clip để làm mồi nhử trước mỗi No.X.
        Áp dụng 100% cấu hình Tab 3: Zoom, Crop, Che logo (blur mask), Màu sắc LUT 8K/HDR, âm thanh sống động.
        """
        post_options = post_options or {}
        try:
            from capcut_filters import apply_look
            post_options = apply_look(post_options)
        except Exception:
            pass
        from editor_processor import EditorProcessor
        base_vf = EditorProcessor._build_vf_filter(post_options, input_label="[0:v]")
        teaser_v_filter = f"{base_vf};[vout]fps=30[outv]"

        hook_level = float(post_options.get("hook_audio_boost", post_options.get("audio_boost", 20.0)))
        hook_af = EditorProcessor.capcut_audio_filter(hook_level)

        for item in ranked_items:
            rank = item["rank"]
            src_video = item.get("downloaded_video", "")
            if not src_video or not os.path.exists(src_video):
                item["teaser_path"] = ""
                continue

            total_dur = DownloaderProcessor.probe_duration_sec(src_video)
            intro_end, outro_start = cls.detect_intro_outro_bounds(src_video, total_dur)
            usable_dur = max(3.0, outro_start - intro_end)
            # Chọn điểm bắt đầu teaser ở khoảng 20% thời lượng nội dung sạch (né trọn intro bumper / title card)
            preferred_start = intro_end + max(1.0, usable_dur * 0.20)
            start_t = cls.find_clean_clip_window(
                src_video, teaser_duration, preferred_start=preferred_start,
                min_start=intro_end, max_end=outro_start
            )

            teaser_path = os.path.join(work_dir, f"top_{rank}", f"teaser_top_{rank}.mp4")

            # Kiểm tra âm thanh gốc có sẵn không
            has_audio = False
            try:
                probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", src_video]
                has_audio = bool(subprocess.check_output(probe_cmd).strip())
            except Exception:
                has_audio = False

            if has_audio:
                teaser_filter_full = (
                    f"{teaser_v_filter};"
                    f"[0:a]aformat=channel_layouts=stereo,aresample=44100,{hook_af}[outa]"
                )
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start_t:.2f}", "-t", f"{teaser_duration:.2f}",
                    "-i", src_video,
                    "-filter_complex", teaser_filter_full,
                    "-map", "[outv]", "-map", "[outa]",
                    "-t", f"{teaser_duration:.2f}",
                    "-r", "30",
                    "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-profile:v", "high", "-level:v", "4.1",
                    "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    teaser_path
                ]
            else:
                teaser_filter_full = f"{teaser_v_filter}"
                cmd = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", f"{start_t:.2f}", "-t", f"{teaser_duration:.2f}",
                    "-i", src_video,
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-filter_complex", teaser_filter_full,
                    "-map", "[outv]", "-map", "1:a",
                    "-t", f"{teaser_duration:.2f}",
                    "-r", "30",
                    "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-profile:v", "high", "-level:v", "4.1",
                    "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                    teaser_path
                ]

            if CREATE_NO_WINDOW:
                subprocess.run(cmd, check=False, creationflags=CREATE_NO_WINDOW)
            else:
                subprocess.run(cmd, check=False)

            item["teaser_path"] = teaser_path if os.path.exists(teaser_path) else ""

        return ranked_items

    @classmethod
    def generate_srt_for_narration(cls, narration: str, transition: str, duration_sec: float, output_srt: str) -> str:
        """
        Tạo file SRT phụ đề chuẩn xác cho lời thoại của từng clip trong Tuyển tập Top.
        Chia các cụm từ ngắn 4-6 từ để hiển thị nhịp nhàng theo phong cách CapCut.
        """
        full_text = f"{narration} {transition}".strip()
        if not full_text:
            return ""

        words = full_text.split()
        if not words:
            return ""

        chunks = []
        curr = []
        for w in words:
            curr.append(w)
            if len(curr) >= 5 or any(w.endswith(p) for p in [".", ",", "!", "?", ";"]):
                chunks.append(" ".join(curr))
                curr = []
        if curr:
            chunks.append(" ".join(curr))

        num_chunks = len(chunks)
        time_per_chunk = max(1.0, duration_sec / max(1, num_chunks))

        def _fmt_time(s):
            hrs = int(s // 3600)
            mins = int((s % 3600) // 60)
            secs = int(s % 60)
            millis = int(round((s - int(s)) * 1000))
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"

        srt_lines = []
        for idx, text in enumerate(chunks, 1):
            start = (idx - 1) * time_per_chunk
            end = min(duration_sec, idx * time_per_chunk)
            if start >= duration_sec:
                break
            srt_lines.append(f"{idx}\n{_fmt_time(start)} --> {_fmt_time(end)}\n{text}\n")

        os.makedirs(os.path.dirname(output_srt), exist_ok=True)
        with open(output_srt, "w", encoding="utf-8") as f:
            f.write("\n".join(srt_lines))
        return output_srt

    @classmethod
    def create_top_badge_overlay(cls, rank: int, title: str, output_png: str, title_style: str = "clean_original", post_options: dict = None) -> tuple:
        """
        Tạo Title Banner đếm ngược chuẩn 100% nhận config từ Tab 3:
        (title_y_pos, title_bg_color, title_color1, title_color2, title_outline_color).
        Ví dụ: 'No. 5 - GYPSY SISTERS AT OCEAN CITY'
        """
        from editor_processor import EditorProcessor
        post_options = post_options or {}
        disp_title = (title or "").strip()
        if title_style == "only_rank" or not disp_title:
            banner_text = f"No. {rank}"
        else:
            banner_text = f"No. {rank} - {disp_title}"

        out_dir = os.path.dirname(output_png)
        os.makedirs(out_dir, exist_ok=True)
        banner_path, bw, bh = EditorProcessor._create_dynamic_title_banner_custom(
            out_dir, banner_text, "", post_options
        )
        if os.path.abspath(banner_path) != os.path.abspath(output_png):
            import shutil
            shutil.copy2(banner_path, output_png)
        banner_x = int((1080 - bw) / 2)
        banner_y = int(post_options.get("title_y_pos", 260))
        return output_png, banner_x, banner_y

    @classmethod
    def calculate_adaptive_clip_durations(cls, clip_durations: list[float], target_total_dur: float) -> list[float]:
        """
        Thuật toán phân bổ thời lượng thông minh (Water-filling / Fair Adaptive Balancing):
        - Nếu clip nào ngắn hơn hạn ngạch trung bình (ví dụ cần 80s nhưng clip chỉ có 60s), 
          clip đó sẽ lấy tối đa thời lượng nó có (60s).
        - Phần thời gian thiếu hụt (20s) sẽ được tự động chia bù đều sang các clip khác dài hơn,
          đảm bảo tổng thời lượng video thành phẩm luôn đạt chuẩn 100% target_total_dur.
        """
        n = len(clip_durations)
        if n == 0:
            return []
        
        total_available = sum(clip_durations)
        if total_available <= target_total_dur:
            return [float(d) for d in clip_durations]
        
        allocated = [0.0] * n
        remaining_target = float(target_total_dur)
        active_indices = set(range(n))
        
        while active_indices and remaining_target > 0.01:
            quota = remaining_target / len(active_indices)
            clamped_any = False
            for idx in list(active_indices):
                if clip_durations[idx] <= quota:
                    allocated[idx] = float(clip_durations[idx])
                    remaining_target -= clip_durations[idx]
                    active_indices.remove(idx)
                    clamped_any = True
            if not clamped_any:
                for idx in active_indices:
                    allocated[idx] = quota
                remaining_target = 0
                break
                
        return allocated

    @classmethod
    def render_tiktok_style_badge(
        cls,
        text: str,
        output_png: str = "",
        max_width: int = 860,
        font_size: int = 48,
        stroke_width: int = 3,
        text_color: tuple = (255, 255, 255, 255),
        stroke_color: tuple = (0, 0, 0, 255)
    ) -> tuple[str, int, int]:
        """
        Vẽ chữ No. X hiển thị chuẩn phong cách TikTok (chữ trắng viền đen, KHÔNG hộp nền):
        - Font Sans-serif đậm nét chuẩn TikTok (Arial Bold / Segoe UI Bold).
        - Tự động ngắt dòng thông minh khi tiêu đề dài để không tràn màn hình.
        - Viền đen rõ nét và bóng đổ nhẹ, nổi bật 100% trên mọi nền video.
        - Trả về: (output_png_path, width, height)
        """
        from font_manager import FontManager
        font = None
        candidate_paths = [
            os.path.join(FontManager.WINDOWS_FONT_DIR, "arialbd.ttf"),
            os.path.join(FontManager.WINDOWS_FONT_DIR, "segoeuib.ttf"),
            os.path.join(FontManager.WINDOWS_FONT_DIR, "seguibl.ttf"),
            FontManager.get_banner_font_path(text)
        ]
        for p in candidate_paths:
            if p and os.path.exists(p):
                try:
                    font = ImageFont.truetype(p, font_size)
                    break
                except Exception:
                    pass
        if font is None:
            font = ImageFont.load_default()

        # Ngắt dòng tự động
        words = str(text or "").split()
        lines = []
        cur_line = []
        for w in words:
            cand = " ".join(cur_line + [w])
            bbox = font.getbbox(cand)
            if (bbox[2] - bbox[0]) <= max_width or not cur_line:
                cur_line.append(w)
            else:
                lines.append(" ".join(cur_line))
                cur_line = [w]
        if cur_line:
            lines.append(" ".join(cur_line))
        wrapped_text = "\n".join(lines) if lines else text

        # Đo kích thước tổng
        dummy = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        d_dummy = ImageDraw.Draw(dummy)
        bbox = d_dummy.multiline_textbbox(
            (0, 0), wrapped_text, font=font, align="center",
            stroke_width=stroke_width, spacing=12
        )

        pad = 24
        bw = int(bbox[2] - bbox[0] + pad * 2)
        bh = int(bbox[3] - bbox[1] + pad * 2)

        img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Lớp bóng đổ nhẹ phía dưới tạo chiều sâu
        draw.multiline_text(
            (pad - bbox[0] + 2, pad - bbox[1] + 2),
            wrapped_text,
            font=font,
            fill=(0, 0, 0, 160),
            stroke_width=2,
            stroke_fill=(0, 0, 0, 160),
            align="center",
            spacing=12
        )

        # Chữ trắng viền đen nét căng chuẩn TikTok
        draw.multiline_text(
            (pad - bbox[0], pad - bbox[1]),
            wrapped_text,
            font=font,
            fill=text_color,
            stroke_width=stroke_width,
            stroke_fill=stroke_color,
            align="center",
            spacing=12
        )

        if not output_png:
            import tempfile
            output_png = os.path.join(tempfile.gettempdir(), f"tiktok_badge_{time.time_ns()}.png")
        os.makedirs(os.path.dirname(os.path.abspath(output_png)), exist_ok=True)
        img.save(output_png, "PNG")
        return output_png, bw, bh

    @classmethod
    def create_playlist_dual_banners(
        cls,
        playlist_title: str,
        rank: int,
        video_title: str,
        work_dir: str,
        post_options: dict = None
    ) -> tuple:
        """
        Tạo 2 Title Banner chuẩn theo quy định:
        1. Banner trên cùng (Top Banner): In Tên Title của Playlist (ví dụ: 'GYPSY SISTERS').
        2. Banner giữa màn hình (Center Badge): Chuẩn phong cách TikTok chữ trắng viền đen 'No. {rank} : {video_title}'.
        """
        from editor_processor import EditorProcessor
        post_options = post_options or {}

        # 1. Top Banner: Tên Playlist
        p_title = str(playlist_title or "TOP HIGHLIGHT").strip().upper()
        top_opts = dict(post_options)
        top_opts["title_y_pos"] = int(post_options.get("playlist_title_y_pos", 160))
        top_dir = os.path.join(work_dir, "banners_top")
        os.makedirs(top_dir, exist_ok=True)
        top_banner_path, top_w, top_h = EditorProcessor._create_dynamic_title_banner_custom(
            top_dir, p_title, "", top_opts
        )
        top_x = int((1080 - top_w) / 2)
        top_y = top_opts["title_y_pos"]

        # 2. Center Banner: No. {rank} : {video_title} (Phong cách chữ TikTok KHÔNG hộp nền)
        clean_v_title = cls.clean_clip_title(video_title)
        title_style = post_options.get("compilation_title_style", "clean_original")
        if title_style == "only_rank" or not clean_v_title:
            badge_text = f"No. {rank}"
        else:
            badge_text = f"No. {rank} : {clean_v_title}"

        badge_dir = os.path.join(work_dir, f"banners_badge_{rank}")
        os.makedirs(badge_dir, exist_ok=True)
        badge_png_path = os.path.join(badge_dir, f"tiktok_badge_no_{rank}.png")

        badge_banner_path, badge_w, badge_h = cls.render_tiktok_style_badge(
            text=badge_text,
            output_png=badge_png_path,
            max_width=880,
            font_size=48,
            stroke_width=3
        )
        badge_x = int((1080 - badge_w) / 2)
        badge_y = int(post_options.get("clip_badge_y_pos", 1050))

        return top_banner_path, top_x, top_y, badge_banner_path, badge_x, badge_y

    @classmethod
    def execute_playlist_highlight_job(cls, job: dict, progress_callback=None) -> str:
        """
        Thực thi Tuyển tập Top Highlight từ YouTube Playlist:
        - Tải và xếp hạng N clip từ No. N ➔ No. 1.
        - AI tính toán đoạn thừa của từng video dựa trên thời lượng tổng (target_duration).
        - Tự động bù trừ thời lượng (Adaptive Balancing): Nếu có clip ngắn hơn hạn ngạch (vd: 60s < 80s),
          clip đó lấy trọn thời lượng, phần thiếu hụt được chia đều bù sang các clip khác dài hơn.
        - Giữ nguyên 100% âm thanh gốc, áp dụng CapCut Limiter +20dB chống rè.
        - Render 2 tầng title: Title Playlist ở trên cùng + No. X : Title video ở giữa màn hình.
        - Ghép nối các phân đoạn thành video hoàn chỉnh đúng thời lượng.
        """
        logger.info("🚀 [PLAYLIST HIGHLIGHT] BẮT ĐẦU DỰNG TUYỂN TẬP PLAYLIST TOP HIGHLIGHT...")
        started_at = time.time()

        job_id = job.get("id", f"pl_{int(time.time())}")
        work_dir = os.path.join(cls.TEMP_DIR, f"playlist_highlight_{job_id}")
        os.makedirs(work_dir, exist_ok=True)

        playlist_url = job.get("url") or job.get("playlist_url") or job.get("compilation_source_path") or ""
        if not playlist_url:
            raise ValueError("Thiếu link YouTube Playlist!")

        clip_count = int(job.get("compilation_clip_count", 5) or 5)
        target_duration = float(job.get("compilation_target_duration", 180.0) or 180.0)
        post_options = dict(job.get("post_options") or {})

        # Tự động thừa kế các thông số hậu kỳ màu sắc, crop, zoom từ config.json của Tóm Tắt Video nếu chưa có
        try:
            from config_manager import load_config as load_tomtat_config
            tt_cfg = load_tomtat_config()
            for k in [
                "use_crop", "crop_w", "crop_h", "crop_x", "crop_y", "crop_ratio",
                "use_blur_mask", "blur_shape", "blur_mask_x", "blur_mask_y", "blur_mask_w", "blur_mask_h",
                "color_look", "color_look_intensity", "color_look_stack",
                "temperature", "tint", "saturation", "exposure", "contrast",
                "highlights", "shadows", "whites", "blacks", "brilliance",
                "sharpen", "clarity", "grain", "blur", "vignette",
                "pos_x", "pos_y", "zoom_percent", "zoom_in", "scale_w", "scale_h", "blur_bg",
                "speed", "audio_boost"
            ]:
                if k not in post_options or post_options[k] is None or (k == "color_look_stack" and not post_options[k]):
                    if k in tt_cfg:
                        post_options[k] = tt_cfg[k]
        except Exception:
            pass

        try:
            from capcut_filters import apply_look
            post_options = apply_look(post_options)
        except Exception:
            pass

        # 1. Quét playlist
        if progress_callback:
            progress_callback(job_id, "scanning", "Đang quét nhanh Playlist YouTube...")
        raw_entries = cls.scan_youtube_playlist(playlist_url)
        if not raw_entries:
            raise RuntimeError(f"Không tìm thấy video nào trong playlist: {playlist_url}")

        playlist_title = raw_entries[0].get("playlist_title") or "TOP HIGHLIGHT"
        logger.info(f"📂 [PLAYLIST TITLE] '{playlist_title}', tìm thấy {len(raw_entries)} clips.")

        rank_by = str(job.get("compilation_rank_by") or post_options.get("compilation_rank_by") or "views").lower().strip()
        # 2. Bốc ngẫu nhiên & xếp hạng No. N ➔ No. 1
        if progress_callback:
            progress_callback(job_id, "ranking", f"Đang bốc {clip_count} clip và xếp hạng No.{clip_count} ➔ No.1 ({rank_by})...")
        ranked_items = cls.pick_and_rank_clips(raw_entries, count=clip_count, rank_by=rank_by)

        # 3. Tải chọn lọc đúng N clip
        if progress_callback:
            progress_callback(job_id, "downloading", f"Đang tải chọn lọc {len(ranked_items)} clips...")
        downloaded_items = cls.download_selected_clips(
            ranked_items, work_dir,
            lambda msg: progress_callback(job_id, "downloading", msg) if progress_callback else None,
            all_entries=raw_entries
        )

        # 4. Tính toán phân bổ thời lượng thích ứng thông minh (Adaptive Duration Balancing)
        bounds_list = []
        clip_durations = []
        usable_durations = []
        for it in downloaded_items:
            v_path = it.get("downloaded_video", "")
            d_tot = DownloaderProcessor.probe_duration_sec(v_path)
            i_end, o_start = cls.detect_intro_outro_bounds(v_path, d_tot)
            bounds_list.append((i_end, o_start))
            clip_durations.append(d_tot)
            usable_durations.append(max(5.0, o_start - i_end))

        allocated_durations = cls.calculate_adaptive_clip_durations(usable_durations, target_duration)
        logger.info(
            f"⏱️ [DYNAMIC DURATION] Mục tiêu: {target_duration:.1f}s | "
            f"Thời lượng gốc: {[f'{d:.1f}s' for d in clip_durations]} | "
            f"Thời lượng sạch (bỏ intro/outro): {[f'{d:.1f}s' for d in usable_durations]} | "
            f"Phân bổ AI: {[f'{d:.1f}s' for d in allocated_durations]}"
        )

        from editor_processor import EditorProcessor
        base_vf = EditorProcessor._build_vf_filter(post_options, input_label="[0:v]")
        speed = max(0.5, min(3.0, float(post_options.get("speed", 1.0) or post_options.get("source_speed", 1.0) or 1.0)))
        audio_boost = float(post_options.get("audio_boost", 20.0) or 20.0)

        tolerance_ratio = float(post_options.get("compilation_duration_tolerance", 0.20))
        segment_videos = []

        for idx, (item, allocated_d, total_clip_dur, (intro_end, outro_start)) in enumerate(
            zip(downloaded_items, allocated_durations, clip_durations, bounds_list), 1
        ):
            r = item["rank"]
            clip_video = item.get("downloaded_video", "")
            if not clip_video or not os.path.exists(clip_video):
                continue

            usable_dur = max(5.0, outro_start - intro_end)
            # Chọn điểm bắt đầu thông minh: nếu clip dài hơn nhiều so với hạn ngạch cần cắt,
            # lấy từ khoảng sau intro bumper để đảm bảo bắt trúng cao trào
            if usable_dur > allocated_d + 5:
                preferred_start = intro_end + max(1.0, (usable_dur - allocated_d) * 0.15)
            else:
                preferred_start = intro_end

            clean_start_t = cls.find_clean_clip_window(
                clip_video, allocated_d, preferred_start=preferred_start,
                min_start=intro_end, max_end=outro_start
            )

            # Linh hoạt +-20% theo nhịp cao trào & điểm ngắt câu tự nhiên (không ép cứng thời lượng, không cắt vào outro)
            effective_dur = cls.find_natural_clip_duration(
                clip_video, allocated_d, total_clip_dur=outro_start, start_t=clean_start_t, tolerance_ratio=tolerance_ratio
            )

            # Tính thời lượng cắt từ video gốc dựa trên tốc độ speed và khống chế không tràn qua outro_start
            max_avail_source = max(3.0, outro_start - clean_start_t)
            raw_cut_dur = effective_dur * speed if abs(speed - 1.0) >= 0.01 else effective_dur
            if raw_cut_dur > max_avail_source:
                raw_cut_dur = max_avail_source
                effective_dur = raw_cut_dur / speed if abs(speed - 1.0) >= 0.01 else raw_cut_dur

            if progress_callback:
                speed_txt = f", speed {speed:.2f}x" if abs(speed - 1.0) >= 0.01 else ""
                progress_callback(job_id, "rendering", f"AI đang tinh lược & dựng No. {r} ({effective_dur:.0f}s / {total_clip_dur:.0f}s{speed_txt})...")

            # Tạo Title 2 tầng:
            # 1. Trên cùng: Tên Playlist
            # 2. Giữa màn hình: No. {r} : {Tên clip}
            top_png, top_x, top_y, badge_png, badge_x, badge_y = cls.create_playlist_dual_banners(
                playlist_title=playlist_title,
                rank=r,
                video_title=item.get("title", ""),
                work_dir=os.path.join(work_dir, f"top_{r}"),
                post_options=post_options
            )

            seg_out = os.path.join(work_dir, f"segment_top_{r}.mp4")

            # Kiểm tra âm thanh gốc của clip
            has_orig_audio = False
            try:
                probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", clip_video]
                has_orig_audio = bool(subprocess.check_output(probe_cmd, creationflags=CREATE_NO_WINDOW).strip())
            except Exception:
                has_orig_audio = False

            a_tempo_str = EditorProcessor.build_atempo_filter(speed)
            a_tempo_prefix = f",{a_tempo_str}" if a_tempo_str else ""

            if has_orig_audio:
                audio_chain = (
                    f"[0:a]aformat=channel_layouts=stereo,aresample=44100,"
                    f"{EditorProcessor.capcut_audio_filter(audio_boost)}{a_tempo_prefix}[a_final]"
                )
            else:
                audio_chain = f"anullsrc=channel_layout=stereo:sample_rate=44100,atrim=0:{effective_dur:.2f}[a_final]"

            # 4.1 Quét kiểm duyệt AI xóa logo, sub cũ, banner (Gemini Grid Inspector)
            clean_chain_str = ""
            curr_v_label = "vout"
            enable_gemini_qc = bool(post_options.get("gemini_grid_inspector", True))
            from antigravity_processor import AntigravityProcessor
            api_key = str(post_options.get("gemini_api_key") or post_options.get("api_key") or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or "")
            has_ai_service = bool(api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or AntigravityProcessor.executable())

            if enable_gemini_qc and has_ai_service:
                try:
                    from ai_processor import AIProcessor
                    qc_frames_dir = os.path.join(work_dir, f"qc_frames_top_{r}")
                    os.makedirs(qc_frames_dir, exist_ok=True)

                    cmd_kf = [
                        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                        "-ss", f"{clean_start_t:.2f}", "-t", f"{raw_cut_dur:.2f}",
                        "-i", clip_video,
                        "-vf", "fps=1,scale=540:960:force_original_aspect_ratio=decrease,pad=540:960:(ow-iw)/2:(oh-ih)/2",
                        os.path.join(qc_frames_dir, "frame_%04d.jpg")
                    ]
                    if CREATE_NO_WINDOW:
                        subprocess.run(cmd_kf, check=False, creationflags=CREATE_NO_WINDOW)
                    else:
                        subprocess.run(cmd_kf, check=False)

                    frame_files = sorted([os.path.join(qc_frames_dir, f) for f in os.listdir(qc_frames_dir) if f.endswith(".jpg")])
                    if frame_files:
                        logger.info(f"🔍 [AI QC TOP {r}] Đang quét bản quyền, logo, sub cũ, banner cho clip No. {r}...")
                        step = max(1, len(frame_files) // 10)
                        sampled_frames = frame_files[::step][:12]
                        kf_items = []
                        for s_idx, fp in enumerate(sampled_frames, start=1):
                            sec = float(s_idx * step)
                            kf_items.append({
                                "path": fp,
                                "start_sec": max(0.0, sec - (step / 2.0)),
                                "end_sec": min(raw_cut_dur, sec + (step / 2.0))
                            })

                        model_name = str(post_options.get("gemini_model") or post_options.get("ai_model") or "")
                        detected_items = AIProcessor.inspect_90s_grid_for_copyright(
                            api_key=api_key, model_name=model_name,
                            duration_sec=raw_cut_dur,
                            keyframes=kf_items
                        )

                        if detected_items:
                            watermark_blurs = EditorProcessor.refine_detection_boxes_with_opencv(
                                detected_items, qc_frames_dir, duration_sec=raw_cut_dur
                            )
                        else:
                            watermark_blurs = []

                        clean_filters = []
                        filter_seq = 0
                        for item in watermark_blurs:
                            lbl = str(item.get("label") or "").strip().lower()
                            if "blood" in lbl:
                                continue
                            box = item.get("box", [0, 0, 0, 0])
                            ymin, xmin, ymax, xmax = box
                            st = max(0.0, float(item.get("start_sec", 0.0)))
                            en = min(raw_cut_dur, float(item.get("end_sec", raw_cut_dur)))
                            cond = f"between(t,{st:.2f},{en:.2f})"
                            filter_seq += 1
                            next_label = f"v_qc_{filter_seq}"
                            bx = max(0, min(1080 - 16, int(xmin * 1080) - 4))
                            by = max(0, min(1920 - 16, int(ymin * 1920) - 4))
                            bw = max(16, min(1080 - bx, int((xmax - xmin) * 1080) + 8))
                            bh = max(16, min(1920 - by, int((ymax - ymin) * 1920) + 8))
                            clean_filters.append(
                                f"[{curr_v_label}]split=2[orig_{filter_seq}][crop_{filter_seq}];"
                                f"[crop_{filter_seq}]crop={bw}:{bh}:{bx}:{by},gblur=sigma=6.0:steps=1[blur_{filter_seq}];"
                                f"[orig_{filter_seq}][blur_{filter_seq}]overlay={bx}:{by}:enable='{cond}'[{next_label}]"
                            )
                            curr_v_label = next_label
                            logger.info(f"🛡️ [AI QC BLUR TOP {r}] Che mờ {item.get('label', 'overlay')} tại [{st:.1f}s -> {en:.1f}s]")

                        if clean_filters:
                            clean_chain_str = ";" + ";".join(clean_filters)
                except Exception as qc_e:
                    logger.warning(f"⚠️ [AI QC TOP {r}] Bỏ qua quét AI do: {qc_e}")

            if abs(speed - 1.0) >= 0.01:
                v_speed_node = f"[{curr_v_label}]setpts={1.0 / speed:.6f}*PTS[v_sped];[v_sped]"
            else:
                v_speed_node = f"[{curr_v_label}]"

            filter_chain = (
                f"{base_vf}{clean_chain_str};"
                f"{v_speed_node}[1:v]overlay={top_x}:{top_y}[v_top];"
                f"[v_top][2:v]overlay={badge_x}:{badge_y},fps=30[v_final];"
                f"{audio_chain}"
            )

            cmd_seg = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clean_start_t:.2f}", "-t", f"{raw_cut_dur:.2f}",
                "-i", clip_video,
                "-i", top_png,
                "-i", badge_png,
                "-filter_complex", filter_chain,
                "-map", "[v_final]", "-map", "[a_final]",
                "-t", f"{effective_dur:.2f}",
                "-r", "30",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-profile:v", "high", "-level:v", "4.1",
                "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                seg_out
            ]
            run_ffmpeg_auto(cmd_seg, label=f"render_playlist_top_{r}", logger=logger)
            if os.path.exists(seg_out):
                segment_videos.append(seg_out)

        if not segment_videos:
            raise RuntimeError("Không xuất được phân đoạn nào từ Playlist!")

        # 5. Ghép tất cả các clip theo thứ tự No. N ➔ No. 1
        os.makedirs(cls.OUTPUT_DIR, exist_ok=True)
        safe_name = re.sub(r'[\\/*?:"<>|]', "", playlist_title).strip().replace(" ", "_") or "Playlist_Top_Highlight"
        final_output = os.path.join(cls.OUTPUT_DIR, f"{safe_name}_{int(time.time())}.mp4")

        final_concat_list = os.path.join(work_dir, "final_concat.txt")
        with open(final_concat_list, "w", encoding="utf-8") as f:
            for s_path in segment_videos:
                safe_sp = os.path.abspath(s_path).replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{safe_sp}'\n")

        cmd_final = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", final_concat_list,
            "-c", "copy", final_output
        ]
        if CREATE_NO_WINDOW:
            subprocess.run(cmd_final, check=False, creationflags=CREATE_NO_WINDOW)
        else:
            subprocess.run(cmd_final, check=False)

        total_sec = time.time() - started_at
        logger.info(f"🎉 [HOÀN TẤT] Tuyển tập Playlist hoàn tất ({total_sec:.1f}s): {final_output}")
        return final_output

    @classmethod
    def execute_compilation_job(cls, job: dict, progress_callback=None) -> str:
        """
        Hàm điều phối thực thi hoàn chỉnh Job Tuyển tập Top-N:
        1. Quét thông tin playlist / folder.
        2. Bốc ngẫu nhiên N clip và xếp hạng từ No. N về No. 1.
        3. Tải chọn lọc (nếu là YouTube).
        4. Bốc sub / transcript.
        5. Sinh kịch bản countdown đếm ngược trọn gói bằng AI.
        6. Sinh voice song song.
        7. Cắt teaser highlight 2-3s.
        8. Render GPU NVENC trực tiếp ra file thành phẩm hoàn chỉnh.
        """
        logger.info("🚀 [COUNTDOWN COMPILATION] BẮT ĐẦU QUY TRÌNH BIÊN TẬP TUYỂN TẬP TOP...")
        started_at = time.time()

        job_id = job.get("id", "top_compilation")
        work_dir = os.path.join(cls.TEMP_DIR, f"compilation_{job_id}")
        os.makedirs(work_dir, exist_ok=True)

        source_type = job.get("compilation_source_type", "playlist")
        source_path = job.get("url") or job.get("compilation_source_path") or job.get("local_path", "")
        clip_count = int(job.get("compilation_clip_count", 5) or 5)
        target_duration = float(job.get("compilation_target_duration") or job.get("video_min_duration_sec") or 180.0)
        title_style = job.get("compilation_title_style", "clean_original")
        enable_teaser = bool(job.get("compilation_enable_teaser", True))
        teaser_duration = float(job.get("compilation_teaser_duration", 3.0) or 3.0)

        post_options = job.get("post_options", {})
        try:
            from capcut_filters import apply_look
            post_options = apply_look(post_options)
        except Exception:
            pass
        market = post_options.get("target_market") or "US"
        voice = job.get("voice") or "Google Translate TTS (Miễn phí)"
        locale = post_options.get("output_locale") or "en-US"
        model_name = job.get("model") or "Google Antigravity (Local)"
        api_key = job.get("api_key") or ""
        storytelling_hint = job.get("prompt") or ""

        # 1. Quét danh sách
        if progress_callback:
            progress_callback(job_id, "scanning", "Đang quét danh sách video...")
        if source_type == "local_folder" or (source_path and os.path.isdir(source_path)):
            raw_entries = cls.scan_local_folder(source_path)
        else:
            raw_entries = cls.scan_youtube_playlist(source_path)

        if not raw_entries:
            raise RuntimeError(f"Không tìm thấy video nào từ nguồn: {source_path}")

        rank_by = str(job.get("compilation_rank_by") or post_options.get("compilation_rank_by") or "views").lower().strip()
        # 2. Bốc ngẫu nhiên N clip & Xếp hạng No. N ➔ No. 1
        if progress_callback:
            progress_callback(job_id, "ranking", f"Đang bốc ngẫu nhiên {clip_count} clip và xếp hạng ({rank_by})...")
        ranked_items = cls.pick_and_rank_clips(raw_entries, count=clip_count, rank_by=rank_by)

        # 3. Tải chọn lọc
        if progress_callback:
            progress_callback(job_id, "downloading", f"Đang tải chọn lọc {len(ranked_items)} clip...")
        downloaded_items = cls.download_selected_clips(
            ranked_items, work_dir,
            lambda msg: progress_callback(job_id, "downloading", msg) if progress_callback else None,
            all_entries=raw_entries
        )

        # 4. Bốc sub
        if progress_callback:
            progress_callback(job_id, "transcribing", "Bốc phụ đề lời thoại...")
        subbed_items = cls.extract_subtitles_or_transcripts(downloaded_items, work_dir)

        # 5. Viết kịch bản AI Countdown
        if progress_callback:
            progress_callback(job_id, "generating_script", "AI đang viết kịch bản đếm ngược...")
        comp_title, scripted_items = cls.generate_countdown_batch_script(
            subbed_items,
            total_duration_sec=target_duration,
            market=market,
            title_style=title_style,
            model_name=model_name,
            api_key=api_key,
            custom_prompt_hint=storytelling_hint,
            work_dir=work_dir,
        )

        # 6. Sinh voice TTS song song
        if progress_callback:
            progress_callback(job_id, "generating_voice", "Đang sinh giọng đọc AI...")
        voiced_items = cls.generate_voices_parallel(scripted_items, voice, locale, work_dir)

        # 7. Trích xuất Teaser Highlight (nếu bật)
        if enable_teaser:
            if progress_callback:
                progress_callback(job_id, "teasers", "Cắt Highlight Teaser trước mỗi No.X...")
            cls.extract_clip_teasers(voiced_items, teaser_duration=teaser_duration, work_dir=work_dir, post_options=post_options)

        # 8. Render GPU NVENC từng phân đoạn và ghép thành phẩm
        if progress_callback:
            progress_callback(job_id, "rendering", "Đang render GPU NVENC video hoàn chỉnh...")

        segment_videos = []
        for it in voiced_items:
            r = it["rank"]
            clip_video = it.get("downloaded_video")
            voice_file = it.get("voice_path")
            voice_dur = float(it.get("voice_duration") or 25.0)
            teaser_file = it.get("teaser_path") if enable_teaser else None

            # Tạo Title Banner chuẩn Tab 3 nhận 100% config
            badge_png = os.path.join(work_dir, f"top_{r}", f"badge_{r}.png")
            _, banner_x, banner_y = cls.create_top_badge_overlay(
                r, it.get("display_title", ""), badge_png,
                title_style=title_style, post_options=post_options
            )

            # Render phân đoạn Top r
            seg_out = os.path.join(work_dir, f"segment_top_{r}.mp4")
            
            # Lấy speed, audio_boost, mix_audio từ post_options
            speed = max(0.5, min(3.0, float(post_options.get("speed", 1.0) or 1.0)))
            audio_boost = float(post_options.get("audio_boost", 0.0) or 0.0)
            mix_audio = bool(post_options.get("mix_original_audio_with_voice", False))
            mix_percent = max(0.0, min(100.0, float(post_options.get("original_audio_mix_percent", 30.0) or 30.0))) / 100.0

            # Tính thời lượng voice và video sau khi áp dụng speed
            effective_voice_dur = voice_dur / speed if abs(speed - 1.0) >= 0.01 else voice_dur
            video_dur = effective_voice_dur + 0.8
            raw_cut_dur = video_dur * speed if abs(speed - 1.0) >= 0.01 else video_dur

            # Tự động dò và cắt bỏ Intro Bumper thừa
            total_clip_dur = DownloaderProcessor.probe_duration_sec(clip_video)
            intro_end, outro_start = cls.detect_intro_outro_bounds(clip_video, total_clip_dur)
            usable_dur = max(3.0, outro_start - intro_end)
            if usable_dur > raw_cut_dur + 4:
                preferred_start = intro_end + max(1.0, (usable_dur - raw_cut_dur) * 0.15)
            else:
                preferred_start = intro_end

            clean_start_t = cls.find_clean_clip_window(
                clip_video, raw_cut_dur, preferred_start=preferred_start,
                min_start=intro_end, max_end=outro_start
            )

            max_avail_source = max(3.0, outro_start - clean_start_t)
            if raw_cut_dur > max_avail_source:
                raw_cut_dur = max_avail_source
                video_dur = raw_cut_dur / speed if abs(speed - 1.0) >= 0.01 else raw_cut_dur

            # 1. Hậu kỳ màu sắc, crop, zoom, blur nền chuẩn 100% từ 1 video (EditorProcessor)
            from editor_processor import EditorProcessor
            base_vf = EditorProcessor._build_vf_filter(post_options, input_label="[0:v]")

            # Chuẩn hóa âm lượng voice bằng capcut_audio_filter chuẩn 100% của 1 video (nén compressor + limiter, chống rè tuyệt đối)
            voice_norm = os.path.join(work_dir, f"top_{r}", "voice_normalized.wav")
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", voice_file,
                "-ar", "44100", "-ac", "2", "-af", EditorProcessor.capcut_audio_filter(audio_boost), voice_norm
            ], check=True, creationflags=CREATE_NO_WINDOW)

            # 2. Tạo file SRT phụ đề lời thoại cho phân đoạn.
            # Dùng voice_dur thô (chưa speed) vì setpts={1.0/speed}*PTS sẽ co toàn bộ hình + sub
            # khớp chính xác tuyệt đối với audio atempo={speed} (đồng bộ 100% theo EditorProcessor).
            sub_srt = os.path.join(work_dir, f"top_{r}", f"sub_top_{r}.srt")
            cls.generate_srt_for_narration(it.get("narration", ""), it.get("transition", ""), voice_dur, sub_srt)

            sub_filter_spec = ""
            if os.path.exists(sub_srt) and os.path.getsize(sub_srt) > 0:
                abs_srt = os.path.abspath(sub_srt).replace("\\", "/")
                if ":" in abs_srt:
                    drive, p_part = abs_srt.split(":", 1)
                    formatted_srt = f"{drive}\\:{p_part}"
                else:
                    formatted_srt = abs_srt

                from font_manager import FontManager
                sample_narration = str(it.get("narration") or it.get("display_title") or "")
                style_type = str(post_options.get("sub_style_type", "tiktok_slim"))
                style_specs = FontManager.get_subtitle_style_specs(
                    style_type=style_type,
                    sample_text=sample_narration,
                    custom_overrides=post_options
                )
                font_name = style_specs["font_name"]
                sub_size = style_specs["font_size"]
                sub_outline = style_specs["outline"]
                sub_shadow = style_specs["shadow"]
                sub_bold = style_specs["bold"]
                sub_margin_v = int(post_options.get("sub_margin_v", 80))
                primary_color = post_options.get("sub_color", "&HFFFFFF&")
                outline_color = post_options.get("sub_outline_color", "&H000000&")

                basic_sub_style = (
                    f"FontName={font_name},FontSize={sub_size},Bold={sub_bold},"
                    f"PrimaryColour={primary_color},OutlineColour={outline_color},"
                    f"BorderStyle=1,Outline={sub_outline},Shadow={sub_shadow},"
                    f"Alignment=2,MarginL=70,MarginR=70,MarginV={sub_margin_v},WrapStyle=1"
                )
                sub_filter_spec = f",subtitles='{formatted_srt}':force_style='{basic_sub_style}'"

            speed_v_filter = f",setpts={1.0 / speed:.4f}*PTS" if abs(speed - 1.0) >= 0.01 else ""

            # Audio filter chuẩn 100% từ 1 video (EditorProcessor)
            if abs(speed - 1.0) >= 0.01:
                voice_chain = f"alimiter=limit=0.89:attack=5:release=80:level=false,atempo={speed:.4f}"
            else:
                voice_chain = "alimiter=limit=0.89:attack=5:release=80:level=false"

            # Kiểm tra xem clip gốc có stream audio không để mix tiếng nền
            has_orig_audio = False
            if mix_audio:
                try:
                    probe_cmd = ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", clip_video]
                    has_orig_audio = bool(subprocess.check_output(probe_cmd).strip())
                except Exception:
                    has_orig_audio = False

            if mix_audio and has_orig_audio:
                scramble_audio = bool(post_options.get("scramble_original_audio", True))
                bed_chain = []
                if scramble_audio:
                    bed_chain.append(
                        "highpass=f=65,equalizer=f=3000:t=q:w=1:g=2.5,equalizer=f=600:t=q:w=1:g=-2.5,"
                        "aecho=0.8:0.88:18:0.12,asetrate=44100*1.03,atempo=0.970873786,aresample=44100"
                    )
                if abs(speed - 1.0) >= 0.01:
                    bed_chain.append(f"atempo={speed:.4f}")
                bed_chain.append(f"volume={mix_percent:.4f}")

                filter_chain = (
                    f"{base_vf};"
                    f"[vout][2:v]overlay={banner_x}:{banner_y}{sub_filter_spec}{speed_v_filter},fps=30[v_badged];"
                    f"[1:a]{voice_chain}[voicea];"
                    f"[0:a]{','.join(bed_chain)},apad[beda];"
                    f"[voicea][beda]amix=inputs=2:duration=first:dropout_transition=0,"
                    f"alimiter=limit=0.95:attack=5:release=80:level=false[a_final]"
                )
            else:
                filter_chain = (
                    f"{base_vf};"
                    f"[vout][2:v]overlay={banner_x}:{banner_y}{sub_filter_spec}{speed_v_filter},fps=30[v_badged];"
                    f"[1:a]{voice_chain}[a_final]"
                )

            # Render segment với profile chuẩn H.264 High/Level 4.1 và Closed GOP 60 khớp 100% với teaser
            cmd_seg = [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clean_start_t:.2f}", "-t", f"{raw_cut_dur:.2f}", "-i", clip_video,
                "-i", voice_norm,
                "-i", badge_png,
                "-filter_complex", filter_chain,
                "-map", "[v_badged]", "-map", "[a_final]",
                "-t", f"{video_dur:.2f}",
                "-r", "30",
                "-pix_fmt", "yuv420p",
                "-c:v", "libx264", "-preset", "fast", "-profile:v", "high", "-level:v", "4.1",
                "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-ac", "2",
                seg_out
            ]
            run_ffmpeg_auto(cmd_seg, label=f"render_top_{r}", logger=logger)

            # Nếu có teaser, ghép teaser trước phân đoạn
            if teaser_file and os.path.exists(teaser_file):
                combo_seg = os.path.join(work_dir, f"combo_top_{r}.mp4")
                concat_list = os.path.join(work_dir, f"concat_{r}.txt")
                safe_teaser = os.path.abspath(teaser_file).replace('\\', '/').replace("'", "'\\''")
                safe_seg = os.path.abspath(seg_out).replace('\\', '/').replace("'", "'\\''")
                with open(concat_list, "w", encoding="utf-8") as f:
                    f.write(f"file '{safe_teaser}'\n")
                    f.write(f"file '{safe_seg}'\n")

                cmd_concat = [
                    "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
                    "-c", "copy", combo_seg
                ]
                if CREATE_NO_WINDOW:
                    subprocess.run(cmd_concat, check=False, creationflags=CREATE_NO_WINDOW)
                else:
                    subprocess.run(cmd_concat, check=False)
                segment_videos.append(combo_seg if os.path.exists(combo_seg) else seg_out)
            else:
                segment_videos.append(seg_out)

        # Ghép tất cả các phân đoạn lại theo thứ tự từ No. N về No. 1
        os.makedirs(cls.OUTPUT_DIR, exist_ok=True)
        safe_title = re.sub(r'[\\/*?:"<>|]', "", comp_title).strip() or "Top_Countdown_Compilation"
        final_output = os.path.join(cls.OUTPUT_DIR, f"{safe_title}_{int(time.time())}.mp4")

        final_concat_list = os.path.join(work_dir, "final_concat.txt")
        with open(final_concat_list, "w", encoding="utf-8") as f:
            for s_path in segment_videos:
                safe_sp = os.path.abspath(s_path).replace('\\', '/').replace("'", "'\\''")
                f.write(f"file '{safe_sp}'\n")

        cmd_final = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", final_concat_list,
            "-c", "copy", final_output
        ]
        if CREATE_NO_WINDOW:
            subprocess.run(cmd_final, check=False, creationflags=CREATE_NO_WINDOW)
        else:
            subprocess.run(cmd_final, check=False)

        total_sec = time.time() - started_at
        logger.info(f"🎉 [HOÀN TẤT] Video Tuyển tập Top Countdown xuất bản thành công ({total_sec:.1f}s): {final_output}")
        return final_output
