"""Языковые модели (Claude, Gemini) и распознавание речи AssemblyAI — для тех, у кого нет fast-gen.

Интерфейс языковой модели: chat_json(system, user, max_tokens) -> dict.
Интерфейс распознавания: transcribe(audio_path) -> {"text", "words":[{"text","start","end"}]}.
Ключи никогда не печатаются.
"""
import json
import re
import time

import requests
import netutil
import endpoints

LLM_SERVICES = {
    "openai": {"name": "OpenAI", "url": "https://platform.openai.com/api-keys", "placeholder": "sk-...", "default_model": "gpt-4.1-mini"},
    "anthropic": {"name": "Claude (Anthropic)", "url": "https://console.anthropic.com/settings/keys", "placeholder": "sk-ant-...", "default_model": "claude-sonnet-5-5"},
    "gemini": {"name": "Gemini (Google)", "url": "https://aistudio.google.com/apikey", "placeholder": "AIza...", "default_model": "gemini-3.6-flash"},
}
STT_SERVICES = {
    "whisper": {"name": "OpenAI Whisper", "url": "https://platform.openai.com/api-keys", "placeholder": "sk-..."},
    "assemblyai": {"name": "AssemblyAI", "url": "https://www.assemblyai.com/app", "placeholder": "ключ AssemblyAI"},
}


class LLMError(RuntimeError):
    pass


def extract_json(text):
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1)
    i, j = t.find("{"), t.rfind("}")
    if i < 0 or j < 0:
        raise json.JSONDecodeError("нет JSON", t, 0)
    return json.loads(t[i:j + 1])


