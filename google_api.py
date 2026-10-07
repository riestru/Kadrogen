# -*- coding: utf-8 -*-
"""Официальный Gemini API от Google (ключ из Google AI Studio): картинки Imagen 4 и Nano Banana, видео Veo.
Имена моделей у Google меняются, поэтому при первом обращении список моделей читается с сервера
и подбирается ближайшее подходящее имя."""
import os
import re
import time
import base64
import threading

import requests
import netutil

BASE = "https://generativelanguage.googleapis.com/v1beta"

# Кандидаты в порядке предпочтения; берётся первая модель из списка сервера, чьё имя начинается с кандидата
CANDIDATES = {
    "imagen-fast": ["imagen-4.0-fast-generate", "imagen-4-fast", "imagen-4.0-generate", "imagen-3.0-generate"],
    "imagen": ["imagen-4.0-generate", "imagen-4.0-ultra-generate", "imagen-4.0-fast-generate", "imagen-3.0-generate"],
    "nb2": ["gemini-3.1-flash-image", "gemini-3-flash-image", "gemini-2.5-flash-image", "gemini-3-pro-image"],
    "nbpro": ["gemini-3.1-pro-image", "gemini-3-pro-image", "gemini-2.5-flash-image"],
    "veo": ["veo-3.1-fast-generate", "veo-3.1-generate", "veo-3.0-fast-generate", "veo-3.0-generate", "veo-2.0-generate"],
}
PRETTY = {"imagen-fast": "Imagen 4 Fast", "imagen": "Imagen 4", "nb2": "Nano Banana 2", "nbpro": "Nano Banana Pro", "veo": "Veo"}


class GoogleError(Exception):
    def __init__(self, status, msg):
        super().__init__(f"{status}: {msg}")
        self.status = status
        self.msg = msg


