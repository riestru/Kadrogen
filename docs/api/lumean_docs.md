# Lumean Public API — справочник для LLM

> Этот файл — самодостаточная спецификация публичного API Lumean для языковых моделей.
> Отдайте его нейросети целиком: в нём есть авторизация, соглашения, все эндпоинты с точными
> полями, enum'ы, схемы ответов, real-time (WebSocket) и сквозные рабочие примеры.
> Все идентификаторы, имена полей, enum-значения и JSON приведены **дословно** — используйте их
> буква-в-букву. Пояснения на русском, технические имена — в оригинале.

Машиночитаемые спецификации (истина о схемах, дополняют этот файл):
`GET /docs/public-openapi.json` (OpenAPI 3.1, REST) и `GET /docs/public-asyncapi.json` (AsyncAPI 2.6, WebSocket).
Английский вариант REST-спеки — `GET /docs/public-openapi.en.json` (то же дерево, переведённые описания).

---

## 0. Что это за API

Lumean — SaaS для генерации медиа ИИ: **озвучка текста (TTS)**, **генерация и редактирование
изображений**, **звуковые эффекты (SFX)**, **музыка**, **клонирование голоса**. Работа строится
вокруг **заказов** (`order`): вы отправляете задачу, сервер разбивает её на чанки, обрабатывает
асинхронно воркерами и отдаёт файлы-результаты.

Публичный API (`/api/public/*`) предназначен для программных интеграций **по API-ключу** (заголовок
`X-API-KEY`). Это отдельный контур от фронтового JWT-API. Ключ создаётся пользователем в личном
кабинете (веб), у него набор **прав** (permissions) и **лимиты**. Здесь описан ТОЛЬКО публичный
контур по ключу.

**Ключевые факты, которые часто понимают неверно (прочитайте до генерации запросов):**

1. **TTS-голос задаётся только через шаблон**, а не полем заказа. Нет `orders.voice_id`.
   Порядок: положить `voice_id` в `config.tts_settings.voice_id` шаблона → создать заказ по этому
   шаблону. См. §11 (Рецепты).
2. **Текст для TTS передаётся в top-level поле `input_text`** запроса `POST /orders` (сырая строка).
   НЕ кладите текст TTS в `task_data`. А вот для template-less SFX/music промпт идёт в `task_data`.
3. **У заказов НЕТ update/delete.** Доступны только `index`, `store`, `show`, плюс действия
   `cancel`/`retry`. `PUT/PATCH/DELETE /orders/{order}` вернут 405/404.
4. **Два независимых лимита:** rate-limit по числу запросов (429, без `Retry-After`) и токен-квота
   по объёму генерации (429, `reason: token_quota_exceeded`, **с** `Retry-After`). См. §6.
5. **Пагинация с разными базами:** внешние каталоги голосов (`voices/elevenlabs/library`, `voices/heygen`)
   считают `page` **с 0**; обычные списки (`orders`, `templates/browse`) — `current_page` **с 1**.
6. **Субтитры и другие сервисные файлы лежат ОТДЕЛЬНО** — в `result.service_files[]`, не в
   `result.files[]`. Оба поля — плоские массивы строк-путей (напр. `.../output/final/service/subtitles.srt`),
   генерируется только для TTS. Скачивается тем же `POST /storage/url`. См. §7.10.
7. **Сбой чанка чинится на уровне чанка, не заказа.** У чанка бывают статусы `failed` (тех. ошибка)
   и `policy_flagged` (контент отклонён политикой провайдера). Частично готовый заказ
   (`partially_completed`) доводят до `completed` так: один вызов
   `POST /orders/{order}/items/retry-failed` забирает все `failed` разом, а `policy_flagged`
   добивают поштучно `POST /orders/{order}/items/{item}/retry` с **исправленным** `text` (повтор с
   тем же текстом бессмыслен). Order-level `retry` для этого не годится — он создаёт новый заказ.
   См. §7.11.
8. **Любые квадратные скобки в тексте озвучки вырезаются** — это разметка для диктора, а не
   список ключевых слов. `[1]`, `[sic]`, `[00:12]` не прозвучат (и не будут оплачены). А вот
   `(ремарка)`, `<тег>`, `*звёздочки*` — прозвучат и тарифицируются. См. §8.3.
9. **Тонкие настройки голоса включаются флагом.** `similarity_boost`, `style` и
   `use_speaker_boost` действуют только при `tts_settings.advanced_voice_settings: true`
   (по умолчанию `false`). Без флага они молча не влияют на звук — ошибки не будет. См. §8.2.

---

## 1. TL;DR — путь от нуля до готового TTS-заказа

Предполагается, что у вас уже есть API-ключ с правами `orders.write`, `templates.write`,
`voices.read`, `orders.download`, `billing.read`. **Внимание:** готовый пресет `automation` НЕ
включает `templates.write` (только `templates.read`) — для этого сценария (создание шаблона)
берите пресет `full` либо добавьте `templates.write` к `automation` вручную при создании ключа.

```bash
BASE=https://api.lumean.app/api/public
KEY=<ваш_api_ключ>

# 1. Найти voice_id (например, из публичной библиотеки ElevenLabs)
curl -s "$BASE/voices/elevenlabs/library?page=0&page_size=20" -H "X-API-KEY: $KEY"

# 2. Создать шаблон TTS с этим voice_id
curl -s -X POST "$BASE/templates" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" -d '{
  "service_key": "elevenlabs",
  "name": "My TTS",
  "config": {
    "tts_settings": {
      "mode": "mode_v1",
      "model_id": "eleven_multilingual_v2",
      "voice_id": "<VOICE_ID_ИЗ_ШАГА_1>",
      "advanced_voice_settings": true,
      "voice_settings": { "stability": 0.5, "similarity_boost": 0.75, "use_speaker_boost": true, "speed": 1.0 }
    }
  }
}'
# → data.id = <TEMPLATE_UUID>

# 3. Создать заказ по шаблону (текст в input_text)
curl -s -X POST "$BASE/orders" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" -d '{
  "template_id": "<TEMPLATE_UUID>",
  "input_text": "Привет! Это тестовая озвучка через Lumean API."
}'
# → data.id = <ORDER_UUID>, data.status = "created"

# 4. Дождаться готовности: опрос статуса (или WebSocket, см. §10)
curl -s "$BASE/orders/<ORDER_UUID>" -H "X-API-KEY: $KEY"
# ждём data.status = "completed" (или "partially_completed")

# 5. Забрать файл-результат: путь берём из data.result.files[] (элемент — строка-путь)
curl -s -X POST "$BASE/storage/url" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "path": "<строка_из_result.files[]>" }'
# → data.url = временная ссылка; качаем обычным GET
```

В шаге 2 `tts_settings.mode` — обязателен, а `tts_settings.language_code` — опциональный ISO-639-1
код (не показан выше для краткости минимального примера); подробнее — §8.2.

---

## 2. Базовый URL и окружения

| Что | Значение (production) |
| --- | --- |
| REST base | `https://api.lumean.app/api/public` |
| WebSocket host / port / scheme | `ws.lumean.app` / `443` / `wss` |
| WS app key (публичный) | `7c4dd881d8116ac76fe89aff71d8c2ff` |
| WS auth endpoint | `POST https://api.lumean.app/api/public/broadcasting/auth` |
| OpenAPI (REST) | `https://api.lumean.app/docs/public-openapi.json` |
| OpenAPI (REST, EN) | `https://api.lumean.app/docs/public-openapi.en.json` |
| AsyncAPI (WS) | `https://api.lumean.app/docs/public-asyncapi.json` |

Все запросы — HTTPS. Тело — JSON (`Content-Type: application/json`), кроме загрузки файлов
(`multipart/form-data`). Ответы — JSON в UTF-8.

---

## 3. Аутентификация — заголовок `X-API-KEY`

Каждый запрос обязан нести заголовок:

```
X-API-KEY: <ваш_ключ>
```

Ключ хешируется (sha256) и резолвится с кэшем ~1 час. При успехе сервер действует от имени
владельца ключа.

### Необязательный заголовок `X-Timezone`

```
X-Timezone: Europe/Moscow
```

Все моменты времени в ответах — ISO-8601 с **явным оффсетом**
(`2026-08-10T15:34:56+03:00`). По умолчанию это UTC; прислав `X-Timezone`, вы
получите те же моменты, записанные в вашей зоне. Принимается только имя зоны
IANA — голый оффсет (`+03:00`) отвергается, потому что не знает про переход на
летнее время.

Ответ сообщает **фактически применённую** зону тем же заголовком: прислав мусор,
вы увидите `X-Timezone: UTC` и поймёте причину. Календарные даты (`Y-m-d`:
границы периодов действия) по зоне не конвертируются — у них её нет.

**Ошибки авторизации (HTTP 401), тело `{ "success": false, "message": <строка>, "data": null }`:**

| Причина | message-ключ |
| --- | --- |
| Нет заголовка | `auth.api_key.missing` |
| Неизвестный / неактивный ключ / удалённый или заблокированный пользователь | `auth.api_key.invalid` |
| Ключ истёк (`expires_at` в прошлом) | `auth.api_key.expired` |

### Права (permissions)

Каждый эндпоинт требует конкретное право. Ключ без нужного права → **HTTP 403**
`{ "success": false, "message": <auth.api_key.forbidden>, "data": null }`.

| Право | Открывает |
| --- | --- |
| `orders.read` | Чтение заказов, чанков, WebSocket-подписка на заказы |
| `orders.write` | Создание/отмена/повтор заказов, retry/regenerate чанков |
| `orders.download` | `POST /storage/url` — ссылки на файлы-результаты |
| `templates.read` | Чтение шаблонов, `browse`, `config-options` |
| `templates.write` | Создание/изменение/удаление шаблонов |
| `voices.read` | Все каталоги голосов (LumVoice + ElevenLabs + HeyGen) |
| `billing.read` | `usage`, `subscriptions`, (при включённом кошельке) `wallets`/`ledger-transactions` |
| `profile.read` | `GET /user` (профиль) |
| `*` (wildcard) | Проходит любую проверку прав |

**Пресеты прав** (набор, который обычно назначают ключу):
- `read_only` = все read-права: `orders.read`, `templates.read`, `voices.read`, `billing.read`, `profile.read`, `orders.download`.
- `automation` = `orders.read`, `orders.write`, `orders.download`, `templates.read`, `voices.read`, `billing.read`, `profile.read`.
- `full` = все права.

> Можно перечислить несколько прав через запятую в требовании — достаточно любого. `orders.download`
> — это отдельное право для скачивания (не покрывается `orders.read`).

---

## 4. Соглашения

### Конверт ответа

**Успех** (200/201):
```json
{ "success": true, "message": "<человекочитаемо>", "data": <payload> }
```

**Ошибка домена/инфраструктуры** (400, 404, 429 rate-limit, 500, 503 и пр.):
```json
{ "success": false, "message": "<причина>" }
```
Ключ `errors` в публичном API на этих кодах не используется — не путайте с 422 ниже, где он
обязателен. Некоторые доменные 4xx (напр. 402/409 биллинга, 429 квоты) несут дополнительные
машиночитаемые поля поверх `success`/`message` — см. §12.

**Ошибка валидации** (422) — стандартный формат Laravel, **без** ключа `success`:
```json
{ "message": "<итоговое>", "errors": { "field.name": ["текст ошибки", "..."] } }
```

### Пагинация

Пагинированные списки кладут в `data`:
```json
{ "items": [ ... ], "current_page": 1, "last_page": 5, "per_page": 20, "total": 93 }
```
Параметры запроса: `page` (с 1), `per_page` (обычно 1..100). **Исключение:** внешние каталоги
голосов используют свою обёртку `{ voices, total, page, page_size, has_more }` с `page` **от 0**.

### Типы идентификаторов

| Ресурс | Тип `id` |
| --- | --- |
| Order | **UUID** (строка) |
| Template | **UUID** (строка) |
| User, ApiKey, Subscription, Service | **integer** |
| LumVoice | id голоса (в пути `voices/{voice}`) |
| Внешний голос (ElevenLabs/HeyGen) | строковый `voice_id` провайдера |

Даты — ISO-8601 (`2026-07-08T12:34:56+00:00`). Локаль ответа — по `Accept-Language`/настройке
пользователя (влияет на локализованные `message`/labels).

---

## 5. Типы заказов (`task_type`)

Enum `task_type` (дословно, 9 значений): `tts`, `image`, `txt2img`, `remix`, `image_edit`,
`voice_clone`, `sfx`, `music`, `voice_search`. Это полный список enum'а (валидные значения фильтра
`GET /orders?task_type[]=…`), но **создаются** через публичный `POST /orders` не все — см. ниже.

Как создаётся каждый **доступный** тип на `POST /orders`:

| task_type | Способ | Что передавать | Файлов в `result.files[]` |
| --- | --- | --- | --- |
| `tts` | по шаблону (сервис `elevenlabs`) | `template_id` + `input_text` (текст озвучки) | 1 (склеенная дорожка) + субтитры в `service_files` |
| `sfx` | template-less | `task_type: "sfx"` + `task_data.text` (см. §8.1, рецепт §11-B) | **ровно 1** аудиофайл |
| `music` | template-less | `task_type: "music"` + `task_data.prompt` (см. §8.1, рецепт §11-C) | **`n_variants`** аудиофайлов (1..4) |
| `voice_search` | template-less | `task_type: "voice_search"` + аудио в `input_files` (multipart), опционально `task_data.top` | файлов нет — результат это список похожих голосов |

