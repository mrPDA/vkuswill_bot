# ============================================================
# Входные переменные
# ============================================================

# ─── Yandex Cloud ────────────────────────────────────────────

variable "cloud_id" {
  description = "Yandex Cloud ID"
  type        = string
  default     = "b1ge6d4nfcu57u4f46hi"
}

variable "folder_id" {
  description = "Yandex Cloud Folder ID"
  type        = string
  default     = "b1gjj3po03aa3m4j8ps5"
}

variable "zone" {
  description = "Availability zone"
  type        = string
  default     = "ru-central1-a"
}

variable "sa_key_file" {
  description = "Path to SA JSON key file for Terraform provider"
  type        = string
  default     = ""
}

# ─── Сеть ────────────────────────────────────────────────────

variable "network_id" {
  description = "ID существующей VPC network (default)"
  type        = string
  default     = "enp1gdenmvuu3e1ffi93"
}

variable "subnet_id" {
  description = "ID существующей подсети (default-ru-central1-a)"
  type        = string
  default     = "e9b5seub4gsmqjjkick5"
}

# ─── VM ──────────────────────────────────────────────────────

variable "vm_cores" {
  description = "vCPU count"
  type        = number
  default     = 2
}

variable "vm_memory" {
  description = "RAM (GB)"
  type        = number
  default     = 4
}

variable "vm_core_fraction" {
  description = "Core fraction (%): 20, 50, 100"
  type        = number
  default     = 50
}

variable "vm_disk_size" {
  description = "Boot disk size (GB)"
  type        = number
  default     = 20
}

