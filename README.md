# Telegram -> Bybit Futures Bot (MVP)

Бот слушает один Telegram-канал с сигналами, парсит сообщения и открывает сделки на Bybit USDT Perpetual.  
Есть веб-админка с настройками, открытыми позициями, историей и обновлением PnL.

## 1) Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## 2) Настрой .env

- `TELEGRAM_API_ID` и `TELEGRAM_API_HASH`: возьми на [my.telegram.org](https://my.telegram.org).
- `TELEGRAM_CHANNEL_ID`: id канала с сигналами (формат `-100...`).
- `BYBIT_API_KEY/BYBIT_API_SECRET`: ключ с правами только на фьючерсную торговлю.
- `BYBIT_TESTNET=true`: сначала обязательно тестируй на testnet.

## 3) Запуск

```bash
python -m app.main
```

Админка: [http://localhost:8000](http://localhost:8000)

## Что уже есть

- Автопарсинг сигналов формата с картинок (LONG/SHORT, leverage, TP1-3, SL).
- Открытие market-ордера при получении сигнала.
- TP смещается по настройке (`tp_adjust_pct`), например на 0.05%.
- Настройка процента фиксации на TP1 (`close_at_tp1_pct`) сохранена в конфиге позиции.
- Из админки можно изменить TP для открытой позиции.
- Live обновление открытых позиций/PnL каждые 5 секунд.

## Важно перед реальным запуском

- Нужна дополнительная валидация шага цены (`tickSize`) и объема (`qtyStep`) по каждому инструменту.
- Нужна защита от дубликатов сигналов.
- Нужна обработка частичного закрытия на TP1/TP2/TP3 отдельными reduce-only ордерами.
- Добавь авторизацию в админку (например, login + 2FA).