class GoogleAI:
    def __init__(self, key):
        self.key = key
        self.s = requests.Session()
        self.s.headers.update({"x-goog-api-key": key})
        self._models = None
        self._lock = threading.Lock()

    # ---------- модели ----------
    def _check(self, r):
        if r.status_code >= 400:
            try:
                msg = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                msg = r.text[:300]
            raise GoogleError(r.status_code, msg)
        return r.json()

    def models(self):
        with self._lock:
            if self._models is None:
                names, token = [], None
                for _ in range(10):
                    params = {"pageSize": 200}
                    if token:
                        params["pageToken"] = token
                    j = self._check(self.s.get(BASE + "/models", params=params, timeout=60))
                    names += [m["name"].split("/", 1)[-1] for m in j.get("models", [])]
                    token = j.get("nextPageToken")
                    if not token:
                        break
                self._models = names
            return self._models

    def resolve(self, kind):
        """Настоящее имя модели для 'imagen-fast' / 'imagen' / 'nb2' / 'nbpro' / 'veo' или None."""
        names = self.models()
        for cand in CANDIDATES[kind]:
            hits = [n for n in names if n.startswith(cand) and "tts" not in n and "audio" not in n]
            # без preview лучше, чем preview; более свежий номер — выше
            hits.sort(key=lambda n: ("preview" in n, tuple(-int(x) for x in re.findall(r"\d+", n))))
            if hits:
                return hits[0]
        return None

    def check(self):
        found = {k: self.resolve(k) for k in ("imagen-fast", "nb2", "nbpro", "veo")}
        parts = [f"{PRETTY[k]}: {v}" for k, v in found.items() if v]
        if not parts:
            raise GoogleError(200, "ключ принят, но моделей картинок в нём нет — включите биллинг в Google AI Studio")
        return "OK · " + " · ".join(parts)

    # ---------- картинки ----------
    @staticmethod
    def _b64(path):
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode()

    def generate_image(self, prompt, out_path, kind="imagen-fast", reference_paths=None, aspect_ratio="16:9"):
        """Картинка выбранного типа. Imagen референсы не принимает; для Nano Banana референсы идут картинками в запросе."""
        model = self.resolve(kind)
        if not model:
            raise GoogleError(404, f"у ключа нет модели {PRETTY.get(kind, kind)}")
        if kind.startswith("imagen"):
            data = self._imagen(model, prompt, aspect_ratio)
        else:
            data = self._gemini_image(model, prompt, reference_paths or [], aspect_ratio)
        with open(out_path, "wb") as f:
            f.write(data)
        return model

    def _imagen(self, model, prompt, aspect_ratio):
        body = {"instances": [{"prompt": prompt}],
                "parameters": {"sampleCount": 1, "aspectRatio": aspect_ratio, "personGeneration": "allow_adult"}}
        j = self._post_retry(f"{BASE}/models/{model}:predict", body)
        preds = j.get("predictions") or []
        if not preds or not preds[0].get("bytesBase64Encoded"):
            raise GoogleError(422, "Imagen не вернул картинку (возможно, промпт отклонён фильтром)")
        return base64.b64decode(preds[0]["bytesBase64Encoded"])

    def _gemini_image(self, model, prompt, refs, aspect_ratio):
        parts = [{"text": prompt}]
        for p in refs[:10]:
            parts.append({"inline_data": {"mime_type": "image/png", "data": self._b64(p)}})
        body = {"contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": aspect_ratio}}}
        try:
            j = self._post_retry(f"{BASE}/models/{model}:generateContent", body)
        except GoogleError as e:
            if e.status != 400 or "imageConfig" not in e.msg and "aspect" not in e.msg.lower():
                raise
            body["generationConfig"] = {"responseModalities": ["IMAGE", "TEXT"]}
            j = self._post_retry(f"{BASE}/models/{model}:generateContent", body)
        for c in j.get("candidates") or []:
            for p in (c.get("content") or {}).get("parts") or []:
                d = p.get("inlineData") or p.get("inline_data")
                if d and d.get("data"):
                    return base64.b64decode(d["data"])
        reason = ((j.get("candidates") or [{}])[0].get("finishReason")) or (j.get("promptFeedback") or {}).get("blockReason") or "пусто"
        raise GoogleError(422, f"модель не вернула картинку ({reason})")

    def _post_retry(self, url, body, tries=4, timeout=300):
        last = None
        for attempt in range(tries):
            try:
                r = self.s.post(url, json=body, timeout=timeout)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(5 * (attempt + 1))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                try:
                    last = r.json().get("error", {}).get("message", r.text[:200])
                except Exception:
                    last = r.text[:200]
                time.sleep((20 if r.status_code == 429 else 8) * (attempt + 1))
                continue
            return self._check(r)
        raise GoogleError(429, f"Google не ответил после {tries} попыток: {last}")

    # ---------- видео ----------
    def generate_video_from_image(self, prompt, img_path, out_path, aspect_ratio="16:9", duration=8, max_wait=1800, progress=None):
        model = self.resolve("veo")
        if not model:
            raise GoogleError(404, "у ключа нет модели Veo")
        inst = {"prompt": prompt, "image": {"bytesBase64Encoded": self._b64(img_path), "mimeType": "image/png"}}
        params = {"aspectRatio": aspect_ratio, "resolution": "1080p", "durationSeconds": duration, "personGeneration": "allow_adult"}
        try:
            j = self._post_retry(f"{BASE}/models/{model}:predictLongRunning", {"instances": [inst], "parameters": params}, tries=2)
        except GoogleError as e:
            if e.status != 400:
                raise
            params.pop("resolution", None)
            params.pop("personGeneration", None)
            j = self._post_retry(f"{BASE}/models/{model}:predictLongRunning", {"instances": [inst], "parameters": params}, tries=2)
        name = j.get("name")
        if not name:
            raise GoogleError(500, "Veo не вернул операцию")
        t0 = time.time()
        while time.time() - t0 < max_wait:
            netutil.pause(10)
            op = self._check(netutil.patient(lambda: self.s.get(f"{BASE}/{name}", timeout=60)))
            if op.get("error"):
                raise GoogleError(500, str(op["error"])[:300])
            if op.get("done"):
                resp = op.get("response") or {}
                samples = (resp.get("generateVideoResponse") or resp).get("generatedSamples") or []
                if not samples:
                    filt = (resp.get("generateVideoResponse") or {}).get("raiMediaFilteredReasons")
                    raise GoogleError(422, f"Veo не вернул видео ({filt or 'фильтр'})")
                uri = samples[0]["video"]["uri"]
                with self.s.get(uri, stream=True, timeout=600) as r:
                    if r.status_code >= 400:
                        raise GoogleError(r.status_code, "не скачалось видео")
                    with open(out_path, "wb") as f:
                        for chunk in r.iter_content(1 << 16):
                            f.write(chunk)
                return model
            if progress:
                progress(f"Veo работает… {int(time.time() - t0)} с")
        raise GoogleError(504, "Veo не успел за отведённое время")
