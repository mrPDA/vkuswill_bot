"""ShoppingAgent: OpenAI-compatible chat engine поверх MCP."""

from __future__ import annotations

import asyncio
import contextlib
from collections import OrderedDict
from typing import TYPE_CHECKING, Any

from vkuswill_bot.agents.intent_classifier import (
    IntentClassificationResult,
    classify_user_intent,
)
from vkuswill_bot.agents.mcp_tool_gateway import McpToolGateway
from vkuswill_bot.agents.shopping_agent_runtime_mixin import ShoppingAgentRuntimeMixin
from vkuswill_bot.agents.shopping_agent_service_mixin import ShoppingAgentServiceMixin
from vkuswill_bot.agents.tool_result_compactor import ToolResultCompactor
from vkuswill_bot.services.prompts import PromptProfile
from vkuswill_bot.services.meal_plan_metrics import (
    MealPlanRolloutController,
    PostgresMealPlanMetricsReader,
    get_meal_plan_metrics_sink,
)
from vkuswill_bot.services.llm_adapters import (
    LLMAdapterProtocol,
    OpenAICompatibleLLMAdapter,
    create_llm_adapter,
    normalize_llm_provider,
)

if TYPE_CHECKING:
    from vkuswill_bot.services.dialog_manager import DialogManager
    from vkuswill_bot.services.langfuse_tracing import LangfuseService
    from vkuswill_bot.services.mcp_client import VkusvillMCPClient
    from vkuswill_bot.services.preferences_store import PreferencesStore
    from vkuswill_bot.services.user_store import UserStore
    from vkuswill_bot.services.redis_dialog_manager import RedisDialogManager

_DEFAULT_MAX_TOOL_CALLS = 20
_DEFAULT_MAX_HISTORY = 30
_DEFAULT_LLM_TIMEOUT_SECONDS = 30.0
_DEFAULT_LLM_RETRIES = 2
_DEFAULT_MCP_TIMEOUT_SECONDS = 15.0
_DEFAULT_MCP_RETRIES = 2
_DEFAULT_LLM_ROUTING_STRATEGY = "single_provider"
_DEFAULT_MAX_TOOL_RESULT_CHARS = 1800
_DEFAULT_MAX_HISTORY_CHARS = 16000
_DEFAULT_MAX_INPUT_CHARS_PER_TURN = 250000
_DEFAULT_MAX_ACTIVE_USERS = 2000
_SUPPORTED_LLM_PROVIDER = "qwen_openai"


