#!/usr/bin/env bash
# ============================================================
# Build ZIP artifact for Alice Yandex Cloud Function
# Usage:
#   bash scripts/build_alice_function_zip.sh [output_zip]
# ============================================================

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_ZIP="${1:-${ROOT_DIR}/dist/alice-skill.zip}"
STAGE_DIR="$(mktemp -d)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
RUNTIME_DEPS=(
  "httpx>=0.27"
  "sniffio>=1.3"
  "async-timeout>=4.0"
  "redis>=5.0"
  "pydantic<2"
  "requests>=2"
  "backoff>=2"
  "wrapt>=1.14"
  "typing-extensions>=4.5"
)
LANGFUSE_SDK="langfuse>=2.60,<3"

cleanup() {
  rm -rf "${STAGE_DIR}"
}
trap cleanup EXIT

mkdir -p "$(dirname "${OUT_ZIP}")"
OUT_ZIP="$(cd "$(dirname "${OUT_ZIP}")" && pwd)/$(basename "${OUT_ZIP}")"

echo "[build-alice-zip] Stage dir: ${STAGE_DIR}"

install_local_linux_deps() {
  echo "[build-alice-zip] Installing runtime deps for local Linux..."
  "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --target "${STAGE_DIR}" \
    "${RUNTIME_DEPS[@]}"
  # Langfuse SDK v2 without heavy optional deps (OpenAI/OTel are not required here).
  "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --target "${STAGE_DIR}" \
    --no-deps \
    "${LANGFUSE_SDK}"
}

install_cross_linux_deps() {
  echo "[build-alice-zip] Installing Linux-compatible wheels (manylinux2014_x86_64)..."
  "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --target "${STAGE_DIR}" \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.11 \
    --abi cp311 \
    --only-binary=:all: \
    "${RUNTIME_DEPS[@]}"
  "${PYTHON_BIN}" -m pip install \
    --disable-pip-version-check \
    --target "${STAGE_DIR}" \
    --platform manylinux2014_x86_64 \
    --implementation cp \
    --python-version 3.11 \
    --abi cp311 \
    --only-binary=:all: \
    --no-deps \
    "${LANGFUSE_SDK}"
}

if [[ "$(uname -s)" == "Linux" && "${ALICE_FORCE_CROSS_BUILD:-0}" != "1" ]]; then
  install_local_linux_deps
else
  install_cross_linux_deps
fi

echo "[build-alice-zip] Copying source code..."
cp -R "${ROOT_DIR}/src/vkuswill_bot" "${STAGE_DIR}/vkuswill_bot"

# Keep function bundle lean: only Alice-related modules and minimal services.
rm -rf "${STAGE_DIR}/vkuswill_bot/bot" "${STAGE_DIR}/vkuswill_bot/mcp_server"
rm -f "${STAGE_DIR}/vkuswill_bot/__main__.py" "${STAGE_DIR}/vkuswill_bot/config.py"
find "${STAGE_DIR}/vkuswill_bot/services" -maxdepth 1 -type f -name "*.py" \
  ! -name "__init__.py" \
  ! -name "mcp_client.py" \
  ! -name "redis_client.py" \
  ! -name "user_store.py" \
  ! -name "migration_runner.py" \
  ! -name "search_processor.py" \
  ! -name "cart_processor.py" \
  ! -name "price_cache.py" \
  ! -name "pii_utils.py" \
  ! -name "langfuse_tracing.py" \
  -delete

# Strip non-runtime files to keep ZIP under Yandex Function inline upload limit.
rm -rf "${STAGE_DIR}/bin"
if [[ -d "${STAGE_DIR}/asyncpg" ]]; then
  rm -rf "${STAGE_DIR}/asyncpg/_testbase"
  find "${STAGE_DIR}/asyncpg" -type f \( -name "*.pyx" -o -name "*.pxd" -o -name "*.pxi" \) -delete
fi
# Remove optional C-accelerators to keep bundle under Yandex inline upload limit.
if [[ -d "${STAGE_DIR}/pydantic" ]]; then
  find "${STAGE_DIR}/pydantic" -type f -name "*.so" -delete
fi
if [[ -d "${STAGE_DIR}/charset_normalizer" ]]; then
  find "${STAGE_DIR}/charset_normalizer" -type f -name "*.so" -delete
fi
if [[ -d "${STAGE_DIR}/wrapt" ]]; then
  find "${STAGE_DIR}/wrapt" -type f \( -name "*.so" -o -name "*.c" \) -delete
fi
# Package metadata is not required at runtime and inflates the archive.
find "${STAGE_DIR}" -type d -name "*.dist-info" -prune -exec rm -rf {} + 2>/dev/null || true

# Remove caches from bundle.
find "${STAGE_DIR}" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGE_DIR}" -type f -name "*.pyc" -delete 2>/dev/null || true

echo "[build-alice-zip] Creating ZIP: ${OUT_ZIP}"
rm -f "${OUT_ZIP}"
(
  cd "${STAGE_DIR}"
  zip -qr "${OUT_ZIP}" .
)

echo "[build-alice-zip] Done. Size:"
ls -lh "${OUT_ZIP}"
