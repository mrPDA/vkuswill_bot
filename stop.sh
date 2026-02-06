#!/bin/bash
# Остановка бота (graceful → force fallback)
cd "$(dirname "$0")"

if [ -f .bot.pid ]; then
    PID=$(cat .bot.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "Останавливаю бота (PID: $PID)..."
        # Graceful: отправляем SIGTERM
        kill "$PID" 2>/dev/null
        # Ожидаем завершения до 15 секунд
        for i in $(seq 1 15); do
            if ! kill -0 "$PID" 2>/dev/null; then
                echo "Бот остановлен корректно (PID: $PID)"
                rm -f .bot.pid
                exit 0
            fi
            sleep 1
        done
        # Fallback: SIGKILL если не завершился
        echo "Принудительная остановка (SIGKILL)..."
        kill -9 "$PID" 2>/dev/null
        echo "Бот остановлен принудительно (PID: $PID)"
    else
        echo "Процесс $PID не найден"
    fi
    rm -f .bot.pid
else
    # Fallback — остановить по имени (graceful)
    pkill -f "python.*vkuswill_bot" 2>/dev/null
    echo "Бот остановлен"
fi
