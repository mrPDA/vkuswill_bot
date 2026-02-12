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

variable "webhook_host" {
  description = "External hostname/IP for Telegram webhook (e.g. YOUR_SERVER_IP)"
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
