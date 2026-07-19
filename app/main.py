import asyncio
import os
from datetime import datetime
from pathlib import Path

from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    COOKIE_NAME,
    AUTH_MAX_AGE,
    check_password,
    is_authenticated,
    make_auth_token,
    require_auth_middleware,
)
from app.config import BOT_IDS, BOT_LABELS, is_serverless, settings
from app.db import SessionLocal, get_db
from app.models import Position, ensure_schema
from app.schemas import ManualTradeRequest, ParsedSignal, UpdatePositionTpRequest, UpdateSettingsRequest
from app.services.ai_manager import ai_manager
from app.services.bybit_client import BybitClient
from app.services.channel_store import (
    channel_message_to_dict,
    get_last_channel_message,
    get_last_signal_message,
)
from app.services.log_buffer import bot_logs, log_event, setup_bot_logging
from app.services.notify_bot import NotifyBot, run_notify_bot_in_background
from app.services.telegram_listener import MultiChannelTelegramListener, run_listener_in_background
from app.services.trade_engine import TradeEngine

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"
SERVERLESS = is_serverless()

app = FastAPI(title="Telegram Bybit Futures Bot")
app.middleware("http")(require_auth_middleware)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ensure_schema()

notify_bots: dict[str, NotifyBot] = {
    "bot1": NotifyBot(
        bot_id="bot1",
        token=settings.telegram_notify_bot_token,
        target_username=settings.telegram_notify_username,
    ),
    "bot2": NotifyBot(
        bot_id="bot2",
        token=settings.telegram_notify_bot_token_2,
        target_username=settings.telegram_notify_username_2,
    ),
}

bybit_client = BybitClient()
trade_engine = TradeEngine(bybit_client, notify_bots=notify_bots)

channels: dict[str, int] = {"bot1": settings.telegram_channel_id}
if settings.telegram_channel_id_2:
    channels["bot2"] = settings.telegram_channel_id_2

# Telethon session нельзя создавать на read-only FS Vercel
tg_listener: MultiChannelTelegramListener | None = None
if not SERVERLESS:
    tg_listener = MultiChannelTelegramListener(trade_engine, channels)


def _cookie_secure() -> bool:
    return SERVERLESS or os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}


def _validate_bot_id(bot_id: str) -> str:
    if bot_id not in BOT_IDS:
        raise HTTPException(status_code=400, detail=f"Unknown bot_id: {bot_id}")
    return bot_id


@app.on_event("startup")
async def startup() -> None:
    setup_bot_logging()
    log_event("INFO", "system", "Сервер запущен")
    if SERVERLESS:
        log_event(
            "WARN",
            "system",
            "Vercel/serverless режим: Telegram listener и фоновые боты отключены. "
            "Админка доступна, автоторговля только на VPS/локально.",
        )
    else:
        log_event("INFO", "system", f"Админка: http://{settings.app_host}:{settings.app_port}")

    db = SessionLocal()
    try:
        for bot_id in BOT_IDS:
            trade_engine.get_or_create_settings(db, bot_id)
    finally:
        db.close()

    if SERVERLESS:
        return

    if tg_listener is not None:
        run_listener_in_background(tg_listener)
    for bot_id, nb in notify_bots.items():
        if nb.is_configured:
            run_notify_bot_in_background(nb)
        else:
            log_event("WARN", bot_id, "Notify-бот не настроен (нет токена)")
    if "bot2" not in channels:
        log_event("WARN", "bot2", "TELEGRAM_CHANNEL_ID_2 не задан — второй канал не слушается")

    asyncio.create_task(_position_sync_loop())


async def _position_sync_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            trade_engine.refresh_open_positions(db)
        except Exception:
            pass
        finally:
            db.close()
        await asyncio.sleep(5)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"error": None},
    )


@app.post("/login")
async def login_submit(request: Request, password: str = Form(...)):
    if not check_password(password):
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={"error": "Неверный пароль"},
            status_code=401,
        )
    response = RedirectResponse(url="/", status_code=303)
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_auth_token(),
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return response


