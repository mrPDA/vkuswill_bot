# ============================================================
# Service Account: github-deployer (для CD pipeline)
# ============================================================

resource "yandex_iam_service_account" "deployer" {
  name        = "vkuswill-github-deployer"
  description = "GitHub Actions CD pipeline for VkusVill Bot"
  folder_id   = var.folder_id
}

# Роли для deployer
resource "yandex_resourcemanager_folder_iam_member" "deployer_cr_pusher" {
  folder_id = var.folder_id
  role      = "container-registry.images.pusher"
  member    = "serviceAccount:${yandex_iam_service_account.deployer.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "deployer_cr_puller" {
  folder_id = var.folder_id
  role      = "container-registry.images.puller"
  member    = "serviceAccount:${yandex_iam_service_account.deployer.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "deployer_lockbox" {
  folder_id = var.folder_id
  role      = "lockbox.payloadViewer"
  member    = "serviceAccount:${yandex_iam_service_account.deployer.id}"
}

resource "yandex_resourcemanager_folder_iam_member" "deployer_compute" {
  folder_id = var.folder_id
  role      = "compute.admin"
  member    = "serviceAccount:${yandex_iam_service_account.deployer.id}"
}

# Статический ключ для docker login в CR
resource "yandex_iam_service_account_static_access_key" "deployer_cr_key" {
  service_account_id = yandex_iam_service_account.deployer.id
  description        = "CR push key for GitHub Actions"
}
