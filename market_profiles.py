"""Cấu hình thị trường đầu ra và quy tắc chuẩn hóa locale/TTS."""

MARKET_PROFILES = {
    "US": {"label": "🇺🇸 Mỹ — English (US)", "name": "United States", "language": "English", "language_code": "en", "locale": "en-US", "word_rates": (200, 280, 240)},
    "GB": {"label": "🇬🇧 Anh — English (UK)", "name": "United Kingdom", "language": "British English", "language_code": "en", "locale": "en-GB", "word_rates": (200, 280, 240)},
    "DE": {"label": "🇩🇪 Đức — Deutsch", "name": "Germany", "language": "German", "language_code": "de", "locale": "de-DE", "word_rates": (190, 250, 220)},
    "FR": {"label": "🇫🇷 Pháp — Français", "name": "France", "language": "French", "language_code": "fr", "locale": "fr-FR", "word_rates": (190, 250, 220)},
    "MX": {"label": "🇲🇽 Mexico — Español", "name": "Mexico", "language": "Mexican Spanish", "language_code": "es", "locale": "es-MX", "word_rates": (200, 270, 235)},
    "BR": {"label": "🇧🇷 Brazil — Português", "name": "Brazil", "language": "Brazilian Portuguese", "language_code": "pt", "locale": "pt-BR", "word_rates": (190, 260, 225)},
    "JP": {"label": "🇯🇵 Nhật Bản — 日本語", "name": "Japan", "language": "Japanese", "language_code": "ja", "locale": "ja-JP", "word_rates": (180, 260, 220)},
    "KR": {"label": "🇰🇷 Hàn Quốc — 한국어", "name": "South Korea", "language": "Korean", "language_code": "ko", "locale": "ko-KR", "word_rates": (180, 260, 220)},
}

DEFAULT_MARKET = "US"

CAPCUT_LOCALE_ALIASES = {
    "en": "en-US", "us": "en-US", "de": "de-DE", "fr": "fr-FR",
    "es": "es-ES", "pt": "pt-BR", "br": "pt-BR", "ja": "ja-JP",
    "jp": "ja-JP", "ko": "ko-KR", "kr": "ko-KR",
}

PREVIEW_TEXT = {
    "en": "Hello, this is a voice quality preview for your video.",
    "de": "Hallo, dies ist eine Hörprobe für dein Video.",
    "fr": "Bonjour, ceci est un aperçu de la voix pour votre vidéo.",
    "es": "Hola, esta es una prueba de voz para tu video.",
    "pt": "Olá, esta é uma prévia da voz para o seu vídeo.",
    "ja": "こんにちは、これは動画用音声のプレビューです。",
    "ko": "안녕하세요. 영상에 사용할 음성 미리 듣기입니다.",
    "vi": "Xin chào, đây là bản nghe thử giọng đọc cho video của bạn.",
}


def get_market_profile(market_code=None):
    return dict(MARKET_PROFILES.get(str(market_code or "").upper(), MARKET_PROFILES[DEFAULT_MARKET]))


def market_code_from_label(label):
    text = str(label or "")
    for code, profile in MARKET_PROFILES.items():
        if text == profile["label"] or text.upper() == code:
            return code
    return DEFAULT_MARKET


def normalize_capcut_locale(lang=None, lan=None):
    primary = str(lang or "").strip()
    if primary and primary.lower() not in {"none", "unknown"}:
        return primary
    return CAPCUT_LOCALE_ALIASES.get(str(lan or "").strip().lower(), primary or "en-US")


def language_code_from_locale(locale):
    return str(locale or "en-US").split("-", 1)[0].lower()

