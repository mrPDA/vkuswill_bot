"""Живое тестирование бота через персоны (Лютик).

Инициализирует GigaChatService с реальными credentials из .env,
загружает системный промпт и отправляет сообщения от имени
разных персон напрямую в process_message().

Тестирует полную цепочку: системный промпт → GigaChat → MCP → ответ.

Использование:
    uv run python tests/persona_live_test.py
    uv run python tests/persona_live_test.py --persona alina
    uv run python tests/persona_live_test.py --persona boris vera
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# Добавляем src в path для импорта
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from vkuswill_bot.config import Config
from vkuswill_bot.services.dialog_manager import DialogManager
from vkuswill_bot.services.gigachat_service import GigaChatService
from vkuswill_bot.services.mcp_client import VkusvillMCPClient
from vkuswill_bot.services.preferences_store import PreferencesStore
from vkuswill_bot.services.recipe_store import RecipeStore
from vkuswill_bot.services.price_cache import PriceCache
from vkuswill_bot.services.search_processor import SearchProcessor
from vkuswill_bot.services.cart_processor import CartProcessor
from vkuswill_bot.services.tool_executor import ToolExecutor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("persona_test")

# Уменьшаем шум от httpx и gigachat
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("gigachat").setLevel(logging.WARNING)


# ---------------------------------------------------------------------------
# Результаты
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    """Результат одного шага диалога."""

    step: int
    user_message: str
    bot_response: str
    latency_sec: float
    success: bool
    error: str = ""


@dataclass
class DialogResult:
    """Результат одного диалога (персона)."""

    persona: str
    dialog_id: str
    description: str
    steps: list[StepResult] = field(default_factory=list)
    total_latency_sec: float = 0.0
    verdict: str = ""  # Заполняется при анализе
    issues: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Персоны и сценарии
# ---------------------------------------------------------------------------

PERSONAS: dict[str, list[dict]] = {
    "alina": [
        {
            "dialog_id": "D-001",
            "persona": "Алина (ЗОЖ, фитнес)",
            "description": "Здоровый завтрак с КБЖУ — одно сообщение",
            "messages": [
                "Собери мне здоровый завтрак — овсянка, ягоды, "
                "греческий йогурт и миндальное молоко. Покажи КБЖУ каждого продукта"
            ],
            "checks": [
                "корзина со ссылкой",
                "КБЖУ/калории упомянуты",
                "дисклеймер о наличии",
            ],
        },
    ],
    "boris": [
        {
            "dialog_id": "D-002",
            "persona": "Борис (папа, семья из 4)",
            "description": "Ужин на семью — одно сообщение, 6 продуктов",
            "messages": [
                "Купи на ужин на 4 человек: куриные грудки, рис, брокколи, помидоры, сметана и хлеб"
            ],
            "checks": [
                "корзина со ссылкой",
                "все 6 позиций",
                "количества масштабированы (не всё q=1)",
            ],
        },
        {
            "dialog_id": "D-011",
            "persona": "Борис (папа, спешит)",
            "description": "Готовые обеды на 3 дня — минимальный ввод",
            "messages": [
                "Готовые обеды на 4 чел на 3 дня"
            ],
            "checks": [
                "не задаёт вопросов — сразу корзина",
                "разнообразие (не одно блюдо)",
                "ссылка на корзину",
            ],
        },
    ],
    "vera": [
        {
            "dialog_id": "D-003",
            "persona": "Вера (нерешительная)",
            "description": "Не знает что хочет — 5 шагов беседы",
            "messages": [
                "Хочу приготовить что-нибудь вкусное на ужин, но не знаю что...",
                "Может пасту? Или нет... А что-нибудь с курицей?",
                "Давай курицу с овощами. Хотя... а есть что-то попроще? Типа готовое?",
                "Ладно, давай готовое. Что есть из готовых блюд с курицей?",
                "А добавь ещё салат какой-нибудь и хлеб",
            ],
            "checks": [
                "бот терпелив (не завалил вопросами)",
                "понял смену намерения (приготовить → готовое)",
                "финальная корзина содержит всё (блюдо + салат + хлеб)",
                "контекст сохранился между сообщениями",
            ],
        },
    ],
    "gleb": [
        {
            "dialog_id": "D-004",
            "persona": "Глеб (гурман)",
            "description": "Тирамису на двоих — рецепт с экзотикой",
            "messages": [
                "Хочу приготовить тирамису на двоих",
            ],
            "checks": [
                "recipe_ingredients вызван (ингредиенты в ответе)",
                "маскарпоне упомянут",
                "корзина со ссылкой",
                "servings=2 (на двоих)",
            ],
        },
    ],
    "darya": [
        {
            "dialog_id": "D-012",
            "persona": "Дарья (студентка, бюджет)",
            "description": "Дешёвая еда за 500 руб — сленг",
            "messages": [
                "Чё есть самого дешёвого пожрать? Рублей на 500 чтобы хватило на 3 дня",
                "Ну норм, но гречку не хочу, есть макарохи какие подешевле?",
            ],
            "checks": [
                "бот понял сленг (пожрать, макарохи)",
                "предложил дешёвые товары",
                "корзина со ссылкой",
                "на втором шаге — замена, не повтор",
            ],
        },
    ],
    "evgeny": [
        {
            "dialog_id": "D-006",
            "persona": "Евгений (кулинар)",
            "description": "Борщ на 8 человек + сметана + КБЖУ",
            "messages": [
                "Хочу приготовить настоящий борщ на 8 человек",
                "Добавь ещё сметану и чёрный хлеб к корзине",
                "Покажи КБЖУ борща",
            ],
            "checks": [
                "recipe_ingredients с servings=8",
                "полный набор ингредиентов (свёкла, капуста, мясо...)",
                "сметана и хлеб добавлены",
                "КБЖУ через nutrition_lookup",
            ],
        },
    ],
    "zhanna": [
        {
            "dialog_id": "D-007",
            "persona": "Жанна (мама, аллергия на глютен)",
            "description": "Еда без глютена для ребёнка — безопасность",
            "messages": [
                "Запомни, что у моего ребёнка аллергия на глютен. Нам нужно всё без глютена",
                "Собери завтрак для ребёнка — каша, молоко и что-нибудь сладкое",
                "А эта каша точно без глютена? Какой состав?",
            ],
            "checks": [
                "предпочтение сохранено",
                "поиск с учётом 'без глютена'",
                "предупреждение о проверке состава",
                "не выдумывает состав товара",
            ],
        },
    ],
    "zahar": [
        {
            "dialog_id": "D-008",
            "persona": "Захар (вечеринка на 10 чел)",
            "description": "Большой заказ закусок — одно сообщение",
            "messages": [
                "Бро, вечеринка на 10 чел! Нужно: чипсы, орешки, "
                "сыр нарезка, колбаса, хумус, овощи для нарезки "
                "(огурцы помидоры перец), хлеб, соус, напитки — "
                "сок и газировка. И пиццу готовую пару штук!"
            ],
            "checks": [
                "найдено большинство позиций (8+)",
                "пицца как готовая (не рецепт)",
                "корзина со ссылкой",
                "масштабирование на 10 чел",
            ],
        },
    ],
}

ALL_PERSONA_KEYS = list(PERSONAS.keys())


# ---------------------------------------------------------------------------
# Инициализация сервисов
# ---------------------------------------------------------------------------


async def create_services() -> tuple[
    GigaChatService, PreferencesStore, RecipeStore, VkusvillMCPClient
]:
    """Создать все сервисы как в __main__.py, но без Telegram."""
    # Загружаем конфиг из .env
    cfg = Config()

    logger.info("Инициализация сервисов...")
    logger.info("  GigaChat модель: %s", cfg.gigachat_model)
    logger.info("  MCP сервер: %s", cfg.mcp_server_url)

    # MCP-клиент
    mcp_client = VkusvillMCPClient(cfg.mcp_server_url)

    # Хранилища
    test_data_dir = Path("data")
    test_data_dir.mkdir(exist_ok=True)
    prefs_store = PreferencesStore(cfg.database_path)
    recipe_store = RecipeStore(cfg.recipe_database_path)

    # Процессоры
    price_cache = PriceCache()
    search_processor = SearchProcessor(price_cache)
    cart_processor = CartProcessor(price_cache)

    # КБЖУ-сервис
    from vkuswill_bot.services.nutrition_service import NutritionService
    nutrition_service = NutritionService()

    # Снимки корзины (in-memory для теста)
    from vkuswill_bot.services.cart_snapshot_store import InMemoryCartSnapshotStore
    cart_snapshot_store = InMemoryCartSnapshotStore()

    # Исполнитель инструментов
    tool_executor = ToolExecutor(
        mcp_client=mcp_client,
        search_processor=search_processor,
        cart_processor=cart_processor,
        preferences_store=prefs_store,
        cart_snapshot_store=cart_snapshot_store,
        nutrition_service=nutrition_service,
    )

    # Менеджер диалогов (in-memory)
    dialog_manager = DialogManager(max_history=cfg.max_history_messages)

    # GigaChat-сервис
    gigachat_service = GigaChatService(
        credentials=cfg.gigachat_credentials,
        model=cfg.gigachat_model,
        scope=cfg.gigachat_scope,
        mcp_client=mcp_client,
        preferences_store=prefs_store,
        recipe_store=recipe_store,
        max_tool_calls=cfg.max_tool_calls,
        max_history=cfg.max_history_messages,
        dialog_manager=dialog_manager,
        tool_executor=tool_executor,
        gigachat_max_concurrent=cfg.gigachat_max_concurrent,
    )

    # Предзагрузка MCP-инструментов
    try:
        tools = await mcp_client.get_tools()
        logger.info("MCP инструменты: %s", [t["name"] for t in tools])
    except Exception as e:
        logger.warning("Не удалось загрузить MCP: %s", e)

    return gigachat_service, prefs_store, recipe_store, mcp_client


# ---------------------------------------------------------------------------
# Запуск диалога
# ---------------------------------------------------------------------------


async def run_dialog(
    gigachat_service: GigaChatService,
    dialog: dict,
    user_id: int,
) -> DialogResult:
    """Прогнать один диалог (персону) через бота."""
    result = DialogResult(
        persona=dialog["persona"],
        dialog_id=dialog["dialog_id"],
        description=dialog["description"],
    )

    messages = dialog["messages"]
    logger.info(
        "\n{'=' * 60}\n  %s: %s\n  %s\n{'=' * 60}",
        dialog["dialog_id"],
        dialog["persona"],
        dialog["description"],
    )

    for i, msg in enumerate(messages, 1):
        logger.info("  [Шаг %d] 👤: %s", i, msg[:80])
        start = time.monotonic()

        try:
            response = await gigachat_service.process_message(user_id, msg)
            latency = time.monotonic() - start
            step = StepResult(
                step=i,
                user_message=msg,
                bot_response=response,
                latency_sec=latency,
                success=True,
            )
            # Показываем первые 200 символов ответа
            preview = response[:200].replace("\n", " ")
            logger.info("  [Шаг %d] 🤖 (%.1fs): %s...", i, latency, preview)
        except Exception as e:
            latency = time.monotonic() - start
            step = StepResult(
                step=i,
                user_message=msg,
                bot_response="",
                latency_sec=latency,
                success=False,
                error=str(e),
            )
            logger.error("  [Шаг %d] ❌ (%.1fs): %s", i, latency, e)

        result.steps.append(step)
        result.total_latency_sec += latency

        # Пауза между сообщениями в мультитурне (даём GigaChat отдышаться)
        if i < len(messages):
            await asyncio.sleep(2.0)

    return result


# ---------------------------------------------------------------------------
# Анализ результатов
# ---------------------------------------------------------------------------


def analyze_dialog(dialog_result: DialogResult, checks: list[str]) -> None:
    """Проанализировать результаты диалога по чек-листу."""
    issues = []
    all_responses = " ".join(s.bot_response.lower() for s in dialog_result.steps)

    # Проверка: все шаги успешны?
    failed_steps = [s for s in dialog_result.steps if not s.success]
    if failed_steps:
        for s in failed_steps:
            issues.append(f"Шаг {s.step} упал с ошибкой: {s.error}")

    # Проверка: есть ли ссылка на корзину?
    if ("корзина со ссылкой" in checks or "ссылка на корзину" in checks) and (
        "href=" not in all_responses and "vkusvill.ru" not in all_responses
    ):
        issues.append("НЕТ ссылки на корзину в ответе")

    # Проверка: есть ли дисклеймер?
    if "дисклеймер о наличии" in checks and (
        "наличие" not in all_responses and "уточняйте" not in all_responses
    ):
        issues.append("НЕТ дисклеймера о наличии товаров")

    # Проверка: КБЖУ упомянут?
    if "КБЖУ/калории упомянуты" in checks or "КБЖУ через nutrition_lookup" in checks:
        kbzhu_keywords = ["ккал", "калори", "белк", "жир", "углевод", "кбжу", "бжу"]
        if not any(kw in all_responses for kw in kbzhu_keywords):
            issues.append("НЕТ данных о КБЖУ/калориях в ответе")

    # Проверка: рецепт?
    if (
        any("recipe" in c.lower() or "ингредиент" in c.lower() for c in checks)
        and "ингредиент" not in all_responses
        and len(all_responses) < 200
    ):
        issues.append("Ответ слишком короткий для рецепта")

    # Проверка: предупреждение для аллергиков?
    if "предупреждение о проверке состава" in checks:
        warn_keywords = ["проверь", "упаковк", "состав", "уточн", "гарантировать"]
        if not any(kw in all_responses for kw in warn_keywords):
            issues.append("НЕТ предупреждения о проверке состава (аллергены!)")

    # Проверка: ошибка "слишком много шагов"?
    if "слишком много шагов" in all_responses:
        issues.append("Бот исчерпал лимит tool_calls (max_tool_calls)")

    # Проверка: стандартная ошибка?
    if "произошла ошибка" in all_responses:
        issues.append("Бот вернул ошибку GigaChat")

    dialog_result.issues = issues
    if not issues:
        dialog_result.verdict = "✅ УСПЕХ"
    elif any("упал" in i or "ошибк" in i.lower() for i in issues):
        dialog_result.verdict = "❌ ПРОВАЛ"
    else:
        dialog_result.verdict = "⚠️ ЧАСТИЧНО"


# ---------------------------------------------------------------------------
# Генерация отчёта
# ---------------------------------------------------------------------------


def generate_report(results: list[DialogResult]) -> str:
    """Сгенерировать Markdown-отчёт."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# Отчёт Лютика: Живое тестирование персон",
        f"\n**Дата:** {now}",
        f"**Диалогов:** {len(results)}",
        "",
        "---",
        "",
        "## Сводка",
        "",
        "| # | Диалог | Персона | Шагов | Время | Вердикт |",
        "|---|--------|---------|:-----:|:-----:|:-------:|",
    ]

    for r in results:
        steps_ok = sum(1 for s in r.steps if s.success)
        lines.append(
            f"| {r.dialog_id} | {r.description[:40]} | {r.persona[:20]} "
            f"| {steps_ok}/{len(r.steps)} | {r.total_latency_sec:.1f}s | {r.verdict} |"
        )

    # Статистика
    success = sum(1 for r in results if r.verdict.startswith("✅"))
    partial = sum(1 for r in results if r.verdict.startswith("⚠️"))
    fail = sum(1 for r in results if r.verdict.startswith("❌"))

    lines.extend([
        "",
        f"**Итого:** ✅ {success} успех | ⚠️ {partial} частично | ❌ {fail} провал",
        "",
        "---",
        "",
    ])

    # Детали каждого диалога
    for r in results:
        lines.append(f"## {r.dialog_id}: {r.persona}")
        lines.append(f"\n**{r.description}**")
        lines.append(f"\n**Вердикт:** {r.verdict}")
        lines.append(f"**Время:** {r.total_latency_sec:.1f} сек")

        if r.issues:
            lines.append("\n**Проблемы:**")
            for issue in r.issues:
                lines.append(f"- ❗ {issue}")

        lines.append("\n**Диалог:**\n")
        for s in r.steps:
            lines.append(f"### Шаг {s.step} ({s.latency_sec:.1f}s)")
            lines.append(f"\n**👤 Пользователь:**\n```\n{s.user_message}\n```")
            if s.success:
                # Ограничиваем вывод ответа
                resp = s.bot_response
                if len(resp) > 2000:
                    resp = resp[:2000] + "\n\n... (обрезано, полный ответ в логах)"
                lines.append(f"\n**🤖 Бот:**\n```\n{resp}\n```")
            else:
                lines.append(f"\n**❌ Ошибка:**\n```\n{s.error}\n```")

        lines.append("\n---\n")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(persona_keys: list[str] | None = None) -> None:
    """Запуск живого тестирования."""
    keys = persona_keys or ALL_PERSONA_KEYS

    # Валидация ключей
    for k in keys:
        if k not in PERSONAS:
            logger.error("Неизвестная персона: %s. Доступные: %s", k, ALL_PERSONA_KEYS)
            return

    logger.info("=" * 60)
    logger.info("  ЛЮТИК: ЖИВОЕ ТЕСТИРОВАНИЕ ПЕРСОН")
    logger.info("  Персоны: %s", ", ".join(keys))
    logger.info("=" * 60)

    # Инициализация
    gigachat_service, prefs_store, recipe_store, mcp_client = await create_services()

    results: list[DialogResult] = []
    user_id_counter = 900000  # Фейковые user_id для тестов

    try:
        for key in keys:
            dialogs = PERSONAS[key]
            for dialog in dialogs:
                user_id_counter += 1
                user_id = user_id_counter

                # Сброс диалога перед каждой персоной
                await gigachat_service.reset_conversation(user_id)

                # Прогон диалога
                result = await run_dialog(gigachat_service, dialog, user_id)

                # Анализ
                analyze_dialog(result, dialog["checks"])
                results.append(result)

                logger.info(
                    "  >>> %s: %s (%s)",
                    dialog["dialog_id"],
                    result.verdict,
                    f"{result.total_latency_sec:.1f}s",
                )

                # Пауза между персонами
                await asyncio.sleep(3.0)

    finally:
        # Генерация отчёта
        report = generate_report(results)
        report_path = Path("tests/PERSONA_LIVE_RESULTS.md")
        report_path.write_text(report, encoding="utf-8")
        logger.info("\nОтчёт сохранён: %s", report_path)

        # Закрытие ресурсов
        await gigachat_service.close()
        await recipe_store.close()
        await prefs_store.close()
        await mcp_client.close()

    # Итого в консоль
    print("\n" + "=" * 60)
    print("  РЕЗУЛЬТАТЫ ЛЮТИКА")
    print("=" * 60)
    for r in results:
        print(f"  {r.dialog_id} {r.persona:30s} {r.verdict}")
        for issue in r.issues:
            print(f"         ❗ {issue}")
    print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Лютик: живое тестирование персон")
    parser.add_argument(
        "--persona",
        nargs="*",
        choices=ALL_PERSONA_KEYS,
        help=f"Персоны для тестирования (по умолчанию все). Доступные: {ALL_PERSONA_KEYS}",
    )
    args = parser.parse_args()

    asyncio.run(main(args.persona))
