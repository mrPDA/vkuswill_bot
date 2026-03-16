# ============================================================
# Сеть: data-источники + Security Group
# ============================================================

# Используем существующую сеть default
data "yandex_vpc_network" "default" {
  network_id = var.network_id
}

data "yandex_vpc_subnet" "default_a" {
  subnet_id = var.subnet_id
}

# ─── Security Group ──────────────────────────────────────────

resource "yandex_vpc_security_group" "bot" {
  name        = "vkuswill-bot-sg"
  description = "Security group for VkusVill Bot"
  network_id  = data.yandex_vpc_network.default.id
  labels      = var.labels

  # SSH
  ingress {
    description    = "SSH"
    protocol       = "TCP"
    port           = 22
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTPS (Telegram webhook → nginx → bot)
  ingress {
    description    = "HTTPS"
    protocol       = "TCP"
    port           = 443
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # HTTP (certbot ACME challenge)
  ingress {
    description    = "HTTP certbot"
    protocol       = "TCP"
    port           = 80
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # Internal app HTTP (webhook upstream + voice-link API)
  ingress {
    description = "Internal app HTTP (8080)"
    protocol    = "TCP"
    port        = 8080
    # Serverless function egress может идти из служебных подсетей VPC,
    # не совпадающих с CIDR VM-подсети.
    v4_cidr_blocks = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
  }

  # Langfuse UI (self-hosted, временный прямой доступ)
  ingress {
    description    = "Langfuse UI"
    protocol       = "TCP"
    port           = 3000
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # Metabase BI (self-hosted)
  ingress {
    description    = "Metabase UI"
    protocol       = "TCP"
    port           = 3001
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # Внутренний трафик (Redis, PG)
  ingress {
    description       = "Internal: all within SG"
    protocol          = "ANY"
    predefined_target = "self_security_group"
  }

  # PostgreSQL для serverless-функции Алисы (из той же VPC-подсети)
  ingress {
    description    = "PostgreSQL from VPC subnet (Alice function)"
    protocol       = "TCP"
    port           = 6432
    v4_cidr_blocks = data.yandex_vpc_subnet.default_a.v4_cidr_blocks
  }

  # Redis для serverless-функции Алисы
  ingress {
    description = "Internal Redis (6379)"
    protocol    = "TCP"
    port        = 6379
    # Для MDB Redis source range serverless egress может не попадать в RFC1918.
    # Оставляем открытым, пока не зафиксируем точные service CIDR.
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # Весь исходящий трафик (Telegram API, GigaChat, MCP)
  egress {
    description    = "Outbound: all"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
