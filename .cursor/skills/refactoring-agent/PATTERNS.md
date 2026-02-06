# Каталог паттернов рефакторинга

Справочник code smells и трансформаций для их устранения. Основан на Martin Fowler "Refactoring" и адаптирован для Python / async / aiogram.

---

## Часть 1: Code Smells — распознавание проблем

### 1.1 Long Method (Длинный метод)

**Признаки:** метод > 50 строк, много уровней вложенности, комментарии-разделители внутри.

**Пример из проекта:**
```python
# ❌ process_message() — 200 строк, 5 уровней вложенности
async def process_message(self, user_id: int, text: str) -> str:
    # ... 200 строк с for, if, try/except ...
```

**Решение:** Extract Method — разбить на методы по 15–30 строк.

---

### 1.2 God Class (Божественный класс)

**Признаки:** класс > 300 строк, > 10 публичных методов, несколько зон ответственности.

**Пример:**
```python
# ❌ Класс одновременно: управляет историей, вызывает GigaChat, 
# обрабатывает инструменты, кеширует цены, верифицирует корзину
class GigaChatService:
    def _get_history(self): ...
    def _trim_history(self): ...
    def _cache_prices_from_search(self): ...
    def _fix_unit_quantities(self): ...
    def _calc_cart_total(self): ...
    def _verify_cart(self): ...
    async def process_message(self): ...
```

**Решение:** Extract Class — выделить отдельные классы по зонам ответственности.

---

### 1.3 Feature Envy (Зависть к чужим данным)

**Признаки:** метод активно работает с данными другого класса или модуля, а не своего.

**Пример:**
```python
# ❌ Метод GigaChatService слишком много знает о структуре JSON поиска
def _trim_search_result(self, result_text: str) -> str:
    data = json.loads(result_text)
    data_field = data.get("data")
    items = data_field.get("items")
    for item in items:
        price = item.get("price")
        if isinstance(price, dict):
            trimmed["price"] = price.get("current")
```

**Решение:** Move Method — переместить в класс, которому данные принадлежат, или Extract Class для обработки результатов.

---

### 1.4 Long Parameter List (Длинный список параметров)

**Признаки:** функция принимает > 4 параметров.

```python
# ❌ Много параметров
def __init__(self, credentials, model, scope, mcp_client, 
             preferences_store, max_tool_calls, max_history): ...

# ✅ Группировка через dataclass / config
@dataclass
class GigaChatConfig:
    credentials: str
    model: str
    scope: str
    max_tool_calls: int = 15
    max_history: int = 50

def __init__(self, config: GigaChatConfig, mcp_client, preferences_store): ...
```

**Решение:** Introduce Parameter Object / Preserve Whole Object.

---

### 1.5 Duplicated Code (Дублирование)

**Признаки:** одинаковые или похожие блоки кода в разных местах.

```python
# ❌ Одинаковый паттерн JSON-парсинга повторяется 5 раз
try:
    data = json.loads(result_text)
except (json.JSONDecodeError, TypeError):
    return {}
data_field = data.get("data") if isinstance(data, dict) else None
if not isinstance(data_field, dict):
    return {}
```

**Решение:** Extract Method — общий `_parse_api_response()`.

---

### 1.6 Deep Nesting (Глубокая вложенность)

**Признаки:** > 3 уровней вложенности (if/for/try внутри друг друга).

```python
# ❌ 5 уровней вложенности
for step in range(max):
    try:
        if msg.function_call:
            if tool_name in LOCAL_TOOLS:
                if not store:
                    ...

# ✅ Early return / Guard clauses
for step in range(max):
    response = await self._get_response(history, functions)
    if response is None:
        return error_message

    if not msg.function_call:
        return msg.content

    result = await self._handle_tool_call(msg, user_id, ...)
```

**Решение:** Replace Nested Conditional with Guard Clauses, Extract Method.

---

### 1.7 Magic Numbers / Strings (Магические значения)

**Признаки:** числа или строки без пояснения в коде.

```python
# ❌ Что значит 1000? 4096? 
result[:1000]
if len(text) > 4096:

# ✅ Именованные константы
MAX_RESULT_LOG_LENGTH = 1000
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
```

**Решение:** Replace Magic Number with Symbolic Constant.

---

### 1.8 Shotgun Surgery (Хирургия дробью)

**Признаки:** одно изменение требует правок в множестве файлов.

