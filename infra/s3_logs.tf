# ============================================================
# S3 Bucket — хранилище логов бота (Yandex Object Storage)
# ============================================================
# Структура ключей: logs/{YYYY}/{MM}/{DD}/{HH}-{MM}-{SS}-{uuid}.jsonl
# Формат: NDJSON (Newline Delimited JSON) — одна JSON-запись на строку
#
# Анализ логов:
#   - CLI:   aws s3 cp s3://vkuswill-bot-logs/logs/2026/02/11/ ./logs/ --recursive --endpoint-url https://storage.yandexcloud.net
#            cat logs/*.jsonl | jq 'select(.level == "ERROR")'
#   - Yandex DataLens: подключить Object Storage как источник данных
#   - ClickHouse:      CREATE TABLE ... ENGINE = S3('https://storage.yandexcloud.net/vkuswill-bot-logs/logs/**/*.jsonl', 'JSONEachRow')
# ============================================================

# ─── Service Account для записи логов ────────────────────────

resource "yandex_iam_service_account" "log_writer" {
  name        = "vkuswill-log-writer"
  description = "Service account for writing bot logs to Object Storage"
  folder_id   = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "log_writer_storage" {
  folder_id = var.folder_id
  role      = "storage.uploader"
  member    = "serviceAccount:${yandex_iam_service_account.log_writer.id}"
}

# Статический ключ для S3 API (boto3)
resource "yandex_iam_service_account_static_access_key" "log_writer_s3" {
  service_account_id = yandex_iam_service_account.log_writer.id
  description        = "S3 access key for bot log shipping"
}

# ─── S3 Bucket ────────────────────────────────────────────────

resource "yandex_storage_bucket" "logs" {
  bucket = var.s3_log_bucket

  # Запретить публичный доступ
  acl = "private"

  # Lifecycle: автоудаление логов старше N дней
  lifecycle_rule {
    id      = "auto-delete-old-logs"
    enabled = true
    prefix  = "logs/"

    expiration {
      days = var.s3_log_retention_days
    }
  }

  # Lifecycle: переход в холодное хранилище через 30 дней
  lifecycle_rule {
    id      = "move-to-cold-storage"
    enabled = true
    prefix  = "logs/"

    transition {
      days          = 30
      storage_class = "COLD"
    }
  }

  # Версионирование отключено (логи append-only, версии не нужны)
  versioning {
    enabled = false
  }

  # Максимальный размер бакета (защита от неконтролируемого роста)
  max_size = var.s3_log_max_size_bytes

  tags = var.labels
}
