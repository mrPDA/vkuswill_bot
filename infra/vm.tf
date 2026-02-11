# ============================================================
# Compute VM — Bot Host
# ============================================================

# Статический IP для webhook и SSH
resource "yandex_vpc_address" "bot" {
  name = "vkuswill-bot-ip"

  external_ipv4_address {
    zone_id = var.zone
  }
}

# Актуальный образ Ubuntu 22.04
data "yandex_compute_image" "ubuntu" {
  family = "ubuntu-2204-lts"
}

resource "yandex_compute_instance" "bot" {
  name        = "vkuswill-bot"
  platform_id = "standard-v3"
  zone        = var.zone
  labels      = var.labels

  resources {
    cores         = var.vm_cores
    memory        = var.vm_memory
    core_fraction = var.vm_core_fraction
  }

  boot_disk {
    initialize_params {
      image_id = data.yandex_compute_image.ubuntu.id
      size     = var.vm_disk_size
      type     = "network-ssd"
    }
  }

  network_interface {
    subnet_id          = data.yandex_vpc_subnet.default_a.id
    nat                = true
    nat_ip_address     = yandex_vpc_address.bot.external_ipv4_address[0].address
    security_group_ids = [yandex_vpc_security_group.bot.id]
  }

  metadata = {
    user-data = templatefile("${path.module}/cloud-init.yaml", {
      ssh_key = trimspace(file(pathexpand(var.vm_ssh_key_path)))
    })
  }

  scheduling_policy {
    preemptible = false
  }
}
