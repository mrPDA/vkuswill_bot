# ============================================================
# Outputs
# ============================================================

# ─── VM ──────────────────────────────────────────────────────

output "vm_external_ip" {
  description = "VM external IP (для SSH и webhook)"
  value       = yandex_vpc_address.bot.external_ipv4_address[0].address
}

output "vm_internal_ip" {
  description = "VM internal IP"
  value       = yandex_compute_instance.bot.network_interface[0].ip_address
}

# ─── Container Registry ─────────────────────────────────────

output "cr_registry_id" {
  description = "Container Registry ID (для docker push)"
  value       = yandex_container_registry.bot.id
}

output "cr_repository" {
  description = "Full CR repository path"
  value       = "cr.yandex/${yandex_container_registry.bot.id}/vkuswill-bot"
}

# ─── Redis ───────────────────────────────────────────────────

output "redis_host" {
  description = "Redis FQDN"
  value       = yandex_mdb_redis_cluster.bot.host[0].fqdn
}

output "redis_url" {
  description = "Redis connection URL"
  value       = "redis://:****@${yandex_mdb_redis_cluster.bot.host[0].fqdn}:6379/0"
  sensitive   = false
}

# ─── PostgreSQL ──────────────────────────────────────────────

output "pg_host" {
  description = "PostgreSQL FQDN"
  value       = yandex_mdb_postgresql_cluster.bot.host[0].fqdn
}

output "pg_connection" {
  description = "PostgreSQL connection string (masked password)"
  value       = "postgresql://bot:****@${yandex_mdb_postgresql_cluster.bot.host[0].fqdn}:6432/vkuswill"
  sensitive   = false
}

# ─── Lockbox ─────────────────────────────────────────────────

output "lockbox_secret_id" {
  description = "Lockbox secret ID"
  value       = yandex_lockbox_secret.bot.id
}

# ─── Service Account ─────────────────────────────────────────

output "deployer_sa_id" {
  description = "GitHub deployer SA ID"
  value       = yandex_iam_service_account.deployer.id
}
