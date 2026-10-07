"""Обёртка над Secret Slider API v2 (secretslider.com) — по официальной документации из кабинета.

Особенности: 1 кредит = 1 картинка; на ключ одновременно только ОДНА активная задача, зато в одну задачу можно
положить до 100 промптов. Поэтому программа отправляет все свои сцены одним пакетом. С сентября 2026 /generate
принимает только JSON-тело (prompts, num_images, aspect_ratio); form-data отдаёт 400 invalid_json.
Ключ X-API-Key из config.json (secretslider_api_key), не печатается.
"""
import io
import json
import os
import re
import time
import zipfile
from urllib.parse import urljoin

import requests
import netutil

BASE_URL = "https://secretslider.com/api/v2"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def find_urls(results):
    """Ссылки на картинки из ответа задачи в любом виде: список строк, список словарей, вложенные поля.
    Порядок сохраняется — он соответствует порядку промптов."""
    out = []

    def walk(o):
        if isinstance(o, str):
            if o.startswith(("http://", "https://", "/")) and (o.lower().split("?")[0].endswith(IMG_EXT) or "/media/" in o or "image" in o.lower()):
                out.append(o)
        elif isinstance(o, dict):
            for k in ("url", "image_url", "file_url", "download_url", "src"):
                if isinstance(o.get(k), str):
                    walk(o[k])
                    return
            for v in o.values():
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)

    if isinstance(results, dict):
        for k in ("image_urls", "images", "urls", "files", "items"):
            if results.get(k):
                walk(results[k])
                if out:
                    return out
    walk(results)
    return out


def shape(o, depth=0):
    """Короткое описание структуры ответа для журнала (без самих ссылок)."""
    if isinstance(o, dict):
        return "{" + ", ".join(f"{k}: {shape(v, depth + 1)}" for k, v in list(o.items())[:12]) + "}" if depth < 3 else "{…}"
    if isinstance(o, list):
        return f"[{len(o)}× {shape(o[0], depth + 1)}]" if o else "[]"
    return type(o).__name__


class SliderError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(f"{status}: {message}")
        self.status = status


