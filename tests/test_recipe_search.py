"""Тесты RecipeSearchService."""

import json
from unittest.mock import AsyncMock

import pytest

from vkuswill_bot.services.recipe_search import RecipeSearchService
from vkuswill_bot.services.search_processor import SearchProcessor


@pytest.fixture
def mock_mcp_client() -> AsyncMock:
    client = AsyncMock()
    return client


@pytest.fixture
def search_processor() -> SearchProcessor:
    return SearchProcessor()


@pytest.fixture
def service(mock_mcp_client, search_processor) -> RecipeSearchService:
    return RecipeSearchService(
        mcp_client=mock_mcp_client,
        search_processor=search_processor,
        max_concurrency=5,
    )


def _search_response(query: str, items: list[dict]) -> str:
    return json.dumps(
        {
            "ok": True,
            "data": {
                "meta": {"q": query},
                "items": items,
            },
        },
        ensure_ascii=False,
    )


class TestRecipeSearchService:
    async def test_empty_ingredients_returns_error(self, service):
        result = await service.search_ingredients([])
        parsed = json.loads(result)
        assert parsed["ok"] is False
        assert "Пустой" in parsed["error"]

    async def test_batch_search_success(self, service, mock_mcp_client):
        mock_mcp_client.call_tool.side_effect = [
            _search_response(
                "морковь",
                [
                    {
                        "xml_id": 100,
                        "name": "Морковь свежая",
                        "price": {"current": 79},
                        "unit": "кг",
                        "weight": {"value": 1, "unit": "кг"},
                    },
                    {
                        "xml_id": 101,
                        "name": "Морковь мытая",
                        "price": {"current": 89},
                        "unit": "кг",
                        "weight": {"value": 1, "unit": "кг"},
                    },
                ],
            ),
            _search_response(
                "лук репчатый",
                [
                    {
                        "xml_id": 200,
                        "name": "Лук репчатый",
                        "price": {"current": 45},
                        "unit": "шт",
                        "weight": {"value": 100, "unit": "г"},
                    },
                ],
            ),
        ]

        ingredients = [
            {"name": "морковь", "search_query": "морковь", "quantity": 0.5, "unit": "кг"},
            {"name": "лук", "search_query": "лук репчатый", "quantity": 2, "unit": "шт"},
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["not_found"] == []
        assert len(parsed["results"]) == 2
        assert parsed["results"][0]["best_match"]["xml_id"] == 100
        assert parsed["results"][0]["best_match"]["suggested_q"] == 0.5
        assert parsed["results"][1]["best_match"]["xml_id"] == 200
        assert parsed["results"][1]["best_match"]["suggested_q"] == 2
        assert parsed["search_log"]["морковь"] == [100, 101]
        assert parsed["search_log"]["лук репчатый"] == [200]

    async def test_not_found_item(self, service, mock_mcp_client):
        mock_mcp_client.call_tool.return_value = _search_response("лавровый лист", [])
        ingredients = [
            {"name": "лавровый лист", "search_query": "лавровый лист", "quantity": 1, "unit": "шт"}
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert parsed["not_found"] == ["лавровый лист"]
        assert parsed["results"][0]["best_match"] is None
        assert parsed["search_log"] == {}

    async def test_discrete_weight_based_suggested_q(self, service, mock_mcp_client):
        mock_mcp_client.call_tool.return_value = _search_response(
            "томатная паста",
            [
                {
                    "xml_id": 300,
                    "name": "Томатная паста",
                    "price": {"current": 139},
                    "unit": "шт",
                    "weight": {"value": 200, "unit": "г"},
                }
            ],
        )
        ingredients = [
            {
                "name": "томатная паста",
                "search_query": "томатная паста",
                "quantity": 400,
                "unit": "г",
            }
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)
        assert parsed["results"][0]["best_match"]["suggested_q"] == 2

    async def test_kg_equivalent_for_nonstandard_units(self, service, mock_mcp_client):
        """kg_equivalent позволяет точно рассчитать q для зубчиков, ст.л. и т.д."""
        mock_mcp_client.call_tool.side_effect = [
            _search_response(
                "чеснок",
                [
                    {
                        "xml_id": 500,
                        "name": "Чеснок, 100 г",
                        "price": {"current": 107},
                        "unit": "шт",
                        "weight": {"value": 100, "unit": "г"},
                    }
                ],
            ),
            _search_response(
                "томатная паста",
                [
                    {
                        "xml_id": 501,
                        "name": "Томатная паста 70 г",
                        "price": {"current": 90},
                        "unit": "шт",
                        "weight": {"value": 70, "unit": "г"},
                    }
                ],
            ),
            _search_response(
                "укроп",
                [
                    {
                        "xml_id": 502,
                        "name": "Укроп, 50 г",
                        "price": {"current": 80},
                        "unit": "шт",
                        "weight": {"value": 50, "unit": "г"},
                    }
                ],
            ),
        ]
        ingredients = [
            # 3 зубчика ≈ 15г → 1 упаковка 100г (а не 3!)
            {
                "name": "чеснок",
                "search_query": "чеснок",
                "quantity": 3,
                "unit": "зубчик",
                "kg_equivalent": 0.015,
            },
            # 2 ст.л. пасты ≈ 60г → 1 упаковка 70г
            {
                "name": "томатная паста",
                "search_query": "томатная паста",
                "quantity": 2,
                "unit": "ст.л.",
                "kg_equivalent": 0.06,
            },
            # 1 пучок укропа ≈ 30г → 1 упаковка 50г
            {
                "name": "укроп",
                "search_query": "укроп",
                "quantity": 1,
                "unit": "пучок",
                "kg_equivalent": 0.03,
            },
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        # Чеснок: ceil(15г / 100г) = 1, не ceil(3) = 3
        assert parsed["results"][0]["best_match"]["suggested_q"] == 1
        # Томатная паста: ceil(60г / 70г) = 1, не ceil(2) = 2
        assert parsed["results"][1]["best_match"]["suggested_q"] == 1
        # Укроп: ceil(30г / 50г) = 1, не ceil(1) = 1 (совпадает, но по правильной причине)
        assert parsed["results"][2]["best_match"]["suggested_q"] == 1

    async def test_kg_equivalent_without_weight_info(self, service, mock_mcp_client):
        """kg_equivalent для весового товара (unit=кг) — используется напрямую."""
        mock_mcp_client.call_tool.return_value = _search_response(
            "свинина",
            [
                {
                    "xml_id": 600,
                    "name": "Вырезка из свинины",
                    "price": {"current": 799},
                    "unit": "кг",
                }
            ],
        )
        ingredients = [
            {
                "name": "свинина",
                "search_query": "свинина",
                "quantity": 1050,
                "unit": "г",
                "kg_equivalent": 1.05,
            },
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)
        assert parsed["results"][0]["best_match"]["suggested_q"] == 1.05

    async def test_micro_units_without_kg_equivalent_default_to_one(
        self,
        service,
        mock_mcp_client,
    ):
        """Микро-единицы (зубчик, ст.л., пучок) без kg_equivalent → q=1."""
        mock_mcp_client.call_tool.side_effect = [
            _search_response(
                "чеснок",
                [
                    {
                        "xml_id": 700,
                        "name": "Чеснок Фермерский, 100 г",
                        "price": {"current": 145},
                        "unit": "шт",
                        "weight": {"value": 100, "unit": "г"},
                    }
                ],
            ),
            _search_response(
                "укроп",
                [
                    {
                        "xml_id": 701,
                        "name": "Укроп, 50 г",
                        "price": {"current": 80},
                        "unit": "шт",
                        "weight": {"value": 50, "unit": "г"},
                    }
                ],
            ),
            _search_response(
                "лавровый лист",
                [
                    {
                        "xml_id": 702,
                        "name": "Лавровый лист 10 г",
                        "price": {"current": 40},
                        "unit": "шт",
                    }
                ],
            ),
        ]
        ingredients = [
            {
                "name": "чеснок",
                "search_query": "чеснок",
                "quantity": 3,
                "unit": "зубчик",
            },
            {
                "name": "укроп",
                "search_query": "укроп",
                "quantity": 1.5,
                "unit": "пучок",
            },
            {
                "name": "лавровый лист",
                "search_query": "лавровый лист",
                "quantity": 3,
                "unit": "лист",
            },
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        # Микро-единицы без kg_equivalent → всегда q=1
        assert parsed["results"][0]["best_match"]["suggested_q"] == 1
        assert parsed["results"][1]["best_match"]["suggested_q"] == 1
        assert parsed["results"][2]["best_match"]["suggested_q"] == 1

    async def test_discrete_q_capped_at_max(self, service, mock_mcp_client):
        """suggested_q для дискретных товаров ограничен _MAX_DISCRETE_Q."""
        mock_mcp_client.call_tool.return_value = _search_response(
            "томатная паста",
            [
                {
                    "xml_id": 800,
                    "name": "Томатная паста 70 г",
                    "price": {"current": 90},
                    "unit": "шт",
                    "weight": {"value": 70, "unit": "г"},
                }
            ],
        )
        ingredients = [
            {
                "name": "томатная паста",
                "search_query": "томатная паста",
                "quantity": 3,
                "unit": "ст.л.",
                "kg_equivalent": 0.45,
            },
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        # ceil(450/70) = 7, но cap = 5
        assert parsed["results"][0]["best_match"]["suggested_q"] == 5

    async def test_partial_errors_do_not_break_batch(self, service, mock_mcp_client):
        mock_mcp_client.call_tool.side_effect = [
            RuntimeError("mcp unavailable"),
            _search_response(
                "мука",
                [{"xml_id": 400, "name": "Мука пшеничная", "price": {"current": 89}, "unit": "кг"}],
            ),
        ]
        ingredients = [
            {"name": "сыр", "search_query": "сыр", "quantity": 0.2, "unit": "кг"},
            {"name": "мука", "search_query": "мука", "quantity": 1, "unit": "кг"},
        ]
        result = await service.search_ingredients(ingredients)
        parsed = json.loads(result)

        assert parsed["ok"] is True
        assert "сыр" in parsed["not_found"]
        assert parsed["results"][0]["best_match"] is None
        assert parsed["results"][1]["best_match"]["xml_id"] == 400