class AnthropicClient:
    @property
    def BASE(self):
        return endpoints.base("anthropic")

    def __init__(self, key, model=None):
        self.s = requests.Session()
        self.s.headers.update({"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
        self.model = model or LLM_SERVICES["anthropic"]["default_model"]

    def check(self):
        r = self.s.get(self.BASE + "/models", timeout=30)
        if r.status_code >= 400:
            raise LLMError(f"Claude: {r.status_code} {r.text[:150]}")
        return "OK"

    def chat_json(self, system, user, max_tokens=4000):
        """Если ответ не влез в max_tokens (stop_reason=max_tokens), JSON приходит обрезанным —
        удваиваем лимит и повторяем, а не падаем с «Expecting ',' delimiter»."""
        last = None
        limit = max_tokens
        for attempt in range(4):
            r = self.s.post(self.BASE + "/messages", json={
                "model": self.model, "max_tokens": limit,  # без temperature: новые модели Claude отвечают 400
                "system": system + "\nОтвечай только JSON, без пояснений и без markdown.",
                "messages": [{"role": "user", "content": user}],
            }, timeout=300)
            if r.status_code in (429, 500, 502, 503, 529):
                last = f"{r.status_code} {r.text[:150]}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise LLMError(f"Claude: {r.status_code} {r.text[:200]}")
            try:
                j = r.json()
                text = "".join(b.get("text", "") for b in j.get("content", []))
                if j.get("stop_reason") == "max_tokens" and limit < 32000:
                    limit = min(32000, limit * 2)
                    last = f"ответ не влез в лимит, повторяю с {limit} токенами"
                    continue
                return extract_json(text)
            except (json.JSONDecodeError, KeyError) as e:
                last = str(e)
                time.sleep(3)
        raise LLMError(f"Claude не ответил корректно: {last}")


class GeminiClient:
    @property
    def BASE(self):
        return endpoints.base("gemini")

    def __init__(self, key, model=None):
        self.key = key
        self.model = model or LLM_SERVICES["gemini"]["default_model"]

    def check(self):
        r = requests.get(self.BASE + "/models", params={"key": self.key}, timeout=30)
        if r.status_code >= 400:
            raise LLMError(f"Gemini: {r.status_code} {r.text[:150]}")
        return "OK"

    def chat_json(self, system, user, max_tokens=4000):
        """Gemini 2.5 по умолчанию «думает», и размышления съедают maxOutputTokens — ответ приходил пустым
        («нет JSON: line 1 column 1»). Для flash-моделей 2.5 размышления выключаем, при обрезке ответа увеличиваем лимит."""
        last = None
        limit = max_tokens
        for attempt in range(4):
            gen = {"temperature": 0.7, "maxOutputTokens": limit, "responseMimeType": "application/json"}
            if "2.5-flash" in self.model:
                gen["thinkingConfig"] = {"thinkingBudget": 0}
            r = requests.post(self.BASE + f"/models/{self.model}:generateContent", params={"key": self.key}, json={
                "system_instruction": {"parts": [{"text": system + "\nОтвечай только JSON."}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen,
            }, timeout=300)
            if r.status_code in (429, 500, 502, 503):
                last = f"{r.status_code} {r.text[:150]}"
                time.sleep(10 * (attempt + 1))
                continue
            if r.status_code >= 400:
                raise LLMError(f"Gemini: {r.status_code} {r.text[:200]}")
            try:
                j = r.json()
                block = (j.get("promptFeedback") or {}).get("blockReason")
                if block:
                    raise LLMError(f"Gemini отклонил запрос: {block}")
                cand = (j.get("candidates") or [{}])[0]
                reason = cand.get("finishReason")
                text = "".join(p.get("text", "") for p in (cand.get("content") or {}).get("parts") or [])
                if not text.strip() or reason == "MAX_TOKENS":
                    last = f"{'пустой' if not text.strip() else 'обрезанный'} ответ, finishReason={reason}"
                    if reason == "MAX_TOKENS" and limit < 32000:
                        limit = min(32000, limit * 2)
                    time.sleep(3)
                    continue
                return extract_json(text)
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                last = str(e)
                time.sleep(3)
        raise LLMError(f"Gemini не ответил корректно: {last}")


class AssemblyAI:
    BASE = "https://api.assemblyai.com/v2"

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["authorization"] = key

    def check(self):
        r = self.s.get(self.BASE + "/transcript", params={"limit": 1}, timeout=30)
        if r.status_code >= 400:
            raise LLMError(f"AssemblyAI: {r.status_code} {r.text[:150]}")
        return "OK"

    def transcribe(self, audio_path, progress=None):
        with open(audio_path, "rb") as f:
            r = netutil.patient(lambda: (f.seek(0), self.s.post(self.BASE + "/upload", data=f, timeout=1800))[1], tries=6)
        if r.status_code >= 400:
            raise LLMError(f"AssemblyAI upload: {r.status_code} {r.text[:150]}")
        upload_url = r.json()["upload_url"]
        r = self.s.post(self.BASE + "/transcript", json={"audio_url": upload_url, "language_detection": True,
                                                         "speech_models": ["universal-3-pro", "universal-2"]}, timeout=60)
        if r.status_code == 400 and "model" in r.text.lower():
            r = self.s.post(self.BASE + "/transcript", json={"audio_url": upload_url, "language_detection": True}, timeout=60)
        if r.status_code >= 400:
            raise LLMError(f"AssemblyAI: {r.status_code} {r.text[:150]}")
        tid = r.json()["id"]
        t0 = time.time()
        while True:
            r = netutil.patient(lambda: self.s.get(self.BASE + f"/transcript/{tid}", timeout=60))
            if r.status_code >= 400:
                raise LLMError(f"AssemblyAI: {r.status_code} {r.text[:150]}")
            j = r.json()
            if progress:
                progress(f"распознаю речь (AssemblyAI): {j.get('status')}")
            if j.get("status") == "completed":
                break
            if j.get("status") == "error":
                raise LLMError(f"AssemblyAI: {j.get('error')}")
            if time.time() - t0 > 3600:
                raise LLMError("AssemblyAI: распознавание не завершилось за час")
            netutil.pause(4)
        words = [{"text": w["text"], "start": w["start"] / 1000.0, "end": w["end"] / 1000.0} for w in j.get("words") or []]
        return {"text": j.get("text") or "", "words": words}