**Решение:** Move Method / Move Field — сгруппировать связанный код.

---

### 1.9 Dead Code (Мёртвый код)

**Признаки:** неиспользуемые импорты, функции, переменные, закомментированный код.

```python
# ❌ Мёртвый код
# import os  # было нужно раньше
# def old_method(self): ...
unused_var = compute_something()
```

**Решение:** Remove Dead Code — удалить. Git сохранит историю.

---

### 1.10 Primitive Obsession (Одержимость примитивами)

**Признаки:** использование `dict`, `str`, `int` вместо доменных типов.

```python
# ❌ Словари везде
cached: dict[int, dict] = {}  # dict с ключами name, price, unit

# ✅ Доменный тип
@dataclass
class ProductInfo:
    name: str
    price: float
    unit: str
```

**Решение:** Replace Data Value with Object / Introduce Value Object.

---

## Часть 2: Трансформации — приёмы рефакторинга

### 2.1 Extract Method

**Когда:** Блок кода можно описать одной фразой. Метод > 30 строк.

```python
# До
async def process_message(self, user_id, text):
    # ... 20 строк подготовки ...
    # Парсим аргументы инструмента
    if isinstance(raw_args, str):
        try:
            args = json.loads(raw_args)
        except json.JSONDecodeError:
            args = {}
    elif isinstance(raw_args, dict):
        args = raw_args
    else:
        args = {}
    # ... продолжение ...

# После
async def process_message(self, user_id, text):
    # ... 20 строк подготовки ...
    args = self._parse_tool_arguments(raw_args)
    # ... продолжение ...

@staticmethod
def _parse_tool_arguments(raw_args: str | dict | None) -> dict:
    """Парсить аргументы вызова инструмента."""
    if isinstance(raw_args, str):
        try:
            return json.loads(raw_args)
        except json.JSONDecodeError:
            return {}
    if isinstance(raw_args, dict):
        return raw_args
    return {}
```

**Чеклист:**
- [ ] Метод имеет осмысленное имя (что делает, не как)
- [ ] Параметры — только те, что нужны
- [ ] Возвращаемый тип аннотирован
- [ ] Docstring добавлен

---

### 2.2 Extract Class

**Когда:** Класс имеет > 2 зон ответственности.

```python
# До: GigaChatService = история + GigaChat API + обработка инструментов + кеш цен

# После: разделение ответственности
class ConversationManager:
    """Управление историей диалогов (LRU)."""
    def get_history(self, user_id: int) -> list[Messages]: ...
    def trim_history(self, user_id: int) -> None: ...
    def reset(self, user_id: int) -> None: ...

class SearchResultProcessor:
    """Обработка и кеширование результатов поиска."""
    def cache_prices(self, result: str) -> None: ...
    def trim_result(self, result: str) -> str: ...
    def extract_xml_ids(self, result: str) -> set[int]: ...

class CartBuilder:
    """Расчёт и верификация корзины."""
    def fix_quantities(self, args: dict) -> dict: ...
    def calc_total(self, args: dict, result: str) -> str: ...
    def verify(self, args: dict, search_log: dict) -> dict: ...
```

**Чеклист:**
- [ ] Каждый класс — одна зона ответственности (SRP)
- [ ] Связность внутри класса высокая
- [ ] Интерфейс между классами минимален
- [ ] Тесты обновлены

---

### 2.3 Replace Conditional with Strategy / Dispatch

**Когда:** Длинная цепочка `if/elif` по типу или имени.

```python
# До: if/elif на 30 строк
if tool_name == "user_preferences_get":
    result = await self._prefs_store.get_formatted(user_id)
elif tool_name == "user_preferences_set":
    ...
elif tool_name == "user_preferences_delete":
    ...

# После: dispatch-словарь
_LOCAL_HANDLERS: dict[str, Callable] = {
    "user_preferences_get": self._handle_prefs_get,
    "user_preferences_set": self._handle_prefs_set,
    "user_preferences_delete": self._handle_prefs_delete,
}

async def _call_local_tool(self, tool_name: str, args: dict, user_id: int) -> str:
    handler = self._LOCAL_HANDLERS.get(tool_name)
    if handler is None:
        return json.dumps({"ok": False, "error": f"Unknown tool: {tool_name}"})
    return await handler(args, user_id)
```

---

### 2.4 Introduce Guard Clauses

**Когда:** Глубокая вложенность из-за проверок условий.

