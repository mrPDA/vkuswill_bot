# ============================================================
# PostgreSQL 16 — self-hosted на VM (Docker контейнер)
# ============================================================
# Мигрировано с Yandex Managed PostgreSQL для снижения стоимости.
# PostgreSQL запускается как Docker-контейнер на bot VM (--network host).
#
# Управление: deploy/deploy.sh → deploy_postgres()
# Бэкапы:     scripts/pg-backup-s3.sh (cron каждые 6 часов)
# Init:       deploy/pg-init.sh (создание БД и пользователей)
#
# Предыдущая конфигурация Managed PostgreSQL:
#   resource_preset_id = "s2.micro"  (2 vCPU, 8 GB RAM)
#   disk_type_id       = "network-ssd"
#   disk_size          = 10 GB
#   version            = 16
#   max_connections     = 100
# ============================================================

# Terraform больше не управляет PostgreSQL — он развёрнут на VM.
# Ниже только S3 bucket для бэкапов.

# ─── S3 Bucket для бэкапов PostgreSQL ────────────────────────

resource "yandex_storage_bucket" "pg_backups" {
  bucket = "vkuswill-pg-backups"

  acl = "private"

  lifecycle_rule {
    id      = "delete-old-backups"
    enabled = true
    prefix  = "backups/"

    expiration {
      days = 30
    }
  }

  lifecycle_rule {
    id      = "cold-storage-after-7d"
    enabled = true
    prefix  = "backups/"

    transition {
      days          = 7
      storage_class = "COLD"
    }
  }

  versioning {
    enabled = false
  }

  max_size = 10737418240 # 10 GB

  tags = var.labels
}

# ─── SA для бэкапов ─────────────────────────────────────────

resource "yandex_iam_service_account" "pg_backup" {
  name        = "vkuswill-pg-backup"
  description = "Service account for PostgreSQL backup to Object Storage"
  folder_id   = var.folder_id
}

resource "yandex_resourcemanager_folder_iam_member" "pg_backup_storage" {
  folder_id = var.folder_id
  role      = "storage.editor"
  member    = "serviceAccount:${yandex_iam_service_account.pg_backup.id}"
}

resource "yandex_iam_service_account_static_access_key" "pg_backup_s3" {
  service_account_id = yandex_iam_service_account.pg_backup.id
  description        = "S3 access key for PostgreSQL backup"
}
