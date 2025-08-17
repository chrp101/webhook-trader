import os, hmac, hashlib, json, logging, math, time
from typing import Optional, Dict, Any
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel, Field
import uvicorn

# ---- Logging ----
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("tv-alpaca-bot")

# ---- Env ----
ALPACA_API_KEY = os.getenv("ALPACA_API_KEY", "")
ALPACA_API_SECRET = os.getenv("ALPACA_API_SECRET", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # shared with TradingView alert
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "ETHUSD")
SAFETY_BUFFER_PCT = float(os.getenv("SAFETY_BUFFER_PCT", "0.99"))  # use 99% of cash by default
TIME_IN_FORCE = os.getenv("ORDER_TIME_IN_FORCE", "gtc").upper()    # GTC/IOC/FOK
FRACTIONAL_SHARES = os.getenv("FRACTIONAL_SHARES", "true").lower() == "true"
CLOSE_ALL_ON_BUY = os.getenv("CLOSE_ALL_ON_BUY", "false").lower() == "true"
CRYPTO_QTY_DECIMALS = int(os.getenv("CRYPTO_QTY_DECIMALS", "6"))
PORT = int(os.getenv("PORT", "8000"))

# ---- Alpaca SDK ----
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

from alpaca.data.historical.stock import StockHistoricalDataClient
from alpaca.data.historical.crypto import CryptoHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest, CryptoLatestTradeRequest

trading = TradingClient(ALPACA_API_KEY, ALPACA_API_SECRET, paper="paper" in ALPACA_BASE_URL)
stock_data = StockHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)
crypto_data = CryptoHistoricalDataClient(ALPACA_API_KEY, ALPACA_API_SECRET)

app = FastAPI(title="TradingView → Alpaca Bot")

# In-memory idempotency cache (per-process). Use a DB/Redis for multi-instance deployments.
PROCESSED_IDS = {}
IDEMPOTENCY_TTL_SEC = 60 * 10  # 10 minutes


def is_crypto_symbol(symbol: str) -> bool:
    s = symbol.upper()
    # Alpaca crypto commonly uses e.g., BTCUSD, ETHUSD
    return s.endswith("USD") or "/" in s


def get_latest_price(symbol: str) -> float:
    s = symbol.upper()
    if is_crypto_symbol(s):
        req = CryptoLatestTradeRequest(symbol_or_symbols=s)
        trade = crypto_data.get_latest_trade(req)
    else:
        req = StockLatestTradeRequest(symbol_or_symbols=s)
        trade = stock_data.get_latest_trade(req)
    # SDK returns dict for multi-symbol; normalize
    if isinstance(trade, dict):
        trade = trade[s]
    return float(trade.price)


def get_cash_available() -> float:
    acct = trading.get_account()
    return float(acct.cash)


def cancel_open_orders_for_symbol(symbol: str):
    req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    open_orders = trading.get_orders(filter=req)
    for o in open_orders:
        if o.symbol.upper() == symbol.upper():
            try:
                trading.cancel_order_by_id(o.id)
                log.info(f"Canceled open order {o.id} for {symbol}")
            except Exception as e:
                log.warning(f"Failed cancel {o.id} for {symbol}: {e}")


def close_position_for_symbol(symbol: str):
    try:
        trading.close_position(symbol)
        log.info(f"Close position sent for {symbol}")
    except Exception as e:
        # If no position exists, Alpaca throws; that’s OK
        msg = str(e).lower()
        if "position does not exist" in msg or "404" in msg:
            log.info(f"No position to close for {symbol}")
        else:
            log.warning(f"Error closing {symbol}: {e}")


