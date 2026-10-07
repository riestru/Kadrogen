"""Озвучка текста (TTS) тремя сервисами с одинаковым интерфейсом:

    client = make_tts(name, key)      # name: voicegen | voicer | lumean
    client.check()                    # -> строка "OK ..." или исключение
    client.voices()                   # -> [{"id","name","lang","gender"}] (может быть пустым)
    client.synthesize(text, voice_id, out_mp3, progress=None)  # блокирующий вызов, пишет mp3

Ключи никогда не печатаются.
"""
import time

import requests
import netutil

TTS_SERVICES = {
    "voicegen": {"name": "VoiceGen", "url": "https://qw1voicegencore.pro", "hint": "ElevenLabs через Telegram-бота VoiceGen, ключ из бота",
                 "placeholder": "ключ VoiceGen"},
    "voicer": {"name": "Voicer", "url": "https://voicer.mat3u.com/docs", "hint": "ElevenLabs, токен из бота @mat3u", "placeholder": "токен Voicer"},
    "lumean": {"name": "Lumean", "url": "https://lumean.app/developers/docs", "hint": "ElevenLabs через Lumean, ключ с правами orders/templates/voices",
               "placeholder": "ключ Lumean"},
    "edge": {"name": "Edge (бесплатно)", "url": "", "hint": "Голоса Microsoft Edge, без ключа и без оплаты", "placeholder": ""},
}
KEYLESS = ("edge",)  # сервисы, которым ключ не нужен


class TTSError(RuntimeError):
    pass


def _err(r: requests.Response, service: str):
    try:
        j = r.json()
        msg = j.get("message") or j.get("detail") or j.get("error") or str(j)
    except ValueError:
        msg = r.text[:200]
    raise TTSError(f"{service}: {r.status_code} {str(msg)[:200]}")


# ======================= VoiceGen (qw1voicegencore.pro) =======================

class VoiceGen:
    BASE = "https://qw1voicegencore.pro"

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {key}"

    def check(self):
        r = self.s.get(self.BASE + "/api/v1/client/me", timeout=30)
        if r.status_code >= 400:
            _err(r, "VoiceGen")
        j = r.json()
        sub = "подписка активна" if j.get("has_active_subscription") else "подписка НЕ активна"
        return f"OK · {sub} · тариф {j.get('current_plan')}"

    def voices(self):
        r = self.s.get(self.BASE + "/api/v1/client/voice-catalog", timeout=60)
        if r.status_code >= 400:
            _err(r, "VoiceGen")
        j = r.json()
        items = j if isinstance(j, list) else (j.get("voices") or j.get("items") or j.get("public") or [])
        if isinstance(j, dict) and not items:
            for k in ("public_voices", "cloned_voices", "personal", "catalog"):
                items += j.get(k) or []
        out = []
        for v in items:
            if isinstance(v, dict) and v.get("id"):
                out.append({"id": v["id"], "name": v.get("display") or v.get("name") or v["id"],
                            "lang": v.get("language", ""), "gender": v.get("gender", ""), "cloned": bool(v.get("is_cloned"))})
        return out

    def synthesize(self, text, voice_id, out_path, progress=None):
        payload = {"text": text, "filename": "voiceover.mp3", "voice_engine": "elevenLabsV3",
                   "settings_preset": "standard", "thread_count": 5}
        if voice_id:
            payload["voice_id"] = voice_id
        r = self.s.post(self.BASE + "/api/v1/client/tasks", json=payload, timeout=60)
        if r.status_code >= 400:
            _err(r, "VoiceGen")
        task_id = r.json()["task"]["task_id"]
        t0 = time.time()
        while True:
            r = netutil.patient(lambda: self.s.get(self.BASE + f"/api/v1/client/tasks/{task_id}", timeout=30))
            if r.status_code >= 400:
                _err(r, "VoiceGen")
            t = r.json().get("task") or r.json()
            st = t.get("status")
            if progress:
                progress(f"озвучка VoiceGen: {st}, {t.get('progress', 0)}%")
            if st == "done":
                break
            if st in ("error", "cancelled"):
                raise TTSError(f"VoiceGen: задача {st}: {t.get('error') or t.get('message') or ''}")
            if time.time() - t0 > 3600:
                raise TTSError("VoiceGen: озвучка не завершилась за час")
            netutil.pause(4)
        r = self.s.get(self.BASE + f"/api/v1/client/tasks/{task_id}/download", timeout=600, allow_redirects=True, stream=True)
        if r.status_code >= 400:
            _err(r, "VoiceGen")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return out_path


