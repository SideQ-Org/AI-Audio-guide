# Accounts & Walk History — Design (draft)

> Локальный проектный документ. **Не имплементация** — план, который согласуем, прежде чем
> писать код. Цель: спроектировать аккаунты (Google / Apple / email), durable-хранилище и
> историю прогулок так, чтобы **не сломать** текущую sid-модель, resume и гость-режим.
> Milestone по CONTINUE.md §0c — это **пред-прод**, последний перед запуском.

---

## 1. Цели и не-цели

**Цели:**
- Вход через **Google**, **Apple** (обязателен на iOS при наличии стороннего входа — правило App
  Store), **email + пароль**.
- При наличии аккаунта — **сохранение истории прогулок** (маршрут, озвученные объекты, дата,
  город, язык), просмотр и повтор.
- **Гость-режим остаётся**: без аккаунта приложением можно пользоваться, но история не сохраняется.
- Ничего не ломать в агент-логике, WS-контракте, sid-resume, фоновом режиме.

**Не-цели (сейчас):**
- Соцфичи (шеринг, друзья, лайки), платёжная интеграция подписок (это отдельный
  monetization-milestone; здесь только *крючок* — история как paid-фича).
- Синхронизация между устройствами сверх «залогинься и увидишь свои прогулки».
- Серверный ретеншн аудио (храним текст нарраций, не звук).

---

## 2. Ключевое решение №1 — где живёт identity — ✅ РЕШЕНО: **A (Supabase)**

> **Выбрано (2026-07-01): вариант A — Supabase.** Managed Auth (Google/Apple/email, JWT) +
> Postgres одним сервисом. Ниже таблица оставлена для истории решения; вся дальнейшая
> конкретика — по Supabase. Ветки `[DIY]` в документе неактуальны (оставлены серым как
> справка на случай пересмотра).

Вход через Google/Apple/email — это самая дорогая по времени и по граблям часть (OAuth-редиректы,
Apple review, верификация email, сброс пароля, ротация ключей). Три пути:

| | A. Managed (Supabase) — **рекомендую** | B. Firebase Auth + свой Postgres | C. Полный DIY (FastAPI-Users) |
|---|---|---|---|
| Identity | Supabase Auth: Google/Apple/email из коробки, JWT | Firebase Auth (Google/Apple/email) | Сами: OAuth flows + пароли |
| БД истории | Supabase Postgres (тот же сервис) | свой Postgres | свой Postgres |
| Бэкенд-работа | Проверять JWT (JWKS), писать историю | Проверять Firebase-токен, писать историю | Всё сами: хэш паролей, JWT, OAuth verify, письма |
| Флаттер SDK | `supabase_flutter` (готовые кнопки) | `firebase_auth` | руками (`google_sign_in`, `sign_in_with_apple`) |
| Вендор-лок | средний (но Postgres переносим) | высокий (Firebase-специфично) | нет |
| Время до рабочего | **1–2 дня** | 2–3 дня | 5–8 дней |
| Стоимость | free-tier щедрый, потом $25/мес | free-tier, потом по нагрузке | только свой хостинг |

**Рекомендация: A (Supabase)** для MVP→прод: закрывает и identity, и durable-БД одним сервисом,
даёт готовый Flutter-UI и Apple Sign-In (иначе — самая болезненная часть). Postgres остаётся
стандартным, миграция на свой инстанс потом не переписывает приложение. **Если критичен полный
контроль/отсутствие вендора** — C, но это +неделя и своя головная боль с Apple review и письмами.

> Остальной документ написан **провайдер-нейтрально** там, где можно, и помечает `[Supabase]` /
> `[DIY]`, где детали расходятся. Финализируем после выбора A/B/C.

---

## 3. Хранилище: два разных слоя (не путать)

Сейчас `state/store.py` хранит **`SessionState`** — эфемерное состояние живой прогулки (seen-list,
history нарраций, area-arc, FSM), in-memory/Redis, **с TTL-эвикцией**. Это осознанно недолговечно.

Аккаунты и история требуют **durable**-слоя — он **отдельный**, не заменяет session store:

| Слой | Что | Где | Живёт |
|---|---|---|---|
| **Session store** (есть) | живое состояние прогулки | in-memory / Redis | минуты–часы, TTL |
| **Durable store** (новый) | users, walks, walk_events | **Postgres** | навсегда |

