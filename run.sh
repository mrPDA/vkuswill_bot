#!/bin/bash
# Быстрый запуск бота без пересборки через uv
cd "$(dirname "$0")"

# Убить предыдущий экземпляр, если есть
if [ -f .bot.pid ]; then
    kill -9 "$(cat .bot.pid)" 2>/dev/null
    rm -f .bot.pid
    sleep 2
fi

# Запуск напрямую через venv
PYTHONPATH=src .venv/bin/python -m vkuswill_bot &
echo $! > .bot.pid
echo "Бот запущен (PID: $(cat .bot.pid))"
