# -*- coding: utf-8 -*-
"""Рерайт сценария: источник — ссылка на YouTube (субтитры ролика) или свой текст;
переписывает языковая модель по ключу пользователя (ChatGPT / Claude / Gemini) или через чат fast-gen."""
import os
import re
import json
import time
import hashlib
import subprocess

import requests
import endpoints

PROMPT = """Прочитай и проанализируй весь сценарий видео на ютубе целиком и полностью.
Есть ли какие то ошибки в тексте, которые плохо повлияли бы на удержание и вовлечение зрителя?
Цель и задача рассказа: стопроцентное удержание зрителя интересным текстом, максимальная вовлеченность именно КАЧЕСТВОМ интересного сценария без фактологических ошибок. У СЕБЯ В ГОЛОВЕ АНАЛИЗИРУЙ, МНЕ НИЧЕГО НЕ ПИШИ.
исправим в тексте то что ты расписал, а также:
1 полностью уберем все призывы к подпискам, лайкам, комментариям и тому подобное (если имеется)
2 сделаем идеальное байтовое вступление (хук), которое заставит зрителя досмотреть до самого конца и приведёт к максимальному удержанию и вовлечению
Пиши на {language} языке сценарий txt-файлом, не md, без технического текста - сразу на озвучку, примерно на {words} слов, цифры и числа пиши буквами.
"Отрерайти" текст сразу с нужным объемом, напиши его как бы другими словами, особенно начало, нужен как бы новый, уникальный рассказ с исправленными ошибками
Текст объемный, имей ввиду СРАЗУ это при написании. Не меняй тему суть и нарратив рассказа
Цель и задача - максимальное удержание и вовлеченность зрителя на ютубе который будет слушать этот рассказ. ОТПРАВЬ МНЕ ИСКЛЮЧИТЕЛЬНО ТЕКСТ.
Начни ответ сразу с первого предложения сценария. Никаких вступлений вроде «Вот сценарий», «Принял», заголовков, названий, пометок и заметок в конце.

Сценарий:
{script}"""

# язык из списка программы (в предложном падеже, как в промпте) → (английское название, самоназвание, кириллица ли)
LANG_INFO = {
    "английском": ("English", "English", False), "китайском": ("Chinese", "中文", False), "хинди": ("Hindi", "हिन्दी", False),
    "испанском": ("Spanish", "español", False), "арабском": ("Arabic", "العربية", False), "французском": ("French", "français", False),
    "бенгальском": ("Bengali", "বাংলা", False), "португальском": ("Portuguese", "português", False), "русском": ("Russian", "русский", True),
    "урду": ("Urdu", "اردو", False), "индонезийском": ("Indonesian", "Bahasa Indonesia", False), "немецком": ("German", "Deutsch", False),
    "японском": ("Japanese", "日本語", False), "турецком": ("Turkish", "Türkçe", False), "вьетнамском": ("Vietnamese", "Tiếng Việt", False),
    "корейском": ("Korean", "한국어", False), "итальянском": ("Italian", "italiano", False), "персидском": ("Persian", "فارسی", False),
    "польском": ("Polish", "polski", False), "украинском": ("Ukrainian", "українська", True), "голландском": ("Dutch", "Nederlands", False),
    "румынском": ("Romanian", "română", False), "греческом": ("Greek", "ελληνικά", False), "чешском": ("Czech", "čeština", False),
    "венгерском": ("Hungarian", "magyar", False), "шведском": ("Swedish", "svenska", False), "казахском": ("Kazakh", "қазақша", True),
    "узбекском": ("Uzbek", "oʻzbekcha", False), "азербайджанском": ("Azerbaijani", "azərbaycanca", False), "иврите": ("Hebrew", "עברית", False),
    "финском": ("Finnish", "suomi", False), "датском": ("Danish", "dansk", False), "норвежском": ("Norwegian", "norsk", False),
}


