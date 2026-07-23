# Telegram -> Bybit Futures Bot

Бот слушает Telegram-каналы с сигналами, парсит сообщения и открывает сделки на Bybit USDT Perpetual.  
Есть веб-админка с паролем, настройками, позициями и терминалом логов.

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Настройки уже в `app/config.py` — `.env` не обязателен.

## Запуск

```bash
python -m app.main
```


## Что есть

- 2 бота / 2 канала
- Парсинг сигналов (рынок / лимит / оба)
- AI follow-up (закрытие / бу / SL / TP)
- Мобильная админка
- Notify-боты в Telegram

## Важно

Ключи API зашиты в код для удобства локального запуска. Перед публичным деплоем лучше вынести секреты в env и сменить пароль.