Правило: на `POST /orders` обязателен **один из** `template_id` **или** `task_type`. Шаблон
однозначно задаёт сервис и его настройки; `task_type` без шаблона работает **только** для
сервисов, у которых код сервиса совпадает с типом задачи — это **`sfx`**, **`music`** и
**`voice_search`**.

> **Через публичный `POST /orders` создаются `tts` (по шаблону), `sfx`, `music` и
> `voice_search` (template-less).** Значения `image`, `txt2img`, `remix`, `image_edit`,
> `voice_clone` валидны только как фильтр `GET /orders?task_type[]=…` (могут встречаться на
> ранее созданных заказах); создать заказ такого типа по API-ключу нельзя — сервиса с таким
> кодом нет, попытка даст **422** `service_not_found`. Не пытайтесь их создавать.

**`voice_search`** — подбор похожих голосов по вашему аудиофрагменту (не по текстовому
описанию). Аудио обязательно (без файла → отказ `source_file_required` до списания), тип
проверяется по содержимому файла, а не по расширению: картинка или архив дадут
`source_file_unsupported`. Запись длиннее 60 секунд обрезается — вектор диктора насыщается
раньше, признак обрезки приходит в результате. `task_data.top` (1..50, по умолчанию 10) задаёт
число кандидатов. Цена — одна ставка за заказ, от длительности записи не зависит.

### Статусы заказов (`order.status`)

Enum `OrderStatus` (дословно, 9 значений): `created`, `pending`, `in_progress`, `completed`,
`result_delivered`, `failed`, `compensated`, `cancelled`, `partially_completed`.

**Терминальные** (заказ достиг финального состояния, дальше не меняется): `completed`,
`result_delivered`, `failed`, `compensated`, `cancelled`. **Нетерминальные**: `created`, `pending`,
`in_progress`, `partially_completed` — последний не терминален специально: его можно доретраить в
`completed` через `POST /orders/{order}/items/retry-failed` (все `failed` разом) или
`POST /orders/{order}/items/{item}/retry` (поштучно, в т.ч. `policy_flagged`) — §7.2.

Строгий фильтр `status` в `GET /orders` (§7.1) принимает только эти значения — иное → 422.

---

## 6. Лимиты: rate-limit и токен-квота

Это **два разных** механизма, оба возвращают 429 — различайте по телу.

### 6.1 Rate-limit (число запросов)

Ограничение частоты по окнам ключа (`limits.requests.per_minute|per_hour|per_day`). Превышение →
**HTTP 429**, тело `{ "success": false, "message": "..." }`. Заголовка `Retry-After` (и
`X-RateLimit-*`) здесь **нет** — это не токен-квота (та несёт `Retry-After`, см. §6.2);
ориентируйтесь на настроенное окно ключа и делайте backoff самостоятельно.

> **Дефолт по частоте — «без лимита», а не 60/мин.** Ключ, созданный без явных request-окон,
> по умолчанию **не ограничивается по частоте** (`limits.requests.* = null` → сервер применяет
> `Limit::none()`). Числовой дефолт включается, только если оператор задал его в окружении
> (`API_KEY_DEFAULT_RATE_PER_MINUTE|_HOUR|_DAY`). Действующие окна вашего ключа — в личном
> кабинете (`limits.requests.*`). Реальный расход всё равно ограничен квотой аккаунта (см. §6.2),
> даже когда частота не лимитирована. Есть **третий** источник 429 — usage-лимит подписки
> (`reason: limit_exceeded`, §12), без `Retry-After`, и **четвёртый** — лимит одновременно
активных задач (`reason: concurrency_limit_exceeded`, §12), тоже без `Retry-After`. Последний
не про частоту и не про объём: он ограничивает, сколько ваших заказов может выполняться
ОДНОВРЕМЕННО. Пачка из сотен заказов, отправленная залпом, упрётся именно в него — лечится
не паузой между запросами, а ожиданием завершения уже запущенных. **Пятый** —
кулдаун после серии контентных блокировок (`reason: policy_cooldown_active`, §12);
он единственный из «заказных» 429 несёт `Retry-After` и `retry_after`/`retry_at`.
Лечится не паузой и не ожиданием чужих задач, а правкой текста: пять подряд
заблокированных политикой задач ограничивают создание новых на 5 минут, дальше 10 и 15.

### 6.2 Токен-квота (объём генерации)

Поверх rate-limit'а, но считает **токены** — учётную единицу объёма работы сервиса
(`токены = стоимость операции в токенах`; это НЕ деньги/LMC). Окна: `per_minute`, `per_hour`,
`per_day`, `per_month` из `limits.tokens.*` ключа. Ключ без токен-лимитов — без ограничения по токенам.

Проверяется **до** создания при `POST /orders` (по оценке), `POST /orders/{order}/retry`
(по сумме чанков оригинала) и `.../regenerate`. Превышение → **HTTP 429** + `Retry-After`:

```json
{
  "success": false,
  "message": "API key token quota exceeded",
  "reason": "token_quota_exceeded",
  "window": "per_minute",
  "limit": 10000,
  "used": 9800,
  "requested": 500,
  "reset_at": "2026-07-08T13:00:00+00:00",
  "retry_after": 42
}
```
`window` — первое нарушенное окно. При 429 по квоте заказ **не создаётся**.

### 6.3 PAYG-добор (квоты подписки не хватило)

Отдельная от лимитов развилка **оплаты**, а не отказа. Если заказ стоит больше, чем осталось токенов
в квоте подписки, и сервис разрешает pay-as-you-go (`Service.payg_access` ≠ `disabled`), сервер **не
создаёт заказ**, а возвращает **402 `payg_topup_required`** с ценой добора. Вы доплачиваете недостающую
часть из **LMC-баланса**, повторив тот же `POST /orders` с `confirm_payg_topup: true` и `quote_token`
из ответа. Это НЕ ошибка ключа и НЕ 429 — не ретрайте вслепую: покажите цену (`shortfall_lmc`) и
подтвердите явно. Полный сквозной флоу — **Рецепт G (§11)**; формы тел — §12. Если PAYG у сервиса
отключён, вместо 402 придёт `insufficient_allowance` (§12) — доплатить нельзя.

---

## 7. Справочник эндпоинтов

Формат: `МЕТОД путь` — `право` — параметры → ответ. Все пути относительны base `…/api/public`.

### 7.1 Orders

- **`GET /orders`** — `orders.read`
  Query: `page` (int≥1), `per_page` (int 1..100, деф. 20), `template_id` (uuid).
  `status` — строгий enum (невалидное значение → 422): `created`, `pending`, `in_progress`,
  `completed`, `result_delivered`, `failed`, `compensated`, `cancelled`, `partially_completed`
  (терминальные статусы — см. §5).
  `task_type` — строка или массив (`?task_type[]=tts&task_type[]=sfx`); мягкая валидация:
  неизвестный тип → пустой список, не ошибка.
  → `data` = пагинированный список `Order` (только ваши заказы).

- **`POST /orders`** — `orders.write` — см. §8.0 (тело). → 201, `data` = `Order`.

- **`GET /orders/{order}`** — `orders.read`. → `data` = `Order` (с `items`). 404/403.

- **`POST /orders/{order}/cancel`** — `orders.write`.
  Отменяет заказ в статусе `created`/`pending`/`partially_completed`, разблокирует средства,
  возвращает токен-квоту. → `data` = `Order`. Недопустимо → 400.

- **`POST /orders/{order}/retry`** — `orders.write`.
  Создаёт **новый** заказ с теми же параметрами (исходный должен быть `completed`/`result_delivered`,
  без `failed`-чанков). Оценка квоты = сумма `price_units` чанков оригинала.
  **Тело (оба поля опциональны, как у `POST /orders`):** `confirm_payg_topup` (boolean),
  `quote_token` (string). Retry проходит **тот же биллинг-flow, что и создание**: при нехватке
  квоты подписки → **402** `payg_topup_required` (с `quote_token`/`shortfall_*`), при устаревшем
  токене добора → **409** `quote_mismatch`, доменный отказ → **403/422**, токен-квота ключа → **429**
  (см. §8.0 и таблицу reason в §12). Повторите с `confirm_payg_topup: true` и актуальным `quote_token`.
  → 201, `data` = новый `Order`.

- **`POST /orders/chunks/preview`** — `orders.read` (preview не мутирует данные — логически
  чтение). **Бесплатно**, токен-квоту ключа не тратит. Dry-run разбиения текста на чанки и
  расчёта стоимости — БЕЗ создания заказа, тот же расчётный конвейер, что и `POST /orders`.
  Тело: `{ template_id, input_text, task_type?, previous_preview_id?, text_hash?,
  generation_mode? }`. Throttle: 30/мин (`public-chunks-preview`, отдельный от `orders.write`).
  → **200** `{ status: "ready", preview_id, summary, chunks, page }` — TTS-текст ≤ 50 000
  символов (и любой non-TTS). → **202** `{ status: "processing", preview_id }` — TTS-текст
  длиннее порога, расчёт в фоне, результат — WS-событие `.ready` на канале
  `private-chunks.preview.{userId}` (см. §10) либо ручной поллинг GET ниже. → 422 без
  `input_text`. Превышение пауз-капов НЕ роняет запрос (в отличие от `POST /orders`) —
  приходит `summary.caps_ok=false`. `preview_id` — sha256-хеш (64 hex, БЕЗ дефисов), детерминирован
  по (текст+шаблон+режим): повторный запрос того же текста отдаётся из кэша мгновенно, в т.ч. если
  тот же текст уже считался веб-сессией того же аккаунта (кэш общий по `user_id`, не по способу
  входа). Полный референс — `docs/PUBLIC_API_CHUNK_PREVIEW.md`.

- **`GET /orders/chunks/preview`** — `orders.read`. Preflight по хешу текста — без пересылки
  самого текста. Query: `template_id` (uuid), `text_hash` (sha256 сырого `input_text`,
  нижний регистр hex), `generation_mode?`. Throttle: 120/мин
  (`public-chunks-preview-read`). → 200 (ready)/202 (processing)/404 (нет в кэше — шлите POST).

- **`GET /orders/chunks/preview/{previewId}`** — `orders.read`. Страница уже посчитанных
  чанков (без пересчёта). `{previewId}` — sha256-хеш, НЕ UUID. Query: `page?` (с 1), `per_page?`
  (деф. 100, макс 500). Throttle: 120/мин (`public-chunks-preview-read`). → 200/202/404
  (протухло/упало — пере-отправьте POST).

### 7.2 Order Items (чанки заказа)

Все — ownership по заказу (право `orders.*`).

- **`GET /orders/{order}/items`** — `orders.read` → `data` = массив `OrderItem` (без пагинации).
- **`GET /orders/{order}/items/{item}`** — `orders.read` → `data` = `OrderItem`.
- **`GET /orders/{order}/items/{item}/text`** — `orders.read` →
  `data` = `{ "text": string|null, "length": int, "original_text_length": int }`.
- **`POST /orders/{order}/items/{item}/retry`** — `orders.write`.
  Тело: `{ "text"?: string(max 50000) }`. Повторяет **сбойный** чанк — `policy_flagged` **или**
  `failed` (заказ при этом `partially_completed`). Токен-квоту НЕ тратит (использует уже залоченный
  остаток `price_units − consumed_units`). Без `text` повторяется **тот же** текст; с `text` —
  заменяет текст чанка (длина ≤ `original_text_length`). Лимит попыток — 5. Недоступно для
  `sfx`/`music`. → 201, `data` = `OrderItem`. **При `policy_flagged` шлите исправленный `text` —
  см. плейбук §7.11.**
- **`POST /orders/{order}/items/retry-failed`** — `orders.write`. **Массовый повтор.**
  Тело **пустое**. Ставит на повтор все чанки заказа в статусе `failed` разом — вместо обхода
  `.../items/{item}/retry` в цикле. `policy_flagged` сюда **не входит**: тот же текст отклонят
  снова, такие чанки перезапускают поштучно с исправленным `text`.
  → **`202`** (не 201): план принят, работа идёт в фоне. `data`:

  ```json
  {
    "queued_count": 2,
    "queued": [
      { "item_id": "9a1e4f60-2b77-4a1c-8d33-5c0e1b7a9f21", "chunk_index": 3 },
      { "item_id": "7c4b18ad-6e02-4f9b-91a7-0d2c5e8b3f14", "chunk_index": 5 }
    ],
    "skipped_count": 1,
    "skipped": [
      { "item_id": "1f2d90c4-3a15-4e6b-b8d2-7f4a6c1e0b93", "chunk_index": 7,
        "reason": "policy_flagged",
        "reason_label": "Чанк отклонён политикой контента — перезапустите его отдельно с исправленным текстом." }
    ]
  }
  ```

  Синхронного результата у вызова нет **по построению** (повтор чанка — это чтение/запись S3 и
  транзакция на каждый элемент): дождитесь `GET .../items` либо слушайте WebSocket —
  событие `bulk_retry.completed` (§10). `reason` каждого пропуска — машинный код:

  | `reason` | Смысл | Что делать |
  | --- | --- | --- |
  | `superseded` | у чанка уже есть успешная версия (либо последняя версия — перегенерация) | ничего, чанк здоров |
  | `active_retry_exists` | повтор уже идёт (`pending`/`processing`) | дождаться |
  | `policy_flagged` | нужен исправленный текст | поштучный retry с новым `text` (§7.11) |
  | `max_attempts_reached` | исчерпан лимит попыток (5) | только новый заказ |
  | `insufficient_funds` | не осталось залоченного остатка (`price_units − consumed_units`) | только новый заказ |

  `reason_label` — тот же код словами, на языке ключа; **логику стройте по `reason`**, не по label.
  **Биллинг:** бесплатно, как и поштучный retry (переиспользует уже заблокированный остаток),
  токен-квоту ключа не расходует.
  **Отказы на уровне заказа** — телом `{ "success": false, "message": "…", "reason": "<машинный>" }`:
  `409` `stitch_in_progress` (по заказу идёт склейка), `422` `invalid_order_status` (заказ не
  `partially_completed`), `422` `generative_not_supported` (`sfx`/`music`), `422` `nothing_to_retry`
  (перезапускать нечего). Отдельными схемами в спеке они не описаны: коды рождаются в
  бизнес-исключении, а не в валидации запроса.

