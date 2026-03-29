# Архитектура VkusVill Bot

Mermaid-диаграммы архитектуры проекта.

---

## Общая схема (C4 Context)

```mermaid
flowchart TB
    subgraph Users["👤 Пользователи"]
        TG[Telegram]
    end

    subgraph Bot["VkusVill Bot"]
        direction TB
        BotCore[Telegram Bot\naiogram 3]
    end

    subgraph External["Внешние сервисы"]
        GigaChat[GigaChat API\nСбер]
        MCP[MCP-сервер\nВкусВилл]
        DB[(PostgreSQL)]
        Redis[(Redis)]
        SQLite[(SQLite)]
    end

    TG <-->|Long Poll / Webhook| BotCore
    BotCore --> GigaChat
    BotCore --> MCP
    BotCore <--> DB
    BotCore <--> Redis
    BotCore <--> SQLite
```

---

## Поток данных (уровень контейнеров)

```mermaid
flowchart TB
    subgraph Telegram["Telegram"]
        API[Bot API]
    end

    subgraph Bot["VkusVill Bot"]
        direction TB
        
        subgraph Entry["Точка входа"]
            Main[__main__.py]
            Polling[Polling / Webhook]
            Health["/health"]
        end

        subgraph Middlewares["Middleware"]
            UM[UserMiddleware]
            TM[ThrottlingMiddleware]
        end

        subgraph Handlers["Handlers"]
            Cmd[Команды: /start, /help, /reset,\n/invite, /survey]
            Text[Текстовые сообщения]
            Admin[Админ: /admin_*]
        end

        subgraph Core["Ядро"]
            GCS[GigaChatService]
            TE[ToolExecutor]
        end

        subgraph Processors["Процессоры"]
            SP[SearchProcessor]
            CP[CartProcessor]
        end

        subgraph Stores["Хранилища"]
            Prefs[PreferencesStore]
            UserStore[UserStore]
            RecipeStore[RecipeStore]
            CartSnap[CartSnapshotStore]
            DialogMgr[DialogManager]
        end

        subgraph Caches["Кеши"]
            PriceCache[PriceCache\nTwoLevelPriceCache]
        end
    end

    subgraph External["Внешние API"]
        GigaChat[GigaChat API]
        MCP[MCP-сервер ВкусВилл]
        OpenFF[Open Food Facts]
    end

    subgraph Persistence["Персистентность"]
        PG[(PostgreSQL)]
        RD[(Redis)]
        PrefsDB[(SQLite prefs)]
        RecipeDB[(SQLite recipes)]
    end

    API --> Main
    Main --> Polling
    Main --> Health
    Polling --> UM --> TM --> Cmd
    TM --> Text
    TM --> Admin
    
    Text --> GCS
    Cmd --> GCS
    
    GCS --> GigaChat
    GCS --> TE
    GCS --> DialogMgr
    GCS --> Prefs
    
    TE --> MCP
    TE --> SP
    TE --> CP
    TE --> Prefs
    TE --> CartSnap
    TE --> OpenFF
    TE --> UserStore
    
    SP --> PriceCache
    CP --> PriceCache
    
    UM --> UserStore
    UserStore --> PG
    Prefs --> PrefsDB
    RecipeStore --> RecipeDB
    CartSnap --> RD
    DialogMgr --> RD
    PriceCache --> RD
```

---

## Компоненты и зависимости (детальная схема)

```mermaid
flowchart LR
    subgraph Input["Вход"]
        TG[Telegram]
    end

    subgraph App["Приложение"]
        direction TB
        
        subgraph BotLayer["Bot Layer"]
            H[handlers.py]
            M[middlewares.py]
        end

        subgraph ServiceLayer["Service Layer"]
            GCS[GigaChatService]
            TE[ToolExecutor]
            LS[LangfuseService]
        end

        subgraph Processors["Processors"]
            SP[SearchProcessor]
            CP[CartProcessor]
            NS[NutritionService]
        end

        subgraph Stores["Stores"]
            Prefs[PreferencesStore]
            UserStore[UserStore]
            RecipeStore[RecipeStore]
            CartSnapStore[CartSnapshotStore]
        end

        subgraph Managers["Managers"]
            DM[DialogManager\nRedisDialogManager]
        end

        subgraph Clients["Clients"]
            MCPClient[VkusvillMCPClient]
        end

        subgraph Caches["Caches"]
            PC[PriceCache]
        end

        subgraph Background["Background"]
            SA[StatsAggregator]
            MR[MigrationRunner]
        end
    end

    subgraph External["Внешние"]
        GigaChat[GigaChat]
        MCP[MCP Server]
        OpenFF[Open Food Facts]
        PG[(PostgreSQL)]
        Redis[(Redis)]
        SQLite1[(SQLite)]
    end

    TG --> H
    H --> M
    M --> GCS
    GCS --> TE
    GCS --> DM
    GCS --> Prefs
    GCS --> LS
    GCS --> GigaChat
    
    TE --> MCPClient
    TE --> SP
    TE --> CP
    TE --> Prefs
    TE --> CartSnapStore
    TE --> NS
    TE --> UserStore
    
    MCPClient --> MCP
    NS --> OpenFF
    
    SP --> PC
    CP --> PC
    
    DM --> Redis
    CartSnapStore --> Redis
    PC --> Redis
    
    UserStore --> PG
    Prefs --> SQLite1
    RecipeStore --> SQLite1
    SA --> PG
```

