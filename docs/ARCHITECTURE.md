# Архитектура VkusVill Bot

Mermaid-диаграммы и краткие пояснения по текущей архитектуре проекта.

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
        Qwen[Qwen\nYandex Cloud AI Studio]
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
            SA[ShoppingAgent]
            MG[McpToolGateway]
            PR[PromptRegistry]
        end

        subgraph Processors["Процессоры"]
            SP[SearchProcessor]
            CP[CartProcessor]
            MP[MealPlanExecutor]
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
        Qwen[Qwen API\nOpenAI-compatible]
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
    
    Text --> SA
    Cmd --> SA
    
    SA --> Qwen
    SA --> MG
    SA --> DialogMgr
    SA --> Prefs
    SA --> PR
    SA --> MP
    
    MG --> MCP
    MG --> SP
    MG --> CP
    MG --> Prefs
    MG --> CartSnap
    MG --> OpenFF
    MG --> UserStore
    MP --> MCP
    MP --> CP
    
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
            SA[ShoppingAgent]
            MG[McpToolGateway]
            LS[LangfuseService]
            PR[PromptRegistry]
        end

        subgraph Processors["Processors"]
            SP[SearchProcessor]
            CP[CartProcessor]
            NS[NutritionService]
            MP[MealPlanExecutor]
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
            StatsAgg[StatsAggregator]
            MR[MigrationRunner]
        end
    end

    subgraph External["Внешние"]
        Qwen[Qwen\nOpenAI-compatible]
        MCP[MCP Server]
        OpenFF[Open Food Facts]
        PG[(PostgreSQL)]
        Redis[(Redis)]
        SQLite1[(SQLite)]
    end

    TG --> H
    H --> M
    M --> SA
    SA --> MG
    SA --> DM
    SA --> Prefs
    SA --> LS
    SA --> PR
    SA --> Qwen
    SA --> MP
    
    MG --> MCPClient
    MG --> SP
    MG --> CP
    MG --> Prefs
    MG --> CartSnapStore
    MG --> NS
    MG --> UserStore
    MP --> MCPClient
    MP --> CP
    
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
    StatsAgg --> PG
```

---

## Цикл обработки сообщения (ShoppingAgent + Function Calling)

```mermaid
sequenceDiagram
    participant User
    participant TG as Telegram
    participant H as Handlers
    participant SA as ShoppingAgent
    participant DM as DialogManager
    participant LLM as Qwen API
    participant MG as McpToolGateway
    participant MCP as MCP Server
    participant Local as Local Tools

    User->>TG: Текстовое сообщение
    TG->>H: handle_text()
    H->>SA: process_message(user_id, text)
    
    SA->>DM: get_history(user_id)
    DM-->>SA: history
    SA->>LLM: classify_user_intent() / prompt profile
    LLM-->>SA: cart / recipe / meal_plan / general
    
    loop Function Calling (до max_tool_calls)
        SA->>LLM: chat(messages, tools)
        LLM-->>SA: tool_calls[]
        
        alt MCP tool (search, cart_link, etc.)
            SA->>MG: call_tool(tool_name, args)
            MG->>MCP: JSON-RPC call
            MCP-->>MG: result
        else Local tool (preferences, recipe, nutrition)
            SA->>MG: call_tool(tool_name, args)
            MG->>Local: internal call
            Local-->>MG: result
        end
        
        MG-->>SA: tool_result
        SA->>DM: append_assistant + tool_result
    end
    
    SA->>LLM: chat(messages) // финальный ответ
    LLM-->>SA: text response
    SA->>DM: append_final_response()
    SA-->>H: response text
    H->>TG: answer(response)
    TG->>User: Ответ бота
```

---

## Поток планирования питания (meal-plan executor)

Meal-plan запросы не идут через общий tool-loop полностью. `ShoppingAgent` сначала строит `TurnState`, определяет `prompt_profile`, проверяет rollout gate и, если пользователь попал в rollout, передаёт turn в `run_meal_plan_turn()`.

```mermaid
flowchart TB
    U[Запрос пользователя\n"план питания на 3 дня"] --> TS[build_turn_state]
    TS --> Gate{profile=meal_plan\nexecutor enabled\nrollout gate passed}
    Gate -->|нет| Loop[Стандартный ShoppingAgent tool-loop]
    Gate -->|да| Parse[LLM-first parse\nmeal-plan-request-extraction]
    Parse --> Req[MealPlanRequest\npeople/days/groups/meal slots]
    Req --> Gen[generate_meal_plan\nschema_version=1]
    Gen --> Validate[Валидация схемы\nдни, слоты, группы, аллергены]
    Validate --> Ingredients[collect_ingredients_for_dishes]
    Ingredients --> Safety[phase2 safety policy\nhard constraints + retry]
    Safety --> Search[search_products_day_by_day]
    Search --> Cart[create_grouped_carts]
    Cart --> Render[deterministic render_response]
    Render --> Trace[Langfuse trace + diagnostics]
```

Ключевые ограничения, подтверждённые кодом:

- `LLM_PROVIDER` поддерживается только как `qwen_openai`; legacy GigaChat runtime удалён.
- Парсинг запроса LLM-first, но при невалидном JSON или исключении используется deterministic fallback из `meal_plan_types.py`.
- Длительность плана ограничена 1..14 днями, количество людей — 1..20.
- Явно запрошенные приёмы пищи (`breakfast`, `lunch`, `dinner`, `snack`) для одной группы дают точное число слотов: `days * len(requested_meal_types)`.
- Генерация принимает только `schema_version=1`, уникальные блюда, валидные `day`, `meal_type`, `servings_total` и `audience_groups`.
- Для планов от 5 дней включаются extended deadlines: 240 секунд на turn и 210 секунд на phase2.

Операционные сигналы:

- Langfuse spans: `meal-plan.parse-request`, `meal-plan.collect-ingredients`, `meal-plan.phase2-safety`, `meal-plan.search-products`, `meal-plan.create-cart`.
- `get_last_turn_diagnostics()` содержит статистику `meal_plan_ingredient_collection`, `meal_plan_recipe_search`, `meal_plan_cart_create`, а также причину executor gate.
- Если executor не может безопасно завершить flow, он возвращается в стандартную обработку с префиксом `Перехожу к стандартной обработке запроса`.

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

## Инструменты (Tools) McpToolGateway

```mermaid
flowchart TB
    MG[McpToolGateway]
    
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

    MG --> MCP
    MG --> Local
    
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
| **ShoppingAgent** | Оркестрация Qwen, prompt routing, function calling, история диалогов |
| **McpToolGateway** | Маршрутизация MCP vs local tools, таймауты, retry и нормализация вызовов |
| **SearchProcessor** | Поиск товаров, кеш цен, постпроцессинг результатов |
| **CartProcessor** | Сборка корзины, верификация, расчёт стоимости |
| **MealPlanExecutor** | Выделенный pipeline планов питания: парсинг запроса, генерация меню, поиск ингредиентов, корзина |
| **DialogManager** | Хранение истории диалога (in-memory или Redis) |
| **PreferencesStore** | Предпочтения пользователя (SQLite) |
| **UserStore** | Пользователи, админы, блокировки, рефералы (PostgreSQL) |
| **VkusvillMCPClient** | JSON-RPC клиент к MCP-серверу ВкусВилл |
| **PriceCache** | Кеш цен (in-memory или L1+L2 с Redis) |
