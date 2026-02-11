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

  # Webhook (Telegram → Bot)
  ingress {
    description    = "Webhook HTTP"
    protocol       = "TCP"
    port           = 8080
    v4_cidr_blocks = ["0.0.0.0/0"]
  }

  # Внутренний трафик (Redis, PG)
  ingress {
    description       = "Internal: all within SG"
    protocol          = "ANY"
    predefined_target = "self_security_group"
  }

  # Весь исходящий трафик (Telegram API, GigaChat, MCP)
  egress {
    description    = "Outbound: all"
    protocol       = "ANY"
    v4_cidr_blocks = ["0.0.0.0/0"]
  }
}