- **`POST /orders/{order}/items/{item}/regenerate`** — `orders.write`.
  Тело: `{ "text": string(required, max 50000) }` (пустой → 422; длина ≤ `original_text_length`).
  Платная перегенерация **`completed`**-чанка (не сбойного!), квота-precheck → 429. Недоступно для
  `sfx`/`music`. → 201, `data` = `OrderItem`.

### 7.3 Storage (скачивание результатов)

- **`POST /storage/url`** — `orders.download`.
  Тело:

  | Поле | Правило | Дефолт | Назначение |
  | --- | --- | --- | --- |
  | `path` | **required**, string | — | Путь файла как в `result.files[]`/`service_files[]` (строка целиком) |
  | `download` | sometimes, boolean | `false` | `true` — ссылка помечается как attachment: файл скачается под человекочитаемым именем заказа, а не откроется во вкладке |
  | `number_base` | sometimes, integer (`0` или `1`) | `1` | С какого числа нумеруются чанки в имени файла (`0` → первый чанк «0») |
  | `number_padding` | sometimes, integer (`1..4`) | `3` | Ширина номера чанка с ведущими нулями (`3` → «007») |

  → `data` = `{ "url": string }` (временная ссылка, качать обычным GET; TTL по умолчанию ~60 мин —
  после истечения запросите заново, сам файл живёт до ретенции заказа, см. §13).
  Коды: 200 / 403 (чужой файл) / 404 (файл не найден/истёк по ретенции) / 422 (`path` не передан) /
  500 (временная ошибка генерации, повторить).
  > `number_base`/`number_padding` держите одинаковыми с запросом ZIP-архива, иначе один и тот же
  > чанк называется по-разному поштучно и внутри архива.

### 7.4 Templates

- **`GET /templates`** — `templates.read`. Корневые шаблоны (`folder_id = null`).
  → `data` = массив `Template` (без пагинации).
- **`GET /templates/browse`** — `templates.read`.
  Query: `page` (≥1), `per_page` (1..100, деф. 15), `sort_by` (`name|created_at`, деф. `name`),
  `sort_order` (`asc|desc`, деф. `asc`), `folder_id` (uuid), `service` (code сервиса).
  → `data` = `{ items:[Template-lite], current_page, last_page, per_page, total, current_folder, breadcrumbs }`.
- **`GET /templates/config-options`** — `templates.read`.
  Query: `service` (required, code активного сервиса). → `data` = карта опций конфига сервиса
  (дескрипторы полей: `type`, `options`, `min`, `max`, `step`, `default`, `nullable`). Используйте
  её, чтобы узнать допустимые ключи/значения `config` для шаблона данного сервиса.
- **`POST /templates`** — `templates.write` — см. §8.2 (тело). → 201, `data` = `Template`.
- **`GET /templates/{template}`** — `templates.read` → `data` = `Template`.
- **`PUT|PATCH /templates/{template}`** — `templates.write`. Частичное обновление
  (`name`, `is_public`, `folder_id`, и `service_key`+`config` вместе). → `data` = `Template`.
- **`DELETE /templates/{template}`** — `templates.write` → `data: null`.

### 7.5 Voices — LumVoice (внутренние голоса)

Право `voices.read`. Элемент — `LumVoice` (см. §9).

- **`GET /voices`** — мои голоса. Query: `voice_status`
  (`pending_clone|cloning|ready|partially_ready|clone_failed|archived`; `partially_ready` — часть
  языков склонирована, часть упала, рабочие языки доступны), `publication_status`
  (`private|pending_review|public|rejected`), `language_code`, `search`, `sort_by`
  (`created_at|updated_at|display_name|last_used_at|usage_count_total`, деф. `created_at`),
  `sort_order` (`asc|desc`, деф. `desc`), `per_page` (1..100, деф. 20). → пагинированный список `LumVoice`.
- **`GET /voices/library`** — моя библиотека. Query: `available` (bool), `search`, `language_code`,
  `sort_by` (`added_at|created_at|nickname`), `sort_order`, `per_page`.
  → пагинированный список `{ id, voice_id, nickname, added_at, available, unavailable_reason, origin("own"|"library"), voice: LumVoice|null }`.
- **`GET /voices/public`** — публичный каталог. Query: `tag_ids` (array<int>), `language_code`,
  `gender` (`male|female|neutral`), `search`, `sort` (`popular|newest|name`, деф. `popular`),
  `per_page`. → пагинированный список `LumVoice` (только публичные, ready, разрешённые в заказах).
- **`GET /voices/tags`** → `data` = массив `{ id, code, name, description, color, sort_order }`.
- **`GET /voices/{voice}`** → `data` = `LumVoice` (свой / публичный / из библиотеки). 404/403.

### 7.6 Voices — внешние провайдеры (ElevenLabs, HeyGen)

Право `voices.read`, read-only. Обёртка — **внутри `data`**:
`data = { voices: [...], total, page, page_size, has_more }`, `page` **с 0**.
Отсюда берут `voice_id` для TTS-шаблонов.

- **`GET /voices/elevenlabs/library`** — публичная Voice Library ElevenLabs.
  Query: `search`, `sort`, `required_languages`, `accent`, `use_cases`, `page` (≥0, деф. 0),
  `page_size` (≥1, деф. 30); `gender` — строгий enum `male|female`; `age` — строгий enum
  `young|middle_aged|old` (именно так, через нижнее подчёркивание; «middle-aged» → 422).
  Элементы — объекты ElevenLabs.
  **Зависит от внешнего API ElevenLabs → 503 при недоступности.**
- **`GET /voices/heygen`** — каталог голосов HeyGen.
  Query: `language`, `accent`, `voice_engine` (CSV), `search`, `sort` (`name|newest|default`),
  `page` (≥0, деф. 0), `page_size` (1..200, деф. 55); `gender` — enum `male|female|unknown`.
  Третье значение есть **только здесь**: у ElevenLabs — `male|female`, у LumVoice
  (`GET /voices/public`) — `male|female|neutral`. HeyGen отдаёт часть голосов без указанного пола.
  Элемент: `{ voice_id, voice_name, display_name, gender, language, locale, accent, flag_url,
  preview_url, voice_engines:[...], labels:{...}, support_realtime, emotion_support, support_locale }`.
- **`GET /voices/heygen/filters`** →
  `data = { voice_engines:[{value,count}], genders:[...], languages:[...], accents:[...], labels:[...] }`.

`preview_url` — подписанная ссылка на аудио-превью, играется обычным `<audio>`.

### 7.7 Billing / Usage

Право `billing.read`.

- **`GET /usage`** → `data` = массив записей потребления:
  `{ limit_id, service_id, service_code, service_name, entitlement_id, entitlement_code, limit_type,
  limit_value, used, remaining, period_start, period_end, resets_in }`.
- **`GET /usage/{service}`** — `{service}` = integer id сервиса. Тот же формат, по одному сервису. 404.

> `GET /wallets` и `GET /ledger-transactions` существуют только при включённом кошельке
> (модель «без аванса» по умолчанию их не отдаёт → 404). В типовой конфигурации баланса нет:
> деньги входят прямой оплатой подписки, публичный surface состояния — `usage` и `subscriptions`.

### 7.8 Subscriptions

- **`GET /subscriptions`** — `billing.read`. Без параметров. → `data`:
```json
{
  "available_models": ["<model_key>", "..."],
  "subscriptions": [
    {
      "id": 1, "status": "active",
      "current_period_start": "…", "current_period_end": "…",
      "items": [
        {
          "plan": { "code": "pro", "name": "Pro" },
          "status": "active", "is_usable": true, "billing_period": "monthly",
          "start_at": "…", "end_at": "…",
          "token_allowance": 1000000, "tokens_used": 12345, "tokens_remaining": 987655
        }
      ]
    }
  ]
}
```
Возвращаются только «живые» подписки (Active и срок не истёк). Денежных полей нет.

### 7.9 Profile

- **`GET /user`** — `profile.read`.
  Query: `include` (CSV, whitelist; неизвестное игнорируется):
  `wallets, telegram, subscriptions, referral, order_settings, entitlements, usage, unread_notifications_count`.
  → `data` = `User` (см. §9) + подмешанные `extras` из include (напр. `usage`, `unread_notifications_count`).

### 7.10 Сервисные файлы (субтитры, alignment)

Помимо основного результата (`result.files[]` — аудио/изображения), заказ может нести **сервисные
файлы** в **отдельном** поле `result.service_files[]`. О них не догадаться из `files` — это разные
контейнеры.

**Форма.** `service_files` — **плоский массив строк-путей**, ровно как и `files[]`:
```json
"result": {
  "user_message": null,
  "files": [ "storage/501/orders/<ORDER_UUID>/output/final/result.mp3" ],
  "service_files": [
    "storage/501/orders/<ORDER_UUID>/output/final/service/subtitles.srt",
    "storage/501/orders/<ORDER_UUID>/output/final/service/subtitles.vtt",
    "storage/501/orders/<ORDER_UUID>/output/final/service/result.json"
  ]
}
```
Пути абсолютные (как у `files`), готовы к обмену на ссылку.

**Типы (видимые потребителю).** Различаются **по расширению / сегменту `/service/`** в самой строке —
отдельного поля `type` у элемента НЕТ:
- **субтитры** — `.srt` / `.vtt` / `.lrc` (basename `subtitles.*`);
- **alignment** — пословное/посимвольное выравнивание текст↔аудио (`alignment.*`);
- **result.json** — богатые тайминги (el-timestamps): караоке, пословная подсветка, точная синхронизация.

(Типы `raw_chunk` — сырые данные чанка — и `metadata` отдаются только админу, потребителю по ключу
не видны: скачивание такого пути вернёт 403.)

**Только TTS.** Субтитры/alignment генерируют TTS-сервисы. У `sfx`/`music` поля `service_files`
**нет** (заказ его не содержит).

**Скачивание** — тем же `POST /storage/url` c `path` = строкой из `service_files[]` (право
`orders.download`):
```bash
curl -s -X POST "$BASE/storage/url" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "path": "storage/501/orders/<ORDER_UUID>/output/final/service/subtitles.srt" }'
```

**Только на уровне заказа.** `service_files` есть в `order.result` (`GET /orders/{order}` и в списке
`GET /orders`). На уровне отдельного чанка (`GET /orders/{order}/items/{item}`) сервисные файлы **не
отдаются** — там только основной `result_file` чанка.

### 7.11 Плейбук: обработка сбоев чанка

Заказ разбивается на чанки (`OrderItem`). Статусы чанка: `pending`, `processing`, `completed`,
`policy_flagged`, `failed`. Если часть чанков не удалась, заказ переходит в **`partially_completed`**
(нетерминальный) — успешные чанки готовы, сбойные ждут вашего действия.

**Два вида сбоя чанка:**
- **`failed`** — техническая ошибка (таймаут воркера, сбой генерации). Обычно достаточно повторить
  как есть.
- **`policy_flagged`** — контент отклонён политикой провайдера (напр. ElevenLabs). **Повтор с тем же
  текстом будет отклонён снова** — нужен исправленный текст.

**Четыре инструмента (не путать):**

| Действие | Для чанка в статусе | Что делает | Стоимость |
| --- | --- | --- | --- |
| `POST .../items/retry-failed` | все `failed` заказа сразу | массовый повтор одним вызовом, `202` + план `queued`/`skipped`; `policy_flagged` пропускает | бесплатно (залоченный остаток) |
| `POST .../items/{item}/retry` | `policy_flagged` **или** `failed` | повторяет сбойный чанк (опц. новый `text`); заказ должен быть `partially_completed` | бесплатно (залоченный остаток) |
| `POST .../items/{item}/regenerate` | `completed` | переделывает уже готовый чанк новым `text` | платно (новая токен-квота, precheck → 429) |
| `POST /orders/{order}/retry` | — (весь заказ) | создаёт **новый** заказ теми же параметрами; исходный должен быть `completed`/`result_delivered` без `failed`-чанков | платно (новый заказ) |

**Как дотянуть `partially_completed` до `completed`:**
1. Один вызов `POST /orders/{order}/items/retry-failed` — он заберёт все технические `failed`.
2. Оставшиеся `policy_flagged` (они придут в `skipped[]` с этой причиной) добейте поштучно
   `POST .../items/{item}/retry` с **исправленным** `text`.

Order-level `POST /orders/{order}/retry` для этого **не подходит** (он не принимает
`partially_completed` и падает при наличии `failed`-чанков). Поштучный обход тоже рабочий путь —
просто дороже по числу запросов; на длинном тексте это десятки вызовов вместо одного.

**Правило policy_flagged:**
1. Получите текущий текст чанка: `GET /orders/{order}/items/{item}/text`.
2. Исправьте формулировку (уберите/перефразируйте потенциально проблемный фрагмент).
3. Повторите с исправленным телом: `POST .../items/{item}/retry` `{ "text": "<новый текст>" }`.

