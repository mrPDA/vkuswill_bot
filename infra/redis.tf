# ============================================================
# Managed Redis 7.2
# ============================================================

resource "yandex_mdb_redis_cluster" "bot" {
  name        = "vkuswill-bot-redis"
  environment = "PRODUCTION"
  network_id  = data.yandex_vpc_network.default.id
  labels      = var.labels

  config {
    version  = "7.2-valkey"
    password = var.redis_password

    maxmemory_policy = "VOLATILE_LRU"
  }

  resources {
    resource_preset_id = "b3-c1-m4" # burstable: 1 vCPU, 4 GB
    disk_type_id       = "network-ssd"
    disk_size          = 16 # GB
  }

  host {
    zone      = var.zone
    subnet_id = data.yandex_vpc_subnet.default_a.id
  }

  security_group_ids = [yandex_vpc_security_group.bot.id]

  maintenance_window {
    type = "WEEKLY"
    day  = "SUN"
    hour = 4
  }
}