---

## Цикл обработки сообщения (Function Calling)

```mermaid
sequenceDiagram
    participant User
    participant TG as Telegram
    participant H as Handlers
    participant GCS as GigaChatService
    participant DM as DialogManager
    participant GC as GigaChat API
    participant TE as ToolExecutor
    participant MCP as MCP Server
    participant Local as Local Tools

    User->>TG: Текстовое сообщение
    TG->>H: handle_text()
    H->>GCS: process_message(user_id, text)
    
    GCS->>DM: get_history(user_id)
    DM-->>GCS: history
    
    loop Function Calling (до max_tool_calls)
        GCS->>GC: chat(messages, functions)
        GC-->>GCS: tool_calls[]
        
        alt MCP tool (search, cart_link, etc.)
            GCS->>TE: execute(tool_name, args)
            TE->>MCP: JSON-RPC call
            MCP-->>TE: result
        else Local tool (preferences, recipe, nutrition)
            GCS->>TE: execute(tool_name, args)
            TE->>Local: internal call
            Local-->>TE: result
        end
        
        TE-->>GCS: tool_result
        GCS->>DM: append_assistant + tool_result
    end
    
    GCS->>GC: chat(messages) // финальный ответ
    GC-->>GCS: text response
    GCS->>DM: append_final_response()
    GCS-->>H: response text
    H->>TG: answer(response)
    TG->>User: Ответ бота
```

---

## Режимы работы (Polling vs Webhook)

```mermaid
flowchart TB
    subgraph Dev["Разработка (USE_WEBHOOK=false)"]
        P[Long Polling]
        P --> DP[Dispatcher.start_polling]
        DP --> TG[Telegram API\ngetUpdates]
    end

    subgraph Prod["Production (USE_WEBHOOK=true)"]
        W[aiohttp Web Server]
        W --> WH["/webhook"]
        W --> HC["/health"]
        TG2[Telegram API] -->|POST Update| WH
        WH --> DP2[Dispatcher]
    end

    subgraph HealthCheck["/health проверки"]
        HC --> RedisChk[Redis ping]
        HC --> PGChk[PostgreSQL SELECT 1]
        HC --> MCPChk[MCP get_tools]
    end
```

---

## Хранилища данных

```mermaid
flowchart LR
    subgraph SQLite["SQLite (файлы)"]
        PrefsDB[(preferences.db\nпредпочтения)]
        RecipeDB[(recipes.db\nрецепты)]
    end

    subgraph PostgreSQL["PostgreSQL"]
        Users[(users\nadmin, blocked)]
        Events[(events\nаналитика)]
        DailyStats[(daily_stats\nагрегаты)]
    end

    subgraph Redis["Redis (опционально)"]
        Dialogs[(диалоги\nTTL)]
        Prices[(кеш цен\nL2)]
        CartSnap[(снимки корзины\n24h)]
    end

    subgraph Memory["In-memory (fallback)"]
        MemDialogs[DialogManager]
        MemCache[PriceCache]
        MemCart[CartSnapshotStore]
    end

    PrefsStore[PreferencesStore] --> PrefsDB
    RecipeStore[RecipeStore] --> RecipeDB
    UserStore[UserStore] --> Users
    UserStore --> Events
    StatsAggregator --> DailyStats
    RedisDialogManager --> Dialogs
    TwoLevelPriceCache --> Prices
    CartSnapshotStore --> CartSnap
```

---

## Инструменты (Tools) ToolExecutor