> **Причина policy/сбоя наружу НЕ отдаётся.** API не сообщает, *что именно* не понравилось
> провайдеру: поля с reason нет, а у полностью упавшего заказа `result` приходит `null`. Правьте
> текст по своему усмотрению — не ищите несуществующее поле с причиной.

**`can_retry` / `can_regenerate` ≠ гарантия успеха.** Эти булевы флаги в `OrderItem` смотрят
**только на статус чанка** (`can_retry` = `policy_flagged`/`failed`; `can_regenerate` = `completed`).
Фактический вызов может всё равно вернуть **400**, если: заказ не `partially_completed`, исчерпан
лимит попыток (5), не осталось залоченного остатка, либо тип задачи — `sfx`/`music`.

**`sfx`/`music`:** item-retry и regenerate для них **недоступны** (одноразовая генерация). При
неудаче — отмените (`cancel`, пока возможно) и создайте заказ заново.

---

## 8. Тела POST-запросов (точные правила валидации)

### 8.0 `POST /orders`

| Поле | Правило |
| --- | --- |
| `template_id` | nullable, **required_without:task_type**, exists (UUID шаблона) |
| `task_type` | nullable, **required_without:template_id**, one of `tts,image,txt2img,remix,image_edit,voice_clone,sfx,music,voice_search` |
| `name` | sometimes, nullable, string, max 120 — человекочитаемое имя заказа: определяет имя скачиваемого файла и ZIP-архива. Не передано → сервер выведет сам (см. ниже) |
| `input_text` | nullable, string — текст/промпт (для TTS — озвучиваемый текст) |
| `task_data` | nullable, array (или JSON-строка) — параметры для template-less сервисов (§8.1) |
| `config_override` | sometimes, nullable, array (или JSON-строка) — пер-заказный оверрайд `config` шаблона (см. ниже) |
| `input_files` | nullable, array; `input_files.*` — file, max 20480 КБ (20 МБ/файл) |
| `skip_service_defaults` | sometimes, nullable, boolean — не подмешивать `default_config` сервиса к `task_data` (§8.1) |
| `confirm_payg_topup` | sometimes, boolean — согласие на PAYG-добор после 402 |
| `quote_token` | sometimes, string |

> **Как выводится `name`, если вы его не прислали.** Порядок источников:
> явный `name` → **имя загруженного файла без расширения** (`Интервью с автором.mp3` →
> `Интервью с автором`) → первые ~60 символов текста по границе слова (паузные теги
> `{{pause=…}}` вырезаются). Имя файла, записанного сервером из `input_text`, источником
> не считается — там был бы `{uuid}.txt`.

> **`config_override` — единственный способ переопределить настройки шаблона под конкретный заказ**,
> не создавая новый шаблон. Форма — тот же JSON-объект, что и `config` шаблона; применяется поверх
> `template.config` **только** к этому заказу. Например, сделать один TTS-заказ другим голосом:
> `{ "template_id": "<UUID>", "input_text": "…", "config_override": { "tts_settings": { "voice_id": "<OTHER_VOICE_ID>" } } }`
> — остальной конфиг шаблона сохранится. Пути вне `Service.validation_schema` **молча отбрасываются**,
> оставшиеся значения валидируются по схеме (вне диапазона/enum → **422**). Работает только при заказе
> по `template_id`; у template-less `sfx`/`music` все параметры и так в `task_data`.
>
> **Явный `null` в `config_override` стирает значение шаблона для этого заказа**, а не означает
> «оставить как есть»: мерж идёт `array_replace_recursive`, и `null` перезаписывает. Клиент,
> сериализующий всю форму настроек целиком с `null` в пустых полях, молча обнулит половину
> шаблона. Единственный способ сказать «не трогать» — не присылать ключ.

> **Входные файлы (`input_files[]`, либо одиночный `input_file` — multipart).** Не «не нужны»:
> для `tts` файл `.txt` — полноценный **третий канал текста** наравне с `input_text` и
> `task_data.text` (все три проходят одни и те же проверки — капы пауз, `text_empty`,
> `text_not_voiceable`); для `voice_search` аудиофайл **обязателен**. Не нужны они только
> `sfx` и `music`.

Провенанс (`source=api`, `api_key_id`) проставляет сервер. Возможные коды: 201 / 400 / 402
(`payg_topup_required` — не хватает квоты подписки, требуется подтверждение добора: тело несёт
`shortfall_tokens`, `shortfall_lmc`, `shortfall_lmc_minor`, `quote_token`, `expires_at` — повторите
запрос с `confirm_payg_topup: true` и тем же `quote_token`) / 409 (`quote_mismatch` — добор
подорожал, `quote_token` устарел: запросите 402 заново) / 422 / 429.

### 8.1 `task_data` для template-less сервисов (`sfx`, `music`, `voice_search`)

Промпт и параметры генеративного сервиса без шаблона идут в `task_data` (объект **или**
JSON-строка — multipart-клиент может прислать всю структуру одним полем). **Сервер домешивает
`default_config` сервиса поверх ваших значений**: то, что вы не передали, приедет в сохранённом
`task_data` заказа со значением по умолчанию (ваши значения приоритетнее). Значения ниже —
предельные. Ключ описания у SFX — **`text`**, у Music — **`prompt`** (не перепутайте).

> **Ключи кладите в КОРЕНЬ `task_data`, без обёртки.** `GET /templates/config-options?service=sfx`
> для этих сервисов отвечает, но отдаёт параметры сгруппированными в секцию (`sfx_settings`,
> `music_settings`, `voice_search_settings`) — это форма для интерфейса настроек, а не форма
> запроса: `validation_schema` у них плоская. `{"sfx_settings": {"text": "…"}}` → **422**
> «text обязателен» (обязательный ключ спрятан обёрткой), а в `config_override` заказа такая
> обёртка отбрасывается молча. У `elevenlabs`/`heygen`/`lumimg` расхождения нет: там имя секции
> и есть путь (`tts_settings.model_id`).

**SFX** (`task_type: "sfx"`) — одна генерация = **ровно один** аудиофайл:

| Ключ | Тип / правило | Дефолт | Примечание |
| --- | --- | --- | --- |
| `text` | **required**, string | — | Описание звука (английский промпт работает лучше всего) |
| `prompt_influence` | nullable, numeric `0..1` | `0.3` | Насколько строго держаться промпта |
| `duration_seconds` | nullable, numeric `0.5..30` | `null` | `null` = авто-длительность |
| `loop` | nullable, boolean | `false` | Бесшовное зацикливание |
| `output_format` | nullable, enum | `mp3_44100_128` | Одно из: `mp3_44100_128`, `mp3_44100_192`, `opus_48000_128` |

**Music** (`task_type: "music"`) — **`n_variants` генераций = `n_variants` файлов**:

| Ключ | Тип / правило | Дефолт | Примечание |
| --- | --- | --- | --- |
| `prompt` | **required**, string | — | Описание трека (жанр, настроение, инструменты) |
| `n_variants` | nullable, integer `1..4` | `1` | Сколько вариантов сгенерировать. **Умножает цену** (`n_variants ×` ставку) и даёт столько же файлов в `result.files[]` |
| `lyrics_text` | nullable, string | `null` | Текст песни; если пусто и не `force_instrumental` — модель может добавить вокал сама |
| `force_instrumental` | nullable, boolean | `false` | Инструментал без вокала |
| `music_length_ms` | nullable, integer `10000..300000` | `30000` | Длительность трека в **миллисекундах** (10 сек … 5 мин) |
| `model_id` | nullable, enum | `music_v2` | Пока единственное значение: `music_v2` |

**Voice Search** (`task_type: "voice_search"`) — аудио едет в `input_files`, здесь один параметр:

| Ключ | Тип / правило | Дефолт | Примечание |
| --- | --- | --- | --- |
| `top` | nullable, integer `1..50` | `10` | Сколько похожих голосов вернуть |

> **Цена этих сервисов зависит от ЧИСЛА генераций, а не от их размера.** `sfx` — одна ставка
> за заказ (`duration_seconds` и длина `text` на цену не влияют); `music` — ставка ×
> `n_variants` (`music_length_ms`, длина `prompt` и `lyrics_text` не влияют); `voice_search` —
> одна ставка (длительность записи и `top` не влияют). Пятиминутный трек стоит ровно столько
> же, сколько десятисекундный. Фактически списанное — в `price_units` заказа.

> **`skip_service_defaults: true` отключает это домешивание.** Пришлите флаг, если у вас
> уже есть полный набор параметров и вы хотите, чтобы воркер получил ровно его: сервер не
> добавит в `task_data` ни одного ключа из `default_config`, и повторное домешивание при
> отправке задачи воркеру тоже не произойдёт. Флаг сохраняется на заказе — `POST
> /orders/{id}/retry` воспроизводит то же поведение. На заказы **по шаблону** флаг не
> влияет: там конфиг берётся из `template.config` (+ `config_override`), а не из
> `default_config` сервиса.

> Значение вне диапазона/enum → **422** (проверяется до биллинга; заказ не создаётся).
> У обоих сервисов **нет** item-level `retry`/`regenerate` (§7.11) и **нет**
> `result.service_files` (субтитры генерирует только TTS). Одноразовая генерация: при неудаче —
> `cancel` (пока возможно) и создать заказ заново.

### 8.2 `POST /templates`

| Поле | Правило |
| --- | --- |
| `service_key` | **required**, code активного сервиса (напр. `elevenlabs`, `lumimg`) |
| `name` | **required**, string, max 255 |
| `config` | **required**, JSON-объект — по схеме сервиса (динамические правила `config.*`) |
| `is_public` | boolean |
| `folder_id` | nullable, exists (UUID вашей папки) |

`config` валидируется по правилам конкретного сервиса. Точную структуру для нужного сервиса берите
из `GET /templates/config-options?service=<code>`. Пример **минимального валидного `config`
для `elevenlabs`** (TTS):
```json
{
  "tts_settings": {
    "mode": "mode_v1",
    "model_id": "eleven_multilingual_v2",
    "voice_id": "<VOICE_ID>",
    "advanced_voice_settings": true,
    "voice_settings": {
      "stability": 0.5,
      "similarity_boost": 0.75,
      "use_speaker_boost": true,
      "speed": 1.0
    }
  }
}
```

> **`similarity_boost`, `style` и `use_speaker_boost` действуют только при
> `tts_settings.advanced_voice_settings: true`.** Флаг по умолчанию `false`, и тогда до движка
> озвучки доезжают лишь `stability` и `speed` — остальные три сохранятся в шаблоне и пройдут
> валидацию (`exclude_when` снимает с них правила, а не значения), но на звук не повлияют.
> Ошибки не будет: со стороны это выглядит как «настройка не работает». Если крутите тонкие
> параметры — ставьте флаг в том же объекте.

**`tts_settings.prev_next_text` и `tts_settings.request_ids`** (оба nullable boolean, по
умолчанию `false`) — сшивка просодии между фрагментами. Длинный текст режется на фрагменты, и
каждый озвучивается своим запросом, поэтому на стыках слышен перепад интонации. Первый флаг
просит передавать движку текст соседних фрагментов (`previous_text`/`next_text`), второй —
идентификаторы соседних генераций (request stitching; провайдер отдаёт им приоритет над
текстом). На цену и на разбиение текста флаги не влияют.

> **Включить можно только ОДИН из двух.** Оба сразу — `422`
> (`tts_stitching_flags_mutually_exclusive`): получив и текст соседей, и идентификаторы,
> ElevenLabs использует только идентификаторы, а текст игнорирует — то есть «включено
> оба» было бы тихо выключенным `prev_next_text`.

**`tts_settings.apply_text_normalization`** (nullable string, по умолчанию `auto`) — нормализация
текста у движка: `auto` — решает сам (например, читать ли числа словами), `on` — применять всегда,
`off` — пропускать.

> **Флаги влияют не на всех моделях, и молча.** Публичная документация ElevenLabs числит
> семейство `eleven_v3` вне request stitching. Значение сохранится в шаблоне и пройдёт
> валидацию, но на звук может не повлиять — ошибки при этом не будет. Та же природа отказа,
> что у `advanced_voice_settings` выше.

Допустимые `model_id` (ElevenLabs): `eleven_v3`, `eleven_v3_beta`, `eleven_multilingual_v2`,
`eleven_flash_v2_5`, `eleven_turbo_v2_5`, `eleven_turbo_v2`, `eleven_flash_v2`. Диапазоны `voice_settings`:
`stability` 0..1 (**единственный обязательный**), `similarity_boost` 0..1 (nullable),
`style` 0..1 (nullable), `use_speaker_boost` boolean, `speed` 0.7..1.2. Опциональные секции:
`pause_settings.*`,
`output_settings.*` (`audio_format` из `mp3,wav,ogg`; `audio_quality` из
`low,medium,high,lossless`; `sample_rate` из `22050,44100,48000`).

**`tts_settings.language_code`** (опционально, nullable, ISO-639-1) — язык синтеза. Отсутствие
поля или `null` = ElevenLabs сама определяет язык по тексту (ненадёжно на коротких/смешанных
фразах) — явный код надёжнее. Полный список из 73 допустимых значений — в
`GET /templates/config-options?service=elevenlabs` (`tts_settings.language_code.options`).
29 базовых кодов (`eleven_multilingual_v2`, `eleven_flash_v2_5`, `eleven_turbo_v2_5`):
`en, ja, zh, de, hi, fr, ko, pt, it, es, id, nl, tr, fil, pl, sv, bg, ro, ar, cs, el, fi, hr, ms,
sk, da, ta, uk, ru`. Остальные 44 (например `he`, `kk`, `vi`, `cy`) поддерживает только
v3-семейство (`eleven_v3`, `eleven_v3_beta`): на других моделях они теперь **отклоняются
валидацией (422)**. Раньше такой запрос проходил, но ElevenLabs отвечала 400 и язык молча
терялся — озвучка выходила на автоопределённом языке. `GET /templates/config-options` отдаёт
полный список в `options` и суженный до 29 — в `conditional_overrides` для не-v3 моделей.