def buy_whole_balance(symbol: str):
    price = get_latest_price(symbol)
    cash = get_cash_available()
    notional = cash * SAFETY_BUFFER_PCT
    log.info(f"{symbol}: last price={price:.8f}, cash={cash:.2f}, notional target={notional:.2f}")

    if notional < 1:  # too little to place
        raise HTTPException(status_code=400, detail="Not enough cash to place order.")

    if is_crypto_symbol(symbol):
        qty = round(notional / price, CRYPTO_QTY_DECIMALS)
        if qty <= 0:
            raise HTTPException(status_code=400, detail="Computed crypto qty <= 0")
        order = MarketOrderRequest(
            symbol=symbol,
            qty=str(qty),
            side=OrderSide.BUY,
            time_in_force=TimeInForce[TIME_IN_FORCE]
        )
        placed = trading.submit_order(order)
        log.info(f"BUY CRYPTO {symbol} qty={qty} (≈${notional:.2f}) order_id={placed.id}")
        return placed.id
    else:
        if FRACTIONAL_SHARES:
            # Fractional equities: use notional
            order = MarketOrderRequest(
                symbol=symbol,
                notional=round(notional, 2),
                side=OrderSide.BUY,
                time_in_force=TimeInForce[TIME_IN_FORCE]
            )
            placed = trading.submit_order(order)
            log.info(f"BUY EQUITY (fractional) {symbol} notional=${notional:.2f} order_id={placed.id}")
            return placed.id
        else:
            # Whole shares
            qty = math.floor(notional / price)
            if qty < 1:
                raise HTTPException(status_code=400, detail="Not enough for 1 whole share; enable FRACTIONAL_SHARES.")
            order = MarketOrderRequest(
                symbol=symbol,
                qty=str(qty),
                side=OrderSide.BUY,
                time_in_force=TimeInForce[TIME_IN_FORCE]
            )
            placed = trading.submit_order(order)
            log.info(f"BUY EQUITY {symbol} qty={qty} (≈${qty*price:.2f}) order_id={placed.id}")
            return placed.id


def verify_secret(body_bytes: bytes, signature: Optional[str]) -> None:
    """
    Supports either:
    - Raw shared secret sent in payload JSON ("secret": "..."), or
    - HMAC-SHA256 signature in header 'X-Signature' over raw body, using WEBHOOK_SECRET as key.
    """
    if not WEBHOOK_SECRET:
        return  # disabled validation (not recommended)
    if signature:
        digest = hmac.new(WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(digest, signature):
            raise HTTPException(status_code=401, detail="Invalid signature")
    else:
        # We'll check JSON field in endpoint handler
        pass


def idempotent(key: str) -> bool:
    # returns True if fresh; False if duplicate
    now = time.time()
    # purge old
    to_del = [k for k, v in PROCESSED_IDS.items() if now - v > IDEMPOTENCY_TTL_SEC]
    for k in to_del:
        PROCESSED_IDS.pop(k, None)
    if key in PROCESSED_IDS:
        return False
    PROCESSED_IDS[key] = now
    return True


class TVAlert(BaseModel):
    # Minimal alert schema; add any fields you use from TradingView
    action: str = Field(..., description="BUY or SELL")
    symbol: Optional[str] = Field(None, description="e.g., ETHUSD, AAPL")
    secret: Optional[str] = Field(None, description="shared secret (if not using header HMAC)")
    id: Optional[str] = Field(None, description="unique id per-bar for idempotency")


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(req: Request):
    raw = await req.body()
    verify_secret(raw, req.headers.get("X-Signature"))

    try:
        payload = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    tv = TVAlert(**payload)

    # If not using header HMAC, enforce body secret
    if WEBHOOK_SECRET and not req.headers.get("X-Signature"):
        if not tv.secret or tv.secret != WEBHOOK_SECRET:
            raise HTTPException(status_code=401, detail="Invalid secret")

    symbol = (tv.symbol or DEFAULT_SYMBOL).upper()
    action = tv.action.strip().upper()

    # Idempotency (strongly recommended to include {{time}} or bar id from TV)
    idem_key = f"{symbol}:{action}:{tv.id or payload.get('time') or ''}"
    if tv.id or payload.get('time'):
        if not idempotent(idem_key):
            log.info(f"Duplicate alert ignored: {idem_key}")
            return {"status": "duplicate_ignored"}

    if action not in ("BUY", "SELL"):
        raise HTTPException(status_code=400, detail="action must be BUY or SELL")

    # Common hygiene
    cancel_open_orders_for_symbol(symbol)

    if action == "SELL":
        close_position_for_symbol(symbol)
        return {"status": "ok", "did": "close_position", "symbol": symbol}

    # BUY flow
    if CLOSE_ALL_ON_BUY:
        try:
            trading.close_all_positions(cancel_orders=True)
            log.info("Closed ALL positions per CLOSE_ALL_ON_BUY=true")
        except Exception as e:
            log.warning(f"close_all_positions error: {e}")

    # Always ensure this symbol is clean before buying
    close_position_for_symbol(symbol)
    order_id = buy_whole_balance(symbol)
    return {"status": "ok", "did": "buy", "symbol": symbol, "order_id": str(order_id)}


if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=PORT, reload=False)
