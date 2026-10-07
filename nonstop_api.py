"""Обёртка над API veononstop.org (v1). Пока используем только генерацию картинок (Banana).

Ключ читается из config.json (поле veononstop_api_key) и никогда не печатается.
"""
import base64
import time

import requests
import netutil

BASE_URL = "https://veononstop.org/api/v1"

# model_key -> человеческое имя
MODELS = {
    "GEM_PIX_2": "Banana Pro",
    "NARWHAL": "Banana 2",
    "HARBOR_SEAL": "Banana 2 Lite",
}


class NonStopError(RuntimeError):
    pass


class NonStop:
    def __init__(self, api_key: str, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = api_key
        self.s.headers["Content-Type"] = "application/json"
        self.timeout = timeout

    def _check(self, r: requests.Response) -> dict:
        try:
            j = r.json()
        except ValueError:
            raise NonStopError(f"{r.status_code}: {r.text[:300]}")
        if r.status_code >= 400 or not j.get("success", True):
            raise NonStopError(f"{r.status_code}: {j.get('error') or r.text[:300]}")
        return j.get("data", j)

    # --- аккаунт ---
    def account_info(self) -> dict:
        return self._check(self.s.get(BASE_URL + "/account/info", timeout=self.timeout))

    def usage(self) -> dict:
        return self._check(self.s.get(BASE_URL + "/account/usage", timeout=self.timeout))

    # --- картинки ---
    def create_image(self, prompt: str, model_key: str = "GEM_PIX_2", aspect_ratio: str = "16:9",
                     num_images: int = 1, reference_images=None, use_all_ref_images=False) -> str:
        payload = {"prompt": prompt, "model_key": model_key, "aspect_ratio": aspect_ratio, "num_images": num_images}
        if reference_images:
            payload["reference_images"] = reference_images
            if use_all_ref_images:
                payload["use_all_ref_images"] = True
        d = self._check(self.s.post(BASE_URL + "/image/banana/generate", json=payload, timeout=self.timeout))
        return d["task_id"]

    def status(self, task_id: str) -> dict:
        return self._check(self.s.get(BASE_URL + f"/video/status/{task_id}", timeout=self.timeout))

    def wait(self, task_id: str, poll: float = 5.0, max_wait: float = 1800) -> dict:
        t0 = time.time()
        while True:
            st = netutil.patient(lambda: self.status(task_id))
            if st.get("status") in ("completed", "failed"):
                return st
            if time.time() - t0 > max_wait:
                try:
                    self.s.post(BASE_URL + f"/video/cancel/{task_id}", timeout=self.timeout)
                except Exception:
                    pass
                raise TimeoutError(f"Задача {task_id} не завершилась за {max_wait} с")
            netutil.pause(poll)

    def download_image(self, item: dict, dest_path: str) -> str:
        """item — элемент массива images из статуса. fifeUrl может быть ссылкой или data URI."""
        url = item.get("fifeUrl") or item.get("url") or item.get("servingBaseUri")
        if not url:
            raise NonStopError("В результате нет ссылки на картинку")
        if url.startswith("data:"):
            data = base64.b64decode(url.split(",", 1)[1])
        else:
            r = requests.get(url, timeout=300)
            r.raise_for_status()
            data = r.content
        with open(dest_path, "wb") as f:
            f.write(data)
        return dest_path

    def generate_image(self, prompt: str, dest_path: str, model_key: str = "GEM_PIX_2",
                       aspect_ratio: str = "16:9", max_wait: float = 1800,
                       reference_images=None, use_all_ref_images=False) -> str:
        """Сгенерировать одну картинку и сохранить. Бросает NonStopError при неудаче."""
        task_id = self.create_image(prompt, model_key=model_key, aspect_ratio=aspect_ratio,
                                    reference_images=reference_images, use_all_ref_images=use_all_ref_images)
        st = self.wait(task_id, max_wait=max_wait)
        if st.get("status") != "completed":
            raise NonStopError(f"генерация не удалась: {st.get('error')}")
        images = st.get("images") or []
        if not images:
            raise NonStopError("задача завершена, но картинок нет")
        return self.download_image(images[0], dest_path)

    # --- оживление картинки в видео (Veo, 8 секунд) ---
    def create_video_from_image(self, prompt: str, image_path: str, aspect_ratio: str = "16:9") -> str:
        with open(image_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        mime = "image/png" if image_path.lower().endswith(".png") else "image/jpeg"
        payload = {"prompt": prompt, "image_base64": b64, "mime_type": mime, "aspect_ratio": aspect_ratio, "count": 1}
        d = self._check(self.s.post(BASE_URL + "/video/image-to-video", json=payload, timeout=self.timeout))
        return d["task_id"]

    def download_video(self, task_id: str, dest_path: str, item: dict = None) -> str:
        """Скачать mp4: сначала через /video/download, при неудаче по прямой ссылке fifeUrl."""
        r = self.s.get(BASE_URL + f"/video/download/{task_id}", timeout=600, stream=True)
        if r.status_code == 200 and "video" in (r.headers.get("Content-Type") or ""):
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            return dest_path
        url = (item or {}).get("fifeUrl") or (item or {}).get("servingBaseUri")
        if not url:
            raise NonStopError(f"не удалось скачать видео: {r.status_code} {r.text[:200]}")
        r = requests.get(url, timeout=600, stream=True)
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return dest_path

    def generate_video_from_image(self, prompt: str, image_path: str, dest_path: str,
                                  aspect_ratio: str = "16:9", max_wait: float = 1800) -> str:
        task_id = self.create_video_from_image(prompt, image_path, aspect_ratio=aspect_ratio)
        st = self.wait(task_id, poll=10, max_wait=max_wait)
        if st.get("status") != "completed":
            raise NonStopError(f"оживление не удалось: {st.get('error')}")
        videos = st.get("videos") or []
        if not videos:
            raise NonStopError("задача завершена, но видео нет")
        return self.download_video(task_id, dest_path, videos[0])