`stability` на v3-семействе принимает только `0.0`, `0.5` и `1.0` — шаг приходит в
`conditional_overrides` отдельной веткой на каждую v3-модель.

### 8.3 Текст озвучки: каналы, разметка, нормализация

**Каналов текста три, и они равноправны** — все три проходят одни и те же проверки:
`input_text`, содержимое `.txt` из `input_files[]`, `task_data.text`.

#### Что вырезается из озвучки и не тарифицируется

Распознаются три вида разметки, но **бесплатен из них только один**:

| Вид | Примеры | Что делает | Тарифицируется |
| --- | --- | --- | --- |
| Пауза | `{{pause=2.5}}`, `{{pause:500ms}}`, `{{pause-3s}}`, `{{pause}}` | Тишина; без значения — 1 секунда | **Нет** |
| SSML-пауза | `<break time="2s"/>`, `<break time="500ms"/>` | То же в SSML-нотации | Да |
| Указание диктору | `[шёпот]`, `[confident]`, `[pause]` | Подсказка модели о манере чтения | Да |

> **Бесплатны и невесомы только `{{…}}`.** Это наш собственный формат: тишину по нему
> строит воркер, до провайдера строка не доезжает.
>
> **Квадратные скобки и `<break/>` — синтаксис самого провайдера.** Он получает их
> целиком, тарифицирует по своим правилам и учитывает в лимите длины запроса. Поэтому
> они оплачиваются и **занимают место во фрагменте**: 999-символьный фрагмент с
> `[confident]` внутри вмещает 988 произносимых символов, а не 999.
>
> Квадратные скобки — это ВСЯ разметка, а не список ключевых слов: подсказкой считается
> любой фрагмент `[…]` без вложенных скобок и переноса строки внутри (regex
> `/\[[^\[\]\n]+\]/u`). Сноска `[1]`, пометка `[sic]`, тайм-код `[00:12]` — модель увидит
> их и, скорее всего, интерпретирует как указание. Легаси-форма `[pause=2s]` в подсчёт
> пауз для капов не входит (там считаются только `{{pause}}` и `<break/>`).
>
> **Круглые и угловые скобки, звёздочки и прочая типографика озвучиваются и
> тарифицируются**: `(пауза)`, `<Fade out>`, `*шёпотом*` диктор прочитает вслух.

#### Санити-капы пауз (`config/chunking.php`)

Проверяются **до** создания заказа; превышение → **422**, деньги не резервируются:

| Ограничение | Значение |
| --- | --- |
| Пауз в тексте | ≤ 500 |
| Одна пауза | ≤ 10 секунд |
| Суммарная тишина | ≤ 3600 секунд |

Паузы бесплатны — капы защищают от мусорного ввода, а не тарифицируют. Отдельный отказ —
текст, в котором после вырезания разметки **не осталось произносимого**: только теги →
`text_empty`; одни знаки препинания (`.`, `!!!`, `—`) или одни скобочные ремарки →
`text_not_voiceable`. Оба — 422.

#### Нормализация до нарезки

Сохранённый заказ и все смещения чанков считаются уже от изменённого текста:

| Что | Как | Для кого |
| --- | --- | --- |
| `\r\n`, `\r` | → `\n` | любой текстовый вход |
| Табуляция | → пробел | любой текстовый вход |
| > 3 переносов подряд | → ровно 3 | любой текстовый вход |
| > 5 пробелов подряд | → ровно 5 | любой текстовый вход |
| `Привет.Мир` | → `Привет. Мир` (пробел после `.`/`!`/`?` перед буквой или цифрой) | **только озвучка** |

Последнее правило не трогает десятичные (`3.14`), сокращения (`т.д.`), акронимы (`U.S.A.`),
URL со схемой, email, многоточия и содержимое тегов разметки. Известный компромисс: голый
домен без схемы (`example.com` в середине фразы) неотличим от `Привет.Мир` и получит пробел —
пишите такие адреса со схемой. Операции идемпотентны.

### 8.4 Абзацный режим озвучки (`generation_mode`)

По умолчанию текст режется на фрагменты подряд, по техническому размеру, и границы абзацев
роли не играют. Абзацный режим делит текст сначала по **пустой строке**, и уже каждый абзац
режется внутри себя.

Ключ лежит **не внутри `tts_settings`**, а рядом с ним, в корне `config_override` (или в корне
`config` шаблона — тогда режим станет постоянным для всех заказов по нему):

```json
{ "template_id": "<UUID>", "input_text": "Первый абзац.\n\nВторой абзац.",
  "config_override": { "generation_mode": "paragraph" } }
```

| Значение | Что делает |
| --- | --- |
| `paragraph` | Абзац — самостоятельная единица генерации |
| `default` | Сплошной рез по размеру фрагмента (как без ключа) |

Поддерживают `elevenlabs` и `heygen`; прочие сервисы ключ игнорируют — отказа не будет, режим
просто не включится. В `GET /templates/config-options` он не перечислен: справочник описывает
настройки сервиса, а режим задаётся на заказ.

**Что меняется в ответе:** у заказа появляется `paragraphs[]` (по каждому абзацу —
`paragraph_index`, `chunks_count`, `completed_chunks`, `status`, `progress_percent`), у
фрагментов — `paragraph_index`.

- `paragraphs[].status` — свой словарь из 4 значений: `pending`, `in_progress`, `failed`,
  `completed` (чанк, заблокированный контентной политикой, схлопывается в `failed`).
- Считаются **только исходные чанки** (`item_type = chunk`), поэтому успешный повтор упавшего
  фрагмента статус абзаца НЕ меняет — смотрите на сам фрагмент.
- `OrderItem.paragraph_index` заполнен только у исходных чанков. У записей `retry` и
  `regeneration` он `null` даже в абзацном заказе — привязку берите у родителя через
  `parent_item_id`.

> **Влияет на цену:** минимальный тарифицируемый объём применяется к КАЖДОМУ абзацу отдельно.
> Пять коротких абзацев стоят пять минимумов, тогда как тот же текст в обычном режиме обошёлся
> бы в один.
>
> **Влияет на скорость:** абзац — самостоятельная задача, одновременно исполняются не больше
> 10 однофрагментных абзацев плюс 5 многофрагментных на заказ, остальные ждут слота. Текст из
> сотни коротких абзацев идёт волнами, зато первые результаты приходят раньше.

---

## 9. Схемы ресурсов (ключи в user-контексте)

По API-ключу все ресурсы отдаются в **пользовательском** контексте — админ-поля скрыты, даже если
ключ принадлежит администратору.

> **Типы в этом разделе точные** (`int` / `enum` / ISO-дата). Портальный `public-openapi.json`
> собирается без доступа к БД, поэтому часть скалярных полей показана там как generic `string` —
> ориентируйтесь на типы отсюда и на примеры ответов (в примерах — реальные значения). Поля с
> пометкой «Скрыто (admin)» по API-ключу **не приходят** (даже если ключ принадлежит администратору).

**`Order`:**
```
id (UUID), user_id, name (string|null), template (Template|null), template_id,
task_type, status (enum `OrderStatus`, 9 значений — см. §5),
price_units (int), price_formatted (float), currency_asset (Asset),
total_tokens (int|null), total_duration_ms (int|null),
tokens_from_quota (int|null), tokens_from_payg (int|null), tokens_spent (int|null),
task_data (object|null),
result ({ user_message, files:[string], service_files:[string] } | null),
created_at, updated_at,
files_expire_at (ISO|null), files_expired (bool),
eta_seconds (int|null), eta_at (ISO|null),
queue_position (int), queue_wait_seconds (int|null) — только voice_search, см. ниже,
completed_chunks (int), total_chunks (int), progress_percent (int),
items: [ OrderItem ]
```
**Прогресс и ожидание.** `completed_chunks`/`total_chunks`/`progress_percent` — сколько чанков уже
готово. `eta_seconds` — оценка времени до готовности (секунды), `eta_at` — тот же прогноз
абсолютным моментом; считаются по накопленной статистике сервиса, это **оценка, а не обещание**.
`queue_position`/`queue_wait_seconds` — про очередь `voice_search` (эти задачи обслуживаются по
одной). **Оба ключа в ответе либо есть, либо их нет вовсе** — `null`-заглушек не будет: у заказов
других типов и у `voice_search`, уже покинувшего очередь, ключи просто отсутствуют. Не пишите
`order.queue_position === null`, проверяйте наличие ключа. Позицию обновляет событие
`voice_search.queue.updated` (§10); в WebSocket-проекции заказа `queue_wait_seconds` = `null`
(пересчёт делается на запрос, а не на каждую рассылку).
**Деньги.** `price_units` — залоченная PAYG-сумма в **minor units** LMC, показывать её как
число нельзя: реальное значение = `price_units / 10^currency_asset.precision`, а `precision`
живёт в активе и не обязана быть равна 2. Рядом всегда идут `price_formatted` (та же сумма
как decimal-число в LMC) и `currency_asset` (объект `Asset` с полем `precision`). Заказ всегда
номинирован в LUMC. Тот же инвариант — у всех денежных ресурсов (Payment, Invoice, Refund).

**Ретенция файлов.** `files_expire_at` — ISO8601-момент, когда файлы заказа удалит сборщик
ретенции, либо `null` = «дедлайна нет»: заказ ещё не в терминальном статусе **или** файлы уже
удалены. `files_expired: true` — файлов больше нет, скачивать нечего (запись заказа в БД
остаётся). Оба поля — арифметика над полями заказа, приходят во всех контекстах, включая
WebSocket. Различайте два `null`-случая по `files_expired`.

**`total_duration_ms`** — суммарная **длина аудио** заказа в миллисекундах (сумма
`duration_ms` по чанкам), а **не** время генерации. `null` — воркер длительности не прислал
(или тип задачи не аудио).
`name` — человекочитаемое имя заказа (источник имени файла/ZIP при скачивании). `total_tokens` —
полная стоимость в токенах по всем завершённым версиям (чанки + регенерации + retry). `tokens_spent` —
реально потраченные токены (чанки + регенерации, **без** retry), разложенные на бесплатную долю из
квоты подписки (`tokens_from_quota`) и платную PAYG-долю (`tokens_from_payg`). **Нулевая доля приходит
как `null`, а не `0`** — при заказе, полностью покрытом квотой, `tokens_from_payg` = `null`; при чистом
PAYG — `tokens_from_quota` = `null`. `tokens_spent` = сумма обеих долей.
`result` (пользовательский контекст): `files` — основные файлы (плоский массив строк-путей; после
склейки TTS содержит ровно один элемент — финальную дорожку); `service_files` —
сервисные файлы (плоский массив строк-путей: субтитры/alignment, только TTS, whitelist — см. §7.10),
присутствует только при непустом наборе видимых файлов; `user_message` — passthrough от воркера,
**обычно `null`** (не гарантированный текст статуса). У упавшего заказа (`failed`/`compensated`)
`result` = **`null`** (детали ошибки скрыты). Скрыто (admin): `source`, `user`, `execution_meta`,
`metrics`, error-детали и не-whitelisted сервисные файлы (`metadata`).

**`OrderItem`:**
```
id, order_id, item_type (chunk|retry|regeneration|pause_edit|stitch), chunk_index (int|null),
parent_item_id (string|null), original_text_length (int), current_text_length (int),
price_units (int), consumed_units (int), remaining_locked (int),
status (pending|processing|completed|policy_flagged|failed),
result_file (object|string|null), duration_ms (int|null),
attempt_number (int), can_retry (bool), can_regenerate (bool),
created_at, updated_at, parent_item (OrderItem|null), retries ([OrderItem])
```
`duration_ms` — длительность синтезированного **аудио** этого чанка в миллисекундах (не время
генерации). `null` — воркер её не прислал. Сумма по чанкам приходит в `Order.total_duration_ms`.
`status`: `policy_flagged` (контент отклонён политикой) и `failed` (тех. ошибка) — оба сбойные,
оба доступны для item-retry; `completed` — доступен для regenerate (см. плейбук §7.11).
`result_file` — **основной** файл этого чанка (не заказа; per-chunk service_files здесь не отдаются).
`can_retry`/`can_regenerate` отражают только статус чанка — **не гарантируют** успех вызова (§7.11).
Скрыто (admin): `metadata` (в т.ч. причина policy/сбоя — потребителю по ключу не видна).

**`Template`:**
```
id (UUID), user_id, slug, name, service_key, service (Service|null),
config (object), is_public (bool), folder_id, folder (при загрузке), created_at, updated_at
```

**`Service`** (вложенный):
```
id, code, name, display_name, payg_access, billing_mode, unit_asset_id, unit_asset (Asset),
required_entitlement_id, required_entitlement, units_per_call, units_per_char, units_per_image,
units_per_second, min_billable_chars, lumcoin_per_token, lumc_precision,
price_lumc_minor (int|null, money-first цена), unit_size, uses_money_first_pricing (bool),
rate_unit, display_category, is_premium, display_order, metadata, is_active, created_at, updated_at
```
Скрыто (admin): `config_schema, default_config, validation_schema, routing_config, task_types,
default_task_type, features, cost_config`.

