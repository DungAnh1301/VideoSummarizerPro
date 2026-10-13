"""Nhân Viên 1: Chuyên Gia Dựng Hình Ảnh (Lead Visual Editor Engine).

Nhiệm vụ:
- Thiết lập khung hình chuẩn 9:16 Canvas (1080x1920), nền blur 2 đầu (boxblur 24:6), video trung tâm 1188px.
- Speaker Centering: Bắt chính xác mặt người nói vào tâm giữa (X=540):
  + pan="left": x_off = 0.28 * max_x_off
  + pan="right": x_off = 0.52 * max_x_off
  + pan="center": x_off = 0.50 * max_x_off
- Emotion Punch Zoom (1.18x): Phóng to bắt cảm xúc trên nền đã blur, giữ nguyên bố cục thẩm mỹ.
- Visual Transition: Chèn kỹ xảo Motion Blur Whip Transition (xfade=hblur, d=0.35s) giữa các cảnh.
- Trả về video ghép các cảnh cùng danh sách mốc thời gian chuyển cảnh (transition_timestamps).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("VisualEditorEngine")

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from part_splitter_engine import (
    probe_duration_sec,
    probe_video_dimensions,
    get_best_video_encoder,
    _ff_run,
)


try:
    import cv2
except ImportError:
    cv2 = None


class VisualEditorEngine:
    """Nhân Viên 1 - Lead Visual Editor & AI Cinematographer (Đạo Diễn Hình Ảnh)."""

    @classmethod
    def _init_detectors(cls) -> Dict[str, Any]:
        """Khởi tạo bộ dò khuôn mặt đa tầng:
        1. OpenCV YuNet DNN (FaceDetectorYN) độ chính xác cao với cả góc nghiêng, mặt nhỏ.
        2. Haar Cascade Frontal + Profile (trái/phải) dự phòng.
        """
        detectors: Dict[str, Any] = {
            "yunet": None,
            "frontal": None,
            "profile": None,
        }
        if cv2 is None:
            return detectors

        try:
            onnx_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_detection_yunet_2023mar.onnx")
            if os.path.isfile(onnx_path) and hasattr(cv2, "FaceDetectorYN"):
                detectors["yunet"] = cv2.FaceDetectorYN.create(onnx_path, "", (960, 540), 0.38, 0.3, 5000)
        except Exception as e:
            logger.warning(f"Không khởi tạo được YuNet DNN: {e}")

        try:
            f_path = cv2.data.haarcascades + "haarcascade_frontalface_alt2.xml"
            if not os.path.exists(f_path):
                f_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            if os.path.exists(f_path):
                detectors["frontal"] = cv2.CascadeClassifier(f_path)

            p_path = cv2.data.haarcascades + "haarcascade_profileface.xml"
            if os.path.exists(p_path):
                detectors["profile"] = cv2.CascadeClassifier(p_path)
        except Exception as e:
            logger.warning(f"Không khởi tạo được Haar Cascade: {e}")

        return detectors

    @classmethod
    def _detect_faces_and_motion_at(
        cls,
        cap,
        detectors: Dict[str, Any],
        t_sec: float
    ) -> Tuple[List[Dict[str, float]], Optional[Tuple[float, float, float]]]:
        """Quét khung hình tại mốc t_sec (và t_sec + 0.2s) để tìm:
        - Danh sách khuôn mặt: [{'u': 0..1, 'v': 0..1, 'area': %, 'speaker_score': float}]
        - Trọng tâm chuyển động hình ảnh (u_mot, v_mot, energy) khi cảnh quay xa/B-roll không rõ mặt.
        """
        if cap is None or cv2 is None:
            return [], None

        import numpy as np
        faces_list: List[Dict[str, float]] = []
        motion_center: Optional[Tuple[float, float, float]] = None

        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_sec) * 1000.0)
            ret1, frame1 = cap.read()
            if not ret1 or frame1 is None:
                return [], None

            small1 = cv2.resize(frame1, (960, 540))
            gray1 = cv2.cvtColor(small1, cv2.COLOR_BGR2GRAY)

            # Đọc thêm 1 frame cách 0.20s để đo chuyển động miệng/đầu (Active Speaker) & chuyển động toàn cảnh
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t_sec + 0.20) * 1000.0)
            ret2, frame2 = cap.read()
            gray2 = None
            diff_frame = None
            if ret2 and frame2 is not None:
                small2 = cv2.resize(frame2, (960, 540))
                gray2 = cv2.cvtColor(small2, cv2.COLOR_BGR2GRAY)
                diff_frame = cv2.absdiff(cv2.GaussianBlur(gray1, (7, 7), 0), cv2.GaussianBlur(gray2, (7, 7), 0))

                # Tính trọng tâm chuyển động trong vùng trung tâm khung hình
                weights = np.zeros_like(diff_frame, dtype=np.float32)
                weights[60:480, 60:900] = diff_frame[60:480, 60:900].astype(np.float32)
                weights[weights < 9.0] = 0.0
                tot_w = float(weights.sum())
                if tot_w > 1500.0:
                    xs = np.arange(960, dtype=np.float32)
                    ys = np.arange(540, dtype=np.float32)
                    u_m = float((weights.sum(axis=0) * xs).sum() / tot_w) / 960.0
                    v_m = float((weights.sum(axis=1) * ys).sum() / tot_w) / 540.0
                    motion_center = (max(0.15, min(0.85, u_m)), max(0.20, min(0.75, v_m)), tot_w)

            raw_boxes = []
            yunet = detectors.get("yunet")
            if yunet is not None:
                _, dets = yunet.detect(small1)
                if dets is not None:
                    for d in dets:
                        x, y, w, h = float(d[0]), float(d[1]), float(d[2]), float(d[3])
                        conf = float(d[-1])
                        area_pct = (w * h) / (960.0 * 540.0) * 100.0
                        if conf >= 0.38 and area_pct >= 0.05:
                            raw_boxes.append((x, y, w, h, conf))

            # Fallback sang Haar Frontal + Profile nếu YuNet không bắt được
            if not raw_boxes:
                frontal = detectors.get("frontal")
                profile = detectors.get("profile")
                if frontal is not None:
                    for (x, y, w, h) in frontal.detectMultiScale(gray1, 1.12, 4, minSize=(32, 32)):
                        raw_boxes.append((float(x), float(y), float(w), float(h), 0.7))
                if not raw_boxes and profile is not None:
                    for (x, y, w, h) in profile.detectMultiScale(gray1, 1.12, 4, minSize=(32, 32)):
                        raw_boxes.append((float(x), float(y), float(w), float(h), 0.65))
                    for (x, y, w, h) in profile.detectMultiScale(cv2.flip(gray1, 1), 1.12, 4, minSize=(32, 32)):
                        real_x = 960.0 - float(x) - float(w)
                        raw_boxes.append((real_x, float(y), float(w), float(h), 0.65))

            for (x, y, w, h, conf) in raw_boxes:
                u = (x + w * 0.5) / 960.0
                v = (y + h * 0.5) / 540.0
                area_pct = (w * h) / (960.0 * 540.0) * 100.0
                if not (0.06 <= u <= 0.94 and 0.08 <= v <= 0.85):
                    continue

                # Tính điểm người đang nói (chuyển động vùng nửa dưới khuôn mặt + kích thước mặt)
                mouth_motion = 1.0
                if diff_frame is not None:
                    x1 = max(0, int(x))
                    x2 = min(960, int(x + w))
                    y1 = max(0, int(y + h * 0.45))
                    y2 = min(540, int(y + h * 1.05))
                    if x2 > x1 and y2 > y1:
                        roi = diff_frame[y1:y2, x1:x2]
                        mouth_motion = float(roi.mean()) + 1.0

                speaker_score = (area_pct ** 0.65) * mouth_motion * conf
                faces_list.append({
                    "u": max(0.12, min(0.88, u)),
                    "v": max(0.18, min(0.75, v)),
                    "area": area_pct,
                    "speaker_score": speaker_score,
                })
        except Exception:
            pass

        return faces_list, motion_center

    @classmethod
    def plan_scene_camera_shots(
        cls,
        cap,
        detectors: Dict[str, Any],
        shots: List[Dict[str, Any]],
        scale_w: int,
        target_fg_h: int
    ) -> List[Dict[str, Any]]:
        """Đạo Diễn Hình Ảnh (AI Cinematographer) cho toàn bộ 1 phân cảnh ~20s:
        - Quét toàn bộ các shots trong cảnh để nhận diện tất cả nhân vật (Trái / Phải / Giữa) và người đang nói.
        - Quyết định chính xác cho từng shot:
          + Zoom Level: Zoom Out (1.00x toàn cảnh) <-> Medium Speaker (1.14x cận trung) <-> Close-Up Zoom In (1.25x đặc tả biểu cảm).
          + Dịch chuyển Camera (x_off, y_off): Căn chuẩn tâm mặt nhân vật theo cả chiều ngang (X=540) và chiều dọc (tỉ lệ vàng mắt Y=36%).
          + Đảm bảo các shot liên tiếp luôn có sự thay đổi góc máy (dịch chuyển Trái/Phải hoặc Zoom In/Zoom Out) rõ ràng, mượt mà.
        """
        planned: List[Dict[str, Any]] = []
        scene_all_faces: List[Dict[str, float]] = []
        per_shot_data: List[Dict[str, Any]] = []

        # Bước 1: Quét dữ liệu thị giác thực tế của từng shot trong cảnh 20s
        for sh in shots:
            st = float(sh["start"])
            en = float(sh["end"])
            dur = max(0.5, en - st)
            sample_times = [
                st + dur * 0.18,
                st + dur * 0.45,
                st + dur * 0.72,
            ]
            shot_faces: List[Dict[str, float]] = []
            shot_motions: List[Tuple[float, float, float]] = []
            for t_s in sample_times:
                f_list, mot = cls._detect_faces_and_motion_at(cap, detectors, t_s)
                shot_faces.extend(f_list)
                scene_all_faces.extend(f_list)
                if mot is not None:
                    shot_motions.append(mot)

            per_shot_data.append({
                "faces": shot_faces,
                "motions": shot_motions,
            })

        # Phân cụm nhân vật Trái / Phải trên toàn phân cảnh (nếu cảnh có 2 người nói chuyện)
        left_cluster = [f for f in scene_all_faces if f["u"] < 0.46]
        right_cluster = [f for f in scene_all_faces if f["u"] >= 0.46]
        scene_u_left = (
            sum(f["u"] * f["speaker_score"] for f in left_cluster) / sum(f["speaker_score"] for f in left_cluster)
            if left_cluster else None
        )
        scene_v_left = (
            sum(f["v"] * f["speaker_score"] for f in left_cluster) / sum(f["speaker_score"] for f in left_cluster)
            if left_cluster else 0.36
        )
        scene_u_right = (
            sum(f["u"] * f["speaker_score"] for f in right_cluster) / sum(f["speaker_score"] for f in right_cluster)
            if right_cluster else None
        )
        scene_v_right = (
            sum(f["v"] * f["speaker_score"] for f in right_cluster) / sum(f["speaker_score"] for f in right_cluster)
            if right_cluster else 0.36
        )

        prev_zoom = 0.0
        prev_u = -1.0

        # Bước 2: Quyết định thông số Camera (u_focus, v_focus, zoom_factor) cho từng shot
        for sh_idx, sh in enumerate(shots):
            s_data = per_shot_data[sh_idx]
            faces = s_data["faces"]
            motions = s_data["motions"]
            role = str(sh.get("camera_role") or "").lower().strip()
            pan_hint = str(sh.get("pan", "center")).lower().strip()
            punch_flag = bool(sh.get("punch", False) or sh.get("punch_in", False))

            # Xác định tọa độ trọng tâm (u_focus, v_focus)
            u_focus = 0.50
            v_focus = 0.36

            if faces:
                # Nhóm các mặt tìm thấy trong shot này
                f_sorted = sorted(faces, key=lambda x: x["speaker_score"], reverse=True)
                best_f = f_sorted[0]

                # Kiểm tra xem có 2 nhân vật đứng cách nhau trong shot không
                u_min = min(f["u"] for f in faces)
                u_max = max(f["u"] for f in faces)
                has_two_people = (u_max - u_min) >= 0.16 and len(faces) >= 2

                if role == "wide_zoom_out" and has_two_people:
                    # Góc toàn cảnh 2 người: lấy trung điểm giữa 2 nhân vật
                    u_focus = 0.5 * (u_min + u_max)
                    v_focus = sum(f["v"] for f in faces) / len(faces)
                elif has_two_people:
                    # Ưu tiên bắt vào người đang nói hoặc phân vai trái/phải theo nhịp dựng
                    left_f = [f for f in faces if f["u"] <= 0.5 * (u_min + u_max)]
                    right_f = [f for f in faces if f["u"] > 0.5 * (u_min + u_max)]
                    if pan_hint == "left" and left_f:
                        chosen = max(left_f, key=lambda x: x["speaker_score"])
                    elif pan_hint == "right" and right_f:
                        chosen = max(right_f, key=lambda x: x["speaker_score"])
                    else:
                        chosen = best_f
                    u_focus = chosen["u"]
                    v_focus = chosen["v"]
                else:
                    # Chỉ thấy 1 mặt trong shot này -> bám chuẩn vào mặt đó
                    u_focus = best_f["u"]
                    v_focus = best_f["v"]
            else:
                # Nếu shot này nhân vật quay lưng / khuất mặt tạm thời:
                # Ưu tiên kế thừa vị trí nhân vật Trái/Phải của cảnh hoặc trọng tâm chuyển động (Motion Saliency)
                if pan_hint == "left" and scene_u_left is not None:
                    u_focus = scene_u_left
                    v_focus = scene_v_left
                elif pan_hint == "right" and scene_u_right is not None:
                    u_focus = scene_u_right
                    v_focus = scene_v_right
                elif motions:
                    tot_e = sum(m[2] for m in motions)
                    u_focus = sum(m[0] * m[2] for m in motions) / tot_e
                    v_focus = sum(m[1] * m[2] for m in motions) / tot_e
                elif scene_all_faces:
                    u_focus = sum(f["u"] for f in scene_all_faces) / len(scene_all_faces)
                    v_focus = sum(f["v"] for f in scene_all_faces) / len(scene_all_faces)
                else:
                    u_focus = 0.38 if pan_hint == "left" else (0.62 if pan_hint == "right" else 0.50)
                    v_focus = 0.40

            # Quyết định độ phóng đại ống kính (Zoom Out / Medium / Close-Up Zoom In)
            if role == "wide_zoom_out":
                zoom_factor = 1.00
            elif role == "closeup_zoom_in" or punch_flag:
                zoom_factor = 1.25
            elif role == "speaker_left":
                zoom_factor = 1.14
            elif role == "speaker_right":
                zoom_factor = 1.08
            else:
                zoom_factor = 1.24 if punch_flag else (1.12 if sh_idx % 2 == 1 else 1.00)

            # Đảm bảo 2 shots liên tiếp trong cùng 1 cảnh luôn có sự biến thiên rõ rệt (Zoom In / Zoom Out hoặc Dịch chuyển Camera)
            if abs(zoom_factor - prev_zoom) < 0.06 and abs(u_focus - prev_u) < 0.07:
                if zoom_factor <= 1.05:
                    zoom_factor = 1.16
                elif zoom_factor >= 1.20:
                    zoom_factor = 1.04
                else:
                    zoom_factor = 1.25

            # Tính toán kích thước scale & tọa độ crop (x_off, y_off) căn chuẩn mặt vào tâm vàng
            z_w = int(round(scale_w * zoom_factor))
            z_w = z_w + (z_w % 2)
            z_h = int(round(target_fg_h * zoom_factor))
            z_h = z_h + (z_h % 2)

            desired_x = int(round(u_focus * z_w - 540.0))
            x_off = max(0, min(max(0, z_w - 1080), desired_x))
            x_off = x_off - (x_off % 2)

            # Căn chiều dọc: đặt khuôn mặt (v_focus) vào đường tỉ lệ vàng 36% từ đỉnh khung trung tâm
            desired_y = int(round(v_focus * z_h - target_fg_h * 0.36))
            y_off = max(0, min(max(0, z_h - target_fg_h), desired_y))
            y_off = y_off - (y_off % 2)

            prev_zoom = zoom_factor
            prev_u = u_focus

            sh_plan = dict(sh)
            sh_plan["u_focus"] = round(u_focus, 3)
            sh_plan["v_focus"] = round(v_focus, 3)
            sh_plan["zoom_factor"] = round(zoom_factor, 2)
            sh_plan["z_w"] = z_w
            sh_plan["z_h"] = z_h
            sh_plan["x_off"] = x_off
            sh_plan["y_off"] = y_off
            planned.append(sh_plan)

        return planned

    @classmethod
    def render_scenes_with_transitions(
        cls,
        source_video: str,
        scenes: List[Dict[str, Any]],
        output_merged_path: str,
        work_dir: str,
        transition_duration: float = 0.35,
        target_fg_h: int = 1188,
        config: Optional[Dict[str, Any]] = None,
        log_fn=None
    ) -> Tuple[str, List[float]]:
        """Render từng phân cảnh với Đạo Diễn Hình Ảnh (Zoom In / Zoom Out / Speaker Shift) và nối bằng xfade=hblur."""
        def _log(msg: str):
            logger.info(msg)
            if callable(log_fn):
                try:
                    log_fn(msg)
                except Exception:
                    pass

        os.makedirs(work_dir, exist_ok=True)
        os.makedirs(os.path.dirname(os.path.abspath(output_merged_path)), exist_ok=True)

        src_w, src_h = probe_video_dimensions(source_video)
        y_top_boundary = (1920 - target_fg_h) // 2

        scale_w = int(round(float(target_fg_h) * float(src_w) / max(1.0, float(src_h))))
        scale_w = scale_w + (scale_w % 2)

        # Khởi tạo Đạo Diễn Hình Ảnh (YuNet DNN + Cascades + Motion Saliency)
        detectors = cls._init_detectors()
        cap = None
        if cv2 is not None:
            try:
                cap = cv2.VideoCapture(source_video)
            except Exception as e:
                logger.warning(f"Không mở được VideoCapture: {e}")

        # Chuẩn bị bộ lọc màu CapCut nếu có cấu hình chỉnh màu
        color_fx = ""
        try:
            from capcut_filters import build_adjust_filters
            fx_cand = build_adjust_filters(config or {}, allow_blur_fx=False)
            if fx_cand and fx_cand != "eq=contrast=1.000:brightness=0.000:saturation=1.000":
                color_fx = fx_cand
                look_name = (config or {}).get("color_look", "Tùy chỉnh")
                _log(f"🎨 [NHÂN VIÊN 1] Áp dụng bảng màu CapCut Studio ({look_name}) cho toàn bộ cảnh quay.")
        except Exception as e:
            logger.warning(f"Không thể khởi tạo bộ lọc màu CapCut: {e}")

        enc_name, enc_opts = get_best_video_encoder()
        scene_files = []
        scene_durations = []

        _log(f"🎥 [ĐẠO DIỄN HÌNH ẢNH] Bắt đầu phân tích khuôn mặt (YuNet DNN) & điều phối Camera (Zoom In / Zoom Out / Speaker Cut) cho {len(scenes)} phân cảnh...")

        # 1. Render từng cảnh
        try:
            for sc_idx, sc in enumerate(scenes):
                sc_name = sc.get("name", f"Scene {sc_idx + 1}")
                raw_shots = sc.get("shots", [])
                if not raw_shots:
                    continue

                # Đạo diễn hình ảnh lập phương án góc máy & tiêu cự (Zoom In / Zoom Out / X-Y Framing) cho từng shot
                planned_shots = cls.plan_scene_camera_shots(
                    cap=cap,
                    detectors=detectors,
                    shots=raw_shots,
                    scale_w=scale_w,
                    target_fg_h=target_fg_h,
                )

                seg_files = []
                cam_summary = []
                for sh_idx, sh in enumerate(planned_shots):
                    start = float(sh["start"])
                    end = float(sh["end"])
                    dur = max(0.5, end - start)

                    z_w = int(sh["z_w"])
                    z_h = int(sh["z_h"])
                    x_off = int(sh["x_off"])
                    y_off = int(sh["y_off"])
                    z_fac = float(sh["zoom_factor"])
                    u_foc = float(sh["u_focus"])

                    cam_summary.append(f"Shot {sh_idx+1}: Zoom {z_fac:.2f}x @ u={u_foc:.2f}")

                    fg_filter = f"scale={z_w}:{z_h},crop=1080:{target_fg_h}:{x_off}:{y_off}"
                    if color_fx:
                        fg_filter = f"{fg_filter},{color_fx}"

                    vf = (
                        f"[0:v]split=2[bg_in][fg_in];"
                        f"[bg_in]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:6[bg];"
                        f"[fg_in]{fg_filter}[fg];"
                        f"[bg][fg]overlay=0:{y_top_boundary},setpts=PTS-STARTPTS[vout]"
                    )

                    fade_d = min(0.04, dur * 0.05)
                    af = f"asetpts=PTS-STARTPTS,afade=t=in:ss=0:d={fade_d:.3f},afade=t=out:st={dur - fade_d:.3f}:d={fade_d:.3f}"

                    seg_ts = os.path.join(work_dir, f"sc_{sc_idx:02d}_shot_{sh_idx:02d}.ts")
                    cmd = [
                        "ffmpeg", "-y",
                        "-ss", f"{start:.3f}",
                        "-i", source_video,
                        "-t", f"{dur:.3f}",
                        "-filter_complex", vf,
                        "-af", af,
                        "-map", "[vout]", "-map", "0:a",
                        "-c:v", enc_name, *enc_opts,
                        "-c:a", "aac", "-b:a", "192k",
                        "-r", "30",
                        "-f", "mpegts",
                        seg_ts
                    ]
                    _ff_run(cmd, log_fn=None, timeout=60, label=f"sh_{sc_idx}_{sh_idx}")
                    seg_files.append(seg_ts)

                if not seg_files:
                    continue

                # Ghép các shots của cảnh này thành video cảnh
                concat_list = os.path.join(work_dir, f"sc_{sc_idx:02d}_concat.txt")
                with open(concat_list, "w", encoding="utf-8") as f:
                    for sf in seg_files:
                        f.write(f"file '{sf}'\n")

                sc_video = os.path.join(work_dir, f"scene_{sc_idx + 1}.mp4")
                concat_cmd = [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", concat_list,
                    "-c", "copy",
                    sc_video
                ]
                _ff_run(concat_cmd, log_fn=None, timeout=120, label=f"sc_{sc_idx}")
                sc_dur = probe_duration_sec(sc_video)
                scene_files.append(sc_video)
                scene_durations.append(sc_dur)
                _log(f"   ✓ Cảnh {sc_idx + 1}/{len(scenes)} ({sc_dur:.1f}s): {' | '.join(cam_summary)}")
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass

        if not scene_files:
            raise RuntimeError("Không có phân cảnh nào được render thành công!")

        if len(scene_files) == 1:
            shutil.copyfile(scene_files[0], output_merged_path)
            return output_merged_path, []

        # 2. Ghép các cảnh bằng Kỹ Xảo Motion Blur (xfade=hblur) & Audio acrossfade
        _log(f"🌪️ [NHÂN VIÊN 1] Áp dụng Kỹ Xảo Motion Blur Whip Transition (xfade=hblur {transition_duration}s) liên hoàn {len(scene_files)} cảnh...")
        inputs_cmd = []
        for sf in scene_files:
            inputs_cmd.extend(["-i", sf])

        filter_v_chains = []
        filter_a_chains = []
        transition_timestamps = []

        cur_offset = scene_durations[0] - transition_duration
        transition_timestamps.append(cur_offset)
        filter_v_chains.append(f"[0:v][1:v]xfade=transition=hblur:duration={transition_duration}:offset={cur_offset:.2f}[v01]")
        filter_a_chains.append(f"[0:a][1:a]acrossfade=d={transition_duration}:c1=tri:c2=tri[a01]")

        last_v = "v01"
        last_a = "a01"

        for i in range(2, len(scene_files)):
            cur_offset = cur_offset + scene_durations[i - 1] - transition_duration
            transition_timestamps.append(cur_offset)
            next_v = f"v0{i}"
            next_a = f"a0{i}"
            filter_v_chains.append(f"[{last_v}][{i}:v]xfade=transition=hblur:duration={transition_duration}:offset={cur_offset:.2f}[{next_v}]")
            filter_a_chains.append(f"[{last_a}][{i}:a]acrossfade=d={transition_duration}:c1=tri:c2=tri[{next_a}]")
            last_v = next_v
            last_a = next_a

        full_chain = ";".join(filter_v_chains + filter_a_chains)
        cmd_merge = [
            "ffmpeg", "-y",
            *inputs_cmd,
            "-filter_complex", full_chain,
            "-map", f"[{last_v}]", "-map", f"[{last_a}]",
            "-c:v", enc_name, *enc_opts,
            "-c:a", "aac", "-b:a", "192k",
            output_merged_path
        ]
        _ff_run(cmd_merge, log_fn=log_fn, timeout=600, label="merge_xfade")
        _log(f"✅ [NHÂN VIÊN 1] Đã hoàn thành ghép nối {len(scene_files)} cảnh: {output_merged_path} ({probe_duration_sec(output_merged_path):.1f}s)")
        return output_merged_path, transition_timestamps
