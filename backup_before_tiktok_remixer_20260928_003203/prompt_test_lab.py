import json
import re
import threading
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk

import requests
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi

from config_manager import load_config


DEEPSEEK_URL = "https://api.deepseek.com/chat/completions"


OLD_WRITER_SYSTEM_PROMPT = """You are a dramatic TikTok US narrator and scriptwriter specializing in high-engagement audio commentary for chaotic nightlife and street conflict videos. Your task is to rewrite the provided script/story content into a long-form, deeply detailed, punchy, and highly engaging TikTok/Reels video script.

Use the following parameters for the rewrite:

- [ORIGINAL SCRIPT]
- [CONTENT CATEGORY]: Street Fights / Nightlife Confrontations
- [MAIN SUBJECTS]
- [CENTRAL CONFLICT]
- [KEY DEVELOPMENTS]
- [TURNING POINT / CLIMAX]
- [ENDING / RESULT]
- [TARGET LENGTH]: 1.4 to 1.8 Minutes (Must be rich in detail, expanded pacing, and substantial narrative depth to ensure a solid 1+ minute read time even at increased speeds)
- [COMMENTARY STYLE]: Fast-paced, street-smart, dramatic, and cynical-humorous American narrator.

STRICT OUTPUT FORMAT RULES:

- Do NOT include any timestamp labels (such as 0–3 seconds, 3–10 seconds, etc.).
- Do NOT write the script with section markers, line breaks, or bullet points.
- Write the final script as ONE continuous, flowing paragraph of text.
- The entire output must contain ONLY the voiceover-ready narration text with no introductory or concluding notes.
- The full script should be around 200 to 280 words total, using natural American English, starting with a strong curiosity-driven hook, expanding on the tension and drama through the middle, and ending with a compelling question designed to drive comments.
"""


WRITER_SYSTEM_PROMPT = """You are an elite viral short-video narrator and retention-focused
story adapter for a US audience.

Read the complete source material and identify its real subject, atmosphere, pacing,
conflict, escalation, turning point, climax, and outcome. Write a highly engaging
voice-over that feels naturally adapted to this specific video instead of following
a generic template.

The optional storytelling hint is only a soft creative suggestion. Infer the video's real
energy, vocabulary, attitude, humor, tension, and narrative rhythm from the source first.
Then transform it into the most gripping version of that same kind of story.

Viewer engagement is the highest priority. You are explicitly allowed to dramatize,
compress, rearrange, infer, and invent entertaining connective details, reactions,
descriptions, tension, jokes, or narrator commentary when they make the story more vivid.
The result does not need to be a documentary transcript. Keep the recognizable central
premise, main subjects, major conflict, and general outcome, but optimize aggressively for
curiosity, retention, escalation, payoff, and comments.

Requirements:
- Start with a strong curiosity-driven hook.
- Keep cause and effect clear.
- Increase intensity as the source events escalate.
- Give the genuine turning point and climax enough weight.
- End naturally and, when suitable, invite viewer reaction.
- Do not copy long passages verbatim.
- Do not include timestamps, headings, stage directions, editing notes, or explanations
  inside the narration.
- Create a short, readable overlay title. Never repeat that title as the opening line of
  the narration.
- Write enough natural narration to exceed the requested minimum duration. There is no
  maximum duration: a compelling script longer than two minutes is acceptable.
- Return valid JSON only, with no Markdown.

Required JSON schema:
{
  "title": "short overlay title",
  "source_style": "brief description of the style inferred from the source",
  "adapted_style": "brief description of the final narration style",
  "script": "complete voice-over narration"
}
"""


EVALUATOR_SYSTEM_PROMPT = """You are a senior viral-content director evaluating a generated
video recap against its source transcript. Viewer engagement is the dominant criterion.
Judge whether the recap creates a powerful hook, sustained curiosity, escalating tension,
a satisfying payoff, vivid language, and a strong reason to keep watching or comment.

Creative invention, dramatization, inferred reactions, compressed chronology, and colorful
details are allowed and should not be penalized by themselves. Content fidelity only needs
to preserve enough of the recognizable premise, main subjects, conflict, and broad outcome.
A content fidelity score of 60 to 70 is acceptable when the adaptation is substantially
more entertaining. Style should feel natural for the source instead of mechanically applying
a generic preset.

Return valid JSON only, with no Markdown, using this schema:
{
  "content_fidelity_score": 0,
  "style_match_score": 0,
  "engagement_score": 0,
  "overall_score": 0,
  "source_style": "what style the source naturally has",
  "summary_style": "what style the generated recap uses",
  "what_works": ["short observation"],
  "what_differs": ["short observation"],
  "important_distortions": ["only material changes, or empty array"],
  "verdict": "DAT, CAN_CHINH, or KHONG_DAT"
}

All scores are integers from 0 to 100. Calculate overall_score with these priorities:
- engagement: 50 percent;
- style match: 35 percent;
- content fidelity: 15 percent.

Verdict rules:
- DAT: engagement >= 90, style match >= 80, and content fidelity >= 60;
- CAN_CHINH: close to those thresholds;
- KHONG_DAT: weak engagement or the source is no longer recognizable.
"""


