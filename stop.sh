#!/bin/bash
# Остановка бота
cd "$(dirname "$0")"

if [ -f .bot.pid ]; then
    PID=$(cat .bot.pid)
    kill -9 "$PID" 2>/dev/null
    rm -f .bot.pid
    echo "Бот остановлен (PID: $PID)"
else
    # Fallback — убить по имени
    pkill -9 -f "python.*vkuswill_bot" 2>/dev/null
    echo "Бот остановлен"
fi
