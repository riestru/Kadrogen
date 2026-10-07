"""Субтитры из слов с таймкодами: сборка .srt и наложение на видео через ffmpeg.

Правила как в хороших инструментах: строка не длиннее 42 символов, реплика режется по знакам
препинания и паузам, паузы до 5 секунд склеиваются (субтитр держится до следующего), чтобы на
экране не было мигания.
"""
import os
import re
import subprocess

CHARS_PER_LINE = 42
MAX_LINES = 2
MAX_CUE_SECONDS = 6.0
GLUE_MAX_GAP = 5.0
MIN_CUE_SECONDS = 0.8


def _fmt(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build_cues(words):
    """words: [{"text","start","end"}] -> [{"start","end","text"}] (text может быть из двух строк)."""
    cues, cur = [], []

    def cur_len(extra=None):
        ws = cur + ([extra] if extra else [])
        return len(" ".join(w["text"] for w in ws))

    def flush():
        if not cur:
            return
        text = " ".join(w["text"] for w in cur)
        cues.append({"start": cur[0]["start"], "end": cur[-1]["end"], "text": wrap(text)})
        cur.clear()

    for i, w in enumerate(words):
        if cur:
            gap = w["start"] - cur[-1]["end"]
            too_long = cur_len(w) > CHARS_PER_LINE * MAX_LINES - 4
            too_slow = w["end"] - cur[0]["start"] > MAX_CUE_SECONDS
            if too_long or too_slow or gap > 1.2:
                flush()
        cur.append(w)
        strong = re.search(r"[.!?…]$", w["text"]) is not None
        weak = re.search(r"[,;:]$", w["text"]) is not None
        if strong or (weak and cur_len() > CHARS_PER_LINE):
            flush()
    flush()
    # склейка пауз и минимальная длительность
    for i, c in enumerate(cues):
        nxt = cues[i + 1]["start"] if i + 1 < len(cues) else None
        if nxt is not None:
            gap = nxt - c["end"]
            if 0 < gap <= GLUE_MAX_GAP:
                c["end"] = nxt
        if c["end"] - c["start"] < MIN_CUE_SECONDS:
            c["end"] = c["start"] + MIN_CUE_SECONDS
            if nxt is not None and c["end"] > nxt:
                c["end"] = nxt
    return cues


def wrap(text):
    """Разбить реплику на 1–2 строки не длиннее CHARS_PER_LINE, по возможности по знаку препинания."""
    if len(text) <= CHARS_PER_LINE:
        return text
    words = text.split()
    best, line1 = None, []
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        if len(a) > CHARS_PER_LINE or len(b) > CHARS_PER_LINE:
            continue
        score = abs(len(a) - len(b)) - (12 if re.search(r"[,.;:!?…]$", a) else 0)
        if best is None or score < best[0]:
            best = (score, a, b)
    if best:
        return best[1] + "\n" + best[2]
    # не влезает в две строки — режем как есть
    mid = len(words) // 2
    return " ".join(words[:mid]) + "\n" + " ".join(words[mid:])


def write_srt(words, path):
    cues = build_cues(words)
    with open(path, "w", encoding="utf-8-sig") as f:
        for i, c in enumerate(cues, 1):
            f.write(f"{i}\n{_fmt(c['start'])} --> {_fmt(c['end'])}\n{c['text']}\n\n")
    return path


# Шрифт пользователя (fonts/IntroDemo-BlackCAPS.otf, семейство «Intro Demo Black CAPS»); тонкая обводка, ниже к краю
STYLE = "FontName=Intro Demo Black CAPS,FontSize=15,Bold=0,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H60000000,Outline=1,Shadow=0.6,MarginV=12,Alignment=2"
FONT_FILE = "IntroDemo-BlackCAPS.otf"


def fonts_dir():
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(sys.argv[0]))
    for d in (os.path.join(base, "fonts"), os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")):
        if os.path.exists(os.path.join(d, FONT_FILE)):
            return d
    return None


def burn(ffmpeg, video_in, srt_path, video_out, no_window=0):
    """Наложить субтитры: перекодирует видео один раз, звук копируется. Шрифт кладётся рядом с .srt (папка fonts)."""
    import shutil
    workdir = os.path.dirname(os.path.abspath(srt_path))
    srt_name = os.path.basename(srt_path)
    style, fd = STYLE, fonts_dir()
    if fd:
        local = os.path.join(workdir, "fonts")
        os.makedirs(local, exist_ok=True)
        if not os.path.exists(os.path.join(local, FONT_FILE)):
            shutil.copy(os.path.join(fd, FONT_FILE), local)
        vf = f"subtitles={srt_name}:fontsdir=fonts:force_style='{style}'"
    else:
        vf = f"subtitles={srt_name}:force_style='{style.replace('Intro Demo Black CAPS', 'Arial')}'"
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-i", os.path.abspath(video_in), "-vf", vf,
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "copy", "-movflags", "+faststart", os.path.abspath(video_out)]
    r = subprocess.run(cmd, capture_output=True, cwd=workdir, creationflags=no_window)
    if r.returncode != 0:
        raise RuntimeError("ffmpeg (субтитры): " + r.stderr.decode(errors="replace")[:400])
    return video_out