class ShoppingAgent(ShoppingAgentRuntimeMixin, ShoppingAgentServiceMixin):
    """Новый chat engine для варианта E (Qwen over MCP)."""

    def __init__(
        self,
        *,
        llm_base_url: str,
        llm_api_key: str,
        llm_model: str,
        llm_max_concurrent: int,
        mcp_client: VkusvillMCPClient,
        dialog_manager: DialogManager | RedisDialogManager,
        llm_provider: str = _SUPPORTED_LLM_PROVIDER,
        llm_routing_strategy: str = _DEFAULT_LLM_ROUTING_STRATEGY,
        max_tool_calls: int = _DEFAULT_MAX_TOOL_CALLS,
        max_history: int = _DEFAULT_MAX_HISTORY,
        langfuse_service: LangfuseService | None = None,
        llm_timeout_seconds: float = _DEFAULT_LLM_TIMEOUT_SECONDS,
        llm_max_tokens: int | None = None,
        llm_max_tokens_recipe: int = 2048,
        llm_temperature: float | None = None,
        prompt_profiles_enabled: bool = False,
        compact_followup_prompt_enabled: bool = True,
        llm_retries: int = _DEFAULT_LLM_RETRIES,
        mcp_timeout_seconds: float = _DEFAULT_MCP_TIMEOUT_SECONDS,
        mcp_retries: int = _DEFAULT_MCP_RETRIES,
        max_tool_result_chars: int = _DEFAULT_MAX_TOOL_RESULT_CHARS,
        max_history_chars: int = _DEFAULT_MAX_HISTORY_CHARS,
        max_input_chars_per_turn: int = _DEFAULT_MAX_INPUT_CHARS_PER_TURN,
        max_active_users: int = _DEFAULT_MAX_ACTIVE_USERS,
        preferences_store: PreferencesStore | None = None,
        llm_client: Any | None = None,
        llm_adapters: dict[str, LLMAdapterProtocol] | None = None,
        intent_classification_enabled: bool = False,
        intent_classification_timeout: float = 5.0,
        llm_queue_timeout_seconds: float = 15.0,
        meal_plan_intent_routing_enabled: bool = True,
        meal_plan_executor_enabled: bool = False,
        meal_plan_shadow_mode_enabled: bool = False,
        meal_plan_rollout_percent: int = 100,
        meal_plan_allow_unvalidated_rollout: bool = False,
        meal_plan_unvalidated_rollout_reason: str = "",
        meal_plan_unvalidated_rollout_actor: str = "",
        meal_plan_unvalidated_rollout_expires_at: str = "",
        meal_plan_unvalidated_rollout_max_ttl_seconds: int = 86400,
        deployment_environment: str = "production",
        user_store: UserStore | None = None,
        meal_plan_metrics_sink: Any | None = None,
    ) -> None:
        self._llm_provider = normalize_llm_provider(llm_provider)
        self._llm_routing_strategy = llm_routing_strategy.strip().lower()
        if self._llm_provider != _SUPPORTED_LLM_PROVIDER:
            raise ValueError(
                "ShoppingAgent supports only llm_provider='qwen_openai' (gigachat_sdk is disabled)",
            )
        if self._llm_routing_strategy != _DEFAULT_LLM_ROUTING_STRATEGY:
            raise ValueError(
                "ShoppingAgent supports only llm_routing_strategy='single_provider'",
            )
        self._model = llm_model
        self._mcp_client = mcp_client
        self._dialog_manager = dialog_manager
        self._max_tool_calls = max(1, max_tool_calls)
        self._max_history = max(10, max_history)
        self._llm_timeout_seconds = max(1.0, llm_timeout_seconds)
        self._llm_max_tokens = max(1, llm_max_tokens) if llm_max_tokens is not None else None
        self._llm_max_tokens_recipe = max(1, min(8192, llm_max_tokens_recipe))
        self._llm_temperature = (
            max(0.0, min(1.0, llm_temperature)) if llm_temperature is not None else None
        )
        self._prompt_profiles_enabled = bool(prompt_profiles_enabled)
        self._compact_followup_prompt_enabled = bool(compact_followup_prompt_enabled)
        self._llm_retries = max(0, llm_retries)
        self._mcp_timeout_seconds = max(1.0, mcp_timeout_seconds)
        self._mcp_retries = max(0, mcp_retries)
        self._max_tool_result_chars = max(300, max_tool_result_chars)
        self._compactor = ToolResultCompactor(max_tool_result_chars=self._max_tool_result_chars)
        self._max_history_chars = max(10000, max_history_chars)
        self._max_input_chars_per_turn = max(10000, max_input_chars_per_turn)
        self._max_active_users = max(1, max_active_users)
        self._preferences_store = preferences_store
        self._intent_classification_enabled = bool(intent_classification_enabled)
        self._intent_classification_timeout = max(1.0, intent_classification_timeout)
        self._meal_plan_intent_routing_enabled = bool(meal_plan_intent_routing_enabled)
        self._meal_plan_executor_enabled = bool(meal_plan_executor_enabled)
        self._meal_plan_shadow_mode_enabled = bool(meal_plan_shadow_mode_enabled)
        self._meal_plan_rollout_percent = max(0, min(100, int(meal_plan_rollout_percent)))
        self._meal_plan_allow_unvalidated_rollout = bool(meal_plan_allow_unvalidated_rollout)
        self._meal_plan_unvalidated_rollout_reason = str(
            meal_plan_unvalidated_rollout_reason
        ).strip()
        self._meal_plan_unvalidated_rollout_actor = str(meal_plan_unvalidated_rollout_actor).strip()
        self._meal_plan_unvalidated_rollout_expires_at = str(
            meal_plan_unvalidated_rollout_expires_at
        ).strip()
        self._meal_plan_unvalidated_rollout_max_ttl_seconds = max(
            1, int(meal_plan_unvalidated_rollout_max_ttl_seconds)
        )
        env = str(deployment_environment).strip().lower()
        self._deployment_environment = env or "production"
        self._user_store = user_store
        self._meal_plan_rollout_controller = None
        pool = getattr(self._user_store, "_pool", None)
        if pool is not None:
            self._meal_plan_rollout_controller = MealPlanRolloutController(
                metrics_reader=PostgresMealPlanMetricsReader(pool=pool),
            )
        if meal_plan_metrics_sink is not None:
            self._meal_plan_metrics_sink = meal_plan_metrics_sink
        else:

            async def _metrics_event_logger(
                event_user_id: int,
                event_type: str,
                metadata: dict[str, Any],
            ) -> None:
                if self._user_store is None:
                    return
                with contextlib.suppress(Exception):
                    await self._user_store.log_event(
                        user_id=event_user_id,
                        event_type=event_type,
                        metadata=metadata,
                    )

            self._meal_plan_metrics_sink = get_meal_plan_metrics_sink(
                event_logger=_metrics_event_logger if self._user_store is not None else None,
            )
        self._api_semaphore = asyncio.Semaphore(max(1, llm_max_concurrent))
        self._llm_queue_timeout_seconds = max(1.0, llm_queue_timeout_seconds)
        self._langfuse = langfuse_service
        self._tools_cache: list[dict[str, Any]] | None = None
        self._mcp_tool_names: set[str] = set()
        self._history: dict[int, list[dict[str, Any]]] = {}
        self._last_cart_snapshot: dict[int, dict[str, Any]] = {}
        self._active_users: OrderedDict[int, None] = OrderedDict()

        self._providers_in_use = self._compute_providers_in_use()
        self._llm_adapters: dict[str, LLMAdapterProtocol] = {}
        self._mcp_gateway: McpToolGateway | None = None

        if llm_adapters is not None:
            normalized_adapters = {
                normalize_llm_provider(name): adapter for name, adapter in llm_adapters.items()
            }
            self._llm_adapters.update(normalized_adapters)
            missing = self._providers_in_use - set(self._llm_adapters.keys())
            if missing:
                missing_str = ", ".join(sorted(missing))
                raise ValueError(f"llm_adapters are missing providers: {missing_str}")
            self._mcp_gateway = self._create_mcp_gateway()
            return

        if llm_client is not None:
            if len(self._providers_in_use) != 1:
                raise ValueError("llm_client injection supports single-provider routing only")
            provider = next(iter(self._providers_in_use))
            if isinstance(llm_client, LLMAdapterProtocol):
                self._llm_adapters[provider] = llm_client
            else:
                self._llm_adapters[provider] = OpenAICompatibleLLMAdapter(
                    timeout_seconds=self._llm_timeout_seconds,
                    client=llm_client,
                )
            self._mcp_gateway = self._create_mcp_gateway()
            return

        for provider in self._providers_in_use:
            self._llm_adapters[provider] = create_llm_adapter(
                provider=provider,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_timeout_seconds=self._llm_timeout_seconds,
            )
        self._mcp_gateway = self._create_mcp_gateway()

    async def _classify_intent(
        self,
        text: str,
        *,
        trace: Any | None = None,
    ) -> PromptProfile | IntentClassificationResult | None:
        """Classify user intent via LLM. Returns None to fall back to keywords."""
        if not self._intent_classification_enabled:
            return None
        adapter = self._llm_adapters.get(self._llm_provider)
        if adapter is None:
            return None
        return await classify_user_intent(
            text,
            adapter,
            self._model,
            timeout_seconds=self._intent_classification_timeout,
            trace=trace,
        )
