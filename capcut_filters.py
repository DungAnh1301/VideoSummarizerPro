"""
Preset lọc màu mô phỏng look đang thịnh (CapCut-style), không phải LUT gốc CapCut.
Cường độ 0–100. Có thể chồng nhiều look (stack).
"""
from copy import deepcopy

ADJUST_KEYS = (
    "temperature", "tint", "saturation", "exposure", "contrast",
    "highlights", "shadows", "whites", "blacks", "brilliance",
    "sharpen", "clarity", "grain",
)
# blur + vignette chỉ từ thanh GUI — preset không được cộng vào (tránh mờ viền).

LOOKS = {
    "Không lọc": {},
    "8K": {
        "sharpen": 28, "clarity": 22, "contrast": 6, "whites": 4,
    },
    "HDR": {
        "saturation": 12, "contrast": 10, "exposure": 3, "highlights": 16,
        "shadows": -8, "whites": 8, "blacks": 4, "brilliance": 14,
    },
    "Cinematic": {
        "temperature": -10, "saturation": -12, "contrast": 22, "exposure": -4,
        "highlights": -16, "shadows": -14, "blacks": 8, "clarity": 18, "grain": 14,
    },
    "Teal Orange": {
        "temperature": 8, "tint": -6, "saturation": 10, "contrast": 18,
        "highlights": -8, "shadows": -10, "clarity": 12, "hue": -8,
    },
    "Vintage Film": {
        "temperature": 16, "saturation": -18, "contrast": -8, "exposure": 4,
        "highlights": 10, "shadows": 8, "whites": -12, "grain": 28, "sepia": 0.28,
    },
    "Fade Soft": {
        "saturation": -20, "contrast": -16, "exposure": 6, "highlights": 14,
        "shadows": 16, "blacks": -10, "whites": -8,
    },
    "Moody Dark": {
        "temperature": -12, "saturation": -10, "contrast": 16, "exposure": -10,
        "highlights": -20, "shadows": -22, "blacks": 16, "clarity": 10, "grain": 10,
    },
    "Warm Golden": {
        "temperature": 28, "tint": 6, "saturation": 8, "exposure": 4,
        "highlights": 8, "shadows": 4, "brilliance": 8,
    },
    "Cold Blue": {
        "temperature": -28, "tint": -4, "saturation": -6, "contrast": 10,
        "highlights": -8, "shadows": -8,
    },
    "Black & White": {
        "contrast": 18, "highlights": -6, "shadows": -8, "clarity": 14,
        "grain": 16, "bw": 1.0,
    },
    "Dreamy Soft": {
        "saturation": 6, "exposure": 8, "contrast": -10, "highlights": 12,
        "brilliance": 14,
    },
    "Vibrant Punch": {
        "saturation": 28, "contrast": 16, "exposure": 4, "clarity": 16,
        "sharpen": 10, "highlights": 6,
    },
    "Night City": {
        "temperature": -18, "saturation": 8, "contrast": 20, "exposure": -8,
        "highlights": 10, "shadows": -18, "clarity": 14,
    },
    "Sepia Classic": {
        "contrast": -4, "exposure": 4, "grain": 18, "sepia": 0.55,
    },
    "Bleach Bypass": {
        "saturation": -22, "contrast": 28, "highlights": 12, "shadows": -16,
        "clarity": 22, "sharpen": 12, "grain": 8,
    },
    "Retro 80s": {
        "temperature": 10, "saturation": 22, "contrast": 12, "tint": 8,
        "highlights": 8, "grain": 12, "hue": 12,
    },
    "Food Fresh": {
        "temperature": 10, "saturation": 20, "contrast": 10, "exposure": 4,
        "clarity": 18, "sharpen": 8, "highlights": 6,
    },
}

LOOK_NAMES = list(LOOKS.keys())


def look_names():
    return list(LOOK_NAMES)


def _clamp_intensity(raw) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        val = 70.0
    return max(0.0, min(100.0, val))


def normalize_stack(raw) -> list:
    stack = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name == "Không lọc" or name not in LOOKS:
                continue
            stack.append({"name": name, "intensity": _clamp_intensity(item.get("intensity", 70))})
    return stack


def apply_look(options: dict) -> dict:
    opts = deepcopy(options or {})
    if opts.get("_look_applied"):
        return opts
    stack = normalize_stack(opts.get("color_look_stack"))
    if not stack:
        name = str(opts.get("color_look") or "Không lọc").strip()
        if name not in LOOKS:
            name = "Không lọc"
        intensity = _clamp_intensity(opts.get("color_look_intensity", 70))
        if name != "Không lọc":
            stack = [{"name": name, "intensity": intensity}]

    extra = {"hue": 0.0, "bw": 0.0, "sepia": 0.0, "name": "Không lọc", "intensity": 0}
    names = []
    for item in stack:
        name = item["name"]
        factor = item["intensity"] / 100.0
        preset = LOOKS.get(name) or {}
        if not preset or factor <= 0.001:
            continue
        names.append(f"{name} {item['intensity']:.0f}%")
        for key in ADJUST_KEYS:
            if key in preset:
                opts[key] = float(opts.get(key, 0) or 0) + float(preset[key]) * factor
        extra["hue"] += float(preset.get("hue", 0) or 0) * factor
        extra["bw"] = min(1.0, extra["bw"] + float(preset.get("bw", 0) or 0) * factor)
        extra["sepia"] = min(1.0, extra["sepia"] + float(preset.get("sepia", 0) or 0) * factor)

    extra["name"] = " + ".join(names) if names else "Không lọc"
    extra["intensity"] = 100.0 if names else 0.0
    opts["_look_extra"] = extra
    opts["color_look_stack"] = stack
    opts["_look_applied"] = True
    return opts