```python
# До
def process(self, data):
    if data is not None:
        if data.get("items"):
            if isinstance(data["items"], list):
                # ... основная логика ...

# После
def process(self, data):
    if data is None:
        return default
    items = data.get("items")
    if not items or not isinstance(items, list):
        return default
    # ... основная логика (без вложенности) ...
```

---

### 2.5 Replace Temp with Query

**Когда:** Временная переменная используется один раз и её вычисление можно вынести.

```python
# До
data_field = data.get("data") if isinstance(data, dict) else None
if not isinstance(data_field, dict):
    return result_text
items = data_field.get("items")

# После — метод-запрос
def _extract_items(self, data: dict) -> list[dict] | None:
    """Извлечь items из ответа API."""
    data_field = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data_field, dict):
        return None
    items = data_field.get("items")
    return items if isinstance(items, list) else None
```

---

### 2.6 Decompose Conditional

**Когда:** Сложное условие трудно прочитать.

```python
# До
if cached and cached.get("unit", "шт") in self._DISCRETE_UNITS:

# После
def _is_discrete_unit(self, xml_id: int) -> bool:
    """Проверить, что товар продаётся в дискретных единицах (шт, уп)."""
    cached = self._price_cache.get(xml_id)
    if cached is None:
        return True  # по умолчанию считаем штучным
    return cached.get("unit", "шт") in self._DISCRETE_UNITS
```

---

### 2.7 Introduce Parameter Object

**Когда:** Группа параметров всегда передаётся вместе.

```python
# До — параметры разбросаны
def __init__(self, credentials: str, model: str, scope: str,
             max_tool_calls: int, max_history: int):

# После — сгруппированы
from dataclasses import dataclass

@dataclass(frozen=True)
class GigaChatConfig:
    """Конфигурация GigaChat-клиента."""
    credentials: str
    model: str
    scope: str
    max_tool_calls: int = 15
    max_history: int = 50

def __init__(self, config: GigaChatConfig, ...):
```

---

### 2.8 Replace Inline Import with Top-Level

**Когда:** `import` внутри метода без веской причины.

```python
# ❌ Импорт внутри метода (замедляет, путает)
def _enhance_cart_schema(params: dict) -> dict:
    import copy
    params = copy.deepcopy(params)

# ✅ Импорт на верхнем уровне
import copy

def _enhance_cart_schema(params: dict) -> dict:
    params = copy.deepcopy(params)
```

**Исключение:** циклические зависимости, условные импорты, тяжёлые библиотеки.

---

### 2.9 Extract Constant

**Когда:** Литерал повторяется или его смысл неочевиден.

```python
# До
result[:1000]
if len(text) > 4096:
if call_counts[call_key] >= 2:

# После
MAX_RESULT_LOG_LENGTH = 1000
MAX_TELEGRAM_MESSAGE_LENGTH = 4096
MAX_IDENTICAL_TOOL_CALLS = 2
```

---

### 2.10 Async-специфичные паттерны

#### Replace `asyncio.to_thread` с нативным async

```python
# До — синхронный SDK оборачивается в to_thread
response = await asyncio.to_thread(self._client.chat, chat)

# Если SDK поддерживает async — используй напрямую
response = await self._client.achat(chat)
```

#### Extract Async Context Manager

```python
# До — повторяющийся паттерн init/cleanup
client = GigaChat(...)
try:
    result = await client.chat(...)
finally:
    client.close()

# После
from contextlib import asynccontextmanager

@asynccontextmanager
async def gigachat_session(config: GigaChatConfig):
    client = GigaChat(**config.__dict__)
    try:
        yield client
    finally:
        await asyncio.to_thread(client.close)
```

---

## Часть 3: Антипаттерны — чего НЕ делать

### ❌ Refactor and Feature — одновременно

Никогда не добавляй новую функциональность во время рефакторинга. Это два разных вида работ.

### ❌ Big Bang Refactoring

Не переписывай всё за один раз. Делай маленькие шаги, проверяй тесты после каждого.

### ❌ Speculative Generality

Не создавай абстракции «на будущее». Рефакторинг по факту, не по гипотезе.

### ❌ Rename Everything

Не переименовывай всё подряд. Переименовывай только то, что действительно запутывает.

### ❌ Over-Engineering

Не заменяй простой `if/elif` на паттерн Strategy с 5 классами, если веток всего 3.