# ======================= Voicer (voicer.mat3u.com) =======================

class Voicer:
    BASE = "https://voicer.mat3u.com/api/v1"
    DEFAULT_VOICE = "AB9XsbSA4eLG12t2myjN"

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["Authorization"] = f"Bearer {key}"

    def check(self):
        r = self.s.get(self.BASE + "/user/stats", timeout=30)
        if r.status_code >= 400:
            _err(r, "Voicer")
        j = r.json()
        sub = j.get("subscription_type")
        rem = j.get("remaining_characters")
        return f"OK · подписка {sub}" + (f" · осталось символов {rem}" if rem else "")

    def voices(self):
        try:
            r = self.s.get(self.BASE + "/voices", timeout=60)
            if r.status_code >= 400:
                return []
            j = r.json()
            items = j if isinstance(j, list) else (j.get("voices") or j.get("items") or [])
            return [{"id": v.get("voice_id") or v.get("id"), "name": v.get("name", ""), "lang": v.get("language", ""),
                     "gender": (v.get("labels") or {}).get("gender", "") if isinstance(v.get("labels"), dict) else v.get("gender", "")}
                    for v in items if isinstance(v, dict) and (v.get("voice_id") or v.get("id"))]
        except Exception:
            return []

    def synthesize(self, text, voice_id, out_path, progress=None):
        payload = {"text": text, "voice_id": voice_id or self.DEFAULT_VOICE, "model_id": "eleven_v3",
                   "split_type": "smart", "split_output": False}
        r = self.s.post(self.BASE + "/voice/synthesize", json=payload, timeout=60)
        if r.status_code >= 400:
            _err(r, "Voicer")
        task_id = r.json()["task_id"]
        t0 = time.time()
        while True:
            r = netutil.patient(lambda: self.s.get(self.BASE + f"/voice/status/{task_id}", timeout=30))
            if r.status_code >= 400:
                _err(r, "Voicer")
            j = r.json()
            st = j.get("status")
            if progress:
                progress(f"озвучка Voicer: {st}, {j.get('progress', 0)}%")
            if st == "completed":
                break
            if st in ("failed", "cancelled", "censored", "pending_confirmation"):
                raise TTSError(f"Voicer: задача {st}: {j.get('error_message') or ''}")
            if time.time() - t0 > 3600:
                raise TTSError("Voicer: озвучка не завершилась за час")
            netutil.pause(4)
        r = self.s.get(self.BASE + f"/voice/download/{task_id}", timeout=600, allow_redirects=True, stream=True)
        if r.status_code >= 400:
            _err(r, "Voicer")
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return out_path


# ======================= Lumean (api.lumean.app) =======================