def lang_rule(language):
    """Жёсткое указание языка — ставится В НАЧАЛО и В КОНЕЦ каждого запроса. Раньше язык был одним словом в середине русской
    инструкции, и на длинных сценариях модель срывалась на русский (тестер: половина по-русски, половина по-польски)."""
    en, native, _ = LANG_INFO.get(language, (language, language, None))
    return (f"OUTPUT LANGUAGE: {en} ({native}). Write the ENTIRE script ONLY in {en}. Every sentence must be in {en}, "
            f"even though these instructions and the source may be in another language. Do not switch languages anywhere.")


def wrong_language(text, language):
    """Грубая, но надёжная проверка под жалобы тестеров: русский текст вместо испанского/польского.
    Для языков не на кириллице доля кириллических букв должна быть мала; для кириллических — наоборот."""
    cyr = LANG_INFO.get(language, (None, None, None))[2]
    letters = [ch for ch in text if ch.isalpha()]
    if cyr is None or len(letters) < 80:
        return False
    share = sum(1 for ch in letters if "Ѐ" <= ch <= "ӿ") / len(letters)
    return share > 0.12 if not cyr else share < 0.5


def bad_paragraphs(text, language):
    """Абзацы не на том языке (модель могла сорваться посреди части). Возвращает (номера, абзацы)."""
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    return [i for i, p in enumerate(paras) if wrong_language(p, language)], paras


ENGINES = ("openai", "anthropic", "gemini", "fastgen")
ENGINE_NAMES = {"openai": "ChatGPT", "anthropic": "Claude", "gemini": "Gemini", "fastgen": "fast-gen"}
DEFAULT_MODELS = {"openai": "gpt-4.1", "anthropic": "claude-sonnet-5-5", "gemini": "gemini-3.6-flash", "fastgen": "openai/gpt-4.1-mini"}


class RewriteError(Exception):
    pass


# ---------- YouTube ----------
def youtube_id(url):
    url = (url or "").strip()
    m = re.search(r"(?:v=|youtu\.be/|shorts/|live/|embed/)([\w-]{11})", url)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", url):
        return url
    raise RewriteError("Не похоже на ссылку YouTube: " + url[:80])


def youtube_transcript(url, progress=None):
    """Текст субтитров ролика (сначала ручные, потом автоматические, любой язык)."""
    from youtube_transcript_api import YouTubeTranscriptApi
    vid = youtube_id(url)
    if progress:
        progress("забираю субтитры ролика с YouTube")
    api = YouTubeTranscriptApi()
    listing = api.list(vid)
    tracks = sorted(listing, key=lambda t: (t.is_generated, t.language_code != "en"))
    if not tracks:
        raise RewriteError("У ролика нет субтитров")
    tr = tracks[0].fetch()
    text = " ".join(s.text.replace("\n", " ") for s in tr if s.text and not s.text.startswith("["))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 200:
        raise RewriteError("Субтитры ролика слишком короткие")
    return text


def youtube_audio(url, out_dir, ffmpeg_dir, progress=None):
    """Запасной путь: если субтитров нет — скачать звук через yt-dlp, чтобы потом распознать."""
    import sys
    vid = youtube_id(url)
    out = os.path.join(out_dir, f"yt_{vid}.m4a")
    if os.path.exists(out) and os.path.getsize(out) > 10000:
        return out
    if progress:
        progress("у ролика нет субтитров — скачиваю звук")
    # yt-dlp вызываем внутри процесса. Раньше запускался `sys.executable -m yt_dlp`, а в собранной программе
    # sys.executable — это сам KadroGen.exe: вместо загрузчика открывались новые окна программы.
    try:
        import yt_dlp
    except ImportError as e:
        raise RewriteError(f"Загрузчик yt-dlp недоступен: {e}")
    base = os.path.splitext(out)[0]
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "format": "bestaudio[ext=m4a]/bestaudio/best",
            "ffmpeg_location": ffmpeg_dir, "outtmpl": base + ".%(ext)s", "retries": 3, "nocheckcertificate": True,
            "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}],
            "logger": _QuietLogger(), "windowsfilenames": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([f"https://www.youtube.com/watch?v={vid}"])
    except Exception as e:
        msg = str(e)
        low = msg.lower()
        if "getaddrinfo" in low or "failed to resolve" in low or "11001" in low or "name resolution" in low:
            raise RewriteError("YouTube недоступен с этого компьютера: не открывается адрес www.youtube.com. "
                               "Обычно это блокировка провайдера — включите VPN и запустите ещё раз, либо скачайте mp3 сами и загрузите как файл.")
        raise RewriteError("Не удалось скачать звук ролика: " + msg[-300:])
    if not os.path.exists(out) or os.path.getsize(out) < 10000:
        cand = [p for p in os.listdir(out_dir) if p.startswith(f"yt_{vid}.")]
        if cand:
            os.replace(os.path.join(out_dir, cand[0]), out)
    if not os.path.exists(out):
        raise RewriteError("Не удалось скачать звук ролика: файл не появился")
    return out