`SessionState` **не переносим** в Postgres целиком — он большой, волатильный и не нужен после
прогулки. В историю мы пишем **дистиллят** (см. §5): что реально стоит показать пользователю.

---

## 4. Модель данных (Postgres)

```
users
  id            uuid  pk
  email         text  unique null            -- null для чисто-OAuth без email
  display_name  text  null
  created_at    timestamptz
  -- пароль НЕ здесь при [Supabase]/[Firebase]; при [DIY]: password_hash text (argon2id)

identities                                    -- связки внешних провайдеров
  id            uuid  pk
  user_id       uuid  fk users
  provider      text  ('google'|'apple'|'email')
  provider_uid  text                          -- sub из OAuth / email
  unique (provider, provider_uid)

walks                                         -- одна прогулка = один "запуск тура"
  id            uuid  pk
  user_id       uuid  fk users
  sid           text                          -- связь с живой WS-сессией (см. §6)
  started_at    timestamptz
  ended_at      timestamptz null
  language      text
  city          text  null                    -- из resolved address
  district      text  null
  distance_m    int   null                    -- накопленная дистанция (клиент/сервер)
  object_count  int   default 0
  title         text  null                    -- авто: "Прогулка по Долгопрудному, 1 июля"

walk_events                                   -- озвученные объекты в порядке тура
  id            uuid  pk
  walk_id       uuid  fk walks
  seq           int                           -- порядок
  place_id      text                          -- backend object id
  name          text
  category      text
  lat, lon      double
  significance  text  (LOW..LANDMARK)
  narration     text  null                    -- текст, что реально сказали (для повтора)
  said_at       timestamptz
```

Индексы: `walks(user_id, started_at desc)`, `walk_events(walk_id, seq)`.
`[Supabase]` — плюс **Row-Level Security**: пользователь видит только свои `walks`/`walk_events`.

---

## 5. Что такое «прогулка» и когда пишем историю

**Определение:** прогулка = период между «Старт» и «Стоп/Пауза-таймаут» в клиенте. Один `sid`
может породить **несколько** прогулок (сегодня утром и вечером); одна прогулка может пережить
несколько WS-реконнектов (та же `sid`-сессия).

**Триггеры записи (сервер-сайд, из оркестратора — единственный источник правды о нарративе):**
- **start walk** — при первом `position` после старта, если у сессии есть `user_id` → `INSERT walks`.
- **append event** — каждый раз, когда оркестратор реально **произнёс** объект (в `pipeline.step`
  на успешной наррации / floor-mention) → `INSERT walk_events`. Это та же точка, где уже стоит
  `aiguide.agent` decision-log — крючок готов.
- **end walk** — на `_stopWalk`/долгом простое → `UPDATE walks.ended_at, title, distance_m`.

**Гость (нет `user_id`):** ничего не пишем. Ноль изменений в горячем пути — просто ветка `if
session.user_id`. Запись — **fire-and-forget** (не блокирует наррацию; ошибка БД логируется, тур
продолжается).

---

## 6. Привязка sid ↔ user_id (сердце интеграции)

Сегодня: `?sid=<32 симв.>` → ключ `SessionState`. Гость-режим — это и есть «sid без user_id».

**Аутентификация WS-подключения:**
- Клиент после логина хранит **access-token** (JWT) в secure storage.
- При коннекте передаёт его: `wss://…/ws?sid=<sid>&token=<jwt>`  *(или* первым сообщением
  `{"type":"auth","token":…}` — предпочтительнее, чтобы токен не светился в логах прокси; решим).
- Бэкенд **валидирует** JWT (`[Supabase]` — по JWKS/секрету проекта; `[DIY]` — своей подписью),
  достаёт `user_id`, кладёт в `SessionState.user_id` (**новое поле**, `str | None = None`).
- Невалидный/просроченный токен → работаем как **гость** (не рвём сокет — деградация, не отказ).

**Новое поле в схеме:** `SessionState.user_id: str | None = None`. Всё. Forward-compatible, как и
обещано в MVP_PITCH — контракт не ломается, старые клиенты (без токена) = гости.

> ВАЖНО: не путать с существующим `WS_TOKEN` (общий shared-secret доступа к эндпоинту). Это
> ортогонально: `WS_TOKEN` — «пускать ли вообще», `user token` — «кто ты». Оба могут
> сосуществовать.

---

