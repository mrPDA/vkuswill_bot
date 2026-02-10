#!/usr/bin/env bash
# ============================================================
# Bootstrap: одноразовая подготовка для Terraform
# Создаёт SA, статический ключ и S3-bucket для хранения state
# ============================================================
# Запуск: bash infra/bootstrap.sh
# После выполнения — скопируйте access_key и secret_key
# в infra/backend.conf (НЕ коммитьте в git!)
# ============================================================

set -euo pipefail

CLOUD_ID="b1ge6d4nfcu57u4f46hi"
FOLDER_ID="b1gjj3po03aa3m4j8ps5"
SA_NAME="vkuswill-tf-admin"
BUCKET_NAME="vkuswill-tf-state"

echo "=== 1/4 Создание Service Account: ${SA_NAME} ==="
yc iam service-account create \
  --name "${SA_NAME}" \
  --description "Terraform admin for VkusVill Bot" \
  --folder-id "${FOLDER_ID}" \
  2>/dev/null || echo "SA уже существует, пропускаем"

SA_ID=$(yc iam service-account get --name "${SA_NAME}" --folder-id "${FOLDER_ID}" --format json | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "  SA ID: ${SA_ID}"

echo "=== 2/4 Назначение ролей ==="
for ROLE in editor storage.admin lockbox.admin container-registry.admin; do
  yc resource-manager folder add-access-binding "${FOLDER_ID}" \
    --role "${ROLE}" \
    --subject "serviceAccount:${SA_ID}" \
    2>/dev/null || echo "  Роль ${ROLE} уже назначена"
done

echo "=== 3/4 Создание статического ключа доступа (для S3 backend) ==="
KEY_JSON=$(yc iam access-key create \
  --service-account-id "${SA_ID}" \
  --description "Terraform S3 backend" \
  --format json)

ACCESS_KEY=$(echo "${KEY_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_key']['key_id'])")
SECRET_KEY=$(echo "${KEY_JSON}" | python3 -c "import sys,json; print(json.load(sys.stdin)['secret'])")

echo "=== 4/4 Создание S3-bucket: ${BUCKET_NAME} ==="
# Для создания bucket нужен AWS CLI или yc с S3 API
# Используем yc storage bucket create (доступно с yc >= 0.100)
yc storage bucket create \
  --name "${BUCKET_NAME}" \
  --folder-id "${FOLDER_ID}" \
  --default-storage-class standard \
  --max-size 1073741824 \
  2>/dev/null || echo "Bucket уже существует, пропускаем"

echo ""
echo "=============================================="
echo "Bootstrap завершён!"
echo "=============================================="
echo ""
echo "Создайте файл infra/backend.conf:"
echo ""
echo "  access_key = \"${ACCESS_KEY}\""
echo "  secret_key = \"${SECRET_KEY}\""
echo ""
echo "Затем запустите:"
echo "  cd infra"
echo "  terraform init -backend-config=backend.conf"
echo ""
echo "ВАЖНО: НЕ коммитьте backend.conf в git!"
echo "=============================================="
