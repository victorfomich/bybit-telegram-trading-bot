from datetime import datetime

from pydantic import BaseModel, Field


class ParsedSignal(BaseModel):
    raw_text: str
    symbol: str
    side: str
    leverage: int
    entry_kind: str
    entry_market: float | None = None
    entry_limit: float | None = None
    tp1: float
    tp2: float
    tp3: float
    sl: float


class UpdateSettingsRequest(BaseModel):
    enabled: bool
    margin_usdt: float
    tp_adjust_pct: float
    close_at_tp1_pct: float
    min_leverage: int = 1
    ai_enabled: bool = False
    bot_id: str = "bot1"


class UpdatePositionTpRequest(BaseModel):
    tp_price: float


class ManualTradeRequest(BaseModel):
    symbol: str
    side: str
    leverage: int = 10
    margin_usdt: float
    tp_price: float
    sl_price: float
    bot_id: str = "bot1"


class PositionOut(BaseModel):
    id: int
    bot_id: str = "bot1"
    symbol: str
    side: str
    leverage: int
    margin_usdt: float
    qty: float
    entry_price: float
    tp_price: float
    sl_price: float
    unrealized_pnl: float
    realized_pnl: float
    status: str
    created_at: datetime

    class Config:
        from_attributes = True
