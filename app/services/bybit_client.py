from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

from pybit.unified_trading import HTTP

from app.config import settings


def _decimal(value) -> Decimal:
    return Decimal(str(value))


def round_to_step(value: float | Decimal, step: float | Decimal, rounding=ROUND_DOWN) -> float:
    value_d = _decimal(value)
    step_d = _decimal(step)
    if step_d <= 0:
        return float(value_d)
    steps = (value_d / step_d).to_integral_value(rounding=rounding)
    result = steps * step_d
    # Normalize string without scientific notation
    text = format(result.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return float(text) if text else 0.0


def format_by_step(value: float | Decimal, step: float | Decimal) -> str:
    value_d = _decimal(value)
    step_d = _decimal(step)
    if step_d >= 1:
        return str(int(value_d.to_integral_value(rounding=ROUND_DOWN)))
    decimals = max(0, -step_d.as_tuple().exponent)
    quantized = value_d.quantize(Decimal(10) ** -decimals)
    text = format(quantized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


class BybitClient:
    def __init__(self) -> None:
        self.client = HTTP(
            testnet=settings.bybit_testnet,
            api_key=settings.bybit_api_key,
            api_secret=settings.bybit_api_secret,
        )
        self._filters_cache: dict[str, dict] = {}

    def get_instrument_filters(self, symbol: str) -> dict:
        symbol = symbol.upper()
        if symbol in self._filters_cache:
            return self._filters_cache[symbol]

        response = self.client.get_instruments_info(category="linear", symbol=symbol)
        items = response["result"]["list"]
        if not items:
            raise ValueError(f"Instrument not found: {symbol}")

        info = items[0]
        lot = info.get("lotSizeFilter", {})
        price = info.get("priceFilter", {})
        filters = {
            "qty_step": float(lot.get("qtyStep") or "0.001"),
            "min_qty": float(lot.get("minOrderQty") or lot.get("qtyStep") or "0.001"),
            "max_qty": float(lot.get("maxOrderQty") or "1000000"),
            "min_notional": float(lot.get("minNotionalValue") or "0"),
            "tick_size": float(price.get("tickSize") or "0.0001"),
        }
        self._filters_cache[symbol] = filters
        return filters

    def normalize_qty(self, symbol: str, qty: float) -> float:
        filters = self.get_instrument_filters(symbol)
        qty_step = filters["qty_step"]
        min_qty = filters["min_qty"]
        max_qty = filters["max_qty"]

        rounded = round_to_step(qty, qty_step, rounding=ROUND_DOWN)
        if rounded < min_qty:
            raise ValueError(
                f"Qty {rounded} < minOrderQty {min_qty} for {symbol}. "
                f"Увеличь маржу или плечо."
            )
        if rounded > max_qty:
            rounded = round_to_step(max_qty, qty_step, rounding=ROUND_DOWN)
        if rounded <= 0:
            raise ValueError(f"Calculated qty is zero for {symbol}")
        return rounded

    def normalize_price(self, symbol: str, price: float) -> float:
        filters = self.get_instrument_filters(symbol)
        return round_to_step(price, filters["tick_size"], rounding=ROUND_HALF_UP)

    def qty_to_str(self, symbol: str, qty: float) -> str:
        filters = self.get_instrument_filters(symbol)
        return format_by_step(qty, filters["qty_step"])

    def price_to_str(self, symbol: str, price: float) -> str:
        filters = self.get_instrument_filters(symbol)
        return format_by_step(price, filters["tick_size"])

    def get_last_price(self, symbol: str) -> float:
        response = self.client.get_tickers(category="linear", symbol=symbol)
        return float(response["result"]["list"][0]["lastPrice"])

    def set_leverage(self, symbol: str, leverage: int) -> None:
        self.client.set_leverage(
            category="linear",
            symbol=symbol,
            buyLeverage=str(leverage),
            sellLeverage=str(leverage),
        )

    def place_market_order(self, symbol: str, side: str, qty: float, tp: float, sl: float) -> str:
        qty_norm = self.normalize_qty(symbol, qty)
        tp_norm = self.normalize_price(symbol, tp)
        sl_norm = self.normalize_price(symbol, sl)

        response = self.client.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side == "LONG" else "Sell",
            orderType="Market",
            qty=self.qty_to_str(symbol, qty_norm),
            takeProfit=self.price_to_str(symbol, tp_norm),
            stopLoss=self.price_to_str(symbol, sl_norm),
            tpslMode="Full",
        )
        return response["result"]["orderId"]

    def place_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        tp: float,
        sl: float,
    ) -> str:
        qty_norm = self.normalize_qty(symbol, qty)
        price_norm = self.normalize_price(symbol, price)
        tp_norm = self.normalize_price(symbol, tp)
        sl_norm = self.normalize_price(symbol, sl)

        response = self.client.place_order(
            category="linear",
            symbol=symbol,
            side="Buy" if side == "LONG" else "Sell",
            orderType="Limit",
            qty=self.qty_to_str(symbol, qty_norm),
            price=self.price_to_str(symbol, price_norm),
            takeProfit=self.price_to_str(symbol, tp_norm),
            stopLoss=self.price_to_str(symbol, sl_norm),
            tpslMode="Full",
            timeInForce="GTC",
        )
        return response["result"]["orderId"]

    def get_order(self, symbol: str, order_id: str) -> dict:
        response = self.client.get_open_orders(category="linear", symbol=symbol, orderId=order_id)
        items = response.get("result", {}).get("list") or []
        if items:
            return items[0]
        # Ордер мог уже исполниться/исчезнуть из open — смотрим историю
        hist = self.client.get_order_history(category="linear", symbol=symbol, orderId=order_id)
        hist_items = hist.get("result", {}).get("list") or []
        if hist_items:
            return hist_items[0]
        return {}

    def cancel_order(self, symbol: str, order_id: str) -> None:
        self.client.cancel_order(category="linear", symbol=symbol, orderId=order_id)

    def amend_tp(self, symbol: str, tp_price: float, side: str | None = None) -> None:
        tp_norm = self.normalize_price(symbol, tp_price)
        self.client.set_trading_stop(
            category="linear",
            symbol=symbol,
            takeProfit=self.price_to_str(symbol, tp_norm),
            tpslMode="Full",
            positionIdx=0,
        )

    def amend_sl(self, symbol: str, sl_price: float) -> None:
        sl_norm = self.normalize_price(symbol, sl_price)
        self.client.set_trading_stop(
            category="linear",
            symbol=symbol,
            stopLoss=self.price_to_str(symbol, sl_norm),
            tpslMode="Full",
            positionIdx=0,
        )

    def close_position(self, symbol: str, side: str, qty: float) -> str:
        qty_norm = self.normalize_qty(symbol, qty)
        response = self.client.place_order(
            category="linear",
            symbol=symbol,
            side="Sell" if side == "LONG" else "Buy",
            orderType="Market",
            qty=self.qty_to_str(symbol, qty_norm),
            reduceOnly=True,
        )
        return response["result"]["orderId"]

    def get_open_positions(self) -> list[dict]:
        response = self.client.get_positions(category="linear", settleCoin="USDT")
        return response["result"]["list"]