def extract_video_id(url: str) -> str:
    patterns = (
        r"(?:youtube\.com/watch\?[^\s]*v=)([\w-]{11})",
        r"(?:youtu\.be/)([\w-]{11})",
        r"(?:youtube\.com/shorts/)([\w-]{11})",
        r"(?:youtube\.com/embed/)([\w-]{11})",
    )
    for pattern in patterns:
        match = re.search(pattern, url or "", re.IGNORECASE)
        if match:
            return match.group(1)
    raise ValueError("Không nhận diện được ID video YouTube từ link đã nhập.")


def fetch_youtube_source(url: str) -> dict:
    video_id = extract_video_id(url)
    metadata = {}
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False) or {}
            metadata = {
                "title": str(info.get("title") or "").strip(),
                "description": str(info.get("description") or "").strip(),
                "duration": info.get("duration"),
                "uploader": str(info.get("uploader") or "").strip(),
            }
    except Exception:
        metadata = {"title": "", "description": "", "duration": None, "uploader": ""}

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["en", "vi"])
    except Exception:
        transcript_list = api.list(video_id)
        selected = None
        for item in transcript_list:
            selected = item
            if getattr(item, "is_generated", False) is False:
                break
        if selected is None:
            raise RuntimeError("Video không có subtitle có thể đọc được.")
        fetched = selected.fetch()

    snippets = getattr(fetched, "snippets", fetched)
    lines = []
    language = getattr(fetched, "language_code", "") or ""
    for snippet in snippets:
        text = getattr(snippet, "text", None)
        if text is None and isinstance(snippet, dict):
            text = snippet.get("text")
        clean = re.sub(r"\s+", " ", str(text or "")).strip()
        if clean:
            lines.append(clean)
    transcript = " ".join(lines).strip()
    if len(transcript) < 30:
        raise RuntimeError("Subtitle lấy được quá ngắn để thử prompt.")
    metadata.update({"video_id": video_id, "transcript": transcript, "language": language})
    return metadata


def call_deepseek(
    api_key: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.7,
    max_tokens: int = None,
) -> dict:
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "response_format": {"type": "json_object"},
        "temperature": temperature,
        "stream": False,
    }
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)
    response = requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"DeepSeek API lỗi [{response.status_code}]: {response.text[:500]}")
    raw = response.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"DeepSeek trả JSON không hợp lệ: {raw[:500]}") from exc