class _QuietLogger:
    def debug(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        pass


# ---------- языковые модели, обычный текст ----------
def _retry(fn, tries=4):
    last = None
    for attempt in range(tries):
        try:
            return fn()
        except requests.RequestException as e:
            last = str(e)
            time.sleep(5 * (attempt + 1))
        except RewriteError as e:
            if not e.args or not str(e).startswith("retry"):
                raise
            last = str(e)
            time.sleep(10 * (attempt + 1))
    raise RewriteError(f"Модель не ответила: {last}")


def chat_text(engine, key, model, prompt, max_tokens):
    model = model or DEFAULT_MODELS[engine]
    if engine == "openai":
        def go():
            from openai_api import _chat_params
            r = requests.post(endpoints.base("openai") + "/chat/completions", headers={"Authorization": f"Bearer {key}"},
                              json={"model": model, **_chat_params(model, max_tokens, 0.8),
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code == 429 or r.status_code >= 500:
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"ChatGPT {r.status_code}: {r.text[:200]}")
            return r.json()["choices"][0]["message"]["content"]
        return _retry(go)
    if engine == "anthropic":
        def go():
            r = requests.post(endpoints.base("anthropic") + "/messages",
                              headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                              json={"model": model, "max_tokens": max_tokens,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code in (429, 500, 502, 503, 529):
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"Claude {r.status_code}: {r.text[:200]}")
            return "".join(b.get("text", "") for b in r.json().get("content", []))
        return _retry(go)
    if engine == "gemini":
        limit = [max_tokens + 4000]  # у Gemini 2.5 «размышления» съедают лимит ответа — даём запас и режем бюджет размышлений

        def go():
            gen = {"temperature": 0.8, "maxOutputTokens": limit[0]}
            if "2.5-flash" in model:
                gen["thinkingConfig"] = {"thinkingBudget": 0}
            elif "2.5-pro" in model:
                gen["thinkingConfig"] = {"thinkingBudget": 128}  # у pro размышления выключить нельзя, минимум 128
            r = requests.post(f"{endpoints.base('gemini')}/models/{model}:generateContent",
                              params={"key": key},
                              json={"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": gen}, timeout=600)
            if r.status_code in (429, 500, 502, 503):
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"Gemini {r.status_code}: {r.text[:200]}")
            j = r.json()
            block = (j.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise RewriteError(f"Gemini отклонил запрос: {block}")
            cand = (j.get("candidates") or [{}])[0]
            text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
            if not text.strip():
                if cand.get("finishReason") == "MAX_TOKENS" and limit[0] < 60000:
                    limit[0] = min(60000, limit[0] * 2)
                raise RewriteError(f"retry пустой ответ Gemini, finishReason={cand.get('finishReason')}")
            return text
        return _retry(go)
    if engine == "fastgen":
        def go(model=model):
            r = requests.post("https://api.fast-gen.ai/v1/chat/completions", headers={"X-API-Key": key},
                              json={"model": model, "temperature": 0.8, "max_tokens": max_tokens,
                                    "messages": [{"role": "user", "content": prompt}]}, timeout=600)
            if r.status_code == 429 or r.status_code >= 500:
                raise RewriteError(f"retry {r.status_code}: {r.text[:150]}")
            if r.status_code >= 400:
                raise RewriteError(f"fast-gen chat {r.status_code}: {r.text[:200]}")
            return r.json()["choices"][0]["message"]["content"]
        try:
            return _retry(go, tries=2)
        except RewriteError:
            return _retry(lambda: go("google/gemini-3.6-flash"))
    raise RewriteError("Неизвестный движок: " + str(engine))


def clean_text(t):
    """Убираем markdown-мусор, кавычки-обёртки и служебные строки."""
    junk = re.compile(r"code_reference|code_event_index|\[file-tag|^\s*word count\s*:|^```|^\s*[?&=\w]*\s*$|сценарий .*готов\s*:?\s*$", re.I)
    lines = [l for l in t.splitlines() if not l.strip() or not junk.search(l)]
    t = "\n".join(lines).strip()
    t = re.sub(r'^\s*\w+\s*=\s*(?:"""|\'\'\')', "", t)  # модель иногда оборачивает: text = """..."""
    t = re.sub(r'(?:"""|\'\'\')\s*$', "", t)
    t = re.sub(r"\[[^\]\n]*[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}[^\]\n]*\]", "", t)  # служебные ссылки вида [... uuid]
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"[ 	]*:{2,}[ 	]*$", "", t, flags=re.M)  # хвосты вида « :::» от некоторых моделей
    # служебные абзацы в начале («Вот переписанный сценарий:», «Принял задачу…») и в конце («Итого слов…»)
    meta = re.compile(r"вот |ниже |готов|переписан|сценари|версия|принял|проанализ|хук|вступлени|here is|here's|rewritten|script|title|"
                      r"название|итого|слов[:\s]|word|конец|the end|надеюсь|если нужно|если хотите", re.I)
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    while len(paras) > 1 and len(paras[0].split()) < 30 and meta.search(paras[0]):
        paras.pop(0)
    while len(paras) > 1 and len(paras[-1].split()) < 30 and meta.search(paras[-1]):
        paras.pop()
    if paras and paras[0].endswith(":") and len(paras[0].split()) < 12:
        paras.pop(0)
    return "\n\n".join(paras).strip()


def rewrite_key(source_text, words, language, engine):
    return hashlib.sha1(f"v2|{engine}|{language}|{words}|{source_text.strip()}".encode("utf-8")).hexdigest()[:10]


def rewrite(source_text, engine, key, model, words, language, cache_dir, progress=None):
    """Возвращает переписанный сценарий; результат кэшируется по содержимому исходника."""
    words = max(100, int(words or 1500))
    language = (language or "польском").strip()
    os.makedirs(cache_dir, exist_ok=True)
    cache = os.path.join(cache_dir, f"rewrite_{rewrite_key(source_text, words, language, engine)}.txt")
    if os.path.exists(cache) and os.path.getsize(cache) > 200:
        if progress:
            progress("рерайт уже есть, беру из кэша")
        return open(cache, encoding="utf-8").read()
    name = ENGINE_NAMES.get(engine, engine)
    if words <= PART_WORDS:
        if progress:
            progress(f"{name} переписывает сценарий (~{words} слов, это займёт минуту-две)")
        out = _ask(engine, key, model, lang_rule(language) + "\n\n" + PROMPT.format(language=language, words=words, script=source_text.strip())
                   + "\n\n" + lang_rule(language), words, language, progress)
    else:
        # Длинный сценарий одним ответом модели не пишут («too long for a single response») — пишем частями
        n = -(-words // PART_WORDS)
        per = -(-words // n)
        parts = []
        for i in range(1, n + 1):
            if progress:
                progress(f"{name} пишет часть {i} из {n} (~{per} слов)")
            tail = " ".join(" ".join(parts).split()[-350:]) if parts else ""
            prompt = lang_rule(language) + "\n\n" + PART_PROMPT.format(language=language, words=words, per=per, i=i, n=n, script=source_text.strip(),
                                        tail=(tail or "(это первая часть)"),
                                        ending="Это последняя часть: доведи рассказ до конца и заверши его." if i == n
                                        else "Это НЕ последняя часть: не завершай рассказ, остановись на месте, с которого удобно продолжить.")
            prompt += "\n\n" + lang_rule(language)
            parts.append(_ask(engine, key, model, prompt, per, language, progress))
        out = "\n\n".join(parts)
    with open(cache, "w", encoding="utf-8") as f:
        f.write(out)
    return out


PART_WORDS = 1200
REFUSAL = re.compile(r"too long|single response|cannot (?:fit|write)|can't (?:fit|write)|не могу (?:уместить|написать)|слишком длинн"
                     r"|^\s*(?:принял задачу|понял|хорошо|конечно|sure|certainly|okay|ok\b|i(?:'ll| will) (?:analy|write|rewrite)|я проанализировал)", re.I)
PART_PROMPT = PROMPT + """

ВАЖНО: итоговый сценарий большой (~{words} слов), поэтому пишем его по частям. Сейчас напиши ТОЛЬКО часть {i} из {n}, примерно {per} слов.
Конец предыдущей части (продолжай ровно с этого места, не повторяй его и не пересказывай начало):
{tail}
{ending}
Никаких заголовков, номеров частей и пояснений — только текст сценария."""


def fix_language(engine, key, model, text, language, progress=None):
    """Абзацы не на том языке переводим отдельным запросом; остальное не трогаем."""
    bad, paras = bad_paragraphs(text, language)
    if not bad:
        return text
    en, native, _ = LANG_INFO.get(language, (language, language, None))
    if progress:
        progress(f"часть текста получилась не на том языке — перевожу {len(bad)} абзац(ев) на {native}")
    out, i = [], 0
    while i < len(paras):
        if i not in bad:
            out.append(paras[i])
            i += 1
            continue
        j = i
        while j + 1 < len(paras) and (j + 1) in bad:  # соседние плохие абзацы переводим одним куском — связность
            j += 1
        chunk = "\n\n".join(paras[i:j + 1])
        tr = clean_text(chat_text(engine, key, model, f"Translate the following text into {en} ({native}). Keep every sentence, the meaning, "
                                  f"the tone and the paragraph breaks. Numbers stay written out in words. Output ONLY the translation, "
                                  f"nothing else.\n\n{chunk}", max_tokens=min(60000, len(chunk.split()) * 6 + 800)))
        out.append(tr if tr and not wrong_language(tr, language) else chunk)
        i = j + 1
    return "\n\n".join(out)


def _ask(engine, key, model, prompt, want, language=None, progress=None):
    """Один запрос к модели с проверкой: отказ, слишком короткий ответ или не тот язык → повтор, потом запасная модель."""
    last = ""
    for attempt in range(3):
        use_model = model
        if attempt == 2 and engine == "fastgen":
            use_model = "google/gemini-3.6-flash"  # третья попытка — другой моделью
        out = clean_text(chat_text(engine, key, use_model, prompt, max_tokens=min(60000, want * 4 + 800)))
        if not REFUSAL.search(out[:300]) and len(out.split()) >= want * 0.3:
            if language:
                bad, paras = bad_paragraphs(out, language)
                if bad and len(bad) > len(paras) * 0.6 and attempt < 2:
                    last = out  # почти всё не на том языке — проще переспросить
                    time.sleep(2)
                    continue
                out = fix_language(engine, key, use_model, out, language, progress)
            return out
        last = out
        time.sleep(3)
    if language and last and len(last.split()) >= want * 0.3 and not REFUSAL.search(last[:300]):
        return fix_language(engine, key, model, last, language, progress)
    raise RewriteError("Модель не справилась с объёмом даже по частям: " + last[:160])