## 7. REST-поверхность (новая, рядом с WS)

WS остаётся для тура. Аккаунты/история — обычный REST (проще кэшировать, тестировать, отдавать в UI):

```
[Supabase] — логин/регистрация делает Supabase SDK на клиенте; бэкенду нужны только:
  GET  /me                      -> профиль (по Bearer JWT)
  GET  /walks?limit=&cursor=    -> список прогулок пользователя
  GET  /walks/{id}              -> прогулка + walk_events (для экрана деталей/повтора)
  DELETE /walks/{id}            -> удалить прогулку (право на забвение)

[DIY] — добавить ещё:
  POST /auth/register           (email+пароль, argon2id)
  POST /auth/login              -> JWT
  POST /auth/oauth/google       (verify id_token, upsert identity)
  POST /auth/oauth/apple        (verify identity token)
  POST /auth/refresh, /logout, /auth/password-reset
```

Все — под тем же FastAPI-приложением; auth-зависимость `Depends(current_user)` валидирует Bearer.

---

## 8. Клиент (Flutter)

- **Login-экран** (каркас UX уже заложен): кнопки `Continue with Google` / `Continue with Apple`
  (iOS) / `Email` + `Continue as guest`. Локализация — добавить ключи в 8 `app_*.arb`.
- **Хранение токена:** `flutter_secure_storage` (Keychain/Keystore), **не** SharedPreferences.
- **Гость → логин без потери прогулки:** при логине во время тура — до-привязать текущий `sid`
  к `user_id` (сервер: `UPDATE walks SET user_id WHERE sid=… AND user_id IS NULL` за период сессии).
- **Экран «История»:** список из `GET /walks`, тап → детали (`walk_events` на карте + тексты),
  кнопка «пройти заново» (проиграть сохранённые нар„ации TTS без сети — приятная paid-фича).
- Apple Sign-In: нужен entitlement + capability в Xcode (документируем в `mobile/README.md`).

---

## 9. Безопасность (связка с security-posture)

- Пароли `[DIY]`: **argon2id** (или bcrypt), никогда plaintext; `[Supabase]` — их забота.
- JWT: короткий access (~1 ч) + refresh; подпись проверяем на каждом REST/WS.
- **RLS** `[Supabase]` или явная проверка `walk.user_id == current_user` `[DIY]` на каждом
  чтении — чтобы нельзя было прочитать чужую историю по id.
- Транспорт: прод уже `wss`/TLS. Токен — первым WS-сообщением, а не в query (не течёт в
  access-логи Caddy, которые включены).
- Rate-limit на `/auth/*` (брутфорс) — добавить в пред-проде вместе с остальным H-тиром.
- Право на забвение: `DELETE /walks/{id}` + удаление аккаунта (каскад) — база под GDPR-запросы.
- Секреты (Supabase service key / JWT signing key) — в прод `.env`, **не** в репо (как сейчас
  ключ OpenRouter).

---

## 9a. Конкретика Supabase (выбранный путь)

**Разделение труда:**
- **Supabase делает:** регистрацию/логин (Google/Apple/email), выдачу JWT, refresh, сброс пароля,
  подтверждение email, хранение таблиц, RLS. Клиент общается с Auth напрямую через `supabase_flutter`.
- **Наш FastAPI-бэкенд делает:** (1) **валидирует** пришедший JWT, (2) пишет историю прогулок,
  (3) отдаёт `/me`, `/walks`. Бэкенд — доверенный писатель истории (у него service-role ключ).

**Валидация JWT на бэкенде** — два подхода, выбрать при реализации:
- **JWKS (asymmetric, реко)** — Supabase перешёл на подписи ECC/RSA; бэкенд тянет публичный
  JWKS проекта, проверяет подпись/`aud`/`exp` локально (быстро, без сети на каждый запрос,
  ключ кэшируется). Пакет: `pyjwt[crypto]` + маленький JWKS-кэш.
- **legacy HS256** — общий `JWT secret` проекта; проще, но секрет на бэкенде. Ок для старта.

**Запись истории** — бэкенд ходит в тот же Postgres. Два варианта клиента:
- **PostgREST** (`GET/POST` в REST Supabase со service-role ключом) — ноль новых зависимостей БД.
- **Прямой Postgres** (`asyncpg`/SQLAlchemy на connection string проекта) — привычнее для миграций
  (Alembic), сложные вставки. *Реко: прямой Postgres для записи истории + Alembic-миграции.*

