"""Обёртка над API RoyalTechno (api.royaltechno.cc): картинки Nano Banana и оживление картинок (Veo 3.1).

Соблюдает правила сервиса: опрос раз в 5 с (картинки) / 10 с (видео) с ETag, Idempotency-Key на задачу,
пауза Retry-After при 429, остановка на 400/401/402/403. Ключ rt_live_... из config.json (royaltechno_api_key),
никогда не печатается.
"""
import base64
import mimetypes
import time
import uuid

import requests
import netutil

BASE_URL = "https://api.royaltechno.cc"
IMAGE_MODELS = {"nano-banana-2": "Nano Banana 2", "nano-banana-pro": "Nano Banana Pro"}
VIDEO_MODEL = "veo-3.1"


class RoyalError(RuntimeError):
    def __init__(self, status, code, message):
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code = status, code


class RoyalTechno:
    def __init__(self, api_key: str, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {api_key}"
        self.timeout = timeout
        self.poll_image, self.poll_video = 5, 10

    def _check(self, r: requests.Response):
        if r.status_code >= 400:
            try:
                e = r.json().get("error", {})
                raise RoyalError(r.status_code, e.get("code", "error"), e.get("message", r.text[:200]))
            except ValueError:
                raise RoyalError(r.status_code, "error", r.text[:200])
        return r.json()

    # --- аккаунт ---
    def account(self) -> dict:
        a = self._check(self.s.get(BASE_URL + "/v1/account", timeout=self.timeout))
        poll = (a.get("limits") or {}).get("recommended_poll_sec") or {}
        self.poll_image = max(5, int(poll.get("image", 5)))
        self.poll_video = max(10, int(poll.get("video", 10)))
        return a

    # --- задачи ---
    def create(self, model: str, inp: dict) -> dict:
        """POST /v1/jobs с повторами по правилам: 429 → ждать Retry-After (до 6 раз), 503 → повтор, остальное — стоп."""
        key = str(uuid.uuid4())
        for attempt in range(6):
            r = self.s.post(BASE_URL + "/v1/jobs", json={"model": model, "input": inp},
                            headers={"Idempotency-Key": key}, timeout=self.timeout)
            if r.status_code in (429, 503):
                try:
                    code = r.json().get("error", {}).get("code", "")
                except ValueError:
                    code = ""
                wait = float(r.headers.get("Retry-After") or min(60, 2 ** attempt))
                if code == "queue_full":
                    wait = max(wait, 60)
                time.sleep(wait)
                continue
            return self._check(r)
        raise RoyalError(429, "rate_limit_exceeded", "сервис перегружен, попробуйте позже")

    def wait(self, job_id: str, poll: float, max_wait: float = 1800) -> dict:
        t0 = time.time()
        etag, last = None, None
        while True:
            headers = {"If-None-Match": etag} if etag else {}
            r = netutil.patient(lambda: self.s.get(BASE_URL + f"/v1/jobs/{job_id}", headers=headers, timeout=self.timeout))
            if r.status_code == 200:
                last = r.json()
                etag = r.headers.get("ETag")
                if last.get("status") in ("succeeded", "failed", "canceled"):
                    return last
            elif r.status_code != 304:
                self._check(r)
            if time.time() - t0 > max_wait:
                try:
                    self.s.post(BASE_URL + f"/v1/jobs/{job_id}/cancel", timeout=self.timeout)
                except Exception:
                    pass
                raise TimeoutError(f"Задача {job_id} не завершилась за {max_wait} с")
            netutil.pause(poll)

    @staticmethod
    def _download(url: str, dest_path: str):
        r = requests.get(url, timeout=600, stream=True)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return dest_path

    @staticmethod
    def data_uri(path: str) -> str:
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as f:
            return f"data:{mime};base64," + base64.b64encode(f.read()).decode()

    def generate_image(self, prompt: str, dest_path: str, model: str = "nano-banana-2",
                       reference_paths=None, max_wait: float = 900) -> str:
        inp = {"prompt": prompt[:4000], "aspect_ratio": "landscape"}
        if reference_paths:
            inp["reference_image_urls"] = [self.data_uri(p) for p in reference_paths[:10]]
        job = self.create(model, inp)
        st = self.wait(job["id"], self.poll_image, max_wait)
        if st.get("status") != "succeeded":
            e = st.get("error") or {}
            raise RoyalError(500, e.get("code", "failed"), e.get("message", "генерация не удалась"))
        out = st.get("output") or {}
        url = out.get("url") or ((out.get("images") or [{}])[0].get("url"))
        if not url:
            raise RoyalError(500, "no_output", "задача завершена, но картинки нет")
        return self._download(url, dest_path)

    def generate_video_from_image(self, prompt: str, image_path: str, dest_path: str,
                                  model: str = VIDEO_MODEL, max_wait: float = 2400) -> str:
        inp = {"prompt": prompt[:4000], "start_image_url": self.data_uri(image_path)}
        job = self.create(model, inp)
        st = self.wait(job["id"], self.poll_video, max_wait)
        if st.get("status") != "succeeded":
            e = st.get("error") or {}
            raise RoyalError(500, e.get("code", "failed"), e.get("message", "оживление не удалось"))
        out = st.get("output") or {}
        url = out.get("url") or ((out.get("videos") or [{}])[0].get("url"))
        if not url:
            raise RoyalError(500, "no_output", "задача завершена, но видео нет")
        return self._download(url, dest_path)
