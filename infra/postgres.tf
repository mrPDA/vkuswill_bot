# ============================================================
# Managed PostgreSQL 16
# ============================================================

resource "yandex_mdb_postgresql_cluster" "bot" {
  name        = "vkuswill-bot-pg"
  environment = "PRODUCTION"
  network_id  = data.yandex_vpc_network.default.id
  labels      = var.labels

  config {
    version = 16

    resources {
      resource_preset_id = "s2.micro" # 2 vCPU, 8 GB (минимальный для network-ssd)
      disk_type_id       = "network-ssd"
      disk_size          = var.pg_disk_size
    }

    postgresql_config = {
      max_connections = 100
    }
  }

  host {
    zone      = var.zone
    subnet_id = data.yandex_vpc_subnet.default_a.id
  }

  security_group_ids = [yandex_vpc_security_group.bot.id]

  maintenance_window {
    type = "WEEKLY"
    day  = "SUN"
    hour = 5
  }
}

# ─── Database ────────────────────────────────────────────────

resource "yandex_mdb_postgresql_database" "vkuswill" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "vkuswill"
  owner      = yandex_mdb_postgresql_user.bot.name

  depends_on = [yandex_mdb_postgresql_user.bot]
}

# ─── User ────────────────────────────────────────────────────

resource "yandex_mdb_postgresql_user" "bot" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "bot"
  password   = var.pg_password
  conn_limit = 50
}

# Права назначаются автоматически через owner в yandex_mdb_postgresql_database

# ─── Langfuse Database & User ─────────────────────────────

resource "yandex_mdb_postgresql_user" "langfuse" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "langfuse"
  password   = var.langfuse_pg_password
  conn_limit = 20
}

resource "yandex_mdb_postgresql_database" "langfuse" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "langfuse"
  owner      = yandex_mdb_postgresql_user.langfuse.name

  depends_on = [yandex_mdb_postgresql_user.langfuse]
}

# ─── Metabase Database & User ────────────────────────────────

resource "yandex_mdb_postgresql_user" "metabase" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "metabase"
  password   = var.metabase_pg_password
  conn_limit = 20
}

resource "yandex_mdb_postgresql_database" "metabase" {
  cluster_id = yandex_mdb_postgresql_cluster.bot.id
  name       = "metabase"
  owner      = yandex_mdb_postgresql_user.metabase.name

  depends_on = [yandex_mdb_postgresql_user.metabase]
}