```mermaid
flowchart TB
    TE[ToolExecutor]
    
    subgraph MCP["MCP Tools (удалённые)"]
        VS[vkusvill_products_search]
        VD[vkusvill_product_details]
        VCL[vkusvill_cart_link_create]
    end

    subgraph Local["Local Tools (локальные)"]
        UPG[user_preferences_get]
        UPS[user_preferences_set]
        UPD[user_preferences_delete]
        RI[recipe_ingredients]
        GPC[get_previous_cart]
        NL[nutrition_lookup]
    end

    TE --> MCP
    TE --> Local
    
    UPG --> PrefsStore
    UPS --> PrefsStore
    UPD --> PrefsStore
    RI --> RecipeService
    GPC --> CartSnapshotStore
    NL --> NutritionService
    
    VS --> SearchProcessor
    VCL --> CartProcessor
```

---

## Meal-plan pipeline (актуальное поведение)

Подсистема meal-plan обрабатывается отдельным executor, а не общим tool-loop.
Это снижает нестабильность в длинных сценариях и позволяет жёстко валидировать
контракт плана до поиска товаров и сборки корзины.

### Ключевые codepaths

| Этап | Codepath | Что делает |
|------|----------|------------|
| Разбор запроса | `src/vkuswill_bot/agents/meal_plan_request_extractor.py` | LLM-first извлечение полей запроса (JSON), затем детерминированная сборка `MealPlanRequest` |
| Нормализация доменной модели | `src/vkuswill_bot/agents/meal_plan_types.py` | Нормализует `days`, группы, ограничения, `requested_meal_types`, вычисляет `min_dishes/max_dishes` |
| Генерация плана | `src/vkuswill_bot/agents/meal_plan_generator.py` | Просит LLM вернуть JSON `schema_version=1`, валидирует схему и делает один retry при ошибке |
| Оркестрация этапов | `src/vkuswill_bot/agents/meal_plan_executor.py` | Parse → Generate → Ingredients → Search → Cart + fail-soft/fallback и таймаут-политика |

### Последовательность обработки

1. `run_meal_plan_turn(...)` запускает специализированный pipeline.
2. `parse_meal_plan_request_with_llm(...)` пытается извлечь структуру из LLM-ответа:
   - `temperature=0.0`, `tool_choice=none`;
   - если JSON невалидный, делает один retry;
   - при повторном фейле переключается на детерминированный парсер.
3. `parse_meal_plan_request(...)` применяет guardrails:
   - `days` в диапазоне `1..14`, поддерживаются формулировки вроде `на два дня`;
   - число дней не интерпретируется как число людей;
   - `people_total` вычисляется детерминированно по явным маркерам людей.
4. `generate_meal_plan(...)` валидирует payload:
   - строго `schema_version=1`;
   - уникальные названия блюд, валидные `day/meal_type/servings_total/audience_groups`;
   - покрытие слотов по дням для явных `requested_meal_types`.
5. После генерации executor собирает ингредиенты, применяет phase2 safety,
   ищет товары по дням и формирует корзину(ы).

### Важные инварианты и ограничения

- Для коротких явных запросов слотов (например, `обеды на два дня`) при одной общей
  группе используется **точное** число блюд:
  `min_dishes == max_dishes == days * len(requested_meal_types)`.
- Лишние "заполнители" в том же слоте (дополнительный lunch в один день) отклоняются
  валидацией payload.
- Для `requested_meal_types=["snack"]` допускаются `snack_1..snack_3`, но наличие
  хотя бы одного snack-слота в каждый день остаётся обязательным.
- Если LLM в extraction-шаге "додумает" `people_total`, итоговое значение всё равно
  берётся из детерминированного парсинга текста.

### Быстрые примеры нормализации

| Вход пользователя | Нормализованный результат |
|-------------------|---------------------------|
| `собери мне обеды для здорового питания на два дня` | `days=2`, `requested_meal_types=["lunch"]`, `people_total=1`, `min_dishes=max_dishes=2` |
| `меню на 3 дня для 2 человек` | `days=3`, `people_total=2`, `requested_meal_types=[]` (далее по умолчанию 3 слота/день) |

---

## Легенда

| Компонент | Назначение |
|-----------|------------|
| **GigaChatService** | Оркестрация LLM, function calling, история диалогов |
| **ToolExecutor** | Маршрутизация MCP vs local tools, обработка ошибок |
| **SearchProcessor** | Поиск товаров, кеш цен, постпроцессинг результатов |
| **CartProcessor** | Сборка корзины, верификация, расчёт стоимости |
| **DialogManager** | Хранение истории диалога (in-memory или Redis) |
| **PreferencesStore** | Предпочтения пользователя (SQLite) |
| **UserStore** | Пользователи, админы, блокировки, рефералы (PostgreSQL) |
| **VkusvillMCPClient** | JSON-RPC клиент к MCP-серверу ВкусВилл |
| **PriceCache** | Кеш цен (in-memory или L1+L2 с Redis) |
