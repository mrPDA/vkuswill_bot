-- Миграция 006: Анонимизация персональных данных
-- Очищаем username, first_name, last_name — ПДн больше не хранятся.
-- Колонки остаются в схеме (обратная совместимость), но не заполняются.

UPDATE users SET username = NULL, first_name = '', last_name = NULL;
