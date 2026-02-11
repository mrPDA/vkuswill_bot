"""Буферизованный logging handler для отправки логов в S3 (Yandex Object Storage).

Логи собираются в памяти и периодически сбрасываются в S3 как NDJSON-файлы.
Структура ключей: ``{prefix}/{YYYY}/{MM}/{DD}/{HH}-{MM}-{SS}-{short_uuid}.jsonl``

Каждая строка — валидный JSON-объект, что позволяет:
- Анализировать логи через ``jq``, ``clickhouse-local``, Yandex DataLens
- Импортировать в ELK / Grafana Loki / ClickHouse
- Фильтровать по уровню, логгеру, временному диапазону

Пример использования::

    handler = S3LogHandler(
        bucket="vkuswill-bot-logs",
        access_key="...",
        secret_key="...",
    )
    logging.getLogger().addHandler(handler)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from datetime import datetime, UTC
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Максимальный размер буфера (защита от утечки памяти при недоступности S3)
_MAX_BUFFER_SIZE = 50_000


class S3LogHandler(logging.Handler):
    """Буферизованный logging handler, отправляющий логи в S3.

    Параметры:
        bucket: Имя S3-бакета.
        prefix: Префикс ключей в бакете (по умолчанию ``logs``).
        endpoint_url: Эндпоинт S3 (по умолчанию Yandex Object Storage).
        region_name: Регион (по умолчанию ``ru-central1``).
        access_key: AWS_ACCESS_KEY_ID (статический ключ SA).
        secret_key: AWS_SECRET_ACCESS_KEY.
        flush_interval: Интервал сброса в секундах (по умолчанию 60).
        flush_size: Порог сброса по количеству записей (по умолчанию 500).
        level: Минимальный уровень логирования.
    """

    def __init__(
        self,
        bucket: str,
        *,
        prefix: str = "logs",
        endpoint_url: str = "https://storage.yandexcloud.net",
        region_name: str = "ru-central1",
        access_key: str = "",
        secret_key: str = "",
        flush_interval: int = 60,
        flush_size: int = 500,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level)

        self.bucket = bucket
        self.prefix = prefix.rstrip("/")
        self._flush_interval = flush_interval
        self._flush_size = flush_size

        # Метаданные для каждого лог-файла
        self._hostname = os.environ.get("HOSTNAME", "unknown")
        self._pid = os.getpid()

        # Буфер и синхронизация
        self._buffer: list[str] = []
        self._lock = threading.Lock()
        self._closed = False

        # S3-клиент (lazy init в фоновом потоке)
        self._client = None
        self._client_kwargs = {
            "endpoint_url": endpoint_url,
            "region_name": region_name,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }

        # Фоновый таймер для периодического сброса
        self._timer: threading.Timer | None = None
        self._start_flush_timer()

    # ------------------------------------------------------------------
    # Lazy-инициализация boto3 клиента (в потоке, не при импорте)
    # ------------------------------------------------------------------

    def _get_client(self):
        """Инициализировать boto3 S3-клиент при первом использовании."""
        if self._client is None:
            try:
                import boto3
                from botocore.config import Config as BotoConfig

                self._client = boto3.client(
                    "s3",
                    config=BotoConfig(
                        retries={"max_attempts": 3, "mode": "standard"},
                        connect_timeout=5,
                        read_timeout=10,
                    ),
                    **self._client_kwargs,
                )
            except ImportError:
                print(
                    "[S3LogHandler] boto3 не установлен, S3 логирование отключено",
                    file=sys.stderr,
                )
                raise
        return self._client

    # ------------------------------------------------------------------
    # Таймер периодического сброса
    # ------------------------------------------------------------------

    def _start_flush_timer(self) -> None:
        """Запустить фоновый таймер для периодического сброса буфера."""
        if self._closed:
            return
        self._timer = threading.Timer(self._flush_interval, self._on_timer)
        self._timer.daemon = True
        self._timer.start()

    def _on_timer(self) -> None:
        """Callback таймера: сбросить буфер и перезапустить таймер."""
        self._flush_to_s3()
        self._start_flush_timer()

    # ------------------------------------------------------------------
    # Основная логика
    # ------------------------------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        """Добавить запись лога в буфер."""
        try:
            entry: dict = {
                "timestamp": datetime.fromtimestamp(
                    record.created,
                    tz=UTC,
                ).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                "hostname": self._hostname,
                "pid": self._pid,
            }

            # Добавить стектрейс, если есть
            if record.exc_info and record.exc_info[1] is not None:
                entry["exception"] = self.formatException(record.exc_info)

            # Добавить extra-поля (user_id, request_id и т.п.)
            for key in ("user_id", "request_id", "chat_id"):
                val = getattr(record, key, None)
                if val is not None:
                    entry[key] = val

            line = json.dumps(entry, ensure_ascii=False)

            records_to_flush: list[str] | None = None

            with self._lock:
                if len(self._buffer) >= _MAX_BUFFER_SIZE:
                    # Сбросить 10% старых записей при переполнении
                    drop_count = _MAX_BUFFER_SIZE // 10
                    self._buffer = self._buffer[drop_count:]

                self._buffer.append(line)

                # Сбросить при достижении порога
                if len(self._buffer) >= self._flush_size:
                    records_to_flush = self._buffer.copy()
                    self._buffer.clear()

            # Загрузка вне блокировки (не блокируем другие потоки)
            if records_to_flush is not None:
                self._upload(records_to_flush)

        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        """Немедленно сбросить буфер в S3."""
        self._flush_to_s3()

    def _flush_to_s3(self) -> None:
        """Извлечь записи из буфера и загрузить в S3."""
        with self._lock:
            if not self._buffer:
                return
            records_to_flush = self._buffer.copy()
            self._buffer.clear()

        self._upload(records_to_flush)

    def _upload(self, records: list[str]) -> None:
        """Загрузить список JSON-строк в S3 как NDJSON-файл."""
        if not records:
            return

        # Сформировать содержимое NDJSON
        content = "\n".join(records) + "\n"

        # Ключ: logs/2026/02/11/14-30-00-abc12345.jsonl
        now = datetime.now(tz=UTC)
        key = (
            f"{self.prefix}/{now:%Y}/{now:%m}/{now:%d}/"
            f"{now:%H}-{now:%M}-{now:%S}-{uuid.uuid4().hex[:8]}.jsonl"
        )

        try:
            client = self._get_client()
            client.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=content.encode("utf-8"),
                ContentType="application/x-ndjson",
            )
        except Exception as exc:
            # При ошибке вернуть записи в буфер (с ограничением)
            with self._lock:
                space_left = _MAX_BUFFER_SIZE - len(self._buffer)
                if space_left > 0:
                    # Добавляем неотправленные записи в начало буфера
                    self._buffer = records[:space_left] + self._buffer

            print(
                f"[S3LogHandler] Ошибка загрузки {len(records)} записей в S3: {exc}",
                file=sys.stderr,
            )

    def close(self) -> None:
        """Остановить таймер и сбросить оставшийся буфер."""
        self._closed = True
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

        # Финальный сброс
        self._flush_to_s3()
        super().close()


def create_s3_log_handler(
    bucket: str,
    *,
    access_key: str,
    secret_key: str,
    prefix: str = "logs",
    endpoint_url: str = "https://storage.yandexcloud.net",
    region_name: str = "ru-central1",
    flush_interval: int = 60,
    flush_size: int = 500,
    level: int = logging.INFO,
) -> S3LogHandler:
    """Фабрика для создания S3LogHandler с валидацией параметров.

    Raises:
        ValueError: Если обязательные параметры не заданы.
    """
    if not bucket:
        msg = "S3_LOG_BUCKET обязателен для S3 логирования"
        raise ValueError(msg)
    if not access_key or not secret_key:
        msg = "S3_LOG_ACCESS_KEY и S3_LOG_SECRET_KEY обязательны"
        raise ValueError(msg)

    return S3LogHandler(
        bucket=bucket,
        prefix=prefix,
        endpoint_url=endpoint_url,
        region_name=region_name,
        access_key=access_key,
        secret_key=secret_key,
        flush_interval=flush_interval,
        flush_size=flush_size,
        level=level,
    )
