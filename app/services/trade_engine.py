from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.models import BotSetting, Position, Signal
from app.schemas import ParsedSignal
from app.services.bybit_client import BybitClient
from app.services.log_buffer import log_event


def _expected_pnl(entry: float, exit_price: float, qty: float, side: str) -> float:
    if side == "LONG":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


class TradeEngine:
    def __init__(self, bybit_client: BybitClient, notify_bots: dict | None = None):
        self.bybit = bybit_client
        self.notify_bots = notify_bots or {}

    def get_or_create_settings(self, db: Session, bot_id: str = "bot1") -> BotSetting:
        row = db.query(BotSetting).filter(BotSetting.bot_id == bot_id).first()
        if row:
            return row

        # миграция: если есть старый ряд без bot_id / единственный ряд
        legacy = db.query(BotSetting).first()
        if legacy and (not getattr(legacy, "bot_id", None) or legacy.bot_id in ("", "bot1")) and bot_id == "bot1":
            legacy.bot_id = "bot1"
            db.commit()
            db.refresh(legacy)
            return legacy

        row = BotSetting(
            bot_id=bot_id,
            enabled=True,
            margin_usdt=app_settings.default_margin_usdt,
            tp_adjust_pct=app_settings.default_take_profit_adjust_pct,
            close_at_tp1_pct=app_settings.default_close_at_tp1_pct,
            min_leverage=1,
            ai_enabled=(bot_id == "bot2"),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row

    def _notify(self, bot_id: str, symbol: str, margin: float, leverage: int, side: str) -> None:
        notifier = self.notify_bots.get(bot_id)
        if notifier:
            notifier.send_trade_opened(symbol=symbol, margin=margin, leverage=leverage, side=side)

    def handle_signal(self, db: Session, signal: ParsedSignal, bot_id: str = "bot1", force: bool = False) -> Position:
        bot_settings = self.get_or_create_settings(db, bot_id)
        if not bot_settings.enabled and not force:
            log_event("WARN", bot_id, "Бот выключен — сигнал пропущен")
            raise ValueError("Bot is disabled")

        min_leverage = getattr(bot_settings, "min_leverage", 1) or 1
        if signal.leverage < min_leverage and not force:
            log_event(
                "WARN",
                bot_id,
                f"Сигнал пропущен: плечо x{signal.leverage} < минимум x{min_leverage} ({signal.symbol})",
            )
            raise ValueError(f"Leverage x{signal.leverage} below minimum x{min_leverage}")

        log_event("INFO", bot_id, f"Новый сигнал: {signal.symbol} {signal.side} x{signal.leverage} ({signal.entry_kind})")

        stored_signal = Signal(
            bot_id=bot_id,
            raw_text=signal.raw_text,
            symbol=signal.symbol,
            side=signal.side,
            leverage=signal.leverage,
            entry_kind=signal.entry_kind,
            entry_market=signal.entry_market,
            entry_limit=signal.entry_limit,
            tp1=signal.tp1,
            tp2=signal.tp2,
            tp3=signal.tp3,
            sl=signal.sl,
        )
        db.add(stored_signal)
        db.commit()
        db.refresh(stored_signal)

        try:
            self.bybit.set_leverage(signal.symbol, signal.leverage)
        except Exception as exc:
            # Bybit иногда кидает ошибку если плечо уже установлено — продолжаем
            log_event("WARN", bot_id, f"set_leverage: {exc}")

        if signal.side == "LONG":
            adjusted_tp = signal.tp1 * (1 - bot_settings.tp_adjust_pct / 100)
        else:
            adjusted_tp = signal.tp1 * (1 + bot_settings.tp_adjust_pct / 100)

        adjusted_tp = self.bybit.normalize_price(signal.symbol, adjusted_tp)
        sl_price = self.bybit.normalize_price(signal.symbol, signal.sl)
        total_margin = float(bot_settings.margin_usdt)

        # BOTH (рынок + лимит): делим маржу пополам — как в канале bot1
        split = signal.entry_kind == "BOTH" and signal.entry_limit
        market_margin = (total_margin / 2.0) if split else (0.0 if signal.entry_kind == "LIMIT" else total_margin)
        limit_margin = (total_margin / 2.0) if split else (total_margin if signal.entry_kind == "LIMIT" else 0.0)

        market_price = self.bybit.get_last_price(signal.symbol)
        market_order_id = ""
        market_qty = 0.0
        limit_order_id = None
        pending_limit_qty = None
        pending_limit_price = None

        if market_margin > 0:
            raw_qty = (market_margin * signal.leverage) / market_price
            market_qty = self.bybit.normalize_qty(signal.symbol, raw_qty)
            log_event(
                "INFO",
                bot_id,
                f"Рынок: {signal.symbol} margin={market_margin} qty={market_qty} "
                f"entry≈{market_price} tp={adjusted_tp} sl={sl_price}",
            )
            market_order_id = self.bybit.place_market_order(
                symbol=signal.symbol,
                side=signal.side,
                qty=market_qty,
                tp=adjusted_tp,
                sl=sl_price,
            )

        if limit_margin > 0 and signal.entry_limit:
            limit_price = self.bybit.normalize_price(signal.symbol, float(signal.entry_limit))
            raw_limit_qty = (limit_margin * signal.leverage) / limit_price
            limit_qty = self.bybit.normalize_qty(signal.symbol, raw_limit_qty)
            log_event(
                "INFO",
                bot_id,
                f"Лимит: {signal.symbol} margin={limit_margin} qty={limit_qty} "
                f"price={limit_price} tp={adjusted_tp} sl={sl_price}",
            )
            limit_order_id = self.bybit.place_limit_order(
                symbol=signal.symbol,
                side=signal.side,
                qty=limit_qty,
                price=limit_price,
                tp=adjusted_tp,
                sl=sl_price,
            )
            pending_limit_qty = limit_qty
            pending_limit_price = limit_price

        if market_qty <= 0 and not limit_order_id:
            raise ValueError("Не удалось выставить ни рыночный, ни лимитный ордер")

        status = "OPEN" if market_qty > 0 else "PENDING"
        entry_price = market_price if market_qty > 0 else float(pending_limit_price or signal.entry_limit or 0)
        qty = market_qty if market_qty > 0 else float(pending_limit_qty or 0)
        exchange_order_id = market_order_id or (limit_order_id or "pending")

        position = Position(
            bot_id=bot_id,
            signal_id=stored_signal.id,
            symbol=signal.symbol,
            side=signal.side,
            leverage=signal.leverage,
            margin_usdt=total_margin,
            qty=qty,
            entry_price=entry_price,
            tp_price=adjusted_tp,
            sl_price=sl_price,
            close_at_tp1_pct=bot_settings.close_at_tp1_pct,
            exchange_order_id=exchange_order_id,
            limit_order_id=limit_order_id,
            pending_limit_qty=pending_limit_qty,
            pending_limit_price=pending_limit_price,
            status=status,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            updated_at=datetime.utcnow(),
        )
        db.add(position)
        db.commit()
        db.refresh(position)

        parts = []
        if market_qty > 0:
            parts.append(f"рынок {market_qty}")
        if pending_limit_qty:
            parts.append(f"лимит {pending_limit_qty}@{pending_limit_price}")
        log_event(
            "INFO",
            bot_id,
            f"Сделка: {position.symbol} {' + '.join(parts)} order={exchange_order_id}"
            + (f" limit={limit_order_id}" if limit_order_id else ""),
        )
        self._notify(bot_id, position.symbol, position.margin_usdt, position.leverage, position.side)
        return position

    def open_manual_trade(
        self,
        db: Session,
        symbol: str,
        side: str,
        leverage: int,
        margin_usdt: float,
        tp_price: float,
        sl_price: float,
        bot_id: str = "bot1",
    ) -> Position:
        symbol = symbol.strip().upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        side = side.strip().upper()
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")

        log_event("INFO", bot_id, f"Тестовая сделка: {symbol} {side} x{leverage} маржа={margin_usdt}")

        stored_signal = Signal(
            bot_id=bot_id,
            raw_text="MANUAL/TEST TRADE",
            symbol=symbol,
            side=side,
            leverage=leverage,
            entry_kind="MANUAL",
            entry_market=None,
            entry_limit=None,
            tp1=tp_price,
            tp2=tp_price,
            tp3=tp_price,
            sl=sl_price,
        )
        db.add(stored_signal)
        db.commit()
        db.refresh(stored_signal)

        try:
            self.bybit.set_leverage(symbol, leverage)
        except Exception as exc:
            log_event("WARN", bot_id, f"set_leverage: {exc}")

        market_price = self.bybit.get_last_price(symbol)
        qty = self.bybit.normalize_qty(symbol, (margin_usdt * leverage) / market_price)
        tp_price = self.bybit.normalize_price(symbol, tp_price)
        sl_price = self.bybit.normalize_price(symbol, sl_price)

        order_id = self.bybit.place_market_order(
            symbol=symbol,
            side=side,
            qty=qty,
            tp=tp_price,
            sl=sl_price,
        )

        position = Position(
            bot_id=bot_id,
            signal_id=stored_signal.id,
            symbol=symbol,
            side=side,
            leverage=leverage,
            margin_usdt=margin_usdt,
            qty=qty,
            entry_price=market_price,
            tp_price=tp_price,
            sl_price=sl_price,
            close_at_tp1_pct=100.0,
            exchange_order_id=order_id,
            status="OPEN",
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            updated_at=datetime.utcnow(),
        )
        db.add(position)
        db.commit()
        db.refresh(position)
        log_event(
            "INFO",
            bot_id,
            f"Тестовая сделка открыта: {position.symbol} qty={position.qty} entry={position.entry_price}",
        )
        self._notify(bot_id, position.symbol, position.margin_usdt, position.leverage, position.side)
        return position

    def find_open_position(self, db: Session, bot_id: str, symbol: str) -> Position | None:
        symbol = symbol.upper()
        if not symbol.endswith("USDT"):
            symbol = f"{symbol}USDT"
        return (
            db.query(Position)
            .filter(
                Position.bot_id == bot_id,
                Position.status.in_(("OPEN", "PENDING")),
                Position.symbol == symbol,
            )
            .order_by(Position.created_at.desc())
            .first()
        )

    def list_open_positions(self, db: Session, bot_id: str) -> list[Position]:
        return (
            db.query(Position)
            .filter(Position.bot_id == bot_id, Position.status.in_(("OPEN", "PENDING")))
            .order_by(Position.created_at.desc())
            .all()
        )

    def _cancel_pending_limit(self, position: Position, bot_id: str | None = None) -> None:
        bot_id = bot_id or position.bot_id
        if not position.limit_order_id or not position.pending_limit_qty:
            return
        try:
            self.bybit.cancel_order(position.symbol, position.limit_order_id)
            log_event("INFO", bot_id, f"Отменена лимитка {position.symbol} order={position.limit_order_id}")
        except Exception as exc:
            log_event("WARN", bot_id, f"Не удалось отменить лимитку {position.symbol}: {exc}")
        position.pending_limit_qty = None

    def apply_ai_action(self, db: Session, bot_id: str, decision: dict) -> dict:
        action = decision.get("action")
        if action in (None, "ignore"):
            return {"ok": False, "skipped": True, "reason": decision.get("reason", "ignore")}

        symbol = decision.get("symbol")
        if not symbol:
            # Никогда не угадываем «единственную открытую» — иначе чужой UPDATE закрывает нашу сделку
            return {"ok": False, "reason": "AI не указал symbol — действие пропущено"}

        position = self.find_open_position(db, bot_id, symbol)
        if not position:
            log_event("WARN", bot_id, f"AI: нет открытой позиции {symbol} у {bot_id}")
            return {"ok": False, "skipped": True, "reason": f"Нет открытой позиции {symbol}"}

        if action == "close":
            self._cancel_pending_limit(position, bot_id)
            if position.status == "OPEN" and position.qty > 0:
                order_id = self.bybit.close_position(position.symbol, position.side, position.qty)
            else:
                order_id = "limit-cancelled"
            position.status = "CLOSED"
            position.updated_at = datetime.utcnow()
            db.commit()
            log_event("INFO", bot_id, f"AI закрыл {position.symbol} order={order_id}")
            self._notify_ai(bot_id, f"Закрыта позиция {position.symbol} по команде канала")
            return {"ok": True, "action": "close", "symbol": position.symbol}

        if position.status != "OPEN":
            return {"ok": False, "reason": "Лимитка ещё не исполнена — SL/TP пока недоступны"}

        if action == "breakeven":
            sl = self.bybit.normalize_price(position.symbol, position.entry_price)
            self.bybit.amend_sl(position.symbol, sl)
            position.sl_price = sl
            position.updated_at = datetime.utcnow()
            db.commit()
            log_event("INFO", bot_id, f"AI бу: {position.symbol} SL -> {sl}")
            self._notify_ai(bot_id, f"{position.symbol}: стоп перенесён в безубыток ({sl})")
            return {"ok": True, "action": "breakeven", "symbol": position.symbol, "sl": sl}

        if action == "set_sl":
            sl_raw = decision.get("sl_price")
            if sl_raw is None:
                return {"ok": False, "reason": "set_sl без цены"}
            sl = self.bybit.normalize_price(position.symbol, float(sl_raw))
            self.bybit.amend_sl(position.symbol, sl)
            position.sl_price = sl
            position.updated_at = datetime.utcnow()
            db.commit()
            log_event("INFO", bot_id, f"AI SL: {position.symbol} -> {sl}")
            self._notify_ai(bot_id, f"{position.symbol}: новый SL {sl}")
            return {"ok": True, "action": "set_sl", "symbol": position.symbol, "sl": sl}

        if action == "set_tp":
            tp_raw = decision.get("tp_price")
            if tp_raw is None:
                return {"ok": False, "reason": "set_tp без цены"}
            tp = self.bybit.normalize_price(position.symbol, float(tp_raw))
            self.bybit.amend_tp(position.symbol, tp)
            position.tp_price = tp
            position.updated_at = datetime.utcnow()
            db.commit()
            log_event("INFO", bot_id, f"AI TP: {position.symbol} -> {tp}")
            self._notify_ai(bot_id, f"{position.symbol}: новый TP {tp}")
            return {"ok": True, "action": "set_tp", "symbol": position.symbol, "tp": tp}

        return {"ok": False, "reason": f"Unknown action {action}"}

    def _notify_ai(self, bot_id: str, text: str) -> None:
        notifier = self.notify_bots.get(bot_id)
        if notifier:
            notifier.send_message(f"[{bot_id}] {text}")

    def _sync_pending_limit(self, position: Position) -> None:
        if not position.limit_order_id or not position.pending_limit_qty:
            return
        try:
            order = self.bybit.get_order(position.symbol, position.limit_order_id)
        except Exception as exc:
            log_event("WARN", position.bot_id, f"Проверка лимитки {position.symbol}: {exc}")
            return

        status = (order.get("orderStatus") or "").strip()
        if status == "Filled":
            filled = float(order.get("cumExecQty") or position.pending_limit_qty or 0)
            avg = float(order.get("avgPrice") or position.pending_limit_price or 0)
            if position.status == "PENDING":
                position.status = "OPEN"
                position.qty = filled
                if avg:
                    position.entry_price = avg
            else:
                old_qty = float(position.qty or 0)
                old_entry = float(position.entry_price or 0)
                new_qty = old_qty + filled
                if new_qty > 0 and avg:
                    position.entry_price = ((old_entry * old_qty) + (avg * filled)) / new_qty
                position.qty = new_qty
            position.pending_limit_qty = None
            position.updated_at = datetime.utcnow()
            log_event(
                "INFO",
                position.bot_id,
                f"Лимитка исполнена: {position.symbol} +{filled} → qty={position.qty}",
            )
        elif status in {"Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"}:
            position.pending_limit_qty = None
            if position.status == "PENDING":
                position.status = "CLOSED"
            position.updated_at = datetime.utcnow()
            log_event("INFO", position.bot_id, f"Лимитка снята: {position.symbol} ({status})")

    def refresh_open_positions(self, db: Session) -> None:
        positions = (
            db.query(Position)
            .filter(Position.status.in_(("OPEN", "PENDING")))
            .all()
        )
        if not positions:
            return

        open_by_symbol = {
            p["symbol"]: p
            for p in self.bybit.get_open_positions()
            if float(p.get("size", 0)) > 0
        }

        for pos in positions:
            self._sync_pending_limit(pos)

            if pos.status == "PENDING":
                # ждём исполнения лимитки — позиции на бирже ещё может не быть
                continue

            exchange_pos = open_by_symbol.get(pos.symbol)
            if not exchange_pos:
                self._cancel_pending_limit(pos)
                pos.status = "CLOSED"
                pos.updated_at = datetime.utcnow()
                log_event("INFO", pos.bot_id, f"Позиция закрыта: {pos.symbol}")
                continue

            size = float(exchange_pos.get("size", 0) or 0)
            if size > 0:
                pos.qty = size
            pos.unrealized_pnl = float(exchange_pos.get("unrealisedPnl", 0.0))
            pos.updated_at = datetime.utcnow()
        db.commit()

    def calculate_tp_sl_projection(self, position: Position) -> dict[str, float]:
        tp_profit = _expected_pnl(position.entry_price, position.tp_price, position.qty, position.side)
        sl_profit = _expected_pnl(position.entry_price, position.sl_price, position.qty, position.side)
        return {"tp_projection_usdt": tp_profit, "sl_projection_usdt": sl_profit}
