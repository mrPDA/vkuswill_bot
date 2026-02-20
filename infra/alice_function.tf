# ============================================================
# Alice Skill — Yandex Serverless Function
# ============================================================

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
    ALICE_MCP_SERVER_URL               = var.mcp_server_url
    ALICE_MCP_API_KEY                  = var.mcp_server_api_key
    ALICE_LINK_API_URL                 = var.alice_link_api_url
    ALICE_LINK_API_KEY                 = var.voice_link_api_key
    ALICE_LINK_API_TIMEOUT_SECONDS     = tostring(var.alice_link_api_timeout_seconds)
    ALICE_LINK_API_VERIFY_SSL          = tostring(var.alice_link_api_verify_ssl)
    ALICE_REQUIRE_LINKED_ACCOUNT       = "true"
    ALICE_DEGRADE_TO_GUEST_ON_DB_ERROR = tostring(var.alice_degrade_to_guest_on_db_error)
    ALICE_DB_CONNECT_TIMEOUT_SECONDS   = tostring(var.alice_db_connect_timeout_seconds)
    ALICE_IDEMPOTENCY_TTL_SECONDS      = tostring(var.alice_idempotency_ttl_seconds)
    VOICE_LINK_CODE_TTL_MINUTES        = tostring(var.voice_link_code_ttl_minutes)
    ALICE_DATABASE_URL                 = var.alice_function_network_id == "" ? "" : "postgresql://bot:${urlencode(var.pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill?sslmode=require"
    DATABASE_URL                       = var.alice_function_network_id == "" ? "" : "postgresql://bot:${urlencode(var.pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill?sslmode=require"
  }

  labels = merge(var.labels, { component = "alice-skill" })
}

resource "yandex_function_iam_binding" "alice_skill_public_invoker" {
  count = var.alice_function_enabled ? 1 : 0

  function_id = yandex_function.alice_skill[0].id
  role        = "serverless.functions.invoker"
  members     = ["system:allUsers"]
}
