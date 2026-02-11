"""Тесты S3LogHandler.

Тестируем:
- Буферизацию записей логов (emit)
- Формат NDJSON (timestamp, level, logger, message, hostname, pid)
- Извлечение extra-полей (user_id, request_id, chat_id)
- Сброс буфера по порогу (flush_size)
- Сброс буфера вручную (flush)
- Защиту от переполнения буфера (_MAX_BUFFER_SIZE)
- Возврат записей в буфер при ошибке S3
- Закрытие handler (close) — остановка таймера + финальный сброс
- Фабрику create_s3_log_handler: валидация параметров
- Формат S3-ключей
"""

import json
import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from vkuswill_bot.services.s3_log_handler import (
    S3LogHandler,
    _MAX_BUFFER_SIZE,
    create_s3_log_handler,
)


# ============================================================================
# Фикстуры
# ============================================================================


@pytest.fixture
def handler() -> S3LogHandler:
    """S3LogHandler с замоканным S3-клиентом (не запускает реальный таймер)."""
    with patch.object(S3LogHandler, "_start_flush_timer"):
        h = S3LogHandler(
            bucket="test-bucket",
            access_key="test-key",
            secret_key="test-secret",
            flush_size=5,
            flush_interval=3600,
        )
    # Мокаем S3-клиент
    h._client = MagicMock()
    # Устанавливаем formatter для корректного форматирования стектрейсов
    h.setFormatter(logging.Formatter())
    return h


@pytest.fixture
def log_record() -> logging.LogRecord:
    """Стандартная запись лога."""
    return logging.LogRecord(
        name="test.logger",
        level=logging.INFO,
        pathname="test.py",
        lineno=10,
        msg="Test message",
        args=None,
        exc_info=None,
    )


# ============================================================================
# emit: буферизация
# ============================================================================


class TestEmit:
    """Тесты emit: добавление записей в буфер."""

    def test_adds_record_to_buffer(self, handler, log_record):
        """emit добавляет запись в буфер."""
        handler.emit(log_record)
        assert len(handler._buffer) == 1

    def test_record_is_valid_json(self, handler, log_record):
        """Запись в буфере — валидный JSON."""
        handler.emit(log_record)
        entry = json.loads(handler._buffer[0])
        assert isinstance(entry, dict)

    def test_record_has_required_fields(self, handler, log_record):
        """Запись содержит обязательные поля."""
        handler.emit(log_record)
        entry = json.loads(handler._buffer[0])
        assert "timestamp" in entry
        assert "level" in entry
        assert "logger" in entry
        assert "message" in entry
        assert "hostname" in entry
        assert "pid" in entry

    def test_record_field_values(self, handler, log_record):
        """Поля записи содержат корректные значения."""
        handler.emit(log_record)
        entry = json.loads(handler._buffer[0])
        assert entry["level"] == "INFO"
        assert entry["logger"] == "test.logger"
        assert entry["message"] == "Test message"

    def test_extra_fields_user_id(self, handler):
        """Extra-поле user_id включается в запись."""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="msg", args=None, exc_info=None,
        )
        record.user_id = 42  # type: ignore[attr-defined]
        handler.emit(record)
        entry = json.loads(handler._buffer[0])
        assert entry["user_id"] == 42

    def test_extra_fields_chat_id(self, handler):
        """Extra-поле chat_id включается в запись."""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="msg", args=None, exc_info=None,
        )
        record.chat_id = 100  # type: ignore[attr-defined]
        handler.emit(record)
        entry = json.loads(handler._buffer[0])
        assert entry["chat_id"] == 100

    def test_extra_fields_request_id(self, handler):
        """Extra-поле request_id включается в запись."""
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="msg", args=None, exc_info=None,
        )
        record.request_id = "req-abc"  # type: ignore[attr-defined]
        handler.emit(record)
        entry = json.loads(handler._buffer[0])
        assert entry["request_id"] == "req-abc"

    def test_no_extra_fields_by_default(self, handler, log_record):
        """Без extra-полей они не попадают в запись."""
        handler.emit(log_record)
        entry = json.loads(handler._buffer[0])
        assert "user_id" not in entry
        assert "chat_id" not in entry
        assert "request_id" not in entry

    def test_exception_info_included(self, handler):
        """Стектрейс исключения включается в запись."""
        try:
            raise ValueError("test error")
        except ValueError:
            import sys
            record = logging.LogRecord(
                name="test", level=logging.ERROR,
                pathname="", lineno=0, msg="error",
                args=None, exc_info=sys.exc_info(),
            )
        handler.emit(record)
        entry = json.loads(handler._buffer[0])
        assert "exception" in entry
        assert "ValueError" in entry["exception"]
        assert "test error" in entry["exception"]

    def test_multiple_records_buffered(self, handler):
        """Несколько записей накапливаются в буфере."""
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)
        assert len(handler._buffer) == 3


# ============================================================================
# Сброс по порогу (flush_size)
# ============================================================================


