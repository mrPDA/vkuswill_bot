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
        Qwen[Qwen / Yandex Cloud AI Studio\nOpenAI-compatible]
        MCP[MCP-сервер\nВкусВилл]
        Alice[Яндекс Диалоги\nАлиса]
        DB[(PostgreSQL)]
        Redis[(Redis)]
        SQLite[(SQLite)]
    end

    TG <-->|Long Poll / Webhook| BotCore
    Alice --> BotCore
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
            Gateway[McpToolGateway]
            MealPlan[MealPlanExecutor]
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
        Qwen[Qwen / OpenAI-compatible API]
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
    Agent --> Gateway
    Agent --> MealPlan
    Agent --> DialogMgr
    Agent --> Prefs
    
    Gateway --> MCP
    Agent --> UserStore
    
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
            Gateway[McpToolGateway]
            LS[LangfuseService]
            MealPlan[MealPlanExecutor]
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
        Qwen[Qwen / Yandex Cloud AI Studio]
        MCP[MCP Server]
        OpenFF[Open Food Facts]
        PG[(PostgreSQL)]
        Redis[(Redis)]
        SQLite1[(SQLite)]
    end

    TG --> H
    H --> M
    M --> Agent
    Agent --> Gateway
    Agent --> MealPlan
    Agent --> DM
    Agent --> Prefs
    Agent --> LS
    Agent --> Qwen
    
    Gateway --> MCPClient
    Agent --> UserStore
    
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
    participant LLM as Qwen OpenAI API
    participant Gateway as McpToolGateway
    participant MCP as MCP Server
    participant Prefs as PreferencesStore

    User->>TG: Текстовое сообщение
    TG->>H: handle_text()
    H->>Agent: process_message(user_id, text)
    
    Agent->>DM: get_history(user_id)
    DM-->>Agent: history
    
    loop Function Calling (до max_tool_calls)
        Agent->>LLM: chat(messages, tools)
        LLM-->>Agent: tool_calls[]
        
        alt MCP tool (search, cart_link, etc.)
            Agent->>Gateway: call_tool(tool_name, args)
            Gateway->>MCP: JSON-RPC call
            MCP-->>Gateway: result
            Gateway-->>Agent: tool_result
        else Local preference tool
            Agent->>Prefs: get/set/delete
            Prefs-->>Agent: result
        end
        
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

## Meal-plan поток

Meal-plan не проходит обычный цикл свободных tool calls от начала до конца.
`ShoppingAgent` сначала определяет `prompt_profile` (`cart`, `recipe`,
`meal_plan`, `status`), а затем для `meal_plan` может запустить выделенный
`run_meal_plan_turn()` из `src/vkuswill_bot/agents/meal_plan_executor.py`.

```mermaid
sequenceDiagram
    participant User
    participant Agent as ShoppingAgent
    participant Extractor as MealPlanRequestExtractor
    participant Generator as MealPlanGenerator
    participant Phase2 as Ingredient/Safety/Search
    participant Cart as GroupedCartOps
    participant Render as ResponseContractRenderer
    participant LLM as Qwen
    participant MCP as MCP Server

    User->>Agent: "рацион на 5 дней без молока"
    Agent->>Agent: resolve prompt_profile=meal_plan + rollout gate
    Agent->>Extractor: parse_meal_plan_request_with_llm()
    Extractor->>LLM: JSON extraction prompt
    LLM-->>Extractor: days, people_total, allergens, meal slots
    Extractor-->>Agent: MealPlanRequest
    Agent->>Generator: generate_meal_plan(request)
    Generator->>LLM: meal-plan generation prompt
    LLM-->>Generator: schema_version=1, dishes[]
    Generator-->>Agent: validated MealPlan
    Agent->>Phase2: collect ingredients + hard-constraint safety retry
    Phase2->>LLM: recipe ingredients / safety checks
    Phase2->>MCP: product searches day by day
    Phase2-->>Agent: products, not_found, soft coverage
    Agent->>Cart: create_grouped_carts()
    Cart->>MCP: vkusvill_cart_link_create
    Cart-->>Agent: grouped cart data
    Agent->>Render: render_meal_plan_contract_response()
    Render-->>User: menu, constraints, cart links / gaps
```

Ключевые ограничения:

- `LLM_PROVIDER` поддерживается только как `qwen_openai`, `LLM_ROUTING_STRATEGY`
  только `single_provider`; это проверяет `create_chat_engine()`.
- LLM-first extraction ограничена доменной моделью `MealPlanRequest`: `days`
  1..14, `people_total` 1..20, допустимые meal slots
  `breakfast/lunch/dinner/snack`.
- Явные meal-slot запросы для одной группы (`обеды на два дня`) дают точное
  число блюд: `days * len(requested_meal_types)`, без дополнительных filler-блюд.
- `hard_constraints` (аллергены, исключения, детские ограничения) проверяются
  после сбора ингредиентов; при нарушении Phase 2 может перегенерировать план или
  fail-soft в стандартный turn.
- Результат рендерится детерминированным response contract v1, чтобы stage/live
  проверки могли валидировать профиль, размер ответа и запрещённые продукты.

Операционные флаги находятся в `Config`: `MEAL_PLAN_INTENT_ROUTING_ENABLED`,
`MEAL_PLAN_EXECUTOR_ENABLED`, `MEAL_PLAN_SHADOW_MODE_ENABLED`,
`MEAL_PLAN_ROLLOUT_PERCENT`, `MEAL_PLAN_ROLLOUT_KPI_GATES_ENABLED` и
`MEAL_PLAN_ALLOW_UNVALIDATED_ROLLOUT`. При включённых KPI gates rollout может
использовать `MealPlanRolloutController` поверх PostgreSQL events.

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

## Инструменты и ToolExecutor

Основной Telegram runtime использует `ShoppingAgent._call_mcp_tool()` и
`McpToolGateway`: gateway вызывает удалённый MCP-сервер, кеширует безопасные
tool results в рамках turn и включает локальные fallback для `recipe_ingredients`
и `recipe_search`, если таких MCP tools нет.

`ToolExecutor` остаётся локальным pipeline для встроенного
`src/vkuswill_bot/mcp_server/server.py`: он нужен, когда этот репозиторий сам
поднимает MCP-compatible сервер и маршрутизирует tools в локальные processors.

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
| **ShoppingAgent** | Основной chat engine: OpenAI-compatible Qwen, prompt profiles, tool loop, история диалогов |
| **McpToolGateway** | Выполнение MCP tool calls внутри `ShoppingAgent` с таймаутами, retry и компактированием результатов |
| **MealPlanExecutor** | Выделенный pipeline для meal-plan: extraction, generation, ingredients, safety, grouped carts, deterministic render |
| **ToolExecutor** | Pipeline встроенного MCP-сервера: pre/postprocess args/results, local tools, Cart/Search processors |
| **SearchProcessor** | Поиск товаров, кеш цен, постпроцессинг результатов |
| **CartProcessor** | Сборка корзины, верификация, расчёт стоимости |
| **DialogManager** | Хранение истории диалога (in-memory или Redis) |
| **PreferencesStore** | Предпочтения пользователя (SQLite) |
| **UserStore** | Пользователи, админы, блокировки, рефералы (PostgreSQL) |
| **VkusvillMCPClient** | JSON-RPC клиент к MCP-серверу ВкусВилл |
| **PriceCache** | Кеш цен (in-memory или L1+L2 с Redis) |
