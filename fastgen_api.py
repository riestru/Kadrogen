"""Небольшая обёртка над API fast-gen.ai (v6).

Ключи читаются из config.json рядом с программой. Ключи никогда не печатаются.
"""
import base64
import json
import mimetypes
import os
import sys
import time

import requests
import netutil

BASE_URL = "https://api.fast-gen.ai"
STORAGE_URL = "https://storage.fast-gen.ai"


def app_dir() -> str:
    """Папка, где лежит программа (работает и для .exe, и для .py)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


_DATA_DIR = None


def data_dir() -> str:
    """Папка для конфига, заданий и результатов. Обычно = папка программы; если она не пишется
    (запуск прямо из архива, Program Files), берём папку VideoGen в LOCALAPPDATA."""
    global _DATA_DIR
    if _DATA_DIR:
        return _DATA_DIR
    d = app_dir()
    try:
        probe = os.path.join(d, ".write_test")
        with open(probe, "w") as f:
            f.write("ok")
        os.remove(probe)
        _DATA_DIR = d
    except OSError:
        d = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "VideoGen")
        os.makedirs(d, exist_ok=True)
        _DATA_DIR = d
    return _DATA_DIR


def load_config() -> dict:
    path = os.path.join(data_dir(), "config.json")
    if not os.path.exists(path):
        raise SystemExit(f"Не найден файл {path}. Создайте его и заполните ключи.")
    with open(path, encoding="utf-8") as f:
        cfg = json.load(f)
    if not cfg.get("fastgen_api_key"):
        raise SystemExit("В config.json пустой fastgen_api_key. Заполните его.")
    return cfg


class FastGen:
    def __init__(self, api_key: str, timeout: int = 60):
        self.s = requests.Session()
        self.s.headers["X-API-Key"] = api_key
        self.timeout = timeout

    # --- простые GET ---
    def get(self, path: str, **params):
        r = self.s.get(BASE_URL + path, params=params or None, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def usage(self):
        return self.get("/api/v6/usage")

    def capabilities(self, **params):
        return self.get("/api/v6/capabilities", **params)

    # --- генерации ---
    def create(self, payload: dict) -> dict:
        """Запустить операцию. Возвращает ответ сервера (с id).
        Бросает requests.HTTPError с телом ответа в тексте при ошибке."""
        r = self.s.post(BASE_URL + "/api/v6/generations", json=payload, timeout=self.timeout)
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code}: {r.text[:500]}", response=r)
        return r.json()

    def status(self, gen_id: str) -> dict:
        r = self.s.get(BASE_URL + f"/api/v6/generations/{gen_id}", timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def wait(self, gen_id: str, poll: float = 3.0, max_wait: float = 900) -> dict:
        """Ждать завершения. Возвращает финальный статус (succeeded/failed)."""
        t0 = time.time()
        try:
            while True:
                st = netutil.patient(lambda: self.status(gen_id))  # обрыв сети не бросает оплаченную задачу
                if st.get("status") in ("succeeded", "failed"):
                    return st
                if time.time() - t0 > max_wait:
                    raise TimeoutError(f"Операция {gen_id} не завершилась за {max_wait} с")
                netutil.pause(poll)  # сон с проверкой кнопки «Стоп»
        except BaseException as e:
            if type(e).__name__ == "Cancelled":
                self.cancel(gen_id)  # «Стоп»: снимаем задачу на сервере, чтобы кредиты не списались зря
            raise

    def cancel(self, gen_id: str):
        try:
            self.s.delete(BASE_URL + f"/api/v6/generations/{gen_id}", timeout=10)
        except Exception:
            pass

    def run(self, payload: dict, poll: float = 3.0, max_wait: float = 900) -> dict:
        acc = self.create(payload)
        return self.wait(acc["id"], poll=poll, max_wait=max_wait)

    # --- скачивание результата ---
    def download_result(self, item: dict, dest_path: str) -> str:
        """Сохранить результат (картинку/файл) на диск. Возвращает путь."""
        if item.get("download_url"):
            r = self.s.get(item["download_url"], timeout=300)
            r.raise_for_status()
            data = r.content
        elif item.get("data"):
            data = base64.b64decode(item["data"].split(",", 1)[1])
        else:
            raise ValueError("В результате нет ни download_url, ни data")
        with open(dest_path, "wb") as f:
            f.write(data)
        return dest_path

    # --- загрузка файлов в хранилище ---
    def upload_file(self, path: str) -> str:
        """Загрузить файл в storage.fast-gen.ai, вернуть 32-символьный id."""
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            r = self.s.post(STORAGE_URL + "/v2/upload", files={"file": (os.path.basename(path), f, mime)}, timeout=600)
        if r.status_code >= 400:
            raise requests.HTTPError(f"{r.status_code}: {r.text[:500]}", response=r)
        j = r.json()
        for key in ("id", "file_id", "storage_id"):
            if key in j:
                return j[key]
        raise ValueError(f"Не понял ответ хранилища: {str(j)[:300]}")


def file_to_data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode()
