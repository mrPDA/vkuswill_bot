# ============================================================
# Alice Skill — Yandex Serverless Function
# ============================================================

locals {
  alice_link_api_url_effective = trimspace(var.alice_link_api_url) != "" ? trimspace(var.alice_link_api_url) : "http://${yandex_compute_instance.bot.network_interface[0].ip_address}:8080/voice-link"
}

resource "yandex_function" "alice_skill" {
  count = var.alice_function_enabled ? 1 : 0

  name        = var.alice_function_name
  description = "Alice skill backend for VkusVill ordering via MCP"
  folder_id   = var.folder_id
  runtime     = "python311"
  entrypoint  = "vkuswill_bot.alice_skill.handler.handler"
  memory      = "256"

  execution_timeout = "10"
  user_hash         = filesha256(var.alice_function_zip_path)

  content {
    zip_filename = var.alice_function_zip_path
  }

  dynamic "connectivity" {
    for_each = var.alice_function_network_id == "" ? [] : [var.alice_function_network_id]
    content {
      network_id = connectivity.value
    }
  }

  environment = {
    ALICE_MCP_SERVER_URL                = var.mcp_server_url
    ALICE_MCP_API_KEY                   = var.mcp_server_api_key
    ALICE_LINK_API_URL                  = local.alice_link_api_url_effective
    ALICE_LINK_API_KEY                  = var.voice_link_api_key
    ALICE_LINK_API_TIMEOUT_SECONDS      = tostring(var.alice_link_api_timeout_seconds)
    ALICE_LINK_API_VERIFY_SSL           = tostring(var.alice_link_api_verify_ssl)
    ALICE_ORDER_API_URL                 = local.alice_link_api_url_effective
    ALICE_ORDER_API_KEY                 = var.voice_link_api_key
    ALICE_ORDER_API_TIMEOUT_SECONDS     = "12"
    ALICE_ORDER_API_VERIFY_SSL          = tostring(var.alice_link_api_verify_ssl)
    ALICE_SKILL_ID                      = var.alice_skill_id
    ALICE_REQUIRE_LINKED_ACCOUNT        = "true"
    ALICE_LINKING_FAIL_CLOSED           = tostring(var.alice_linking_fail_closed)
    ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR  = tostring(var.alice_degrade_to_guest_on_db_error)
    ALICE_DB_CONNECT_TIMEOUT_SECONDS    = tostring(var.alice_db_connect_timeout_seconds)
    ALICE_IDEMPOTENCY_TTL_SECONDS       = tostring(var.alice_idempotency_ttl_seconds)
    ALICE_IDEMPOTENCY_KEY_PREFIX        = var.alice_idempotency_key_prefix
    ALICE_RATE_LIMIT_KEY_PREFIX         = var.alice_rate_limit_key_prefix
    ALICE_ORDER_RATE_LIMIT              = tostring(var.alice_order_rate_limit)
    ALICE_ORDER_RATE_WINDOW_SECONDS     = tostring(var.alice_order_rate_window_seconds)
    ALICE_LINK_CODE_RATE_LIMIT          = tostring(var.alice_link_code_rate_limit)
    ALICE_LINK_CODE_RATE_WINDOW_SECONDS = tostring(var.alice_link_code_rate_window_seconds)
    ALICE_LANGFUSE_ENABLED              = tostring(var.alice_langfuse_enabled)
    ALICE_LANGFUSE_PUBLIC_KEY           = var.langfuse_public_key
    ALICE_LANGFUSE_SECRET_KEY           = var.langfuse_secret_key
    ALICE_LANGFUSE_HOST                 = var.alice_langfuse_host != "" ? var.alice_langfuse_host : "http://${yandex_compute_instance.bot.network_interface[0].ip_address}:3000"
    ALICE_LANGFUSE_ANONYMIZE_MESSAGES   = tostring(var.alice_langfuse_anonymize_messages)
    ALICE_REDIS_URL                     = var.alice_function_network_id == "" ? "" : "redis://:${var.redis_password}@${yandex_mdb_redis_cluster.bot.host[0].fqdn}:6379/0"
    VOICE_LINK_CODE_TTL_MINUTES         = tostring(var.voice_link_code_ttl_minutes)
    ALICE_DATABASE_URL                  = var.alice_function_network_id == "" ? "" : "postgresql://bot:${urlencode(var.pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill?sslmode=require"
    DATABASE_URL                        = var.alice_function_network_id == "" ? "" : "postgresql://bot:${urlencode(var.pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill?sslmode=require"
  }

  labels = merge(var.labels, { component = "alice-skill" })

  lifecycle {
    precondition {
      condition     = trimspace(var.alice_skill_id) != ""
      error_message = "alice_skill_id must be set when alice_function_enabled=true."
    }

    precondition {
      condition     = var.alice_function_network_id != ""
      error_message = "alice_function_network_id must be set for production Alice function (required for private Redis/PostgreSQL and internal voice-link API)."
    }

    precondition {
      condition     = trimspace(var.voice_link_api_key) != ""
      error_message = "voice_link_api_key must be set for secure voice-link API access."
    }

    precondition {
      condition     = trimspace(var.mcp_server_api_key) != ""
      error_message = "mcp_server_api_key must be set for Alice MCP access."
    }
  }
}

resource "yandex_function_iam_binding" "alice_skill_public_invoker" {
  count = var.alice_function_enabled ? 1 : 0

  function_id = yandex_function.alice_skill[0].id
  role        = "serverless.functions.invoker"
  members     = ["system:allUsers"]
}
