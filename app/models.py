from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, engine


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(16), default="bot1", nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    entry_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    entry_market: Mapped[float | None] = mapped_column(Float)
    entry_limit: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float] = mapped_column(Float, nullable=False)
    tp2: Mapped[float] = mapped_column(Float, nullable=False)
    tp3: Mapped[float] = mapped_column(Float, nullable=False)
    sl: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(16), default="bot1", nullable=False)
    signal_id: Mapped[int] = mapped_column(Integer, nullable=False)
    symbol: Mapped[str] = mapped_column(String(32), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    leverage: Mapped[int] = mapped_column(Integer, nullable=False)
    margin_usdt: Mapped[float] = mapped_column(Float, nullable=False)
    qty: Mapped[float] = mapped_column(Float, nullable=False)
    entry_price: Mapped[float] = mapped_column(Float, nullable=False)
    tp_price: Mapped[float] = mapped_column(Float, nullable=False)
    sl_price: Mapped[float] = mapped_column(Float, nullable=False)
    close_at_tp1_pct: Mapped[float] = mapped_column(Float, nullable=False, default=100.0)
    exchange_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    limit_order_id: Mapped[str | None] = mapped_column(String(64))
    pending_limit_qty: Mapped[float | None] = mapped_column(Float)
    pending_limit_price: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="OPEN")
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class BotSetting(Base):
    __tablename__ = "bot_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(16), default="bot1", nullable=False, unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    margin_usdt: Mapped[float] = mapped_column(Float, default=50.0, nullable=False)
    tp_adjust_pct: Mapped[float] = mapped_column(Float, default=0.05, nullable=False)
    close_at_tp1_pct: Mapped[float] = mapped_column(Float, default=100.0, nullable=False)
    min_leverage: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class ChannelMessage(Base):
    __tablename__ = "channel_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_id: Mapped[str] = mapped_column(String(16), default="bot1", nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    is_signal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(32))
    side: Mapped[str | None] = mapped_column(String(8))
    leverage: Mapped[int | None] = mapped_column(Integer)
    entry_kind: Mapped[str | None] = mapped_column(String(16))
    entry_market: Mapped[float | None] = mapped_column(Float)
    entry_limit: Mapped[float | None] = mapped_column(Float)
    tp1: Mapped[float | None] = mapped_column(Float)
    tp2: Mapped[float | None] = mapped_column(Float)
    tp3: Mapped[float | None] = mapped_column(Float)
    sl: Mapped[float | None] = mapped_column(Float)
    parse_error: Mapped[str | None] = mapped_column(Text)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


def ensure_schema() -> None:
    """Добавляет недостающие колонки в старые sqlite-таблицы без потери данных."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        migrations = [
            ("signals", "bot_id", "ALTER TABLE signals ADD COLUMN bot_id VARCHAR(16) DEFAULT 'bot1'"),
            ("positions", "bot_id", "ALTER TABLE positions ADD COLUMN bot_id VARCHAR(16) DEFAULT 'bot1'"),
            ("channel_messages", "bot_id", "ALTER TABLE channel_messages ADD COLUMN bot_id VARCHAR(16) DEFAULT 'bot1'"),
            ("bot_settings", "bot_id", "ALTER TABLE bot_settings ADD COLUMN bot_id VARCHAR(16) DEFAULT 'bot1'"),
            ("bot_settings", "min_leverage", "ALTER TABLE bot_settings ADD COLUMN min_leverage INTEGER DEFAULT 1"),
            ("positions", "limit_order_id", "ALTER TABLE positions ADD COLUMN limit_order_id VARCHAR(64)"),
            ("positions", "pending_limit_qty", "ALTER TABLE positions ADD COLUMN pending_limit_qty FLOAT"),
            ("positions", "pending_limit_price", "ALTER TABLE positions ADD COLUMN pending_limit_price FLOAT"),
        ]
        for table, column, ddl in migrations:
            cols = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})")).fetchall()}
            if column not in cols:
                try:
                    conn.execute(text(ddl))
                except Exception:
                    pass

        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(bot_settings)")).fetchall()}
        if "ai_enabled" not in cols:
            try:
                conn.execute(text("ALTER TABLE bot_settings ADD COLUMN ai_enabled BOOLEAN DEFAULT 0"))
                conn.execute(text("UPDATE bot_settings SET ai_enabled = 1 WHERE bot_id = 'bot2'"))
                conn.execute(text("UPDATE bot_settings SET ai_enabled = 0 WHERE bot_id = 'bot1'"))
            except Exception:
                pass
