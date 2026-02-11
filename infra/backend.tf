# ============================================================
# S3 Backend — Yandex Object Storage
# ============================================================
# Инициализация:
#   terraform init -backend-config=backend.conf
#
# backend.conf (НЕ в git!):
#   access_key = "..."
#   secret_key = "..."
# ============================================================

terraform {
  backend "s3" {
    endpoint = "https://storage.yandexcloud.net"
    bucket   = "vkuswill-tf-state"
    key      = "vkuswill-bot/terraform.tfstate"
    region   = "ru-central1"

    skip_region_validation      = true
    skip_credentials_validation = true
    skip_metadata_api_check     = true

    # Не используем DynamoDB для блокировки
    dynamodb_table = ""
  }
}