**Как читать тариф.** `code` — то самое значение, что идёт в `service_key` шаблона и в путь
`GET /usage/{service}`; `id` в запросах не нужен. `billing_mode` говорит, **какое из полей
`units_per_*` вообще работает** (за вызов / за символ / за секунду / за изображение) — остальные у
этого сервиса смысла не имеют. Все `units_*`, `lumcoin_per_token` и `price_lumc_minor` — **в
минимальных единицах**: реальное значение = `value / 10^precision`, где `precision` для `units_*`
лежит в `unit_asset`, а для LMC-полей — в `lumc_precision`. `min_billable_chars` — пол тарификации:
текст короче тарифицируется как этот минимум. `lumcoin_per_token` нужен для оценки PAYG-добора
(`shortfall_tokens × lumcoin_per_token`, §6.3). Если `uses_money_first_pricing: true`, цена задана
сразу в LMC — `price_lumc_minor` за `unit_size` натуральных единиц (напр. за 1000 символов), а не
выведена из токенов; на итоговую сумму заказа способ расчёта не влияет. `required_entitlement`
(`null` — сервис открыт всем) — возможность подписки, без которой заказ отклонят: есть ли она у
вас, видно в `GET /usage`. `is_active: false` — сервис заказы не принимает. `payg_access` —
разрешён ли для сервиса доплатный добор сверх квоты (`disabled` → вместо 402 придёт
`insufficient_allowance`, §12). `metadata` — произвольный набор ключей, на конкретные не
полагайтесь.

**`LumVoice`:**
```
id, display_name, description, gender, accent, default_language_code,
voice_status, publication_status, allow_usage_in_orders (bool),
preview_generated_at, created_at, updated_at,
tags: [{id,code,name,...}], languages: [...],
preview_urls: { "<language_code>": <signed_url|null> }
```
**Что здесь важно для заказа.** `id` — это и есть значение для `config.voice_id` шаблона.
`voice_status` — состояние клонирования: `ready` — годен целиком, `partially_ready` — часть языков
склонирована, часть упала, **рабочие языки доступны**. Поэтому решает не только статус голоса, а
`languages[]`: заказывать можно на языке, клон которого завершён (состояние каждого —
`VoiceLanguage.status`, причина сбоя — `failure_reason`). `allow_usage_in_orders` — разрешение
автора отдать голос другим: без него голос не показывается в `GET /voices/public`, а у тех, кто
успел добавить его в библиотеку, запись становится `available: false` с `unavailable_reason`
(`author_unpublished` / `admin_force_removed` / `author_deleted` / `owner_deleted`). На **свои**
голоса флаг так не влияет. `publication_status` — стадия модерации, тоже про видимость в общем
каталоге, а не про допуск к заказу. Практическое следствие: голос из `GET /voices/public` заведомо
`ready` и разрешён (каталог фильтрует по обоим полям); голос из своей библиотеки проверяйте по
`available`, а свой собственный — по `languages[]`.
`preview_urls` — карта «код языка → временная ссылка на демо-аудио» (значение `null` = превью для
языка нет); ссылки подписанные и живут ограниченное время, кэшировать их надолго нельзя.
Скрыто (admin): статистика использования, модерация, `owner_user_id`, `metadata` и т.п.

**`User`** (по api-key):
```
id, email, lang_code, display_currency (ISO-4217|null), has_telegram (bool), created_at, updated_at
```
При включённых фичах добавляются `wallets` (если кошелёк включён) и реферальные поля. Динамические
include подмешивают `telegram, subscriptions, order_settings, entitlements, usage,
unread_notifications_count`. Всегда скрыто: `is_admin, email_verified_at, allowed_models,
referrer_id, is_blocked` и прочие приватные/админ-поля.

**`Asset`** (вложенный — `asset` / `currency_asset` / `unit_asset` в денежных полях):
```
id (int), code (string, напр. `LUMC`), kind (enum `AssetKind`: `money` | `unit`),
precision (int — знаков после запятой; реальное значение = stored / 10^precision),
name (string — локализованное), deleted_at (ISO|null — обычно null), created_at, updated_at
```
Скрыто (admin): `is_active`, `is_default`, `default_balance`.

**`Subscription`** (публичная проекция — `GET /subscriptions`, безденежная):
```
id (int), status (string: `active` и т.п.),
current_period_start (ISO), current_period_end (ISO),
items: [ SubscriptionItem ]
```
Публичный surface намеренно урезан: нет денежных полей (`unit_amount`/LMC), вложенного `user`,
кошельков. Возвращаются только «живые» подписки (`Active` и срок не истёк). Полная (внутренняя) форма
с деньгами по API-ключу **не отдаётся** — портальная спека показывает её схему (`SubscriptionItem`/
`SubscriptionPlan` с money-полями), но это НЕ публичный контур; авторитетна форма ниже и в §7.8.

**`SubscriptionItem`** (публичная проекция, вложен в `Subscription.items[]`):
```
plan ({ code, name } | null), status (string: `active` | `canceled` | `expired`),
is_usable (bool — «живая ли»: `Active` И срок `end_at` не истёк; авторитетнее `status`),
billing_period (string: `monthly`),
start_at (ISO), end_at (ISO),
token_allowance (int|null — месячный лимит токенов), tokens_used (int|null),
tokens_remaining (int = token_allowance − tokens_used)
```

**`Entitlement`** (право; вложен в план при `?include=entitlements` на `/user`):
```
id (int), code (string), name (string — локализованное),
metadata (object|null), is_active (bool), created_at, updated_at,
is_unlimited (bool — только в контексте плана: сервисы этого права безлимитны, токены не списываются)
```

**`TemplateFolder`** (папка шаблонов):
```
id (int), user_id (int), name (string),
templates: [ Template ] (при eager-load), created_at, updated_at
```

**`BrowseTemplates`** (ответ `GET /templates/browse` — папки+шаблоны, виртуальная пагинация):
```
items: [ TemplateItem ] (папки и шаблоны вперемешку; дискриминатор — поле `type`: `folder` | `template`),
current_page (int), last_page (int), per_page (int), total (int),
current_folder ({ id, name } | null), breadcrumbs: [ { id, name } ]
```

**`TemplateItem`** (элемент `BrowseTemplates.items[]`; форма зависит от `type` — НЕ `kind`):
```
папка (type "folder"):   type ("folder"), id (int), name (string), templates_count (int), created_at, updated_at
шаблон (type "template"): type ("template"), id (UUID), name (string), slug (string|null),
                          service_key (string), service (Service|null при eager-load), config (object),
                          is_public (bool), folder_id (UUID|null), created_at, updated_at
```
Различайте по `type`: у папки `id` — **int** и есть `templates_count`; у шаблона `id` — **UUID** и есть
`service_key`/`config`. Это lite-проекция (у шаблона нет вложенных `user`/`folder`, у папки — `templates`).

**`VoiceTag`** (вложен в `LumVoice.tags[]`; отдельно — `GET /voices/tags`):
```
id (int), code (string), name (string — локализованное), description (string|null),
color (string — hex), sort_order (int)
```
Скрыто (admin): `name_translations`, `description_translations`, `is_active`, timestamps, `deleted_at`.

**`VoiceLanguage`** (вложен в `LumVoice.languages[]` — статус клон-озвучки по языку):
```
id (int), language_code (string, напр. `en`), status (string|null — статус клонирования голоса),
reference_duration_ms (int|null), model_version (string|null), failure_reason (string|null),
clone_started_at (ISO|null), clone_completed_at (ISO|null), updated_at
```
Скрыто (admin): `reference_audio_path`, `reference_transcription`, `model_path`, `preview_path`, `metadata`.

---

## 10. Real-time (WebSocket) — статус заказов без поллинга

Протокол Pusher-совместимый (`pusher-js` или Laravel Echo).

```js
import Pusher from 'pusher-js';

const pusher = new Pusher('7c4dd881d8116ac76fe89aff71d8c2ff', {
  wsHost: 'ws.lumean.app', wsPort: 443, wssPort: 443,
  forceTLS: true, enabledTransports: ['ws', 'wss'], cluster: '',
  channelAuthorization: {
    endpoint: 'https://api.lumean.app/api/public/broadcasting/auth',
    headers: { 'X-API-KEY': 'ВАШ_API_КЛЮЧ' },   // нужно право orders.read
  },
});

const ch = pusher.subscribe('private-orders.42');   // 42 = ID владельца ключа (user_id)
ch.bind('status.changed', (e) => console.log(e.new_status, e.order.id));
ch.bind('progress',       (e) => console.log(`${e.completed}/${e.total} (${e.percent}%)`));
```

**Каналы:**
- `private-orders.{userId}` — все заказы аккаунта (`userId` = ваш `user_id`, из `GET /user`).
- `private-order.task.{orderId}` — один заказ (`orderId` = UUID заказа).
- `private-chunks.preview.{userId}` — результат async-расчёта `POST /orders/chunks/preview`
  (§7.1). Право доступа то же (`orders.read`).

**События** (для `pusher-js` имя без ведущей точки; для Echo — `.listen('.status.changed')`):

| Событие | Канал | payload |
| --- | --- | --- |
| `created` | orders | `{ order }` |
| `status.changed` | оба | `{ order, previous_status, new_status, items_summary: {total, by_status} }` |
| `item.status.changed` | оба | `{ item, previous_status, new_status, completed_chunks, total_chunks, progress_percent }` |
| `progress` | orders | `{ order_id, completed, total, percent }` |
| `progress` | order.task | `{ task_id, progress, chunks_done, chunks_total, timestamp }` |
| `bulk_retry.completed` | оба | `{ order_id, created_count, failed_count }` |
| `renamed` | оба | `{ order }` |
| `voice_search.queue.updated` | оба | `{ order_id, queue_position, queue_wait_seconds }` |
| `policy_cooldown.applied` | orders | `{ reason, minutes, retry_after, retry_at, level, threshold }` |
| `policy_cooldown.cleared` | orders | `{ was_blocked, strikes, level }` |
| `archive.progress` | orders | `{ order_id, archive_id, percent, files_done, files_total }` |
| `archive.ready` | orders | `{ order_id, archive_id, url, size, expires_at }` |
| `archive.failed` | orders | `{ order_id, archive_id, reason }` |
| `ready` | chunks.preview | `{ status: "ready", preview_id, summary, page }` — БЕЗ `chunks` (см. ниже) |

> **`bulk_retry.completed`** — финал массового повтора (`POST .../items/retry-failed`, §7.2):
> `created_count` — сколько повторных чанков реально создано, `failed_count` — сколько не удалось
> поставить (гарды в момент исполнения строже предварительных: между ответом `202` и запуском
> джобы состояние чанка могло измениться). Это событие «план исполнен», а не «заказ готов» —
> готовность по-прежнему смотрите по `status.changed`/`item.status.changed`.
>
> **`voice_search.queue.updated`** — только для заказов `voice_search`: они обслуживаются по
> одному, и место в очереди меняется по мере продвижения. Те же значения приходят в полях
> `queue_position`/`queue_wait_seconds` заказа (§9); `queue_wait_seconds` — оценка, а не обещание.
>
> **`renamed` и `archive.*` по API-ключу инициировать нельзя** — переименование заказа и сборка
> ZIP-архива живут только в веб-кабинете (в публичном контуре таких эндпоинтов нет). События
> приходят, если владелец аккаунта сделал это из интерфейса, — учитывайте их, чтобы не считать
> неизвестными; `renamed` несёт полный обновлённый `order`, поэтому им можно освежать своё
> состояние заказа.

> **`policy_cooldown.applied`** предупреждает о кулдауне (`reason: policy_cooldown_active`, §12)
> **до** того, как вы упрётесь в 429: ограничение накладывается в момент финализации последней
> провалившейся задачи, а не в ответ на ваш запрос. Отсчёт вести по `retry_at` (абсолютный
> ISO-8601), а не по `retry_after`: событие идёт через очередь, и относительное значение
> стареет в пути. Локализованного текста в payload нет — рассылка идёт из фонового воркера,
> где язык клиента неизвестен; текст отдаёт синхронный 429. **`policy_cooldown.cleared`**
> приходит, когда ограничение снято досрочно успешной задачей (такое бывает: задачи,
> отправленные до ограничения, продолжают выполняться) — по нему можно возобновлять отправку,
> не дожидаясь `retry_at`.

> **`ready`** на `chunks.preview` — ограниченное уведомление, чанки в payload НЕ приходят
> (страница с их текстом превысила бы лимит кадра Reverb): подтяните первую страницу через
> `GET /orders/chunks/preview/{previewId}?page=1` (§7.1). Полный референс, включая
> TypeScript-типы и примеры — `docs/PUBLIC_API_CHUNK_PREVIEW.md`.
>
> `progress` на двух каналах имеет **разную форму** — различайте по каналу. Подписка возможна
> только на свои каналы (иначе 403). Файлы в payload — только метаданные; скачивание — через
> `POST /storage/url`. Права канала: `orders.*` требуют `orders.read`; квоты/леджер — `billing.read`.

**Авторизация подписки вручную (клиент без Pusher SDK).** Приватный канал требует подписи: сервер
Reverb присылает вам `socket_id` при подключении, вы отдаёте его вместе с именем канала на
`POST /api/public/broadcasting/auth` и полученную строку передаёте в кадре подписки.

| Поле тела | Тип | Что это |
| --- | --- | --- |
| `socket_id` | string, required | Идентификатор соединения из кадра `pusher:connection_established` |
| `channel_name` | string, required | Полное имя канала **с префиксом** `private-` (напр. `private-orders.42`) |

