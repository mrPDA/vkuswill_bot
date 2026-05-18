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
        Qwen[Qwen LLM\nYandex Cloud AI Studio]
        MCP[MCP-сервер\nВкусВилл]
        DB[(PostgreSQL)]
        Redis[(Redis)]
        SQLite[(SQLite)]
    end

    TG <-->|Long Poll / Webhook| BotCore
    BotCore --> Qwen
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
            Agent[ShoppingAgent]
            TE[ToolExecutor]
            MPE[MealPlanExecutor]
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
        Qwen[Qwen LLM\nOpenAI-compatible]
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
    
    Text --> Agent
    Cmd --> Agent
    
    Agent --> Qwen
    Agent --> TE
    Agent --> MPE
    Agent --> DialogMgr
    Agent --> Prefs
    
    TE --> MCP
    TE --> SP
    TE --> CP
    TE --> Prefs
    TE --> CartSnap
    TE --> OpenFF
    TE --> UserStore
    MPE --> MCP
    MPE --> CP
    MPE --> CartSnap
    
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
            Agent[ShoppingAgent]
            TE[ToolExecutor]
            LS[LangfuseService]
        end

        subgraph Processors["Processors"]
            SP[SearchProcessor]
            CP[CartProcessor]
            NS[NutritionService]
            MPE[MealPlanExecutor]
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
        Qwen[Qwen / OpenAI-compatible]
        MCP[MCP Server]
        OpenFF[Open Food Facts]
        PG[(PostgreSQL)]
        Redis[(Redis)]
        SQLite1[(SQLite)]
    end

    TG --> H
    H --> M
    M --> Agent
    Agent --> TE
    Agent --> MPE
    Agent --> DM
    Agent --> Prefs
    Agent --> LS
    Agent --> Qwen
    
    TE --> MCPClient
    TE --> SP
    TE --> CP
    TE --> Prefs
    TE --> CartSnapStore
    TE --> NS
    TE --> UserStore
    MPE --> MCPClient
    MPE --> CP
    MPE --> CartSnapStore
    
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
    participant Agent as ShoppingAgent
    participant DM as DialogManager
    participant LLM as Qwen LLM
    participant TE as ToolExecutor
    participant MCP as MCP Server
    participant Local as Local Tools

    User->>TG: Текстовое сообщение
    TG->>H: handle_text()
    H->>Agent: process_message(user_id, text)
    
    Agent->>DM: get_history(user_id)
    DM-->>Agent: history
    
    loop Function Calling (до max_tool_calls)
        Agent->>LLM: chat(messages, functions)
        LLM-->>Agent: tool_calls[]
        
        alt MCP tool (search, cart_link, etc.)
            Agent->>TE: execute(tool_name, args)
            TE->>MCP: JSON-RPC call
            MCP-->>TE: result
        else Local tool (preferences, recipe, nutrition)
            Agent->>TE: execute(tool_name, args)
            TE->>Local: internal call
            Local-->>TE: result
        end
        
        TE-->>Agent: tool_result
        Agent->>DM: append_assistant + tool_result
    end
    
    Agent->>LLM: chat(messages) // финальный ответ
    LLM-->>Agent: text response
    Agent->>DM: append_final_response()
    Agent-->>H: response text
    H->>TG: answer(response)
    TG->>User: Ответ бота
```

---

## Meal-plan executor

Запросы планирования питания проходят через тот же `ShoppingAgent`, но при
`diagnostics.prompt_profile == "meal_plan"` могут уйти в выделенный executor
вместо общего tool-loop. Это сделано, чтобы меню, ингредиенты и корзина
строились по детерминированному контракту, а не зависели от произвольного
финального ответа LLM.

```mermaid
flowchart TB
    UserText[Текст пользователя] --> Classifier[Intent + prompt profile]
    Classifier --> Gate{Можно использовать executor?}
    Gate -->|нет| ToolLoop[Обычный ShoppingAgent tool-loop]
    Gate -->|да| Parse[meal_plan_request_extractor.py]
    Parse --> Types[meal_plan_types.py\nMealPlanRequest]
    Types --> Generate[meal_plan_generator.py\nJSON schema v1 + retry]
    Generate --> Ingredients[meal_plan_phase2_ops.py\nrecipe ingredients]
    Ingredients --> Search[meal_plan_day_search_ops.py\nMCP search]
    Search --> Cart[meal_plan_cart_ops.py\ncart link create]
    Cart --> Render[meal_plan_response_contract.py\nResponse Contract v1]
