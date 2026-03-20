.PHONY: help install test test-cov test-security secret-scan lint format run run-debug verify-llm clean \
       docker-build docker-up docker-down docker-up-pi docker-up-pi-caddy docker-up-pi-tunnel \
       docker-down-pi docker-logs-pi docker-pi-langfuse-import-prompts docker-logs docker-ps \
       tf-init tf-plan tf-apply tf-destroy build-alice-zip

# Цвета
BLUE := \033[34m
GREEN := \033[32m
YELLOW := \033[33m
RESET := \033[0m
BOLD := \033[1m

help: ## Показать справку
	@echo "$(BOLD)Команды для vkuswill-bot$(RESET)"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  $(BLUE)%-15s$(RESET) %s\n", $$1, $$2}'

# ─── Разработка ───────────────────────────────────────────────────────────────

install: ## Установить зависимости
	uv sync --all-extras

test: ## Запустить тесты
	uv run pytest -v

test-cov: ## Тесты с покрытием
	uv run pytest --cov=src/vkuswill_bot --cov-report=term-missing --cov-report=html

test-security: ## Тесты безопасности
	uv run pytest tests/test_security_sast.py tests/test_config_security.py tests/test_ai_safety.py -v

secret-scan: ## Поиск утечек секретов (требует gitleaks)
	gitleaks detect --source . --no-banner --redact --config .gitleaks.toml

lint: ## Проверка линтером (ruff)
	uv run ruff check src/ tests/

format: ## Форматирование кода (ruff)
	uv run ruff format src/ tests/

# ─── Бот ──────────────────────────────────────────────────────────────────────

run: ## Запустить бота
	uv run python -m vkuswill_bot

run-debug: ## Запустить бота в режиме отладки
	DEBUG=true uv run python -m vkuswill_bot

# ─── Утилиты ──────────────────────────────────────────────────────────────────

clean: ## Очистить кэши и временные файлы
	rm -rf __pycache__ .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov coverage.xml
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "$(GREEN)Кэши очищены.$(RESET)"

setup-hooks: ## Настроить git hooks
	git config core.hooksPath .githooks
	@echo "$(GREEN)Git hooks настроены (.githooks/)$(RESET)"

verify-llm: ## Проверить LLM (Yandex AI Studio) по переменным из .env; таймаут до 120 с
	python3 scripts/verify_llm_yandex.py

# ─── Docker ──────────────────────────────────────────────────────────────────

docker-build: ## Собрать Docker-образ
	docker build -t vkuswill-bot:latest .
	@echo "$(GREEN)Образ vkuswill-bot:latest собран.$(RESET)"

docker-up: ## Запустить бота + Redis + PostgreSQL (docker compose)
	docker compose up -d
	@echo "$(GREEN)Сервисы запущены. Логи: make docker-logs$(RESET)"

docker-down: ## Остановить все контейнеры
	docker compose down
	@echo "$(YELLOW)Сервисы остановлены.$(RESET)"

docker-up-pi: ## Pi: Postgres + Langfuse + бот + AmneziaWG (см. .env.pi.example, импорт промптов: make docker-pi-langfuse-import-prompts)
	docker compose -f docker-compose.pi.yml up -d --build
	@echo "$(GREEN)Pi-стек запущен. Логи: make docker-logs-pi$(RESET)"

docker-up-pi-caddy: ## Pi + Caddy (Let's Encrypt): задай WEBHOOK_DOMAIN и WEBHOOK_HOST в .env
	docker compose -f docker-compose.pi.yml --profile caddy up -d --build
	@echo "$(GREEN)Pi + Caddy. Логи бота: make docker-logs-pi$(RESET)"

docker-up-pi-tunnel: ## Pi + Cloudflare Tunnel: задай CLOUDFLARE_TUNNEL_TOKEN и WEBHOOK_HOST в .env
	docker compose -f docker-compose.pi.yml --profile cf-tunnel up -d --build
	@echo "$(GREEN)Pi + cloudflared. Логи: docker compose -f docker-compose.pi.yml logs -f cloudflared$(RESET)"

docker-down-pi: ## Остановить Pi-стек (включая AmneziaWG, caddy, cf-tunnel)
	docker compose -f docker-compose.pi.yml --profile caddy --profile cf-tunnel down --remove-orphans
	@echo "$(YELLOW)Pi-стек остановлен.$(RESET)"

docker-logs-pi: ## Логи Pi-стека
	docker compose -f docker-compose.pi.yml logs -f --tail=100

docker-pi-langfuse-import-prompts: ## Pi: залить промпты из prompts/langfuse-export в self-hosted Langfuse
	docker compose -f docker-compose.pi.yml exec bot uv run python scripts/import_prompts_from_langfuse_export.py --label production
	@echo "$(GREEN)При необходимости: docker compose -f docker-compose.pi.yml exec bot uv run python scripts/migrate_prompts_to_langfuse.py --label production$(RESET)"

docker-logs: ## Показать логи всех контейнеров
	docker compose logs -f --tail=100

docker-ps: ## Статус контейнеров
	docker compose ps

# ─── Terraform ───────────────────────────────────────────────────────────────

tf-init: ## Инициализировать Terraform (требует infra/backend.conf)
	cd infra && terraform init -backend-config=backend.conf
	@echo "$(GREEN)Terraform инициализирован.$(RESET)"

tf-plan: ## Показать план изменений инфраструктуры
	cd infra && terraform plan

tf-apply: ## Применить изменения инфраструктуры
	cd infra && terraform apply
	@echo "$(GREEN)Инфраструктура обновлена.$(RESET)"

tf-destroy: ## Уничтожить инфраструктуру (ОСТОРОЖНО!)
	@echo "$(YELLOW)ВНИМАНИЕ: удаление всех ресурсов YC!$(RESET)"
	cd infra && terraform destroy

build-alice-zip: ## Собрать ZIP-артефакт serverless-функции Алисы (linux-совместимый)
	bash scripts/build_alice_function_zip.sh
	@echo "$(GREEN)Alice Function ZIP собран: dist/alice-skill.zip$(RESET)"