**Секреты (в прод `.env`, не в репо — как ключ OpenRouter):**
```
SUPABASE_URL=...                 # https://<proj>.supabase.co
SUPABASE_JWKS_URL=.../auth/v1/keys   # для JWKS-валидации
SUPABASE_JWT_AUD=authenticated
SUPABASE_DB_URL=postgresql://...     # прямое подключение для записи истории
# (клиентские anon/publishable ключи живут в Flutter, не на бэкенде)
```

**Локальная разработка без облака:** `supabase` CLI поднимает **локальный стек в docker**
(Postgres + Auth) — те же схемы/RLS, что в проде. Значит фазы 2–5 можно строить и тестировать
**локально**, без облачного проекта; облако подключаем на фазе 7 (реальные Google/Apple консоли).

**RLS-политики** (Postgres): на `walks`/`walk_events` — `user_id = auth.uid()` для select/delete;
insert истории делает бэкенд под service-role (RLS его не ограничивает). Клиент читает свою историю
либо через наш `/walks` (бэкенд с service-role + фильтр по user_id из JWT), либо напрямую из
Supabase под RLS — *реко: через наш `/walks`*, чтобы вся авторизация была в одном месте.

---

## 10. План внедрения (фазами, ничего не ломая)

1. ~~Решение A/B/C~~ — ✅ **Supabase.**
2. ~~**Durable-слой**: Alembic-миграции, таблицы §4 + RLS~~ — ✅ **СДЕЛАНО (2026-07-02).**
   `app/services/accounts/` (models/db/repository), `alembic/` (миграция 0001, upgrade+downgrade
   валидны), `db/rls.sql`, `db/README.md` (dev-runbook под supabase CLI), extra `accounts` в
   pyproject, `DATABASE_URL` в config. 5 тестов репозитория (SQLite, изоляция+cascade) —
   офлайн-гейт 111 passed. Ничего не записывается и не подключено к туру: чистая инфраструктура.
3. ~~**Session ↔ user_id**: `SessionState.user_id`, валидация токена на WS-коннекте~~ —
   ✅ **СДЕЛАНО (2026-07-02).** `SessionState.user_id: str|None`, входящее `auth`-сообщение
   (`WSAuth`), `accounts/auth.py` (`verify_token`: JWKS→HS256, деградация в `None`), ветка `auth`
   в `_dispatch` (валидация в thread, bind в сессию, ack `{authenticated}`). Конфиг:
   `supabase_jwks_url/jwt_secret/jwt_aud`. Выбор §11.2 — токен сообщением; §11.3 — JWKS (+HS256).
   10 тестов (unit verify_token: valid/expired/wrong-secret/wrong-aud/no-sub/disabled; WS: bind/
   degrade-to-guest/no-auth-still-works). Гейт 121 passed. Агент-логика не тронута.
4. ~~**Запись истории**: врезка в оркестратор (start/append/end, §5), fire-and-forget~~ —
   ✅ **СДЕЛАНО (2026-07-02).** `accounts/history.py` (`record_object`): синхронно решает
   новая-прогулка/продолжение и стемпит `SessionState.walk_id`/`walk_last_event_at` (сохраняются
   штатным save оркестратора), DB-I/O — detached task с заранее сгенерированным `walk_id`, ошибки
   логируются и глотаются. Хук — в narrate-step точке (рядом с `GUIDE.narrate`), только свежие
   объекты + floor (не элаборации/area-биты). Гость/`database_url` пусто → sqlalchemy даже не
   импортируется (ленивый импорт за guard). Юзер материализуется лениво при первой прогулке
   (`get_or_create_user`, id из JWT sub). 5 тестов (write/guest/disabled/gap-split/оркестратор-хук),
   гейт 126 passed.
   **MVP-трактовки:** (а) прогулка стартует лениво на первом *озвученном объекте* (не на первом
   `position`) — нет пустых прогулок; (б) `ended_at` = время последнего объекта (нет явного «стоп»-
   сигнала от клиента); (в) `walk_gap_s`=1800с разбивает утро/вечер на одном sid на 2 прогулки;
   (г) `distance_m` пока null — §11.6, клиент дошлёт позже.