variable "vm_ssh_key_path" {
  description = "Path to SSH public key for VM access"
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

# ─── PostgreSQL ──────────────────────────────────────────────

variable "pg_password" {
  description = "PostgreSQL bot user password"
  type        = string
  sensitive   = true
}

variable "pg_disk_size" {
  description = "PostgreSQL disk size (GB)"
  type        = number
  default     = 10
}

# ─── Redis ───────────────────────────────────────────────────

variable "redis_password" {
  description = "Redis password"
  type        = string
  sensitive   = true
}

# ─── Lockbox secrets ─────────────────────────────────────────

variable "bot_token" {
  description = "Telegram Bot Token"
  type        = string
  sensitive   = true
}

variable "gigachat_credentials" {
  description = "GigaChat API credentials"
  type        = string
  sensitive   = true
}

variable "mcp_server_url" {
  description = "MCP Server URL"
  type        = string
  default     = "https://mcp001.vkusvill.ru/mcp"
}

variable "mcp_server_enabled" {
  description = "Enable dedicated MCP server container in production"
  type        = string
  default     = "true"
}

variable "mcp_server_port" {
  description = "MCP server HTTP port on VM"
  type        = string
  default     = "8081"
}

variable "mcp_server_api_key" {
  description = "Single API key for MCP server HTTP auth (legacy)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "mcp_server_api_keys" {
  description = "JSON map of MCP client API keys for multi-client auth"
  type        = string
  sensitive   = true
  default     = "{}"
}

variable "voice_link_api_key" {
  description = "API key for /voice-link/* API on bot VM"
  type        = string
  sensitive   = true
  default     = ""
}

variable "admin_user_ids" {
  description = "Telegram admin user IDs (JSON array)"
  type        = string
  default     = "[]"
}

# ─── Webhook / Runtime ────────────────────────────────────────

variable "gigachat_model" {
  description = "GigaChat model name"
  type        = string
  default     = "GigaChat-2-Max"
}

# ─── Alice Skill (Yandex Serverless Function) ─────────────────

variable "alice_function_enabled" {
  description = "Create and manage Alice skill serverless function"
  type        = bool
  default     = false
}

variable "alice_function_name" {
  description = "Serverless function name for Alice skill"
  type        = string
  default     = "vkuswill-alice-skill"
}

variable "alice_function_zip_path" {
  description = "Path to ZIP archive with Alice skill code"
  type        = string
  default     = "../dist/alice-skill.zip"
}

variable "alice_function_network_id" {
  description = "VPC network ID for Alice function private connectivity to internal VM API"
  type        = string
  default     = ""
}

variable "alice_link_api_url" {
  description = "Voice-link API URL for Alice function (empty = auto internal VM IP:8080/voice-link)"
  type        = string
  default     = ""
}

variable "alice_skill_id" {
  description = "Yandex Dialogs skill_id for inbound event validation in Alice function"
  type        = string
  default     = ""
}

variable "alice_linking_fail_closed" {
  description = "Fail closed for account linking when DB/API backend is unavailable"
  type        = bool
  default     = true
}

variable "alice_idempotency_key_prefix" {
  description = "Redis key prefix for Alice idempotency records"
  type        = string
  default     = "alice:idem:"
}

variable "alice_rate_limit_key_prefix" {
  description = "Redis key prefix for Alice rate limiting counters"
  type        = string
  default     = "alice:rl:"
}

variable "alice_order_rate_limit" {
  description = "Max order requests per voice user within order_rate_window_seconds"
  type        = number
  default     = 12
}

variable "alice_order_rate_window_seconds" {
  description = "Window for Alice order rate limiting in seconds"
  type        = number
  default     = 60
}

variable "alice_link_code_rate_limit" {
  description = "Max link code attempts per voice user within link_code_rate_window_seconds"
  type        = number
  default     = 6
}

variable "alice_link_code_rate_window_seconds" {
  description = "Window for Alice link code rate limiting in seconds"
  type        = number
  default     = 600
}

variable "alice_idempotency_ttl_seconds" {
  description = "Idempotency TTL for Alice skill requests"
  type        = number
  default     = 90
}

variable "alice_db_connect_timeout_seconds" {
  description = "DB connect timeout for Alice skill cold start"
  type        = number
  default     = 3
}

variable "alice_link_api_timeout_seconds" {
  description = "HTTP timeout for voice-link API calls from Alice skill"
  type        = number
  default     = 5
}

variable "alice_link_api_verify_ssl" {
  description = "Verify TLS certificate when Alice skill calls voice-link API"
  type        = bool
  default     = true
}

variable "alice_langfuse_enabled" {
  description = "Enable Langfuse tracing for Alice serverless function"
  type        = bool
  default     = true
}

variable "alice_langfuse_host" {
  description = "Langfuse host for Alice function (empty = internal VM URL)"
  type        = string
  default     = ""
}

variable "alice_langfuse_anonymize_messages" {
  description = "Mask Alice utterances before sending to Langfuse"
  type        = bool
  default     = true
}

variable "alice_degrade_to_guest_on_db_error" {
  description = "Allow guest ordering when DB for linking is temporarily unavailable"
  type        = bool
  default     = false
}

variable "voice_link_code_ttl_minutes" {
  description = "TTL for /link_voice one-time codes"
  type        = number
  default     = 10
}

variable "webhook_host" {
  description = "External hostname/IP for Telegram webhook (e.g. 1.2.3.4)"
  type        = string
}

# ─── Langfuse ─────────────────────────────────────────────────

variable "langfuse_pg_password" {
  description = "PostgreSQL password for Langfuse user"
  type        = string
  sensitive   = true
}

variable "langfuse_nextauth_secret" {
  description = "NextAuth secret for Langfuse (openssl rand -base64 32)"
  type        = string
  sensitive   = true
}

variable "langfuse_salt" {
  description = "Encryption salt for Langfuse (openssl rand -base64 32)"
  type        = string
  sensitive   = true
}

variable "langfuse_public_key" {
  description = "Langfuse project public key (pk-lf-...)"
  type        = string
  default     = ""
}

variable "langfuse_secret_key" {
  description = "Langfuse project secret key (sk-lf-...)"
  type        = string
  sensitive   = true
  default     = ""
}

# ─── Metabase ─────────────────────────────────────────────────

variable "metabase_pg_password" {
  description = "PostgreSQL password for Metabase user"
  type        = string
  sensitive   = true
}

# ─── S3 Logs ─────────────────────────────────────────────────

variable "s3_log_bucket" {
  description = "S3 bucket name for bot logs"
  type        = string
  default     = "vkuswill-bot-logs"
}

variable "s3_log_retention_days" {
  description = "Days to keep logs in S3 before auto-deletion"
  type        = number
  default     = 90
}

variable "s3_log_max_size_bytes" {
  description = "Max bucket size in bytes (5 GB default)"
  type        = number
  default     = 5368709120 # 5 * 1024^3
}

# ─── Labels ──────────────────────────────────────────────────

variable "labels" {
  description = "Common labels for all resources"
  type        = map(string)
  default = {
    project = "vkuswill-bot"
    env     = "production"
  }
}