class SecretSlider:
    def __init__(self, api_key, timeout=60):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = api_key
        self.timeout = timeout

    def _check(self, r):
        if r.status_code >= 400:
            try:
                j = r.json()
                msg = j.get("message") or j.get("message_ru") or j.get("code") or j.get("error") or str(j)
                retry = j.get("retry_after")
            except ValueError:
                msg, retry = r.text[:200], None
            e = SliderError(r.status_code, str(msg)[:250])
            e.retry_after = retry
            raise e
        return r.json()

    def balance(self):
        return self._check(self.s.get(BASE_URL + "/balance", timeout=self.timeout))

    def active(self):
        return self._check(self.s.get(BASE_URL + "/tasks/active", timeout=self.timeout))

    def wait_for_slot(self, max_wait=1800, progress=None):
        """На ключ разрешена одна активная задача — ждём, пока предыдущая закончится."""
        t0 = time.time()
        while True:
            try:
                a = self.active()
            except SliderError:
                return
            if not a.get("active_count"):
                return
            eta = max(10, int((a.get("active_tasks") or [{}])[0].get("estimated_wait_seconds") or 30))
            if progress:
                progress(f"secretslider: жду свободный слот (~{eta} с)")
            if time.time() - t0 > max_wait:
                raise SliderError(429, "слот генерации занят слишком долго")
            time.sleep(min(eta, 60))

    def create_batch(self, prompts, aspect_ratio="16:9"):
        """Одна задача на много промптов (multipart/visual, чтобы задать 16:9). Возвращает task_id."""
        # сервис перевёл /generate на JSON-тело; старый form-data оставлен запасным на случай отката на их стороне
        body = {"prompts": list(prompts), "num_images": 1, "aspect_ratio": aspect_ratio}
        data = {"mode": "visual", "prompts": json.dumps(list(prompts), ensure_ascii=False), "num_images": "1", "aspect_ratio": aspect_ratio}
        for attempt in range(4):
            r = self.s.post(BASE_URL + "/generate", json=body, timeout=self.timeout)
            if r.status_code in (400, 415, 422):
                old = self.s.post(BASE_URL + "/generate", data=data, timeout=self.timeout)
                if old.status_code < 400 or old.status_code == 429:
                    r = old
            if r.status_code == 429:
                try:
                    wait = int(r.json().get("retry_after") or 60)
                except ValueError:
                    wait = 60
                time.sleep(min(max(wait, 10), 180))
                continue
            if r.status_code in (503, 502) and attempt == 0:
                # визуальный режим недоступен — пробуем JSON без соотношения сторон
                r = self.s.post(BASE_URL + "/generate", json={"prompts": list(prompts), "style": "photorealism", "num_images": 1}, timeout=self.timeout)
            j = self._check(r)
            if not j.get("task_id"):
                raise SliderError(500, j.get("message_ru") or j.get("message") or "задача не создана")
            return j["task_id"]
        raise SliderError(429, "не удалось занять слот генерации")

    def wait(self, task_id, max_wait=3600, progress=None):
        t0 = time.time()
        while True:
            j = self._check(netutil.patient(lambda: self.s.get(BASE_URL + f"/task/{task_id}", timeout=self.timeout)))
            st = (j.get("status") or "").lower()
            if progress:
                progress(f"secretslider: {j.get('progress', 0)}% {j.get('progress_text') or ''}".strip())
            if st in ("completed", "partial_success", "success", "succeeded", "done", "finished", "complete", "partial"):
                return j
            if int(j.get("progress") or 0) >= 100 and find_urls(j.get("results")):
                return j
            if st in ("failed", "canceled", "cancelled"):
                raise SliderError(500, j.get("error_message") or f"задача {st}")
            if time.time() - t0 > max_wait:
                try:
                    self.s.post(BASE_URL + f"/task/{task_id}/cancel", timeout=self.timeout)
                except Exception:
                    pass
                raise TimeoutError(f"Задача {task_id} не завершилась за {max_wait} с")
            netutil.pause(5)

    def from_zip(self, task_id, dest_paths):
        """Запасной путь: архив задачи. Файлы внутри идут по порядку промптов (сортируем по числам в имени)."""
        r = self.s.get(BASE_URL + f"/task/{task_id}/download", timeout=900)
        r.raise_for_status()
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = [n for n in z.namelist() if n.lower().endswith(IMG_EXT)]
        names.sort(key=lambda n: [int(x) for x in re.findall(r"\d+", os.path.basename(n))] or [0])
        out = []
        for i, dest in enumerate(dest_paths):
            if i < len(names):
                with open(dest, "wb") as f:
                    f.write(z.read(names[i]))
                out.append(dest)
            else:
                out.append(None)
        return out

    def generate_batch(self, prompts, dest_paths, progress=None, log=None):
        """prompts[i] -> dest_paths[i]. Возвращает список путей, которые удалось скачать (None для пропущенных)."""
        say = log or (lambda m: None)
        self.wait_for_slot(progress=progress)
        task_id = self.create_batch(prompts)
        say(f"secretslider: задача {task_id} создана, промптов {len(prompts)}")
        j = self.wait(task_id, progress=progress)
        urls = [urljoin(BASE_URL + "/", u) if u.startswith("/") else u for u in find_urls(j.get("results"))]
        say(f"secretslider: задача {task_id} {j.get('status')}, ссылок {len(urls)} из {len(prompts)}")
        if len(urls) < len(prompts):
            say(f"secretslider: ответ {shape(j)}")
        if not urls:
            try:
                out = self.from_zip(task_id, dest_paths)
                say(f"secretslider: взял из архива {sum(1 for x in out if x)} картинок")
                return out
            except Exception as e:
                say(f"secretslider: архив не скачался: {str(e)[:200]}")
                return [None] * len(dest_paths)
        out = []
        for i, dest in enumerate(dest_paths):
            url = urls[i] if i < len(urls) else None
            if not url:
                out.append(None)
                continue
            try:
                r = self.s.get(url, timeout=600, stream=True)
                r.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        f.write(chunk)
                out.append(dest)
            except Exception as e:
                say(f"secretslider: картинка {i + 1} не скачалась: {str(e)[:160]}")
                out.append(None)
        return out