5. ~~**REST /me + /walks**: чтение истории под auth~~ — ✅ **СДЕЛАНО (2026-07-02).**
   `accounts/api.py` (`APIRouter`): `GET /me`, `GET /walks?limit=&cursor=` (keyset-пагинация),
   `GET /walks/{id}` (+ events), `DELETE /walks/{id}` (204). Зависимость `current_user` тянет
   `Bearer`-JWT → `verify_token` (в thread) → 401 при невалидном. Изоляция: все запросы фильтруют
   по `user_id` из токена (repository уже это делает). Import-safe: топ api.py — только FastAPI/
   pydantic/`verify_token`; db/repository — лениво в хендлерах (503 если store выключен), базовая
   установка sqlalchemy при бутe НЕ грузит (проверено). 5 тестов (/me, изоляция списка, детали+
   ownership 404, delete+404-не-владельцу, 401 без/с плохим токеном). Гейт 131 passed.
6. ~~**Клиент**: login-экран, гость-режим, экран истории~~ — ✅ **СДЕЛАНО (2026-07-02).**
   `mobile/lib/accounts/`: `accounts_config.dart` (dart-define SUPABASE_URL/ANON_KEY; пусто →
   accounts OFF, гость как сейчас), `auth_service.dart` (supabase_flutter: Google/Apple OAuth +
   email/пароль, ChangeNotifier), `api_client.dart`+`models.dart` (REST /me,/walks через http+Bearer),
   `login_screen.dart`, `walk_detail_screen.dart` (офлайн-переозвучка нарраций через flutter_tts).
   `walk_history_screen.dart` переписан: gate вход→список→детали→удаление. В `main.dart`: init Supabase
   (guarded), `_sendAuth()` при коннекте (токен первым делом после language/theme), listener на
   auth-изменения (live bind/unbind), секция аккаунта в настройках. Ключи локализации (+21×8 языков).
   `flutter analyze` чист, widget-тест зелёный. deps: supabase_flutter, http. **Инструкция по проекту/
   ключам — `SUPABASE_SETUP.md`.** До-привязка sid при логине-в-процессе: покрыта тем, что `_sendAuth`
   шлётся live по auth-change → бэкенд биндит user_id в текущую resumable-сессию (новая прогулка со
   след. объекта). Secure storage — supabase_flutter хранит сессию сам; свой secure-адаптер отложен в §8.
7. **Apple/Google настройка**: консоли провайдеров, Xcode capability, редирект-URI. Прогон на
   реальном устройстве.
8. **Пред-прод хардненинг**: rate-limit auth, ротация ключей, удаление аккаунта.

Каждая фаза — отдельный коммит с зелёным офлайн-гейтом; гость-путь и агент-логика не трогаются.

---

## 11. Открытые вопросы (согласовать перед кодом)

1. ~~A / B / C (§2)~~ — ✅ **РЕШЕНО: Supabase.**
2. **Токен в WS**: query `?token=` (проще) vs первым сообщением `auth` (безопаснее логов). *Реко: сообщением.*
3. **JWT-валидация**: JWKS (реко) vs legacy HS256 (§9a).
4. **Запись истории**: прямой Postgres+Alembic (реко) vs PostgREST (§9a).
5. **Dev-БД**: `supabase` CLI (локальный стек, те же схемы) vs просто локальный Postgres в docker. *Реко: supabase CLI.*
6. **Где считать distance_m** — клиент (есть GPS-трек) или сервер (из позиций). *Реко: клиент шлёт в end.*
7. **Хранить ли тексты нарраций** в `walk_events.narration` (плюс: повтор офлайн; минус: объём). *Реко: да, это ценность истории.*
8. **Удаление аккаунта** в MVP-объёме или отложить в пред-прод-хардненинг? *Реко: DELETE /walks сразу, удаление аккаунта — фаза 8.*

---

*Прогресс: §11.1 (Supabase) ✅, фазы 2–6 ✅ (2026-07-02). **Весь бэкенд (131 offline-тест) и клиент
(flutter analyze чист, widget-тест зелёный) готовы; фича спит без ключей.** Следующий шаг — фаза 7:
пользователь заводит Supabase-проект по `SUPABASE_SETUP.md` (проект, ключи, миграция+RLS, провайдеры,
deep-links для Google/Apple), после чего логин работает end-to-end. Затем фаза 8 (хардненинг:
rate-limit /auth, удаление аккаунта, secure-storage-адаптер).*