```bash
curl -s -X POST "https://api.lumean.app/api/public/broadcasting/auth" \
  -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "socket_id": "123456.7891011", "channel_name": "private-orders.42" }'
# → { "auth": "7c4dd881d8116ac76fe89aff71d8c2ff:<hmac>" }
```

Строку `auth` целиком кладите в `data.auth` кадра `pusher:subscribe`. Отказы: **401** — ключа нет
или он невалиден; **403** — у ключа нет права под этот канал (`orders.*` → `orders.read`) **или**
канал чужой (проверка владельца — по `user_id` внутри имени канала). Эндпоинт живёт **вне** общей
группы публичного API: на него действует отдельный лимит (1200 запросов в минуту), окна ключа
(`per_minute`/`per_hour`/`per_day`, §6.1) он не расходует. Пользуетесь `pusher-js`/Laravel Echo —
всё перечисленное SDK делает сам, ему достаточно блока `channelAuthorization` из примера выше.

---

## 11. Рецепты (сквозные примеры)

### Рецепт A. TTS на ElevenLabs
См. §1 (TL;DR) — полный путь: голос → шаблон → заказ → ожидание → `storage/url`.
Аналогично для голосов **HeyGen** (`GET /voices/heygen`) и **LumVoice**
(`GET /voices/public` или `GET /voices`): берёте оттуда `voice_id`/`id` и кладёте в
`config.tts_settings.voice_id` шаблона соответствующего сервиса.

### Рецепт B. Звуковой эффект (SFX, без шаблона) — полный цикл

Шаблон не нужен: тип и параметры — прямо в запросе. Ключ описания звука — **`text`** (НЕ `prompt`).

**1. Создать заказ.**
```bash
curl -s -X POST "$BASE/orders" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" -d '{
  "task_type": "sfx",
  "task_data": {
    "text": "Ocean waves crashing on rocks, distant seagulls",
    "duration_seconds": 8,
    "prompt_influence": 0.4,
    "output_format": "mp3_44100_128"
  }
}'
```
**Ответ `201`** — `data` = заказ. Обратите внимание: сервер **домешал дефолты** (`loop`) в
`task_data`; ключей `template`/`items` в только что созданном заказе ещё нет:
```json
{
  "success": true,
  "message": "Order created successfully.",
  "data": {
    "id": "b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
    "user_id": 501,
    "template_id": null,
    "task_type": "sfx",
    "status": "created",
    "price_units": 0,
    "total_tokens": 0,
    "task_data": {
      "prompt_influence": 0.4,
      "duration_seconds": 8,
      "loop": false,
      "output_format": "mp3_44100_128",
      "text": "Ocean waves crashing on rocks, distant seagulls"
    },
    "result": null,
    "created_at": "2026-07-17T12:05:00+00:00",
    "updated_at": "2026-07-17T12:05:00+00:00",
    "eta_seconds": 45,
    "eta_at": "2026-07-17T12:05:45+00:00",
    "completed_chunks": 0,
    "total_chunks": 1,
    "progress_percent": 0
  }
}
```
- `total_tokens: 0` при создании — считаются только **завершённые** чанки, наполнится по мере обработки.
- `price_units` — заблокированная сумма (LMC-minor, деньги, НЕ токены); `0`, если покрыто квотой подписки.

**2. Дождаться готовности** — опрос `GET /orders/b2c3d4e5-…` (или WebSocket §10) до `status: "completed"`.

**Готовый заказ** — **ровно один** файл в `result.files[]` (строка-путь), поля `service_files`
у SFX нет вовсе:
```json
{
  "success": true,
  "message": "Order loaded successfully.",
  "data": {
    "id": "b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e",
    "task_type": "sfx",
    "status": "completed",
    "price_units": 0,
    "total_tokens": 1000,
    "task_data": {
      "prompt_influence": 0.4, "duration_seconds": 8, "loop": false,
      "output_format": "mp3_44100_128", "text": "Ocean waves crashing on rocks, distant seagulls"
    },
    "result": {
      "user_message": null,
      "files": [
        "storage/501/orders/b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e/output/chunks/0/result.mp3"
      ]
    },
    "created_at": "2026-07-17T12:05:00+00:00",
    "updated_at": "2026-07-17T12:05:40+00:00",
    "eta_seconds": null,
    "eta_at": null,
    "completed_chunks": 1,
    "total_chunks": 1,
    "progress_percent": 100,
    "items": [
      {
        "id": "a6b7c8d9-0e1f-4a2b-8c3d-4e5f6a7b8c9d",
        "item_type": "chunk", "chunk_index": 0, "status": "completed",
        "price_units": 1000, "consumed_units": 1000,
        "result_file": "storage/501/orders/b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e/output/chunks/0/result.mp3"
      }
    ]
  }
}
```
> `total_tokens: 1000` здесь **иллюстративно** — фактическая ставка задаётся сервисом (см. §7.7
> `usage` и §6.2 токен-квоту). Расширение файла — `mp3` или `opus` в зависимости от `output_format`.

**3. Скачать** — путь из `result.files[0]` (строка целиком) в `POST /storage/url` → `data.url` → `GET`:
```bash
curl -s -X POST "$BASE/storage/url" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "path": "storage/501/orders/b2c3d4e5-6f7a-4b8c-9d0e-1f2a3b4c5d6e/output/chunks/0/result.mp3" }'
```

### Рецепт C. Музыка (без шаблона) — полный цикл, `n_variants` → несколько файлов

Ключ описания у Music — **`prompt`**. Главная особенность: `n_variants` даёт **столько же
отдельных (несклеенных) файлов** в `result.files[]` и **умножает цену** (`n_variants ×` ставку).

**1. Создать заказ** (три варианта одного трека):
```bash
curl -s -X POST "$BASE/orders" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" -d '{
  "task_type": "music",
  "task_data": {
    "prompt": "Lo-fi hip hop, calm, rainy night, vinyl crackle",
    "n_variants": 3,
    "music_length_ms": 30000,
    "force_instrumental": true
  }
}'
```
**Ответ `201`** — `total_chunks` = `n_variants`; сервер домешал дефолты (`lyrics_text`, `model_id`)
в `task_data`:
```json
{
  "success": true,
  "message": "Order created successfully.",
  "data": {
    "id": "c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f",
    "user_id": 501,
    "template_id": null,
    "task_type": "music",
    "status": "created",
    "price_units": 0,
    "total_tokens": 0,
    "task_data": {
      "n_variants": 3,
      "lyrics_text": null,
      "force_instrumental": true,
      "music_length_ms": 30000,
      "model_id": "music_v2",
      "prompt": "Lo-fi hip hop, calm, rainy night, vinyl crackle"
    },
    "result": null,
    "created_at": "2026-07-17T12:10:00+00:00",
    "updated_at": "2026-07-17T12:10:00+00:00",
    "eta_seconds": 90,
    "eta_at": "2026-07-17T12:11:30+00:00",
    "completed_chunks": 0,
    "total_chunks": 3,
    "progress_percent": 0
  }
}
```

**2. Дождаться готовности** (`GET /orders/c7d8e9f0-…` до `completed`). **Готовый заказ —
`n_variants` файлов, по одному на вариант, НЕ склеены:**
```json
{
  "success": true,
  "message": "Order loaded successfully.",
  "data": {
    "id": "c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f",
    "task_type": "music",
    "status": "completed",
    "price_units": 0,
    "total_tokens": 6000,
    "task_data": {
      "n_variants": 3, "lyrics_text": null, "force_instrumental": true,
      "music_length_ms": 30000, "model_id": "music_v2",
      "prompt": "Lo-fi hip hop, calm, rainy night, vinyl crackle"
    },
    "result": {
      "user_message": null,
      "files": [
        "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/0/result.mp3",
        "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/1/result.mp3",
        "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/2/result.mp3"
      ]
    },
    "completed_chunks": 3,
    "total_chunks": 3,
    "progress_percent": 100,
    "items": [
      { "chunk_index": 0, "item_type": "chunk", "status": "completed", "result_file": "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/0/result.mp3" },
      { "chunk_index": 1, "item_type": "chunk", "status": "completed", "result_file": "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/1/result.mp3" },
      { "chunk_index": 2, "item_type": "chunk", "status": "completed", "result_file": "storage/501/orders/c7d8e9f0-1a2b-4c3d-8e4f-5a6b7c8d9e0f/output/chunks/2/result.mp3" }
    ]
  }
}
```
> `total_tokens: 6000` = `n_variants(3) ×` ставку (иллюстративно: 2000/вариант). При `n_variants=1`
> в `result.files[]` будет один файл. Если один из вариантов упадёт — заказ станет
> `partially_completed`, но `sfx`/`music` **не** доводятся item-retry (§7.11): отмените и создайте заново.

**3. Скачать все** — пройдитесь `POST /storage/url` по каждому пути из `result.files[]`
(строка целиком) → `data.url` → `GET` (см. рецепт E).

### Рецепт D. Проверить остаток токенов подписки
```bash
curl -s "$BASE/subscriptions" -H "X-API-KEY: $KEY"
# items[].tokens_remaining — сколько токенов ещё доступно
```

### Рецепт E. Скачать все файлы готового заказа
```bash
# 1) GET /orders/{id} → result.files[] (основные) + result.service_files[] (субтитры, TTS)
# 2) для каждого path (из files[] И из service_files[]): POST /storage/url { "path": path } → data.url
# 3) GET data.url → бинарник файла
```
`result.files[]` и `result.service_files[]` — строки-пути; кладите строку целиком в `path`. Если поля нет — субтитров
у этого заказа не сгенерировано (напр. `sfx`/`music`).

### Рецепт F. Дотянуть частично готовый заказ (`partially_completed`)

**Шаг 1. Все технические `failed` — одним вызовом.** Тело пустое.
```bash
curl -s -X POST "$BASE/orders/<ORDER>/items/retry-failed" -H "X-API-KEY: $KEY"
```
**Ответ `202`** — план принят, повтор идёт в фоне:
```json
{
  "success": true,
  "message": "Упавшие чанки поставлены на перезапуск",
  "data": {
    "queued_count": 2,
    "queued": [
      { "item_id": "9a1e4f60-2b77-4a1c-8d33-5c0e1b7a9f21", "chunk_index": 3 },
      { "item_id": "7c4b18ad-6e02-4f9b-91a7-0d2c5e8b3f14", "chunk_index": 5 }
    ],
    "skipped_count": 1,
    "skipped": [
      { "item_id": "1f2d90c4-3a15-4e6b-b8d2-7f4a6c1e0b93", "chunk_index": 7,
        "reason": "policy_flagged",
        "reason_label": "Чанк отклонён политикой контента — перезапустите его отдельно с исправленным текстом." }
    ]
  }
}
```
> `message` и `reason_label` приходят на языке аккаунта-владельца ключа (в примере — русский;
> у англоязычного аккаунта будут `Failed chunks queued for retry` и `The chunk was rejected by
> content policy — retry it individually with corrected text.`). Тексты меняются при правке
> локализации — **никогда не разбирайте их программно**, для логики есть `reason`.
**Шаг 2. Каждый `skipped` с `reason: "policy_flagged"` — поштучно с исправленным текстом.**
```bash
curl -s "$BASE/orders/<ORDER>/items/<ITEM>/text" -H "X-API-KEY: $KEY"          # → data.text
curl -s -X POST "$BASE/orders/<ORDER>/items/<ITEM>/retry" -H "X-API-KEY: $KEY" \
  -H "Content-Type: application/json" -d '{ "text": "<исправленный текст>" }'
```
**Шаг 3. Дождаться результата.** Синхронного ответа у массового повтора нет: опрашивайте
`GET /orders/<ORDER>/items` (или `GET /orders/<ORDER>` до `status = completed`), либо слушайте
WebSocket — `bulk_retry.completed` придёт с `created_count`/`failed_count` (§10).
Лимит попыток — 5 на чанк; исчерпанные вернутся в `skipped` с `max_attempts_reached`.

### Рецепт G. Квоты подписки не хватило — PAYG-добор (что делать при `402 payg_topup_required`)

Подписка даёт месячный лимит токенов. Если заказ стоит **больше**, чем осталось в квоте, и сервис
разрешает pay-as-you-go, сервер **не создаёт заказ сразу**, а возвращает **402** с ценой добора —
вы досогласовываете доплату недостающей части из LMC-баланса. Это не ошибка ключа, а развилка оплаты.

```bash
# 1) Обычный POST /orders (без confirm). Квоты не хватило → сервер вернёт 402:
curl -s -X POST "$BASE/orders" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "template_id": "<UUID>", "input_text": "…" }'
# HTTP 402, тело:
# {
#   "success": false,
#   "reason": "payg_topup_required",
#   "message": "…",
#   "shortfall_tokens": 1200,           # токенов сверх остатка квоты — их и доплачиваете
#   "shortfall_lmc": 3.60,              # цена доплаты (LMC, human-формат)
#   "shortfall_lmc_minor": 360000000,   # та же цена в minor-units LMC
#   "quote_token": "<QUOTE>",           # подписанная квота цены — верните её как есть
#   "expires_at": "2026-07-25T12:34:56+00:00"  # до этого момента цена гарантирована
# }

# 2a) СОГЛАСНЫ доплатить → повторите ТОТ ЖЕ запрос + confirm_payg_topup + quote_token:
curl -s -X POST "$BASE/orders" -H "X-API-KEY: $KEY" -H "Content-Type: application/json" \
  -d '{ "template_id": "<UUID>", "input_text": "…",
        "confirm_payg_topup": true, "quote_token": "<QUOTE>" }'
# → 201: shortfall_lmc списан из LMC-баланса, заказ создан.

# 2b) НЕ хотите платить → просто НЕ повторяйте. Заказ не создан, ничего не списано.
```