def extra_ffmpeg_tail(options: dict) -> str:
    extra = (options or {}).get("_look_extra") or {}
    parts = []
    hue = float(extra.get("hue", 0) or 0)
    if abs(hue) >= 0.5:
        parts.append(f"hue=h={hue:.2f}")
    bw = float(extra.get("bw", 0) or 0)
    if bw >= 0.05:
        c = max(0.0, min(1.0, 1.0 - bw))
        g = bw / 3.0
        parts.append(
            f"colorchannelmixer=rr={c+g:.3f}:rg={g:.3f}:rb={g:.3f}:"
            f"gr={g:.3f}:gg={c+g:.3f}:gb={g:.3f}:"
            f"br={g:.3f}:bg={g:.3f}:bb={c+g:.3f}"
        )
    sepia = float(extra.get("sepia", 0) or 0)
    if sepia >= 0.05:
        s = max(0.0, min(1.0, sepia))
        keep = 1.0 - s
        parts.append(
            "colorchannelmixer="
            f"rr={keep+0.393*s:.3f}:rg={0.769*s:.3f}:rb={0.189*s:.3f}:"
            f"gr={0.349*s:.3f}:gg={keep+0.686*s:.3f}:gb={0.168*s:.3f}:"
            f"br={0.272*s:.3f}:bg={0.534*s:.3f}:bb={keep+0.131*s:.3f}"
        )
    if not parts:
        return ""
    return "," + ",".join(parts)


def _f(options, key, default=0.0) -> float:
    try:
        return float((options or {}).get(key, default) or 0)
    except (TypeError, ValueError):
        return float(default)


def build_adjust_filters(options: dict, allow_blur_fx: bool = True) -> str:
    """
    Bộ lọc màu sắc CapCut chuẩn 15 thông số gốc nguyên bản.
    """
    opts = apply_look(options or {})
    temp_val = _f(opts, "temperature")
    tint_val = _f(opts, "tint")
    sat_val = _f(opts, "saturation")
    exposure_val = _f(opts, "exposure")
    contrast_val = _f(opts, "contrast")
    highlights_val = _f(opts, "highlights")
    shadows_val = _f(opts, "shadows")
    whites_val = _f(opts, "whites")
    blacks_val = _f(opts, "blacks")
    brilliance_val = _f(opts, "brilliance")
    sharpen_val = _f(opts, "sharpen")
    clarity_val = _f(opts, "clarity")
    grain_val = _f(opts, "grain")
    blur_val = _f(opts, "blur")
    vignette_val = _f(opts, "vignette")

    brightness = exposure_val / 100.0
    contrast = max(0.0, 1.0 + (contrast_val / 40.0))
    saturation = max(0.0, 1.0 + (sat_val / 25.0))

    r_mod = temp_val / 80.0
    b_mod = -temp_val / 80.0
    g_mod = tint_val / 80.0

    parts = [f"eq=contrast={contrast:.3f}:brightness={brightness:.3f}:saturation={saturation:.3f}"]

    if abs(temp_val) >= 0.05 or abs(tint_val) >= 0.05:
        parts.append(f"colorbalance=rh={r_mod:.3f}:bh={b_mod:.3f}:gh={g_mod:.3f}")

    if abs(highlights_val) >= 0.05 or abs(shadows_val) >= 0.05:
        h_shift = highlights_val / 200.0
        sh_shift = shadows_val / 200.0
        p1_x, p1_y = 0.25, max(0.01, min(0.49, 0.25 + sh_shift))
        p2_x, p2_y = 0.75, max(0.51, min(0.99, 0.75 + h_shift))
        parts.append(f"curves=all='0/0 {p1_x:.2f}/{p1_y:.2f} {p2_x:.2f}/{p2_y:.2f} 1/1'")

    if abs(whites_val) >= 0.05 or abs(blacks_val) >= 0.05:
        w_mod = max(0.55, min(0.98, 1.0 - (whites_val / 300.0)))
        b_mod = max(0.02, min(0.45, 0.0 + (blacks_val / 300.0)))
        parts.append(f"curves=all='{b_mod:.2f}/0.0 0.5/0.5 {w_mod:.2f}/1.0'")

    if abs(brilliance_val) >= 0.05:
        gamma_val = max(0.1, 1.0 + (brilliance_val / 100.0))
        parts.append(f"eq=gamma={gamma_val:.3f}")

    sharpen_amount = max(0.0, sharpen_val) / 100.0 * 1.5
    clarity_amount = max(0.0, clarity_val) / 100.0 * 1.2
    detail_amount = min(1.5, sharpen_amount + clarity_amount)
    if detail_amount > 0.01:
        detail_kernel = 7 if sharpen_amount > 0 and clarity_amount > 0 else (5 if sharpen_amount > 0 else 9)
        parts.append(
            f"unsharp={detail_kernel}:{detail_kernel}:{detail_amount:.4f}:"
            f"{detail_kernel}:{detail_kernel}:0.0"
        )

    if allow_blur_fx and blur_val > 0.1:
        b_radius = max(1, int(blur_val / 4))
        parts.append(f"gblur=sigma={b_radius}")

    if grain_val > 0.1:
        noise_v = int((grain_val / 100.0) * 40)
        if noise_v > 0:
            parts.append(f"noise=alls={noise_v}:allf=t+u")

    if allow_blur_fx and vignette_val > 0.1:
        v_angle = 0.4 + ((100 - vignette_val) / 200.0)
        parts.append(f"vignette=PI/{v_angle:.3f}")

    chain = ",".join(parts)
    tail = extra_ffmpeg_tail(opts)
    if tail:
        chain += tail
    return chain or "null"