def call_deepseek_text(api_key: str, system_prompt: str, user_prompt: str) -> str:
    payload = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.65,
        "stream": False,
    }
    response = requests.post(
        DEEPSEEK_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code != 200:
        raise RuntimeError(f"DeepSeek API lỗi [{response.status_code}]: {response.text[:500]}")
    return str(response.json()["choices"][0]["message"]["content"] or "").strip()


def build_writer_user_prompt(source: dict, hint: str, duration_sec: int) -> str:
    hint_text = hint.strip() or "No preference. Infer the best style from the source."
    description = source.get("description") or "Not available"
    # Description can be huge and promotional; a short prefix is enough context.
    description = description[:3000]
    return f"""OPTIONAL STORYTELLING HINT:
{hint_text}

Treat this as a soft suggestion. First infer the vocabulary, emotion, pacing, and
narrative shape that best fit the actual source.

MINIMUM NARRATION DURATION:
{duration_sec} seconds

This is a hard minimum, not a target window and not a maximum. The finished narration must
not be shorter than this value. A longer script, including one over two minutes, is fully
acceptable when it remains engaging and avoids empty repetition.

SOURCE VIDEO TITLE:
{source.get('title') or 'Not available'}

SOURCE VIDEO DESCRIPTION:
{description}

TRANSCRIPT LANGUAGE:
{source.get('language') or 'Unknown'}

SOURCE TRANSCRIPT:
<source_transcript>
{source['transcript']}
</source_transcript>

Write the most compelling adaptation now. Prioritize viewer retention over strict factual
precision. Do not mention word counts or duration in the output."""


def build_evaluation_prompt(source: dict, result: dict, hint: str) -> str:
    return f"""OPTIONAL USER HINT:
{hint.strip() or 'None'}

SOURCE VIDEO TITLE:
{source.get('title') or 'Not available'}

SOURCE TRANSCRIPT:
<source_transcript>
{source['transcript']}
</source_transcript>

GENERATED TITLE:
{result.get('title', '')}

GENERATED SCRIPT:
<generated_script>
{result.get('script', '')}
</generated_script>

Compare the generated recap with the source. Focus especially on whether its vocabulary,
pacing, tension, humor or seriousness evolve appropriately with the actual story, and
whether the opening and progression are attractive to viewers."""


def build_old_writer_prompt(source: dict) -> str:
    return f"""[ORIGINAL SCRIPT]
{source['transcript']}

[SOURCE VIDEO TITLE]
{source.get('title') or 'Not available'}

Rewrite this source according to all system instructions. Output only the final continuous
voice-over paragraph."""


def build_head_to_head_prompt(source: dict, new_result: dict, new_eval: dict,
                              old_script: str, old_eval: dict) -> str:
    return f"""Compare two recap approaches generated from the same source.

SOURCE TITLE:
{source.get('title') or 'Not available'}

NEW DYNAMIC-PROMPT SCRIPT:
{new_result.get('script', '')}

NEW SCRIPT SCORES:
{json.dumps(new_eval, ensure_ascii=False)}

OLD FIXED-PROMPT SCRIPT:
{old_script}

OLD SCRIPT SCORES:
{json.dumps(old_eval, ensure_ascii=False)}

Return valid JSON only:
{{
  "winner": "PROMPT_MOI, PROMPT_CU, or HOA",
  "reason": "concise reason",
  "new_prompt_advantages": ["observation"],
  "old_prompt_advantages": ["observation"],
  "recommended_use": "final recommendation"
}}"""


def estimate_script_seconds(script: str, words_per_minute: float = 165.0):
    words = len((script or "").split())
    return words, (words / words_per_minute * 60.0 if words else 0.0)


def revise_length_once(api_key: str, result: dict, minimum_sec: int) -> tuple[dict, bool]:
    script = str(result.get("script") or "").strip()
    words, estimated = estimate_script_seconds(script)
    if estimated >= minimum_sec:
        return result, False

    min_words = max(1, round(minimum_sec * 165 / 60))
    safety_words = max(min_words + 20, round(min_words * 1.15))
    revision_prompt = f"""Rewrite only the narration below exactly once.

ACTION: Expand the narration because it is below the hard minimum duration.
MINIMUM: Write at least {safety_words} words so it safely exceeds {minimum_sec} seconds.
There is no maximum. Word count is an internal writing constraint; never mention it in
the narration.

Preserve the core events, outcome, strongest hook, natural source style, and viewer appeal.
Remove weaker details before removing the conflict, turning point, or outcome. Do not add
headings or explanations. Return valid JSON containing only the script field:
{{"script": "revised narration"}}

CURRENT NARRATION:
{script}"""
    revised = call_deepseek(
        api_key,
        "You are a precise senior script editor. The requested length is mandatory. Return valid JSON only.",
        revision_prompt,
        temperature=0.2,
        max_tokens=round(safety_words * 1.8 + 80),
    )
    if not str(revised.get("script") or "").strip():
        return result, False
    revised["title"] = result.get("title", "")
    revised["source_style"] = result.get("source_style", "")
    revised["adapted_style"] = result.get("adapted_style", "")
    return revised, True


class PromptTestLab(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Prompt Test Lab — So sánh Prompt mới và Prompt cũ")
        self.geometry("1120x780")
        self.minsize(900, 650)
        self.config_data = load_config()
        self.source = None
        self.result = None
        self._build_ui()

    def _build_ui(self):
        top = ttk.LabelFrame(self, text="Dữ liệu thử nghiệm", padding=10)
        top.pack(fill=tk.X, padx=10, pady=8)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Link YouTube:").grid(row=0, column=0, sticky=tk.W, padx=(0, 8))
        self.url_var = tk.StringVar()
        ttk.Entry(top, textvariable=self.url_var).grid(row=0, column=1, columnspan=4, sticky=tk.EW)

        ttk.Label(top, text="Gợi ý màu sắc:").grid(row=1, column=0, sticky=tk.W, pady=(8, 0))
        self.hint_var = tk.StringVar(value="Crime")
        ttk.Entry(top, textvariable=self.hint_var, width=35).grid(row=1, column=1, sticky=tk.W, pady=(8, 0))
        help_label = ttk.Label(top, text="ⓘ", cursor="hand2", foreground="#1b67c9")
        help_label.grid(row=1, column=2, sticky=tk.W, padx=6, pady=(8, 0))
        self._tooltip(help_label, "Ví dụ: Crime căng thẳng, Điều tra bí ẩn, Hài châm biếm,\nKịch tính đường phố, Cảm xúc. Có thể để trống để AI tự chọn.")

        ttk.Label(top, text="Tối thiểu:").grid(row=1, column=3, sticky=tk.E, pady=(8, 0))
        self.duration_var = tk.IntVar(value=90)
        ttk.Spinbox(top, from_=30, to=300, textvariable=self.duration_var, width=7).grid(row=1, column=4, sticky=tk.W, padx=(6, 0), pady=(8, 0))
        ttk.Label(top, text="giây").grid(row=1, column=5, sticky=tk.W, padx=(4, 0), pady=(8, 0))

        actions = ttk.Frame(top)
        actions.grid(row=2, column=0, columnspan=6, sticky=tk.W, pady=(10, 0))
        self.run_btn = ttk.Button(actions, text="▶ Chạy so sánh A/B", command=self.start_test)
        self.run_btn.pack(side=tk.LEFT)
        self.status_var = tk.StringVar(value="Sẵn sàng")
        ttk.Label(actions, textvariable=self.status_var).pack(side=tk.LEFT, padx=12)

        notebook = ttk.Notebook(self)
        notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        self.source_text = self._tab(notebook, "Nguồn YouTube")
        self.script_text = self._tab(notebook, "Prompt mới")
        self.old_script_text = self._tab(notebook, "Prompt cũ")
        self.compare_text = self._tab(notebook, "Kết quả A/B")
        self.raw_text = self._tab(notebook, "JSON đầy đủ")

    @staticmethod
    def _tab(notebook, title):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = scrolledtext.ScrolledText(frame, wrap=tk.WORD, font=("Segoe UI", 10))
        text.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        return text

    def _tooltip(self, widget, text):
        popup = {"window": None}
        def show(_event=None):
            if popup["window"]:
                return
            win = tk.Toplevel(self)
            win.wm_overrideredirect(True)
            win.geometry(f"+{widget.winfo_rootx() + 18}+{widget.winfo_rooty() + 18}")
            ttk.Label(win, text=text, relief=tk.SOLID, padding=7).pack()
            popup["window"] = win
        def hide(_event=None):
            if popup["window"]:
                popup["window"].destroy()
                popup["window"] = None
        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def _set_text(self, widget, value):
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)

    def start_test(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showwarning("Thiếu link", "Hãy nhập link YouTube cần thử.")
            return
        try:
            duration = int(self.duration_var.get())
        except Exception:
            duration = 0
        if not 30 <= duration <= 300:
            messagebox.showwarning("Thời lượng", "Thời lượng phải nằm trong khoảng 30–300 giây.")
            return
        api_key = str(self.config_data.get("api_key") or "").strip()
        if not api_key:
            messagebox.showerror("Thiếu API Key", "Chưa có API Key trong config.json của tool chính.")
            return
        self.run_btn.configure(state=tk.DISABLED)
        self.status_var.set("Đang tải metadata và subtitle…")
        threading.Thread(target=self._run, args=(url, self.hint_var.get(), duration, api_key), daemon=True).start()

    def _run(self, url, hint, duration, api_key):
        try:
            source = fetch_youtube_source(url)
            self.source = source
            source_view = (
                f"TITLE: {source.get('title') or '(không lấy được)'}\n"
                f"CHANNEL: {source.get('uploader') or '(không lấy được)'}\n"
                f"VIDEO DURATION: {source.get('duration') or '?'} giây\n"
                f"SUB LANGUAGE: {source.get('language') or '?'}\n"
                f"TRANSCRIPT: {len(source['transcript'])} ký tự\n\n"
                f"{source['transcript']}"
            )
            self.after(0, self._set_text, self.source_text, source_view)
            self.after(0, self.status_var.set, "Đã có sub — DeepSeek đang viết kịch bản…")

            writer_user = build_writer_user_prompt(source, hint, duration)
            result = call_deepseek(api_key, WRITER_SYSTEM_PROMPT, writer_user, temperature=0.65)
            original_words, original_seconds = estimate_script_seconds(str(result.get("script") or ""))
            result, length_revised = revise_length_once(api_key, result, duration)
            script = str(result.get("script") or "").strip()
            if len(script) < 40:
                raise RuntimeError("DeepSeek trả về script rỗng hoặc quá ngắn.")
            self.result = result
            words, estimated_seconds = estimate_script_seconds(script)
            if estimated_seconds < duration:
                duration_status = "CHƯA ĐẠT — ngắn hơn mốc tối thiểu"
            else:
                duration_status = "ĐẠT — không giới hạn thời lượng tối đa"
            script_view = (
                f"TITLE: {result.get('title', '')}\n"
                f"PHONG CÁCH NGUỒN: {result.get('source_style', '')}\n"
                f"PHONG CÁCH KỊCH BẢN: {result.get('adapted_style', '')}\n"
                f"SỐ TỪ: {words}\n"
                f"THỜI LƯỢNG ƯỚC TÍNH: {estimated_seconds:.1f}s (mốc tham khảo 165 từ/phút)\n\n"
                f"THỜI LƯỢNG TỐI THIỂU: {duration}s (không có giới hạn tối đa)\n"
                f"TỰ MỞ RỘNG NẾU THIẾU: {'Có' if length_revised else 'Không'}"
                + (f" (ban đầu {original_words} từ / {original_seconds:.1f}s)" if length_revised else "")
                + "\n"
                f"KIỂM TRA THỜI LƯỢNG: {duration_status}\n\n"
                f"{script}"
            )
            self.after(0, self._set_text, self.script_text, script_view)
            self.after(0, self.status_var.set, "Đang nhờ DeepSeek đối chiếu lại nguồn và kịch bản…")

            evaluation = call_deepseek(
                api_key,
                EVALUATOR_SYSTEM_PROMPT,
                build_evaluation_prompt(source, result, hint),
                temperature=0.2,
            )

            self.after(0, self.status_var.set, "Prompt cũ đang viết một bản riêng…")
            old_script = call_deepseek_text(
                api_key,
                OLD_WRITER_SYSTEM_PROMPT,
                build_old_writer_prompt(source),
            )
            if len(old_script) < 40:
                raise RuntimeError("Prompt cũ trả về kịch bản rỗng hoặc quá ngắn.")
            old_words, old_seconds = estimate_script_seconds(old_script)
            old_view = (
                f"SỐ TỪ: {old_words}\n"
                f"THỜI LƯỢNG ƯỚC TÍNH: {old_seconds:.1f}s (165 từ/phút)\n\n"
                f"{old_script}"
            )
            self.after(0, self._set_text, self.old_script_text, old_view)

            self.after(0, self.status_var.set, "Đang chấm prompt cũ bằng cùng thang điểm…")
            old_evaluation = call_deepseek(
                api_key,
                EVALUATOR_SYSTEM_PROMPT,
                build_evaluation_prompt(source, {"title": "", "script": old_script}, hint),
                temperature=0.2,
            )

            self.after(0, self.status_var.set, "Đang kết luận so sánh A/B…")
            head_to_head = call_deepseek(
                api_key,
                "You are a neutral senior content director. Compare evidence and scores fairly. Return JSON only.",
                build_head_to_head_prompt(source, result, evaluation, old_script, old_evaluation),
                temperature=0.1,
            )
            compare_view = (
                "=== PROMPT MỚI ===\n"
                + json.dumps(evaluation, ensure_ascii=False, indent=2)
                + "\n\n=== PROMPT CŨ ===\n"
                + json.dumps(old_evaluation, ensure_ascii=False, indent=2)
                + "\n\n=== KẾT LUẬN A/B ===\n"
                + json.dumps(head_to_head, ensure_ascii=False, indent=2)
            )
            raw_view = json.dumps(
                {
                    "new_prompt": {"writer": result, "evaluation": evaluation},
                    "old_prompt": {"script": old_script, "evaluation": old_evaluation},
                    "head_to_head": head_to_head,
                },
                ensure_ascii=False,
                indent=2,
            )
            self.after(0, self._set_text, self.compare_text, compare_view)
            self.after(0, self._set_text, self.raw_text, raw_view)
            self.after(0, self.status_var.set, "Hoàn tất: đã chấm Prompt mới và Prompt cũ trên cùng một nguồn.")
        except Exception as exc:
            self.after(0, self.status_var.set, "Có lỗi khi chạy thử.")
            self.after(0, messagebox.showerror, "Prompt Test Lab", str(exc))
        finally:
            self.after(0, self.run_btn.configure, {"state": tk.NORMAL})


if __name__ == "__main__":
    PromptTestLab().mainloop()