class TestFlushOnSize:
    """Тесты сброса буфера при достижении flush_size."""

    def test_flushes_at_threshold(self, handler):
        """Буфер сбрасывается при достижении flush_size (5)."""
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        # Буфер очищен после порога
        assert len(handler._buffer) == 0
        # S3 upload вызван
        handler._client.put_object.assert_called_once()

    def test_upload_content_is_ndjson(self, handler):
        """Загружаемое содержимое — NDJSON (каждая строка — JSON)."""
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        call_kwargs = handler._client.put_object.call_args.kwargs
        body = call_kwargs["Body"].decode("utf-8")
        lines = body.strip().split("\n")
        assert len(lines) == 5
        for line in lines:
            entry = json.loads(line)
            assert "message" in entry

    def test_upload_content_type(self, handler):
        """ContentType загрузки — application/x-ndjson."""
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        call_kwargs = handler._client.put_object.call_args.kwargs
        assert call_kwargs["ContentType"] == "application/x-ndjson"

    def test_upload_bucket_correct(self, handler):
        """Загрузка идёт в правильный бакет."""
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        call_kwargs = handler._client.put_object.call_args.kwargs
        assert call_kwargs["Bucket"] == "test-bucket"

    def test_upload_key_format(self, handler):
        """S3-ключ имеет формат: prefix/YYYY/MM/DD/HH-MM-SS-uuid.jsonl."""
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        call_kwargs = handler._client.put_object.call_args.kwargs
        key = call_kwargs["Key"]
        assert key.startswith("logs/")
        assert key.endswith(".jsonl")
        # Проверяем формат: logs/YYYY/MM/DD/HH-MM-SS-uuid.jsonl
        parts = key.split("/")
        assert len(parts) == 5  # prefix, year, month, day, filename


# ============================================================================
# Ручной flush
# ============================================================================


class TestFlush:
    """Тесты flush: немедленный сброс буфера."""

    def test_flush_uploads_buffer(self, handler):
        """flush загружает накопленные записи."""
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        assert len(handler._buffer) == 3
        handler.flush()
        assert len(handler._buffer) == 0
        handler._client.put_object.assert_called_once()

    def test_flush_empty_buffer_noop(self, handler):
        """flush с пустым буфером — не вызывает S3."""
        handler.flush()
        handler._client.put_object.assert_not_called()


# ============================================================================
# Защита от переполнения буфера
# ============================================================================


class TestBufferOverflow:
    """Тесты защиты от переполнения буфера."""

    def test_max_buffer_size_constant(self):
        """_MAX_BUFFER_SIZE имеет значение 50000."""
        assert _MAX_BUFFER_SIZE == 50_000

    def test_buffer_overflow_drops_old_records(self):
        """При переполнении буфера сбрасываются 10% старых записей."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(
                bucket="test",
                flush_size=_MAX_BUFFER_SIZE + 100,  # порог выше лимита
            )
        h._client = MagicMock()

        # Заполняем буфер до предела
        h._buffer = [f"record_{i}" for i in range(_MAX_BUFFER_SIZE)]

        # Добавляем ещё одну запись
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="overflow",
            args=None, exc_info=None,
        )
        h.emit(record)

        # 10% старых записей удалены + добавлена новая
        expected_drop = _MAX_BUFFER_SIZE // 10
        expected_size = _MAX_BUFFER_SIZE - expected_drop + 1
        assert len(h._buffer) == expected_size


# ============================================================================
# Обработка ошибок S3
# ============================================================================


class TestS3UploadError:
    """Тесты обработки ошибок при загрузке в S3."""

    def test_upload_error_returns_records_to_buffer(self, handler):
        """При ошибке S3 записи возвращаются в буфер."""
        handler._client.put_object.side_effect = RuntimeError("S3 down")

        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        # Записи вернулись в буфер (flush_size=5 → автоматический flush → ошибка)
        assert len(handler._buffer) == 5

    def test_upload_error_does_not_crash(self, handler):
        """Ошибка S3 не крашит handler."""
        handler._client.put_object.side_effect = RuntimeError("S3 down")

        # Заполняем до порога
        for i in range(5):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        # Можно продолжать писать
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="after error",
            args=None, exc_info=None,
        )
        handler.emit(record)
        assert len(handler._buffer) >= 1


# ============================================================================
# close
# ============================================================================


class TestClose:
    """Тесты close: остановка таймера и финальный сброс."""

    def test_close_flushes_buffer(self, handler):
        """close сбрасывает оставшийся буфер."""
        for i in range(3):
            record = logging.LogRecord(
                name="test", level=logging.INFO,
                pathname="", lineno=0, msg=f"msg {i}",
                args=None, exc_info=None,
            )
            handler.emit(record)

        handler.close()
        assert len(handler._buffer) == 0
        handler._client.put_object.assert_called_once()

    def test_close_stops_timer(self):
        """close останавливает фоновый таймер."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test", flush_size=100)
        h._client = MagicMock()
        mock_timer = MagicMock()
        h._timer = mock_timer

        h.close()
        mock_timer.cancel.assert_called_once()
        assert h._timer is None

    def test_close_sets_closed_flag(self, handler):
        """close устанавливает флаг _closed."""
        handler.close()
        assert handler._closed is True

    def test_close_idempotent(self, handler):
        """Повторный close не крашит."""
        handler.close()
        handler.close()  # не должно упасть