Важное:
- **Токен истёк** (после `expires_at`) или квота изменилась между запросами → повторный запрос отдаст
  **свежий 402** с новым `quote_token`; используйте новый.
- **Цена выросла** относительно согласованной → **409** `quote_mismatch` → запросите 402 заново (пошлите
  без `confirm_payg_topup`) и подтвердите новую цену.
- **Доступность PAYG зависит от сервиса.** Если у сервиса PAYG отключён (`Service.payg_access = disabled`),
  402 **не будет** — заказ откажет с `insufficient_allowance`/`no_subscription` (см. §12), доплатить нельзя.
  Политику видно в `Service.payg_access`: `everyone` (PAYG всем) / `subscribers_only` (добор только
  подписчикам) / `disabled` (только из квоты).
- Итог по деньгам виден в заказе: `tokens_from_quota` (бесплатно из квоты) + `tokens_from_payg`
  (оплачено доплатой) — см. §9 `Order`.
- Тот же флоу у `POST /orders/{order}/retry` (retry идёт через тот же биллинг).

---

## 12. Коды ответов (сводка)

| Код | Когда | Тело |
| --- | --- | --- |
| 200 | Успех чтения/действия | `{ success:true, message, data }` |
| 201 | Ресурс создан (order/template/item) | `{ success:true, message, data }` |
| 400 | Ошибка домена (недопустимое действие) | `{ success:false, message }` |
| 401 | Нет/невалидный/истёкший ключ | `{ success:false, message, data:null }` |
| 402 | `payg_topup_required` — не хватает квоты подписки, нужен добор | `{ success:false, message, reason:"payg_topup_required", shortfall_tokens, shortfall_lmc, shortfall_lmc_minor, quote_token, expires_at }` — повторите `POST /orders` с `confirm_payg_topup:true` и тем же `quote_token` |
| 403 | Нет права ключа / чужой ресурс | `{ success:false, message, data:null }` |
| 404 | Ресурс не найден / файл не найден | `{ success:false, message }` |
| 405 | Метод не поддержан (напр. `DELETE /orders/{id}`) | стандартный |
| 409 | `quote_mismatch` — добор подорожал, `quote_token` устарел (гонка) | `{ success:false, message, reason:"quote_mismatch", actual_lmc_minor, quoted_lmc_minor }` — запросите 402 заново |
| 422 | Ошибка валидации | `{ message, errors:{field:[...]} }` (без `success`) |
| 429 | Rate-limit **или** токен-квота — различайте по телу | rate: `{ success:false, message }`, **без** `Retry-After`; квота: `{ success:false, message, reason:"token_quota_exceeded", window, limit, used, requested, reset_at, retry_after }` + заголовок `Retry-After` |
| 500 | Временная инфраструктурная ошибка | `{ success:false, message }`, безопасный текст, повторить |
| 503 | Внешний ElevenLabs library недоступен | только `voices/elevenlabs/library` |

Коды **402** и **409** одинаково применимы к `POST /orders` **и** `POST /orders/{order}/retry` (retry
проходит тот же биллинг-flow).

**Доменный отказ `POST /orders` и `POST /orders/{order}/retry` (`OrderNotAllowedException`).**
Тело: `{ "success": false, "message": "...", "reason": "<машинный>" }`. HTTP-код зависит от `reason`:

| `reason` | HTTP | Смысл |
| --- | --- | --- |
| `insufficient_balance` / `insufficient_allowance` | 402 | Не хватает баланса/залоченного лимита (модель с авансом) |
| `no_subscription` / `entitlement_missing` / `template_access_denied` | 403 | Нет подписки/entitlement под сервис либо доступа к шаблону |
| `limit_exceeded` | 429 | **Превышен usage-лимит подписки** — третий вид 429 (см. §6): в теле только `message`+`reason`, **нет** полей токен-квоты и **нет** `Retry-After`. Сверьтесь с `GET /usage` |
| `concurrency_limit_exceeded` | 429 | **Исчерпаны слоты одновременных задач** — четвёртый вид 429 (см. §6). Ограничено не число запросов и не объём, а количество ваших заказов **в работе одновременно**. `Retry-After` нет. Ждать надо завершения СВОИХ активных заказов (минуты), а НЕ конца расчётного периода — повтор сразу после завершения любого из них пройдёт. Действующий потолок и текущее число активных — в `GET /usage` (запись с `limit_type: "concurrent"`; у неё `period_start`/`period_end`/`resets_in` = `null`, потому что периода у такого лимита нет). Лимит может быть задан как на все сервисы сразу, так и отдельно на каждый |
| `content_blocked` | 422 | **Текст уже отбивался контентной политикой сервиса** и лежит в глобальном блоклисте. Повторять бессмысленно: исход будет тот же, а попытка снова стоит денег. Единственное действие — отредактировать спорные фрагменты. Проверка идёт по ЧАНКАМ (точное совпадение текста фрагмента), поэтому в длинном тексте достаточно поправить один спорный кусок. Тот же `reason` отдают повтор и перегенерация чанка. **Узнать заранее по API-ключу нельзя:** dry-run разбиения на чанки (`POST /orders/chunks/preview`) — эндпоинт веб-кабинета (JWT), в публичный контур он не входит и по ключу даст 401. Единственная стратегия — обработать 422 при создании заказа |
| `policy_cooldown_active` | 429 | **Кулдаун после серии блокировок по политике** — пятый вид 429 (см. §6). Пять подряд заблокированных политикой задач ограничивают создание новых на 5 минут, при повторных залётах — 10, затем 15 (выше не поднимается). В отличие от прочих 429 заказа, здесь **есть** `Retry-After` и поля `retry_after` (секунды) / `retry_at` (ISO-8601). Сбои инфраструктуры (таймаут воркера, сеть) счётчик не двигают — считаются только контентные блокировки. Любая успешно выполненная задача обнуляет и счётчик, и достигнутую ступень |
| `service_not_found` / `not_allowed` | 422 | Сервис не найден/не активен либо прочий доменный отказ |

---

## 13. FAQ / частые ошибки для LLM

- **«Как прикрепить голос к заказу?»** — Никак напрямую. Голос → в шаблон
  (`config.tts_settings.voice_id`) → заказ по шаблону. Поля `orders.voice_id` не существует.
- **«Куда положить текст TTS?»** — В top-level `input_text` запроса `POST /orders`. Не в `task_data`.
- **«Как создать SFX/Music и что придёт в ответе?»** — Без шаблона: `POST /orders` с
  `task_type: "sfx"` и `task_data.text` (описание звука) **или** `task_type: "music"` и
  `task_data.prompt`. Ответ `201` — заказ со `status: "created"` (сервер домешивает дефолты в
  `task_data`); файлы появятся после обработки в `result.files[]` (SFX — **1** файл; Music —
  **`n_variants`** файлов, по одному на вариант, не склеены). Полные примеры запрос+ответ —
  §11-B (SFX) и §11-C (Music), поля — §8.1. Ключ описания: у SFX `text`, у Music `prompt`.
- **«Можно ли создать `image`/`txt2img`/`remix`/`image_edit`/`voice_clone` по API-ключу?»** — Нет.
  Через публичный `POST /orders` создаются `tts`, `sfx`, `music` и `voice_search` (§5). Остальные
  значения — лишь для фильтра `GET /orders?task_type[]=…`. Технически: они проходят валидацию
  запроса (в enum'е), но падают на резолве сервиса — сервиса с таким кодом не существует — и
  дают **422** `service_not_found`. Не отправляйте их.
- **«А `voice_search` создаётся?»** — Да, с 27.07.2026 (раньше — нет, и старые версии этого
  справочника утверждали обратное). Это template-less тип: аудиофрагмент в `input_files`,
  опционально `task_data.top`. Подробности и ограничения — §5.
- **«Как долго доступны файлы готового заказа?»** — Ограниченное время: storage заказа удаляется по
  ретенции (**по умолчанию 7 дней** после перехода в терминальный статус — `completed`/`result_delivered`/
  `failed`/`compensated`/`cancelled`). После удаления `POST /storage/url` по этим путям → **404**. Нюанс:
  `GET /orders/{order}` после истечения **всё ещё показывает старые пути** в `result.files[]` — они уже
  «мёртвые», отдельного признака истечения в пользовательском ответе нет (виден только 404 при скачивании).
  Запись заказа в БД сохраняется. **Скачивайте результаты сразу после `completed`, не откладывайте.**
- **«Работает ли курсорная пагинация (`?pag=cursor`)?»** — В публичном API **нет**. Параметр проходит
  валидацию (не даёт 422), но **молча игнорируется** — списки всегда отдают offset-форму
  `{ items, current_page, last_page, per_page, total }`. Листайте через `page`/`per_page`; поля
  `next_cursor` здесь нет.
- **«Получил `402 payg_topup_required` при создании заказа — что это и что делать?»** — Квоты вашей
  подписки не хватило на этот заказ, а сервис разрешает pay-as-you-go: сервер предлагает **доплатить**
  недостающую часть из LMC-баланса (это НЕ ошибка ключа и НЕ 429). В теле 402: `shortfall_tokens`
  (сколько токенов сверх квоты), `shortfall_lmc` (цена доплаты), `quote_token` + `expires_at` (до
  когда цена валидна). **Что делать:** согласны — повторите **тот же** `POST /orders` с
  `confirm_payg_topup: true` и тем же `quote_token` → 201, доплата спишется из LMC. Не согласны —
  просто не повторяйте (ничего не списано). Токен истёк / квота изменилась → повторный запрос даст
  свежий 402 с новым `quote_token`; цена выросла → 409 `quote_mismatch` (запросите 402 заново).
  Полный пример — **Рецепт G (§11)**. Если сервис PAYG отключил (`Service.payg_access = disabled`),
  402 не будет — заказ откажет с `insufficient_allowance` (§12), доплатить нельзя.
- **«Как узнать, спишется ли доплата PAYG заранее?»** — Оценка идёт при создании: если квоты хватает —
  заказ сразу `201` без 402. Разбивка по факту — в заказе: `tokens_from_quota` (бесплатно из квоты) и
  `tokens_from_payg` (оплачено доплатой), см. §9 `Order`. Остаток квоты — `GET /subscriptions`
  (`items[].tokens_remaining`, Рецепт D).
- **«Почему пустой список голосов ElevenLabs?»** — `page` начинается **с 0**. `page=1` — это вторая страница.
- **«Заказ создан, где файл?»** — Дождитесь `status = completed`/`partially_completed`, возьмите
  `result.files[]` (строка-путь), обменяйте на ссылку через `POST /storage/url`.
- **«Обновить/удалить заказ?»** — Нельзя. Только `cancel` (до обработки) или `retry` (создаёт новый).
- **«429 — что делать?»** — Если тело несёт `reason: token_quota_exceeded` — исчерпана квота по
  токенам, ждать до `reset_at` (заголовок `Retry-After` тоже есть). Иначе это rate-limit —
  `Retry-After` здесь НЕ присылается, ориентируйтесь на окно ключа (`per_minute`/`per_hour`/`per_day`)
  и делайте backoff самостоятельно.
- **«Какие поля config у шаблона?»** — Спросите `GET /templates/config-options?service=<code>`;
  структура зависит от сервиса.
- **«403 на, казалось бы, доступном эндпоинте?»** — У ключа нет нужного права. Скачивание требует
  отдельного `orders.download`, каталоги голосов — `voices.read`, профиль — `profile.read`.
- **«Где субтитры (.srt/.vtt) заказа?»** — В отдельном поле `result.service_files[]` (не в
  `result.files[]`), только для TTS. Это массив строк-путей; скачивайте их тем же `POST /storage/url`.
  См. §7.10.
- **«Чанк заказа `failed`/`policy_flagged` — что делать?»** — Заказ стал `partially_completed`.
  Сначала один вызов `POST /orders/{order}/items/retry-failed` — он поставит на повтор все
  технические `failed` сразу и вернёт `202` со списком `skipped`. Затем каждый пропущенный с
  `reason: "policy_flagged"` добейте поштучно `POST /orders/{order}/items/{item}/retry`,
  передав **исправленный** `text` (тот же текст отклонят снова). Order-level `retry` здесь не
  поможет. См. §7.11 и Рецепт F.
- **«Массовый повтор вернул 202, но в ответе нет готовых чанков — где результат?»** — Его там и не
  будет: `202` означает «план принят», повтор выполняется в фоне. Тело несёт только `queued`
  (что поставлено) и `skipped` (что и почему пропущено). Готовность узнавайте опросом
  `GET /orders/{order}/items` или событием `bulk_retry.completed` по WebSocket (§10).
- **«API не говорит, почему сработала policy — где причина?»** — Её нет в ответе по ключу (это
  admin-only). Правьте текст по своему усмотрению; не ищите поле с reason — его не отдают.
- **«Заказ `failed`, но `result: null` — где детали ошибки?»** — Их нет в пользовательском контексте
  (отфильтрованы). Ориентируйтесь на `status` заказа и статусы чанков (`failed`/`policy_flagged`).
- **«`can_retry: true`, но retry вернул 400?»** — Флаг смотрит только на статус чанка. Реальный вызов
  ещё требует: заказ `partially_completed`, попыток < 5, остался залоченный остаток, тип не `sfx`/`music`.