@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    password = str((body or {}).get("password") or "")
    if not check_password(password):
        raise HTTPException(status_code=401, detail="Неверный пароль")
    response = JSONResponse({"ok": True})
    response.set_cookie(
        key=COOKIE_NAME,
        value=make_auth_token(),
        max_age=AUTH_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=_cookie_secure(),
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


@app.get("/", response_class=HTMLResponse)
def admin_panel(request: Request, db: Session = Depends(get_db)):
    settings_map = {}
    for bot_id in BOT_IDS:
        settings_map[bot_id] = trade_engine.get_or_create_settings(db, bot_id)

    open_positions = (
        db.query(Position)
        .filter(Position.status.in_(("OPEN", "PENDING")))
        .order_by(Position.created_at.desc())
        .all()
    )
    closed_positions = (
        db.query(Position).filter(Position.status == "CLOSED").order_by(Position.created_at.desc()).limit(100).all()
    )
    projections = {p.id: trade_engine.calculate_tp_sl_projection(p) for p in open_positions}

    last_signals = {bot_id: get_last_signal_message(db, bot_id) for bot_id in BOT_IDS}

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "bot_ids": BOT_IDS,
            "bot_labels": BOT_LABELS,
            "settings_map": settings_map,
            "open_positions": open_positions,
            "closed_positions": closed_positions,
            "projections": projections,
            "last_signals": last_signals,
            "channels": channels,
            "now": datetime.utcnow(),
        },
    )


@app.post("/api/settings")
def update_settings(payload: UpdateSettingsRequest, db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(payload.bot_id)
    st = trade_engine.get_or_create_settings(db, bot_id)
    st.enabled = payload.enabled
    st.margin_usdt = payload.margin_usdt
    st.tp_adjust_pct = payload.tp_adjust_pct
    st.close_at_tp1_pct = payload.close_at_tp1_pct
    st.min_leverage = max(1, int(payload.min_leverage))
    st.ai_enabled = bool(payload.ai_enabled)
    st.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "bot_id": bot_id}