# ============================================================================
# Таймер
# ============================================================================


class TestTimer:
    """Тесты управления таймером."""

    def test_timer_not_started_when_closed(self):
        """Таймер не запускается если handler закрыт."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test")
        h._client = MagicMock()
        h._closed = True
        h._start_flush_timer()
        assert h._timer is None

    def test_timer_is_daemon(self):
        """Фоновый таймер — daemon (не блокирует завершение процесса)."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test", flush_interval=9999)
        h._client = MagicMock()
        h._closed = False

        # Вручную запускаем таймер
        h._start_flush_timer()
        assert h._timer is not None
        assert h._timer.daemon is True

        # Чистим
        h._timer.cancel()


# ============================================================================
# Lazy-инициализация S3-клиента
# ============================================================================


class TestLazyClient:
    """Тесты lazy-инициализации boto3 клиента."""

    def test_client_none_by_default(self):
        """До первого вызова клиент не инициализирован."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test")
        assert h._client is None

    def test_client_kwargs_stored(self):
        """Параметры для boto3 сохранены."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(
                bucket="test",
                endpoint_url="https://custom.endpoint",
                region_name="us-east-1",
                access_key="ak",
                secret_key="sk",
            )
        assert h._client_kwargs["endpoint_url"] == "https://custom.endpoint"
        assert h._client_kwargs["region_name"] == "us-east-1"
        assert h._client_kwargs["aws_access_key_id"] == "ak"
        assert h._client_kwargs["aws_secret_access_key"] == "sk"


# ============================================================================
# Конструктор
# ============================================================================


class TestConstructor:
    """Тесты конструктора S3LogHandler."""

    def test_default_prefix(self):
        """Префикс по умолчанию — 'logs'."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test")
        assert h.prefix == "logs"

    def test_custom_prefix_stripped(self):
        """Trailing слэш в префиксе убирается."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test", prefix="custom/prefix/")
        assert h.prefix == "custom/prefix"

    def test_bucket_stored(self):
        """Имя бакета сохраняется."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="my-bucket")
        assert h.bucket == "my-bucket"

    def test_flush_interval_stored(self):
        """flush_interval сохраняется."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test", flush_interval=120)
        assert h._flush_interval == 120

    def test_flush_size_stored(self):
        """flush_size сохраняется."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = S3LogHandler(bucket="test", flush_size=1000)
        assert h._flush_size == 1000


# ============================================================================
# create_s3_log_handler: фабрика с валидацией
# ============================================================================


class TestCreateS3LogHandler:
    """Тесты create_s3_log_handler: фабрика с валидацией."""

    def test_missing_bucket_raises(self):
        """Пустой bucket → ValueError."""
        with pytest.raises(ValueError, match="S3_LOG_BUCKET"):
            create_s3_log_handler(
                bucket="",
                access_key="ak",
                secret_key="sk",
            )

    def test_missing_access_key_raises(self):
        """Пустой access_key → ValueError."""
        with pytest.raises(ValueError, match="S3_LOG_ACCESS_KEY"):
            create_s3_log_handler(
                bucket="bucket",
                access_key="",
                secret_key="sk",
            )

    def test_missing_secret_key_raises(self):
        """Пустой secret_key → ValueError."""
        with pytest.raises(ValueError, match="S3_LOG_ACCESS_KEY"):
            create_s3_log_handler(
                bucket="bucket",
                access_key="ak",
                secret_key="",
            )

    def test_valid_params_creates_handler(self):
        """Валидные параметры создают S3LogHandler."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = create_s3_log_handler(
                bucket="test-bucket",
                access_key="ak",
                secret_key="sk",
            )
        assert isinstance(h, S3LogHandler)
        assert h.bucket == "test-bucket"
        # Чистим
        h._closed = True

    def test_custom_params_passed(self):
        """Кастомные параметры передаются в handler."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = create_s3_log_handler(
                bucket="custom-bucket",
                access_key="ak",
                secret_key="sk",
                prefix="custom-prefix",
                flush_interval=30,
                flush_size=100,
                level=logging.WARNING,
            )
        assert h.bucket == "custom-bucket"
        assert h.prefix == "custom-prefix"
        assert h._flush_interval == 30
        assert h._flush_size == 100
        assert h.level == logging.WARNING
        h._closed = True

    def test_default_level_is_info(self):
        """Уровень по умолчанию — INFO."""
        with patch.object(S3LogHandler, "_start_flush_timer"):
            h = create_s3_log_handler(
                bucket="test",
                access_key="ak",
                secret_key="sk",
            )
        assert h.level == logging.INFO
        h._closed = True
