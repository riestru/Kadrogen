"""Обёртка над API OpenAI: распознавание речи (Whisper, таймкоды слов) и чат-модель.

Используется, когда у пользователя нет ключа fast-gen. Ключ читается из config.json (openai_api_key)
и никогда не печатается.
"""
import json
import os
import subprocess
import tempfile
import time

import requests

import endpoints


def BASE():
    return endpoints.base("openai")


def _chat_params(model, max_tokens, temperature):
    """gpt-5.x и o-серия не принимают max_tokens/temperature — у них max_completion_tokens и температура только 1."""
    m = (model or "").lower()
    if m.startswith(("gpt-5", "o1", "o3", "o4")):
        return {"max_completion_tokens": max_tokens}
    return {"max_tokens": max_tokens, "temperature": temperature}
CHUNK_SECONDS = 600           # Whisper принимает файлы до 25 МБ: режем аудио по 10 минут
DEFAULT_CHAT_MODEL = "gpt-4.1-mini"
TRANSCRIBE_MODEL = "whisper-1"  # единственная модель с таймкодами слов


class OpenAIError(RuntimeError):
    pass


class OpenAIClient:
    def __init__(self, api_key: str, timeout: int = 120):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout

    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message") or r.text[:300]
            except ValueError:
                msg = r.text[:300]
            raise OpenAIError(f"{r.status_code}: {str(msg)[:300]}")
        return r.json()

    def check(self) -> str:
        """Проверка ключа: список моделей."""
        import time as _t
        for attempt in range(3):  # api.openai.com иногда отвечает не с первого раза
            try:
                j = self._check(self.s.get(BASE() + "/models", timeout=30))
                break
            except requests.exceptions.RequestException:
                if attempt == 2:
                    raise
                _t.sleep(2)
        ids = [m.get("id", "") for m in j.get("data", [])]
        has_whisper = any("whisper" in i for i in ids)
        return "OK" + ("" if has_whisper else " · внимание: модель whisper-1 не видна на этом ключе")

    # --- чат ---
    def chat_json(self, system: str, user: str, model: str = None, max_tokens: int = 4000, temperature: float = 0.7) -> dict:
        last = None
        limit = max_tokens
        for attempt in range(4):
            try:
                r = self.s.post(BASE() + "/chat/completions", json={
                    "model": model or DEFAULT_CHAT_MODEL,
                    "response_format": {"type": "json_object"},
                    **_chat_params(model or DEFAULT_CHAT_MODEL, limit, temperature),
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                }, timeout=300)
                if r.status_code == 429 or r.status_code >= 500:
                    last = f"HTTP {r.status_code}: {r.text[:200]}"
                    time.sleep(10 * (attempt + 1))
                    continue
                j = self._check(r)
                choice = j["choices"][0]
                if choice.get("finish_reason") == "length" and limit < 32000:
                    limit = min(32000, limit * 2)  # ответ обрезан — повторяем с большим лимитом
                    last = f"ответ не влез в лимит, повторяю с {limit} токенами"
                    continue
                return json.loads(choice["message"]["content"])
            except (requests.RequestException, json.JSONDecodeError, KeyError) as e:
                last = str(e)
                time.sleep(5 * (attempt + 1))
        raise OpenAIError(f"чат-модель не ответила корректно: {last}")

    # --- распознавание речи ---
    def _transcribe_file(self, path: str, language: str = None) -> dict:
        data = {"model": TRANSCRIBE_MODEL, "response_format": "verbose_json", "timestamp_granularities[]": "word"}
        if language:
            data["language"] = language
        last = ""
        for attempt in range(6):
            try:
                with open(path, "rb") as f:
                    r = self.s.post(BASE() + "/audio/transcriptions", data=data,
                                    files={"file": (os.path.basename(path), f, "audio/mpeg")}, timeout=600)
            except requests.RequestException as e:
                last = str(e)[:200]
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code == 429 and "insufficient_quota" in r.text:
                raise OpenAIError("на ключе OpenAI закончилась квота или не привязана карта (insufficient_quota). "
                                  "Пополните баланс на platform.openai.com или выберите другой распознаватель.")
            if r.status_code == 401:
                raise OpenAIError("ключ OpenAI не принят (401). Проверьте ключ.")
            if r.status_code == 429 or r.status_code >= 500:
                last = f"{r.status_code}: {r.text[:200]}"
                time.sleep(15 * (attempt + 1))
                continue
            return self._check(r)
        raise OpenAIError(f"сервис распознавания OpenAI не ответил после 6 попыток ({last})")

    def transcribe(self, audio_path: str, ffmpeg: str, ffprobe: str, duration: float, language: str = None,
                   progress=None) -> dict:
        """Возвращает {"text": ..., "words": [{"text","start","end"}]}. Длинное аудио режется на куски."""
        no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        n_chunks = max(1, int(duration // CHUNK_SECONDS) + (1 if duration % CHUNK_SECONDS > 1 else 0))
        words, texts = [], []
        with tempfile.TemporaryDirectory() as tmp:
            for k in range(n_chunks):
                start = k * CHUNK_SECONDS
                if n_chunks == 1 and os.path.getsize(audio_path) < 24 * 1024 * 1024:
                    part = audio_path
                else:
                    part = os.path.join(tmp, f"part{k:03d}.mp3")
                    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-ss", str(start), "-t", str(CHUNK_SECONDS),
                                    "-i", audio_path, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", part],
                                   check=True, capture_output=True, creationflags=no_window)
                if progress:
                    progress("transcribe", k, n_chunks, f"распознаю речь (OpenAI Whisper), часть {k + 1} из {n_chunks}")
                j = self._transcribe_file(part, language)
                texts.append((j.get("text") or "").strip())
                for w in j.get("words") or []:
                    words.append({"text": w.get("word", "").strip(), "start": float(w["start"]) + start,
                                  "end": float(w["end"]) + start})
        # Whisper отдаёт слова без знаков препинания — восстанавливаем их из текста, чтобы резать по фразам
        words = _attach_punctuation(words, " ".join(texts))
        return {"text": " ".join(texts), "words": words}


def _attach_punctuation(words, text):
    """Сопоставляем слова с текстом и переносим знаки препинания в конец слов."""
    import re
    tokens = text.split()
    ti = 0
    out = []
    for w in words:
        core = re.sub(r"[^\w]", "", w["text"]).lower()
        matched = None
        for look in range(ti, min(ti + 4, len(tokens))):
            if re.sub(r"[^\w]", "", tokens[look]).lower() == core and core:
                matched = tokens[look]
                ti = look + 1
                break
        out.append({"text": matched if matched else w["text"], "start": w["start"], "end": w["end"]})
    return out