class Lumean:
    BASE = "https://api.lumean.app/api/public"
    TEMPLATE_NAME = "VideoGen TTS"

    def __init__(self, key):
        self.s = requests.Session()
        self.s.headers["X-API-KEY"] = key
        self.s.headers["Content-Type"] = "application/json"
        try:
            from requests.adapters import HTTPAdapter
            from urllib3.util.retry import Retry
            self.s.mount("https://", HTTPAdapter(max_retries=Retry(total=4, connect=4, read=2, backoff_factor=3,
                                                                    status_forcelist=(502, 503, 504), allowed_methods=frozenset(["GET"]))))
        except Exception:
            pass

    def _j(self, r, what="Lumean"):
        if r.status_code >= 400:
            _err(r, what)
        j = r.json()
        return j.get("data", j)

    def check(self):
        r = self.s.get(self.BASE + "/user", timeout=30)
        if r.status_code in (403, 404):  # нет права profile.read — пробуем voices.read
            r2 = self.s.get(self.BASE + "/voices/elevenlabs/library", params={"page": 0, "page_size": 1}, timeout=30)
            if r2.status_code == 403:
                raise TTSError("Lumean: у ключа нет прав. В кабинете Lumean создайте новый API-ключ с набором прав «full» "
                               "(нужны orders.write, templates.write, voices.read, orders.download).")
            self._j(r2)
            return "OK · ключ принят (без доступа к профилю)"
        d = self._j(r)
        name = d.get("name") or d.get("email") or ""
        return f"OK · {name}".rstrip(" ·")

    def voices(self):
        try:
            d = self._j(self.s.get(self.BASE + "/voices/elevenlabs/library", params={"page": 0, "page_size": 30}, timeout=60))
            items = d.get("voices") or []
            return [{"id": v.get("voice_id"), "name": v.get("name", ""), "lang": (v.get("language") or ""),
                     "gender": v.get("gender", "")} for v in items if v.get("voice_id")]
        except Exception:
            return []

    MODELS = ("eleven_v4", "eleven_v3")  # новейшая модель первой; если сервис её не принимает — откат

    def _template(self, voice_id, language):
        """Находим или создаём шаблон TTS с нужным голосом."""
        existing = self._j(self.s.get(self.BASE + "/templates", timeout=60)) or []
        last = None
        for model_id in self.MODELS:
            for t in existing:
                cfg = (t.get("config") or {}).get("tts_settings") or {}
                if t.get("name") == self.TEMPLATE_NAME and cfg.get("voice_id") == voice_id and cfg.get("model_id") == model_id:
                    return t["id"]
            body = {"service_key": "elevenlabs", "name": self.TEMPLATE_NAME, "config": {"tts_settings": {
                "mode": "mode_v1", "model_id": model_id, "voice_id": voice_id,
                "advanced_voice_settings": False,
                "voice_settings": {"stability": 0.5, "speed": 1.0}}}}
            if language:
                body["config"]["tts_settings"]["language_code"] = language
            try:
                return self._j(self.s.post(self.BASE + "/templates", json=body, timeout=60))["id"]
            except TTSError as e:
                last = e
                if "422" not in str(e):
                    raise
        raise last

    def synthesize(self, text, voice_id, out_path, progress=None, language=None):
        if not voice_id:
            vs = self.voices()
            if not vs:
                raise TTSError("Lumean: не выбран голос")
            voice_id = vs[0]["id"]
        tid = self._template(voice_id, language)
        d = self._j(self.s.post(self.BASE + "/orders", json={"template_id": tid, "input_text": text, "name": "VideoGen voiceover"}, timeout=60))
        order_id = d["id"]
        t0 = time.time()
        retried, retried_at, reorders = 0, 0.0, 0
        while True:
            d = self._j(netutil.patient(lambda: self.s.get(self.BASE + f"/orders/{order_id}", timeout=30)))
            if reorders < 1 and time.time() - t0 > 1500 and (d.get("status") or "") not in ("partially_completed", "completed", "result_delivered"):
                # заказ висит в очереди 25 минут — такое у Lumean бывает, отправляем тот же текст заново
                reorders += 1
                if progress:
                    progress("озвучка Lumean: заказ завис в очереди, отправляю заново")
                d = self._j(self.s.post(self.BASE + "/orders", json={"template_id": tid, "input_text": text, "name": "VideoGen voiceover"}, timeout=60))
                order_id, t0 = d["id"], time.time()
                continue
            st = d.get("status")
            if progress:
                progress(f"озвучка Lumean: {st}")
            if st in ("completed", "result_delivered"):
                break
            if st in ("failed", "compensated", "cancelled"):
                raise TTSError(f"Lumean: заказ {st}: {(d.get('result') or {}).get('user_message') or ''}")
            if st == "partially_completed" and retried < 3 and time.time() - retried_at > 60:
                retried, retried_at = retried + 1, time.time()
                self.s.post(self.BASE + f"/orders/{order_id}/items/retry-failed", timeout=30)
            if time.time() - t0 > 3600:
                raise TTSError("Lumean: озвучка не завершилась за час")
            netutil.pause(4)
        files = (d.get("result") or {}).get("files") or []
        if not files:
            raise TTSError("Lumean: заказ готов, но файла нет")
        url = self._j(self.s.post(self.BASE + "/storage/url", json={"path": files[0]}, timeout=30))["url"]
        r = requests.get(url, timeout=600, stream=True)
        r.raise_for_status()
        with open(out_path, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
        return out_path


# ======================= Edge (бесплатные голоса Microsoft) =======================

class EdgeTTS:
    """Озвучка голосами Microsoft Edge через библиотеку edge-tts: ключ не нужен. Медленно (около 0.7 от реального времени),
    поэтому длинный текст режется на куски по предложениям и синтезируется в несколько потоков, потом склеивается ffmpeg."""
    DEFAULT_VOICE = "ru-RU-DmitryNeural"
    CHUNK = 1000
    WORKERS = 4

    def __init__(self, key=None):
        import edge_tts
        self.edge = edge_tts

    def check(self):
        vs = self.voices()
        return f"OK · без ключа · голосов {len(vs)}, по-русски: Dmitry, Svetlana"

    def voices(self):
        import asyncio
        raw = asyncio.run(self.edge.list_voices())
        out = []
        for v in raw:
            loc = v.get("Locale", "")
            name = v.get("FriendlyName", v["ShortName"]).replace("Microsoft ", "").replace(" Online (Natural)", "")
            out.append({"id": v["ShortName"], "name": name, "lang": loc, "gender": v.get("Gender", "")})
        out.sort(key=lambda x: (0 if x["lang"].startswith("ru") else 1, x["lang"], x["name"]))  # русские первыми
        return out

    def _split(self, text):
        import re
        sents = re.split(r"(?<=[.!?…])\s+", text.strip())
        parts, cur = [], ""
        for snt in sents:
            while len(snt) > self.CHUNK:
                cut = snt.rfind(" ", 0, self.CHUNK)
                cut = cut if cut > self.CHUNK // 2 else self.CHUNK
                if cur:
                    parts.append(cur)
                    cur = ""
                parts.append(snt[:cut].strip())
                snt = snt[cut:].strip()
            if len(cur) + len(snt) + 1 > self.CHUNK and cur:
                parts.append(cur)
                cur = snt
            else:
                cur = (cur + " " + snt).strip()
        if cur:
            parts.append(cur)
        return [x for x in parts if x]

    def synthesize(self, text, voice_id, out_path, progress=None, language=None):
        import asyncio
        import os
        import subprocess
        import tempfile
        from fastgen_api import app_dir
        voice = (voice_id or "").strip() or self.DEFAULT_VOICE
        parts = self._split(text)
        tmpdir = tempfile.mkdtemp(prefix="edge_")
        files = [os.path.join(tmpdir, f"p{k:03d}.mp3") for k in range(len(parts))]
        done = [0]

        async def one(k, sem):
            async with sem:
                for attempt in range(3):
                    try:
                        await self.edge.Communicate(parts[k], voice).save(files[k])
                        if os.path.getsize(files[k]) > 500:
                            break
                    except Exception as e:
                        if attempt == 2:
                            raise TTSError(f"Edge: не удалось озвучить часть {k + 1}: {str(e)[:120]}")
                        await asyncio.sleep(3 * (attempt + 1))
                done[0] += 1
                if progress:
                    progress(f"озвучка Edge: {done[0]} из {len(parts)}")

        async def run():
            sem = asyncio.Semaphore(self.WORKERS)
            await asyncio.gather(*(one(k, sem) for k in range(len(parts))))

        asyncio.run(run())
        if len(files) == 1:
            os.replace(files[0], out_path)
        else:
            lst = os.path.join(tmpdir, "list.txt")
            with open(lst, "w", encoding="utf-8") as f:
                for x in files:
                    f.write("file '" + x.replace("\\", "/").replace("'", "'\\''") + "'\n")
            ff = os.path.join(app_dir(), "ffmpeg", "ffmpeg.exe")
            ff = ff if os.path.exists(ff) else "ffmpeg"
            r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c", "copy", "-f", "mp3", out_path],
                               capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if r.returncode != 0:
                raise TTSError("Edge: не удалось склеить части: " + r.stderr.decode(errors="replace")[:200])
        for x in files:
            try:
                os.remove(x)
            except OSError:
                pass
        return out_path


class FastGenTTS:
    """Голоса Gemini по ключу fast-gen: 10 «запросных» кредитов за фразу (кредиты картинок не тратятся),
    лимит 600 запросных кредитов в час. Текст режется на куски по предложениям, куски озвучиваются параллельно."""
    OP = "aistudio_gemini_3.1_flash_tts_preview_speech_generate"
    CHUNK = 3000
    WORKERS = 3

    def __init__(self, key):
        from fastgen_api import FastGen
        self.api = FastGen(key)

    def check(self):
        return f"OK · голосов Gemini: {len(self.voices())}"

    def voices(self):
        raw = self.api.get("/api/v6/voices")
        items = raw if isinstance(raw, list) else (raw.get("voices") or raw.get("items") or [])
        out = []
        for v in items:
            if not v.get("id"):
                continue
            tone = v.get("tone_ru") or v.get("tone") or ""
            out.append({"id": v["id"], "name": f"{v.get('name') or v['id']}{' · ' + tone if tone else ''}", "lang": "", "gender": v.get("gender", "")})
        return out

    def _one(self, text, voice):
        payload = {"operation": self.OP, "prompt": text}
        if voice:
            payload["options"] = {"voice": voice}
        for attempt in range(12):
            try:
                st = self.api.run(payload, poll=3, max_wait=900)
            except requests.HTTPError as e:
                if "429" in str(e):
                    netutil.pause(60)  # часовой лимит запросов — ждём минуту и повторяем
                    continue
                raise
            if st.get("status") == "succeeded" and st.get("results"):
                return st
            err = f"{st.get('error_code')} {st.get('error')}"
            if any(x in err.lower() for x in ("overloaded", "try again", "rate", "limit", "generation.failed")):
                netutil.pause(min(300, 30 * (attempt + 1)))
                continue
            raise TTSError("fast-gen озвучка: " + err[:200])
        raise TTSError("fast-gen озвучка: сервис не принял запрос после 12 попыток")

    def synthesize(self, text, voice_id, out_path, progress=None, language=None):
        import os
        import subprocess
        import tempfile
        import concurrent.futures as cf
        from fastgen_api import app_dir
        voice = (voice_id or "").strip()
        parts = EdgeTTS._split(self, text)
        tmpdir = tempfile.mkdtemp(prefix="fgtts_")
        files = [os.path.join(tmpdir, f"p{k:03d}.wav") for k in range(len(parts))]
        done = [0]

        def work(k):
            st = self._one(parts[k], voice)
            self.api.download_result(st["results"][0], files[k])
            done[0] += 1
            if progress:
                progress(f"озвучка fast-gen (Gemini): {done[0]} из {len(parts)}")

        with cf.ThreadPoolExecutor(max_workers=self.WORKERS) as ex:
            list(ex.map(work, range(len(parts))))
        lst = os.path.join(tmpdir, "list.txt")
        with open(lst, "w", encoding="utf-8") as f:
            for x in files:
                f.write("file '" + x.replace("\\", "/").replace("'", "'\\''") + "'\n")
        ff = os.path.join(app_dir(), "ffmpeg", "ffmpeg.exe")
        ff = ff if os.path.exists(ff) else "ffmpeg"
        r = subprocess.run([ff, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-c:a", "libmp3lame", "-b:a", "192k", "-f", "mp3", out_path],
                           capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise TTSError("fast-gen озвучка: не удалось склеить части: " + r.stderr.decode(errors="replace")[:200])
        for x in files:
            try:
                os.remove(x)
            except OSError:
                pass
        return out_path


def make_tts(name, key):
    return {"voicegen": VoiceGen, "voicer": Voicer, "lumean": Lumean, "edge": EdgeTTS, "fastgen": FastGenTTS}[name](key)
