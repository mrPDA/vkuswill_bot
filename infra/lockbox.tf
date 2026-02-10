# ============================================================
# Lockbox — секреты для бота
# ============================================================

resource "yandex_lockbox_secret" "bot" {
  name        = "vkuswill-bot-secrets"
  description = "Secrets for VkusVill Telegram Bot"
  folder_id   = var.folder_id
  labels      = var.labels
}

resource "yandex_lockbox_secret_version" "bot" {
  secret_id = yandex_lockbox_secret.bot.id

  entries {
    key        = "BOT_TOKEN"
    text_value = var.bot_token
  }

  entries {
    key        = "GIGACHAT_CREDENTIALS"
    text_value = var.gigachat_credentials
  }

  entries {
    key = "REDIS_URL"
    text_value = "redis://:${var.redis_password}@${yandex_mdb_redis_cluster.bot.host[0].fqdn}:6379/0"
  }

  entries {
    key = "DATABASE_URL"
    text_value = "postgresql://bot:${var.pg_password}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill"
  }

  entries {
    key        = "MCP_SERVER_URL"
    text_value = var.mcp_server_url
  }

  entries {
    key        = "ADMIN_USER_IDS"
    text_value = var.admin_user_ids
  }
}
