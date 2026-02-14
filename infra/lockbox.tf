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

  # Webhook / Runtime
  entries {
    key        = "GIGACHAT_MODEL"
    text_value = var.gigachat_model
  }

  entries {
    key        = "USE_WEBHOOK"
    text_value = "true"
  }

  entries {
    key        = "WEBHOOK_HOST"
    text_value = var.webhook_host
  }

  entries {
    key        = "WEBHOOK_PORT"
    text_value = "8080"
  }

  # Langfuse (LLM-observability, self-hosted на VM)
  entries {
    key        = "LANGFUSE_ENABLED"
    text_value = "true"
  }

  entries {
    key        = "LANGFUSE_HOST"
    text_value = "http://localhost:3000"
  }

  entries {
    key = "LANGFUSE_DATABASE_URL"
    text_value = "postgresql://langfuse:${urlencode(var.langfuse_pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/langfuse?sslmode=require"
  }

  entries {
    key        = "LANGFUSE_NEXTAUTH_SECRET"
    text_value = var.langfuse_nextauth_secret
  }

  entries {
    key        = "LANGFUSE_SALT"
    text_value = var.langfuse_salt
  }

  entries {
    key        = "LANGFUSE_PUBLIC_KEY"
    text_value = var.langfuse_public_key
  }

  entries {
    key        = "LANGFUSE_SECRET_KEY"
    text_value = var.langfuse_secret_key
  }

  # Metabase (BI-дашборды, self-hosted на VM)
  entries {
    key        = "METABASE_ENABLED"
    text_value = "true"
  }

  entries {
    key = "METABASE_DATABASE_URL"
    text_value = "postgresql://metabase:${urlencode(var.metabase_pg_password)}@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/metabase?sslmode=require"
  }

  # S3 логирование
  entries {
    key        = "S3_LOG_ENABLED"
    text_value = "true"
  }

  entries {
    key        = "S3_LOG_BUCKET"
    text_value = var.s3_log_bucket
  }

  entries {
    key        = "S3_LOG_ACCESS_KEY"
    text_value = yandex_iam_service_account_static_access_key.log_writer_s3.access_key
  }

  entries {
    key        = "S3_LOG_SECRET_KEY"
    text_value = yandex_iam_service_account_static_access_key.log_writer_s3.secret_key
  }
}
