"""Общие помощники для клиентов сервисов.

pause(sec)   — сон, который раз в секунду проверяет кнопку «Стоп» (раньше «Стоп» ждал конца опроса сервиса — до десятков минут).
patient(fn)  — терпеливый опрос: секундный обрыв сети или 5xx во время ожидания результата не бросает уже оплаченную задачу.
"""
import time

import requests

CANCEL_CHECK = None   # pipeline ставит сюда check_cancel


def check_cancel():
    if CANCEL_CHECK:
        CANCEL_CHECK()


def pause(sec):
    end = time.time() + max(0.0, float(sec))
    while True:
        check_cancel()
        left = end - time.time()
        if left <= 0:
            return
        time.sleep(min(1.0, left))


def patient(fn, tries=10):
    """Вызвать fn() (обычно один GET статуса). Сетевые ошибки, таймауты, 429 и 5xx — подождать и повторить, до tries раз."""
    last = None
    for attempt in range(tries):
        check_cancel()
        try:
            r = fn()
            code = getattr(r, "status_code", None)
            if isinstance(code, int) and (code >= 500 or code == 429) and attempt < tries - 1:
                last = f"HTTP {code}"
                pause(min(30, 3 * (attempt + 1)))
                continue
            return r
        except requests.HTTPError as e:
            code = getattr(getattr(e, "response", None), "status_code", None)
            if code is not None and code < 500 and code != 429:
                raise
            last = e
        except requests.RequestException as e:
            last = e
        if attempt == tries - 1:
            break
        pause(min(30, 3 * (attempt + 1)))
    if isinstance(last, Exception):
        raise last
    raise requests.ConnectionError(f"сервис не отвечает: {last}")
