# Архитектура процесса — блок-схема

Как устроен «мозг» аудиогида: один **stateful-оркестратор** гоняет непрерывный цикл и владеет всем
состоянием сессии (FSM, seen-list, история, память). Вокруг — **stateless LLM-роли** и **сервисы**.
Роли не общаются между собой, только через `SessionState`, который передаёт оркестратор.

Диаграммы ниже — на [Mermaid](https://mermaid.js.org) (рендерится на GitHub и в большинстве
Markdown-просмотрщиков).

> Схемы описывают **реактивный** тик («свободная прогулка»). Проактивный режим «Проведи меня»
> (guided) идёт отдельной веткой `orchestrator._guided_tick` (ведение по заранее спланированному
> маршруту + единая арка повествования), а выравнивание трека — отдельным map-matching-каналом.
> Их устройство см. в `CLAUDE.md` (разделы «Guided mode» и «GPS track alignment»).

---

## 1. Цикл на один тик (позиция → рассказ)

```mermaid
flowchart TD
    GPS["📍 GPS + курс + темп<br/>(телефон, WebSocket)"] --> DISC

    subgraph AGENT["Агент — один тик (pipeline.py)"]
        DISC["1 · Поиск объектов<br/>OSM Overpass, радиус N<br/>кэш inventory"] --> PERSIST
        PERSIST["2 · Сохранение + адрес<br/>страна / город / район / улица"] --> ENRICH
        ENRICH["3 · Обогащение + значимость<br/>Wikipedia→Wikidata (free)<br/>→ веб-серч fallback (~5-7с)<br/>SKIP·LOW·MED·HIGH·LANDMARK"] --> DIR
        DIR["4 · Директор — СТРУКТУРА<br/>callback · look-ahead<br/>revisit · дедуп фактов<br/>(director.py, детерминированно)"] --> SCORE
        SCORE["5 · Scorer<br/>ранжирует, выбирает<br/>след. место, expand_radius"] --> NARR
        NARR["6 · Narrator<br/>пишет SUMMARY + блок CARD<br/>(значимость → объём)"]
    end

    NARR --> SCHED
    SCHED["NarrationScheduler<br/>выдаёт ПО ОДНОМУ предложению<br/>weave на границах · park/resume"] --> WS
    WS(["WebSocket /ws"]) --> TTS["🔊 Клиентский TTS<br/>flutter_tts (free)<br/>/ нейро audio_b64 (paid)"]
    TTS -->|"ack played (темп)"| SCHED

    GPS -.->|"position: обновляет контекст"| SCHED
    GPS -.->|"новый объект в bubble → peek_bubble"| SCHED
```

**Ключевое:**
- Доставка **по предложению** (`narration_schedule.py`) — новый объект вплетается на границе
  предложения, а не в середине слова; прерванная линия паркуется и **возобновляется** позже.
- `played`-ack задаёт темп: сервер отдаёт следующее предложение, пока текущее ещё играет (буфер в
  1 предложение) — так убирается пауза между фразами.
- Прогрев: рассказ про объект впереди и следующий area-beat **пред-генерируются** в фоне
  (`warm_narration`/`prefetch_area`), чтобы холодная задержка LLM (5-20с) была спрятана за
  проговоркой.

---

## 2. Лестница «затишья» — почему гид не молчит

Когда рядом нет нового объекта, оркестратор (`orchestrator._continue_monologue`) спускается по
лестнице наполнителей, чтобы монолог шёл непрерывно:

```mermaid
flowchart TD
    LULL{"Затишье?<br/>рядом нет нового объекта"} -->|да| A
    A["1 · Арка района<br/>_area_line: город→район→улица<br/>(каскад, если аутлайн иссяк)"] -->|нечего| B
    A -->|есть| SAY
    B["2 · Revisit<br/>вернулись к раннему объекту<br/>+ свежая деталь"] -->|нет| C
    B -->|да| SAY
    C["3 · Elaborate<br/>ещё деталь о последнем объекте<br/>лимит _MAX_ELABORATE"] -->|исчерпан| D
    C -->|есть| SAY
    D["4 · Reach<br/>объект впереди виднеется<br/>reach=True"] -->|нет| E
    D -->|да| SAY
    E["5 · Bridge «идём дальше»<br/>1 раз за затишье"] --> SIL
    SIL["6 · [SILENCE]"]
    SAY(["🔊 Проговорка"])
```

> **Инварианты:** факты — только из enrichment (не выдумывать); нет факта → `[SILENCE]`. Каскад
> улица→район→город позволяет говорить про место, когда конкретного объекта рядом нет (Ур. 2 плана —
> расширяем этот каскад и elaborate, чтобы «нечего сказать» не случалось).

---

## 3. Barge-in (вопрос голосом/текстом) — высший приоритет

```mermaid
flowchart LR
    Q["🎤 utterance / audio<br/>(вопрос)"] --> CANCEL["Отменить<br/>текущий шаг продюсера"]
    CANCEL --> COMP["Companion<br/>отвечает (+ tools,<br/>control_patch)"]
    COMP --> REPLY["reply → TTS"]
    REPLY --> RESUME["Продюсер<br/>возобновляет тур"]
```

- Вопрос **отменяет** текущий шаг, отвечает, затем тур **возобновляется** (с мостиком-связкой).
- Ответ Companion — это **весь** ответ; вопрос НЕ ставится повторно как area-beat (чтобы не было
  второго дублирующего beat'а).

---

## Где что лежит (карта кода)

| Слой | Файл |
|---|---|
| Оркестратор (FSM, состояние, лестница затишья) | `backend/app/services/agent/orchestrator.py` |
| Работа на тик (discovery→facts→scorer→narrator, прогрев) | `backend/app/services/agent/pipeline.py` |
| Директор (callback/look-ahead/revisit/дедуп) | `backend/app/services/agent/director.py` |
| Планировщик доставки (по предложению, weave, resume) | `backend/app/services/agent/narration_schedule.py` |
| Роли LLM | `scorer.py` · `narrator.py` · `companion.py` · `planner.py` |
| Память прогулки (граф) | `backend/app/shared/memory.py` (`WalkMemory`) |
| Промпты (CORE + роли) | `backend/prompts/*.txt` |
| Гео/поиск (Overpass, ранжирование, inventory) | `backend/app/services/geo/` |
| Обогащение (wiki/web + фото карточки) | `backend/app/services/enrichment/enricher.py` |
| WS-контракт и домен-модели | `backend/app/shared/schemas.py` |
| Конфиг (все ручки) | `backend/app/config.py` |
| Клиент (карта, TTS/STT, UI) | `mobile/lib/main.dart`, `mobile/lib/ui/` |

Полное описание — в `ARCHITECTURE.md` (рус.) и `CLAUDE.md`.