```

Ключевые ограничения:

- `ShoppingAgent` поддерживает только `LLM_PROVIDER=qwen_openai` и
  `LLM_ROUTING_STRATEGY=single_provider`; фабрика `create_chat_engine()` падает
  при другой конфигурации.
- Gate executor-а находится в `agents/shopping_turn_executor.py`: нужен
  `prompt_profile="meal_plan"`, включенный `MEAL_PLAN_EXECUTOR_ENABLED`, не
  включенный shadow mode и попадание пользователя в rollout bucket.
- KPI-gate rollout-а живет в `services/meal_plan_rollout_policy.py`. В
  production unvalidated bypass запрещен; в non-prod он требует reason, actor,
  валидный `expires_at` и TTL не больше
  `MEAL_PLAN_UNVALIDATED_ROLLOUT_MAX_TTL_SECONDS`.
- `meal_plan_types.py` парсит дни, аудитории, аллергены, диеты, кухни и явные
  приёмы пищи. Для запроса вроде "обеды на два дня" диапазон блюд становится
  точным: `days * requested_meal_types`.
- `meal_plan_generator.py` принимает только `schema_version=1`, проверяет
  диапазон блюд, дни, `meal_type`, `servings_total`, `audience_groups`, покрытие
  дней/слотов и hard-constraints. Разрешен один retry после repair JSON
  (markdown fences, trailing comma, JSON внутри free-form текста).
- Runtime deadlines описаны в `agents/meal_plan_runtime_policy.py`: планы на 5+
  дней получают расширенный turn/phase2 budget, отдельные MCP-вызовы ограничены
  timeout-ами и одним retry.

---

## Нормализация корзины

Аргументы `vkusvill_cart_link_create` проходят две ступени нормализации:

1. `services/tool_input_normalizers.py::fix_cart_args` — shared слой для
   `ToolExecutor` и `CartProcessor`: добавляет `q=1`, приводит строковые `q`
   (`"1,5"` -> `1.5`) к float и суммирует дубли по `xml_id`.
2. `services/cart_processor.py::fix_unit_quantities` — асинхронная коррекция по
   `PriceCache`: округляет штучные единицы вверх, ограничивает q, пересчитывает
   подозрительные граммы/мл в количество упаковок и отдельно нормализует яйца
   до одной упаковки.

Если меняется поведение количества или объединения товаров, править нужно
shared normalizer и покрывать его тестами на уровне tool preprocessing; если
нужны правила по unit/весу/ценам, править `CartProcessor` и проверять сценарии с
`PriceCache`.

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

## Легенда

| Компонент | Назначение |
|-----------|------------|
| **ShoppingAgent** | Оркестрация Qwen/OpenAI-compatible runtime, function calling, история диалогов |
| **ToolExecutor** | Маршрутизация MCP vs local tools, обработка ошибок |
| **MealPlanExecutor** | Выделенный pipeline планирования питания: запрос, меню, ингредиенты, корзина, контракт ответа |
| **SearchProcessor** | Поиск товаров, кеш цен, постпроцессинг результатов |
| **CartProcessor** | Нормализация количества, сборка корзины, верификация, расчёт стоимости |
| **DialogManager** | Хранение истории диалога (in-memory или Redis) |
| **PreferencesStore** | Предпочтения пользователя (SQLite) |
| **UserStore** | Пользователи, админы, блокировки, рефералы (PostgreSQL) |
| **VkusvillMCPClient** | JSON-RPC клиент к MCP-серверу ВкусВилл |
| **PriceCache** | Кеш цен (in-memory или L1+L2 с Redis) |