@app.get("/api/settings")
def get_settings(bot_id: str = Query("bot1"), db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(bot_id)
    st = trade_engine.get_or_create_settings(db, bot_id)
    return {
        "bot_id": bot_id,
        "enabled": st.enabled,
        "margin_usdt": st.margin_usdt,
        "tp_adjust_pct": st.tp_adjust_pct,
        "close_at_tp1_pct": st.close_at_tp1_pct,
        "min_leverage": getattr(st, "min_leverage", 1) or 1,
        "ai_enabled": bool(getattr(st, "ai_enabled", False)),
    }


@app.post("/api/positions/{position_id}/tp")
def update_position_tp(position_id: int, payload: UpdatePositionTpRequest, db: Session = Depends(get_db)):
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(status_code=404, detail="Position not found")
    if position.status != "OPEN":
        raise HTTPException(status_code=400, detail="Position is closed")

    bybit_client.amend_tp(position.symbol, payload.tp_price, position.side)
    position.tp_price = payload.tp_price
    position.updated_at = datetime.utcnow()
    db.commit()
    return {"ok": True}


@app.get("/api/positions/open")
def get_open_positions(bot_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Position).filter(Position.status.in_(("OPEN", "PENDING")))
    if bot_id:
        bot_id = _validate_bot_id(bot_id)
        query = query.filter(Position.bot_id == bot_id)
    positions = query.order_by(Position.created_at.desc()).all()
    rows = []
    for p in positions:
        projections = trade_engine.calculate_tp_sl_projection(p)
        rows.append(
            {
                "id": p.id,
                "bot_id": p.bot_id,
                "symbol": p.symbol,
                "side": p.side,
                "leverage": p.leverage,
                "qty": p.qty,
                "entry_price": p.entry_price,
                "tp_price": p.tp_price,
                "sl_price": p.sl_price,
                "status": p.status,
                "pending_limit_qty": getattr(p, "pending_limit_qty", None),
                "pending_limit_price": getattr(p, "pending_limit_price", None),
                "unrealized_pnl": p.unrealized_pnl,
                "tp_projection_usdt": projections["tp_projection_usdt"],
                "sl_projection_usdt": projections["sl_projection_usdt"],
            }
        )
    return rows


@app.get("/api/channel/last-signal")
async def get_last_signal(bot_id: str = Query("bot1"), db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(bot_id)
    msg = get_last_signal_message(db, bot_id)
    if not msg and tg_listener is not None:
        try:
            await tg_listener.fetch_last_channel_signal(bot_id)
            msg = get_last_signal_message(db, bot_id)
        except Exception:
            pass
    if not msg:
        msg = get_last_channel_message(db, bot_id)
    return {"signal": channel_message_to_dict(msg)}


@app.post("/api/channel/refresh")
async def refresh_channel_signal(bot_id: str = Query("bot1"), db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(bot_id)
    if bot_id not in channels:
        raise HTTPException(status_code=400, detail=f"Канал для {bot_id} не настроен в .env")
    if tg_listener is None:
        raise HTTPException(
            status_code=503,
            detail="Telegram listener недоступен на Vercel. Запусти бота на VPS/локально.",
        )
    try:
        await tg_listener.fetch_last_channel_signal(bot_id)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Telegram unavailable: {exc}") from exc
    msg = get_last_signal_message(db, bot_id) or get_last_channel_message(db, bot_id)
    return {"signal": channel_message_to_dict(msg)}


@app.post("/api/test-trade/from-last-signal")
async def test_trade_from_last_signal(bot_id: str = Query("bot1"), db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(bot_id)
    msg = get_last_signal_message(db, bot_id)
    if not msg and tg_listener is not None:
        try:
            await tg_listener.fetch_last_channel_signal(bot_id)
            msg = get_last_signal_message(db, bot_id)
        except Exception:
            pass
    if not msg:
        raise HTTPException(status_code=404, detail="Нет сигналов в истории канала")
    if not msg.is_signal:
        raise HTTPException(status_code=400, detail=f"Последнее сообщение не сигнал: {msg.parse_error}")

    parsed = ParsedSignal(
        raw_text=msg.raw_text,
        symbol=msg.symbol,
        side=msg.side,
        leverage=msg.leverage,
        entry_kind=msg.entry_kind,
        entry_market=msg.entry_market,
        entry_limit=msg.entry_limit,
        tp1=msg.tp1,
        tp2=msg.tp2,
        tp3=msg.tp3,
        sl=msg.sl,
    )
    try:
        position = trade_engine.handle_signal(db, parsed, bot_id=bot_id, force=True)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "position_id": position.id, "symbol": position.symbol, "bot_id": bot_id}


@app.post("/api/test-trade/manual")
def test_trade_manual(payload: ManualTradeRequest, db: Session = Depends(get_db)):
    bot_id = _validate_bot_id(payload.bot_id)
    try:
        position = trade_engine.open_manual_trade(
            db,
            symbol=payload.symbol,
            side=payload.side,
            leverage=payload.leverage,
            margin_usdt=payload.margin_usdt,
            tp_price=payload.tp_price,
            sl_price=payload.sl_price,
            bot_id=bot_id,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, "position_id": position.id, "symbol": position.symbol, "bot_id": bot_id}


@app.get("/api/logs")
def get_logs(after: int = 0):
    if after > 0:
        return {"logs": bot_logs.get_logs(after)}
    return {"logs": bot_logs.all_logs()}


@app.get("/api/notify-bot/status")
def notify_bot_status(bot_id: str | None = None):
    if bot_id:
        bot_id = _validate_bot_id(bot_id)
        return notify_bots[bot_id].get_status()
    return {bid: notify_bots[bid].get_status() for bid in BOT_IDS}


@app.get("/api/ai/status")
def ai_status(db: Session = Depends(get_db)):
    base = ai_manager.get_status()
    per_bot = {}
    for bot_id in BOT_IDS:
        st = trade_engine.get_or_create_settings(db, bot_id)
        per_bot[bot_id] = bool(getattr(st, "ai_enabled", False))
    return {**base, "bots": per_bot}


@app.post("/api/ai/test")
async def ai_test():
    import asyncio

    result = await asyncio.to_thread(ai_manager.test_connection)
    if result.get("ok"):
        log_event("INFO", "ai", f"Проверка AI OK: {result.get('decision')}")
    else:
        log_event("ERROR", "ai", f"Проверка AI FAIL: {result.get('message')}")
    return result


@app.get("/health")
def health():
    return {
        "ok": True,
        "service": "telegram-bybit-bot",
        "bots": list(channels.keys()),
        "channels": channels,
        "ai_configured": ai_manager.is_configured,
        "serverless": SERVERLESS,
        "listener_enabled": tg_listener is not None,
    }


def run():
    import uvicorn

    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)


if __name__ == "__main__":
    run()
