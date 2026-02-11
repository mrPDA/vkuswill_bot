# ============================================================
# Container Registry
# ============================================================

resource "yandex_container_registry" "bot" {
  name      = "vkuswill-bot"
  folder_id = var.folder_id
  labels    = var.labels
}

# Lifecycle policy (retain 10 images) настраивается через yc CLI:
#   yc container registry lifecycle-policy create \
#     --registry-id <ID> \
#     --name retain-10 \
#     --active \
#     --rule "untagged=true, retained_top=10, expire_period=168h" \
#     --rule "untagged=false, retained_top=10"
