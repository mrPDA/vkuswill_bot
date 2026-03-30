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

## Актуальные инварианты (март 2026)

### 1) Meal-plan: LLM-first extraction + deterministic fallback

`meal_plan`-ветка использует отдельный executor (`run_meal_plan_turn`) и сначала пытается извлечь структуру запроса через LLM (`parse_meal_plan_request_with_llm`), а затем:

- валидирует и нормализует поля (`days`, `people_total`, `diet`, `requested_meal_types`, `allergens_excluded`);
- при невалидном JSON или ошибке LLM откатывается на детерминированный парсер без падения user-flow;
- пишет diagnostics/trace-метаданные о source (`llm` vs `deterministic_*_fallback`) для наблюдаемости.

Ключевые codepath:

- `src/vkuswill_bot/agents/meal_plan_executor.py`
- `src/vkuswill_bot/agents/meal_plan_request_extractor.py`
- `src/vkuswill_bot/agents/shopping_turn_executor.py`

Ограничения:

- запуск meal-plan executor gated через rollout/shadow-mode;
- fallback к стандартному turn-пути встроен и ожидаем как штатный сценарий, а не как crash-path.

### 2) Cart tool args: единая нормализация перед MCP

Для `vkusvill_cart_link_create` аргументы приводятся к единому виду до вызова MCP:

- автоподстановка `q=1`, если количество не задано;
- нормализация строковых `q` (например, `"1,5"` -> `1.5`);
- merge дублей по `xml_id` с суммированием количества;
- последующая корректировка единиц (`fix_unit_quantities`) в preprocessor pipeline.

Ключевые codepath:

- `src/vkuswill_bot/services/tool_input_normalizers.py` (`fix_cart_args`)
- `src/vkuswill_bot/services/tool_executor_pipeline.py` (`ToolArgsPreprocessor.preprocess`)
- `src/vkuswill_bot/services/cart_processor.py`

Практический эффект: одинаковые cart-аргументы независимо от того, кто вызвал корзину (агент, recipe flow, recovery path), и меньше регрессий на смешанных форматах количества.

### 3) Response contracts: единый набор для stage и live runtime

Сценарии `TC-*` вынесены в shared-модуль и переиспользуются двумя раннерами:

- stage pytest (`tests/test_stage_response_contracts.py`) проверяет debug API + Langfuse trace provenance;
- live runner (`scripts/run_live_response_contracts.py`) гоняет тот же набор на текущем локальном runtime.

Ключевые codepath:

- `src/vkuswill_bot/testing/response_contract_cases.py`
- `tests/test_stage_response_contracts.py`
- `scripts/run_live_response_contracts.py`
- `src/vkuswill_bot/bot/telegram_delivery.py`

Runbook: `docs/stage-response-contracts.md`.

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
