#!/bin/bash
# Быстрый запуск бота без пересборки через uv
cd "$(dirname "$0")"

# Остановить предыдущий экземпляр корректно, если есть
if [ -f .bot.pid ]; then
    OLD_PID=$(cat .bot.pid)
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Останавливаю предыдущий экземпляр (PID: $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null
        # Ожидаем завершения до 10 секунд
        for i in $(seq 1 10); do
            if ! kill -0 "$OLD_PID" 2>/dev/null; then
                break
            fi
            sleep 1
        done
        # Принудительно, если не завершился
        if kill -0 "$OLD_PID" 2>/dev/null; then
            echo "Принудительная остановка (SIGKILL)..."
            kill -9 "$OLD_PID" 2>/dev/null
        fi
    fi
    rm -f .bot.pid
fi

# Запуск напрямую через venv
PYTHONPATH=src .venv/bin/python -m vkuswill_bot &
echo $! > .bot.pid
echo "Бот запущен (PID: $(cat .bot.pid))"
