"""Обёртка над API yougen.io (v1): картинки Nano Banana и оживление картинок (Veo).

Ключ sf_... читается из config.json (поле yougen_api_key) и никогда не печатается.
"""
import base64
import mimetypes
import time

import requests
import netutil

BASE_URL = "https://yougen.io"

IMAGE_MODELS = {"nano-banana-2": "Nano Banana 2", "nano-banana-pro": "Nano Banana Pro", "nano-banana-2-lite": "Nano Banana 2 Lite"}


class YouGenError(RuntimeError):
    pass


class YouGen:
    def __init__(self, api_key: str, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = api_key
        self.timeout = timeout

    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            try:
                msg = r.json()
                msg = msg.get("error") or msg.get("detail") or msg.get("message") or str(msg)
            except ValueError:
                msg = r.text[:300]
            raise YouGenError(f"{r.status_code}: {str(msg)[:300]}")
        return r.json()

    # --- аккаунт ---
    def usage(self) -> dict:
        return self._check(self.s.get(BASE_URL + "/api/v1/usage", timeout=self.timeout))

    def models(self) -> list:
        j = self._check(self.s.get(BASE_URL + "/api/v1/models", timeout=self.timeout))
        return j.get("data", j) if isinstance(j, dict) else j

    # --- файлы ---
    def upload_file(self, path: str) -> str:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            data = f"data:{mime};base64," + base64.b64encode(f.read()).decode()
        j = self._check(self.s.post(BASE_URL + "/api/v1/files", json={"data": data}, timeout=300))
        return j["id"]

    # --- генерации ---
    def create(self, payload: dict) -> dict:
        return self._check(self.s.post(BASE_URL + "/api/v1/generations", json=payload, timeout=self.timeout))

    def status(self, gen_id: str) -> dict:
        return self._check(self.s.get(BASE_URL + f"/api/v1/generations/{gen_id}", timeout=self.timeout))

    def wait(self, gen_id: str, poll: float = 3.0, max_wait: float = 1800) -> dict:
        t0 = time.time()
        while True:
            st = netutil.patient(lambda: self.status(gen_id))
            if st.get("status") in ("succeeded", "failed"):
                return st
            if time.time() - t0 > max_wait:
                try:
                    self.s.delete(BASE_URL + f"/api/v1/generations/{gen_id}", timeout=self.timeout)
                except Exception:
                    pass
                raise TimeoutError(f"Задача {gen_id} не завершилась за {max_wait} с")
            netutil.pause(poll)

    def download(self, gen_id: str, dest_path: str, item: dict = None) -> str:
        """Скачать результат: сначала /content (ключом), при неудаче по download_url."""
        r = self.s.get(BASE_URL + f"/api/v1/generations/{gen_id}/content", timeout=600, stream=True)
        if r.status_code != 200:
            url = (item or {}).get("download_url")
            if not url:
                raise YouGenError(f"не удалось скачать результат: {r.status_code} {r.text[:200]}")
            r = self.s.get(url, timeout=600, stream=True)
            r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return dest_path

    def run(self, payload: dict, dest_path: str, poll: float = 3.0, max_wait: float = 1800) -> str:
        acc = self.create(payload)
        gen_id = acc["id"]
        st = self.wait(gen_id, poll=poll, max_wait=max_wait)
        if st.get("status") != "succeeded":
            raise YouGenError(f"генерация не удалась: {st.get('error_code') or ''} {st.get('error') or ''}".strip())
        results = st.get("results") or []
        return self.download(gen_id, dest_path, results[0] if results else None)

    def generate_image(self, prompt: str, dest_path: str, model: str = "nano-banana-2", aspect_ratio: str = "16:9",
                       inputs=None, max_wait: float = 900) -> str:
        payload = {"model": model, "prompt": prompt, "aspect_ratio": aspect_ratio}
        if inputs:
            payload["inputs"] = inputs
        return self.run(payload, dest_path, poll=3, max_wait=max_wait)

    def generate_video_from_image(self, prompt: str, image_path: str, dest_path: str, model: str = "veo-3.1-fast",
                                  aspect_ratio: str = "16:9", max_wait: float = 1800) -> str:
        file_id = self.upload_file(image_path)
        payload = {"model": model, "prompt": prompt, "inputs": [file_id], "aspect_ratio": aspect_ratio}
        return self.run(payload, dest_path, poll=5, max_wait=max_wait)
